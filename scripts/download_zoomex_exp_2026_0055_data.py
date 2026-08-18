#!/usr/bin/env python3
"""EXP-2026-0055のsource追加銘柄とunseen targetを非表示取得する。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from scripts.download_zoomex_exp_2026_0015_data import (  # noqa: E402
    BASE_URL,
    CATEGORY,
    _find_symbol,
    _get_json,
)
from scripts.download_zoomex_exp_2026_0054_data import (  # noqa: E402
    DATA_END,
    DATA_START,
    INTERVAL,
    PRICE_ENDPOINTS,
    _public_summary,
    _write_frame,
    fetch_funding_history,
    fetch_price_series,
)


SNAPSHOT_ID = "DATA-2026-0007"
EXPERIMENT_ID = "EXP-2026-0055"
SOURCE_SUPPLEMENT_SYMBOLS = (
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "NEARUSDT",
)
SEALED_TARGET_SYMBOLS = (
    "BCHUSDT",
    "LTCUSDT",
    "DOTUSDT",
    "DOGEUSDT",
)
SYMBOLS = (*SOURCE_SUPPLEMENT_SYMBOLS, *SEALED_TARGET_SYMBOLS)
TARGET_EVALUATION_START = "2026-01-01T00:00:00+00:00"
TARGET_EVALUATION_END_EXCLUSIVE = DATA_END.isoformat()


def _safe_public_summary(metadata: dict[str, object]) -> dict[str, object]:
    """価格・Funding・instrument値を除外した取得要約を返す。

    Args:
        metadata: 保存用の完全metadata。

    Returns:
        hash、行数、端点、欠測数と封印状態だけの要約。
    """

    summary = _public_summary(metadata)
    summary["source_supplement_symbols"] = list(SOURCE_SUPPLEMENT_SYMBOLS)
    summary["sealed_target_symbols"] = list(SEALED_TARGET_SYMBOLS)
    summary["sealed_target_content_opened"] = False
    return summary


def main() -> None:
    """9銘柄の1時間系列を取得し、target値をstdoutへ出さず保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/processed/EXP-2026-0055")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0055-data.json")
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
        if instrument.get("status") != "Trading" or instrument.get("settleCoin") != "USDT":
            raise ValueError(f"target is not an active USDT linear contract: {symbol}")
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
            {
                "symbol": symbol,
                "role": (
                    "source_supplement"
                    if symbol in SOURCE_SUPPLEMENT_SYMBOLS
                    else "sealed_unseen_target"
                ),
                "instrument": instrument,
                "artifacts": artifacts,
            }
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
        "source_supplement_symbols": SOURCE_SUPPLEMENT_SYMBOLS,
        "sealed_target_symbols": SEALED_TARGET_SYMBOLS,
        "sealed_holdout": {
            "start_utc": TARGET_EVALUATION_START,
            "end_utc_exclusive": TARGET_EVALUATION_END_EXCLUSIVE,
            "content_opened": False,
            "all_target_market_values_opened": False,
            "opening_rule": (
                "EXP-2026-0055の仮説とsource-only leave-one-asset-out gateをcommitで固定し、"
                "gate通過後に4 targetを一度だけ開く。"
            ),
        },
        "authentication_used": False,
        "price_endpoints": PRICE_ENDPOINTS,
        "funding_endpoint": "/cloud/trade/v3/market/funding/history",
        "target_selection": {
            "rule": (
                "2026-08-18確認時に既存実験未使用、Trading、USDT linear、2022年以前launchの"
                "候補をlaunchTime昇順、同値symbol昇順で4銘柄固定。価格・return・volumeは未使用。"
            ),
            "selected": SEALED_TARGET_SYMBOLS,
            "reserves": ("ETCUSDT", "TRXUSDT", "ATOMUSDT"),
            "excluded_short_history": ("SUIUSDT",),
        },
        "symbols": symbols_metadata,
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(_safe_public_summary(metadata), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
