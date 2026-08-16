"""ZOOMEX注文候補を取引仕様と資金配分の境界で検査する。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Literal

from crypt_ai.allocation import AllocationState, PortfolioAllocator


OrderType = Literal["limit", "market"]
PositionSide = Literal["long", "short"]


def _decimal(value: object, name: str) -> Decimal:
    """値を有限なDecimalへ変換する。

    Args:
        value: 数値または文字列化できる数値。
        name: エラーに表示する値の名前。

    Returns:
        変換後のDecimal。

    Raises:
        ValueError: 数値へ変換できない、または有限でない場合。
    """

    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a decimal") from error
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class ZoomexInstrument:
    """ZOOMEX銘柄仕様の注文検査に必要な最小値。

    Attributes:
        symbol: ZOOMEXの完全一致シンボル。
        tick_size: 価格の最小刻み。
        qty_step: 数量の最小刻み。
        min_order_qty: 最小注文数量。
        min_order_notional: 最小注文元本。
    """

    symbol: str
    tick_size: Decimal
    qty_step: Decimal
    min_order_qty: Decimal
    min_order_notional: Decimal

    def __post_init__(self) -> None:
        """銘柄名と取引仕様の正値を検査する。"""

        if not self.symbol or self.symbol.strip() != self.symbol:
            raise ValueError("symbol must be a non-empty trimmed string")
        for name in ("tick_size", "qty_step", "min_order_qty", "min_order_notional"):
            value = _decimal(getattr(self, name), name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)

    @classmethod
    def from_instrument_record(cls, record: dict[str, object]) -> "ZoomexInstrument":
        """ZOOMEX `instruments-info`の1レコードから銘柄仕様を作る。

        Args:
            record: `symbol`、`priceFilter`、`lotSizeFilter`を含むレコード。

        Returns:
            注文検査用の銘柄仕様。

        Raises:
            ValueError: 必須階層または値が不足・不正な場合。
        """

        try:
            price_filter = record["priceFilter"]
            lot_filter = record["lotSizeFilter"]
            return cls(
                symbol=str(record["symbol"]),
                tick_size=_decimal(price_filter["tickSize"], "tickSize"),
                qty_step=_decimal(lot_filter["qtyStep"], "qtyStep"),
                min_order_qty=_decimal(lot_filter["minOrderQty"], "minOrderQty"),
                min_order_notional=_decimal(
                    lot_filter["minNotionalValue"], "minNotionalValue"
                ),
            )
        except (KeyError, TypeError) as error:
            raise ValueError("invalid ZOOMEX instrument record") from error


@dataclass(frozen=True)
class OrderCandidate:
    """取引所へ渡す前の注文候補。API送信は行わない。"""

    symbol: str
    position_side: PositionSide
    action: Literal["open", "close"]
    exchange_side: Literal["buy", "sell"]
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None
    estimated_notional: Decimal
    reduce_only: bool
    allocation_reason: str | None = None


class OrderRejected(ValueError):
    """資金配分またはZOOMEX仕様を満たさない注文候補。"""


def build_entry_order(
    allocator: PortfolioAllocator,
    state: AllocationState,
    instrument: ZoomexInstrument,
    *,
    side: PositionSide,
    lot_count: int = 1,
    reference_price: Decimal,
    order_type: OrderType = "limit",
) -> OrderCandidate:
    """配分承認済みの新規long/short注文候補を作る。

    この関数は資金配分状態を変更しない。約定を確認した後に呼び出し側が
    `allocator.try_open`で状態を更新し、注文拒否・取消時は更新しない。

    Args:
        allocator: 資産・ロット・上限を検査するアロケータ。
        state: 現在の配分状態。
        instrument: ZOOMEX銘柄仕様。
        side: 建玉の方向。
        lot_count: 要求する固定ロット数。
        reference_price: 指値価格または成行数量計算の基準価格。
        order_type: `limit`または`market`。

    Returns:
        取引所仕様へ丸め済みの新規注文候補。

    Raises:
        OrderRejected: 配分、価格、数量、最小元本、注文種別が不正な場合。
    """

    if order_type not in {"limit", "market"}:
        raise OrderRejected("invalid_order_type")
    price = _positive_price(reference_price)
    if instrument.symbol not in allocator.config.allowed_symbols:
        raise OrderRejected("unknown_symbol")
    decision = allocator.evaluate_order(
        state,
        side=side,
        symbol=instrument.symbol,
        lot_count=lot_count,
    )
    if not decision.accepted:
        raise OrderRejected(f"allocation:{decision.reason}")
    exchange_side: Literal["buy", "sell"] = "buy" if side == "long" else "sell"
    rounded_price = (
        None
        if order_type == "market"
        else _round_price(price, instrument.tick_size, exchange_side)
    )
    sizing_price = rounded_price or price
    quantity = _floor_step(
        decision.approved_notional / sizing_price,
        instrument.qty_step,
    )
    _validate_quantity(quantity, sizing_price, instrument)
    return OrderCandidate(
        symbol=instrument.symbol,
        position_side=side,
        action="open",
        exchange_side=exchange_side,
        order_type=order_type,
        quantity=quantity,
        price=rounded_price,
        estimated_notional=quantity * sizing_price,
        reduce_only=False,
        allocation_reason=decision.reason,
    )


def build_exit_order(
    instrument: ZoomexInstrument,
    *,
    side: PositionSide,
    quantity: Decimal,
    reference_price: Decimal,
    order_type: OrderType = "limit",
) -> OrderCandidate:
    """既存建玉をreduce-only決済する注文候補を作る。

    Args:
        instrument: ZOOMEX銘柄仕様。
        side: 決済する建玉の方向。
        quantity: 決済対象数量。
        reference_price: 指値価格または成行数量計算の基準価格。
        order_type: `limit`または`market`。

    Returns:
        取引所仕様へ丸め済みのreduce-only注文候補。

    Raises:
        OrderRejected: 価格、数量、注文種別が不正な場合。
    """

    if order_type not in {"limit", "market"}:
        raise OrderRejected("invalid_order_type")
    price = _positive_price(reference_price)
    exchange_side: Literal["buy", "sell"] = "sell" if side == "long" else "buy"
    rounded_price = (
        None
        if order_type == "market"
        else _round_price(price, instrument.tick_size, exchange_side)
    )
    sizing_price = rounded_price or price
    rounded_quantity = _floor_step(quantity, instrument.qty_step)
    _validate_quantity(rounded_quantity, sizing_price, instrument)
    return OrderCandidate(
        symbol=instrument.symbol,
        position_side=side,
        action="close",
        exchange_side=exchange_side,
        order_type=order_type,
        quantity=rounded_quantity,
        price=rounded_price,
        estimated_notional=rounded_quantity * sizing_price,
        reduce_only=True,
    )


def _positive_price(value: Decimal) -> Decimal:
    """正の価格を検証する。"""

    price = _decimal(value, "reference_price")
    if price <= 0:
        raise OrderRejected("invalid_price")
    return price


def _round_price(
    price: Decimal,
    tick_size: Decimal,
    exchange_side: Literal["buy", "sell"],
) -> Decimal:
    """指値価格を注文側に不利にならない方向へtick丸めする。"""

    rounding = ROUND_FLOOR if exchange_side == "buy" else ROUND_CEILING
    ticks = (price / tick_size).to_integral_value(rounding=rounding)
    return ticks * tick_size


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    """数量をqtyStep以下へ切り下げる。"""

    steps = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return steps * step


def _validate_quantity(
    quantity: Decimal,
    price: Decimal,
    instrument: ZoomexInstrument,
) -> None:
    """数量、元本、数量刻みを最小注文条件へ照合する。"""

    if quantity < instrument.min_order_qty:
        raise OrderRejected("below_min_order_qty")
    if quantity * price < instrument.min_order_notional:
        raise OrderRejected("below_min_order_notional")
