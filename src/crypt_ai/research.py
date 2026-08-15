"""実験で共有するKline整形、シグナル、決定論的会計を提供する。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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
