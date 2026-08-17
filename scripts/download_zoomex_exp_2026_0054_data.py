#!/usr/bin/env python3
"""EXP-2026-0054用ZOOMEX 1時間足をholdout非表示で取得する。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path

import pandas as pd

from scripts.download_zoomex_exp_2026_0015_data import (
    BASE_URL,
    CATEGORY,
    FUNDING_PAGE_LIMIT,
    PAGE_LIMIT,
    _find_symbol,
    _get_json,
    _sha256,
)


SNAPSHOT_ID = "DATA-2026-0006"
EXPERIMENT_ID = "EXP-2026-0054"
INTERVAL = "60"
SYMBOLS = ("BTCUSDT", "LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT")
TRADED_SYMBOLS = ("LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT")
CONTEXT_SYMBOLS = ("BTCUSDT",)
DATA_START = pd.Timestamp("2021-01-01T00:00:00Z")
DATA_END = pd.Timestamp("2026-08-01T00:00:00Z")
DEVELOPMENT_END = pd.Timestamp("2026-01-01T00:00:00Z")
SEALED_HOLDOUT_START = DEVELOPMENT_END
SEALED_HOLDOUT_END = DATA_END
EVALUATION_START = pd.Timestamp("2022-02-01T00:00:00Z")
BAR_DELTA = pd.Timedelta(hours=1)
MIN_WARMUP_BARS = 720
PRICE_ENDPOINTS = {
    "trade": "/cloud/trade/v3/market/kline",
    "mark_price": "/cloud/trade/v3/market/mark-price-kline",
    "index_price": "/cloud/trade/v3/market/index-price-kline",
    "premium_index": "/cloud/trade/v3/market/premium-index-price-kline",
}


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
    """封印終了から開始へ公開APIを逆ページングする。

    Args:
        path: ZOOMEX API path。
        symbol: ZOOMEX linear symbol。
        limit: 1ページの取得件数。
        parameter_names: startとendのAPI parameter名。
        timestamp_key: 配列indexまたはobject keyの時刻位置。
        extra_parameters: intervalなど追加parameter。

    Returns:
        API配列を連結した未加工行。

    Raises:
        ValueError: ページング時刻が過去へ進まない場合。
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
            int(row[timestamp_key])
            if isinstance(timestamp_key, str)
            else int(row[timestamp_key])
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


def _missing_metadata(times: pd.Series) -> dict[str, object]:
    """観測時刻間の未補間gapを機械可読に集計する。

    Args:
        times: 昇順・重複なしのUTC event time。

    Returns:
        欠測行数、segment数、各segment端点。
    """

    missing_segments: list[dict[str, object]] = []
    missing_count = 0
    for previous, current in zip(times.iloc[:-1], times.iloc[1:], strict=True):
        delta = current - previous
        if delta <= BAR_DELTA:
            continue
        count = int(delta / BAR_DELTA) - 1
        missing_count += count
        missing_segments.append(
            {
                "start_utc": (previous + BAR_DELTA).isoformat(),
                "end_utc": (current - BAR_DELTA).isoformat(),
                "missing_rows": count,
            }
        )
    return {
        "missing_row_count": missing_count,
        "missing_segment_count": len(missing_segments),
        "missing_segments": missing_segments,
    }


