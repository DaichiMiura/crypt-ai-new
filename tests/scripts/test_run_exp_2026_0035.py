from decimal import Decimal

from scripts.run_exp_2026_0035 import (
    ARM_LONG_CAPS,
    ARM_MAX_POSITIONS,
    LOOKBACK_BARS,
    REBALANCE_BARS,
    _allocation_config,
    _compare,
)


def test_exp_0035_fixes_ranking_windows_and_position_caps():
    """30日順位、7日更新、上位1・2銘柄の配分上限を固定することをテストする。"""

    assert LOOKBACK_BARS == 360
    assert REBALANCE_BARS == 84
    assert ARM_LONG_CAPS["momentum_top1"] == Decimal("200")
    assert ARM_LONG_CAPS["momentum_top2"] == Decimal("400")
    assert ARM_MAX_POSITIONS["momentum_top1"] == 1
    assert ARM_MAX_POSITIONS["momentum_top2"] == 2


def test_exp_0035_allocation_uses_fixed_two_hundred_usdt_lot():
    """相対モメンタムarmが1銘柄200 USDTの固定ロットを使うことをテストする。"""

    config = _allocation_config("momentum_top2")

    assert config.lot_notional == Decimal("200")
    assert config.per_symbol_max_notional == Decimal("200")
    assert config.max_long_gross_notional == Decimal("400")
    assert config.reserve_cash == Decimal("200")


def test_exp_0035_comparison_detects_candidate_improvements():
    """候補が現行ロングの最終資産と最大DDを改善した場合を判定することをテストする。"""

    baseline = {"final_equity": "900", "max_drawdown": "-0.20"}
    candidate = {"final_equity": "950", "max_drawdown": "-0.10"}

    result = _compare(baseline, candidate)

    assert result["final_equity_delta"] == "50"
    assert result["max_drawdown_delta"] == "0.10"
    assert result["final_equity_improved"] is True
    assert result["max_drawdown_improved"] is True
