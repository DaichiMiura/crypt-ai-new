#!/usr/bin/env python3
"""EXP-2026-0058の15分premium indexを封印取得する。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from scripts.download_zoomex_exp_2026_0015_data import (  # noqa: E402
    BASE_URL,
    CATEGORY,
    PAGE_LIMIT,
    _get_json,
)
from scripts.download_zoomex_exp_2026_0054_data import _write_frame  # noqa: E402
from scripts.download_zoomex_exp_2026_0056_data import (  # noqa: E402
    ALL_SYMBOLS,
    DATA_END,
    DATA_START,
    SEALED_TARGET_SYMBOLS,
    SOURCE_SYMBOLS,
    _missing_metadata,
    _timestamp_ms,
)


SNAPSHOT_ID = "DATA-2026-0009"
EXPERIMENT_ID = "EXP-2026-0058"
ENDPOINT = "/cloud/trade/v3/market/premium-index-price-kline"
INTERVAL = "15"
BAR_DELTA = pd.Timedelta(minutes=15)
TARGET_START = pd.Timestamp("2026-01-01T00:00:00Z")


def fetch_premium_rows(symbol: str) -> list[list[str]]:
    """指定銘柄の15分premium index Klineを逆ページングする。

    Args:
        symbol: ZOOMEX linear symbol。

    Returns:
        API未加工premium Kline行。

    Raises:
        ValueError: ページングが過去へ進まない場合。
    """

    start_ms = _timestamp_ms(DATA_START)
    cursor_end = _timestamp_ms(DATA_END) - 1
    rows: list[list[str]] = []
    while cursor_end > start_ms:
        payload = _get_json(ENDPOINT, {
            "category": CATEGORY,
            "symbol": symbol,
            "interval": INTERVAL,
            "start": start_ms,
            "end": cursor_end,
            "limit": PAGE_LIMIT,
        })
        page = payload.get("result", {}).get("list", [])
        if not page:
            break
        rows.extend(page)
        oldest = min(int(row[0]) for row in page)
        next_end = oldest - 1
        if next_end >= cursor_end:
            raise ValueError(f"premium pagination did not move backwards: {symbol}")
        cursor_end = next_end
        if oldest <= start_ms:
            break
    return rows


def normalize_premium_rows(
    rows: list[list[str]], symbol: str,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """premium index Klineを値変更なしで正規化する。

    Args:
        rows: API premium Kline行。
        symbol: ZOOMEX linear symbol。

    Returns:
        観測DataFrameと品質metadata。

    Raises:
        ValueError: 空、重複、期間、整列、数値が不正な場合。
    """

    if not rows:
        raise ValueError(f"no premium rows returned: {symbol}")
    if any(len(row) < 5 for row in rows):
        raise ValueError(f"short premium row: {symbol}")
    frame = pd.DataFrame({
        "event_time": pd.to_datetime([int(row[0]) for row in rows], unit="ms", utc=True),
        "open": [row[1] for row in rows],
        "high": [row[2] for row in rows],
        "low": [row[3] for row in rows],
        "close": [row[4] for row in rows],
    })
    duplicate_count = int(frame["event_time"].duplicated().sum())
    if duplicate_count:
        raise ValueError(f"duplicate premium rows: {symbol}")
    frame = frame.sort_values("event_time").reset_index(drop=True)
    if any(timestamp.minute not in {0, 15, 30, 45} or timestamp.second for timestamp in frame["event_time"]):
        raise ValueError(f"unaligned premium timestamp: {symbol}")
    if frame.iloc[0]["event_time"] > DATA_START:
        raise ValueError(f"premium data starts after requested start: {symbol}")
    if frame.iloc[-1]["event_time"] != DATA_END - BAR_DELTA:
        raise ValueError(f"premium data does not reach sealed end: {symbol}")
    frame = frame.loc[frame["event_time"] >= DATA_START].reset_index(drop=True)
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not frame[column].map(math.isfinite).all():
            raise ValueError(f"non-finite premium {column}: {symbol}")
    frame["symbol"] = symbol
    frame["is_interpolated"] = False
    quality = {
        "duplicate_count": duplicate_count,
        "interpolated_row_count": 0,
        **_missing_metadata(frame["event_time"]),
    }
    return frame, quality


def safe_public_summary(metadata: dict[str, object]) -> dict[str, object]:
    """premium値を除いた取得要約を返す。

    Args:
        metadata: 完全metadata。

    Returns:
        行数、端点、hash、欠測、封印状態だけの要約。
    """

    return {
        "snapshot_id": metadata["snapshot_id"],
        "status": "SEALED_DATA_ACQUIRED",
        "sealed_target_content_opened": False,
        "data_end_exclusive": metadata["data_end_exclusive"],
        "symbols": {
            record["symbol"]: {
                key: record["artifact"][key]
                for key in (
                    "rows", "start_utc", "end_utc", "sha256", "duplicate_count",
                    "missing_row_count", "missing_segment_count", "interpolated_row_count",
                )
            }
            for record in metadata["symbols"]
        },
    }


def main() -> None:
    """14銘柄のpremium indexを取得し、target値を非表示で保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/processed/EXP-2026-0058-premium")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0058-premium-data.json")
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for symbol in ALL_SYMBOLS:
        frame, quality = normalize_premium_rows(fetch_premium_rows(symbol), symbol)
        symbol_dir = args.output_dir / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        artifact = _write_frame(frame, symbol_dir / "premium-index-15m.csv", quality)
        records.append({
            "symbol": symbol,
            "role": "sealed_unseen_target" if symbol in SEALED_TARGET_SYMBOLS else "source_or_context",
            "artifact": artifact,
        })
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
        "source_symbols": SOURCE_SYMBOLS,
        "sealed_target_symbols": SEALED_TARGET_SYMBOLS,
        "sealed_holdout": {
            "start_utc": TARGET_START.isoformat(),
            "end_utc_exclusive": DATA_END.isoformat(),
            "content_opened": False,
            "all_target_market_values_opened": False,
            "opening_rule": "EXP-2026-0058の全source gate合格時だけ固定pipelineで一度開く。",
        },
        "authentication_used": False,
        "endpoint": ENDPOINT,
        "symbols": records,
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(safe_public_summary(metadata), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
