from decimal import Decimal

from scripts.run_exp_2026_0015 import _compare_index_benchmark


def test_compare_index_benchmark_uses_four_year_compounding():
    """年率10%を4年間複利にした46.41%基準を計算することをテストする。"""
    result = _compare_index_benchmark(
        initial_equity=Decimal("1000"), final_equity=Decimal("1464.1")
    )

    assert Decimal(result["cumulative_return"]) == Decimal("0.4641")
    assert Decimal(result["benchmark_final_equity"]) == Decimal("1464.1")
    assert result["beats_benchmark"] is True


def test_compare_index_benchmark_rejects_below_hurdle():
    """基準未達の最終資産を不合格とすることをテストする。"""
    result = _compare_index_benchmark(
        initial_equity=Decimal("1000"), final_equity=Decimal("1464.099")
    )

    assert result["beats_benchmark"] is False
