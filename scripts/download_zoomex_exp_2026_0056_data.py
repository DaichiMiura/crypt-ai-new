#!/usr/bin/env python3
"""EXP-2026-0056の15分sourceとunseen targetを非表示取得する。"""

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
    FUNDING_PAGE_LIMIT,
    PAGE_LIMIT,
    _find_symbol,
    _get_json,
    _sha256,
)
from scripts.download_zoomex_exp_2026_0054_data import (  # noqa: E402
    _write_frame,
    fetch_funding_history,
    fetch_price_series,
)


SNAPSHOT_ID = "DATA-2026-0008"
EXPERIMENT_ID = "EXP-2026-0056"
SOURCE_SYMBOLS = (
    "BTCUSDT", "LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT",
    "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "NEARUSDT",
)
SEALED_TARGET_SYMBOLS = ("ETCUSDT", "FILUSDT", "TRXUSDT", "XLMUSDT")
ALL_SYMBOLS = (*SOURCE_SYMBOLS, *SEALED_TARGET_SYMBOLS)
DATA_START = pd.Timestamp("2022-01-01T00:00:00Z")
DATA_END = pd.Timestamp("2026-08-01T00:00:00Z")
TARGET_START = pd.Timestamp("2026-01-01T00:00:00Z")
INTERVAL = "15"
BAR_DELTA = pd.Timedelta(minutes=15)
TRADE_ENDPOINT = "/cloud/trade/v3/market/kline"


def _timestamp_ms(timestamp: pd.Timestamp) -> int:
    """UTC Timestampをミリ秒Unix時刻へ変換する。

    Args:
        timestamp: UTC時刻。

    Returns:
        ミリ秒Unix時刻。
    """

    return int(timestamp.timestamp() * 1000)


def _fetch_15m_rows(symbol: str) -> list[list[str]]:
    """指定symbolの15分trade Klineを逆ページングする。

    Args:
        symbol: ZOOMEX linear symbol。

    Returns:
        API未加工Kline行。

    Raises:
        ValueError: ページングが過去へ進まない場合。
    """

    start_ms = _timestamp_ms(DATA_START)
    cursor_end = _timestamp_ms(DATA_END) - 1
    rows: list[list[str]] = []
    while cursor_end > start_ms:
        payload = _get_json(TRADE_ENDPOINT, {
            "category": CATEGORY, "symbol": symbol, "interval": INTERVAL,
            "start": start_ms, "end": cursor_end, "limit": PAGE_LIMIT,
        })
        page = payload.get("result", {}).get("list", [])
        if not page:
            break
        rows.extend(page)
        oldest = min(int(row[0]) for row in page)
        next_end = oldest - 1
        if next_end >= cursor_end:
            raise ValueError(f"15m pagination did not move backwards: {symbol}")
        cursor_end = next_end
        if oldest <= start_ms:
            break
    return rows


def _missing_metadata(times: pd.Series) -> dict[str, object]:
    """15分観測時刻のgapを集計する。

    Args:
        times: 昇順・重複なしUTC時刻。

    Returns:
        欠測行数、segment数、端点。
    """

    segments: list[dict[str, object]] = []
    missing = 0
    for previous, current in zip(times.iloc[:-1], times.iloc[1:], strict=True):
        delta = current - previous
        if delta <= BAR_DELTA:
            continue
        count = int(delta / BAR_DELTA) - 1
        missing += count
        segments.append({
            "start_utc": (previous + BAR_DELTA).isoformat(),
            "end_utc": (current - BAR_DELTA).isoformat(),
            "missing_rows": count,
        })
    return {"missing_row_count": missing, "missing_segment_count": len(segments), "missing_segments": segments}


