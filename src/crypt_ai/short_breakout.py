"""長期SMAとDonchian下抜けによる単一エントリー型ショートを提供する。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from enum import StrEnum

import pandas as pd

from crypt_ai.void_short_accounting import (
    VoidShortCostModel,
    VoidShortLiquidity,
    VoidShortPosition,
    apply_void_short_funding,
    close_void_short,
    open_void_short_taker,
)
from crypt_ai.void_short_backtest import VoidShortInstrument


class ShortBreakoutExit(StrEnum):
    """下落ブレイク型ショートの決済理由。"""

    STOP_LOSS = "STOP_LOSS"
    SMA_EXIT = "SMA_EXIT"
    TIME_EXIT = "TIME_EXIT"


@dataclass(frozen=True)
class ShortBreakoutConfig:
    """下落ブレイク型ショートのシグナル・資金配分設定。"""

    initial_equity: Decimal = Decimal("250")
    evaluation_start: pd.Timestamp = pd.Timestamp("2022-02-01T00:00:00Z")
    evaluation_end: pd.Timestamp = pd.Timestamp("2026-01-01T00:00:00Z")
    costs: VoidShortCostModel = VoidShortCostModel()
    sma_bars: int = 2400
    donchian_bars: int = 240
    atr_bars: int = 240
    stop_atr_multiplier: Decimal = Decimal("3")
    max_holding_bars: int = 168
    entry_lot_count: int = 4
    sizing_leverage: Decimal = Decimal("20")
    lot_divisor: Decimal = Decimal("100")

    def __post_init__(self) -> None:
        """資産、期間、窓幅、ロット設定が有効か検査する。

        Raises:
            ValueError: 設定値が非正・非有限、または期間が逆の場合。
        """

        if not self.initial_equity.is_finite() or self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive and finite")
        if self.evaluation_start >= self.evaluation_end:
            raise ValueError("evaluation_start must precede evaluation_end")
        for name, value in (
            ("stop_atr_multiplier", self.stop_atr_multiplier),
            ("sizing_leverage", self.sizing_leverage),
            ("lot_divisor", self.lot_divisor),
        ):
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        for name, value in (
            ("sma_bars", self.sma_bars),
            ("donchian_bars", self.donchian_bars),
            ("atr_bars", self.atr_bars),
            ("max_holding_bars", self.max_holding_bars),
            ("entry_lot_count", self.entry_lot_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class ShortBreakoutResult:
    """下落ブレイク型ショートの指標、イベント、評価額曲線。"""

    symbol: str
    metrics: dict[str, object]
    events: tuple[dict[str, object], ...]
    equity_curve: tuple[dict[str, object], ...]


def prepare_short_breakout_signals(
    frame: pd.DataFrame,
    *,
    sma_bars: int = 2400,
    donchian_bars: int = 240,
    atr_bars: int = 240,
) -> pd.DataFrame:
    """長期SMA、Donchian下抜け、ATR、手仕舞いシグナルを計算する。

    現在足の終値で確定したシグナルは、呼び出し側が次足始値へ遅延して
    適用する。Donchian下限は現在足を含めず、直前の確定足だけから計算する。

    Args:
        frame: 2時間足の時刻、OHLC、補間フラグを持つDataFrame。
        sma_bars: 長期SMAの本数。
        donchian_bars: 直前安値チャネルの本数。
        atr_bars: ATRの本数。

    Returns:
        SMA、Donchian下限、ATR、entry・exitシグナルを追加したDataFrame。

    Raises:
        ValueError: 時系列、補間、OHLC、窓幅に不備がある場合。
    """

    for name, value in (
        ("sma_bars", sma_bars),
        ("donchian_bars", donchian_bars),
        ("atr_bars", atr_bars),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    required = {"event_time", "open", "high", "low", "close", "is_interpolated"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("frame must not be empty")
    result = frame.copy()
    result["event_time"] = pd.to_datetime(
        result["event_time"], utc=True, errors="coerce"
    )
    if result["event_time"].isna().any() or result["event_time"].duplicated().any():
        raise ValueError("event_time must be valid and unique")
    if not result["event_time"].is_monotonic_increasing:
        raise ValueError("event_time must be sorted")
    if len(result) > 1 and not result["event_time"].diff().dropna().eq(
        pd.Timedelta(hours=2)
    ).all():
        raise ValueError("event_time must be continuous 2-hour bars")
    if not result["is_interpolated"].map(lambda value: isinstance(value, bool)).all():
        raise ValueError("is_interpolated must contain bool values")
    if result["is_interpolated"].any():
        raise ValueError("interpolated rows are not allowed")
    for column in ("open", "high", "low", "close"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
        if result[column].isna().any() or not result[column].gt(0).all():
            raise ValueError(f"{column} must contain positive numeric values")
    if not (
        (result["low"] <= result["open"])
        & (result["open"] <= result["high"])
        & (result["low"] <= result["close"])
        & (result["close"] <= result["high"])
    ).all():
        raise ValueError("OHLC price relationship is invalid")

    close = result["close"]
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["sma"] = close.rolling(sma_bars, min_periods=sma_bars).mean()
    result["donchian_low"] = result["low"].shift(1).rolling(
        donchian_bars, min_periods=donchian_bars
    ).min()
    result["atr"] = true_range.rolling(atr_bars, min_periods=atr_bars).mean()
    result["entry_signal_at_close"] = (
        result["sma"].notna()
        & result["donchian_low"].notna()
        & result["atr"].notna()
        & (close < result["sma"])
        & (close < result["donchian_low"])
    )
    result["exit_signal_at_close"] = result["sma"].notna() & (close > result["sma"])
    return result


def _floor_quantity(raw_quantity: Decimal, instrument: VoidShortInstrument) -> Decimal:
    """銘柄の数量刻みへ数量を切り下げる。"""

    steps = (raw_quantity / instrument.qty_step).to_integral_value(
        rounding=ROUND_FLOOR
    )
    quantity = steps * instrument.qty_step
    if quantity < instrument.min_order_qty:
        raise ValueError("entry quantity is below min_order_qty")
    if quantity <= 0:
        raise ValueError("entry quantity must be positive")
    return quantity


def _entry_quantity(
    config: ShortBreakoutConfig,
    instrument: VoidShortInstrument,
    raw_price: Decimal,
) -> Decimal:
    """固定ショートスリーブ資産から単一エントリー数量を算出する。"""

    lot_notional = (
        config.initial_equity * config.sizing_leverage / config.lot_divisor
    )
    total_notional = lot_notional * config.entry_lot_count
    quantity = _floor_quantity(total_notional / raw_price, instrument)
    if quantity * raw_price < instrument.min_order_notional:
        raise ValueError("entry notional is below min_order_notional")
    return quantity


def _validate_funding(funding: pd.DataFrame) -> None:
    """Funding DataFrameの時刻と率を検査する。"""

    required = {"event_time", "funding_rate"}
    if not required.issubset(funding.columns):
        raise ValueError(f"missing funding columns: {sorted(required - set(funding.columns))}")
    timestamps = pd.to_datetime(funding["event_time"], utc=True, errors="coerce")
    rates = pd.to_numeric(funding["funding_rate"], errors="coerce")
    if timestamps.isna().any() or timestamps.duplicated().any() or rates.isna().any():
        raise ValueError("funding values must be valid and unique")


def _mark_equity(
    initial_equity: Decimal, position: VoidShortPosition, mark_price: Decimal
) -> Decimal:
    """ショート建玉をmark価格で評価した総資産を返す。"""

    return initial_equity + position.cash_flow - position.quantity * mark_price


def _summarize(
    symbol: str,
    config: ShortBreakoutConfig,
    position: VoidShortPosition,
    events: list[dict[str, object]],
    equity_curve: list[dict[str, object]],
) -> dict[str, object]:
    """イベントと評価額曲線から下落ブレイク指標を集計する。"""

    if not equity_curve:
        raise ValueError("equity_curve must not be empty")
    equities = [Decimal(str(row["equity"])) for row in equity_curve]
    peak = config.initial_equity
    max_drawdown = Decimal("0")
    for equity in equities:
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - Decimal("1"))
    event_types = [str(event["event_type"]) for event in events]
    return {
        "symbol": symbol,
        "initial_equity": str(config.initial_equity),
        "final_equity": str(equities[-1]),
        "net_pnl": str(equities[-1] - config.initial_equity),
        "return_rate": str(equities[-1] / config.initial_equity - Decimal("1")),
        "max_drawdown": str(max_drawdown),
        "entry_count": event_types.count("ENTRY"),
        "stop_loss_count": event_types.count(ShortBreakoutExit.STOP_LOSS),
        "sma_exit_count": event_types.count(ShortBreakoutExit.SMA_EXIT),
        "time_exit_count": event_types.count(ShortBreakoutExit.TIME_EXIT),
        "funding_event_count": event_types.count("FUNDING"),
        "total_fees": str(position.trading_fees),
        "total_funding_cash_flow": str(position.funding_cash_flow),
        "max_position_quantity": str(
            max(Decimal(str(row["position_quantity"])) for row in equity_curve)
        ),
        "max_position_notional": str(
            max(Decimal(str(row["position_notional"])) for row in equity_curve)
        ),
        "open_position_at_end": position.quantity > 0,
    }


def run_short_breakout_backtest(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    instrument: VoidShortInstrument,
    config: ShortBreakoutConfig = ShortBreakoutConfig(),
) -> ShortBreakoutResult:
    """下落ブレイク型ショートを決定論的にバックテストする。

    シグナルは前足終値で確定し、次足始値で一度だけエントリーする。保有中は
    追加建玉を禁止し、高値が固定ストップへ到達した場合を最優先でtaker決済
    する。Fundingは保有中の該当時刻に適用する。

    Args:
        frame: tradeとmarkの2時間足を結合したDataFrame。
        funding: Funding時刻と率のDataFrame。
        instrument: ZOOMEX銘柄仕様。
        config: シグナル、資金配分、評価期間、費用設定。

    Returns:
        指標、監査イベント、各バーのmark評価額を含む結果。

    Raises:
        ValueError: 入力データ、銘柄仕様、または評価期間が不正な場合。
    """

    prepared = prepare_short_breakout_signals(
        frame,
        sma_bars=config.sma_bars,
        donchian_bars=config.donchian_bars,
        atr_bars=config.atr_bars,
    )
    _validate_funding(funding)
    if config.evaluation_start >= config.evaluation_end:
        raise ValueError("evaluation_start must precede evaluation_end")
    mark_close_column = "mark_close" if "mark_close" in prepared else "close"
    mark_high_column = "mark_high" if "mark_high" in prepared else "high"
    funding_by_time = {
        timestamp: Decimal(str(rate))
        for timestamp, rate in zip(
            pd.to_datetime(funding["event_time"], utc=True),
            funding["funding_rate"],
            strict=True,
        )
    }
    position = VoidShortPosition()
    entry_index: int | None = None
    stop_price: Decimal | None = None
    events: list[dict[str, object]] = []
    equity_curve: list[dict[str, object]] = []
    for index in range(1, len(prepared)):
        row = prepared.iloc[index]
        signal_row = prepared.iloc[index - 1]
        timestamp = pd.Timestamp(row["event_time"])
        if not (
            config.evaluation_start <= timestamp < config.evaluation_end
        ):
            continue
        mark_close = Decimal(str(row[mark_close_column]))
        mark_high = Decimal(str(row[mark_high_column]))
        raw_open = Decimal(str(row["open"]))
        if position.quantity > 0 and timestamp in funding_by_time:
            previous = position
            position = apply_void_short_funding(
                position,
                mark_price=mark_close,
                funding_rate=funding_by_time[timestamp],
            )
            events.append(
                {
                    "event_time": timestamp.isoformat(),
                    "event_type": "FUNDING",
                    "quantity": str(position.quantity),
                    "funding_rate": str(funding_by_time[timestamp]),
                    "funding_delta": str(
                        position.funding_cash_flow - previous.funding_cash_flow
                    ),
                    "fee_delta": "0",
                }
            )

        closed_this_bar = False
        if position.quantity > 0 and entry_index is not None:
            reason: ShortBreakoutExit | None = None
            exit_price = raw_open
            if stop_price is not None and mark_high >= stop_price:
                reason = ShortBreakoutExit.STOP_LOSS
                exit_price = max(raw_open, stop_price)
            elif index - entry_index >= config.max_holding_bars:
                reason = ShortBreakoutExit.TIME_EXIT
            elif bool(signal_row["exit_signal_at_close"]):
                reason = ShortBreakoutExit.SMA_EXIT
            if reason is not None:
                closed_quantity = position.quantity
                previous_fees = position.trading_fees
                position = close_void_short(
                    position,
                    quantity=closed_quantity,
                    raw_price=exit_price,
                    liquidity=VoidShortLiquidity.TAKER,
                    costs=config.costs,
                )
                events.append(
                    {
                        "event_time": timestamp.isoformat(),
                        "event_type": reason.value,
                        "quantity": str(closed_quantity),
                        "reference_price": str(exit_price),
                        "fee_delta": str(position.trading_fees - previous_fees),
                    }
                )
                entry_index = None
                stop_price = None
                closed_this_bar = True

        if position.quantity == 0 and not closed_this_bar and bool(
            signal_row["entry_signal_at_close"]
        ):
            atr = Decimal(str(signal_row["atr"]))
            quantity = _entry_quantity(config, instrument, raw_open)
            previous_fees = position.trading_fees
            position = open_void_short_taker(
                position,
                quantity=quantity,
                raw_price=raw_open,
                costs=config.costs,
            )
            stop_price = position.average_entry_price + (
                atr * config.stop_atr_multiplier
            )
            entry_index = index
            events.append(
                {
                    "event_time": timestamp.isoformat(),
                    "event_type": "ENTRY",
                    "quantity": str(quantity),
                    "reference_price": str(raw_open),
                    "execution_price": str(position.average_entry_price),
                    "stop_price": str(stop_price),
                    "fee_delta": str(position.trading_fees - previous_fees),
                }
            )
        position_notional = position.quantity * mark_close
        equity_curve.append(
            {
                "event_time": timestamp.isoformat(),
                "equity": str(_mark_equity(config.initial_equity, position, mark_close)),
                "position_quantity": str(position.quantity),
                "position_notional": str(position_notional),
            }
        )

    if not equity_curve:
        raise ValueError("evaluation period is empty")
    metrics = _summarize(
        instrument.symbol, config, position, events, equity_curve
    )
    return ShortBreakoutResult(
        symbol=instrument.symbol,
        metrics=metrics,
        events=tuple(events),
        equity_curve=tuple(equity_curve),
    )
