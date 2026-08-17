from decimal import Decimal

from scripts.run_exp_2026_0048 import (
    LOT_NOTIONAL,
    SIGNAL_CONFIG,
    _allocation_config,
    _decision,
)


def test_funding_carry_parameters_are_frozen():
    """Funding 3回lookback、3回更新、上下2銘柄、次足遅延を固定していることをテストする。"""

    assert (
        SIGNAL_CONFIG.lookback_events,
        SIGNAL_CONFIG.rebalance_events,
        SIGNAL_CONFIG.long_count,
        SIGNAL_CONFIG.short_count,
        SIGNAL_CONFIG.signal_delay_bars,
    ) == (3, 3, 2, 2, 1)
    assert LOT_NOTIONAL == Decimal("50")


def test_funding_carry_allocation_is_gross_neutral_and_bounded():
    """long/short両側を同額枠へ制限し、予備資金を確保することをテストする。"""

    config = _allocation_config()
    assert config.max_long_gross_notional == Decimal("200")
    assert config.max_short_gross_notional == Decimal("200")
    assert config.max_total_gross_notional == Decimal("400")
    assert config.per_symbol_max_notional == Decimal("50")
    assert config.reserve_cash == Decimal("200")


def test_funding_carry_rejects_negative_oos_and_cost_stress():
    """OOS純損益または費用stressが不採用条件ならREJECTEDになることをテストする。"""

    base = {
        "net_pnl": "-1",
        "funding_cash_flow": "1",
        "max_drawdown": "-0.1",
    }
    stress = {"net_pnl": "1", "funding_cash_flow": "1", "max_drawdown": "-0.1"}
    decision, reasons = _decision(base, stress)
    assert decision == "REJECTED"
    assert "oos_does_not_beat_cash_control" in reasons
