from decimal import Decimal

from scripts.run_exp_2026_0034 import (
    ARM_BUDGET,
    CASH_CONTROL_ARMS,
    HEDGE_ARMS,
    _compare_hedge_to_cash,
)


def test_exp_0034_uses_matching_hedge_and_cash_budgets():
    """ヘッジ群と現金保持群が同一basis予算を使うことをテストする。"""

    assert ARM_BUDGET["hedge_5pct"] == ARM_BUDGET["cash_control_5pct"]
    assert ARM_BUDGET["hedge_10pct"] == ARM_BUDGET["cash_control_10pct"]
    assert ARM_BUDGET["hedge_20pct"] == ARM_BUDGET["cash_control_20pct"]
    assert HEDGE_ARMS == {"hedge_5pct", "hedge_10pct", "hedge_20pct"}
    assert CASH_CONTROL_ARMS == {
        "cash_control_5pct",
        "cash_control_10pct",
        "cash_control_20pct",
    }


def test_exp_0034_comparison_detects_hedge_improvement():
    """ヘッジ群が現金保持群を上回る場合を判定することをテストする。"""

    hedge = {
        "metrics": {
            "final_equity": "1010",
            "max_drawdown": "-0.10",
            "total_fees": "2",
            "total_funding_cash_flow": "1",
        }
    }
    cash = {
        "metrics": {
            "final_equity": "1000",
            "max_drawdown": "-0.20",
            "total_fees": "0",
            "total_funding_cash_flow": "0",
        }
    }

    result = _compare_hedge_to_cash(hedge, cash)

    assert result["final_equity_delta_hedge_minus_cash"] == "10"
    assert result["max_drawdown_delta_hedge_minus_cash"] == "0.10"
    assert result["hedge_final_equity_better"] is True
    assert result["hedge_max_drawdown_better"] is True
    assert Decimal(result["fee_delta_hedge_minus_cash"]) == Decimal("2")
