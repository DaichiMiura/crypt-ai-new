from decimal import Decimal

from scripts.run_exp_2026_0029 import ARM_SPECS, _aggregate, _compare_pair


def test_hedge_allocations_are_preregistered():
    """5%、10%、20%のショート配分をテストする。"""
    assert ARM_SPECS["hedge_5pct"] == (Decimal("937.5"), Decimal("62.5"))
    assert ARM_SPECS["hedge_10pct"] == (Decimal("875"), Decimal("125"))
    assert ARM_SPECS["hedge_20pct"] == (Decimal("750"), Decimal("250"))


def test_compare_pair_reports_hedge_delta():
    """ヘッジ配分とlong-onlyの最終資産・DD差をテストする。"""
    baseline = {
        "final_equity": "1000",
        "max_drawdown": "-0.30",
        "entry_count": 4,
        "total_funding_cash_flow": "0",
        "total_fees": "4",
        "max_position_notional": "1000",
        "liquidation_count": 0,
        "index_benchmark": {"beats_benchmark": False},
    }
    candidate = {
        "final_equity": "1020",
        "max_drawdown": "-0.25",
        "entry_count": 20,
        "total_funding_cash_flow": "-1",
        "total_fees": "5",
        "max_position_notional": "900",
        "liquidation_count": 0,
        "index_benchmark": {"beats_benchmark": False},
    }

    comparison = _compare_pair(baseline, candidate)

    assert comparison["final_equity_delta"] == "20"
    assert comparison["max_drawdown_delta"] == "0.05"
    assert comparison["max_position_notional_delta"] == "-100"
    assert comparison["entry_count_delta"] == 16


def test_aggregate_counts_allocation_improvements():
    """配分armの改善銘柄数と最大DD差の中央値をテストする。"""
    comparisons = {
        "A": {
            "final_equity_delta": "20",
            "max_drawdown_delta": "0.05",
            "entry_count_delta": 10,
            "max_position_notional_delta": "-50",
        },
        "B": {
            "final_equity_delta": "-5",
            "max_drawdown_delta": "-0.01",
            "entry_count_delta": 8,
            "max_position_notional_delta": "-20",
        },
    }

    aggregate = _aggregate(comparisons)

    assert aggregate["symbols_improved_final_equity"] == 1
    assert aggregate["symbols_worsened_final_equity"] == 1
    assert aggregate["median_max_drawdown_delta"] == "0.02"
    assert aggregate["median_max_position_notional_delta"] == "-35"
