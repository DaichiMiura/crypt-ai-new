"""Funding率のクロスセクショナル・キャリーシグナルを提供する。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import combinations
import math
from numbers import Real

import pandas as pd


@dataclass(frozen=True)
class FundingCarrySignalConfig:
    """Funding率順位をlong/shortへ変換する固定条件。"""

    lookback_events: int = 3
    rebalance_events: int = 3
    long_count: int = 2
    short_count: int = 2
    signal_delay_bars: int = 1

    def __post_init__(self) -> None:
        """固定条件が正の整数で、long/shortが重複しないことを検査する。"""

        for name in (
            "lookback_events",
            "rebalance_events",
            "long_count",
            "short_count",
            "signal_delay_bars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.long_count + self.short_count > 0 and (
            self.long_count <= 0 or self.short_count <= 0
        ):
            raise ValueError("long_count and short_count must be positive")


@dataclass(frozen=True)
class SelectiveFundingCarrySignalConfig:
    """費用edgeと価格βを制約する選択的Fundingキャリー条件。"""

    lookback_events: int = 6
    holding_events: int = 6
    rebalance_events: int = 6
    long_count: int = 2
    short_count: int = 2
    signal_delay_bars: int = 1
    beta_window_bars: int = 360
    max_beta_gap: float = 0.25
    minimum_projected_carry: float = 0.0048

    def __post_init__(self) -> None:
        """整数条件と有限な非負閾値を検査する。

        Raises:
            ValueError: 個数、期間、または閾値が不正な場合。
        """

        for name in (
            "lookback_events",
            "holding_events",
            "rebalance_events",
            "long_count",
            "short_count",
            "signal_delay_bars",
            "beta_window_bars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("max_beta_gap", "minimum_projected_carry"):
            value = getattr(self, name)
            if not isinstance(value, Real) or not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} must be a finite non-negative number")


def prepare_selective_funding_carry_signals(
    frames: Mapping[str, pd.DataFrame],
    config: SelectiveFundingCarrySignalConfig,
    *,
    start_trading_at: pd.Timestamp | None = None,
) -> dict[str, pd.DataFrame]:
    """絶対Funding符号、費用edge、過去価格βで選別したシグナルを作る。

    Funding時刻の現在rateと現在closeは意思決定へ含めない。Funding予測には
    直前の確定イベントだけを使い、βには直前バーまでのclose-to-close returnを
    使う。条件を満たす2 long + 2 short組合せがなければcashを維持する。

    Args:
        frames: 銘柄別の価格、Funding率、Fundingイベントを持つ2時間足。
        config: 選択条件、費用edge、β制約を固定した設定。
        start_trading_at: このUTC時刻より前のシグナルを無効化する境界。

    Returns:
        選択診断列と遅延済みdesired positionを追加した銘柄別DataFrame。

    Raises:
        ValueError: 入力、銘柄数、時刻、価格、Fundingが不正な場合。
    """

    if not frames:
        raise ValueError("frames must not be empty")
    if config.long_count + config.short_count > len(frames):
        raise ValueError("long_count plus short_count exceeds symbol count")

    normalized = _normalize_frames(frames)
    symbols = tuple(sorted(normalized))
    timestamps = next(iter(normalized.values()))["event_time"].tolist()
    start = _normalize_start(start_trading_at)
    histories: dict[str, list[float]] = {symbol: [] for symbol in symbols}
    returns = _past_return_frames(normalized, symbols)
    current_desired = {symbol: (0, 0) for symbol in symbols}
    scheduled: dict[int, dict[str, tuple[int, int]]] = {}
    event_count = 0
    output_rows: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in symbols}

    for index, timestamp in enumerate(timestamps):
        if index in scheduled:
            current_desired = scheduled[index]

        rows = {symbol: normalized[symbol].iloc[index] for symbol in symbols}
        is_event = all(bool(row["funding_event"]) for row in rows.values())
        any_event = any(bool(row["funding_event"]) for row in rows.values())
        if any_event and not is_event:
            raise ValueError(f"funding event timestamps differ: {timestamp}")

        means: dict[str, float] = {}
        betas: dict[str, float] = {}
        selected_longs: tuple[str, ...] = ()
        selected_shorts: tuple[str, ...] = ()
        projected_carry: float | None = None
        beta_gap: float | None = None
        gate_reason: str | None = None
        signal_event = False
        if is_event and event_count >= config.lookback_events and (
            event_count % config.rebalance_events == 0
        ):
            means = {
                symbol: sum(histories[symbol][-config.lookback_events :])
                / config.lookback_events
                for symbol in symbols
            }
            betas = _trailing_betas(returns, symbols, index, config.beta_window_bars)
            if len(betas) != len(symbols):
                gate_reason = "insufficient_beta_history"
            else:
                selected = _select_signed_beta_neutral_basket(means, betas, config)
                if selected is None:
                    negative_count = sum(value < 0 for value in means.values())
                    positive_count = sum(value > 0 for value in means.values())
                    if negative_count < config.long_count or positive_count < config.short_count:
                        gate_reason = "insufficient_signed_candidates"
                    else:
                        gate_reason = "beta_gap_exceeded"
                else:
                    selected_longs, selected_shorts, spread, beta_gap = selected
                    projected_carry = spread * config.holding_events
                    if projected_carry < config.minimum_projected_carry:
                        gate_reason = "projected_carry_below_threshold"
                        selected_longs = ()
                        selected_shorts = ()
                    else:
                        gate_reason = "accepted"

            if start is None or timestamp >= start:
                desired = {symbol: (0, 0) for symbol in symbols}
                for symbol in selected_longs:
                    desired[symbol] = (1, 0)
                for symbol in selected_shorts:
                    desired[symbol] = (0, 1)
                target_index = index + config.signal_delay_bars
                if target_index < len(timestamps):
                    scheduled[target_index] = desired
                    signal_event = True

        if is_event:
            for symbol in symbols:
                histories[symbol].append(float(rows[symbol]["funding_rate"]))
            event_count += 1

        for symbol in symbols:
            row = normalized[symbol].iloc[index]
            long_position, short_position = current_desired[symbol]
            output_rows[symbol].append(
                {
                    **row.to_dict(),
                    "funding_lookback_mean": means.get(symbol),
                    "trailing_market_beta": betas.get(symbol),
                    "projected_funding_carry": projected_carry,
                    "selected_beta_gap": beta_gap,
                    "funding_gate_reason": gate_reason,
                    "funding_signal_event": signal_event,
                    "desired_long_position": long_position,
                    "desired_short_position": short_position,
                }
            )

    return {
        symbol: pd.DataFrame(rows).reset_index(drop=True)
        for symbol, rows in output_rows.items()
    }


def prepare_cross_sectional_funding_carry_signals(
    frames: Mapping[str, pd.DataFrame],
    config: FundingCarrySignalConfig,
    *,
    start_trading_at: pd.Timestamp | None = None,
) -> dict[str, pd.DataFrame]:
    """過去Funding率の順位から遅延したlong/shortシグナルを作る。

    Fundingイベント時点では、そのイベントの率を順位計算へ使わない。直前の
    `lookback_events`回の率だけで順位を作り、`signal_delay_bars`本後のバーへ
    desired状態を予約する。これにより、決済時点で初めて確定するFunding率を
    同じ時点のentry判断へ混入させない。

    Args:
        frames: 銘柄別の`event_time`、`open`、`close`、`funding_rate`、
            `funding_event`を持つ2時間足DataFrame。
        config: lookback、更新間隔、上下の銘柄数、signal遅延を固定した設定。
        start_trading_at: 指定した時刻より前に作ったシグナルを無効にするUTC時刻。
            Noneの場合は入力全期間でシグナルを有効にする。

    Returns:
        入力列にFunding順位、順位平均、シグナルイベント、desired long/shortを
        追加した銘柄別DataFrame。

    Raises:
        ValueError: 入力列、時刻、価格、Fundingイベント、銘柄間の時刻が不正な場合。
    """

    if not frames:
        raise ValueError("frames must not be empty")
    if config.long_count + config.short_count > len(frames):
        raise ValueError("long_count plus short_count exceeds symbol count")

    normalized = _normalize_frames(frames)
    symbols = tuple(sorted(normalized))
    timestamps = next(iter(normalized.values()))["event_time"].tolist()
    start = _normalize_start(start_trading_at)
    histories: dict[str, list[float]] = {symbol: [] for symbol in symbols}
    current_desired = {symbol: (0, 0) for symbol in symbols}
    scheduled: dict[int, dict[str, tuple[int, int]]] = {}
    event_count = 0
    output_rows: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in symbols}

    for index, timestamp in enumerate(timestamps):
        if index in scheduled:
            current_desired = scheduled[index]

        rows = {symbol: normalized[symbol].iloc[index] for symbol in symbols}
        is_event = all(bool(row["funding_event"]) for row in rows.values())
        any_event = any(bool(row["funding_event"]) for row in rows.values())
        if any_event and not is_event:
            raise ValueError(f"funding event timestamps differ: {timestamp}")

        means: dict[str, float] = {}
        ranks: dict[str, int] = {}
        signal_event = False
        carry_spread: float | None = None
        if is_event:
            if event_count >= config.lookback_events and (
                event_count % config.rebalance_events == 0
            ):
                means = {
                    symbol: sum(histories[symbol][-config.lookback_events :])
                    / config.lookback_events
                    for symbol in symbols
                }
                ordered = sorted(symbols, key=lambda symbol: (means[symbol], symbol))
                ranks = {symbol: position + 1 for position, symbol in enumerate(ordered)}
                carry_spread = means[ordered[-1]] - means[ordered[0]]
                if start is None or timestamp >= start:
                    desired = {symbol: (0, 0) for symbol in symbols}
                    for symbol in ordered[: config.long_count]:
                        desired[symbol] = (1, 0)
                    for symbol in ordered[-config.short_count :]:
                        if desired[symbol] != (0, 0):
                            raise ValueError("long and short symbol sets overlap")
                        desired[symbol] = (0, 1)
                    target_index = index + config.signal_delay_bars
                    if target_index < len(timestamps):
                        scheduled[target_index] = desired
                        signal_event = True

            for symbol in symbols:
                histories[symbol].append(float(rows[symbol]["funding_rate"]))
            event_count += 1

        for symbol in symbols:
            row = normalized[symbol].iloc[index]
            long_position, short_position = current_desired[symbol]
            output_rows[symbol].append(
                {
                    **row.to_dict(),
                    "funding_lookback_mean": means.get(symbol),
                    "funding_carry_rank": ranks.get(symbol),
                    "funding_carry_spread": carry_spread,
                    "funding_signal_event": signal_event,
                    "desired_long_position": long_position,
                    "desired_short_position": short_position,
                }
            )

    return {
        symbol: pd.DataFrame(rows).reset_index(drop=True)
        for symbol, rows in output_rows.items()
    }


def _past_return_frames(
    frames: Mapping[str, pd.DataFrame],
    symbols: tuple[str, ...],
) -> pd.DataFrame:
    """銘柄別close-to-close returnを同じ時刻へ整列する。

    Args:
        frames: 正規化済み銘柄別DataFrame。
        symbols: 決定論的な銘柄順。

    Returns:
        入力行indexと一致する銘柄別return DataFrame。
    """

    return pd.DataFrame(
        {
            symbol: frames[symbol]["close"].pct_change(fill_method=None)
            for symbol in symbols
        }
    )


def _trailing_betas(
    returns: pd.DataFrame,
    symbols: tuple[str, ...],
    current_index: int,
    window_bars: int,
) -> dict[str, float]:
    """現在バーを除く過去returnから等金額indexに対するβを計算する。

    Args:
        returns: 銘柄別return DataFrame。
        symbols: βを計算する銘柄順。
        current_index: 意思決定する現在バーのindex。
        window_bars: 必要な過去return本数。

    Returns:
        全履歴が揃う場合の銘柄別β。履歴不足時は空dict。
    """

    start = current_index - window_bars
    if start < 1:
        return {}
    trailing = returns.iloc[start:current_index].copy()
    if len(trailing) != window_bars or trailing[list(symbols)].isna().any().any():
        return {}
    market = trailing[list(symbols)].mean(axis=1)
    variance = float(market.var(ddof=0))
    if not math.isfinite(variance) or variance <= 0:
        return {}
    result: dict[str, float] = {}
    for symbol in symbols:
        covariance = float(
            ((trailing[symbol] - trailing[symbol].mean()) * (market - market.mean())).mean()
        )
        beta = covariance / variance
        if not math.isfinite(beta):
            return {}
        result[symbol] = beta
    return result


def _select_signed_beta_neutral_basket(
    means: Mapping[str, float],
    betas: Mapping[str, float],
    config: SelectiveFundingCarrySignalConfig,
) -> tuple[tuple[str, ...], tuple[str, ...], float, float] | None:
    """絶対Funding符号とβ差を満たす最大予測差の組合せを選ぶ。

    Args:
        means: 銘柄別の過去Funding平均。
        betas: 銘柄別の過去市場β。
        config: 銘柄数とβ差上限を含む固定条件。

    Returns:
        long、short、1イベント予測差、β差。候補がなければNone。
    """

    long_candidates = tuple(sorted(symbol for symbol, value in means.items() if value < 0))
    short_candidates = tuple(sorted(symbol for symbol, value in means.items() if value > 0))
    candidates: list[tuple[float, float, tuple[str, ...], tuple[str, ...]]] = []
    for longs in combinations(long_candidates, config.long_count):
        for shorts in combinations(short_candidates, config.short_count):
            if set(longs).intersection(shorts):
                continue
            long_beta = sum(betas[symbol] for symbol in longs) / config.long_count
            short_beta = sum(betas[symbol] for symbol in shorts) / config.short_count
            gap = abs(long_beta - short_beta)
            if gap > config.max_beta_gap:
                continue
            long_rate = sum(means[symbol] for symbol in longs) / config.long_count
            short_rate = sum(means[symbol] for symbol in shorts) / config.short_count
            spread = short_rate - long_rate
            candidates.append((-spread, gap, longs, shorts))
    if not candidates:
        return None
    negative_spread, gap, longs, shorts = min(candidates)
    return longs, shorts, -negative_spread, gap


def _normalize_frames(frames: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """入力DataFrameをUTC時刻、価格、Fundingイベントについて検査する。

    Args:
        frames: 銘柄名から入力DataFrameへのマッピング。

    Returns:
        検査・正規化後の銘柄別DataFrame。

    Raises:
        ValueError: 必須列、時刻、価格、Funding値、銘柄間の同期が不正な場合。
    """

    required = {"event_time", "open", "close", "funding_rate", "funding_event"}
    normalized: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"missing funding-carry columns for {symbol}: {sorted(missing)}")
        result = frame.copy()
        result["event_time"] = pd.to_datetime(
            result["event_time"], utc=True, errors="coerce"
        )
        if (
            result.empty
            or result["event_time"].isna().any()
            or result["event_time"].duplicated().any()
            or not result["event_time"].is_monotonic_increasing
        ):
            raise ValueError(f"event_time must be unique and sorted: {symbol}")
        for column in ("open", "close"):
            result[column] = pd.to_numeric(result[column], errors="coerce")
            if result[column].isna().any() or not result[column].gt(0).all():
                raise ValueError(f"{column} must be positive: {symbol}")
        result["funding_event"] = result["funding_event"].map(_bool_value)
        result["funding_rate"] = pd.to_numeric(result["funding_rate"], errors="coerce")
        finite_or_missing = result["funding_rate"].map(
            lambda value: pd.isna(value) or math.isfinite(float(value))
        )
        if not finite_or_missing.all():
            raise ValueError(f"funding rate is not finite: {symbol}")
        if result.loc[result["funding_event"], "funding_rate"].isna().any():
            raise ValueError(f"funding event has missing rate: {symbol}")
        result["funding_rate"] = result["funding_rate"].fillna(0.0)
        normalized[symbol] = result.reset_index(drop=True)

    timestamp_sets = {frozenset(frame["event_time"]) for frame in normalized.values()}
    if len(timestamp_sets) != 1:
        raise ValueError("all funding-carry frames must have identical timestamps")
    event_sets = {frozenset(frame.loc[frame["funding_event"], "event_time"]) for frame in normalized.values()}
    if len(event_sets) != 1:
        raise ValueError("all funding-carry frames must have identical funding events")
    if not next(iter(event_sets)):
        raise ValueError("funding events must not be empty")
    return normalized


def _bool_value(value: object) -> bool:
    """CSV由来のbool値を厳密に正規化する。

    Args:
        value: 真偽値、0/1、または文字列化された真偽値。

    Returns:
        正規化したbool値。

    Raises:
        ValueError: 真偽値として解釈できない場合。
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, Real) and value in (0, 1):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"invalid funding_event boolean: {value}")


def _normalize_start(value: pd.Timestamp | None) -> pd.Timestamp | None:
    """開始時刻をUTCへ正規化する。

    Args:
        value: timezone-awareな開始時刻。Noneは制約なしを表す。

    Returns:
        UTCへ変換した開始時刻、またはNone。

    Raises:
        ValueError: timezoneなしの開始時刻が渡された場合。
    """

    if value is None:
        return None
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        raise ValueError("start_trading_at must include a timezone")
    return result.tz_convert("UTC")
