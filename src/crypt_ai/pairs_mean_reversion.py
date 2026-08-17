"""2銘柄のrolling log spread平均回帰シグナルを提供する。"""

from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd


@dataclass(frozen=True)
class PairMeanReversionConfig:
    """rolling OLSとz-scoreによる固定ペア条件。"""

    regression_window_bars: int = 720
    spread_window_bars: int = 360
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 4.0
    max_holding_bars: int = 168
    signal_delay_bars: int = 1

    def __post_init__(self) -> None:
        """窓、閾値、遅延を検査する。

        Raises:
            ValueError: 固定条件が正または順序どおりでない場合。
        """

        for name in (
            "regression_window_bars",
            "spread_window_bars",
            "max_holding_bars",
            "signal_delay_bars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("entry_z", "exit_z", "stop_z"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if not 0 <= self.exit_z < self.entry_z < self.stop_z:
            raise ValueError("z thresholds must satisfy 0 <= exit < entry < stop")


def prepare_pair_mean_reversion_signals(
    avax: pd.DataFrame,
    near: pd.DataFrame,
    config: PairMeanReversionConfig,
    *,
    start_trading_at: pd.Timestamp | None = None,
) -> dict[str, pd.DataFrame]:
    """AVAX/NEARの過去rolling spreadから遅延済みペアシグナルを作る。

    回帰係数とspread分布は現在行より前の確定closeだけで推定する。現在closeは
    z-score観測にだけ使い、売買は指定bar数後のopenへ遅延する。

    Args:
        avax: AVAXUSDTの時刻、価格、Fundingを持つDataFrame。
        near: NEARUSDTの時刻、価格、Fundingを持つDataFrame。
        config: rolling窓、z閾値、時間切れ、遅延の固定条件。
        start_trading_at: entryを許す最初のtimezone-aware UTC時刻。

    Returns:
        両銘柄について診断列とdesired long/shortを追加したDataFrame。

    Raises:
        ValueError: 入力、同期、価格、Funding、開始時刻が不正な場合。
    """

    left = _normalize_pair_frame(avax, "AVAXUSDT")
    right = _normalize_pair_frame(near, "NEARUSDT")
    if not left["event_time"].equals(right["event_time"]):
        raise ValueError("pair timestamps must be identical")
    if not left["funding_event"].equals(right["funding_event"]):
        raise ValueError("pair funding events must be identical")
    start = _normalize_start(start_trading_at)

    avax_log = left["close"].map(math.log)
    near_log = right["close"].map(math.log)
    rows = {"AVAXUSDT": [], "NEARUSDT": []}
    current_position = "flat"
    entry_index: int | None = None
    scheduled: dict[int, tuple[str, int | None]] = {}

    for index, timestamp in enumerate(left["event_time"]):
        if index in scheduled:
            current_position, entry_index = scheduled[index]

        alpha: float | None = None
        beta: float | None = None
        spread: float | None = None
        spread_mean: float | None = None
        spread_std: float | None = None
        z_score: float | None = None
        signal_action = "hold"
        exit_reason: str | None = None

        if index >= max(config.regression_window_bars, config.spread_window_bars):
            regression_start = index - config.regression_window_bars
            x = near_log.iloc[regression_start:index]
            y = avax_log.iloc[regression_start:index]
            x_mean = float(x.mean())
            y_mean = float(y.mean())
            variance = float(((x - x_mean) ** 2).mean())
            if variance > 0 and math.isfinite(variance):
                beta = float(((x - x_mean) * (y - y_mean)).mean()) / variance
                alpha = y_mean - beta * x_mean
                history_start = index - config.spread_window_bars
                history = avax_log.iloc[history_start:index] - (
                    alpha + beta * near_log.iloc[history_start:index]
                )
                spread_mean = float(history.mean())
                spread_std = float(history.std(ddof=0))
                spread = float(avax_log.iloc[index] - (alpha + beta * near_log.iloc[index]))
                if spread_std > 0 and math.isfinite(spread_std):
                    z_score = (spread - spread_mean) / spread_std

        target_position = current_position
        target_entry_index = entry_index
        can_trade = start is None or timestamp >= start
        if z_score is not None and can_trade:
            if current_position == "flat":
                if z_score >= config.entry_z:
                    target_position = "avax_short_near_long"
                    target_entry_index = index + config.signal_delay_bars
                    signal_action = "enter_avax_short"
                elif z_score <= -config.entry_z:
                    target_position = "avax_long_near_short"
                    target_entry_index = index + config.signal_delay_bars
                    signal_action = "enter_avax_long"
            else:
                held_bars = index - entry_index if entry_index is not None else 0
                if abs(z_score) >= config.stop_z:
                    target_position = "flat"
                    target_entry_index = None
                    signal_action = "exit"
                    exit_reason = "z_stop"
                elif abs(z_score) <= config.exit_z:
                    target_position = "flat"
                    target_entry_index = None
                    signal_action = "exit"
                    exit_reason = "mean_reversion"
                elif held_bars >= config.max_holding_bars:
                    target_position = "flat"
                    target_entry_index = None
                    signal_action = "exit"
                    exit_reason = "time_stop"

        if target_position != current_position:
            target_index = index + config.signal_delay_bars
            if target_index < len(left):
                scheduled[target_index] = (target_position, target_entry_index)

        positions = {
            "AVAXUSDT": (
                int(current_position == "avax_long_near_short"),
                int(current_position == "avax_short_near_long"),
            ),
            "NEARUSDT": (
                int(current_position == "avax_short_near_long"),
                int(current_position == "avax_long_near_short"),
            ),
        }
        for symbol, frame in (("AVAXUSDT", left), ("NEARUSDT", right)):
            desired_long, desired_short = positions[symbol]
            rows[symbol].append(
                {
                    **frame.iloc[index].to_dict(),
                    "pair_alpha": alpha,
                    "pair_beta": beta,
                    "pair_spread": spread,
                    "pair_spread_mean": spread_mean,
                    "pair_spread_std": spread_std,
                    "pair_z_score": z_score,
                    "pair_position": current_position,
                    "pair_signal_action": signal_action,
                    "pair_exit_reason": exit_reason,
                    "desired_long_position": desired_long,
                    "desired_short_position": desired_short,
                }
            )

    return {symbol: pd.DataFrame(values) for symbol, values in rows.items()}


def _normalize_pair_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """ペア片脚の必須列と値を検査する。

    Args:
        frame: 検査するDataFrame。
        symbol: エラー表示用の銘柄。

    Returns:
        UTC時刻と数値を正規化したコピー。

    Raises:
        ValueError: 必須列、時刻、価格、Fundingが不正な場合。
    """

    required = {"event_time", "open", "close", "funding_rate", "funding_event"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing pair columns for {symbol}: {sorted(missing)}")
    result = frame.copy().reset_index(drop=True)
    result["event_time"] = pd.to_datetime(result["event_time"], utc=True, errors="coerce")
    if (
        result.empty
        or result["event_time"].isna().any()
        or result["event_time"].duplicated().any()
        or not result["event_time"].is_monotonic_increasing
    ):
        raise ValueError(f"invalid pair timestamps: {symbol}")
    for column in ("open", "close"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
        if result[column].isna().any() or not result[column].gt(0).all():
            raise ValueError(f"invalid {column}: {symbol}")
    result["funding_rate"] = pd.to_numeric(result["funding_rate"], errors="coerce")
    if result["funding_rate"].isna().any() or not result["funding_rate"].map(math.isfinite).all():
        raise ValueError(f"invalid funding rate: {symbol}")
    result["funding_event"] = result["funding_event"].map(bool)
    return result


def _normalize_start(value: pd.Timestamp | None) -> pd.Timestamp | None:
    """開始時刻をUTCへ変換する。

    Args:
        value: timezone-awareな開始時刻またはNone。

    Returns:
        UTC時刻またはNone。

    Raises:
        ValueError: timezoneなしの場合。
    """

    if value is None:
        return None
    result = pd.Timestamp(value)
    if result.tzinfo is None:
        raise ValueError("start_trading_at must include a timezone")
    return result.tz_convert("UTC")
