import pandas as pd
import pytest

from scripts import download_zoomex_exp_2026_0015_data as downloader


def _price_row(timestamp: pd.Timestamp) -> list[str]:
    """テスト用の価格Kline配列を作る。"""
    return [str(int(timestamp.timestamp() * 1000)), "100", "101", "99", "100", "10", "1000"]


def test_normalize_price_rows_accepts_complete_two_hour_history():
    """完全な2時間足履歴を重複なく正規化することをテストする。"""
    first = downloader.EVALUATION_START - downloader.MIN_WARMUP_BARS * downloader.BAR_DELTA
    dates = pd.date_range(first, downloader.DATA_END - downloader.BAR_DELTA, freq="2h")
    result = downloader._normalize_price_rows(
        [_price_row(date) for date in dates], "LINKUSDT", "trade"
    )

    assert len(result) == len(dates)
    assert result.iloc[0]["event_time"] == first
    assert result.iloc[-1]["event_time"] == downloader.DATA_END - downloader.BAR_DELTA
    assert result["is_interpolated"].sum() == 0


def test_normalize_price_rows_rejects_internal_gap():
    """2時間足の内部欠損を拒否することをテストする。"""
    first = downloader.EVALUATION_START - downloader.MIN_WARMUP_BARS * downloader.BAR_DELTA
    dates = pd.date_range(first, downloader.DATA_END - downloader.BAR_DELTA, freq="2h")
    dates = dates.delete(10)

    with pytest.raises(ValueError, match="gap"):
        downloader._normalize_price_rows(
            [_price_row(date) for date in dates], "LINKUSDT", "trade"
        )


def test_align_price_series_trims_leading_source_offsets(monkeypatch):
    """価格3系列の先頭時刻ずれを共通区間へ揃えることをテストする。"""
    end = pd.Timestamp("2022-01-01T10:00:00Z")
    monkeypatch.setattr(downloader, "DATA_END", end)
    dates = pd.date_range("2022-01-01T00:00:00Z", end - downloader.BAR_DELTA, freq="2h")
    frames = {
        "trade": pd.DataFrame({"event_time": dates}),
        "mark_price": pd.DataFrame({"event_time": dates[1:]}),
        "index_price": pd.DataFrame({"event_time": dates[2:]}),
    }

    result = downloader._align_price_series(frames, "LINKUSDT")

    assert all(frame["event_time"].tolist() == dates[2:].tolist() for frame in result.values())
