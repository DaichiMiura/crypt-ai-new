import pandas as pd

from scripts import download_exp_2026_0014_data as downloader


def _kline(timestamp: pd.Timestamp, price: str) -> list[object]:
    """テスト用Kline配列を作る。"""
    return [
        int(timestamp.timestamp() * 1000),
        price,
        price,
        price,
        price,
        "1",
    ]


def test_fetch_daily_klines_accepts_complete_symbol_history(monkeypatch):
    """銘柄の日足履歴を重複なく正規化することをテストする。"""
    first = downloader.MIN_WARMUP_START - pd.Timedelta(days=1)
    dates = pd.date_range(first, downloader.EVALUATION_END - pd.Timedelta(days=1), freq="D")
    monkeypatch.setattr(
        downloader,
        "_get_json",
        lambda path, parameters: [_kline(date, "100") for date in dates],
    )

    result = downloader.fetch_daily_klines("ETHUSDT")

    assert result.iloc[0]["event_time"] == first
    assert result.iloc[-1]["event_time"] == downloader.EVALUATION_END - pd.Timedelta(days=1)
    assert result["is_interpolated"].sum() == 0
