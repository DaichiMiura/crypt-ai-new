"""EXP-2026-0042 paper/shadow collectorをテストする。"""

from decimal import Decimal

import pandas as pd

from scripts.capture_exp_2026_0042_paper_shadow import (
    BAR_DELTA,
    LOOKBACK_BARS,
    SYMBOLS,
    _apply_pending_orders,
    _apply_funding,
    _execution_price,
    _normalize_closed_bars,
)


def test_normalize_closed_bars_excludes_incomplete_bar() -> None:
    """未確定の最新2時間足を除外することをテストする。"""

    times = pd.date_range("2026-01-01", periods=LOOKBACK_BARS + 3, freq=BAR_DELTA, tz="UTC")
    rows = [
        [str(int(time.timestamp() * 1000)), "1", "2", "0.5", "1.5", "1", "1"]
        for time in reversed(times)
    ]
    server_time = times[-1] + BAR_DELTA - pd.Timedelta(minutes=1)

    result = _normalize_closed_bars(rows, server_time)

    assert result.iloc[-1]["event_time"] == times[-2]


def test_execution_price_is_adverse_on_both_sides() -> None:
    """paper約定価格が売買とも不利側へ調整されることをテストする。"""

    assert _execution_price("100", "buy") == Decimal("100.1000")
    assert _execution_price("100", "sell") == Decimal("99.9000")


def test_pending_target_enters_on_next_bar_open() -> None:
    """予約targetが次の2時間足openで仮想約定することをテストする。"""

    times = pd.date_range("2026-01-01", periods=2, freq=BAR_DELTA, tz="UTC")
    frames = {
        symbol: pd.DataFrame(
            {
                "event_time": times,
                "open": [100, 110],
                "high": [101, 111],
                "low": [99, 109],
                "close": [100, 110],
            }
        )
        for symbol in SYMBOLS
    }
    state = {
        "cash_usdt": "1000",
        "positions": {},
        "pending_target_symbols": ["LINKUSDT"],
        "pending_from_bar": times[0].isoformat(),
    }

    events = _apply_pending_orders(state, frames)

    assert events[0]["event_type"] == "ENTRY"
    assert events[0]["event_time"] == times[1].isoformat()
    assert state["cash_usdt"] == "999.8800"
    assert set(state["positions"]) == {"LINKUSDT"}


def test_funding_uses_close_of_bar_ending_at_funding_time(monkeypatch) -> None:
    """Funding時刻に終了する確定足の終値で評価することをテストする。"""

    times = pd.date_range("2026-01-01 04:00", periods=2, freq=BAR_DELTA, tz="UTC")
    frames = {
        symbol: pd.DataFrame(
            {
                "event_time": times,
                "open": [100, 110],
                "high": [101, 111],
                "low": [99, 109],
                "close": [100, 110],
            }
        )
        for symbol in SYMBOLS
    }
    funding_time = times[-1] + BAR_DELTA
    monkeypatch.setattr(
        "scripts.capture_exp_2026_0042_paper_shadow._get_json",
        lambda *_args, **_kwargs: {
            "result": {
                "list": [
                    {
                        "fundingRateTimestamp": str(int(funding_time.timestamp() * 1000)),
                        "fundingRate": "0.001",
                    }
                ]
            }
        },
    )
    state = {
        "cash_usdt": "1000",
        "positions": {
            "LINKUSDT": {
                "quantity": "2",
                "entry_price": "100",
                "last_funding_time": times[0].isoformat(),
            }
        },
    }

    events = _apply_funding(state, frames)

    assert events[0]["event_type"] == "FUNDING"
    assert events[0]["notional"] == "220"
    assert events[0]["cash_flow"] == "-0.220"
    assert state["cash_usdt"] == "999.780"
