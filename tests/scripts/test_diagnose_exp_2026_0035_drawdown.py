"""EXP-2026-0035ドローダウン診断をテストする。"""

from decimal import Decimal

import pandas as pd

from scripts.diagnose_exp_2026_0035_drawdown import (
    _drawdown_episode,
    _reconstruct_trades,
)


def test_drawdown_episode_finds_peak_and_trough() -> None:
    """最大DDの起点と底を特定できることをテストする。"""

    frame = pd.DataFrame(
        {
            "event_time": pd.date_range("2026-01-01", periods=5, tz="UTC"),
            "equity": [100, 120, 90, 110, 121],
        }
    )

    result = _drawdown_episode(frame)

    assert result["peak_equity"] == Decimal("120")
    assert result["trough_equity"] == Decimal("90")
    assert result["max_drawdown"] == Decimal("-0.25")
    assert result["recovery_time"] == frame.iloc[4]["event_time"]


def test_reconstruct_trades_includes_fees_and_funding() -> None:
    """取引純損益へ売買手数料とFundingを含めることをテストする。"""

    events = pd.DataFrame(
        [
            {
                "event_time": "2026-01-01T00:00:00Z",
                "event_type": "ENTRY",
                "symbol": "AAAUSDT",
                "notional": "200",
                "fee": "0.12",
                "funding_delta": None,
            },
            {
                "event_time": "2026-01-01T08:00:00Z",
                "event_type": "FUNDING",
                "symbol": "AAAUSDT",
                "notional": "205",
                "fee": None,
                "funding_delta": "-0.02",
            },
            {
                "event_time": "2026-01-02T00:00:00Z",
                "event_type": "EXIT",
                "symbol": "AAAUSDT",
                "notional": "210",
                "fee": "0.13",
                "funding_delta": None,
            },
        ]
    )

    trades = _reconstruct_trades(events)

    assert trades.iloc[0]["net_pnl"] == Decimal("9.73")
