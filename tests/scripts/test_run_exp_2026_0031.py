from decimal import Decimal

from scripts.run_exp_2026_0031 import (
    ARM_SHORT_CAPS,
    LOOKBACK_BARS,
    REBALANCE_BARS,
    SHORT_COUNT,
    _allocation_config,
    _benchmark,
)


def test_cross_sectional_parameters_are_fixed_before_backtest():
    """30日lookback、7日更新、下位2銘柄を固定していることをテストする。"""
    assert (LOOKBACK_BARS, REBALANCE_BARS, SHORT_COUNT) == (360, 84, 2)
    assert ARM_SHORT_CAPS["short_10pct"] == Decimal("100")


def test_short_arm_disables_long_and_keeps_lot_constraints():
    """ショートarmがロングを無効にし、固定ロットと上限を維持することをテストする。"""
    config = _allocation_config(Decimal("200"))
    assert config.max_long_gross_notional == Decimal("0")
    assert config.max_short_gross_notional == Decimal("200")
    assert config.max_total_gross_notional == Decimal("200")
    assert config.lot_notional == Decimal("50")


def test_benchmark_uses_four_year_compounding():
    """年率10%を4年間複利計算することをテストする。"""
    result = _benchmark(Decimal("1000"))
    assert result["benchmark_final_equity"] == "1464.10000000"
    assert result["beats_benchmark"] is False
