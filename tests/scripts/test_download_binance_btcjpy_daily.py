import pandas as pd

from scripts import download_binance_btcjpy_daily as downloader


def _kline(timestamp: pd.Timestamp, price: str) -> list[object]:
    """テスト用Binance Kline配列を作る。"""
    open_time = int(timestamp.timestamp() * 1000)
    return [
        open_time,
        price,
        price,
        price,
        price,
        "1",
        open_time + 86_399_999,
        "1",
        1,
        "1",
        "1",
        "0",
    ]


def test_download_daily_klines_normalizes_complete_closed_days(monkeypatch):
    """公開APIの日足をpaper入力形式へ変換することをテストする。"""
    start = pd.Timestamp("2026-08-10T00:00:00Z")
    end = pd.Timestamp("2026-08-12T00:00:00Z")

    monkeypatch.setattr(
        downloader,
        "_get_json",
        lambda path, parameters=None: [
            _kline(start, "100"),
            _kline(start + pd.Timedelta(days=1), "110"),
        ],
    )
    result = downloader.download_daily_klines("BTCJPY", start, end)

    assert len(result) == 2
    assert list(result["close"]) == [100, 110]
    assert result["is_interpolated"].sum() == 0


def test_fetch_closed_day_cutoff_uses_binance_server_time(monkeypatch):
    """取得終端をローカル時計でなくBinance server timeから決めることをテストする。"""
    timestamp = pd.Timestamp("2026-08-15T12:34:56Z")
    monkeypatch.setattr(
        downloader,
        "_get_json",
        lambda path, parameters=None: {"serverTime": int(timestamp.timestamp() * 1000)},
    )

    assert downloader.fetch_closed_day_cutoff() == pd.Timestamp(
        "2026-08-15T00:00:00Z"
    )