def normalize_15m_rows(
    rows: list[list[str]], symbol: str,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """15分trade Klineを値変更なしで正規化する。

    Args:
        rows: API Kline行。
        symbol: ZOOMEX linear symbol。

    Returns:
        観測DataFrameと品質metadata。

    Raises:
        ValueError: 空、重複、期間、整列、数値が不正な場合。
    """

    if not rows:
        raise ValueError(f"no 15m rows returned: {symbol}")
    frame = pd.DataFrame({
        "event_time": pd.to_datetime([int(row[0]) for row in rows], unit="ms", utc=True),
        "open": [row[1] for row in rows], "high": [row[2] for row in rows],
        "low": [row[3] for row in rows], "close": [row[4] for row in rows],
        "volume": [row[5] for row in rows], "turnover": [row[6] for row in rows],
    })
    duplicate_count = int(frame["event_time"].duplicated().sum())
    if duplicate_count:
        raise ValueError(f"duplicate 15m rows: {symbol}")
    frame = frame.sort_values("event_time").reset_index(drop=True)
    if any(timestamp.minute not in {0, 15, 30, 45} or timestamp.second for timestamp in frame["event_time"]):
        raise ValueError(f"unaligned 15m timestamp: {symbol}")
    if frame.iloc[0]["event_time"] > DATA_START:
        raise ValueError(f"15m data starts after requested start: {symbol}")
    if frame.iloc[-1]["event_time"] != DATA_END - BAR_DELTA:
        raise ValueError(f"15m data does not reach sealed end: {symbol}")
    frame = frame.loc[frame["event_time"] >= DATA_START].reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume", "turnover"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not frame[column].map(math.isfinite).all():
            raise ValueError(f"non-finite 15m {column}: {symbol}")
    if not (frame[["open", "high", "low", "close"]] > 0).all().all():
        raise ValueError(f"non-positive 15m price: {symbol}")
    if (frame[["volume", "turnover"]] < 0).any().any():
        raise ValueError(f"negative 15m activity: {symbol}")
    frame["symbol"] = symbol
    frame["is_interpolated"] = False
    quality = {"duplicate_count": duplicate_count, "interpolated_row_count": 0, **_missing_metadata(frame["event_time"])}
    return frame, quality


def _safe_public_summary(metadata: dict[str, object]) -> dict[str, object]:
    """価格、Funding、instrument値を除いた取得要約を返す。

    Args:
        metadata: 完全metadata。

    Returns:
        行数、端点、hash、欠測、封印状態。
    """

    symbols: dict[str, object] = {}
    for record in metadata["symbols"]:
        symbols[record["symbol"]] = {
            name: {key: artifact[key] for key in (
                "rows", "start_utc", "end_utc", "sha256", "duplicate_count",
                "missing_row_count", "missing_segment_count", "interpolated_row_count",
            ) if key in artifact}
            for name, artifact in record["artifacts"].items()
        }
    return {
        "snapshot_id": metadata["snapshot_id"], "status": "SEALED_DATA_ACQUIRED",
        "sealed_target_content_opened": False, "data_end_exclusive": metadata["data_end_exclusive"],
        "symbols": symbols,
    }


def main() -> None:
    """14銘柄の15分足とtarget会計系列を取得して非表示保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/EXP-2026-0056"))
    parser.add_argument("--metadata", type=Path, default=Path("var/exp-2026-0056-data.json"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    instruments = _get_json(
        "/cloud/trade/v3/market/instruments-info", {"category": CATEGORY, "limit": 1000}
    ).get("result", {}).get("list", [])
    records: list[dict[str, object]] = []
    for symbol in ALL_SYMBOLS:
        instrument = _find_symbol(instruments, symbol)
        if instrument.get("status") != "Trading" or instrument.get("settleCoin") != "USDT":
            raise ValueError(f"not an active USDT linear contract: {symbol}")
        symbol_dir = args.output_dir / symbol
        symbol_dir.mkdir(parents=True, exist_ok=True)
        trade, trade_quality = normalize_15m_rows(_fetch_15m_rows(symbol), symbol)
        artifacts = {"trade_15m": _write_frame(trade, symbol_dir / "trade-15m.csv", trade_quality)}
        if symbol in SEALED_TARGET_SYMBOLS:
            mark, mark_quality = fetch_price_series(symbol, "mark_price")
            funding, funding_quality = fetch_funding_history(symbol)
            artifacts["mark_price_1h"] = _write_frame(mark, symbol_dir / "mark-price-1h.csv", mark_quality)
            artifacts["funding"] = _write_frame(funding, symbol_dir / "funding-rate.csv", funding_quality)
        records.append({
            "symbol": symbol,
            "role": "sealed_unseen_target" if symbol in SEALED_TARGET_SYMBOLS else "source_or_context",
            "instrument": instrument,
            "artifacts": artifacts,
        })
    metadata = {
        "schema_version": 1, "snapshot_id": SNAPSHOT_ID, "experiment_id": EXPERIMENT_ID,
        "source": "ZOOMEX Global public V3 REST API", "base_url": BASE_URL,
        "category": CATEGORY, "interval": INTERVAL,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_start": DATA_START.isoformat(), "data_end_exclusive": DATA_END.isoformat(),
        "source_symbols": SOURCE_SYMBOLS, "sealed_target_symbols": SEALED_TARGET_SYMBOLS,
        "sealed_holdout": {
            "start_utc": TARGET_START.isoformat(), "end_utc_exclusive": DATA_END.isoformat(),
            "content_opened": False, "all_target_market_values_opened": False,
            "opening_rule": "EXP-2026-0056の仮説と12-fold source gateをcommitで固定し、合格時だけ一度開く。",
        },
        "authentication_used": False,
        "endpoints": {
            "trade_15m": TRADE_ENDPOINT,
            "mark_price_1h": "/cloud/trade/v3/market/mark-price-kline",
            "funding": "/cloud/trade/v3/market/funding/history",
            "instruments_info": "/cloud/trade/v3/market/instruments-info",
        },
        "target_selection": {
            "rule": "既存実験未使用、Trading、USDT linear、2022年以前launchをlaunchTime・symbol昇順で4銘柄固定。価格・return・volumeは未使用。",
            "selected": SEALED_TARGET_SYMBOLS,
            "reserves": ("ICPUSDT", "ALGOUSDT", "ATOMUSDT"),
        },
        "symbols": records,
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(_safe_public_summary(metadata), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
