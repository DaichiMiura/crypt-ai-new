#!/usr/bin/env python3
"""EXP-2026-0014用のGlobal Spot USDT日足proxyを取得する。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd


BASE_URL = "https://api.binance.com"
SYMBOLS = ("ETHUSDT", "SOLUSDT", "XRPUSDT")
EVALUATION_START = pd.Timestamp("2022-01-01T00:00:00Z")
EVALUATION_END = pd.Timestamp("2026-01-01T00:00:00Z")
MIN_WARMUP_START = EVALUATION_START - pd.Timedelta(days=200)


def _get_json(path: str, parameters: dict[str, object]) -> object:
    """公開Binance REST endpointからJSONを取得する。

    Args:
        path: API path。
        parameters: query parameters。

    Returns:
        JSON payload。
    """

    query = urlencode(parameters)
    with urlopen(f"{BASE_URL}{path}?{query}", timeout=30) as response:  # noqa: S310
        return json.loads(response.read())


def fetch_daily_klines(symbol: str) -> pd.DataFrame:
    """指定銘柄の2020年以降の日足をpagination取得する。

    Args:
        symbol: Binance Global Spot symbol。

    Returns:
        `event_time`、OHLCV、補間フラグを持つ日足データ。

    Raises:
        ValueError: warm-up不足、欠損、重複、または空応答の場合。
    """

    cursor = int(pd.Timestamp("2020-01-01T00:00:00Z").timestamp() * 1000)
    end_ms = int(EVALUATION_END.timestamp() * 1000)
    rows: list[list[object]] = []
    while cursor < end_ms:
        payload = _get_json(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": "1d",
                "startTime": cursor,
                "endTime": end_ms - 1,
                "limit": 1000,
            },
        )
        if not payload:
            break
        rows.extend(payload)
        next_cursor = int(payload[-1][0]) + 86_400_000
        if next_cursor <= cursor:
            raise ValueError(f"pagination did not advance: {symbol}")
        cursor = next_cursor
        if len(payload) < 1000:
            break
    if not rows:
        raise ValueError(f"no daily Klines returned: {symbol}")
    frame = pd.DataFrame(
        {
            "event_time": pd.to_datetime([row[0] for row in rows], unit="ms", utc=True),
            "open": [row[1] for row in rows],
            "high": [row[2] for row in rows],
            "low": [row[3] for row in rows],
            "close": [row[4] for row in rows],
            "volume": [row[5] for row in rows],
            "is_interpolated": False,
        }
    )
    if frame["event_time"].duplicated().any():
        raise ValueError(f"duplicate daily Klines: {symbol}")
    frame = frame.sort_values("event_time").reset_index(drop=True)
    if frame.iloc[0]["event_time"] > MIN_WARMUP_START:
        raise ValueError(f"insufficient warm-up history: {symbol}")
    expected = pd.date_range(
        frame.iloc[0]["event_time"],
        EVALUATION_END - pd.Timedelta(days=1),
        freq="D",
    )
    if list(frame["event_time"]) != list(expected):
        raise ValueError(f"daily gap in downloaded data: {symbol}")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def _sha256(path: Path) -> str:
    """ファイルSHA-256を返す。

    Args:
        path: 対象ファイル。

    Returns:
        小文字16進SHA-256。
    """

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """3銘柄の日足CSVと取得メタデータを保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed/EXP-2026-0014"))
    parser.add_argument("--metadata", type=Path, default=Path("var/exp-2026-0014-data.json"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for symbol in SYMBOLS:
        frame = fetch_daily_klines(symbol)
        output = args.output_dir / f"{symbol}-1d.csv"
        frame.to_csv(output, index=False)
        records.append(
            {
                "symbol": symbol,
                "start_utc": frame.iloc[0]["event_time"].isoformat(),
                "end_utc": frame.iloc[-1]["event_time"].isoformat(),
                "rows": len(frame),
                "sha256": _sha256(output),
                "path": str(output),
            }
        )
    metadata = {
        "experiment_id": "EXP-2026-0014",
        "source": "Binance Global Spot public REST API",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "evaluation_start": EVALUATION_START.isoformat(),
        "evaluation_end_exclusive": EVALUATION_END.isoformat(),
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
