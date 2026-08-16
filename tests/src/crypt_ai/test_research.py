from decimal import Decimal

import pandas as pd
import pytest

from crypt_ai.research import (
    KLINE_COLUMNS,
    CostModel,
    aggregate_hourly_to_daily,
    inspect_hourly_data,
    inspect_daily_data,
    interpolate_missing_hourly_data,
    prepare_atr_trailing_exit_signals,
    prepare_bollinger_mean_reversion_signals,
    prepare_cross_sectional_momentum_long_signals,
    prepare_cross_sectional_momentum_short_signals,
    prepare_donchian_bollinger_exit_signals,
    prepare_donchian_long_short_regime_signals,
    prepare_donchian_regime_filter_signals,
    prepare_donchian_signals,
    prepare_signals,
    prepare_volatility_scaled_regime_signals,
    run_backtest,
    run_fractional_entry_backtest,
    run_long_short_backtest,
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


def test_cross_sectional_momentum_selects_worst_symbols_and_holds_until_rebalance():
    """下位モメンタム銘柄を選び、次のリバランスまでショート状態を保持することをテストする。"""
    timestamps = pd.date_range("2026-01-01", periods=8, freq="h", tz="UTC")
    frames = {
        "AAA": pd.DataFrame({"event_time": timestamps, "close": [100, 100, 100, 100, 100, 100, 100, 100]}),
        "BBB": pd.DataFrame({"event_time": timestamps, "close": [100, 90, 90, 90, 90, 90, 90, 90]}),
        "CCC": pd.DataFrame({"event_time": timestamps, "close": [100, 110, 110, 110, 110, 110, 110, 110]}),
    }

    result = prepare_cross_sectional_momentum_short_signals(
        frames, lookback_bars=1, rebalance_bars=3, short_count=1
    )

    assert result["BBB"].loc[1, "cross_sectional_rank"] == 1
    assert result["BBB"].loc[1, "short_signal_position"] == 1
    assert result["BBB"].loc[2, "short_signal_position"] == 1
    assert result["BBB"].loc[2, "desired_short_position"] == 1
    assert result["CCC"].loc[2, "short_signal_position"] == 0


def test_cross_sectional_momentum_rejects_unsynchronized_frames():
    """銘柄間で時刻が一致しない入力を拒否することをテストする。"""
    timestamps = pd.date_range("2026-01-01", periods=3, freq="h", tz="UTC")
    with pytest.raises(ValueError, match="identical timestamps"):
        prepare_cross_sectional_momentum_short_signals(
            {
                "AAA": pd.DataFrame({"event_time": timestamps, "close": [1, 2, 3]}),
                "BBB": pd.DataFrame(
                    {
                        "event_time": timestamps + pd.Timedelta(hours=1),
                        "close": [1, 2, 3],
                    }
                ),
            },
            lookback_bars=1,
            rebalance_bars=1,
            short_count=1,
        )


def test_cross_sectional_long_selects_best_symbols_on_positive_market():
    """正の市場regimeで上位モメンタム銘柄を次足からロングすることをテストする。"""

    timestamps = pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC")
    frames = {
        "AAA": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 101, 102, 103, 104]}
        ),
        "BBB": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 110, 120, 130, 140]}
        ),
        "CCC": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 105, 110, 115, 120]}
        ),
    }

    result = prepare_cross_sectional_momentum_long_signals(
        frames, lookback_bars=1, rebalance_bars=2, long_count=1
    )

    assert result["BBB"].loc[1, "cross_sectional_rank"] == 1
    assert result["BBB"].loc[1, "long_signal_position"] == 1
    assert result["BBB"].loc[2, "desired_long_position"] == 1
    assert result["AAA"].loc[2, "desired_long_position"] == 0


