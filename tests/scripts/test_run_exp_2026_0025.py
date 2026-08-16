from decimal import Decimal

from scripts.run_exp_2026_0025 import (
    SHORT_BREAKOUT_ATR_BARS,
    SHORT_BREAKOUT_DONCHIAN_BARS,
    SHORT_BREAKOUT_ENTRY_LOTS,
    SHORT_BREAKOUT_MAX_HOLDING_BARS,
    SHORT_BREAKOUT_SMA_BARS,
    SHORT_BREAKOUT_STOP_ATR,
    _aggregate_pairs,
    _compare_pair,
)


def test_short_breakout_parameters_are_preregistered():
    """下落ブレイクのSMA、Donchian、ATR、保有期間、ロット条件をテストする。"""
    assert SHORT_BREAKOUT_SMA_BARS == 2400
    assert SHORT_BREAKOUT_DONCHIAN_BARS == 240
    assert SHORT_BREAKOUT_ATR_BARS == 240
    assert SHORT_BREAKOUT_STOP_ATR == Decimal("3")
    assert SHORT_BREAKOUT_MAX_HOLDING_BARS == 168
    assert SHORT_BREAKOUT_ENTRY_LOTS == 4


def test_compare_pair_reports_breakout_minus_void_difference():
    """下落ブレイク候補とVOID式基準の差分をテストする。"""
    baseline = {
        "final_equity": "240",
        "max_drawdown": "-0.30",
        "entry_count": 10,
        "total_funding_cash_flow": "-2",
        "max_position_notional": "210",
        "liquidation_count": 1,
        "index_benchmark": {"beats_benchmark": False},
    }
    candidate = {
        "final_equity": "250",
        "max_drawdown": "-0.20",
        "entry_count": 4,
        "total_funding_cash_flow": "-1",
        "max_position_notional": "200",
        "liquidation_count": 0,
        "index_benchmark": {"beats_benchmark": False},
    }

    comparison = _compare_pair(baseline, candidate)

    assert comparison["final_equity_delta"] == "10"
    assert comparison["max_drawdown_delta"] == "0.10"
    assert comparison["entry_count_delta"] == -6
    assert comparison["max_position_notional_delta"] == "-10"


def test_aggregate_pairs_counts_better_final_equity():
    """銘柄別の最終資産改善数と中央値をテストする。"""
    comparisons = {
        "A": {
            "final_equity_delta": "5",
            "max_drawdown_delta": "0.03",
            "entry_count_delta": -1,
            "max_position_notional_delta": "-10",
            "liquidation_count_delta": -1,
        },
        "B": {
            "final_equity_delta": "-2",
            "max_drawdown_delta": "-0.01",
            "entry_count_delta": 2,
            "max_position_notional_delta": "5",
            "liquidation_count_delta": 0,
        },
    }

    aggregate = _aggregate_pairs(comparisons)

    assert aggregate["symbols_improved_final_equity"] == 1
    assert aggregate["symbols_worsened_final_equity"] == 1
    assert aggregate["median_final_equity_delta"] == "1.5"
    assert aggregate["sum_liquidation_count_delta"] == -1
