from decimal import Decimal

from scripts.run_exp_2026_0030 import (
    ARM_SHORT_CAPS,
    ENTRY_WINDOW,
    EXIT_WINDOW,
    REGIME_WINDOW,
    _allocation_config,
    _benchmark,
)


def test_allocation_backtest_arms_keep_fixed_total_cap():
    """各armがショート枠を変えても合計上限と1ロットを固定することをテストする。"""
    assert ARM_SHORT_CAPS["hedge_5pct"] == Decimal("50")
    config = _allocation_config(ARM_SHORT_CAPS["hedge_20pct"])
    assert config.max_total_gross_notional == Decimal("800")
    assert config.max_long_gross_notional == Decimal("600")
    assert config.lot_notional == Decimal("50")


def test_signal_windows_are_time_scaled_to_two_hour_bars():
    """55日・20日・200日を2時間足本数へ固定していることをテストする。"""
    assert (ENTRY_WINDOW, EXIT_WINDOW, REGIME_WINDOW) == (660, 240, 2400)


def test_benchmark_uses_four_year_compounding():
    """年率10%を4年間複利計算することをテストする。"""
    result = _benchmark(Decimal("1000"))
    assert result["benchmark_final_equity"] == "1464.10000000"
    assert result["beats_benchmark"] is False
