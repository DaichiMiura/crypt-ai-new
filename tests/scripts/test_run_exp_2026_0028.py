from decimal import Decimal

from scripts.run_exp_2026_0028 import _aggregate, _compare_pair


def test_compare_pair_uses_portfolio_final_equity_and_drawdown():
    """ヘッジarmの最終資産と最大DD差をテストする。"""
    baseline = {
        "final_equity": "1000",
        "max_drawdown": "-0.30",
        "entry_count": 20,
        "total_funding_cash_flow": "-2",
        "total_fees": "10",
        "max_position_notional": "1000",
        "liquidation_count": 0,
        "index_benchmark": {"beats_benchmark": False},
    }
    candidate = {
        "final_equity": "1050",
        "max_drawdown": "-0.20",
        "entry_count": 18,
        "total_funding_cash_flow": "-1",
        "total_fees": "9",
        "max_position_notional": "1020",
        "liquidation_count": 0,
        "index_benchmark": {"beats_benchmark": False},
    }

    comparison = _compare_pair(baseline, candidate)

    assert comparison["final_equity_delta"] == "50"
    assert comparison["max_drawdown_delta"] == "0.10"
    assert comparison["entry_count_delta"] == -2
    assert comparison["max_position_notional_delta"] == "20"


def test_aggregate_counts_portfolio_improvements():
    """銘柄別ポートフォリオ改善数と中央値をテストする。"""
    comparisons = {
        "A": {
            "final_equity_delta": "50",
            "max_drawdown_delta": "0.10",
            "entry_count_delta": -2,
            "max_position_notional_delta": "20",
        },
        "B": {
            "final_equity_delta": "-5",
            "max_drawdown_delta": "-0.02",
            "entry_count_delta": 0,
            "max_position_notional_delta": "5",
        },
    }

    aggregate = _aggregate(comparisons)

    assert aggregate["symbols_improved_final_equity"] == 1
    assert aggregate["symbols_worsened_final_equity"] == 1
    assert aggregate["median_final_equity_delta"] == "22.5"
    assert aggregate["symbols_improved_max_drawdown"] == 1
