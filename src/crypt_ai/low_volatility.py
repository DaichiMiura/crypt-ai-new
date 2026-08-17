"""クロスセクショナル低ボラティリティlongシグナルを提供する。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from numbers import Real

import pandas as pd


@dataclass(frozen=True)
class LowVolatilitySignalConfig:
    """低vol順位と長期市場regimeの固定条件。"""

    volatility_window_bars: int = 360
    regime_window_bars: int = 2160
    rebalance_bars: int = 84
    selected_count: int = 2
    signal_delay_bars: int = 1
    annualization_bars: int = 4380

    def __post_init__(self) -> None:
        """窓、銘柄数、遅延、年率化係数を検査する。

        Raises:
            ValueError: 設定が正の整数でない場合。
        """

        for name in (
            "volatility_window_bars",
            "regime_window_bars",
            "rebalance_bars",
            "selected_count",
            "signal_delay_bars",
            "annualization_bars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


def prepare_low_volatility_signals(
    frames: Mapping[str, pd.DataFrame],
    config: LowVolatilitySignalConfig,
    *,
    start_trading_at: pd.Timestamp,
) -> dict[str, pd.DataFrame]:
    """30日実現vol下位銘柄を正の長期regimeだけ週次選択する。

    時刻tの確定closeまでのreturnと長期returnだけで順位を作り、売買状態は
    `signal_delay_bars`後へ予約する。週次更新の間は選択を変更しない。

    Args:
        frames: 同期した銘柄別2時間足とFunding DataFrame。
        config: vol、regime、更新、銘柄数、遅延の固定条件。
        start_trading_at: 最初の週次判定を行うtimezone-aware UTC時刻。

    Returns:
        vol、regime、順位、選択、desired longを追加した銘柄別DataFrame。

    Raises:
        ValueError: 入力、時刻、価格、Funding、銘柄数、開始時刻が不正な場合。
    """

    if not frames:
        raise ValueError("frames must not be empty")
    if config.selected_count > len(frames):
        raise ValueError("selected_count exceeds symbol count")
    normalized = _normalize_frames(frames)
    symbols = tuple(sorted(normalized))
    timestamps = normalized[symbols[0]]["event_time"]
    start = _normalize_start(start_trading_at)
    eligible = timestamps[timestamps >= start]
    if eligible.empty:
        raise ValueError("start_trading_at is outside input timestamps")
    start_index = int(eligible.index[0])

    closes = pd.DataFrame(
        {symbol: normalized[symbol]["close"] for symbol in symbols}
    )
    returns = closes.pct_change(fill_method=None)
    current_desired = {symbol: 0 for symbol in symbols}
    scheduled: dict[int, dict[str, int]] = {}
    output: dict[str, list[dict[str, object]]] = {symbol: [] for symbol in symbols}

    for index, timestamp in enumerate(timestamps):
        if index in scheduled:
            current_desired = scheduled[index]

        is_rebalance = index >= start_index and (
            (index - start_index) % config.rebalance_bars == 0
        )
        volatilities: dict[str, float] = {}
        long_returns: dict[str, float] = {}
        ranks: dict[str, int] = {}
        regime_median: float | None = None
        selected: tuple[str, ...] = ()
        reason: str | None = None
        if is_rebalance:
            vol_start = index - config.volatility_window_bars + 1
            regime_start = index - config.regime_window_bars
            if vol_start < 1 or regime_start < 0:
                reason = "insufficient_history"
            else:
                window = returns.iloc[vol_start : index + 1]
                if len(window) != config.volatility_window_bars or window.isna().any().any():
                    reason = "insufficient_history"
                else:
                    volatilities = {
                        symbol: float(window[symbol].std(ddof=0))
                        * math.sqrt(config.annualization_bars)
                        for symbol in symbols
                    }
                    long_returns = {
                        symbol: float(closes.loc[index, symbol] / closes.loc[regime_start, symbol] - 1)
                        for symbol in symbols
                    }
                    if not all(
                        math.isfinite(value)
                        for value in (*volatilities.values(), *long_returns.values())
                    ):
                        reason = "nonfinite_feature"
                    else:
                        regime_median = float(pd.Series(long_returns).median())
                        ordered = sorted(
                            symbols, key=lambda symbol: (volatilities[symbol], symbol)
                        )
                        ranks = {
                            symbol: position + 1
                            for position, symbol in enumerate(ordered)
                        }
                        if regime_median <= 0:
                            reason = "market_regime_nonpositive"
                        else:
                            selected = tuple(ordered[: config.selected_count])
                            reason = "accepted"

            desired = {symbol: int(symbol in selected) for symbol in symbols}
            target_index = index + config.signal_delay_bars
            if target_index < len(timestamps):
                scheduled[target_index] = desired

        for symbol in symbols:
            output[symbol].append(
                {
                    **normalized[symbol].iloc[index].to_dict(),
                    "realized_volatility": volatilities.get(symbol),
                    "regime_return_180d": long_returns.get(symbol),
                    "market_regime_median": regime_median,
                    "volatility_rank": ranks.get(symbol),
                    "low_vol_rebalance": is_rebalance,
                    "low_vol_selected": symbol in selected,
                    "low_vol_reason": reason,
                    "desired_long_position": current_desired[symbol],
                    "desired_short_position": 0,
                }
            )

    return {
        symbol: pd.DataFrame(rows).reset_index(drop=True)
        for symbol, rows in output.items()
    }


def _normalize_frames(frames: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """銘柄別入力をUTC時刻、価格、Fundingについて検査する。

    Args:
        frames: 銘柄別の入力DataFrame。

    Returns:
        正規化済みDataFrame。

    Raises:
        ValueError: 必須列、値、銘柄間同期が不正な場合。
    """

    required = {"event_time", "open", "close", "funding_rate", "funding_event"}
    normalized: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"missing low-vol columns for {symbol}: {sorted(missing)}")
        result = frame.copy().reset_index(drop=True)
        result["event_time"] = pd.to_datetime(result["event_time"], utc=True, errors="coerce")
        if (
            result.empty
            or result["event_time"].isna().any()
            or result["event_time"].duplicated().any()
            or not result["event_time"].is_monotonic_increasing
        ):
            raise ValueError(f"invalid low-vol timestamps: {symbol}")
        for column in ("open", "close"):
            result[column] = pd.to_numeric(result[column], errors="coerce")
            if result[column].isna().any() or not result[column].gt(0).all():
                raise ValueError(f"invalid {column}: {symbol}")
        result["funding_rate"] = pd.to_numeric(result["funding_rate"], errors="coerce")
        if result["funding_rate"].isna().any() or not result["funding_rate"].map(math.isfinite).all():
            raise ValueError(f"invalid funding rate: {symbol}")
        result["funding_event"] = result["funding_event"].map(_bool_value)
        normalized[symbol] = result
    timestamp_sets = {tuple(frame["event_time"]) for frame in normalized.values()}
    funding_sets = {
        tuple(frame.loc[frame["funding_event"], "event_time"])
        for frame in normalized.values()
    }
    if len(timestamp_sets) != 1 or len(funding_sets) != 1:
        raise ValueError("low-vol frames must have identical timestamps and funding events")
    return normalized


def _bool_value(value: object) -> bool:
    """bool互換値を厳密に変換する。

    Args:
        value: bool、0/1、または文字列。

    Returns:
        正規化したbool。

    Raises:
        ValueError: boolとして解釈できない場合。
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


def _normalize_start(value: pd.Timestamp) -> pd.Timestamp:
    """開始時刻をUTCへ変換する。

    Args:
        value: timezone-aware時刻。

    Returns:
        UTC時刻。

    Raises:
        ValueError: timezoneがない場合。
    """

    result = pd.Timestamp(value)
    if result.tzinfo is None:
        raise ValueError("start_trading_at must include a timezone")
    return result.tz_convert("UTC")
