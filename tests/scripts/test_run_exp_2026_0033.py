from decimal import Decimal

import pandas as pd
import pytest

from scripts.run_exp_2026_0033 import (
    ARM_BASIS_BUDGET,
    ARM_MAX_PAIRS,
    INDEX_BENCHMARK,
    PAIR_NOTIONAL,
    _benchmark,
    _compare_to_baseline,
    _validate_common_timestamps,
)


def test_exp_0033_arms_fix_basis_budgets_and_pair_caps():
    """basis枠を0・5・10・20%と同時保有上限へ固定することをテストする。"""

    assert ARM_BASIS_BUDGET == {
        "long_only": Decimal("0"),
        "hedge_5pct": Decimal("50"),
        "hedge_10pct": Decimal("100"),
        "hedge_20pct": Decimal("200"),
    }
    assert ARM_MAX_PAIRS == {
        "long_only": 0,
        "hedge_5pct": 1,
        "hedge_10pct": 2,
        "hedge_20pct": 4,
    }
    assert PAIR_NOTIONAL == Decimal("24")


def test_exp_0033_benchmark_is_four_year_compound_return():
    """4年間の年率10%複利ベンチマークを計算することをテストする。"""

    result = _benchmark(Decimal("1000"))

    assert INDEX_BENCHMARK == Decimal("1464.10000000")
    assert result["benchmark_final_equity"] == "1464.10000000"
    assert result["beats_benchmark"] is False


def test_exp_0033_comparison_marks_both_improvements():
    """long_onlyに対する最終資産と最大DDの改善を判定することをテストする。"""

    baseline = {
        "final_equity": "1000",
        "max_drawdown": "-0.20",
        "total_fees": "1",
        "total_funding_cash_flow": "-2",
    }
    candidate = {
        "final_equity": "1010",
        "max_drawdown": "-0.10",
        "total_fees": "2",
        "total_funding_cash_flow": "-1",
    }

    result = _compare_to_baseline(baseline, candidate)

    assert result["final_equity_improved"] is True
    assert result["max_drawdown_improved"] is True
    assert result["final_equity_delta"] == "10"
    assert result["max_drawdown_delta"] == "0.10"


def test_exp_0033_common_timestamp_validation_rejects_mismatch():
    """銘柄間で評価時刻が異なる入力を拒否することをテストする。"""

    first = pd.DataFrame({"event_time": pd.to_datetime(["2022-01-01T00:00:00Z"])})
    second = pd.DataFrame({"event_time": pd.to_datetime(["2022-01-01T02:00:00Z"])})

    with pytest.raises(ValueError, match="timestamps differ"):
        _validate_common_timestamps({"A": first, "B": second}, name="test")
