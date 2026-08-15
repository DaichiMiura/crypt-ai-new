#!/usr/bin/env python3
"""認証不要のBinance Spot公開APIからBTCJPY確定日足を取得する。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd


BASE_URL = "https://api.binance.com"
KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "number_of_trades",
    "taker_buy_base_asset_volume",
    "taker_buy_quote_asset_volume",
    "ignore",
]


def _get_json(path: str, parameters: dict[str, object] | None = None) -> object:
    """Binance公開REST endpointからJSONを取得する。

    Args:
        path: `/api/v3/`から始まるendpoint path。
        parameters: query stringへ変換する任意パラメータ。

    Returns:
        JSONから復号したlistまたはmapping。
    """

    query = f"?{urlencode(parameters)}" if parameters else ""
    with urlopen(f"{BASE_URL}{path}{query}", timeout=20) as response:  # noqa: S310
        return json.loads(response.read())


def fetch_closed_day_cutoff() -> pd.Timestamp:
    """Binance server timeから当日未確定日足を除く終端を返す。

    Returns:
        当日00:00 UTC。取得範囲ではこの時刻を含めない。
    """

    payload = _get_json("/api/v3/time")
    return pd.Timestamp(payload["serverTime"], unit="ms", tz="UTC").normalize()


def download_daily_klines(
    symbol: str, start_utc: pd.Timestamp, end_exclusive_utc: pd.Timestamp
) -> pd.DataFrame:
    """指定範囲の確定済みUTC日足をpaginationして取得する。

    Args:
        symbol: Binance Spot symbol。
        start_utc: 取得開始日を含むUTC時刻。
        end_exclusive_utc: 取得終了に含めないUTC時刻。

    Returns:
        paper runnerの入力形式へ正規化した日足データ。

    Raises:
        ValueError: 空応答、重複、欠損、または範囲不足の場合。
    """

    cursor = int(start_utc.timestamp() * 1000)
    end_ms = int(end_exclusive_utc.timestamp() * 1000)
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
            raise ValueError("Binance Kline pagination did not advance")
        cursor = next_cursor
        if len(payload) < 1000:
            break
    if not rows:
        raise ValueError("Binance returned no daily Klines")
    raw = pd.DataFrame(rows, columns=KLINE_COLUMNS)
    frame = pd.DataFrame(
        {
            "event_time": pd.to_datetime(raw["open_time"], unit="ms", utc=True),
            "open": pd.to_numeric(raw["open"], errors="raise"),
            "high": pd.to_numeric(raw["high"], errors="raise"),
            "low": pd.to_numeric(raw["low"], errors="raise"),
            "close": pd.to_numeric(raw["close"], errors="raise"),
            "volume": pd.to_numeric(raw["volume"], errors="raise"),
            "is_interpolated": False,
        }
    ).drop_duplicates("event_time", keep="last")
    frame = frame[
        (frame["event_time"] >= start_utc)
        & (frame["event_time"] < end_exclusive_utc)
    ].sort_values("event_time").reset_index(drop=True)
    expected = pd.date_range(
        start_utc.normalize(),
        end_exclusive_utc.normalize() - pd.Timedelta(days=1),
        freq="D",
    )
    if list(frame["event_time"]) != list(expected):
        raise ValueError("Binance daily Klines contain a gap or endpoint mismatch")
    return frame


def _sha256(path: Path) -> str:
    """ファイル内容のSHA-256を返す。

    Args:
        path: 対象ファイル。

    Returns:
        小文字16進SHA-256。
    """

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    """BTCJPY確定日足と取得メタデータをローカルへ保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTCJPY")
    parser.add_argument("--start", default="2025-08-16T00:00:00Z")
    parser.add_argument(
        "--output", type=Path, default=Path("data/paper/BTCJPY-1d.csv")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/paper/BTCJPY-download.json")
    )
    args = parser.parse_args()
    start = pd.Timestamp(args.start)
    cutoff = fetch_closed_day_cutoff()
    frame = download_daily_klines(args.symbol, start, cutoff)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    metadata = {
        "source": "Binance Spot public REST API",
        "base_url": BASE_URL,
        "symbol": args.symbol,
        "interval": "1d",
        "retrieved_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "start_utc": frame.iloc[0]["event_time"].isoformat(),
        "end_utc": frame.iloc[-1]["event_time"].isoformat(),
        "rows": len(frame),
        "output": str(args.output),
        "sha256": _sha256(args.output),
        "authentication_used": False,
    }
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
