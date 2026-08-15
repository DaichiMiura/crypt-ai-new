#!/usr/bin/env python3
"""EXP-2026-0002の線形補間データセットを作成する。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.exp_2026_0001 import (  # noqa: E402
    INPUT_COLUMNS,
    inspect_hourly_data,
    interpolate_missing_hourly_data,
    load_kline_files,
)


def main() -> None:
    """入力データを補間し、合成行を記録したCSVとメタデータを保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-file",
        type=Path,
        default=Path("data/processed/EXP-2026-0001/BTCUSDT-1h.csv"),
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=Path("data/processed/EXP-2026-0002/BTCUSDT-1h-interpolated.csv"),
    )
    parser.add_argument(
        "--metadata-file",
        type=Path,
        default=Path("data/metadata/DATA-2026-0002/interpolation.json"),
    )
    args = parser.parse_args()

    source = load_kline_files([args.input_file])
    source_quality = inspect_hourly_data(source)
    result = interpolate_missing_hourly_data(source)
    output_quality = inspect_hourly_data(result)
    if output_quality["missing_intervals"] or output_quality["duplicate_count"]:
        raise ValueError(f"interpolated dataset is incomplete: {output_quality}")
    if not output_quality["interpolated_rows"]:
        raise ValueError("no synthetic rows were created")

    args.output_file.parent.mkdir(parents=True, exist_ok=True)
    result[INPUT_COLUMNS].to_csv(args.output_file, index=False, header=False)
    output_hash = hashlib.sha256(args.output_file.read_bytes()).hexdigest()
    metadata = {
        "experiment_id": "EXP-2026-0002",
        "raw_snapshot_id": "DATA-2026-0001",
        "output_snapshot_id": "DATA-2026-0002",
        "input": {"path": str(args.input_file), "quality": source_quality},
        "output": {
            "path": str(args.output_file),
            "sha256": output_hash,
            "quality": output_quality,
        },
        "policy": {
            "method": "time_linear_interpolation",
            "scope": "internal gaps only",
            "ohlc_constraint": "synthetic high >= max(open, close), synthetic low <= min(open, close)",
            "observed_values_unchanged_after_numeric_normalization": True,
            "synthetic_rows_are_not_venue_observations": True,
        },
    }
    args.metadata_file.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_file.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
