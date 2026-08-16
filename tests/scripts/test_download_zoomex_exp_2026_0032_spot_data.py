import pandas as pd
import pytest

from scripts import download_zoomex_exp_2026_0032_spot_data as downloader


def _spot_row(timestamp: pd.Timestamp) -> list[str]:
    """テスト用の現物Kline配列を作る。"""
    return [str(int(timestamp.timestamp() * 1000)), "100", "101", "99", "100", "10", "1000"]


def test_normalize_spot_rows_accepts_complete_two_hour_history():
    """完全な現物2時間足履歴を重複なく正規化することをテストする。"""
    first = downloader.EVALUATION_START - downloader.MIN_WARMUP_BARS * downloader.BAR_DELTA
    dates = pd.date_range(first, downloader.DATA_END - downloader.BAR_DELTA, freq="2h")

    result = downloader._normalize_rows([_spot_row(date) for date in dates], "LINKUSDT")

    assert len(result) == len(dates)
    assert result.iloc[0]["event_time"] == first
    assert result.iloc[-1]["event_time"] == downloader.DATA_END - downloader.BAR_DELTA
    assert result["is_interpolated"].sum() == 0


def test_normalize_spot_rows_rejects_internal_gap():
    """現物2時間足の内部欠損を拒否することをテストする。"""
    first = downloader.EVALUATION_START - downloader.MIN_WARMUP_BARS * downloader.BAR_DELTA
    dates = pd.date_range(first, downloader.DATA_END - downloader.BAR_DELTA, freq="2h")
    dates = dates.delete(10)

    with pytest.raises(ValueError, match="gap"):
        downloader._normalize_rows([_spot_row(date) for date in dates], "LINKUSDT")