def _normalize_price_rows(
    rows: list[list[str]], symbol: str, source: str
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Klineを値変更なしで正規化し、欠測を記録する。

    Args:
        rows: ZOOMEX Kline配列。
        symbol: ZOOMEX linear symbol。
        source: trade、mark、index、premiumの系列名。

    Returns:
        観測行だけのDataFrameと欠測metadata。

    Raises:
        ValueError: 空、未知source、重複、範囲、時刻、価格が不正な場合。
    """

    if source not in PRICE_ENDPOINTS:
        raise ValueError(f"unknown price source: {source}")
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
    duplicate_count = int(frame["event_time"].duplicated().sum())
    if duplicate_count:
        raise ValueError(f"duplicate {source} rows: {symbol}")
    frame = frame.sort_values("event_time").reset_index(drop=True)
    if any(timestamp.minute or timestamp.second for timestamp in frame["event_time"]):
        raise ValueError(f"unaligned hourly timestamp: {source} {symbol}")
    if frame.iloc[0]["event_time"] > EVALUATION_START - MIN_WARMUP_BARS * BAR_DELTA:
        raise ValueError(f"insufficient hourly warm-up: {source} {symbol}")
    if frame.iloc[-1]["event_time"] != DATA_END - BAR_DELTA:
        raise ValueError(f"data does not reach sealed end: {source} {symbol}")
    for column in frame.columns:
        if column == "event_time":
            continue
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not frame[column].map(math.isfinite).all():
            raise ValueError(f"non-finite {source}.{column}: {symbol}")
    for column in ("open", "high", "low", "close"):
        if source != "premium_index" and not (frame[column] > 0).all():
            raise ValueError(f"non-positive {source}.{column}: {symbol}")
    if source == "trade" and (
        (frame["volume"] < 0).any() or (frame["turnover"] < 0).any()
    ):
        raise ValueError(f"negative trade activity: {symbol}")
    frame["is_interpolated"] = False
    quality = {
        "duplicate_count": duplicate_count,
        "interpolated_row_count": 0,
        **_missing_metadata(frame["event_time"]),
    }
    return frame, quality


def fetch_price_series(
    symbol: str, source: str
) -> tuple[pd.DataFrame, dict[str, object]]:
    """指定symbol・sourceの1時間Klineを取得する。

    Args:
        symbol: ZOOMEX linear symbol。
        source: `PRICE_ENDPOINTS`の系列名。

    Returns:
        正規化DataFrameと品質metadata。
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


def _normalize_funding_rows(
    rows: list[dict[str, str]], symbol: str
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Fundingを値変更なしで正規化する。

    Args:
        rows: Funding API object配列。
        symbol: ZOOMEX linear symbol。

    Returns:
        Funding DataFrameと重複metadata。

    Raises:
        ValueError: 空、重複、範囲、または数値が不正な場合。
    """

    if not rows:
        raise ValueError(f"no funding rows returned: {symbol}")
    frame = pd.DataFrame(
        {
            "event_time": pd.to_datetime(
                [int(row["fundingRateTimestamp"]) for row in rows],
                unit="ms",
                utc=True,
            ),
            "funding_rate": [row["fundingRate"] for row in rows],
            "symbol": symbol,
        }
    )
    duplicate_count = int(frame["event_time"].duplicated().sum())
    if duplicate_count:
        raise ValueError(f"duplicate funding rows: {symbol}")
    frame = frame.sort_values("event_time").reset_index(drop=True)
    if frame.iloc[0]["event_time"] > EVALUATION_START - pd.Timedelta(days=30):
        raise ValueError(f"insufficient funding warm-up: {symbol}")
    if frame.iloc[-1]["event_time"] < DATA_END - pd.Timedelta(days=2):
        raise ValueError(f"funding data does not reach sealed end: {symbol}")
    frame["funding_rate"] = pd.to_numeric(frame["funding_rate"], errors="raise")
    if not frame["funding_rate"].map(math.isfinite).all():
        raise ValueError(f"non-finite funding rate: {symbol}")
    return frame, {"duplicate_count": duplicate_count}


def fetch_funding_history(
    symbol: str,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """指定symbolのFunding履歴を取得する。

    Args:
        symbol: ZOOMEX linear symbol。

    Returns:
        正規化Fundingと品質metadata。
    """

    rows = _fetch_backwards(
        "/cloud/trade/v3/market/funding/history",
        symbol,
        FUNDING_PAGE_LIMIT,
        ("startTime", "endTime"),
        "fundingRateTimestamp",
    )
    return _normalize_funding_rows(rows, symbol)


def _write_frame(
    frame: pd.DataFrame, path: Path, quality: dict[str, object]
) -> dict[str, object]:
    """観測DataFrameを保存して非価格metadataを返す。

    Args:
        frame: 保存対象DataFrame。
        path: 出力CSV。
        quality: 欠測・重複metadata。

    Returns:
        path、行数、端点、hash、品質情報。
    """

    frame.to_csv(path, index=False)
    return {
        "path": str(path),
        "rows": len(frame),
        "start_utc": frame.iloc[0]["event_time"].isoformat(),
        "end_utc": frame.iloc[-1]["event_time"].isoformat(),
        "sha256": _sha256(path),
        **quality,
    }


def _public_summary(metadata: dict[str, object]) -> dict[str, object]:
    """stdout用に価格・銘柄仕様を含まない取得要約を作る。

    Args:
        metadata: 保存用の完全metadata。

    Returns:
        snapshot、封印期間、artifact品質だけを含む要約。
    """

    symbols = {}
    for symbol_record in metadata["symbols"]:
        symbols[str(symbol_record["symbol"])] = {
            name: {
                key: artifact[key]
                for key in (
                    "rows",
                    "start_utc",
                    "end_utc",
                    "sha256",
                    "duplicate_count",
                    "missing_row_count",
                    "missing_segment_count",
                    "interpolated_row_count",
                )
                if key in artifact
            }
            for name, artifact in symbol_record["artifacts"].items()
        }
    return {
        "snapshot_id": metadata["snapshot_id"],
        "status": "SEALED_DATA_ACQUIRED",
        "holdout_content_opened": False,
        "data_end_exclusive": metadata["data_end_exclusive"],
        "sealed_holdout_start": metadata["sealed_holdout"]["start_utc"],
        "sealed_holdout_end_exclusive": metadata["sealed_holdout"]["end_utc_exclusive"],
        "symbols": symbols,
    }


def main() -> None:
    """5銘柄の1時間系列を取得しholdout非表示metadataを保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/processed/EXP-2026-0054")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0054-data.json")
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    instruments_payload = _get_json(
        "/cloud/trade/v3/market/instruments-info",
        {"category": CATEGORY, "limit": 1000},
    )
    instrument_records = instruments_payload.get("result", {}).get("list", [])

    symbols_metadata: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        symbol_dir = args.output_dir / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        instrument = _find_symbol(instrument_records, symbol)
        artifacts: dict[str, object] = {}
        for source in PRICE_ENDPOINTS:
            frame, quality = fetch_price_series(symbol, source)
            artifacts[source] = _write_frame(
                frame,
                symbol_dir / f"{source.replace('_', '-')}-1h.csv",
                quality,
            )
        funding, funding_quality = fetch_funding_history(symbol)
        artifacts["funding"] = _write_frame(
            funding, symbol_dir / "funding-rate.csv", funding_quality
        )
        symbols_metadata.append(
            {"symbol": symbol, "instrument": instrument, "artifacts": artifacts}
        )

    metadata = {
        "schema_version": 1,
        "snapshot_id": SNAPSHOT_ID,
        "experiment_id": EXPERIMENT_ID,
        "source": "ZOOMEX Global public V3 REST API",
        "base_url": BASE_URL,
        "category": CATEGORY,
        "interval": INTERVAL,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_start": DATA_START.isoformat(),
        "data_end_exclusive": DATA_END.isoformat(),
        "development_end_exclusive": DEVELOPMENT_END.isoformat(),
        "sealed_holdout": {
            "start_utc": SEALED_HOLDOUT_START.isoformat(),
            "end_utc_exclusive": SEALED_HOLDOUT_END.isoformat(),
            "content_opened": False,
        },
        "authentication_used": False,
        "traded_symbols": TRADED_SYMBOLS,
        "context_symbols": CONTEXT_SYMBOLS,
        "price_endpoints": PRICE_ENDPOINTS,
        "funding_endpoint": "/cloud/trade/v3/market/funding/history",
        "symbols": symbols_metadata,
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(_public_summary(metadata), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
