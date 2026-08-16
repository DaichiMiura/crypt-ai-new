"""現物ロング・無期限先物ショートの研究用ペア会計を提供する。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import pandas as pd

from crypt_ai.basis import BasisSignalConfig, prepare_basis_signals


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
class BasisCostModel:
    """現物・先物の片道費用仮定。"""

    spot_fee_rate: Decimal = Decimal("0.001")
    perp_fee_rate: Decimal = Decimal("0.0006")
    spot_round_trip_spread: Decimal = Decimal("0.001")
    perp_round_trip_spread: Decimal = Decimal("0.001")
    spot_slippage_per_fill: Decimal = Decimal("0.0005")
    perp_slippage_per_fill: Decimal = Decimal("0.0005")

    def __post_init__(self) -> None:
        """すべての費用率が非負かつ100%未満であることを検査する。

        Raises:
            ValueError: 費用率が負、または片道費用が100%以上の場合。
        """

        for name in (
            "spot_fee_rate",
            "perp_fee_rate",
            "spot_round_trip_spread",
            "perp_round_trip_spread",
            "spot_slippage_per_fill",
            "perp_slippage_per_fill",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        for fee_name, spread_name, slippage_name in (
            ("spot_fee_rate", "spot_round_trip_spread", "spot_slippage_per_fill"),
            ("perp_fee_rate", "perp_round_trip_spread", "perp_slippage_per_fill"),
        ):
            if getattr(self, fee_name) >= 1:
                raise ValueError(f"{fee_name} must be below 1")
            if (
                getattr(self, spread_name) / Decimal("2")
                + getattr(self, slippage_name)
                >= 1
            ):
                raise ValueError(f"{spread_name} and {slippage_name} are too large")

    def spot_buy_price(self, raw_price: Decimal) -> Decimal:
        """現物買いのspread・slippage込み価格を返す。"""

        return raw_price * (
            Decimal("1")
            + self.spot_round_trip_spread / Decimal("2")
            + self.spot_slippage_per_fill
        )

    def spot_sell_price(self, raw_price: Decimal) -> Decimal:
        """現物売りのspread・slippage込み価格を返す。"""

        return raw_price * (
            Decimal("1")
            - self.spot_round_trip_spread / Decimal("2")
            - self.spot_slippage_per_fill
        )

    def perp_sell_price(self, raw_price: Decimal) -> Decimal:
        """先物ショート開始のspread・slippage込み価格を返す。"""

        return raw_price * (
            Decimal("1")
            - self.perp_round_trip_spread / Decimal("2")
            - self.perp_slippage_per_fill
        )

    def perp_buy_price(self, raw_price: Decimal) -> Decimal:
        """先物ショート決済のspread・slippage込み価格を返す。"""

        return raw_price * (
            Decimal("1")
            + self.perp_round_trip_spread / Decimal("2")
            + self.perp_slippage_per_fill
        )


@dataclass(frozen=True)
class BasisBacktestConfig:
    """ベーシス収束ペアのバックテスト設定。"""

    initial_equity: Decimal = Decimal("1000")
    reserve_cash: Decimal = Decimal("200")
    pair_notional: Decimal = Decimal("100")
    max_concurrent_pairs: int = 4
    evaluation_start: pd.Timestamp = pd.Timestamp("2022-02-01T00:00:00Z")
    evaluation_end: pd.Timestamp = pd.Timestamp("2026-01-01T00:00:00Z")
    signal_config: BasisSignalConfig = BasisSignalConfig()
    costs: BasisCostModel = BasisCostModel()
    reject_interpolated_entries: bool = True

    def __post_init__(self) -> None:
        """資金、期間、同時保有数、フラグの整合性を検査する。

        Raises:
            ValueError: 設定値が非正、期間が逆、または予約資金が不正な場合。
        """

        if not self.initial_equity.is_finite() or self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive and finite")
        if not self.reserve_cash.is_finite() or not (
            Decimal("0") <= self.reserve_cash <= self.initial_equity
        ):
            raise ValueError("reserve_cash must be between zero and initial_equity")
        if not self.pair_notional.is_finite() or self.pair_notional <= 0:
            raise ValueError("pair_notional must be positive and finite")
        if (
            isinstance(self.max_concurrent_pairs, bool)
            or not isinstance(self.max_concurrent_pairs, int)
            or self.max_concurrent_pairs <= 0
        ):
            raise ValueError("max_concurrent_pairs must be a positive integer")
        if self.evaluation_start >= self.evaluation_end:
            raise ValueError("evaluation_start must precede evaluation_end")
        if not isinstance(self.reject_interpolated_entries, bool):
            raise ValueError("reject_interpolated_entries must be bool")


@dataclass(frozen=True)
class BasisBacktestResult:
    """ベーシス収束ペアの会計結果。"""

    metrics: dict[str, object]
    events: tuple[dict[str, object], ...]
    equity_curve: tuple[dict[str, object], ...]


@dataclass
class _BasisPosition:
    """現物longと先物shortの数量・約定値を保持する。"""

    spot_quantity: Decimal
    spot_entry_price: Decimal
    perp_quantity: Decimal
    perp_entry_price: Decimal
    notional: Decimal
    funding_cash_flow: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")


def run_basis_backtest(
    frames: Mapping[str, pd.DataFrame],
    config: BasisBacktestConfig,
) -> BasisBacktestResult:
    """複数銘柄の現物long・先物shortペアを決定論的に会計する。

    現物と先物を同一元本で同時に建て、現物の価格変化と先物shortの価格変化を
    相殺する。Fundingは正の率ならshortが受け取り、指定時刻に既存ペアだけへ
    適用する。各ペアは予約資金を除くcashから現物元本と先物担保元本を取り置く。

    Args:
        frames: 銘柄別に`event_time`、現物・先物のOHLC、`perp_mark_close`、
            `funding_rate`、`desired_pair_position`を含むDataFrame。
        config: 資金、費用、シグナル、同時保有上限を定義する設定。

    Returns:
        最終equity、最大DD、ペア損益、Funding、監査イベント、equity曲線。

    Raises:
        ValueError: 銘柄、時刻、価格、シグナル、Funding、またはcashが不正な場合。
    """

    normalized = _normalize_frames(frames)
    timestamps = sorted(next(iter(normalized.values())))
    cash = config.initial_equity
    positions: dict[str, _BasisPosition] = {}
    previous_desired = {symbol: 0 for symbol in normalized}
    events: list[dict[str, object]] = []
    equity_curve: list[dict[str, object]] = []

    for timestamp in timestamps:
        rows = {symbol: values[timestamp] for symbol, values in normalized.items()}

        for symbol in sorted(rows):
            position = positions.get(symbol)
            if position is None:
                continue
            rate = _funding_rate(rows[symbol], symbol)
            if rate == 0:
                continue
            mark = _price(rows[symbol].perp_mark_close, f"{symbol}.perp_mark_close")
            notional = position.perp_quantity * mark
            delta = notional * rate
            cash += delta
            position.funding_cash_flow += delta
            events.append(
                {
                    "event_time": timestamp.isoformat(),
                    "event_type": "FUNDING",
                    "symbol": symbol,
                    "perp_notional": str(notional),
                    "funding_rate": str(rate),
                    "funding_delta": str(delta),
                }
            )

        for symbol in sorted(rows):
            position = positions.get(symbol)
            if position is None or _desired(rows[symbol]) != 0:
                continue
            cash, event = _close_pair(
                cash, symbol, rows[symbol], position, config.costs
            )
            events.append(event)
            positions.pop(symbol)

        for symbol in sorted(rows):
            desired = _desired(rows[symbol])
            if desired != 1 or previous_desired[symbol] == 1 or symbol in positions:
                continue
            row = rows[symbol]
            if config.reject_interpolated_entries and bool(
                getattr(row, "is_interpolated", False)
            ):
                events.append(
                    _rejection_event(timestamp, symbol, "interpolated_bar")
                )
                continue
            if len(positions) >= config.max_concurrent_pairs:
                events.append(_rejection_event(timestamp, symbol, "max_pairs"))
                continue
            spot_open = _price(row.spot_open, f"{symbol}.spot_open")
            perp_open = _price(row.perp_open, f"{symbol}.perp_open")
            spot_execution = config.costs.spot_buy_price(spot_open)
            perp_execution = config.costs.perp_sell_price(perp_open)
            spot_quantity = config.pair_notional / spot_execution
            perp_quantity = config.pair_notional / perp_execution
            spot_fee = spot_quantity * spot_execution * config.costs.spot_fee_rate
            perp_fee = perp_quantity * perp_execution * config.costs.perp_fee_rate
            required_cash = config.pair_notional * 2 + spot_fee + perp_fee
            if cash - required_cash < config.reserve_cash:
                events.append(_rejection_event(timestamp, symbol, "reserve_cash"))
                continue
            cash -= required_cash
            positions[symbol] = _BasisPosition(
                spot_quantity=spot_quantity,
                spot_entry_price=spot_execution,
                perp_quantity=perp_quantity,
                perp_entry_price=perp_execution,
                notional=config.pair_notional,
                fees=spot_fee + perp_fee,
            )
            events.append(
                {
                    "event_time": timestamp.isoformat(),
                    "event_type": "ENTRY_PAIR",
                    "symbol": symbol,
                    "spot_quantity": str(spot_quantity),
                    "perp_quantity": str(perp_quantity),
                    "spot_entry_price": str(spot_execution),
                    "perp_entry_price": str(perp_execution),
                    "notional_per_leg": str(config.pair_notional),
                    "spot_fee": str(spot_fee),
                    "perp_fee": str(perp_fee),
                }
            )

        for symbol, row in rows.items():
            previous_desired[symbol] = _desired(row)

        marked_value = Decimal("0")
        for symbol, position in positions.items():
            spot_close = _price(rows[symbol].spot_close, f"{symbol}.spot_close")
            perp_mark = _price(
                rows[symbol].perp_mark_close, f"{symbol}.perp_mark_close"
            )
            spot_value = position.spot_quantity * spot_close
            perp_value = position.notional + position.perp_quantity * (
                position.perp_entry_price - perp_mark
            )
            marked_value += spot_value + perp_value
        equity = cash + marked_value
        equity_curve.append(
            {
                "event_time": timestamp.isoformat(),
                "cash": str(cash),
                "equity": str(equity),
                "open_pair_count": len(positions),
                "gross_pair_notional": str(
                    sum(
                        (position.notional * 2 for position in positions.values()),
                        Decimal("0"),
                    )
                ),
            }
        )

    return BasisBacktestResult(
        metrics=_summarize(config.initial_equity, equity_curve, events, positions),
        events=tuple(events),
        equity_curve=tuple(equity_curve),
    )


def _normalize_frames(
    frames: Mapping[str, pd.DataFrame],
) -> dict[str, dict[pd.Timestamp, object]]:
    """入力DataFrameの時刻、価格、シグナル、Fundingを検証して行辞書へ変換する。

    Args:
        frames: 銘柄別のベーシス入力DataFrame。

    Returns:
        銘柄ごとの時刻から名前付き行への辞書。

    Raises:
        ValueError: 入力が空、必須列欠落、時刻不一致、価格非正、またはシグナル不正の場合。
    """

    if not frames:
        raise ValueError("frames must not be empty")
    required = {
        "event_time",
        "spot_open",
        "spot_close",
        "perp_open",
        "perp_mark_close",
        "funding_rate",
        "desired_pair_position",
    }
    normalized: dict[str, dict[pd.Timestamp, object]] = {}
    for symbol, frame in frames.items():
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"missing basis frame columns for {symbol}: {sorted(missing)}")
        source = frame.copy()
        source["event_time"] = pd.to_datetime(
            source["event_time"], utc=True, errors="coerce"
        )
        if (
            source["event_time"].isna().any()
            or source["event_time"].duplicated().any()
            or not source["event_time"].is_monotonic_increasing
        ):
            raise ValueError(f"event_time must be unique and sorted: {symbol}")
        if source.empty:
            raise ValueError(f"basis frame is empty: {symbol}")
        for column in ("spot_open", "spot_close", "perp_open", "perp_mark_close"):
            source[column] = pd.to_numeric(source[column], errors="coerce")
            if source[column].isna().any() or not source[column].gt(0).all():
                raise ValueError(f"invalid {column}: {symbol}")
        source["funding_rate"] = pd.to_numeric(source["funding_rate"], errors="coerce")
        if source["funding_rate"].isna().any():
            raise ValueError(f"invalid funding_rate: {symbol}")
        if not source["desired_pair_position"].isin((0, 1)).all():
            raise ValueError(f"invalid desired_pair_position: {symbol}")
        normalized[symbol] = {
            row.event_time: row for row in source.itertuples(index=False)
        }
    if len({frozenset(values) for values in normalized.values()}) != 1:
        raise ValueError("all basis frames must have identical timestamps")
    return normalized


def _price(value: object, name: str) -> Decimal:
    """正の価格をDecimalへ変換する。

    Args:
        value: 市場価格。
        name: エラーに表示する価格名。

    Returns:
        検証済みの価格。

    Raises:
        ValueError: 欠損、非有限、または非正の場合。
    """

    result = _decimal(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _funding_rate(row: object, symbol: str) -> Decimal:
    """行からFunding率を読み込む。

    Args:
        row: DataFrameの名前付き行。
        symbol: エラーに表示する銘柄名。

    Returns:
        Funding率。

    Raises:
        ValueError: Funding率が有限な数値でない場合。
    """

    return _decimal(getattr(row, "funding_rate"), f"{symbol}.funding_rate")


def _desired(row: object) -> int:
    """ペア建玉シグナルを0または1として取得する。"""

    value = int(getattr(row, "desired_pair_position"))
    if value not in (0, 1):
        raise ValueError(f"invalid desired_pair_position: {value}")
    return value


def _close_pair(
    cash: Decimal,
    symbol: str,
    row: object,
    position: _BasisPosition,
    costs: BasisCostModel,
) -> tuple[Decimal, dict[str, object]]:
    """現物と先物の両脚を決済し、cashと退出イベントを返す。

    Args:
        cash: 決済前のcash。
        symbol: 銘柄名。
        row: 決済足の名前付き行。
        position: 決済対象のペア建玉。
        costs: 現物・先物の費用モデル。

    Returns:
        決済後cashと退出イベント。
    """

    spot_exit = costs.spot_sell_price(_price(row.spot_open, f"{symbol}.spot_open"))
    perp_exit = costs.perp_buy_price(_price(row.perp_open, f"{symbol}.perp_open"))
    spot_gross = position.spot_quantity * spot_exit
    perp_pnl = position.perp_quantity * (position.perp_entry_price - perp_exit)
    spot_fee = spot_gross * costs.spot_fee_rate
    perp_fee = position.perp_quantity * perp_exit * costs.perp_fee_rate
    spot_pnl = position.spot_quantity * (spot_exit - position.spot_entry_price)
    cash += spot_gross - spot_fee + position.notional + perp_pnl - perp_fee
    position.fees += spot_fee + perp_fee
    return cash, {
        "event_time": pd.Timestamp(row.event_time).isoformat(),
        "event_type": "EXIT_PAIR",
        "symbol": symbol,
        "spot_exit_price": str(spot_exit),
        "perp_exit_price": str(perp_exit),
        "spot_pnl": str(spot_pnl),
        "perp_pnl": str(perp_pnl),
        "funding_cash_flow": str(position.funding_cash_flow),
        "spot_fee": str(spot_fee),
        "perp_fee": str(perp_fee),
        "net_pair_pnl": str(spot_pnl + perp_pnl + position.funding_cash_flow - position.fees),
    }


def _rejection_event(
    timestamp: pd.Timestamp,
    symbol: str,
    reason: str,
) -> dict[str, object]:
    """ペアentry拒否の監査イベントを作る。

    Args:
        timestamp: 拒否が発生したUTC時刻。
        symbol: 拒否された銘柄。
        reason: 機械可読な拒否理由。

    Returns:
        拒否イベント。
    """

    return {
        "event_time": timestamp.isoformat(),
        "event_type": "ORDER_REJECTED",
        "symbol": symbol,
        "reason": reason,
    }


def _summarize(
    initial_equity: Decimal,
    equity_curve: list[dict[str, object]],
    events: list[dict[str, object]],
    positions: Mapping[str, _BasisPosition],
) -> dict[str, object]:
    """equity曲線とイベントから主要指標を集計する。

    Args:
        initial_equity: 初期equity。
        equity_curve: 各バーの評価額。
        events: 約定・Funding・拒否イベント。
        positions: 評価終了時に残る建玉。

    Returns:
        最終equity、最大DD、ペア損益、Funding、手数料、拒否数を含む指標。
    """

    if not equity_curve:
        raise ValueError("equity_curve must not be empty")
    equities = [_decimal(row["equity"], "equity") for row in equity_curve]
    peak = initial_equity
    max_drawdown = Decimal("0")
    for equity in equities:
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - Decimal("1"))
    closed = [event for event in events if event["event_type"] == "EXIT_PAIR"]
    funding = sum(
        (_decimal(event["funding_delta"], "funding_delta") for event in events if event["event_type"] == "FUNDING"),
        Decimal("0"),
    )
    fees = sum(
        (
            _decimal(event[field], field)
            for event in events
            if event["event_type"] in {"ENTRY_PAIR", "EXIT_PAIR"}
            for field in ("spot_fee", "perp_fee")
            if field in event
        ),
        Decimal("0"),
    )
    realized = sum(
        (
            _decimal(event["net_pair_pnl"], "net_pair_pnl")
            for event in closed
        ),
        Decimal("0"),
    )
    return {
        "initial_equity": str(initial_equity),
        "final_equity": str(equities[-1]),
        "net_pnl": str(equities[-1] - initial_equity),
        "return_rate": str(equities[-1] / initial_equity - Decimal("1")),
        "max_drawdown": str(max_drawdown),
        "pair_entry_count": sum(event["event_type"] == "ENTRY_PAIR" for event in events),
        "pair_exit_count": len(closed),
        "open_pair_count_at_end": len(positions),
        "realized_net_pair_pnl": str(realized),
        "funding_cash_flow": str(funding),
        "total_fees": str(fees),
        "allocation_rejection_count": sum(event["event_type"] == "ORDER_REJECTED" for event in events),
        "max_gross_pair_notional": str(
            max(
                (_decimal(row["gross_pair_notional"], "gross_pair_notional") for row in equity_curve),
                default=Decimal("0"),
            )
        ),
    }
