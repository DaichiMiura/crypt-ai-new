import json

import pandas as pd
import pytest

from scripts import download_zoomex_exp_2026_0054_data as downloader


def _price_row(timestamp: pd.Timestamp, source: str = "trade") -> list[str]:
    """テスト用の1時間Kline配列を作る。"""

    row = [
        str(int(timestamp.timestamp() * 1000)),
        "100",
        "101",
        "99",
        "100",
    ]
    return [*row, "10", "1000"] if source == "trade" else row


def _test_dates() -> pd.DatetimeIndex:
    """warm-upと封印終了を満たす短縮テスト時刻を返す。"""

    return pd.date_range("2021-12-31T22:00:00Z", "2022-01-01T03:00:00Z", freq="1h")


def test_normalize_hourly_rows_records_gap_without_interpolation(monkeypatch):
    """内部欠測を補間せず、欠測metadataへ残す。"""

    monkeypatch.setattr(downloader, "EVALUATION_START", pd.Timestamp("2022-01-01T00:00:00Z"))
    monkeypatch.setattr(downloader, "MIN_WARMUP_BARS", 2)
    monkeypatch.setattr(downloader, "DATA_END", pd.Timestamp("2022-01-01T04:00:00Z"))
    dates = _test_dates().delete(3)

    frame, quality = downloader._normalize_price_rows(
        [_price_row(date) for date in dates], "LINKUSDT", "trade"
    )

    assert len(frame) == len(dates)
    assert frame["is_interpolated"].sum() == 0
    assert quality["missing_row_count"] == 1
    assert quality["missing_segment_count"] == 1


def test_normalize_hourly_rows_rejects_duplicate(monkeypatch):
    """同じ開始時刻のKlineをデータ品質違反として拒否する。"""

    monkeypatch.setattr(downloader, "EVALUATION_START", pd.Timestamp("2022-01-01T00:00:00Z"))
    monkeypatch.setattr(downloader, "MIN_WARMUP_BARS", 2)
    monkeypatch.setattr(downloader, "DATA_END", pd.Timestamp("2022-01-01T04:00:00Z"))
    dates = _test_dates()
    rows = [_price_row(date) for date in dates]

    with pytest.raises(ValueError, match="duplicate"):
        downloader._normalize_price_rows([*rows, rows[0]], "LINKUSDT", "trade")


def test_premium_index_accepts_negative_values(monkeypatch):
    """premium indexは価格でないため負値を正当に保持する。"""

    monkeypatch.setattr(downloader, "EVALUATION_START", pd.Timestamp("2022-01-01T00:00:00Z"))
    monkeypatch.setattr(downloader, "MIN_WARMUP_BARS", 2)
    monkeypatch.setattr(downloader, "DATA_END", pd.Timestamp("2022-01-01T04:00:00Z"))
    rows = [
        [str(int(date.timestamp() * 1000)), "-0.01", "0", "-0.02", "-0.01"]
        for date in _test_dates()
    ]

    frame, quality = downloader._normalize_price_rows(
        rows, "LINKUSDT", "premium_index"
    )

    assert frame.iloc[0]["open"] == pytest.approx(-0.01)
    assert quality["missing_row_count"] == 0


def test_public_summary_excludes_instrument_and_market_values():
    """stdout要約へ銘柄仕様や価格値を混入させない。"""

    metadata = {
        "snapshot_id": downloader.SNAPSHOT_ID,
        "data_end_exclusive": downloader.DATA_END.isoformat(),
        "sealed_holdout": {
            "start_utc": downloader.SEALED_HOLDOUT_START.isoformat(),
            "end_utc_exclusive": downloader.SEALED_HOLDOUT_END.isoformat(),
        },
        "symbols": [
            {
                "symbol": "LINKUSDT",
                "instrument": {"symbol": "LINKUSDT", "priceScale": "4"},
                "artifacts": {
                    "trade": {
                        "rows": 10,
                        "start_utc": "2021-01-01T00:00:00+00:00",
                        "end_utc": "2026-07-31T23:00:00+00:00",
                        "sha256": "abc",
                        "duplicate_count": 0,
                        "missing_row_count": 0,
                        "missing_segment_count": 0,
                        "interpolated_row_count": 0,
                    }
                },
            }
        ],
    }

    serialized = json.dumps(downloader._public_summary(metadata))

    assert "priceScale" not in serialized
    assert "instrument" not in serialized
    assert "holdout_content_opened" in serialized
