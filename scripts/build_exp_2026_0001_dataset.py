#!/usr/bin/env python3
"""月次Klineの欠損を公式日次アーカイブで補完し、processed datasetを作る。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "src"))
sys.path.insert(0, str(repo_root))

from crypt_ai.exp_2026_0001 import (  # noqa: E402
    KLINE_COLUMNS,
    inspect_hourly_data,
    load_kline_files,
)
from scripts.download_binance_global_data import (  # noqa: E402
    download_bytes,
    parse_checksum,
    safe_extract,
)


def missing_dates(frame: pd.DataFrame) -> list[str]:
    """1時間足の欠損を含む日付を重複なく返す。

    Args:
        frame: `load_kline_files`が返す時系列データ。

    Returns:
        日次アーカイブで補完候補となるISO日付のリスト。
    """

    timestamps = frame["event_time"].sort_values().reset_index(drop=True)
    dates: set[str] = set()
    for index in range(1, len(timestamps)):
        previous = timestamps.iloc[index - 1]
        current = timestamps.iloc[index]
        if current - previous <= pd.Timedelta(hours=1):
            continue
        cursor = previous.normalize()
        end = current.normalize()
        while cursor <= end:
            dates.add(cursor.date().isoformat())
            cursor += pd.Timedelta(days=1)
    return sorted(dates)


def fetch_daily_correction(
    symbol: str,
    interval: str,
    day: str,
    output_dir: Path,
) -> dict[str, str | list[str]]:
    """指定日の公式日次Klineを取得し、チェックサム検証する。

    Args:
        symbol: Binanceの大文字シンボル。
        interval: Kline interval。
        day: 対象日（YYYY-MM-DD）。
        output_dir: 補正rawデータの保存先。

    Returns:
        URL、SHA-256、展開ファイルを含むメタデータ。
    """

    base = "https://data.binance.vision/data/spot/daily/klines"
    filename = f"{symbol}-{interval}-{day}.zip"
    url = f"{base}/{symbol}/{interval}/{filename}"
    zip_bytes = download_bytes(url)
    checksum_text = download_bytes(f"{url}.CHECKSUM").decode("utf-8")
    expected = parse_checksum(checksum_text)
    actual = hashlib.sha256(zip_bytes).hexdigest()
    if expected != actual:
        raise ValueError(f"checksum mismatch for {filename}: {actual} != {expected}")
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / filename
    zip_path.write_bytes(zip_bytes)
    (output_dir / f"{filename}.CHECKSUM").write_text(checksum_text, encoding="utf-8")
    members = safe_extract(zip_bytes, output_dir)
    return {"day": day, "url": url, "sha256": actual, "members": members}


def consolidate_dataset(
    monthly_paths: list[Path],
    correction_paths: list[Path],
    output_path: Path,
    allow_unresolved_gaps: bool = False,
) -> dict[str, object]:
    """月次rawと日次補正rawを統合し、補正行数を記録する。

    Args:
        monthly_paths: 月次CSVのパス。
        correction_paths: 日次補正CSVのパス。
        output_path: processed CSVの保存先。
        allow_unresolved_gaps: 欠損を残したQA用出力を許可するか。

    Returns:
        出力件数、補正件数、データ品質の辞書。

    Raises:
        ValueError: 補正後も欠損や重複が残る場合。
    """

    monthly = load_kline_files(monthly_paths)
    corrections = load_kline_files(correction_paths) if correction_paths else pd.DataFrame()
    if corrections.empty:
        combined = monthly.copy()
        corrected_rows = 0
    else:
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
        corrected_rows = before - len(combined)
    quality = inspect_hourly_data(combined)
    if quality["duplicate_count"] or (
        quality["missing_intervals"] and not allow_unresolved_gaps
    ):
        raise ValueError(f"processed dataset is still incomplete: {quality}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined[KLINE_COLUMNS].to_csv(output_path, index=False, header=False)
    return {
        "output": str(output_path),
        "rows": int(len(combined)),
        "corrected_rows": corrected_rows,
        "quality": quality,
    }


def main() -> None:
    """欠損検出、日次補完、processed CSV出力を実行する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
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
        "--output-file",
        type=Path,
        default=Path("data/processed/EXP-2026-0001/BTCUSDT-1h.csv"),
    )
    parser.add_argument(
        "--metadata-file",
        type=Path,
        default=Path("data/metadata/DATA-2026-0001/corrections.json"),
    )
    parser.add_argument(
        "--allow-unresolved-gaps",
        action="store_true",
        help="欠損を残したQA用processed出力を許可する。バックテストには使わない。",
    )
    args = parser.parse_args()

    monthly_paths = sorted(args.monthly_dir.glob("*.csv"))
    monthly = load_kline_files(monthly_paths)
    dates = missing_dates(monthly)
    correction_records = []
    for day in dates:
        correction_records.append(
            fetch_daily_correction(args.symbol, args.interval, day, args.correction_dir)
        )
    correction_paths = sorted(args.correction_dir.glob("*.csv"))
    result = consolidate_dataset(
        monthly_paths,
        correction_paths,
        args.output_file,
        allow_unresolved_gaps=args.allow_unresolved_gaps,
    )
    metadata = {"missing_dates": dates, "corrections": correction_records, "dataset": result}
    args.metadata_file.parent.mkdir(parents=True, exist_ok=True)
    args.metadata_file.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
