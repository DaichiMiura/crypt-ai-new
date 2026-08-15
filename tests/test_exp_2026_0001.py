from decimal import Decimal

import pandas as pd

from crypt_ai.exp_2026_0001 import (
    CostModel,
    inspect_hourly_data,
    prepare_signals,
    run_backtest,
)


def test_prepare_signals_uses_previous_closed_bar():
    """SMAシグナルを次のバーに遅延させることをテストする。"""
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range("2026-01-01", periods=6, freq="h", tz="UTC"),
            "open": [1, 1, 1, 1, 1, 1],
            "close": [1, 1, 1, 2, 3, 4],
        }
    )
    result = prepare_signals(frame, fast_window=2, slow_window=3)
    assert result.loc[3, "desired_position"] == 0
    assert result.loc[4, "desired_position"] == 1


def test_run_backtest_charges_fee_on_both_sides():
    """買いと売りの両方に手数料を計上することをテストする。"""
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC"),
            "open": [100, 100, 110, 110],
            "close": [100, 100, 110, 110],
            "desired_position": [0, 1, 1, 0],
        }
    )
    equity, trades = run_backtest(
        frame,
        CostModel(
            fee_rate=Decimal("0.001"),
            round_trip_spread=Decimal("0"),
            slippage_per_fill=Decimal("0"),
        ),
        initial_cash=Decimal("1000"),
    )
    assert list(trades["side"]) == ["BUY", "SELL"]
    assert len(trades) == 2
    assert float(equity.iloc[-1]["equity"]) < 1100


def test_inspect_hourly_data_counts_gaps():
    """1時間足の欠損区間を数えることをテストする。"""
    frame = pd.DataFrame(
        {
            "event_time": pd.to_datetime(
                ["2026-01-01T00:00:00Z", "2026-01-01T02:00:00Z"]
            )
        }
    )
    result = inspect_hourly_data(frame)
    assert result["missing_segments"] == 1
    assert result["missing_intervals"] == 1
