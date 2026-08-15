#!/usr/bin/env python3
"""EXP-2026-0015用のZOOMEX無期限データを取得する。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pandas as pd


BASE_URL = "https://openapi.zoomex.com"
CATEGORY = "linear"
INTERVAL = "120"
SYMBOLS = ("LINKUSDT", "UNIUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT", "AAVEUSDT")
DATA_START = pd.Timestamp("2021-01-01T00:00:00Z")
DATA_END = pd.Timestamp("2026-01-01T00:00:00Z")
EVALUATION_START = pd.Timestamp("2022-02-01T00:00:00Z")
BAR_DELTA = pd.Timedelta(hours=2)
MIN_WARMUP_BARS = 400
PAGE_LIMIT = 1000
FUNDING_PAGE_LIMIT = 200
REQUEST_DELAY_SECONDS = 0.02

PRICE_ENDPOINTS = {
    "trade": "/cloud/trade/v3/market/kline",
    "mark_price": "/cloud/trade/v3/market/mark-price-kline",
    "index_price": "/cloud/trade/v3/market/index-price-kline",
}


def _get_json(path: str, parameters: dict[str, object]) -> dict[str, object]:
    """公開ZOOMEX REST endpointからJSONを取得する。

    Args:
        path: API path。
        parameters: query parameters。

    Returns:
        成功したJSON payload。

    Raises:
        ValueError: APIがエラーを返した場合。
        OSError: HTTP通信に失敗した場合。
    """

    query = urlencode(parameters)
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            with urlopen(  # noqa: S310
                f"{BASE_URL}{path}?{query}", timeout=30
            ) as response:
                payload = json.loads(response.read())
            if payload.get("retCode") != 0:
                message = str(payload)
                if "Too many" not in message and "frequent" not in message:
                    raise ValueError(f"ZOOMEX API error: {path}: {payload}")
                raise RuntimeError(f"ZOOMEX rate limit response: {path}: {payload}")
            time.sleep(REQUEST_DELAY_SECONDS)
            return payload
        except (HTTPError, URLError, TimeoutError, RuntimeError) as error:
            last_error = error
            if attempt == 3:
                raise
            time.sleep(2**attempt)
    raise RuntimeError(f"ZOOMEX request failed: {path}") from last_error


def _timestamp_ms(timestamp: pd.Timestamp) -> int:
    """UTC Timestampをミリ秒Unix時刻へ変換する。

    Args:
        timestamp: UTCの時刻。

    Returns:
        ミリ秒Unix時刻。
    """

    return int(timestamp.timestamp() * 1000)


def _fetch_backwards(
    path: str,
    symbol: str,
    limit: int,
    parameter_names: tuple[str, str],
    timestamp_key: int | str,
    extra_parameters: dict[str, object] | None = None,
) -> list[object]:
    """期間末から期間初へページングして時系列配列を取得する。

    Args:
        path: API path。
        symbol: ZOOMEX symbol。
        limit: 1ページの取得件数。
        parameter_names: startとendに対応するAPIパラメータ名。
        timestamp_key: 配列の時刻列番号、またはオブジェクトの時刻キー。
        extra_parameters: categoryなど追加パラメータ。

    Returns:
        APIが返した配列の連結結果。

    Raises:
        ValueError: ページングが前進しない場合。
    """

    start_name, end_name = parameter_names
    start_ms = _timestamp_ms(DATA_START)
    cursor_end = _timestamp_ms(DATA_END) - 1
    rows: list[object] = []
    while cursor_end > start_ms:
        parameters: dict[str, object] = {
            "category": CATEGORY,
            "symbol": symbol,
            start_name: start_ms,
            end_name: cursor_end,
            "limit": limit,
        }
        if extra_parameters:
            parameters.update(extra_parameters)
        payload = _get_json(path, parameters)
        result = payload.get("result", {})
        page = result.get("list", []) if isinstance(result, dict) else []
        if not page:
            break
        rows.extend(page)
        timestamps = [
            int(row[timestamp_key]) if isinstance(timestamp_key, str) else int(row[timestamp_key])
            for row in page
        ]
        oldest = min(timestamps)
        next_end = oldest - 1
        if next_end >= cursor_end:
            raise ValueError(f"pagination did not move backwards: {path} {symbol}")
        cursor_end = next_end
        if oldest <= start_ms:
            break
    return rows


def _normalize_price_rows(
    rows: list[list[str]],
    symbol: str,
    source: str,
) -> pd.DataFrame:
    """価格Kline配列を昇順の検証済みDataFrameへ正規化する。

    Args:
        rows: ZOOMEX Kline配列。
        symbol: ZOOMEX symbol。
        source: `trade`、`mark_price`、または`index_price`。

    Returns:
        event_timeとOHLC、必要ならvolume・turnoverを持つDataFrame。

    Raises:
        ValueError: 空、重複、内部gap、warm-up不足、または範囲外の場合。
    """

    if not rows:
        raise ValueError(f"no {source} rows returned: {symbol}")
    frame = pd.DataFrame(
        {
            "event_time": pd.to_datetime(
                [int(row[0]) for row in rows], unit="ms", utc=True
            ),
            "open": [row[1] for row in rows],
            "high": [row[2] for row in rows],
            "low": [row[3] for row in rows],
            "close": [row[4] for row in rows],
        }
    )
    if source == "trade":
        frame["volume"] = [row[5] for row in rows]
        frame["turnover"] = [row[6] for row in rows]
    if frame["event_time"].duplicated().any():
        raise ValueError(f"duplicate {source} rows: {symbol}")
    frame = frame.sort_values("event_time").reset_index(drop=True)
    if frame.iloc[0]["event_time"] > EVALUATION_START - MIN_WARMUP_BARS * BAR_DELTA:
        raise ValueError(f"insufficient 400-bar warm-up: {source} {symbol}")
    if frame.iloc[-1]["event_time"] != DATA_END - BAR_DELTA:
        raise ValueError(f"data does not reach frozen end: {source} {symbol}")
    expected = pd.date_range(frame.iloc[0]["event_time"], frame.iloc[-1]["event_time"], freq=BAR_DELTA)
    if list(frame["event_time"]) != list(expected):
        raise ValueError(f"2-hour gap in {source} data: {symbol}")
    for column in frame.columns:
        if column != "event_time":
            frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["is_interpolated"] = False
    return frame


def fetch_price_series(symbol: str, source: str) -> pd.DataFrame:
    """指定銘柄の価格系列をZOOMEXから取得する。

    Args:
        symbol: ZOOMEX linear symbol。
        source: `trade`、`mark_price`、または`index_price`。

    Returns:
        2時間足の検証済み価格DataFrame。

    Raises:
        ValueError: sourceが不正、またはデータ品質検査に失敗した場合。
    """

    if source not in PRICE_ENDPOINTS:
        raise ValueError(f"unknown price source: {source}")
    rows = _fetch_backwards(
        PRICE_ENDPOINTS[source],
        symbol,
        PAGE_LIMIT,
        ("start", "end"),
        0,
        {"interval": INTERVAL},
    )
    return _normalize_price_rows(rows, symbol, source)


def _align_price_series(
    frames: dict[str, pd.DataFrame], symbol: str
) -> dict[str, pd.DataFrame]:
    """trade・mark・index価格を共通タイムスタンプへ揃える。

    Args:
        frames: 価格系列をキーにしたDataFrame辞書。
        symbol: ZOOMEX symbol。

    Returns:
        共通区間だけを残したDataFrame辞書。

    Raises:
        ValueError: 共通区間が空、または共通区間にgapがある場合。
    """

    if set(frames) != set(PRICE_ENDPOINTS):
        raise ValueError(f"incomplete price sources: {symbol}: {sorted(frames)}")
    common_times = set.intersection(
        *(set(frame["event_time"]) for frame in frames.values())
    )
    if not common_times:
        raise ValueError(f"price sources have no common timestamps: {symbol}")
    common_start = max(frame.iloc[0]["event_time"] for frame in frames.values())
    expected = pd.date_range(common_start, DATA_END - BAR_DELTA, freq=BAR_DELTA)
    if common_times != set(expected):
        raise ValueError(f"price sources are not aligned continuously: {symbol}")
    return {
        source: frame[frame["event_time"].isin(common_times)].reset_index(drop=True)
        for source, frame in frames.items()
    }


def _normalize_funding_rows(rows: list[dict[str, str]], symbol: str) -> pd.DataFrame:
    """Funding履歴を昇順の検証済みDataFrameへ正規化する。

    Args:
        rows: Funding APIのオブジェクト配列。
        symbol: ZOOMEX symbol。

    Returns:
        event_time、funding_rate、symbolを持つDataFrame。

    Raises:
        ValueError: 空、重複、範囲外、または非数値Fundingの場合。
    """

    if not rows:
        raise ValueError(f"no funding rows returned: {symbol}")
    frame = pd.DataFrame(
        {
            "event_time": pd.to_datetime(
                [int(row["fundingRateTimestamp"]) for row in rows], unit="ms", utc=True
            ),
            "funding_rate": [row["fundingRate"] for row in rows],
            "symbol": symbol,
        }
    )
    if frame["event_time"].duplicated().any():
        raise ValueError(f"duplicate funding rows: {symbol}")
    frame = frame.sort_values("event_time").reset_index(drop=True)
    if frame.iloc[0]["event_time"] > EVALUATION_START - MIN_WARMUP_BARS * BAR_DELTA:
        raise ValueError(f"insufficient funding warm-up: {symbol}")
    if frame.iloc[-1]["event_time"] < DATA_END - pd.Timedelta(days=2):
        raise ValueError(f"funding data does not reach frozen end: {symbol}")
    frame["funding_rate"] = pd.to_numeric(frame["funding_rate"], errors="raise")
    return frame


def fetch_funding_history(symbol: str) -> pd.DataFrame:
    """指定銘柄のFunding履歴をZOOMEXから取得する。

    Args:
        symbol: ZOOMEX linear symbol。

    Returns:
        Funding履歴DataFrame。
    """

    rows = _fetch_backwards(
        "/cloud/trade/v3/market/funding/history",
        symbol,
        FUNDING_PAGE_LIMIT,
        ("startTime", "endTime"),
        "fundingRateTimestamp",
    )
    return _normalize_funding_rows(rows, symbol)


def _sha256(path: Path) -> str:
    """ファイルSHA-256を返す。

    Args:
        path: 対象ファイル。

    Returns:
        小文字16進SHA-256。
    """

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_frame(frame: pd.DataFrame, path: Path) -> dict[str, object]:
    """DataFrameをCSV保存し、取得メタデータを返す。

    Args:
        frame: 保存対象DataFrame。
        path: 出力CSVパス。

    Returns:
        行数・端点・SHA-256を含むメタデータ。
    """

    frame.to_csv(path, index=False)
    return {
        "path": str(path),
        "rows": len(frame),
        "start_utc": frame.iloc[0]["event_time"].isoformat(),
        "end_utc": frame.iloc[-1]["event_time"].isoformat(),
        "sha256": _sha256(path),
    }


def _find_symbol(records: list[dict[str, object]], symbol: str) -> dict[str, object]:
    """APIの銘柄一覧から指定symbolを取得する。

    Args:
        records: instruments-infoの銘柄配列。
        symbol: 検索するsymbol。

    Returns:
        指定銘柄の仕様。

    Raises:
        ValueError: 銘柄が見つからない、またはTradingでない場合。
    """

    for record in records:
        if record.get("symbol") == symbol:
            if record.get("status") != "Trading":
                raise ValueError(f"symbol is not Trading: {symbol}")
            return record
    raise ValueError(f"symbol not found in instruments-info: {symbol}")


def main() -> None:
    """6銘柄の価格・Funding・銘柄仕様を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument("--metadata", type=Path, default=Path("var/exp-2026-0015-data.json"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    instruments_payload = _get_json(
        "/cloud/trade/v3/market/instruments-info", {"category": CATEGORY, "limit": 1000}
    )
    instrument_records = instruments_payload.get("result", {}).get("list", [])
    ticker_payload = _get_json(
        "/cloud/trade/v3/market/tickers", {"category": CATEGORY}
    )
    ticker_records = ticker_payload.get("result", {}).get("list", [])
    ticker_by_symbol = {record["symbol"]: record for record in ticker_records}

    symbols_metadata: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        symbol_dir = args.output_dir / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        instrument = _find_symbol(instrument_records, symbol)
        if symbol not in ticker_by_symbol:
            raise ValueError(f"symbol missing from ticker snapshot: {symbol}")
        artifacts: dict[str, object] = {}
        price_frames = {
            source: fetch_price_series(symbol, source) for source in PRICE_ENDPOINTS
        }
        for source, frame in _align_price_series(price_frames, symbol).items():
            artifacts[source] = _write_frame(
                frame,
                symbol_dir / f"{source.replace('_', '-')}-2h.csv",
            )
        artifacts["funding"] = _write_frame(
            fetch_funding_history(symbol), symbol_dir / "funding-rate.csv"
        )
        symbols_metadata.append(
            {
                "symbol": symbol,
                "instrument": instrument,
                "ticker_snapshot": ticker_by_symbol[symbol],
                "artifacts": artifacts,
            }
        )

    metadata = {
        "experiment_id": "EXP-2026-0015",
        "source": "ZOOMEX Global public V3 REST API",
        "base_url": BASE_URL,
        "category": CATEGORY,
        "interval": INTERVAL,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_start": DATA_START.isoformat(),
        "data_end_exclusive": DATA_END.isoformat(),
        "evaluation_start": EVALUATION_START.isoformat(),
        "authentication_used": False,
        "symbols": symbols_metadata,
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
