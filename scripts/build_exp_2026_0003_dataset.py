#!/usr/bin/env python3
"""EXP-2026-0003用に補間済み1時間足をUTC日足へ集約する。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.research import (  # noqa: E402
    DAILY_COLUMNS,
    aggregate_hourly_to_daily,
    inspect_daily_data,
    inspect_hourly_data,
    load_kline_files,
)


def main() -> None:
    """補間済み1時間足を日足へ変換し、データ来歴を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-file",
        type=Path,
        default=Path("data/processed/EXP-2026-0002/BTCUSDT-1h-interpolated.csv"),
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=Path("data/processed/EXP-2026-0003/BTCUSDT-1d.csv"),
    )
    parser.add_argument(
        "--metadata-file",
        type=Path,
        default=Path("data/metadata/DATA-2026-0003/aggregation.json"),
    )
    args = parser.parse_args()

    hourly = load_kline_files([args.input_file])
    hourly_quality = inspect_hourly_data(hourly)
    if hourly_quality["duplicate_count"] or hourly_quality["missing_intervals"]:
        raise ValueError(f"input hourly data is incomplete: {hourly_quality}")
    daily = aggregate_hourly_to_daily(hourly)
    daily_quality = inspect_daily_data(daily)
    if daily_quality["duplicate_count"] or daily_quality["missing_intervals"]:
        raise ValueError(f"output daily data is incomplete: {daily_quality}")

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    daily[DAILY_COLUMNS].to_csv(args.output_file, index=False)
    output_hash = hashlib.sha256(args.output_file.read_bytes()).hexdigest()
    metadata = {
        "experiment_id": "EXP-2026-0003",
        "raw_snapshot_id": "DATA-2026-0001",
        "parent_snapshot_id": "DATA-2026-0002",
        "output_snapshot_id": "DATA-2026-0003",
        "input": {"path": str(args.input_file), "quality": hourly_quality},
        "output": {
            "path": str(args.output_file),
            "sha256": output_hash,
            "quality": daily_quality,
        },
        "policy": {
            "timezone": "UTC",
            "aggregation": "open=first, high=max, low=min, close=last, volume=sum",
            "requires_24_hourly_bars_per_day": True,
            "synthetic_day_is_true_if_any_hour_is_synthetic": True,
        },
    }
    args.metadata_file.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_file.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