def test_cross_sectional_long_holds_cash_when_market_median_is_not_positive():
    """市場中央値モメンタムが非正なら上位銘柄もロングしないことをテストする。"""

    timestamps = pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC")
    frames = {
        "AAA": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 110, 110, 110]}
        ),
        "BBB": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 90, 90, 90]}
        ),
        "CCC": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 80, 80, 80]}
        ),
    }

    result = prepare_cross_sectional_momentum_long_signals(
        frames, lookback_bars=1, rebalance_bars=2, long_count=1
    )

    assert result["AAA"].loc[1, "market_regime_ok"] == False  # noqa: E712
    assert result["AAA"].loc[2, "desired_long_position"] == 0
    assert sum(result[symbol].loc[1, "long_signal_position"] for symbol in frames) == 0


def test_cross_sectional_long_early_exit_waits_until_next_rebalance():
    """モメンタム0以下の早期退出後に次のリバランスまで再選定しないことをテストする。"""

    timestamps = pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC")
    frames = {
        "AAA": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 120, 100, 130, 140]}
        ),
        "BBB": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 110, 115, 120, 125]}
        ),
        "CCC": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 105, 110, 115, 120]}
        ),
    }

    result = prepare_cross_sectional_momentum_long_signals(
        frames,
        lookback_bars=1,
        rebalance_bars=3,
        long_count=1,
        early_exit_on_nonpositive=True,
    )

    assert result["AAA"].loc[1, "long_signal_position"] == 1
    assert result["AAA"].loc[2, "momentum_early_exit_signal"] == True  # noqa: E712
    assert result["AAA"].loc[2, "long_signal_position"] == 0
    assert result["AAA"].loc[3, "long_signal_position"] == 0
    assert result["AAA"].loc[3, "desired_long_position"] == 0


def test_cross_sectional_long_market_exit_closes_all_until_next_rebalance():
    """市場中央値0以下の早期退出後に全銘柄が次のリバランスまで待つことをテストする。"""

    timestamps = pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC")
    frames = {
        "AAA": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 120, 100, 130, 140]}
        ),
        "BBB": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 110, 90, 120, 130]}
        ),
        "CCC": pd.DataFrame(
            {"event_time": timestamps, "close": [100, 105, 80, 115, 120]}
        ),
    }

    result = prepare_cross_sectional_momentum_long_signals(
        frames,
        lookback_bars=1,
        rebalance_bars=3,
        long_count=2,
        early_exit_on_nonpositive_median=True,
    )

    assert result["AAA"].loc[1, "long_signal_position"] == 1
    assert result["BBB"].loc[1, "long_signal_position"] == 1
    assert result["AAA"].loc[2, "market_early_exit_signal"] == True  # noqa: E712
    assert sum(result[symbol].loc[2, "long_signal_position"] for symbol in frames) == 0
    assert sum(result[symbol].loc[3, "long_signal_position"] for symbol in frames) == 0


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


def test_prepare_donchian_regime_filter_blocks_breakout_below_long_sma():
    """長期SMAを下回るDonchian突破の新規entryを抑制することをテストする。"""
    timestamps = pd.date_range(
        "2026-01-01T00:00:00Z", periods=201, freq="D", tz="UTC"
    )
    close = [300] * 145 + [100] * 55 + [110]
    frame = pd.DataFrame(
        {
            "event_time": timestamps,
            "open": close,
            "high": [value + 1 for value in close],
            "low": [value - 1 for value in close],
            "close": close,
        }
    )

    result = prepare_donchian_regime_filter_signals(
        frame, entry_window=55, exit_window=20, regime_window=200
    )

    assert result.loc[200, "base_signal_position"] == 1
    assert bool(result.loc[200, "regime_ok"]) is False
    assert result.loc[200, "signal_position"] == 0
    assert result.loc[201 - 1, "desired_position"] == 0


def test_prepare_donchian_regime_filter_allows_breakout_above_long_sma():
    """長期SMAを上回るDonchian突破を次日entryへ遅延することをテストする。"""
    timestamps = pd.date_range(
        "2026-01-01T00:00:00Z", periods=201, freq="D", tz="UTC"
    )
    close = [100] * 200 + [120]
    frame = pd.DataFrame(
        {
            "event_time": timestamps,
            "open": close,
            "high": [value + 1 for value in close],
            "low": [value - 1 for value in close],
            "close": close,
        }
    )

    result = prepare_donchian_regime_filter_signals(
        frame, entry_window=55, exit_window=20, regime_window=200
    )

    assert bool(result.loc[200, "regime_ok"]) is True
    assert bool(result.loc[200, "entry_signal"]) is True
    assert result.loc[200, "desired_position"] == 0
    assert result.loc[200, "signal_position"] == 1


