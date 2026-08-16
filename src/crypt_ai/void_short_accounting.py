"""EXP-2026-0015のVOID式ショート用約定・費用会計を提供する。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class VoidShortLiquidity(StrEnum):
    """約定が板へ流動性を提供したかを表す。"""

    MAKER = "MAKER"
    TAKER = "TAKER"


@dataclass(frozen=True)
class VoidShortCostModel:
    """ZOOMEX通常USDT無期限の費用仮定。

    Attributes:
        maker_fee_rate: 指値maker約定の手数料率。
        taker_fee_rate: 市場性taker約定の手数料率。
        taker_slippage_rate: 市場性買い決済へ上乗せする価格比率。
    """

    maker_fee_rate: Decimal = Decimal("0.0002")
    taker_fee_rate: Decimal = Decimal("0.0006")
    taker_slippage_rate: Decimal = Decimal("0.0005")

    def __post_init__(self) -> None:
        """費用率が有限かつ許容範囲内であることを検査する。

        Raises:
            ValueError: 費用率が非有限、負、または1以上の場合。
        """

        for name, value in (
            ("maker_fee_rate", self.maker_fee_rate),
            ("taker_fee_rate", self.taker_fee_rate),
            ("taker_slippage_rate", self.taker_slippage_rate),
        ):
            if not value.is_finite() or value < 0 or value >= 1:
                raise ValueError(f"{name} must be finite and in [0, 1)")


@dataclass(frozen=True)
class VoidShortPosition:
    """USDT建てショート建玉と確定済み会計の状態。

    Attributes:
        quantity: 現在のショート数量。
        average_entry_price: 手数料を含めない加重平均売り約定価格。
        cash_flow: 売買代金、手数料、Fundingを合算したUSDTキャッシュフロー。
        trading_fees: 累積売買手数料。
        funding_cash_flow: 累積Funding受払。受取を正、支払を負とする。
    """

    quantity: Decimal = Decimal("0")
    average_entry_price: Decimal = Decimal("0")
    cash_flow: Decimal = Decimal("0")
    trading_fees: Decimal = Decimal("0")
    funding_cash_flow: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        """建玉状態の数値と数量・平均価格の組合せを検査する。

        Raises:
            ValueError: 数値が非有限、数量が負、または建玉と平均価格が不整合な場合。
        """

        values = (
            self.quantity,
            self.average_entry_price,
            self.cash_flow,
            self.trading_fees,
            self.funding_cash_flow,
        )
        if any(not value.is_finite() for value in values):
            raise ValueError("position values must be finite")
        if self.quantity < 0 or self.trading_fees < 0:
            raise ValueError("quantity and trading_fees must not be negative")
        if (self.quantity == 0) != (self.average_entry_price == 0):
            raise ValueError("flat position must have zero average_entry_price")
        if self.quantity > 0 and self.average_entry_price <= 0:
            raise ValueError("open position must have positive average_entry_price")

    @property
    def realized_net_pnl(self) -> Decimal | None:
        """建玉完済時の手数料・Funding込み確定損益を返す。

        Returns:
            建玉がなければ累積キャッシュフロー、保有中なら``None``。
        """

        return self.cash_flow if self.quantity == 0 else None


def open_void_short_maker(
    position: VoidShortPosition,
    *,
    quantity: Decimal,
    limit_price: Decimal,
    costs: VoidShortCostModel,
) -> VoidShortPosition:
    """価格接触した売り指値を全量maker約定として建玉へ加える。

    Args:
        position: 約定前のショート建玉。
        quantity: 約定数量。
        limit_price: 売り指値価格。
        costs: 費用モデル。

    Returns:
        加重平均建値、売却代金、maker手数料を反映した建玉。

    Raises:
        ValueError: 数量または価格が非正・非有限の場合。
    """

    _require_positive(quantity=quantity, price=limit_price)
    old_notional = position.quantity * position.average_entry_price
    fill_notional = quantity * limit_price
    new_quantity = position.quantity + quantity
    fee = fill_notional * costs.maker_fee_rate
    return VoidShortPosition(
        quantity=new_quantity,
        average_entry_price=(old_notional + fill_notional) / new_quantity,
        cash_flow=position.cash_flow + fill_notional - fee,
        trading_fees=position.trading_fees + fee,
        funding_cash_flow=position.funding_cash_flow,
    )


def open_void_short_taker(
    position: VoidShortPosition,
    *,
    quantity: Decimal,
    raw_price: Decimal,
    costs: VoidShortCostModel,
) -> VoidShortPosition:
    """市場性の売り約定をショート建玉へ加える。

    売りのtaker約定は、スリッページによって基準価格より低い価格で約定する
    と仮定する。新しい下落ブレイク型ショートの次足始値エントリーで使う。

    Args:
        position: 約定前のショート建玉。
        quantity: 新規売り数量。
        raw_price: 約定判定に使う市場価格。
        costs: 費用モデル。

    Returns:
        加重平均建値、売却代金、taker手数料を反映した建玉。

    Raises:
        ValueError: 数量または価格が非正・非有限の場合。
    """

    _require_positive(quantity=quantity, price=raw_price)
    execution_price = raw_price * (Decimal("1") - costs.taker_slippage_rate)
    old_notional = position.quantity * position.average_entry_price
    fill_notional = quantity * execution_price
    new_quantity = position.quantity + quantity
    fee = fill_notional * costs.taker_fee_rate
    return VoidShortPosition(
        quantity=new_quantity,
        average_entry_price=(old_notional + fill_notional) / new_quantity,
        cash_flow=position.cash_flow + fill_notional - fee,
        trading_fees=position.trading_fees + fee,
        funding_cash_flow=position.funding_cash_flow,
    )


def close_void_short(
    position: VoidShortPosition,
    *,
    quantity: Decimal,
    raw_price: Decimal,
    liquidity: VoidShortLiquidity,
    costs: VoidShortCostModel,
) -> VoidShortPosition:
    """reduce-only買い約定を建玉へ反映する。

    makerは指定価格、takerはショートに不利な上方スリッページを適用する。

    Args:
        position: 決済前のショート建玉。
        quantity: 決済数量。
        raw_price: 指値価格または市場決済の基準価格。
        liquidity: makerまたはtaker。
        costs: 費用モデル。

    Returns:
        買戻し代金と手数料を反映した建玉。

    Raises:
        ValueError: 入力が不正、または建玉を超えて決済する場合。
    """

    _require_positive(quantity=quantity, price=raw_price)
    if quantity > position.quantity:
        raise ValueError("close quantity must not exceed open quantity")
    if liquidity == VoidShortLiquidity.MAKER:
        execution_price = raw_price
        fee_rate = costs.maker_fee_rate
    elif liquidity == VoidShortLiquidity.TAKER:
        execution_price = raw_price * (Decimal("1") + costs.taker_slippage_rate)
        fee_rate = costs.taker_fee_rate
    else:
        raise ValueError(f"unknown liquidity: {liquidity}")
    notional = quantity * execution_price
    fee = notional * fee_rate
    remaining = position.quantity - quantity
    return VoidShortPosition(
        quantity=remaining,
        average_entry_price=(
            position.average_entry_price if remaining > 0 else Decimal("0")
        ),
        cash_flow=position.cash_flow - notional - fee,
        trading_fees=position.trading_fees + fee,
        funding_cash_flow=position.funding_cash_flow,
    )


def apply_void_short_funding(
    position: VoidShortPosition,
    *,
    mark_price: Decimal,
    funding_rate: Decimal,
) -> VoidShortPosition:
    """Funding時刻に保有するショート建玉へFundingを反映する。

    正のFunding率ではショートが受け取り、負の率では支払う。

    Args:
        position: Funding直前のショート建玉。
        mark_price: Funding時刻のmark price。
        funding_rate: ZOOMEX履歴のFunding率。

    Returns:
        Fundingキャッシュフローを反映した建玉。

    Raises:
        ValueError: mark priceが非正・非有限、またはFunding率が非有限の場合。
    """

    _require_positive(price=mark_price)
    if not funding_rate.is_finite():
        raise ValueError("funding_rate must be finite")
    funding = position.quantity * mark_price * funding_rate
    return VoidShortPosition(
        quantity=position.quantity,
        average_entry_price=position.average_entry_price,
        cash_flow=position.cash_flow + funding,
        trading_fees=position.trading_fees,
        funding_cash_flow=position.funding_cash_flow + funding,
    )


def _require_positive(**values: Decimal) -> None:
    """名前付きDecimalが正かつ有限であることを検査する。

    Args:
        **values: 検査する名前付き数値。

    Raises:
        ValueError: いずれかが非正または非有限の場合。
    """

    for name, value in values.items():
        if not value.is_finite() or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
