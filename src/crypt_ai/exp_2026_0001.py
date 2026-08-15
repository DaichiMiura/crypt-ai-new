"""EXP-2026-0001のデータ整形、シグナル、決定論的会計を提供する。"""

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
    expanded[INTERPOLATED_COLUMN] = synthetic
    return expanded.reset_index()


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
