#!/usr/bin/env python3
"""EXP-2026-0032用のZOOMEX現物2時間足を取得する。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd


BASE_URL = "https://openapi.zoomex.com"
CATEGORY = "spot"
INTERVAL = "120"
# ベーシスには現物とlinear先物の両方が必要なため、公開現物Klineを取得できる銘柄だけを固定する。
SYMBOLS = ("LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT")
DATA_START = pd.Timestamp("2021-01-01T00:00:00Z")
DATA_END = pd.Timestamp("2026-01-01T00:00:00Z")
EVALUATION_START = pd.Timestamp("2022-02-01T00:00:00Z")
BAR_DELTA = pd.Timedelta(hours=2)
MIN_WARMUP_BARS = 400
PAGE_LIMIT = 1000
REQUEST_DELAY_SECONDS = 0.02
KLINE_ENDPOINT = "/cloud/trade/v3/market/kline"


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
            with urlopen(f"{BASE_URL}{path}?{query}", timeout=30) as response:  # noqa: S310
                payload = json.loads(response.read())
            if payload.get("retCode") != 0:
                message = str(payload)
                if "Too many" not in message and "frequent" not in message:
                    raise ValueError(f"ZOOMEX API error: {path}: {payload}")
                raise RuntimeError(f"ZOOMEX rate limit response: {path}: {payload}")
            time.sleep(REQUEST_DELAY_SECONDS)
            return payload
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            if error.code == 400:
                raise ValueError(
                    f"ZOOMEX HTTP 400: {path}: {body[:500]}"
                ) from error
            last_error = error
            if attempt == 3:
                raise
            time.sleep(2**attempt)
        except (URLError, TimeoutError, RuntimeError) as error:
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


def _fetch_backwards(symbol: str) -> list[list[str]]:
    """現物Klineを期間末から期間初へページングして取得する。

    Args:
        symbol: ZOOMEX現物symbol。

    Returns:
        APIが返したKline配列の連結結果。

    Raises:
        ValueError: ページングが前進しない場合。
    """

    start_ms = _timestamp_ms(DATA_START)
    cursor_end = _timestamp_ms(DATA_END) - 1
    rows: list[list[str]] = []
    while cursor_end > start_ms:
        payload = _get_json(
            KLINE_ENDPOINT,
            {
                "category": CATEGORY,
                "symbol": symbol,
                "interval": INTERVAL,
                "start": start_ms,
                "end": cursor_end,
                "limit": PAGE_LIMIT,
            },
        )
        result = payload.get("result", {})
        page = result.get("list", []) if isinstance(result, dict) else []
        if not page:
            break
        rows.extend(page)
        oldest = min(int(row[0]) for row in page)
        next_end = oldest - 1
        if next_end >= cursor_end:
            raise ValueError(f"pagination did not move backwards: {symbol}")
        cursor_end = next_end
        if oldest <= start_ms:
            break
    return rows


def _normalize_rows(rows: list[list[str]], symbol: str) -> pd.DataFrame:
    """現物Kline配列を昇順の品質検証済みDataFrameへ正規化する。

    Args:
        rows: ZOOMEX spot Kline配列。
        symbol: ZOOMEX symbol。

    Returns:
        event_time、OHLCV、turnover、補間フラグを持つDataFrame。

    Raises:
        ValueError: 空、重複、内部gap、ウォームアップ不足、終端不足、または非数値の場合。
    """

    if not rows:
        raise ValueError(f"no spot rows returned: {symbol}")
    frame = pd.DataFrame(
        {
            "event_time": pd.to_datetime(
                [int(row[0]) for row in rows], unit="ms", utc=True
            ),
            "open": [row[1] for row in rows],
            "high": [row[2] for row in rows],
            "low": [row[3] for row in rows],
            "close": [row[4] for row in rows],
            "volume": [row[5] for row in rows],
            "turnover": [row[6] for row in rows],
        }
    )
    if frame["event_time"].duplicated().any():
        raise ValueError(f"duplicate spot rows: {symbol}")
    frame = frame.sort_values("event_time").reset_index(drop=True)
    warmup_start = EVALUATION_START - MIN_WARMUP_BARS * BAR_DELTA
    if frame.iloc[0]["event_time"] > warmup_start:
        raise ValueError(f"insufficient spot warm-up: {symbol}")
    if frame.iloc[-1]["event_time"] != DATA_END - BAR_DELTA:
        raise ValueError(f"spot data does not reach frozen end: {symbol}")
    expected = pd.date_range(
        frame.iloc[0]["event_time"], frame.iloc[-1]["event_time"], freq=BAR_DELTA
    )
    if list(frame["event_time"]) != list(expected):
        raise ValueError(f"2-hour gap in spot data: {symbol}")
    for column in frame.columns:
        if column != "event_time":
            frame[column] = pd.to_numeric(frame[column], errors="raise")
            if frame[column].isna().any() or not frame[column].map(pd.notna).all():
                raise ValueError(f"invalid spot {column}: {symbol}")
    if not frame[["open", "high", "low", "close"]].gt(0).all().all():
        raise ValueError(f"spot prices must be positive: {symbol}")
    frame["is_interpolated"] = False
    return frame


def fetch_spot_series(symbol: str) -> pd.DataFrame:
    """指定銘柄の現物2時間足をZOOMEXから取得する。

    Args:
        symbol: ZOOMEX現物symbol。

    Returns:
        品質検証済みの現物OHLCV DataFrame。
    """

    return _normalize_rows(_fetch_backwards(symbol), symbol)


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


def main() -> None:
    """6銘柄の現物価格と取得条件を保存する。

    Raises:
        ValueError: いずれかの銘柄で現物データ品質検査に失敗した場合。
    """

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/processed/EXP-2026-0032")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0032-spot-data.json")
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        symbol_dir = args.output_dir / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        frame = fetch_spot_series(symbol)
        records.append(
            {
                "symbol": symbol,
                "artifact": _write_frame(frame, symbol_dir / "spot-2h.csv"),
            }
        )
    metadata = {
        "experiment_id": "EXP-2026-0032",
        "source": "ZOOMEX Global public V3 REST API",
        "base_url": BASE_URL,
        "category": CATEGORY,
        "interval": INTERVAL,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_start_requested": DATA_START.isoformat(),
        "data_end_exclusive": DATA_END.isoformat(),
        "evaluation_start": EVALUATION_START.isoformat(),
        "authentication_used": False,
        "symbols": records,
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
