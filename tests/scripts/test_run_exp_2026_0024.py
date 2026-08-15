from decimal import Decimal

from scripts.run_exp_2026_0024 import (
    SHORT_COMPOUND_CONTROL,
    SHORT_COMPOUND_VARIANT,
    SHORT_ENTRY_LOT_COUNTS,
    SHORT_MAX_ENTRY_LOT_COUNT,
    _aggregate_pair,
    _compare_pair,
)


def test_short_compounding_arms_are_preregistered():
    """ショート複利controlとvariantの設定をテストする。"""
    assert SHORT_COMPOUND_CONTROL is False
    assert SHORT_COMPOUND_VARIANT is True
    assert SHORT_ENTRY_LOT_COUNTS == (1, 1, 1, 1)
    assert SHORT_MAX_ENTRY_LOT_COUNT == 4


def test_compare_pair_reports_compounding_exposure_delta():
    """候補と基準の最終資産、DD、想定元本差をテストする。"""
    baseline = {
        "final_equity": "250",
        "max_drawdown": "-0.20",
        "entry_count": 4,
        "total_funding_cash_flow": "-1",
        "max_position_quantity": "10",
        "max_position_notional": "200",
        "liquidation_count": 0,
        "index_benchmark": {"beats_benchmark": False},
    }
    candidate = {
        "final_equity": "260",
        "max_drawdown": "-0.15",
        "entry_count": 5,
        "total_funding_cash_flow": "-2",
        "max_position_quantity": "12",
        "max_position_notional": "240",
        "liquidation_count": 1,
        "index_benchmark": {"beats_benchmark": False},
    }

    comparison = _compare_pair(baseline, candidate)

    assert comparison["final_equity_delta"] == "10"
    assert comparison["max_drawdown_delta"] == "0.05"
    assert comparison["max_position_notional_delta"] == "40"
    assert comparison["liquidation_count_delta"] == 1


def test_aggregate_pair_counts_improvements_and_worsening_drawdown():
    """銘柄別の改善数と最大DD差の中央値をテストする。"""
    comparisons = {
        "A": {
            "final_equity_delta": "10",
            "max_drawdown_delta": "0.05",
            "entry_count_delta": 1,
            "funding_cash_flow_delta": "-1",
            "max_position_notional_delta": "20",
            "liquidation_count_delta": 0,
        },
        "B": {
            "final_equity_delta": "-2",
            "max_drawdown_delta": "-0.03",
            "entry_count_delta": 0,
            "funding_cash_flow_delta": "0",
            "max_position_notional_delta": "-5",
            "liquidation_count_delta": 1,
        },
    }

    aggregate = _aggregate_pair(comparisons)

    assert aggregate["symbols_improved_final_equity"] == 1
    assert aggregate["symbols_worsened_final_equity"] == 1
    assert aggregate["symbols_improved_max_drawdown"] == 1
    assert aggregate["symbols_worsened_max_drawdown"] == 1
    assert aggregate["median_max_position_notional_delta"] == "7.5"
    assert aggregate["sum_liquidation_count_delta"] == 1
