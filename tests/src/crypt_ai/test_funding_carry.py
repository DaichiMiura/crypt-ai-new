import pandas as pd
import pytest

from crypt_ai.funding_carry import (
    FundingCarrySignalConfig,
    SelectiveFundingCarrySignalConfig,
    prepare_cross_sectional_funding_carry_signals,
    prepare_selective_funding_carry_signals,
)


def _frames(rates_by_symbol: dict[str, list[float]], events: list[bool]) -> dict[str, pd.DataFrame]:
    """テスト用の同期した価格・Fundingフレームを作る。"""

    timestamps = pd.date_range(
        "2026-01-01T00:00:00Z", periods=len(events), freq="2h"
    )
    return {
        symbol: pd.DataFrame(
            {
                "event_time": timestamps,
                "open": [100.0] * len(events),
                "close": [100.0] * len(events),
                "funding_rate": rates,
                "funding_event": events,
            }
        )
        for symbol, rates in rates_by_symbol.items()
    }


def test_signal_uses_only_prior_funding_and_delays_one_bar():
    """現在Fundingを順位へ混ぜず、次の2時間足へシグナルを遅延することをテストする。"""

    frames = _frames(
        {
            "AAA": [0.01, 0.01, 0.01, 0.90, 0.90],
            "BBB": [0.02, 0.02, 0.02, 0.01, 0.01],
            "CCC": [0.03, 0.03, 0.03, 0.02, 0.02],
            "DDD": [0.04, 0.04, 0.04, 0.03, 0.03],
        },
        [True] * 5,
    )
    result = prepare_cross_sectional_funding_carry_signals(
        frames,
        FundingCarrySignalConfig(
            lookback_events=3,
            rebalance_events=3,
            long_count=1,
            short_count=1,
            signal_delay_bars=1,
        ),
    )

    assert result["AAA"].loc[3, "funding_signal_event"] == True  # noqa: E712
    assert result["AAA"].loc[3, "desired_long_position"] == 0
    assert result["AAA"].loc[4, "desired_long_position"] == 1
    assert result["DDD"].loc[4, "desired_short_position"] == 1


def test_signal_start_keeps_pre_start_positions_flat():
    """開始時刻より前に計算できるシグナルを評価期間へ持ち込まないことをテストする。"""

    frames = _frames(
        {symbol: [0.01 + index * 0.01] * 6 for index, symbol in enumerate(["AAA", "BBB", "CCC", "DDD"])},
        [True] * 6,
    )
    result = prepare_cross_sectional_funding_carry_signals(
        frames,
        FundingCarrySignalConfig(long_count=1, short_count=1),
        start_trading_at=pd.Timestamp("2026-01-01T08:00:00Z"),
    )

    assert result["AAA"].loc[:4, "desired_long_position"].sum() == 0
    assert result["DDD"].loc[:4, "desired_short_position"].sum() == 0


def test_signal_rejects_unsynchronized_funding_events():
    """銘柄間でFundingイベント時刻が異なる入力を拒否することをテストする。"""

    frames = _frames(
        {symbol: [0.01] * 4 for symbol in ["AAA", "BBB", "CCC", "DDD"]},
        [True] * 4,
    )
    frames["DDD"].loc[1, "funding_event"] = False
    with pytest.raises(ValueError, match="identical funding events"):
        prepare_cross_sectional_funding_carry_signals(
            frames, FundingCarrySignalConfig(long_count=1, short_count=1)
        )


def test_signal_rejects_overlapping_long_short_counts():
    """longとshortの対象数がユニバースを超える設定を拒否することをテストする。"""

    frames = _frames(
        {symbol: [0.01] * 4 for symbol in ["AAA", "BBB", "CCC"]},
        [True] * 4,
    )
    with pytest.raises(ValueError, match="exceeds symbol count"):
        prepare_cross_sectional_funding_carry_signals(
            frames,
            FundingCarrySignalConfig(long_count=2, short_count=2),
        )


def test_selective_signal_uses_past_signs_beta_and_cost_gate():
    """現在値を除くFunding符号、過去β、費用edgeで次バーへ予約することをテストする。"""

    events = [True] * 7
    frames = _frames(
        {
            "AAA": [-0.02, -0.02, -0.02, -0.02, 0.90, 0.90, 0.90],
            "BBB": [-0.01, -0.01, -0.01, -0.01, 0.80, 0.80, 0.80],
            "CCC": [0.01, 0.01, 0.01, 0.01, -0.80, -0.80, -0.80],
            "DDD": [0.02, 0.02, 0.02, 0.02, -0.90, -0.90, -0.90],
        },
        events,
    )
    for frame in frames.values():
        frame["close"] = [100, 101, 100, 102, 101, 103, 102]
    result = prepare_selective_funding_carry_signals(
        frames,
        SelectiveFundingCarrySignalConfig(
            lookback_events=2,
            holding_events=2,
            rebalance_events=2,
            long_count=2,
            short_count=2,
            signal_delay_bars=1,
            beta_window_bars=3,
            max_beta_gap=0.01,
            minimum_projected_carry=0.01,
        ),
    )

    assert result["AAA"].loc[4, "funding_gate_reason"] == "accepted"
    assert result["AAA"].loc[4, "desired_long_position"] == 0
    assert result["AAA"].loc[5, "desired_long_position"] == 1
    assert result["BBB"].loc[5, "desired_long_position"] == 1
    assert result["CCC"].loc[5, "desired_short_position"] == 1
    assert result["DDD"].loc[5, "desired_short_position"] == 1


def test_selective_signal_stays_flat_below_cost_threshold():
    """絶対符号が揃っても予測carry不足ならcashを維持することをテストする。"""

    frames = _frames(
        {
            "AAA": [-0.0001] * 7,
            "BBB": [-0.0001] * 7,
            "CCC": [0.0001] * 7,
            "DDD": [0.0001] * 7,
        },
        [True] * 7,
    )
    for frame in frames.values():
        frame["close"] = [100, 101, 100, 102, 101, 103, 102]
    result = prepare_selective_funding_carry_signals(
        frames,
        SelectiveFundingCarrySignalConfig(
            lookback_events=2,
            holding_events=2,
            rebalance_events=2,
            long_count=2,
            short_count=2,
            beta_window_bars=3,
            minimum_projected_carry=0.0048,
        ),
    )

    assert result["AAA"].loc[4, "funding_gate_reason"] == "projected_carry_below_threshold"
    assert sum(result["AAA"]["desired_long_position"]) == 0
    assert sum(result["DDD"]["desired_short_position"]) == 0
