"""ロング建玉を固定ロットで段階的に買い増す研究用会計。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

import pandas as pd

from crypt_ai.allocation import AllocationConfig
from crypt_ai.portfolio import (
    AllocatedPortfolioResult,
    _decimal,
    _funding_rate,
    _normalize_frames,
    _price,
    _rejection_event,
    _summarize,
)
from crypt_ai.research import CostModel


@dataclass
class _LongLadderPosition:
    """段階買い増し中のロング建玉。

    Attributes:
        quantity: 合計保有数量。
        cost_basis: entry手数料を除く約定元本合計。
        entry_fees: entryと買い増しで支払った手数料合計。
        anchor_price: 初回entry約定価格。
        entry_atr: 初回entry判断時に固定したATR。
        lot_count: 約定済みロット数。
        triggered_levels: 発火済みの買い増し段階番号。
        pending_levels: 次足openで注文する段階番号。
    """

    quantity: Decimal
    cost_basis: Decimal
    entry_fees: Decimal
    anchor_price: Decimal
    entry_atr: Decimal | None
    lot_count: int = 1
    triggered_levels: set[int] = field(default_factory=set)
    pending_levels: list[int] = field(default_factory=list)


def run_long_ladder_portfolio(
    frames: Mapping[str, pd.DataFrame],
    config: AllocationConfig,
    cost_model: CostModel,
    *,
    fixed_drawdowns: Sequence[Decimal] | None = None,
    atr_multipliers: Sequence[Decimal] | None = None,
    max_lots_per_symbol: int = 5,
    reject_interpolated_entries: bool = True,
) -> AllocatedPortfolioResult:
    """初回entry後の下落段階ごとに固定1ロットを追加する。

    買い増し段階は確定バーのlowで発火し、次バーopenで約定する。価格下落率と
    entry時固定ATR倍率はどちらか一方だけ指定する。シグナル退出は買い増しより
    先に処理し、未約定の段階を破棄する。

    Args:
        frames: 同期した銘柄別OHLC、longシグナル、任意のATRとFunding。
        config: 固定ロットと資金上限を含む配分設定。
        cost_model: 手数料、spread、slippageの仮定。
        fixed_drawdowns: 初回約定価格からの正の下落率。
        atr_multipliers: entry時ATRに掛ける正の倍率。
        max_lots_per_symbol: 初回を含む1銘柄の最大ロット数。
        reject_interpolated_entries: 補間バーのentryとtriggerを拒否するか。

    Returns:
        買い増しを含む会計指標、監査イベント、equity曲線。

    Raises:
        ValueError: 段階設定、ATR、入力価格、またはシグナルが不正な場合。
    """

    levels, mode = _normalize_ladder_levels(
        fixed_drawdowns, atr_multipliers, max_lots_per_symbol
    )
    normalized = _normalize_frames(frames, config)
    timestamps = sorted(next(iter(normalized.values())))
    positions: dict[str, _LongLadderPosition] = {}
    previous_desired = {symbol: 0 for symbol in normalized}
    cash = config.initial_equity
    events: list[dict[str, object]] = []
    equity_curve: list[dict[str, object]] = []

    for timestamp in timestamps:
        rows = {symbol: values[timestamp] for symbol, values in normalized.items()}

        for symbol in sorted(rows):
            position = positions.get(symbol)
            if position is None:
                continue
            funding_rate = _funding_rate(rows[symbol], symbol)
            if funding_rate == 0:
                continue
            funding_notional = position.quantity * _price(
                rows[symbol].open, f"{symbol}.open"
            )
            funding_delta = -funding_notional * funding_rate
            cash += funding_delta
            events.append(
                {
                    "event_time": timestamp.isoformat(),
                    "event_type": "FUNDING",
                    "side": "long",
                    "symbol": symbol,
                    "notional": str(funding_notional),
                    "funding_rate": str(funding_rate),
                    "funding_delta": str(funding_delta),
                }
            )

        for symbol in sorted(rows):
            position = positions.get(symbol)
            desired = _long_desired(rows[symbol], symbol)
            if position is None or desired == 1:
                continue
            raw_open = _price(rows[symbol].open, f"{symbol}.open")
            execution_price = cost_model.sell_price(raw_open)
            gross = position.quantity * execution_price
            fee = gross * cost_model.fee_rate
            cash += gross - fee
            pnl = gross - fee - position.cost_basis - position.entry_fees
            events.append(
                {
                    "event_time": timestamp.isoformat(),
                    "event_type": "EXIT",
                    "side": "long",
                    "symbol": symbol,
                    "quantity": str(position.quantity),
                    "notional": str(gross),
                    "lot_count": position.lot_count,
                    "fee": str(fee),
                    "pnl": str(pnl),
                    "had_additions": position.lot_count > 1,
                }
            )
            positions.pop(symbol)

        for symbol in sorted(rows):
            position = positions.get(symbol)
            if position is None or not position.pending_levels:
                continue
            if _long_desired(rows[symbol], symbol) == 0:
                position.pending_levels.clear()
                continue
            pending = tuple(position.pending_levels)
            position.pending_levels.clear()
            for level_index in pending:
                if position.lot_count >= max_lots_per_symbol:
                    break
                accepted = _open_ladder_lot(
                    timestamp,
                    symbol,
                    rows[symbol],
                    position,
                    positions,
                    config,
                    cost_model,
                    cash,
                    events,
                    event_type="ADD",
                    level_index=level_index,
                )
                if accepted is None:
                    continue
                cash = accepted

        for symbol in sorted(rows):
            if symbol in positions:
                continue
            desired = _long_desired(rows[symbol], symbol)
            if desired != 1 or previous_desired[symbol] == 1:
                continue
            if reject_interpolated_entries and bool(
                getattr(rows[symbol], "is_interpolated", False)
            ):
                events.append(
                    _rejection_event(timestamp, symbol, "long", "interpolated_bar")
                )
                continue
            entry_atr = None
            if mode == "atr":
                entry_atr = _entry_atr(rows[symbol], symbol)
                if entry_atr is None:
                    events.append(
                        _rejection_event(timestamp, symbol, "long", "missing_entry_atr")
                    )
                    continue
            position = _LongLadderPosition(
                quantity=Decimal("0"),
                cost_basis=Decimal("0"),
                entry_fees=Decimal("0"),
                anchor_price=Decimal("0"),
                entry_atr=entry_atr,
                lot_count=0,
            )
            accepted = _open_ladder_lot(
                timestamp,
                symbol,
                rows[symbol],
                position,
                positions,
                config,
                cost_model,
                cash,
                events,
                event_type="ENTRY",
                level_index=None,
            )
            if accepted is not None:
                cash = accepted
                position.anchor_price = _price(
                    events[-1]["execution_price"], "execution_price"
                )
                positions[symbol] = position

        for symbol, row in rows.items():
            previous_desired[symbol] = _long_desired(row, symbol)

        for symbol, position in positions.items():
            row = rows[symbol]
            if reject_interpolated_entries and bool(
                getattr(row, "is_interpolated", False)
            ):
                continue
            low = _price(row.low, f"{symbol}.low")
            thresholds = _ladder_thresholds(position, levels, mode)
            for level_index, threshold in enumerate(thresholds, start=1):
                if (
                    level_index not in position.triggered_levels
                    and low <= threshold
                ):
                    position.triggered_levels.add(level_index)
                    position.pending_levels.append(level_index)
                    events.append(
                        {
                            "event_time": timestamp.isoformat(),
                            "event_type": "ADD_TRIGGER",
                            "side": "long",
                            "symbol": symbol,
                            "level_index": level_index,
                            "threshold_price": str(threshold),
                            "observed_low": str(low),
                        }
                    )

        marked_value = Decimal("0")
        marked_gross = Decimal("0")
        allocated = Decimal("0")
        for symbol, position in positions.items():
            close = _price(rows[symbol].close, f"{symbol}.close")
            value = position.quantity * close
            marked_value += value
            marked_gross += value
            allocated += position.cost_basis
        equity = cash + marked_value
        equity_curve.append(
            {
                "event_time": timestamp.isoformat(),
                "cash": str(cash),
                "equity": str(equity),
                "position_notional": str(marked_gross),
                "allocated_gross_notional": str(allocated),
                "open_symbol_count": len(positions),
                "open_position_count": len(positions),
            }
        )

    return AllocatedPortfolioResult(
        metrics=_summarize(config.initial_equity, equity_curve, events),
        events=tuple(events),
        equity_curve=tuple(equity_curve),
    )


def _normalize_ladder_levels(
    fixed_drawdowns: Sequence[Decimal] | None,
    atr_multipliers: Sequence[Decimal] | None,
    max_lots_per_symbol: int,
) -> tuple[tuple[Decimal, ...], str]:
    """買い増し段階を検証する。

    Args:
        fixed_drawdowns: 初回価格からの下落率。
        atr_multipliers: entry時ATRの倍率。
        max_lots_per_symbol: 初回を含む最大ロット数。

    Returns:
        昇順の段階と`fixed`または`atr`。

    Raises:
        ValueError: 指定方法、段階数、値、または順序が不正な場合。
    """

    if (fixed_drawdowns is None) == (atr_multipliers is None):
        raise ValueError("specify exactly one ladder level mode")
    if max_lots_per_symbol < 2:
        raise ValueError("max_lots_per_symbol must be at least 2")
    raw = fixed_drawdowns if fixed_drawdowns is not None else atr_multipliers
    assert raw is not None
    levels = tuple(_decimal(value, "ladder_level") for value in raw)
    if len(levels) != max_lots_per_symbol - 1:
        raise ValueError("ladder level count must equal max lots minus one")
    if any(value <= 0 for value in levels) or tuple(sorted(levels)) != levels:
        raise ValueError("ladder levels must be positive and ascending")
    if len(set(levels)) != len(levels):
        raise ValueError("ladder levels must be unique")
    if fixed_drawdowns is not None and any(value >= 1 for value in levels):
        raise ValueError("fixed drawdowns must be below one")
    return levels, "fixed" if fixed_drawdowns is not None else "atr"


def _long_desired(row: object, symbol: str) -> int:
    """行からlongシグナルを検証して返す。"""

    value = getattr(row, "desired_long_position", getattr(row, "desired_position", 0))
    try:
        desired = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid desired long position: {symbol}") from error
    if desired not in (0, 1):
        raise ValueError(f"invalid desired long position: {symbol}")
    return desired


def _entry_atr(row: object, symbol: str) -> Decimal | None:
    """entry行のATRを読み、欠損ならNoneを返す。"""

    value = getattr(row, "entry_atr", None)
    if value is None or pd.isna(value):
        return None
    atr = _decimal(value, f"{symbol}.entry_atr")
    if atr <= 0:
        raise ValueError(f"{symbol}.entry_atr must be positive")
    return atr


def _ladder_thresholds(
    position: _LongLadderPosition,
    levels: tuple[Decimal, ...],
    mode: str,
) -> tuple[Decimal, ...]:
    """建玉の初回価格から買い増し価格を計算する。"""

    if mode == "fixed":
        return tuple(position.anchor_price * (Decimal("1") - level) for level in levels)
    if position.entry_atr is None:
        raise ValueError("entry_atr is required for ATR ladder")
    thresholds = tuple(
        position.anchor_price - position.entry_atr * level for level in levels
    )
    if any(value <= 0 for value in thresholds):
        raise ValueError("ATR ladder threshold must be positive")
    return thresholds


def _open_ladder_lot(
    timestamp: pd.Timestamp,
    symbol: str,
    row: object,
    position: _LongLadderPosition,
    positions: dict[str, _LongLadderPosition],
    config: AllocationConfig,
    cost_model: CostModel,
    cash: Decimal,
    events: list[dict[str, object]],
    *,
    event_type: str,
    level_index: int | None,
) -> Decimal | None:
    """固定1ロットを検査して建玉へ加算する。

    Args:
        timestamp: 約定時刻。
        symbol: 対象銘柄。
        row: 約定足の市場行。
        position: 加算対象の建玉。
        positions: 他銘柄を含む現在建玉。
        config: ロットと配分上限。
        cost_model: 約定費用モデル。
        cash: 約定直前の現金。
        events: 監査イベントの格納先。
        event_type: `ENTRY`または`ADD`。
        level_index: 買い増し段階。初回はNone。

    Returns:
        約定後cash。拒否時はNone。
    """

    if event_type == "ENTRY" and len(positions) >= config.max_concurrent_long_positions:
        events.append(_rejection_event(timestamp, symbol, "long", "max_concurrent_positions"))
        return None
    total_allocated = sum((item.cost_basis for item in positions.values()), Decimal("0"))
    if event_type == "ADD":
        total_allocated -= position.cost_basis
    requested_symbol = position.cost_basis + config.lot_notional
    requested_total = total_allocated + requested_symbol
    reason = None
    if requested_symbol > config.per_symbol_max_notional:
        reason = "per_symbol_cap"
    elif requested_total > config.max_long_gross_notional or requested_total > config.max_total_gross_notional:
        reason = "gross_cap"
    raw_open = _price(row.open, f"{symbol}.open")
    execution_price = cost_model.buy_price(raw_open)
    fee = config.lot_notional * cost_model.fee_rate
    required_cash = config.lot_notional + fee
    if reason is None and cash - required_cash < config.reserve_cash:
        reason = "insufficient_cash"
    if reason is not None:
        rejection = _rejection_event(timestamp, symbol, "long", reason)
        rejection["requested_event_type"] = event_type
        rejection["level_index"] = level_index
        events.append(rejection)
        return None
    quantity = config.lot_notional / execution_price
    position.quantity += quantity
    position.cost_basis += config.lot_notional
    position.entry_fees += fee
    position.lot_count += 1
    events.append(
        {
            "event_time": timestamp.isoformat(),
            "event_type": event_type,
            "side": "long",
            "symbol": symbol,
            "quantity": str(quantity),
            "notional": str(config.lot_notional),
            "lot_count": position.lot_count,
            "execution_price": str(execution_price),
            "fee": str(fee),
            "level_index": level_index,
        }
    )
    return cash - required_cash
