#!/usr/bin/env python3
"""EXP-2026-0007用の補間済み時間足とUTC日足を作成する。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.research import (  # noqa: E402
    DAILY_COLUMNS,
    INPUT_COLUMNS,
    aggregate_hourly_to_daily,
    inspect_daily_data,
    inspect_hourly_data,
    interpolate_missing_hourly_data,
    load_kline_files,
)


def _merge_corrections(
    monthly_paths: list[Path], correction_paths: list[Path]
) -> tuple[pd.DataFrame, int]:
    """月次データへ日次補正データを優先して結合する。

    Args:
        monthly_paths: Binance公式月次Kline CSVのパス。
        correction_paths: Binance公式日次補正Kline CSVのパス。

    Returns:
        補正行を優先したデータフレームと置換行数。

    Raises:
        ValueError: 月次データが空、または補正データに重複時刻がある場合。
    """

    monthly = load_kline_files(monthly_paths)
    if not correction_paths:
        return monthly, 0
    corrections = load_kline_files(correction_paths)
    monthly["source_priority"] = 0
    corrections["source_priority"] = 1
    combined = pd.concat([monthly, corrections], ignore_index=True)
    before = len(combined)
    combined = (
        combined.sort_values(["event_time", "source_priority"])
        .drop_duplicates("event_time", keep="last")
        .sort_values("event_time")
        .reset_index(drop=True)
    )
    combined = combined.drop(columns=["source_priority"])
    return combined, before - len(combined)


def _sha256(path: Path) -> str:
    """ファイルのSHA-256ハッシュを返す。

    Args:
        path: ハッシュを計算するファイル。

    Returns:
        小文字のSHA-256文字列。
    """

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """raw Klineを統合・補間・日足化し、来歴メタデータを保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--monthly-dir",
        type=Path,
        default=Path("data/raw/binance_global/spot/klines/BTCUSDT/1h"),
    )
    parser.add_argument(
        "--correction-dir",
        type=Path,
        default=Path("data/raw/binance_global/spot/daily-corrections/BTCUSDT/1h"),
    )
    parser.add_argument(
        "--output-hourly",
        type=Path,
        default=Path(
            "data/processed/EXP-2026-0007/BTCUSDT-1h-interpolated.csv"
        ),
    )
    parser.add_argument(
        "--output-daily",
        type=Path,
        default=Path("data/processed/EXP-2026-0007/BTCUSDT-1d.csv"),
    )
    parser.add_argument(
        "--metadata-file",
        type=Path,
        default=Path("data/metadata/DATA-2026-0004/build.json"),
    )
    args = parser.parse_args()

    monthly_paths = sorted(args.monthly_dir.glob("*.csv"))
    correction_paths = sorted(args.correction_dir.glob("*.csv"))
    source, corrected_rows = _merge_corrections(monthly_paths, correction_paths)
    source_quality = inspect_hourly_data(source)
    if source_quality["duplicate_count"]:
        raise ValueError(f"raw source contains duplicates: {source_quality}")

    hourly = interpolate_missing_hourly_data(source)
    hourly_quality = inspect_hourly_data(hourly)
    if hourly_quality["duplicate_count"] or hourly_quality["missing_intervals"]:
        raise ValueError(f"interpolated source is incomplete: {hourly_quality}")

    daily = aggregate_hourly_to_daily(hourly)
    daily_quality = inspect_daily_data(daily)
    if daily_quality["duplicate_count"] or daily_quality["missing_intervals"]:
        raise ValueError(f"daily source is incomplete: {daily_quality}")
    if daily.empty or daily.iloc[0]["event_time"] > pd.Timestamp(
        "2020-01-01T00:00:00Z"
    ):
        raise ValueError("forward dataset lacks the required warm-up history")
    if daily.iloc[-1]["event_time"] < pd.Timestamp("2026-07-31T00:00:00Z"):
        raise ValueError("forward dataset does not reach the registered end date")

    args.output_hourly.parent.mkdir(parents=True, exist_ok=True)
    hourly[INPUT_COLUMNS].to_csv(args.output_hourly, index=False, header=False)
    args.output_daily.parent.mkdir(parents=True, exist_ok=True)
    daily[DAILY_COLUMNS].to_csv(args.output_daily, index=False)

    metadata = {
        "experiment_id": "EXP-2026-0007",
        "snapshot_id": "DATA-2026-0004",
        "source": {
            "monthly_files": [
                {"path": str(path), "sha256": _sha256(path)}
                for path in monthly_paths
            ],
            "correction_files": [
                {"path": str(path), "sha256": _sha256(path)}
                for path in correction_paths
            ],
            "corrected_rows": corrected_rows,
            "quality": source_quality,
        },
        "transform": {
            "interpolation": "time_linear_interpolation_internal_gaps_only",
            "hourly_quality": hourly_quality,
            "daily_aggregation": "open=first, high=max, low=min, close=last, volume=sum",
            "daily_quality": daily_quality,
        },
        "outputs": {
            "hourly": {
                "path": str(args.output_hourly),
                "sha256": _sha256(args.output_hourly),
            },
            "daily": {
                "path": str(args.output_daily),
                "sha256": _sha256(args.output_daily),
            },
        },
        "forward_window": {
            "start_utc": "2026-01-01T00:00:00Z",
            "end_utc": "2026-07-31T23:59:59Z",
        },
    }
    args.metadata_file.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_file.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
