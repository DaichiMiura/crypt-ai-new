"""実験で共有するKline整形、シグナル、決定論的会計を提供する。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
import math
from pathlib import Path
from typing import Iterable

import pandas as pd


KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
]
INTERPOLATED_COLUMN = "is_interpolated"
INPUT_COLUMNS = [*KLINE_COLUMNS, INTERPOLATED_COLUMN]
DAILY_COLUMNS = [
    "event_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    INTERPOLATED_COLUMN,
]


@dataclass(frozen=True)
class CostModel:
    """バックテストで使う片道の費用仮定。"""

    fee_rate: Decimal
    round_trip_spread: Decimal
    slippage_per_fill: Decimal

    def __post_init__(self) -> None:
        """費用率が非負で、片道手数料として解釈できることを検査する。

        Raises:
            ValueError: 費用率が負、または手数料率が100%以上の場合。
        """

        for name, value in (
            ("fee_rate", self.fee_rate),
            ("round_trip_spread", self.round_trip_spread),
            ("slippage_per_fill", self.slippage_per_fill),
        ):
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.fee_rate >= 1:
            raise ValueError("fee_rate must be below 1")
        if self.round_trip_spread / Decimal("2") + self.slippage_per_fill >= 1:
            raise ValueError("sell-side spread and slippage must be below 1")

    @property
    def half_spread(self) -> Decimal:
        """片道のspread仮定を返す。"""

        return self.round_trip_spread / Decimal("2")

    def buy_price(self, raw_price: Decimal) -> Decimal:
        """買い側のspreadとslippageを含む想定約定価格を返す。

        Args:
            raw_price: 約定候補となる市場価格。

        Returns:
            買い側コストを加えた価格。
        """

        return raw_price * (Decimal("1") + self.half_spread + self.slippage_per_fill)

    def sell_price(self, raw_price: Decimal) -> Decimal:
        """売り側のspreadとslippageを含む想定約定価格を返す。

        Args:
            raw_price: 約定候補となる市場価格。

        Returns:
            売り側コストを差し引いた価格。
        """

        return raw_price * (Decimal("1") - self.half_spread - self.slippage_per_fill)


def _parse_timestamp(values: pd.Series) -> pd.Series:
    """ミリ秒・マイクロ秒の時刻列をUTCへ正規化する。

    Args:
        values: Binanceのopen timeまたはclose time列。

    Returns:
        UTCのtimezone-awareな時刻列。

    Raises:
        ValueError: 数値へ変換できない時刻がある場合。
    """

    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.isna().any():
        raise ValueError("timestamp column contains non-numeric values")

    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns, UTC]")
    microseconds = numeric >= 100_000_000_000_000
    if microseconds.any():
        result.loc[microseconds] = pd.to_datetime(
            numeric.loc[microseconds], unit="us", utc=True
        )
    milliseconds = ~microseconds
    if milliseconds.any():
        result.loc[milliseconds] = pd.to_datetime(
            numeric.loc[milliseconds], unit="ms", utc=True
        )
    return result


def read_kline_file(path: Path) -> pd.DataFrame:
    """BinanceのKline CSVを読み込んで型を整える。

    Args:
        path: ヘッダーなしのKline CSVへのパス。

    Returns:
        正規化前のKlineデータフレーム。

    Raises:
        ValueError: 必須列を数値へ変換できない場合。
    """

    frame = pd.read_csv(path, header=None, names=INPUT_COLUMNS)
    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="coerce")
    frame = frame[frame["open_time"].notna()].copy()
    frame[INTERPOLATED_COLUMN] = (
        frame[INTERPOLATED_COLUMN]
        .fillna(False)
        .astype(str)
        .str.lower()
        .isin(["1", "true", "yes"])
    )
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if frame[column].isna().any():
            raise ValueError(f"{path}: invalid numeric value in {column}")
    frame["event_time"] = _parse_timestamp(frame["open_time"])
    return frame


def load_kline_files(paths: Iterable[Path]) -> pd.DataFrame:
    """複数のKline CSVを結合し、重複を検査して時系列へ整列する。

    Args:
        paths: 読み込むKline CSVのパス列。

    Returns:
        `event_time`で昇順に整列したKlineデータフレーム。

    Raises:
        ValueError: 入力が空、重複時刻、または時刻がUTCでない場合。
    """

    ordered_paths = sorted(Path(path) for path in paths)
    if not ordered_paths:
        raise ValueError("no kline files were provided")
    frame = pd.concat((read_kline_file(path) for path in ordered_paths), ignore_index=True)
    duplicate_count = int(frame["event_time"].duplicated().sum())
    if duplicate_count:
        raise ValueError(f"duplicate kline open times: {duplicate_count}")
    return frame.sort_values("event_time").reset_index(drop=True)


def inspect_hourly_data(frame: pd.DataFrame) -> dict[str, object]:
    """1時間足データの欠損と基本範囲を検査する。

    Args:
        frame: `load_kline_files`が返す時系列データ。

    Returns:
        行数、期間、重複数、欠損時間数を含む検査結果。
    """

    timestamps = frame["event_time"].sort_values()
    gaps = timestamps.diff().dropna()
    expected = pd.Timedelta(hours=1)
    missing_segments = int((gaps > expected).sum())
    missing_intervals = int(
        ((gaps / expected).round().astype("int64") - 1).clip(lower=0).sum()
    )
    duplicate_count = int(timestamps.duplicated().sum())
    interpolated = frame.get(
        INTERPOLATED_COLUMN, pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
    return {
        "rows": int(len(frame)),
        "start_utc": timestamps.iloc[0].isoformat() if len(timestamps) else None,
        "end_utc": timestamps.iloc[-1].isoformat() if len(timestamps) else None,
        "duplicate_count": duplicate_count,
        "missing_segments": missing_segments,
        "missing_intervals": missing_intervals,
        "interpolated_rows": int(interpolated.sum()),
        "interpolated_ratio": float(interpolated.mean()) if len(frame) else 0.0,
    }


def interpolate_missing_hourly_data(frame: pd.DataFrame) -> pd.DataFrame:
    """内部欠損の1時間足を時間線形補間し、合成行を明示する。

    `open`、`high`、`low`、`close`、出来高関連列を時刻に対して線形補間する。
    合成行のhigh/lowはOHLCの順序を壊さないように補正し、観測済みの行は
    変更しない。これは取引所の実測値ではないため、戻り値の
    `is_interpolated`で合成行を追跡できる。

    Args:
        frame: `event_time`を持つ、重複のない1時間足データ。

    Returns:
        欠損を埋めたデータフレーム。合成行には`is_interpolated=True`を付ける。

    Raises:
        ValueError: 欠損が期間の端にある、必須列が不足する、または補間後にNaNが残る場合。
    """

    required = {"event_time", *KLINE_COLUMNS}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing interpolation columns: {sorted(missing)}")
    source = frame.sort_values("event_time").copy()
    if source["event_time"].duplicated().any():
        raise ValueError("cannot interpolate duplicate event times")
    source[INTERPOLATED_COLUMN] = source.get(
        INTERPOLATED_COLUMN, pd.Series(False, index=source.index)
    ).fillna(False).astype(bool)
    source = source.set_index("event_time")
    expected = pd.date_range(
        source.index.min(), source.index.max(), freq="h", tz="UTC"
    )
    expected.name = "event_time"
    missing_times = expected.difference(source.index)
    if not len(missing_times):
        return source.reset_index()
    if missing_times[0] <= expected[0] or missing_times[-1] >= expected[-1]:
        raise ValueError("linear interpolation requires internal gaps only")

    expanded = source.reindex(expected)
    synthetic = expanded["open"].isna()
    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
    ]
    observed_values = {
        column: expanded[column].copy()
        for column in [*numeric_columns, "open_time", "close_time", "ignore"]
    }
    for column in numeric_columns:
        expanded[column] = pd.to_numeric(expanded[column], errors="coerce").interpolate(
            method="time", limit_area="inside"
        )
    if expanded[numeric_columns].isna().any().any():
        raise ValueError("linear interpolation left numeric NaN values")

    synthetic_indices = expanded.index[synthetic]
    expanded.loc[synthetic_indices, "high"] = expanded.loc[
        synthetic_indices, ["high", "open", "close"]
    ].max(axis=1)
    expanded.loc[synthetic_indices, "low"] = expanded.loc[
        synthetic_indices, ["low", "open", "close"]
    ].min(axis=1)
    nonnegative_columns = [
        "volume",
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
    ]
    expanded.loc[synthetic_indices, nonnegative_columns] = expanded.loc[
        synthetic_indices, nonnegative_columns
    ].clip(lower=0)
    expanded.loc[synthetic_indices, "number_of_trades"] = expanded.loc[
        synthetic_indices, "number_of_trades"
    ].round()
    timestamps_ms = expanded.index.astype("int64") // 1_000_000
    expanded["open_time"] = timestamps_ms
    expanded["close_time"] = timestamps_ms + 3_599_999
    expanded["ignore"] = expanded["ignore"].fillna(0)
    observed_indices = expanded.index[~synthetic]
    for column, original in observed_values.items():
        expanded.loc[observed_indices, column] = original.loc[observed_indices]
    expanded[INTERPOLATED_COLUMN] = synthetic
    return expanded.reset_index()


def aggregate_hourly_to_daily(frame: pd.DataFrame) -> pd.DataFrame:
    """完全なUTC 1時間足をUTC日足へ集約する。

    Args:
        frame: `load_kline_files`が返す、欠損のない1時間足データ。

    Returns:
        日足のevent_time、OHLCV、出来高関連列、合成行フラグを含むデータ。

    Raises:
        ValueError: 日単位の24本が揃わない、重複がある、または必須列が不足する場合。
    """

    required = {
        "event_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing daily aggregation columns: {sorted(missing)}")
    source = frame.sort_values("event_time").copy()
    if source["event_time"].duplicated().any():
        raise ValueError("cannot aggregate duplicate event times")
    source["day"] = source["event_time"].dt.floor("D")
    counts = source.groupby("day", sort=True).size()
    if (counts != 24).any():
        incomplete = counts[counts != 24].to_dict()
        raise ValueError(f"daily aggregation requires 24 hourly bars: {incomplete}")

    numeric_columns = [
        "volume",
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
    ]
    rows: list[dict[str, object]] = []
    for day, group in source.groupby("day", sort=True):
        group = group.sort_values("event_time")

        def numeric_sum(column: str) -> float:
            if column not in group:
                return 0.0
            return float(pd.to_numeric(group[column], errors="coerce").sum())

        rows.append(
            {
                "event_time": day,
                "open": float(group.iloc[0]["open"]),
                "high": float(group["high"].max()),
                "low": float(group["low"].min()),
                "close": float(group.iloc[-1]["close"]),
                "volume": numeric_sum("volume"),
                "quote_asset_volume": numeric_sum("quote_asset_volume"),
                "number_of_trades": int(round(numeric_sum("number_of_trades"))),
                "taker_buy_base_asset_volume": numeric_sum(
                    "taker_buy_base_asset_volume"
                ),
                "taker_buy_quote_asset_volume": numeric_sum(
                    "taker_buy_quote_asset_volume"
                ),
                INTERPOLATED_COLUMN: bool(
                    group.get(
                        INTERPOLATED_COLUMN,
                        pd.Series(False, index=group.index),
                    )
                    .fillna(False)
                    .astype(bool)
                    .any()
                ),
            }
        )
    return pd.DataFrame(rows, columns=DAILY_COLUMNS)


def inspect_daily_data(frame: pd.DataFrame) -> dict[str, object]:
    """日足データの重複、欠損、合成行を検査する。

    Args:
        frame: `aggregate_hourly_to_daily`が返す日足データ。

    Returns:
        行数、期間、重複日数、欠損日数、合成行数を含む検査結果。
    """

    timestamps = frame["event_time"].sort_values()
    gaps = timestamps.diff().dropna()
    expected = pd.Timedelta(days=1)
    missing_segments = int((gaps > expected).sum())
    missing_intervals = int(
        ((gaps / expected).round().astype("int64") - 1).clip(lower=0).sum()
    )
    duplicate_count = int(timestamps.duplicated().sum())
    interpolated = frame.get(
        INTERPOLATED_COLUMN, pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
    return {
        "rows": int(len(frame)),
        "start_utc": timestamps.iloc[0].isoformat() if len(timestamps) else None,
        "end_utc": timestamps.iloc[-1].isoformat() if len(timestamps) else None,
        "duplicate_count": duplicate_count,
        "missing_segments": missing_segments,
        "missing_intervals": missing_intervals,
        "interpolated_rows": int(interpolated.sum()),
        "interpolated_ratio": float(interpolated.mean()) if len(frame) else 0.0,
    }


def prepare_donchian_signals(
    frame: pd.DataFrame,
    entry_window: int = 55,
    exit_window: int = 20,
) -> pd.DataFrame:
    """終値のDonchianチャネル突破から次バー用の現物ポジションを作る。

    現在の終値を、直前`entry_window`本の最高値より上で確定した場合に買い、
    直前`exit_window`本の最安値より下で確定した場合に決済する。現在足自身を
    チャネル計算へ含めず、シグナルは次バー始値へ遅延させる。

    Args:
        frame: `event_time`、`open`、`high`、`low`、`close`列を含む日足データ。
        entry_window: エントリー判定に使う過去バー数。
        exit_window: 決済判定に使う過去バー数。

    Returns:
        チャネル水準、シグナル状態、次バー用`desired_position`を追加したデータ。

    Raises:
        ValueError: 必須列が不足する、windowが正でない、またはentryがexit以下の場合。
    """

    required = {"event_time", "open", "high", "low", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing Donchian columns: {sorted(missing)}")
    if entry_window <= 0 or exit_window <= 0 or entry_window <= exit_window:
        raise ValueError("windows must satisfy 0 < exit_window < entry_window")
    result = frame.sort_values("event_time").reset_index(drop=True).copy()
    result["entry_level"] = result["high"].rolling(entry_window).max().shift(1)
    result["exit_level"] = result["low"].rolling(exit_window).min().shift(1)

    signal_positions: list[int] = []
    position = 0
    for row in result.itertuples(index=False):
        if position == 0 and pd.notna(row.entry_level) and row.close > row.entry_level:
            position = 1
        elif position == 1 and pd.notna(row.exit_level) and row.close < row.exit_level:
            position = 0
        signal_positions.append(position)
    result["signal_position"] = signal_positions
    result["desired_position"] = (
        result["signal_position"].shift(1, fill_value=0).astype(int)
    )
    return result


def prepare_donchian_regime_filter_signals(
    frame: pd.DataFrame,
    entry_window: int = 55,
    exit_window: int = 20,
    regime_window: int = 200,
) -> pd.DataFrame:
    """Donchianシグナルの新規エントリーだけを長期SMAで制限する。

    Donchian 55/20のシグナルを基礎にし、flat状態からの新規entryが発生する日
    の終値が`regime_window`日SMAを上回る場合だけ保有状態へ遷移する。いったん
    保有した後はSMAの低下では退出せず、Donchianのexitだけを使う。SMAやDonchian
    の履歴が不足する期間はregime不成立としてentryを許可しない。確定足の状態は
    次日始値へ遅延させる。

    Args:
        frame: `event_time`、`open`、`high`、`low`、`close`列を含む日足データ。
        entry_window: Donchian entryに使う過去バー数。
        exit_window: Donchian exitに使う過去バー数。
        regime_window: 長期SMAに使う日足本数。

    Returns:
        Donchian水準、SMAレジーム判定、filteredシグナル状態、
        次日用`desired_position`を追加したデータ。

    Raises:
        ValueError: windowが正でない、または入力列が不足する場合。
    """

    if regime_window <= 0:
        raise ValueError("regime_window must be positive")
    result = prepare_donchian_signals(
        frame, entry_window=entry_window, exit_window=exit_window
    )
    result["base_signal_position"] = result["signal_position"]
    result["regime_sma"] = result["close"].rolling(regime_window).mean()
    result["regime_ok"] = result["close"] > result["regime_sma"]

    filtered_positions: list[int] = []
    entry_signals: list[bool] = []
    exit_signals: list[bool] = []
    position = 0
    previous_base_position = 0
    for row in result.itertuples(index=False):
        entered = False
        exited = False
        base_position = int(row.base_signal_position)
        if position == 0 and base_position == 1 and previous_base_position == 0:
            if bool(row.regime_ok):
                position = 1
                entered = True
        elif position == 1 and base_position == 0:
            position = 0
            exited = True
        filtered_positions.append(position)
        entry_signals.append(entered)
        exit_signals.append(exited)
        previous_base_position = base_position

    result["signal_position"] = filtered_positions
    result["entry_signal"] = entry_signals
    result["exit_signal"] = exit_signals
    result["desired_position"] = (
        result["signal_position"].shift(1, fill_value=0).astype(int)
    )
    return result


def prepare_atr_trailing_exit_signals(
    frame: pd.DataFrame,
    entry_window: int = 55,
    baseline_exit_window: int = 20,
    regime_window: int = 200,
    atr_window: int = 20,
    atr_multiplier: float = 3.0,
) -> pd.DataFrame:
    """SMA制限付きDonchian entryへATRトレーリング退出を適用する。

    entry候補はDonchian 55日高値突破かつ長期SMA上側に固定する。実際に保有を
    始めた日以降の最高値から単純移動平均ATRの指定倍数を引き、stopは切り下げない。
    終値がstopを下回った日の次日始値で退出する。退出後は、基礎Donchian状態が
    いったんflatへ戻った後に発生する新しいentryイベントまで再entryしない。

    Args:
        frame: `event_time`、`open`、`high`、`low`、`close`列を含む日足データ。
        entry_window: Donchian entryに使う過去バー数。
        baseline_exit_window: 基礎状態の循環判定に使うDonchian exitのバー数。
        regime_window: 新規entryを許可する長期SMAのバー数。
        atr_window: True Rangeの単純移動平均に使うバー数。
        atr_multiplier: 最高値からstopまでのATR倍率。

    Returns:
        基準戦略列、ATR、追随stop、退出イベント、次日用ATR positionを追加したデータ。

    Raises:
        ValueError: ATR設定が正でない場合。
    """

    if atr_window <= 0 or atr_multiplier <= 0:
        raise ValueError("atr_window and atr_multiplier must be positive")
    result = prepare_donchian_regime_filter_signals(
        frame,
        entry_window=entry_window,
        exit_window=baseline_exit_window,
        regime_window=regime_window,
    )
    previous_close = result["close"].shift(1)
    result["true_range"] = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["atr"] = result["true_range"].rolling(atr_window).mean()

    desired_positions: list[int] = []
    highest_highs: list[float | None] = []
    candidate_stops: list[float | None] = []
    trailing_stops: list[float | None] = []
    exit_signals: list[bool] = []
    current_position = 0
    next_position = 0
    highest_high: float | None = None
    trailing_stop: float | None = None

    for row in result.itertuples(index=False):
        previous_position = current_position
        current_position = next_position
        exit_signal = False
        candidate_stop: float | None = None
        if current_position == 1:
            high = float(row.high)
            highest_high = high if previous_position == 0 else max(highest_high, high)
            if pd.notna(row.atr):
                candidate_stop = highest_high - atr_multiplier * float(row.atr)
                trailing_stop = (
                    candidate_stop
                    if trailing_stop is None
                    else max(trailing_stop, candidate_stop)
                )
            if trailing_stop is not None and float(row.close) < trailing_stop:
                exit_signal = True
                next_position = 0
            else:
                next_position = 1
        else:
            highest_high = None
            trailing_stop = None
            if bool(row.entry_signal) and pd.notna(row.atr) and float(row.atr) > 0:
                next_position = 1
            else:
                next_position = 0
        desired_positions.append(current_position)
        highest_highs.append(highest_high)
        candidate_stops.append(candidate_stop)
        trailing_stops.append(trailing_stop)
        exit_signals.append(exit_signal)

    result["atr_highest_high"] = highest_highs
    result["atr_candidate_stop"] = candidate_stops
    result["atr_trailing_stop"] = trailing_stops
    result["atr_exit_signal"] = exit_signals
    result["desired_atr_position"] = desired_positions
    return result


def prepare_donchian_long_short_regime_signals(
    frame: pd.DataFrame,
    entry_window: int = 55,
    exit_window: int = 20,
    regime_window: int = 200,
) -> pd.DataFrame:
    """Donchianのlong・short鏡像シグナルと単一ポジション状態を作る。

    longは過去`entry_window`本の高値突破かつcloseがSMA200を上回る場合に建て、
    過去`exit_window`本の安値割れで決済する。shortは過去`entry_window`本の安値
    割れかつcloseがSMA200を下回る場合に建て、過去`exit_window`本の高値突破で
    買い戻す。long・shortの単独状態と、同時に一つだけ保有するcombined状態を
    別々に出力する。確定足の状態は次日始値へ遅延させる。

    Args:
        frame: `event_time`、`open`、`high`、`low`、`close`列を含む日足データ。
        entry_window: long/shortのentryに使う過去バー数。
        exit_window: long/shortのexitに使う過去バー数。
        regime_window: long/shortのSMAレジーム判定に使う日足本数。

    Returns:
        long・short・combinedの状態、チャネル、レジーム判定、次日用positionを
        追加したデータ。shortのpositionは`-1`で表す。

    Raises:
        ValueError: 必須列が不足する、またはwindowが正でない場合。
    """

    required = {"event_time", "open", "high", "low", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing long/short Donchian columns: {sorted(missing)}")
    if entry_window <= 0 or exit_window <= 0 or regime_window <= 0:
        raise ValueError("windows must be positive")
    if entry_window <= exit_window:
        raise ValueError("entry_window must be greater than exit_window")

    result = frame.sort_values("event_time").reset_index(drop=True).copy()
    result["long_entry_level"] = result["high"].rolling(entry_window).max().shift(1)
    result["long_exit_level"] = result["low"].rolling(exit_window).min().shift(1)
    result["short_entry_level"] = result["low"].rolling(entry_window).min().shift(1)
    result["short_exit_level"] = result["high"].rolling(exit_window).max().shift(1)
    result["regime_sma"] = result["close"].rolling(regime_window).mean()
    result["long_regime_ok"] = result["close"] > result["regime_sma"]
    result["short_regime_ok"] = result["close"] < result["regime_sma"]

    base_long_positions: list[int] = []
    base_short_positions: list[int] = []
    long_positions: list[int] = []
    short_positions: list[int] = []
    combined_positions: list[int] = []
    long_entries: list[bool] = []
    long_exits: list[bool] = []
    short_entries: list[bool] = []
    short_exits: list[bool] = []
    combined_conflicts: list[bool] = []
    base_long_position = 0
    base_short_position = 0
    long_position = 0
    short_position = 0
    combined_position = 0
    for row in result.itertuples(index=False):
        previous_base_long = base_long_position
        previous_base_short = base_short_position
        if (
            base_long_position == 0
            and pd.notna(row.long_entry_level)
            and row.close > row.long_entry_level
        ):
            base_long_position = 1
        elif (
            base_long_position == 1
            and pd.notna(row.long_exit_level)
            and row.close < row.long_exit_level
        ):
            base_long_position = 0
        if (
            base_short_position == 0
            and pd.notna(row.short_entry_level)
            and row.close < row.short_entry_level
        ):
            base_short_position = -1
        elif (
            base_short_position == -1
            and pd.notna(row.short_exit_level)
            and row.close > row.short_exit_level
        ):
            base_short_position = 0

        long_entry = (
            long_position == 0
            and previous_base_long == 0
            and base_long_position == 1
            and bool(row.long_regime_ok)
        )
        long_exit = long_position == 1 and base_long_position == 0
        short_entry = (
            short_position == 0
            and previous_base_short == 0
            and base_short_position == -1
            and bool(row.short_regime_ok)
        )
        short_exit = short_position == -1 and base_short_position == 0
        if long_entry:
            long_position = 1
        elif long_exit:
            long_position = 0
        if short_entry:
            short_position = -1
        elif short_exit:
            short_position = 0

        conflict = False
        if combined_position == 1:
            if long_exit:
                combined_position = 0
        elif combined_position == -1:
            if short_exit:
                combined_position = 0
        elif long_entry and short_entry:
            conflict = True
        elif long_entry:
            combined_position = 1
        elif short_entry:
            combined_position = -1

        base_long_positions.append(base_long_position)
        base_short_positions.append(base_short_position)
        long_positions.append(long_position)
        short_positions.append(short_position)
        combined_positions.append(combined_position)
        long_entries.append(long_entry)
        long_exits.append(long_exit)
        short_entries.append(short_entry)
        short_exits.append(short_exit)
        combined_conflicts.append(conflict)

    result["base_long_signal_position"] = base_long_positions
    result["base_short_signal_position"] = base_short_positions
    result["long_signal_position"] = long_positions
    result["short_signal_position"] = short_positions
    result["signal_position"] = combined_positions
    result["long_entry_signal"] = long_entries
    result["long_exit_signal"] = long_exits
    result["short_entry_signal"] = short_entries
    result["short_exit_signal"] = short_exits
    result["combined_conflict"] = combined_conflicts
    result["desired_long_position"] = (
        result["long_signal_position"].shift(1, fill_value=0).astype(int)
    )
    result["desired_short_position"] = (
        result["short_signal_position"].shift(1, fill_value=0).astype(int)
    )
    result["desired_position"] = (
        result["signal_position"].shift(1, fill_value=0).astype(int)
    )
    return result


def prepare_cross_sectional_momentum_short_signals(
    frames: Mapping[str, pd.DataFrame],
    *,
    lookback_bars: int = 360,
    rebalance_bars: int = 84,
    short_count: int = 2,
) -> dict[str, pd.DataFrame]:
    """銘柄間モメンタムの下位銘柄をショートする状態を作る。

    各銘柄の確定終値から`lookback_bars`本前までのリターンを比較し、
    リターンが低い順に`short_count`銘柄を選ぶ。選定は`rebalance_bars`本ごとに
    行い、確定したランキングを次のバーの始値へ遅延する。したがって、選定に
    現在バーの始値・高値・安値、または将来バーの情報は使わない。銘柄間の同率は
    銘柄名の昇順で決定し、同じ入力から同じ選定結果を再現できるようにする。

    Args:
        frames: 銘柄名をキーとする、UTCの`event_time`と正の`close`を含むDataFrame。
            すべての銘柄は同じ時刻集合、昇順、一定間隔でなければならない。
        lookback_bars: モメンタム計算に使う過去バー数。
        rebalance_bars: ランキングを更新する間隔。最初の更新は、データ先頭から
            `lookback_bars`本経過した時点で行う。
        short_count: 各更新でショートする下位銘柄数。

    Returns:
        銘柄ごとの入力列に、モメンタム、順位、リバランス選定、
        `desired_short_position`、`desired_long_position`を追加したDataFrame。

    Raises:
        ValueError: 銘柄が空、入力列・時刻・価格が不正、銘柄間の時刻が不一致、
            またはパラメータが不正な場合。
    """

    if not frames:
        raise ValueError("frames must not be empty")
    if lookback_bars <= 0 or rebalance_bars <= 0 or short_count <= 0:
        raise ValueError("lookback_bars, rebalance_bars, and short_count must be positive")
    symbols = tuple(sorted(frames))
    if short_count > len(symbols):
        raise ValueError("short_count must not exceed the number of symbols")

    prepared: dict[str, pd.DataFrame] = {}
    timestamp_sets: set[frozenset[pd.Timestamp]] = set()
    for symbol in symbols:
        source = frames[symbol].copy()
        required = {"event_time", "close"}
        missing = required.difference(source.columns)
        if missing:
            raise ValueError(f"missing cross-sectional columns for {symbol}: {sorted(missing)}")
        source["event_time"] = pd.to_datetime(source["event_time"], utc=True, errors="coerce")
        if source["event_time"].isna().any():
            raise ValueError(f"invalid event_time: {symbol}")
        if (
            source["event_time"].duplicated().any()
            or not source["event_time"].is_monotonic_increasing
        ):
            raise ValueError(f"event_time must be unique and sorted: {symbol}")
        source["close"] = pd.to_numeric(source["close"], errors="coerce")
        if source["close"].isna().any() or not source["close"].gt(0).all():
            raise ValueError(f"close must be positive: {symbol}")
        prepared[symbol] = source.reset_index(drop=True)
        timestamp_sets.add(frozenset(source["event_time"]))

    if len(timestamp_sets) != 1:
        raise ValueError("all cross-sectional frames must have identical timestamps")
    timestamps = prepared[symbols[0]]["event_time"]
    if len(timestamps) <= lookback_bars:
        raise ValueError("frames must contain more rows than lookback_bars")

    momentum = pd.DataFrame(
        {
            symbol: prepared[symbol]["close"].div(
                prepared[symbol]["close"].shift(lookback_bars)
            )
            - 1
            for symbol in symbols
        }
    )
    selected = {symbol: [0] * len(timestamps) for symbol in symbols}
    ranks = {symbol: [pd.NA] * len(timestamps) for symbol in symbols}
    rebalance_flags = [False] * len(timestamps)
    active: dict[str, int] = {symbol: 0 for symbol in symbols}
    for index in range(len(timestamps)):
        if index >= lookback_bars and (index - lookback_bars) % rebalance_bars == 0:
            values = {
                symbol: float(momentum.loc[index, symbol]) for symbol in symbols
            }
            if all(math.isfinite(value) for value in values.values()):
                ordered = sorted(symbols, key=lambda symbol: (values[symbol], symbol))
                current_ranks = {
                    symbol: rank for rank, symbol in enumerate(ordered, start=1)
                }
                for symbol in symbols:
                    ranks[symbol][index] = current_ranks[symbol]
                    active[symbol] = int(symbol in ordered[:short_count])
                rebalance_flags[index] = True
        for symbol in symbols:
            selected[symbol][index] = active[symbol]

    result: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = prepared[symbol].copy()
        frame["momentum_return"] = momentum[symbol]
        frame["cross_sectional_rank"] = pd.array(ranks[symbol], dtype="Int64")
        frame["rebalance_signal"] = rebalance_flags
        frame["short_signal_position"] = selected[symbol]
        frame["desired_short_position"] = (
            frame["short_signal_position"].shift(1, fill_value=0).astype(int)
        )
        frame["desired_long_position"] = 0
        result[symbol] = frame
    return result


def prepare_cross_sectional_momentum_long_signals(
    frames: Mapping[str, pd.DataFrame],
    *,
    lookback_bars: int = 360,
    rebalance_bars: int = 84,
    long_count: int = 2,
    require_positive_median: bool = True,
    early_exit_on_nonpositive: bool = False,
    early_exit_on_nonpositive_median: bool = False,
) -> dict[str, pd.DataFrame]:
    """銘柄間モメンタム上位をロングする状態を作る。

    各銘柄の確定終値から`lookback_bars`本のリターンを計算し、リターンが
    高い順に`long_count`銘柄を選ぶ。`require_positive_median`が有効な場合、
    全銘柄リターンの中央値が0以下なら全銘柄を現金状態にする。
    `early_exit_on_nonpositive`が有効な場合、選定銘柄自身のモメンタムが0以下に
    なった時点で選定を解除し、次のリバランスまで再選定しない。状態は確定した
    次のバーの始値へ遅延する。`early_exit_on_nonpositive_median`が有効な場合は、
    週の途中で全銘柄モメンタム中央値が0以下になった時点で全選定を解除する。

    Args:
        frames: 銘柄名をキーとする、UTCの`event_time`と正の`close`を含む
            DataFrame。全銘柄の時刻集合は一致しなければならない。
        lookback_bars: モメンタム計算に使う過去バー数。
        rebalance_bars: ランキングを更新する間隔。
        long_count: 各更新でロングする上位銘柄数。
        require_positive_median: 銘柄リターン中央値が正の場合だけ選定するか。
        early_exit_on_nonpositive: 選定銘柄のモメンタムが0以下なら、次の
            リバランスを待たず選定解除するか。
        early_exit_on_nonpositive_median: 全銘柄モメンタム中央値が週の途中で
            0以下なら、全選定を次のリバランスまで解除するか。

    Returns:
        銘柄別入力へモメンタム、順位、市場regime、選定状態、次足始値用
        `desired_long_position`を追加したDataFrame。

    Raises:
        ValueError: 入力、時刻、価格、パラメータ、または銘柄間時刻が不正な場合。
    """

    if not frames:
        raise ValueError("frames must not be empty")
    if lookback_bars <= 0 or rebalance_bars <= 0 or long_count <= 0:
        raise ValueError("lookback_bars, rebalance_bars, and long_count must be positive")
    if not all(
        isinstance(value, bool)
        for value in (
            require_positive_median,
            early_exit_on_nonpositive,
            early_exit_on_nonpositive_median,
        )
    ):
        raise ValueError("regime and early-exit flags must be bool")
    symbols = tuple(sorted(frames))
    if long_count > len(symbols):
        raise ValueError("long_count must not exceed the number of symbols")

    prepared: dict[str, pd.DataFrame] = {}
    timestamp_sets: set[frozenset[pd.Timestamp]] = set()
    for symbol in symbols:
        source = frames[symbol].copy()
        required = {"event_time", "close"}
        missing = required.difference(source.columns)
        if missing:
            raise ValueError(
                f"missing cross-sectional columns for {symbol}: {sorted(missing)}"
            )
        source["event_time"] = pd.to_datetime(
            source["event_time"], utc=True, errors="coerce"
        )
        if source["event_time"].isna().any():
            raise ValueError(f"invalid event_time: {symbol}")
        if (
            source["event_time"].duplicated().any()
            or not source["event_time"].is_monotonic_increasing
        ):
            raise ValueError(f"event_time must be unique and sorted: {symbol}")
        source["close"] = pd.to_numeric(source["close"], errors="coerce")
        if source["close"].isna().any() or not source["close"].gt(0).all():
            raise ValueError(f"close must be positive: {symbol}")
        prepared[symbol] = source.reset_index(drop=True)
        timestamp_sets.add(frozenset(source["event_time"]))

    if len(timestamp_sets) != 1:
        raise ValueError("all cross-sectional frames must have identical timestamps")
    timestamps = prepared[symbols[0]]["event_time"]
    if len(timestamps) <= lookback_bars:
        raise ValueError("frames must contain more rows than lookback_bars")

    momentum = pd.DataFrame(
        {
            symbol: prepared[symbol]["close"].div(
                prepared[symbol]["close"].shift(lookback_bars)
            )
            - 1
            for symbol in symbols
        }
    )
    selected = {symbol: [0] * len(timestamps) for symbol in symbols}
    early_exit_flags = {symbol: [False] * len(timestamps) for symbol in symbols}
    market_early_exit_flags = [False] * len(timestamps)
    ranks = {symbol: [pd.NA] * len(timestamps) for symbol in symbols}
    rebalance_flags = [False] * len(timestamps)
    median_values = [float("nan")] * len(timestamps)
    live_median_values = [float("nan")] * len(timestamps)
    regime_flags = [False] * len(timestamps)
    active = {symbol: 0 for symbol in symbols}
    current_median = float("nan")
    current_regime = False
    for index in range(len(timestamps)):
        live_values = {
            symbol: float(momentum.loc[index, symbol]) for symbol in symbols
        }
        live_median = (
            float(pd.Series(tuple(live_values.values())).median())
            if all(math.isfinite(value) for value in live_values.values())
            else float("nan")
        )
        live_median_values[index] = live_median
        if index >= lookback_bars and (index - lookback_bars) % rebalance_bars == 0:
            values = live_values
            if all(math.isfinite(value) for value in values.values()):
                current_median = live_median
                current_regime = (
                    current_median > 0 if require_positive_median else True
                )
                ordered = sorted(
                    symbols, key=lambda symbol: (-values[symbol], symbol)
                )
                current_ranks = {
                    symbol: rank for rank, symbol in enumerate(ordered, start=1)
                }
                for symbol in symbols:
                    ranks[symbol][index] = current_ranks[symbol]
                    active[symbol] = int(
                        current_regime and symbol in ordered[:long_count]
                    )
                rebalance_flags[index] = True
            else:
                current_median = float("nan")
                current_regime = False
                active = {symbol: 0 for symbol in symbols}
        if early_exit_on_nonpositive and index >= lookback_bars:
            for symbol in symbols:
                value = float(momentum.loc[index, symbol])
                if active[symbol] and (not math.isfinite(value) or value <= 0):
                    active[symbol] = 0
                    early_exit_flags[symbol][index] = True
        if (
            early_exit_on_nonpositive_median
            and not rebalance_flags[index]
            and any(active.values())
            and (not math.isfinite(live_median) or live_median <= 0)
        ):
            active = {symbol: 0 for symbol in symbols}
            market_early_exit_flags[index] = True
        for symbol in symbols:
            selected[symbol][index] = active[symbol]
        median_values[index] = current_median
        regime_flags[index] = current_regime

    result: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = prepared[symbol].copy()
        frame["momentum_return"] = momentum[symbol]
        frame["cross_sectional_rank"] = pd.array(ranks[symbol], dtype="Int64")
        frame["market_median_momentum"] = median_values
        frame["live_market_median_momentum"] = live_median_values
        frame["market_regime_ok"] = regime_flags
        frame["rebalance_signal"] = rebalance_flags
        frame["momentum_early_exit_signal"] = early_exit_flags[symbol]
        frame["market_early_exit_signal"] = market_early_exit_flags
        frame["long_signal_position"] = selected[symbol]
        frame["desired_long_position"] = (
            frame["long_signal_position"].shift(1, fill_value=0).astype(int)
        )
        frame["desired_short_position"] = 0
        result[symbol] = frame
    return result


def prepare_volatility_scaled_regime_signals(
    frame: pd.DataFrame,
    entry_window: int = 55,
    exit_window: int = 20,
    regime_window: int = 200,
    volatility_window: int = 20,
    target_annual_volatility: float = 0.40,
) -> pd.DataFrame:
    """SMA付きDonchianのentry時に実現ボラティリティで投資比率を固定する。

    日次close-to-close単純リターンの母標準偏差を年率化し、新規entry時の
    投資比率を`min(1, target / realized_volatility)`とする。レバレッジは使わず、
    entry後はDonchian exitまで投資比率を変えない。日tの確定値から得た比率は
    日t+1の始値へ遅延する。

    Args:
        frame: `event_time`、`open`、`high`、`low`、`close`列を含む日足データ。
        entry_window: Donchian entryに使う過去バー数。
        exit_window: Donchian exitに使う過去バー数。
        regime_window: 新規entryのSMA判定に使う日足本数。
        volatility_window: 実現ボラティリティに使う日次リターン本数。
        target_annual_volatility: 投資比率の分子となる年率目標ボラティリティ。

    Returns:
        実現ボラティリティ、entry時の投資比率、保有中の固定比率、次日始値用
        `desired_exposure`を追加したデータ。

    Raises:
        ValueError: windowまたは目標ボラティリティが正でない場合。
    """

    if volatility_window <= 0:
        raise ValueError("volatility_window must be positive")
    if target_annual_volatility <= 0:
        raise ValueError("target_annual_volatility must be positive")
    result = prepare_donchian_regime_filter_signals(
        frame,
        entry_window=entry_window,
        exit_window=exit_window,
        regime_window=regime_window,
    )
    result["daily_return"] = result["close"].pct_change(fill_method=None)
    result["realized_volatility"] = (
        result["daily_return"].rolling(volatility_window).std(ddof=0)
        * math.sqrt(365)
    )

    signal_exposures: list[float] = []
    entry_exposures: list[float | None] = []
    active_exposure = 0.0
    for row in result.itertuples(index=False):
        entry_exposure = None
        if bool(row.entry_signal):
            realized = float(row.realized_volatility)
            if math.isfinite(realized) and realized > 0:
                active_exposure = min(
                    1.0, target_annual_volatility / realized
                )
                entry_exposure = active_exposure
        elif bool(row.exit_signal):
            active_exposure = 0.0
        signal_exposures.append(active_exposure)
        entry_exposures.append(entry_exposure)

    result["entry_exposure"] = entry_exposures
    result["signal_exposure"] = signal_exposures
    result["desired_exposure"] = result["signal_exposure"].shift(
        1, fill_value=0.0
    )
    return result


def prepare_bollinger_mean_reversion_signals(
    frame: pd.DataFrame,
    window: int = 20,
    std_multiplier: float = 2.0,
) -> pd.DataFrame:
    """ボリンジャーバンド下抜け買い・中心線回帰決済のポジションを作る。

    日tの終値が、日tを含む直近`window`本の平均から`std_multiplier`倍の
    標準偏差を引いた下側バンドを下回った場合に買い状態へ遷移する。保有中に
    終値が同じ窓の中心線を上回った場合に決済状態へ遷移する。標準偏差は母標準
    偏差（`ddof=0`）とし、確定した日足のシグナルを次日始値へ遅延させる。

    Args:
        frame: `event_time`、`open`、`close`列を含む日足データ。
        window: 移動平均と標準偏差に使う過去の日足本数。
        std_multiplier: 中心線からバンドまでの標準偏差倍率。

    Returns:
        バンド水準、シグナル状態、次日用`desired_position`を追加したデータ。

    Raises:
        ValueError: 必須列が不足する、windowが正でない、または倍率が正でない場合。
    """

    required = {"event_time", "open", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing Bollinger columns: {sorted(missing)}")
    if window <= 0 or std_multiplier <= 0:
        raise ValueError("window and std_multiplier must be positive")

    result = frame.sort_values("event_time").reset_index(drop=True).copy()
    result["middle_band"] = result["close"].rolling(window).mean()
    result["band_std"] = result["close"].rolling(window).std(ddof=0)
    result["upper_band"] = result["middle_band"] + std_multiplier * result["band_std"]
    result["lower_band"] = result["middle_band"] - std_multiplier * result["band_std"]

    signal_positions: list[int] = []
    position = 0
    for row in result.itertuples(index=False):
        if position == 0 and pd.notna(row.lower_band) and row.close < row.lower_band:
            position = 1
        elif position == 1 and pd.notna(row.middle_band) and row.close > row.middle_band:
            position = 0
        signal_positions.append(position)
    result["signal_position"] = signal_positions
    result["desired_position"] = (
        result["signal_position"].shift(1, fill_value=0).astype(int)
    )
    return result


def prepare_donchian_bollinger_exit_signals(
    frame: pd.DataFrame,
    entry_window: int = 55,
    band_window: int = 20,
    std_multiplier: float = 2.0,
) -> pd.DataFrame:
    """Donchianの買いとボリンジャー平均回帰の退出を組み合わせる。

    日tの終値が、日tを含めない直前`entry_window`本のhighの最高値を上回ったら
    現物ロングを建てる。保有中に終値がボリンジャー下側バンドを下回ったら退出を
    待機状態にし、その後に終値が中心線を上回った場合に退出する。標準偏差は母標準
    偏差（`ddof=0`）とし、確定足のシグナルを次日始値へ遅延させる。ボリンジャーの
    下側バンド割れを経ていない保有では、中心線の上抜けだけで退出しない。

    Args:
        frame: `event_time`、`open`、`high`、`close`列を含む日足データ。
        entry_window: Donchian買い判定に使う過去バー数。
        band_window: ボリンジャー中心線と標準偏差に使う日足本数。
        std_multiplier: 中心線からバンドまでの標準偏差倍率。

    Returns:
        Donchian水準、ボリンジャー水準、entry/exitイベント、状態、
        次日用`desired_position`を追加したデータ。

    Raises:
        ValueError: 必須列が不足する、windowが正でない、または倍率が正でない場合。
    """

    required = {"event_time", "open", "high", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing Donchian/Bollinger columns: {sorted(missing)}")
    if entry_window <= 0 or band_window <= 0 or std_multiplier <= 0:
        raise ValueError("windows and std_multiplier must be positive")

    result = frame.sort_values("event_time").reset_index(drop=True).copy()
    result["entry_level"] = result["high"].rolling(entry_window).max().shift(1)
    result["middle_band"] = result["close"].rolling(band_window).mean()
    result["band_std"] = result["close"].rolling(band_window).std(ddof=0)
    result["upper_band"] = result["middle_band"] + std_multiplier * result["band_std"]
    result["lower_band"] = result["middle_band"] - std_multiplier * result["band_std"]

    signal_positions: list[int] = []
    entry_signals: list[bool] = []
    exit_signals: list[bool] = []
    overlay_armed: list[bool] = []
    position = 0
    armed = False
    for row in result.itertuples(index=False):
        entered = False
        exited = False
        if position == 0 and pd.notna(row.entry_level) and row.close > row.entry_level:
            position = 1
            entered = True
            armed = False
        elif position == 1:
            if pd.notna(row.lower_band) and row.close < row.lower_band:
                armed = True
            if armed and pd.notna(row.middle_band) and row.close > row.middle_band:
                position = 0
                exited = True
                armed = False
        signal_positions.append(position)
        entry_signals.append(entered)
        exit_signals.append(exited)
        overlay_armed.append(armed)
    result["entry_signal"] = entry_signals
    result["exit_signal"] = exit_signals
    result["overlay_armed"] = overlay_armed
    result["signal_position"] = signal_positions
    result["desired_position"] = (
        result["signal_position"].shift(1, fill_value=0).astype(int)
    )
    return result


def prepare_signals(frame: pd.DataFrame, fast_window: int = 20, slow_window: int = 50) -> pd.DataFrame:
    """確定足だけを使ってSMAクロスの次バー用ポジションを作る。

    Args:
        frame: `event_time`、`open`、`close`列を含むKlineデータ。
        fast_window: 短期移動平均の本数。
        slow_window: 長期移動平均の本数。

    Returns:
        SMA列と、次バー始値で適用する`desired_position`を追加したデータ。

    Raises:
        ValueError: windowが正でない、またはfastがslow以上の場合。
    """

    if fast_window <= 0 or slow_window <= 0 or fast_window >= slow_window:
        raise ValueError("window sizes must satisfy 0 < fast_window < slow_window")
    result = frame.copy()
    result["fast_sma"] = result["close"].rolling(fast_window).mean()
    result["slow_sma"] = result["close"].rolling(slow_window).mean()
    close_signal = (result["fast_sma"] > result["slow_sma"]).fillna(False)
    result["desired_position"] = close_signal.shift(1, fill_value=False).astype(int)
    return result


def _decimal(value: object) -> Decimal:
    """数値を文字列表現経由でDecimalへ変換する。

    Args:
        value: pandasの行から得た価格、数量、または残高。

    Returns:
        浮動小数点の二進表現を混入させないDecimal値。
    """

    return Decimal(str(value))


def run_backtest(
    frame: pd.DataFrame,
    cost_model: CostModel,
    initial_cash: Decimal = Decimal("1000"),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """次バー始値で売買する現物ロング専用バックテストを実行する。

    Args:
        frame: `prepare_signals`の出力データ。
        cost_model: fee、spread、slippageの仮定。
        initial_cash: 初期のpaper現金残高。

    Returns:
        `(equity_curve, trades)`の組。金額計算はDecimalで行い、出力時に文字列化する。

    Raises:
        ValueError: 必須列が不足する、現金が負になる、または未知のposition値がある場合。
    """

    required = {"event_time", "open", "close", "desired_position"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing backtest columns: {sorted(missing)}")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    cash = initial_cash
    quantity = Decimal("0")
    equity_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []

    for row in frame.itertuples(index=False):
        desired = int(row.desired_position)
        if desired not in (0, 1):
            raise ValueError(f"unknown desired_position: {desired}")
        raw_open = _decimal(row.open)
        if raw_open <= 0:
            raise ValueError("open price must be positive")
        if desired == 1 and quantity == 0:
            execution_price = cost_model.buy_price(raw_open)
            fee = cash * cost_model.fee_rate
            quantity = (cash - fee) / execution_price
            cash = Decimal("0")
            trade_rows.append(
                {
                    "event_time": row.event_time,
                    "side": "BUY",
                    "raw_price": str(raw_open),
                    "execution_price": str(execution_price),
                    "quantity": str(quantity),
                    "fee": str(fee),
                    INTERPOLATED_COLUMN: bool(
                        getattr(row, INTERPOLATED_COLUMN, False)
                    ),
                }
            )
        elif desired == 0 and quantity > 0:
            execution_price = cost_model.sell_price(raw_open)
            sold_quantity = quantity
            gross = sold_quantity * execution_price
            fee = gross * cost_model.fee_rate
            cash = gross - fee
            quantity = Decimal("0")
            trade_rows.append(
                {
                    "event_time": row.event_time,
                    "side": "SELL",
                    "raw_price": str(raw_open),
                    "execution_price": str(execution_price),
                    "quantity": str(sold_quantity),
                    "fee": str(fee),
                    INTERPOLATED_COLUMN: bool(
                        getattr(row, INTERPOLATED_COLUMN, False)
                    ),
                }
            )
        close = _decimal(row.close)
        equity = cash + quantity * close
        equity_rows.append(
            {
                "event_time": row.event_time,
                "cash": str(cash),
                "quantity": str(quantity),
                "mark_price": str(close),
                "equity": str(equity),
                INTERPOLATED_COLUMN: bool(
                    getattr(row, INTERPOLATED_COLUMN, False)
                ),
            }
        )
    return pd.DataFrame(equity_rows), pd.DataFrame(trade_rows)


def run_long_short_backtest(
    frame: pd.DataFrame,
    cost_model: CostModel,
    initial_cash: Decimal = Decimal("1000"),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """long・short・flatの次日始値ポジションを決定論的に会計する。

    shortは初期現金と同額のnotionalを借りて売る合成モデルとし、売却代金を
    現金へ加え、mark-to-market時にshort数量を負債として差し引く。借入金利、
    funding、mark/index価格差、maintenance margin、liquidationは扱わないため、
    margin/futuresの約定証拠ではなく、価格方向と会計の研究用診断に限る。

    Args:
        frame: `desired_position`が`-1`（short）、`0`（flat）、`1`（long）のデータ。
        cost_model: 各entry、exit、coverに適用するfee、spread、slippageの仮定。
        initial_cash: 初期のpaper担保相当資金。

    Returns:
        `(equity_curve, trades)`の組。short entryは`SELL_SHORT`、買い戻しは
        `BUY_TO_COVER`として記録する。

    Raises:
        ValueError: 必須列不足、未知のposition、負の現金、または不正な価格の場合。
    """

    required = {"event_time", "open", "close", "desired_position"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing long/short backtest columns: {sorted(missing)}")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    cash = initial_cash
    long_quantity = Decimal("0")
    short_quantity = Decimal("0")
    equity_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []

    def close_long(row: object, raw_open: Decimal) -> None:
        """long数量を売却して現金化する。

        Args:
            row: 約定時刻と補間日フラグを持つ入力行。
            raw_open: 約定前の始値。
        """

        nonlocal cash, long_quantity
        execution_price = cost_model.sell_price(raw_open)
        sold_quantity = long_quantity
        gross = sold_quantity * execution_price
        fee = gross * cost_model.fee_rate
        cash += gross - fee
        long_quantity = Decimal("0")
        trade_rows.append(
            {
                "event_time": row.event_time,
                "side": "SELL",
                "raw_price": str(raw_open),
                "execution_price": str(execution_price),
                "quantity": str(sold_quantity),
                "fee": str(fee),
                INTERPOLATED_COLUMN: bool(getattr(row, INTERPOLATED_COLUMN, False)),
            }
        )

    def close_short(row: object, raw_open: Decimal) -> None:
        """short数量を買い戻して負債を解消する。

        Args:
            row: 約定時刻と補間日フラグを持つ入力行。
            raw_open: 約定前の始値。
        """

        nonlocal cash, short_quantity
        execution_price = cost_model.buy_price(raw_open)
        covered_quantity = short_quantity
        gross = covered_quantity * execution_price
        fee = gross * cost_model.fee_rate
        cash -= gross + fee
        short_quantity = Decimal("0")
        trade_rows.append(
            {
                "event_time": row.event_time,
                "side": "BUY_TO_COVER",
                "raw_price": str(raw_open),
                "execution_price": str(execution_price),
                "quantity": str(covered_quantity),
                "fee": str(fee),
                INTERPOLATED_COLUMN: bool(getattr(row, INTERPOLATED_COLUMN, False)),
            }
        )

    for row in frame.itertuples(index=False):
        desired = int(row.desired_position)
        if desired not in (-1, 0, 1):
            raise ValueError(f"unknown desired_position: {desired}")
        raw_open = _decimal(row.open)
        if raw_open <= 0:
            raise ValueError("open price must be positive")

        if desired != 1 and long_quantity > 0:
            close_long(row, raw_open)
        if desired != -1 and short_quantity > 0:
            close_short(row, raw_open)
        if desired == 1 and long_quantity == 0 and short_quantity == 0:
            execution_price = cost_model.buy_price(raw_open)
            fee = cash * cost_model.fee_rate
            long_quantity = (cash - fee) / execution_price
            cash = Decimal("0")
            trade_rows.append(
                {
                    "event_time": row.event_time,
                    "side": "BUY",
                    "raw_price": str(raw_open),
                    "execution_price": str(execution_price),
                    "quantity": str(long_quantity),
                    "fee": str(fee),
                    INTERPOLATED_COLUMN: bool(
                        getattr(row, INTERPOLATED_COLUMN, False)
                    ),
                }
            )
        elif desired == -1 and long_quantity == 0 and short_quantity == 0:
            execution_price = cost_model.sell_price(raw_open)
            short_quantity = cash / execution_price
            gross = short_quantity * execution_price
            fee = gross * cost_model.fee_rate
            cash += gross - fee
            trade_rows.append(
                {
                    "event_time": row.event_time,
                    "side": "SELL_SHORT",
                    "raw_price": str(raw_open),
                    "execution_price": str(execution_price),
                    "quantity": str(short_quantity),
                    "fee": str(fee),
                    INTERPOLATED_COLUMN: bool(
                        getattr(row, INTERPOLATED_COLUMN, False)
                    ),
                }
            )

        close = _decimal(row.close)
        equity = cash + long_quantity * close - short_quantity * close
        if equity < 0:
            raise ValueError("synthetic long/short equity became negative")
        equity_rows.append(
            {
                "event_time": row.event_time,
                "cash": str(cash),
                "long_quantity": str(long_quantity),
                "short_quantity": str(short_quantity),
                "mark_price": str(close),
                "equity": str(equity),
                INTERPOLATED_COLUMN: bool(getattr(row, INTERPOLATED_COLUMN, False)),
            }
        )
    return pd.DataFrame(equity_rows), pd.DataFrame(trade_rows)


def run_fractional_entry_backtest(
    frame: pd.DataFrame,
    cost_model: CostModel,
    initial_cash: Decimal = Decimal("1000"),
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """entry時に固定した0〜1の投資比率で現物ロングを会計する。

    `desired_exposure`が0から正へ変わる始値で、その時点の現金に投資比率を
    掛けた予算だけを使って買う。残額は現金で保持し、保有中の比率変更は
    許可しない。0へ戻る始値で全数量を売却する。

    Args:
        frame: `event_time`、`open`、`close`、`desired_exposure`列を含むデータ。
        cost_model: entryとexitへ適用するfee、spread、slippageの仮定。
        initial_cash: 初期のpaper現金残高。

    Returns:
        `(equity_curve, trades)`の組。BUYにはentry時の`target_exposure`を記録する。

    Raises:
        ValueError: 必須列不足、比率範囲外、保有中の比率変更、不正価格、または
            現金・資産が負になる場合。
    """

    required = {"event_time", "open", "close", "desired_exposure"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing fractional backtest columns: {sorted(missing)}")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    cash = initial_cash
    quantity = Decimal("0")
    active_exposure = Decimal("0")
    equity_rows: list[dict[str, object]] = []
    trade_rows: list[dict[str, object]] = []

    for row in frame.itertuples(index=False):
        desired = _decimal(row.desired_exposure)
        if not Decimal("0") <= desired <= Decimal("1"):
            raise ValueError(f"desired_exposure must be between 0 and 1: {desired}")
        raw_open = _decimal(row.open)
        if raw_open <= 0:
            raise ValueError("open price must be positive")

        if desired == 0 and quantity > 0:
            execution_price = cost_model.sell_price(raw_open)
            sold_quantity = quantity
            gross = sold_quantity * execution_price
            fee = gross * cost_model.fee_rate
            cash += gross - fee
            quantity = Decimal("0")
            active_exposure = Decimal("0")
            trade_rows.append(
                {
                    "event_time": row.event_time,
                    "side": "SELL",
                    "raw_price": str(raw_open),
                    "execution_price": str(execution_price),
                    "quantity": str(sold_quantity),
                    "fee": str(fee),
                    "target_exposure": "0",
                    INTERPOLATED_COLUMN: bool(
                        getattr(row, INTERPOLATED_COLUMN, False)
                    ),
                }
            )
        elif desired > 0 and quantity == 0:
            execution_price = cost_model.buy_price(raw_open)
            budget = cash * desired
            fee = budget * cost_model.fee_rate
            quantity = (budget - fee) / execution_price
            cash -= budget
            active_exposure = desired
            trade_rows.append(
                {
                    "event_time": row.event_time,
                    "side": "BUY",
                    "raw_price": str(raw_open),
                    "execution_price": str(execution_price),
                    "quantity": str(quantity),
                    "fee": str(fee),
                    "target_exposure": str(desired),
                    INTERPOLATED_COLUMN: bool(
                        getattr(row, INTERPOLATED_COLUMN, False)
                    ),
                }
            )
        elif desired > 0 and desired != active_exposure:
            raise ValueError("desired_exposure changed while position was open")

        close = _decimal(row.close)
        if close <= 0:
            raise ValueError("close price must be positive")
        equity = cash + quantity * close
        if cash < 0 or equity < 0:
            raise ValueError("fractional backtest balance became negative")
        equity_rows.append(
            {
                "event_time": row.event_time,
                "cash": str(cash),
                "quantity": str(quantity),
                "active_exposure": str(active_exposure),
                "mark_price": str(close),
                "equity": str(equity),
                INTERPOLATED_COLUMN: bool(
                    getattr(row, INTERPOLATED_COLUMN, False)
                ),
            }
        )
    return pd.DataFrame(equity_rows), pd.DataFrame(trade_rows)


def run_buy_and_hold(
    frame: pd.DataFrame,
    cost_model: CostModel,
    initial_cash: Decimal = Decimal("1000"),
) -> pd.DataFrame:
    """同一期間・同一費用モデルの現物買い持ち曲線を作る。

    Args:
        frame: `event_time`、`open`、`close`列を含む時系列データ。
        cost_model: 買い付け時のfee、spread、slippageの仮定。
        initial_cash: 初期のpaper現金残高。

    Returns:
        初バー始値で買い、各バー終値で評価した損益曲線。

    Raises:
        ValueError: 入力が空、必須列が不足する、または初期資金が正でない場合。
    """

    required = {"event_time", "open", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing buy-and-hold columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("buy-and-hold frame must not be empty")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")
    first_open = _decimal(frame.iloc[0]["open"])
    if first_open <= 0:
        raise ValueError("open price must be positive")
    fee = initial_cash * cost_model.fee_rate
    quantity = (initial_cash - fee) / cost_model.buy_price(first_open)
    rows: list[dict[str, object]] = []
    for row in frame.itertuples(index=False):
        close = _decimal(row.close)
        if close <= 0:
            raise ValueError("close price must be positive")
        rows.append(
            {
                "event_time": row.event_time,
                "cash": "0",
                "quantity": str(quantity),
                "mark_price": str(close),
                "equity": str(quantity * close),
                INTERPOLATED_COLUMN: bool(
                    getattr(row, INTERPOLATED_COLUMN, False)
                ),
            }
        )
    return pd.DataFrame(rows)


def summarize_equity(equity_curve: pd.DataFrame) -> dict[str, float | int | None]:
    """損益曲線から基本的な研究指標を計算する。

    Args:
        equity_curve: `run_backtest`が返す損益曲線。

    Returns:
        最終資産、CAGR、最大ドローダウン、観測行数を含む指標。
    """

    if equity_curve.empty:
        return {"rows": 0, "final_equity": None, "cagr": None, "max_drawdown": None}
    values = pd.to_numeric(equity_curve["equity"])
    running_max = values.cummax()
    drawdown = values / running_max - 1
    start = values.iloc[0]
    end = values.iloc[-1]
    elapsed_days = (
        equity_curve["event_time"].iloc[-1] - equity_curve["event_time"].iloc[0]
    ).total_seconds() / 86_400
    cagr = None
    if start > 0 and elapsed_days > 0:
        cagr = float((end / start) ** (365.25 / elapsed_days) - 1)
    return {
        "rows": int(len(equity_curve)),
        "final_equity": float(end),
        "cagr": cagr,
        "max_drawdown": float(drawdown.min()),
    }