def test_prepare_atr_exit_uses_next_open_and_ratchets_stop():
    """ATR stopを切り下げず、終値割れの次日まで保有することをテストする。"""
    close = [100, 100, 100, 100, 100, 110, 112, 115, 111, 109]
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range(
                "2026-01-01", periods=len(close), freq="D", tz="UTC"
            ),
            "open": close,
            "high": close,
            "low": [value - 2 for value in close],
            "close": close,
        }
    )

    result = prepare_atr_trailing_exit_signals(
        frame,
        entry_window=3,
        baseline_exit_window=2,
        regime_window=3,
        atr_window=2,
        atr_multiplier=1.0,
    )

    assert bool(result.loc[5, "entry_signal"]) is True
    assert result.loc[5, "desired_atr_position"] == 0
    assert result.loc[6, "desired_atr_position"] == 1
    held_stops = result.loc[6:8, "atr_trailing_stop"].dropna()
    assert held_stops.is_monotonic_increasing
    assert bool(result.loc[8, "atr_exit_signal"]) is True
    assert result.loc[8, "desired_atr_position"] == 1
    assert result.loc[9, "desired_atr_position"] == 0


def test_prepare_atr_exit_waits_for_new_base_entry_after_exit():
    """ATR退出後に基礎状態がlongのままなら再entryしないことをテストする。"""
    close = [100, 100, 100, 100, 100, 110, 112, 115, 111, 112, 113]
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range(
                "2026-01-01", periods=len(close), freq="D", tz="UTC"
            ),
            "open": close,
            "high": close,
            "low": [value - 2 for value in close],
            "close": close,
        }
    )

    result = prepare_atr_trailing_exit_signals(
        frame,
        entry_window=3,
        baseline_exit_window=2,
        regime_window=3,
        atr_window=2,
        atr_multiplier=1.0,
    )

    assert result.loc[9, "signal_position"] == 1
    assert result.loc[9, "desired_atr_position"] == 0
    assert result.loc[10, "desired_atr_position"] == 0


def test_prepare_long_short_signals_detects_short_breakout_below_long_sma():
    """長期SMAを下回るDonchian安値割れをshort entryへ遅延することをテストする。"""
    timestamps = pd.date_range(
        "2026-01-01T00:00:00Z", periods=201, freq="D", tz="UTC"
    )
    close = [300] * 200 + [90]
    frame = pd.DataFrame(
        {
            "event_time": timestamps,
            "open": close,
            "high": [value + 1 for value in close],
            "low": [value - 1 for value in close],
            "close": close,
        }
    )

    result = prepare_donchian_long_short_regime_signals(
        frame, entry_window=55, exit_window=20, regime_window=200
    )

    assert bool(result.loc[200, "short_entry_signal"]) is True
    assert result.loc[200, "short_signal_position"] == -1
    assert result.loc[200, "signal_position"] == -1
    assert result.loc[200, "desired_position"] == 0


def test_long_short_long_leg_matches_existing_regime_filter():
    """long/short実装のlong系列がEXP-2026-0008と一致することをテストする。"""
    close = [100] * 200 + list(range(101, 151)) + list(range(150, 89, -1))
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range(
                "2025-01-01", periods=len(close), freq="D", tz="UTC"
            ),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
        }
    )

    expected = prepare_donchian_regime_filter_signals(frame)
    actual = prepare_donchian_long_short_regime_signals(frame)

    pd.testing.assert_series_equal(
        actual["long_signal_position"],
        expected["signal_position"],
        check_names=False,
    )
    pd.testing.assert_series_equal(
        actual["desired_long_position"],
        expected["desired_position"],
        check_names=False,
    )


