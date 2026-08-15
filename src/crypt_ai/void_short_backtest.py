"""EXP-2026-0015のVOID式ショートを2時間足で再現する。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

from crypt_ai.void_short import (
    VOID_SHORT_CORE_POLICY,
    VOID_SHORT_STOP_PULLBACK_ATR,
    VoidShortAdverseState,
    VoidShortSizedLimit,
    VoidShortStopDecision,
    build_void_short_fibonacci_levels,
    build_void_short_stop_plan,
    build_void_short_take_profits,
    evaluate_void_short_stop_bar,
    pending_void_short_limits_must_cancel,
    prepare_void_short_entry_setup,
    size_void_short_limit_levels,
)
from crypt_ai.void_short_accounting import (
    VoidShortCostModel,
    VoidShortLiquidity,
    VoidShortPosition,
    apply_void_short_funding,
    close_void_short,
    open_void_short_maker,
)


VOID_SHORT_DEFAULT_MAINTENANCE_MARGIN_RATE = Decimal("0.005")


@dataclass(frozen=True)
class VoidShortInstrument:
    """ZOOMEX銘柄仕様のうちバックテストに必要な値。"""

    symbol: str
    tick_size: Decimal
    qty_step: Decimal
    min_order_qty: Decimal
    min_order_notional: Decimal


@dataclass(frozen=True)
class VoidShortBacktestConfig:
    """VOID式ショートのバックテスト実行設定。"""

    initial_equity: Decimal = Decimal("1000")
    evaluation_start: pd.Timestamp = pd.Timestamp("2022-02-01T00:00:00Z")
    evaluation_end: pd.Timestamp = pd.Timestamp("2026-01-01T00:00:00Z")
    costs: VoidShortCostModel = VoidShortCostModel()
    maintenance_margin_rate: Decimal = (
        VOID_SHORT_DEFAULT_MAINTENANCE_MARGIN_RATE
    )
    normal_stop_enabled: bool = True
    normal_stop_pullback_atr: Decimal = VOID_SHORT_STOP_PULLBACK_ATR
    entry_lot_counts: tuple[int, ...] = (1, 1, 1, 1)
    max_entry_lot_count: int = 4

    def __post_init__(self) -> None:
        """実行設定の資産、期間、清算余裕率を検査する。

        Raises:
            ValueError: 設定値が非正・非有限、または期間が逆の場合。
        """

        if not self.initial_equity.is_finite() or self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive and finite")
        if self.evaluation_start >= self.evaluation_end:
            raise ValueError("evaluation_start must precede evaluation_end")
        if (
            not self.maintenance_margin_rate.is_finite()
            or self.maintenance_margin_rate < 0
            or self.maintenance_margin_rate >= 1
        ):
            raise ValueError("maintenance_margin_rate must be in [0, 1)")
        if (
            not self.normal_stop_pullback_atr.is_finite()
            or self.normal_stop_pullback_atr <= 0
        ):
            raise ValueError("normal_stop_pullback_atr must be positive and finite")
        if len(self.entry_lot_counts) != 4 or any(
            isinstance(count, bool)
            or not isinstance(count, int)
            or count <= 0
            for count in self.entry_lot_counts
        ):
            raise ValueError("entry_lot_counts must contain four positive integers")
        if (
            isinstance(self.max_entry_lot_count, bool)
            or not isinstance(self.max_entry_lot_count, int)
            or self.max_entry_lot_count <= 0
        ):
            raise ValueError("max_entry_lot_count must be a positive integer")
        if sum(self.entry_lot_counts) > self.max_entry_lot_count:
            raise ValueError("entry_lot_counts exceed max_entry_lot_count")


@dataclass(frozen=True)
class VoidShortBacktestResult:
    """一銘柄バックテストの集計と監査イベント。"""

    symbol: str
    metrics: dict[str, object]
    events: tuple[dict[str, object], ...]
    equity_curve: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class _PendingLevel:
    """未約定指値と作成時点の情報。"""

    level: VoidShortSizedLimit
    created_index: int


def run_void_short_backtest(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    instrument: VoidShortInstrument,
    config: VoidShortBacktestConfig,
) -> VoidShortBacktestResult:
    """一銘柄のVOID式ショートを決定論的にバックテストする。

    シグナルは入力全期間で先に計算するが、評価期間内の各バーでは前バーまでの
    情報だけを使う。指値は高値接触で全量maker約定、市場性買戻しには会計設定の
    taker費用・スリッページを適用する。現在足のイベント順が分からない場合は
    清算、緊急停止、通常損切り、利確の順で不利な処理を優先する。

    Args:
        frame: 2時間足のtrade OHLCV DataFrame。
        funding: Funding時刻とFunding率のDataFrame。
        instrument: ZOOMEX銘柄仕様。
        config: 評価期間・資産・費用設定。

    Returns:
        損益指標、約定イベント、各バーのmark評価額。

    Raises:
        ValueError: 入力DataFrameの必須列、時系列、価格、Fundingに不備がある場合。
    """

    prepared = prepare_void_short_entry_setup(frame)
    _validate_funding(funding)
    _validate_instrument(instrument)
    eval_frame = prepared[
        (prepared["event_time"] >= config.evaluation_start)
        & (prepared["event_time"] < config.evaluation_end)
    ].reset_index(drop=True)
    if eval_frame.empty:
        raise ValueError(f"evaluation period is empty: {instrument.symbol}")

    funding_by_time = {
        timestamp: Decimal(str(rate))
        for timestamp, rate in zip(
            funding["event_time"], funding["funding_rate"], strict=True
        )
    }
    position = VoidShortPosition()
    pending: dict[Decimal, _PendingLevel] = {}
    plan = None
    active_anchors: tuple[Decimal, Decimal, Decimal] | None = None
    stop_state = VoidShortAdverseState()
    pending_normal_stop = False
    last_entry_index: int | None = None
    completed_targets: set[Decimal] = set()
    events: list[dict[str, object]] = []
    equity_curve: list[dict[str, object]] = []

    for index, row in eval_frame.iterrows():
        timestamp = pd.Timestamp(row["event_time"])
        normal_stop_was_pending = pending_normal_stop
        if position.quantity > 0 and timestamp in funding_by_time:
            previous = position
            position = apply_void_short_funding(
                position,
                mark_price=_decimal(row["mark_close"]),
                funding_rate=funding_by_time[timestamp],
            )
            events.append(
                _funding_event(
                    timestamp,
                    position.quantity,
                    funding_by_time[timestamp],
                    position.funding_cash_flow - previous.funding_cash_flow,
                )
            )

        if position.quantity > 0 and last_entry_index is not None:
            can_exit = index > last_entry_index
            if can_exit:
                stop_result = _evaluate_stop(
                    position=position,
                    plan=plan,
                    stop_state=stop_state,
                    row=row,
                    config=config,
                )
                stop_state = stop_result[0]
                decision = stop_result[1]
                if decision in (
                    VoidShortStopDecision.LIQUIDATION,
                    VoidShortStopDecision.EMERGENCY_STOP,
                ):
                    reason = decision.value
                    position, event = _close_event(
                        position,
                        timestamp,
                        quantity=position.quantity,
                        raw_price=_decimal(row["mark_high"]),
                        liquidity=VoidShortLiquidity.TAKER,
                        reason=reason,
                        costs=config.costs,
                    )
                    events.append(event)
                    pending.clear()
                    plan = None
                    active_anchors = None
                    pending_normal_stop = False
                    completed_targets.clear()
                elif (
                    decision == VoidShortStopDecision.NORMAL_STOP_NEXT_BAR
                    and config.normal_stop_enabled
                ):
                    pending_normal_stop = True
                    pending.clear()

            if position.quantity > 0 and normal_stop_was_pending:
                if index - last_entry_index >= VOID_SHORT_CORE_POLICY.max_holding_bars:
                    pending_normal_stop = False
                else:
                    position, event = _close_event(
                        position,
                        timestamp,
                        quantity=position.quantity,
                        raw_price=_decimal(row["open"]),
                        liquidity=VoidShortLiquidity.TAKER,
                        reason="NORMAL_STOP",
                        costs=config.costs,
                    )
                    events.append(event)
                    pending_normal_stop = False
                    plan = None
                    active_anchors = None
                    completed_targets.clear()

            if (
                position.quantity > 0
                and not pending_normal_stop
                and index - last_entry_index
                >= VOID_SHORT_CORE_POLICY.max_holding_bars
            ):
                position, event = _close_event(
                    position,
                    timestamp,
                    quantity=position.quantity,
                    raw_price=_decimal(row["open"]),
                    liquidity=VoidShortLiquidity.TAKER,
                    reason="TIME_EXIT",
                    costs=config.costs,
                )
                events.append(event)
                plan = None
                active_anchors = None
                completed_targets.clear()

            if position.quantity > 0 and not pending_normal_stop:
                position, target_events, completed_targets = _process_take_profits(
                    position=position,
                    plan=plan,
                    anchors=active_anchors,
                    completed_targets=completed_targets,
                    row=row,
                    timestamp=timestamp,
                    instrument=instrument,
                    costs=config.costs,
                )
                events.extend(target_events)
                if position.quantity == 0:
                    plan = None
                    active_anchors = None
                    completed_targets.clear()

        if position.quantity == 0:
            if plan is None:
                pending.clear()
                active_anchors = None
            pending, plan, new_anchors = _update_pending_levels(
                pending=pending,
                plan=plan,
                index=index,
                row=row,
                instrument=instrument,
                initial_equity=config.initial_equity,
                current_equity=_mark_equity(
                    config.initial_equity, position, _decimal(row["mark_close"])
                ),
                lot_counts=config.entry_lot_counts,
                max_total_lot_count=config.max_entry_lot_count,
            )
            if new_anchors is not None:
                active_anchors = new_anchors
        if pending:
            if pending_void_short_limits_must_cancel(
                pending_bars=max(
                    index - item.created_index for item in pending.values()
                ),
                downtrend_regime=bool(row["downtrend_regime_for_bar"]),
                state_known=bool(row["trend_state_known_for_bar"]),
            ):
                pending.clear()
            else:
                touched = tuple(
                    item
                    for item in pending.values()
                    if _decimal(row["high"]) >= item.level.limit_price
                )
                for item in touched:
                    pending.pop(item.level.ratio, None)
                    previous = position
                    position = open_void_short_maker(
                        position,
                        quantity=item.level.quantity,
                        limit_price=item.level.limit_price,
                        costs=config.costs,
                    )
                    last_entry_index = index
                    stop_state = VoidShortAdverseState()
                    plan = plan or build_void_short_stop_plan(
                        rally_start_price=active_anchors[0],
                        rally_peak_price=active_anchors[1],
                        rebound_low_price=active_anchors[2],
                        tick_size=instrument.tick_size,
                    )
                    events.append(
                        _fill_event(
                            timestamp,
                            "ENTRY",
                            position.quantity - previous.quantity,
                            item.level.limit_price,
                            previous,
                            position,
                            item.level.ratio,
                        )
                    )
        equity_curve.append(
            {
                "event_time": timestamp.isoformat(),
                "equity": str(
                    _mark_equity(
                        config.initial_equity,
                        position,
                        _decimal(row["mark_close"]),
                    )
                ),
                "position_quantity": str(position.quantity),
                "position_notional": str(
                    position.quantity * _decimal(row["mark_close"])
                ),
            }
        )

    if position.quantity > 0:
        last_row = eval_frame.iloc[-1]
        position, event = _close_event(
            position,
            pd.Timestamp(last_row["event_time"]),
            quantity=position.quantity,
            raw_price=_decimal(last_row["close"]),
            liquidity=VoidShortLiquidity.TAKER,
            reason="END_OF_DATA",
            costs=config.costs,
        )
        events.append(event)
        equity_curve[-1]["equity"] = str(config.initial_equity + position.cash_flow)
        equity_curve[-1]["position_quantity"] = "0"
        equity_curve[-1]["position_notional"] = "0"

    metrics = _summarize_metrics(
        instrument.symbol,
        config.initial_equity,
        position,
        events,
        equity_curve,
    )
    return VoidShortBacktestResult(
        symbol=instrument.symbol,
        metrics=metrics,
        events=tuple(events),
        equity_curve=tuple(equity_curve),
    )


def _update_pending_levels(
    *,
    pending: dict[Decimal, _PendingLevel],
    plan: object,
    index: int,
    row: pd.Series,
    instrument: VoidShortInstrument,
    initial_equity: Decimal,
    current_equity: Decimal,
    lot_counts: tuple[int, ...],
    max_total_lot_count: int,
) -> tuple[
    dict[Decimal, _PendingLevel],
    object,
    tuple[Decimal, Decimal, Decimal] | None,
]:
    """準備完了イベントを次足の有効な未約定指値へ変換する。"""

    if pending or not bool(row["entry_setup_ready_for_bar"]):
        return pending, plan, None
    anchors = (
        row["rally_start_price_for_bar"],
        row["rally_peak_price_for_bar"],
        row["rebound_low_price_for_bar"],
    )
    if any(pd.isna(value) for value in anchors):
        return pending, plan, None
    anchor_values = tuple(_decimal(value) for value in anchors)
    if not anchor_values[0] < anchor_values[2] < anchor_values[1]:
        return {}, None, None
    levels = build_void_short_fibonacci_levels(
        rally_start_price=anchor_values[0],
        rally_peak_price=anchor_values[1],
        rebound_low_price=anchor_values[2],
        current_price=_decimal(row["open"]),
        tick_size=instrument.tick_size,
    )
    sized = size_void_short_limit_levels(
        levels,
        initial_equity=initial_equity,
        current_equity=current_equity,
        qty_step=instrument.qty_step,
        min_order_qty=instrument.min_order_qty,
        min_order_notional=instrument.min_order_notional,
        lot_counts=lot_counts,
        max_total_lot_count=max_total_lot_count,
    )
    return (
        {level.ratio: _PendingLevel(level, index) for level in sized},
        build_void_short_stop_plan(
            rally_start_price=anchor_values[0],
            rally_peak_price=anchor_values[1],
            rebound_low_price=anchor_values[2],
            tick_size=instrument.tick_size,
        ),
        anchor_values,
    ) if sized else ({}, None, None)


def _evaluate_stop(
    *,
    position: VoidShortPosition,
    plan: object,
    stop_state: VoidShortAdverseState,
    row: pd.Series,
    config: VoidShortBacktestConfig,
) -> tuple[VoidShortAdverseState, VoidShortStopDecision]:
    """現在足のmark価格から損切り判定を得る。"""

    if plan is None:
        return stop_state, VoidShortStopDecision.HOLD
    liquidation_price = position.average_entry_price * (
        Decimal("2") - config.maintenance_margin_rate
    )
    result = evaluate_void_short_stop_bar(
        plan=plan,
        state=stop_state,
        mark_high=_decimal(row["mark_high"]),
        mark_close=_decimal(row["mark_close"]),
        atr=_decimal(row["atr14"]),
        liquidation_price=liquidation_price,
        normal_stop_pullback_atr=config.normal_stop_pullback_atr,
    )
    return result.state, result.decision


def _process_take_profits(
    *,
    position: VoidShortPosition,
    plan: object,
    anchors: tuple[Decimal, Decimal, Decimal] | None,
    completed_targets: set[Decimal],
    row: pd.Series,
    timestamp: pd.Timestamp,
    instrument: VoidShortInstrument,
    costs: VoidShortCostModel,
) -> tuple[VoidShortPosition, list[dict[str, object]], set[Decimal]]:
    """同一足の利確候補を浅い水準から順に処理する。"""

    if plan is None or anchors is None:
        return position, [], completed_targets
    targets = build_void_short_take_profits(
        rally_start_price=anchors[0],
        rally_peak_price=anchors[1],
        average_entry_price=position.average_entry_price,
        open_quantity=position.quantity,
        tick_size=instrument.tick_size,
        qty_step=instrument.qty_step,
    )
    result_events: list[dict[str, object]] = []
    for target in sorted(targets, key=lambda item: item.limit_price, reverse=True):
        if target.ratio in completed_targets or _decimal(row["low"]) > target.limit_price:
            continue
        previous = position
        position = close_void_short(
            position,
            quantity=target.quantity,
            raw_price=target.limit_price,
            liquidity=VoidShortLiquidity.MAKER,
            costs=costs,
        )
        completed_targets.add(target.ratio)
        result_events.append(
            _fill_event(
                timestamp,
                "TAKE_PROFIT",
                target.quantity,
                target.limit_price,
                previous,
                position,
                target.ratio,
            )
        )
        if position.quantity == 0:
            break
    return position, result_events, completed_targets


def _close_event(
    position: VoidShortPosition,
    timestamp: pd.Timestamp,
    *,
    quantity: Decimal,
    raw_price: Decimal,
    liquidity: VoidShortLiquidity,
    reason: str,
    costs: VoidShortCostModel,
) -> tuple[VoidShortPosition, dict[str, object]]:
    """決済を会計へ適用し、監査イベントを作る。"""

    previous = position
    updated = close_void_short(
        position,
        quantity=quantity,
        raw_price=raw_price,
        liquidity=liquidity,
        costs=costs,
    )
    return updated, _fill_event(
        timestamp,
        reason,
        quantity,
        raw_price,
        previous,
        updated,
        None,
    )


def _fill_event(
    timestamp: pd.Timestamp,
    event_type: str,
    quantity: Decimal,
    raw_price: Decimal,
    previous: VoidShortPosition,
    updated: VoidShortPosition,
    ratio: Decimal | None,
) -> dict[str, object]:
    """建玉差分から約定監査イベントを作る。"""

    return {
        "event_time": timestamp.isoformat(),
        "event_type": event_type,
        "quantity": str(quantity),
        "reference_price": str(raw_price),
        "fee_delta": str(updated.trading_fees - previous.trading_fees),
        "funding_delta": "0",
        "ratio": str(ratio) if ratio is not None else None,
        "position_quantity_after": str(updated.quantity),
    }


def _funding_event(
    timestamp: pd.Timestamp,
    quantity: Decimal,
    rate: Decimal,
    amount: Decimal,
) -> dict[str, object]:
    """Funding受払監査イベントを作る。"""

    return {
        "event_time": timestamp.isoformat(),
        "event_type": "FUNDING",
        "quantity": str(quantity),
        "funding_rate": str(rate),
        "funding_delta": str(amount),
        "fee_delta": "0",
        "ratio": None,
    }


def _summarize_metrics(
    symbol: str,
    initial_equity: Decimal,
    position: VoidShortPosition,
    events: list[dict[str, object]],
    equity_curve: list[dict[str, object]],
) -> dict[str, object]:
    """イベントと評価額から一銘柄の主要指標を集計する。"""

    equities = [Decimal(str(row["equity"])) for row in equity_curve]
    peak = initial_equity
    max_drawdown = Decimal("0")
    for equity in equities:
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - Decimal("1"))
    event_types = [str(event["event_type"]) for event in events]
    final_equity = equities[-1] if equities else initial_equity
    return {
        "symbol": symbol,
        "initial_equity": str(initial_equity),
        "final_equity": str(final_equity),
        "net_pnl": str(final_equity - initial_equity),
        "return_rate": str(final_equity / initial_equity - Decimal("1")),
        "max_drawdown": str(max_drawdown),
        "entry_count": event_types.count("ENTRY"),
        "take_profit_count": event_types.count("TAKE_PROFIT"),
        "normal_stop_count": event_types.count("NORMAL_STOP"),
        "emergency_stop_count": event_types.count("EMERGENCY_STOP"),
        "liquidation_count": event_types.count("LIQUIDATION"),
        "time_exit_count": event_types.count("TIME_EXIT"),
        "funding_event_count": event_types.count("FUNDING"),
        "total_fees": str(position.trading_fees),
        "total_funding_cash_flow": str(position.funding_cash_flow),
        "open_position_at_end": position.quantity > 0,
        "max_position_quantity": str(
            max(
                (Decimal(str(row["position_quantity"])) for row in equity_curve),
                default=Decimal("0"),
            )
        ),
        "max_position_notional": str(
            max(
                (Decimal(str(row["position_notional"])) for row in equity_curve),
                default=Decimal("0"),
            )
        ),
    }


def _mark_equity(
    initial_equity: Decimal, position: VoidShortPosition, mark_price: Decimal
) -> Decimal:
    """初期資産と建玉キャッシュフローからmark評価額を返す。"""

    return initial_equity + position.cash_flow - position.quantity * mark_price


def _decimal(value: object) -> Decimal:
    """欠損でない値をDecimalへ変換する。"""

    if pd.isna(value):
        raise ValueError("price or anchor must not be missing")
    return Decimal(str(value))


def _validate_funding(funding: pd.DataFrame) -> None:
    """Funding DataFrameの最小構造を検査する。"""

    required = {"event_time", "funding_rate"}
    if not required.issubset(funding.columns) or funding.empty:
        raise ValueError("funding must contain event_time and funding_rate")
    times = pd.to_datetime(funding["event_time"], utc=True, errors="coerce")
    if times.isna().any() or times.duplicated().any() or not times.is_monotonic_increasing:
        raise ValueError("funding event_time must be unique and sorted")
    rates = pd.to_numeric(funding["funding_rate"], errors="coerce")
    if rates.isna().any():
        raise ValueError("funding_rate must be numeric")


def _validate_instrument(instrument: VoidShortInstrument) -> None:
    """銘柄仕様の正値を検査する。"""

    if not instrument.symbol:
        raise ValueError("instrument symbol must not be empty")
    for name in ("tick_size", "qty_step", "min_order_qty", "min_order_notional"):
        value = getattr(instrument, name)
        if not value.is_finite() or value <= 0:
            raise ValueError(f"instrument {name} must be positive and finite")
