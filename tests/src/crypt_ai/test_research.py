from decimal import Decimal

import pandas as pd

from crypt_ai.research import (
    KLINE_COLUMNS,
    CostModel,
    aggregate_hourly_to_daily,
    inspect_hourly_data,
    inspect_daily_data,
    interpolate_missing_hourly_data,
    prepare_bollinger_mean_reversion_signals,
    prepare_donchian_signals,
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


def test_interpolate_missing_hourly_data_marks_synthetic_rows():
    """内部欠損を線形補間し、合成行を識別できることをテストする。"""
    timestamps = pd.to_datetime(
        ["2026-01-01T00:00:00Z", "2026-01-01T02:00:00Z"]
    )
    rows = []
    for index, timestamp in enumerate(timestamps):
        open_time = int(timestamp.timestamp() * 1000)
        rows.append(
            [
                open_time,
                100 + index * 10,
                105 + index * 10,
                95 + index * 10,
                100 + index * 10,
                10,
                open_time + 3_599_999,
                1_000,
                10,
                5,
                500,
                "0",
            ]
        )
    frame = pd.DataFrame(rows, columns=KLINE_COLUMNS)
    frame["event_time"] = timestamps

    result = interpolate_missing_hourly_data(frame)

    assert len(result) == 3
    assert bool(result.loc[1, "is_interpolated"]) is True
    assert result.loc[1, "close"] == 105
    assert result.loc[1, "high"] >= result.loc[1, "close"]
    assert inspect_hourly_data(result)["missing_intervals"] == 0


def test_aggregate_hourly_to_daily_requires_complete_days():
    """24本の1時間足を1日へ集約し、日足品質を検査できることをテストする。"""
    timestamps = pd.date_range(
        "2026-01-01T00:00:00Z", periods=48, freq="h", tz="UTC"
    )
    frame = pd.DataFrame(
        {
            "event_time": timestamps,
            "open": range(48),
            "high": [value + 1 for value in range(48)],
            "low": range(48),
            "close": range(1, 49),
            "volume": [1] * 48,
            "is_interpolated": [False] * 47 + [True],
        }
    )

    result = aggregate_hourly_to_daily(frame)

    assert len(result) == 2
    assert result.loc[0, "open"] == 0
    assert result.loc[0, "close"] == 24
    assert bool(result.loc[1, "is_interpolated"]) is True
    assert inspect_daily_data(result)["missing_intervals"] == 0


def test_prepare_donchian_signals_delays_breakout_to_next_day():
    """Donchian突破を次の日足始値へ遅延させることをテストする。"""
    timestamps = pd.date_range(
        "2026-01-01T00:00:00Z", periods=60, freq="D", tz="UTC"
    )
    close = [100 + index for index in range(60)]
    close[55] = 160
    frame = pd.DataFrame(
        {
            "event_time": timestamps,
            "open": close,
            "high": [value + 1 for value in close],
            "low": [value - 1 for value in close],
            "close": close,
        }
    )

    result = prepare_donchian_signals(frame, entry_window=55, exit_window=20)

    assert result.loc[55, "signal_position"] == 1
    assert result.loc[55, "desired_position"] == 0
    assert result.loc[56, "desired_position"] == 1


def test_prepare_bollinger_signals_delays_entry_and_exit_to_next_day():
    """ボリンジャーバンドの買いと中心線決済を次日始値へ遅延させることをテストする。"""
    timestamps = pd.date_range(
        "2026-01-01T00:00:00Z", periods=25, freq="D", tz="UTC"
    )
    close = [100] * 20 + [80, 100, 100, 100, 100]
    frame = pd.DataFrame(
        {
            "event_time": timestamps,
            "open": close,
            "close": close,
        }
    )

    result = prepare_bollinger_mean_reversion_signals(
        frame, window=20, std_multiplier=2.0
    )

    assert result.loc[20, "signal_position"] == 1
    assert result.loc[20, "desired_position"] == 0
    assert result.loc[21, "signal_position"] == 0
    assert result.loc[21, "desired_position"] == 1
    assert result.loc[22, "desired_position"] == 0
