"""複数銘柄のシグナルを資金配分層経由で会計する研究用ポートフォリオ。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import pandas as pd

from crypt_ai.allocation import AllocationConfig, AllocationState, PortfolioAllocator
from crypt_ai.research import CostModel


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
class AllocatedPortfolioResult:
    """配分承認後の複数銘柄ポートフォリオ会計結果。

    Attributes:
        metrics: 最終資産、最大DD、配分拒否数などの集計。
        events: 約定と配分拒否の監査イベント。
        equity_curve: 各時刻のequity、現金、評価元本。
    """

    metrics: dict[str, object]
    events: tuple[dict[str, object], ...]
    equity_curve: tuple[dict[str, object], ...]


@dataclass
class _PortfolioPosition:
    """1銘柄1ロットの会計に必要な建玉情報。

    Attributes:
        side: `long`または`short`。
        quantity: 保有数量。
        entry_price: 約定時の価格。
        notional: 配分承認された固定元本。
        lot_count: entry時に承認されたロット数。
    """

    side: str
    quantity: Decimal
    entry_price: Decimal
    notional: Decimal
    lot_count: int


def run_allocated_portfolio(
    frames: Mapping[str, pd.DataFrame],
    config: AllocationConfig,
    cost_model: CostModel,
    *,
    reject_interpolated_entries: bool = True,
) -> AllocatedPortfolioResult:
    """複数銘柄のlong/shortシグナルを配分承認付きで同時に会計する。

    `desired_position`はlongシグナルの別名として扱い、shortを使う場合は
    `desired_short_position`をDataFrameへ追加する。いずれも0から1になった時に
    固定ロットを要求し、`PortfolioAllocator`が承認した場合だけ建てる。任意の
    `desired_long_lot_count`または`desired_short_lot_count`列があればentry時の
    ロット数に使い、列がなければ1ロットとする。保有中のロット変更は行わない。
    1から0になった時は決済して配分枠を解放する。銘柄、sideの処理順は固定する。

    入力行に任意の`funding_rate`列がある場合、各時刻の既存建玉へFundingを適用する。
    正のFundingはlongが支払い、shortが受け取る。欠損値または列なしは0として扱う。
    Fundingは、その時刻の新規entryより前、既存建玉のexitより前に適用する。

    Shortは、1ロット元本を担保相当額としてcashから取り置き、mark時には
    `entry_price - mark_price`の含み損益を反映する研究用会計である。取引所の
    証拠金、清算、数量刻みは別の実行・リスク層で検証する。

    Args:
        frames: 銘柄をキーとする、`event_time`、`open`、`close`、任意の
            `funding_rate`、
            `desired_position`または`desired_long_position`を含み、必要に応じて
            `desired_short_position`も含む同一時刻の日足DataFrame。
        config: 資産、固定ロット、配分上限を定義する設定。
        cost_model: 売買手数料、spread、slippageの仮定。
        reject_interpolated_entries: 補間バー上の新規注文を拒否するか。

    Returns:
        配分後の会計指標、監査イベント、equity曲線。

    Raises:
        ValueError: 銘柄、時刻、価格、シグナルの入力が不正な場合。
    """

    normalized = _normalize_frames(frames, config)
    timestamps = sorted(next(iter(normalized.values())))
    state = AllocationState(config.initial_equity)
    allocator = PortfolioAllocator(config)
    cash = config.initial_equity
    positions: dict[tuple[str, str], _PortfolioPosition] = {}
    previous_desired: dict[tuple[str, str], int] = {
        (symbol, side): 0
        for symbol in normalized
        for side in ("long", "short")
    }
    events: list[dict[str, object]] = []
    equity_curve: list[dict[str, object]] = []

    for timestamp in timestamps:
        rows = {symbol: values[timestamp] for symbol, values in normalized.items()}

        # Fundingは時刻時点で既に保有している建玉へだけ適用する。
        for symbol in sorted(rows):
            row = rows[symbol]
            funding_rate = _funding_rate(row, symbol)
            if funding_rate == 0:
                continue
            close = _price(row.close, f"{symbol}.close")
            for side in ("long", "short"):
                position = positions.get((symbol, side))
                if position is None:
                    continue
                notional = position.quantity * close
                funding_delta = (
                    -notional * funding_rate
                    if side == "long"
                    else notional * funding_rate
                )
                cash += funding_delta
                events.append(
                    {
                        "event_time": timestamp.isoformat(),
                        "event_type": "FUNDING",
                        "side": side,
                        "symbol": symbol,
                        "notional": str(notional),
                        "funding_rate": str(funding_rate),
                        "funding_delta": str(funding_delta),
                    }
                )

        # 決済を先に処理し、同じ足で解放された枠を別銘柄が利用できるようにする。
        for symbol in sorted(rows):
            row = rows[symbol]
            raw_open = _price(row.open, f"{symbol}.open")
            for side in ("long", "short"):
                key = (symbol, side)
                position = positions.get(key)
                if position is None or _desired_position(
                    row, symbol, _signal_column(side)
                ) != 0:
                    continue
                execution_price = (
                    cost_model.sell_price(raw_open)
                    if side == "long"
                    else cost_model.buy_price(raw_open)
                )
                fee = position.quantity * execution_price * cost_model.fee_rate
                if side == "long":
                    gross = position.quantity * execution_price
                    cash += gross - fee
                    pnl = None
                else:
                    gross = position.quantity * execution_price
                    pnl = position.quantity * (position.entry_price - execution_price)
                    cash += position.notional + pnl - fee
                allocator.release(
                    state,
                    side=side,
                    symbol=symbol,
                    lot_count=position.lot_count,
                )
                positions.pop(key)
                event = {
                    "event_time": timestamp.isoformat(),
                    "event_type": "EXIT",
                    "side": side,
                    "symbol": symbol,
                    "quantity": str(position.quantity),
                    "notional": str(gross),
                    "fee": str(fee),
                }
                if pnl is not None:
                    event["pnl"] = str(pnl)
                events.append(event)

        # 新規配分も銘柄昇順、long先行で処理し、同時刻の結果を再現可能にする。
        for symbol in sorted(rows):
            row = rows[symbol]
            for side in ("long", "short"):
                key = (symbol, side)
                if (
                    _desired_position(row, symbol, _signal_column(side)) != 1
                    or previous_desired[key] == 1
                    or key in positions
                ):
                    continue
                if reject_interpolated_entries and bool(
                    getattr(row, "is_interpolated", False)
                ):
                    events.append(
                        _rejection_event(timestamp, symbol, side, "interpolated_bar")
                    )
                    continue
                lot_count = _desired_lot_count(row, symbol, side)
                decision = allocator.evaluate_order(
                    state,
                    side=side,
                    symbol=symbol,
                    lot_count=lot_count,
                )
                if not decision.accepted:
                    events.append(
                        _rejection_event(timestamp, symbol, side, decision.reason)
                    )
                    continue
                raw_open = _price(row.open, f"{symbol}.open")
                execution_price = (
                    cost_model.buy_price(raw_open)
                    if side == "long"
                    else cost_model.sell_price(raw_open)
                )
                fee = decision.approved_notional * cost_model.fee_rate
                required_cash = decision.approved_notional + fee
                if required_cash > cash:
                    events.append(
                        _rejection_event(timestamp, symbol, side, "insufficient_cash")
                    )
                    continue
                quantity = decision.approved_notional / execution_price
                cash -= required_cash
                allocator.try_open(
                    state,
                    side=side,
                    symbol=symbol,
                    lot_count=lot_count,
                )
                positions[key] = _PortfolioPosition(
                    side=side,
                    quantity=quantity,
                    entry_price=execution_price,
                    notional=decision.approved_notional,
                    lot_count=lot_count,
                )
                events.append(
                    {
                        "event_time": timestamp.isoformat(),
                        "event_type": "ENTRY",
                        "side": side,
                        "symbol": symbol,
                        "quantity": str(quantity),
                        "notional": str(decision.approved_notional),
                        "lot_count": lot_count,
                        "execution_price": str(execution_price),
                        "fee": str(fee),
                        "allocation_reason": decision.reason,
                    }
                )

        for symbol, row in rows.items():
            for side in ("long", "short"):
                previous_desired[(symbol, side)] = _desired_position(
                    row, symbol, _signal_column(side)
                )

        mark_notional = Decimal("0")
        marked_value = Decimal("0")
        for (symbol, side), position in positions.items():
            close = _price(rows[symbol].close, f"{symbol}.close")
            current_notional = position.quantity * close
            mark_notional += current_notional
            if side == "long":
                marked_value += current_notional
            else:
                marked_value += position.notional + position.quantity * (
                    position.entry_price - close
                )
        equity = cash + marked_value
        state.equity = equity
        equity_curve.append(
            {
                "event_time": timestamp.isoformat(),
                "cash": str(cash),
                "equity": str(equity),
                "position_notional": str(mark_notional),
                "allocated_gross_notional": str(state.total_gross_notional),
                "open_symbol_count": len({symbol for symbol, _ in positions}),
                "open_position_count": len(positions),
            }
        )

    metrics = _summarize(
        config.initial_equity,
        equity_curve,
        events,
    )
    return AllocatedPortfolioResult(
        metrics=metrics,
        events=tuple(events),
        equity_curve=tuple(equity_curve),
    )


def run_allocated_long_portfolio(
    frames: Mapping[str, pd.DataFrame],
    config: AllocationConfig,
    cost_model: CostModel,
    *,
    reject_interpolated_entries: bool = True,
) -> AllocatedPortfolioResult:
    """複数銘柄のlongシグナルだけを配分承認付きで同時に会計する。

    Args:
        frames: `desired_position`を含む銘柄別DataFrame。
        config: 資産、固定ロット、配分上限を定義する設定。
        cost_model: 売買手数料、spread、slippageの仮定。
        reject_interpolated_entries: 補間バー上の新規注文を拒否するか。

    Returns:
        配分後の会計指標、監査イベント、equity曲線。

    Raises:
        ValueError: 銘柄、時刻、価格、シグナルの入力が不正な場合。
    """

    return run_allocated_portfolio(
        frames,
        config,
        cost_model,
        reject_interpolated_entries=reject_interpolated_entries,
    )


def _normalize_frames(
    frames: Mapping[str, pd.DataFrame], config: AllocationConfig
) -> dict[str, dict[pd.Timestamp, object]]:
    """入力銘柄の時刻と必須列を検証して行辞書へ変換する。

    Args:
        frames: 銘柄別シグナルDataFrame。
        config: 許可銘柄を含む配分設定。

    Returns:
        銘柄ごとの時刻から行への辞書。

    Raises:
        ValueError: 銘柄、時刻、列、または時刻集合が不正な場合。
    """

    if not frames:
        raise ValueError("frames must not be empty")
    normalized: dict[str, dict[pd.Timestamp, object]] = {}
    required = {"event_time", "open", "close"}
    for symbol, frame in frames.items():
        if symbol not in config.allowed_symbols:
            raise ValueError(f"symbol is not allowed by allocation config: {symbol}")
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"missing portfolio columns for {symbol}: {sorted(missing)}")
        if not {
            "desired_position",
            "desired_long_position",
            "desired_short_position",
        }.intersection(frame.columns):
            raise ValueError(f"missing portfolio signal columns for {symbol}")
        source = frame.copy()
        if "desired_long_position" not in source:
            source["desired_long_position"] = source.get(
                "desired_position", pd.Series(0, index=source.index)
            )
        if "desired_short_position" not in source:
            source["desired_short_position"] = 0
        for side in ("long", "short"):
            lot_column = f"desired_{side}_lot_count"
            if lot_column not in source:
                source[lot_column] = 1
            numeric_lots = pd.to_numeric(source[lot_column], errors="coerce")
            if (
                numeric_lots.isna().any()
                or not numeric_lots.gt(0).all()
                or not numeric_lots.mod(1).eq(0).all()
            ):
                raise ValueError(f"{lot_column} must contain positive integers: {symbol}")
            source[lot_column] = numeric_lots.astype(int)
        source["event_time"] = pd.to_datetime(
            source["event_time"], utc=True, errors="coerce"
        )
        if source["event_time"].isna().any():
            raise ValueError(f"event_time contains invalid values: {symbol}")
        if source["event_time"].duplicated().any() or not source["event_time"].is_monotonic_increasing:
            raise ValueError(f"event_time must be unique and sorted: {symbol}")
        if source.empty:
            raise ValueError(f"portfolio frame is empty: {symbol}")
        normalized[symbol] = {
            row.event_time: row for row in source.itertuples(index=False)
        }
    timestamp_sets = {frozenset(values) for values in normalized.values()}
    if len(timestamp_sets) != 1:
        raise ValueError("all portfolio frames must have identical timestamps")
    return normalized


def _desired_position(row: object, symbol: str, column: str) -> int:
    """シグナル行から0または1のside別desired_positionを取り出す。

    Args:
        row: DataFrameの名前付き行。
        symbol: エラーに表示する銘柄。
        column: 読み取るシグナル列。

    Returns:
        検証済みのdesired_position。

    Raises:
        ValueError: 値が0または1でない場合。
    """

    try:
        desired = int(getattr(row, column))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"invalid {column}: {symbol}") from error
    if desired not in (0, 1):
        raise ValueError(f"unknown {column} for {symbol}: {desired}")
    return desired


def _signal_column(side: str) -> str:
    """sideに対応するシグナル列名を返す。

    Args:
        side: `long`または`short`。

    Returns:
        DataFrameから読むdesired position列。

    Raises:
        ValueError: 不明なsideの場合。
    """

    if side == "long":
        return "desired_long_position"
    if side == "short":
        return "desired_short_position"
    raise ValueError("side must be 'long' or 'short'")


def _desired_lot_count(row: object, symbol: str, side: str) -> int:
    """シグナル行からside別の正のentryロット数を取り出す。

    Args:
        row: DataFrameの名前付き行。
        symbol: エラーに表示する銘柄。
        side: `long`または`short`。

    Returns:
        配分層へ要求する正のロット数。

    Raises:
        ValueError: sideまたはロット数が不正な場合。
    """

    if side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    column = f"desired_{side}_lot_count"
    try:
        value = getattr(row, column)
        lot_count = int(value)
    except (AttributeError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"invalid {column}: {symbol}") from error
    if lot_count <= 0 or float(value) != lot_count:
        raise ValueError(f"invalid {column}: {symbol}")
    return lot_count


def _price(value: object, name: str) -> Decimal:
    """正の市場価格をDecimalへ変換する。

    Args:
        value: 市場価格。
        name: エラーに表示する列名。

    Returns:
        検証済みの価格。

    Raises:
        ValueError: 欠損、非有限、または非正の価格の場合。
    """

    result = _decimal(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _funding_rate(row: object, symbol: str) -> Decimal:
    """行から有限なFunding率を読み、未指定・欠損を0として返す。

    Args:
        row: DataFrameの名前付き行。
        symbol: エラーに表示する銘柄。

    Returns:
        Funding決済に使う率。

    Raises:
        ValueError: Funding率が有限な数値でない場合。
    """

    value = getattr(row, "funding_rate", 0)
    if value is None or pd.isna(value):
        return Decimal("0")
    return _decimal(value, f"{symbol}.funding_rate")


def _rejection_event(
    timestamp: pd.Timestamp, symbol: str, side: str, reason: str
) -> dict[str, object]:
    """配分拒否の監査イベントを作る。

    Args:
        timestamp: 拒否が発生したUTC時刻。
        symbol: 拒否された銘柄。
        side: 拒否された売買側。
        reason: 機械可読な拒否理由。

    Returns:
        配分拒否イベント。
    """

    return {
        "event_time": timestamp.isoformat(),
        "event_type": "ORDER_REJECTED",
        "side": side,
        "symbol": symbol,
        "allocation_reason": reason,
    }


def _summarize(
    initial_equity: Decimal,
    equity_curve: list[dict[str, object]],
    events: list[dict[str, object]],
) -> dict[str, object]:
    """配分済みequity曲線とイベントを基本指標へ集計する。

    Args:
        initial_equity: 初期equity。
        equity_curve: 時刻ごとの評価額。
        events: 約定・拒否イベント。

    Returns:
        最終資産、収益率、最大DD、注文件数を含む指標。

    Raises:
        ValueError: equity曲線が空の場合。
    """

    if not equity_curve:
        raise ValueError("equity_curve must not be empty")
    equities = [_decimal(row["equity"], "equity") for row in equity_curve]
    peak = initial_equity
    max_drawdown = Decimal("0")
    for equity in equities:
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - Decimal("1"))
    event_types = [str(event["event_type"]) for event in events]
    long_entries = sum(
        event["event_type"] == "ENTRY" and event["side"] == "long"
        for event in events
    )
    short_entries = sum(
        event["event_type"] == "ENTRY" and event["side"] == "short"
        for event in events
    )
    long_exits = sum(
        event["event_type"] == "EXIT" and event["side"] == "long"
        for event in events
    )
    short_exits = sum(
        event["event_type"] == "EXIT" and event["side"] == "short"
        for event in events
    )
    short_realized_pnl = sum(
        (
            _decimal(event["pnl"], "pnl")
            for event in events
            if event["event_type"] == "EXIT"
            and event["side"] == "short"
            and "pnl" in event
        ),
        Decimal("0"),
    )
    funding_cash_flow = sum(
        (
            _decimal(event["funding_delta"], "funding_delta")
            for event in events
            if event["event_type"] == "FUNDING"
        ),
        Decimal("0"),
    )
    total_fees = sum(
        (
            _decimal(event["fee"], "fee")
            for event in events
            if event["event_type"] in {"ENTRY", "EXIT"} and "fee" in event
        ),
        Decimal("0"),
    )
    return {
        "initial_equity": str(initial_equity),
        "final_equity": str(equities[-1]),
        "net_pnl": str(equities[-1] - initial_equity),
        "return_rate": str(equities[-1] / initial_equity - Decimal("1")),
        "max_drawdown": str(max_drawdown),
        "entry_count": event_types.count("ENTRY"),
        "exit_count": event_types.count("EXIT"),
        "long_entry_count": long_entries,
        "short_entry_count": short_entries,
        "long_exit_count": long_exits,
        "short_exit_count": short_exits,
        "short_realized_pnl": str(short_realized_pnl),
        "funding_cash_flow": str(funding_cash_flow),
        "total_fees": str(total_fees),
        "allocation_rejection_count": event_types.count("ORDER_REJECTED"),
        "max_position_notional": str(
            max(
                (_decimal(row["position_notional"], "position_notional") for row in equity_curve),
                default=Decimal("0"),
            )
        ),
        "max_allocated_gross_notional": str(
            max(
                (_decimal(row["allocated_gross_notional"], "allocated_gross_notional") for row in equity_curve),
                default=Decimal("0"),
            )
        ),
    }
