#!/usr/bin/env python3
"""Binance Public DataのKlineを取得し、チェックサムとexchangeInfoを保存する。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from zipfile import ZipFile


def month_range(start: str, end: str) -> list[tuple[int, int]]:
    """YYYY-MMの範囲を月単位のリストへ展開する。

    Args:
        start: 開始月（YYYY-MM）。
        end: 終了月（YYYY-MM）。

    Returns:
        開始月と終了月を含む年月タプルのリスト。

    Raises:
        ValueError: 月形式が不正、またはstartがendより後の場合。
    """

    start_date = date.fromisoformat(f"{start}-01")
    end_date = date.fromisoformat(f"{end}-01")
    if start_date > end_date:
        raise ValueError("start month must not be after end month")
    result: list[tuple[int, int]] = []
    year, month = start_date.year, start_date.month
    while (year, month) <= (end_date.year, end_date.month):
        result.append((year, month))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return result


def parse_checksum(text: str) -> str:
    """CHECKSUMファイルからSHA-256文字列を取り出す。

    Args:
        text: Binanceが提供するCHECKSUMファイルの本文。

    Returns:
        小文字化したSHA-256文字列。

    Raises:
        ValueError: 64桁のSHA-256が見つからない場合。
    """

    for token in text.split():
        candidate = token.lower()
        if len(candidate) == 64 and all(character in "0123456789abcdef" for character in candidate):
            return candidate
    raise ValueError("SHA-256 checksum was not found")


def download_bytes(url: str) -> bytes:
    """URLからバイト列を取得する。

    Args:
        url: HTTPS URL。

    Returns:
        レスポンス本文。

    Raises:
        urllib.error.HTTPError: HTTPエラーが返った場合。
        urllib.error.URLError: 接続に失敗した場合。
    """

    request = urllib.request.Request(url, headers={"User-Agent": "crypt-ai-research/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def safe_extract(zip_bytes: bytes, output_dir: Path) -> list[str]:
    """ZIPをパストラバーサル検査後に展開する。

    Args:
        zip_bytes: ZIPファイルのバイト列。
        output_dir: 展開先ディレクトリ。

    Returns:
        展開したファイル名のリスト。

    Raises:
        ValueError: ZIP内に安全でないパスがある場合。
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    with ZipFile(io.BytesIO(zip_bytes)) as archive:
        names = archive.namelist()
        root = output_dir.resolve()
        for name in names:
            target = (output_dir / name).resolve()
            if root != target and root not in target.parents:
                raise ValueError(f"unsafe archive member: {name}")
        archive.extractall(output_dir)
    return names


def fetch_monthly_klines(
    symbol: str,
    interval: str,
    start_month: str,
    end_month: str,
    output_dir: Path,
) -> dict[str, object]:
    """指定範囲の月次Klineを取得し、チェックサム検証する。

    Args:
        symbol: Binanceの大文字シンボル。
        interval: Kline interval（例: `1h`）。
        start_month: 開始月（YYYY-MM）。
        end_month: 終了月（YYYY-MM）。
        output_dir: ZIP、CHECKSUM、CSVの保存先。

    Returns:
        取得ファイルとSHA-256を含むメタデータ。
    """

    base = "https://data.binance.vision/data/spot/monthly/klines"
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for year, month in month_range(start_month, end_month):
        month_text = f"{year:04d}-{month:02d}"
        filename = f"{symbol}-{interval}-{month_text}.zip"
        url = f"{base}/{symbol}/{interval}/{filename}"
        zip_bytes = download_bytes(url)
        checksum_text = download_bytes(f"{url}.CHECKSUM").decode("utf-8")
        expected = parse_checksum(checksum_text)
        actual = hashlib.sha256(zip_bytes).hexdigest()
        if actual != expected:
            raise ValueError(f"checksum mismatch for {filename}: {actual} != {expected}")
        zip_path = output_dir / filename
        checksum_path = output_dir / f"{filename}.CHECKSUM"
        zip_path.write_bytes(zip_bytes)
        checksum_path.write_text(checksum_text, encoding="utf-8")
        members = safe_extract(zip_bytes, output_dir)
        records.append(
            {
                "month": month_text,
                "url": url,
                "zip": str(zip_path),
                "sha256": actual,
                "members": members,
            }
        )
    return {"symbol": symbol, "interval": interval, "files": records}


def fetch_exchange_info(symbol: str, output_path: Path) -> dict[str, object]:
    """SpotのexchangeInfoを取得し、時点の注文制約を保存する。

    Args:
        symbol: Binanceの大文字シンボル。
        output_path: JSON保存先。

    Returns:
        APIレスポンスをJSONとして解釈した辞書。
    """

    query = urllib.parse.urlencode({"symbol": symbol})
    payload = json.loads(
        download_bytes(f"https://data-api.binance.vision/api/v3/exchangeInfo?{query}")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def main() -> None:
    """コマンドライン引数を読み、データ取得を実行する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--start-month", default="2020-01")
    parser.add_argument("--end-month", default="2025-12")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/raw/binance_global/spot/klines/BTCUSDT/1h"),
    )
    parser.add_argument(
        "--metadata-dir",
        type=Path,
        default=Path("data/metadata/DATA-2026-0001"),
    )
    args = parser.parse_args()

    result = fetch_monthly_klines(
        args.symbol, args.interval, args.start_month, args.end_month, args.output_dir
    )
    exchange_info_path = args.metadata_dir / f"exchange-info-{args.symbol}.json"
    exchange_info = fetch_exchange_info(args.symbol, exchange_info_path)
    manifest = {
        "download": result,
        "exchange_info": str(exchange_info_path),
        "exchange_info_symbols": [
            entry.get("symbol") for entry in exchange_info.get("symbols", [])
        ],
    }
    args.metadata_dir.mkdir(parents=True, exist_ok=True)
    (args.metadata_dir / "download-summary.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