def test_run_long_short_backtest_accounts_for_short_cover():
    """short売却と買い戻しの損益および手数料を計上することをテストする。"""
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range(
                "2026-01-01", periods=3, freq="D", tz="UTC"
            ),
            "open": [100, 80, 80],
            "close": [100, 80, 80],
            "desired_position": [-1, -1, 0],
        }
    )

    equity, trades = run_long_short_backtest(
        frame,
        CostModel(
            fee_rate=Decimal("0.001"),
            round_trip_spread=Decimal("0"),
            slippage_per_fill=Decimal("0"),
        ),
        initial_cash=Decimal("1000"),
    )

    assert list(trades["side"]) == ["SELL_SHORT", "BUY_TO_COVER"]
    assert float(equity.iloc[-1]["equity"]) > 1000


def test_prepare_volatility_scaled_signals_delays_reduced_entry_exposure():
    """高ボラティリティentryの縮小比率を次日始値へ遅延することをテストする。"""
    close = [100, 115, 90, 120, 80, 130, 131]
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range(
                "2026-01-01", periods=len(close), freq="D", tz="UTC"
            ),
            "open": close,
            "high": close,
            "low": [value - 1 for value in close],
            "close": close,
        }
    )

    result = prepare_volatility_scaled_regime_signals(
        frame,
        entry_window=4,
        exit_window=2,
        regime_window=5,
        volatility_window=3,
        target_annual_volatility=0.40,
    )

    assert bool(result.loc[5, "entry_signal"]) is True
    assert 0 < result.loc[5, "entry_exposure"] < 1
    assert result.loc[5, "desired_exposure"] == 0
    assert result.loc[6, "desired_exposure"] == result.loc[5, "entry_exposure"]


def test_run_fractional_backtest_keeps_uninvested_cash():
    """50%のentryで残りの現金を保持し、exit損益を計上することをテストする。"""
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range(
                "2026-01-01", periods=3, freq="D", tz="UTC"
            ),
            "open": [100, 120, 120],
            "close": [100, 120, 120],
            "desired_exposure": [0.5, 0.5, 0.0],
        }
    )

    equity, trades = run_fractional_entry_backtest(
        frame,
        CostModel(
            fee_rate=Decimal("0"),
            round_trip_spread=Decimal("0"),
            slippage_per_fill=Decimal("0"),
        ),
    )

    assert list(trades["side"]) == ["BUY", "SELL"]
    assert Decimal(equity.iloc[0]["cash"]) == Decimal("500")
    assert Decimal(equity.iloc[-1]["equity"]) == Decimal("1100")


def test_run_fractional_backtest_rejects_resize_while_holding():
    """保有中に投資比率を書き換える入力を拒否することをテストする。"""
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range(
                "2026-01-01", periods=2, freq="D", tz="UTC"
            ),
            "open": [100, 100],
            "close": [100, 100],
            "desired_exposure": [0.5, 0.4],
        }
    )

    with pytest.raises(ValueError, match="changed while position was open"):
        run_fractional_entry_backtest(
            frame,
            CostModel(
                fee_rate=Decimal("0"),
                round_trip_spread=Decimal("0"),
                slippage_per_fill=Decimal("0"),
            ),
        )


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


def test_prepare_donchian_bollinger_exit_requires_lower_band_arm():
    """Donchian買い後に下側バンド割れを経て中心線で退出することをテストする。"""
    timestamps = pd.date_range(
        "2026-01-01T00:00:00Z", periods=65, freq="D", tz="UTC"
    )
    close = [100] * 55 + [120, 100, 80, 100, 100] + [100] * 5
    frame = pd.DataFrame(
        {
            "event_time": timestamps,
            "open": close,
            "high": [value + 1 for value in close],
            "close": close,
        }
    )

    result = prepare_donchian_bollinger_exit_signals(
        frame, entry_window=55, band_window=3, std_multiplier=1.0
    )

    assert bool(result.loc[55, "entry_signal"]) is True
    assert result.loc[55, "desired_position"] == 0
    assert result.loc[56, "desired_position"] == 1
    assert bool(result.loc[57, "overlay_armed"]) is True
    assert bool(result.loc[58, "exit_signal"]) is True
    assert result.loc[58, "desired_position"] == 1
    assert result.loc[59, "desired_position"] == 0
