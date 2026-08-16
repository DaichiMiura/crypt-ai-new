from decimal import Decimal

from scripts.run_exp_2026_0038 import (
    ARMS,
    _allocation_config,
)


def test_exp_0038_keeps_allocation_equal_between_exit_arms():
    """週次退出と市場中央値退出でロット・上限・同時保有数を固定することをテストする。"""

    assert ARMS == ("cash_control", "weekly_exit", "market_median_zero_exit")
    weekly = _allocation_config("weekly_exit")
    market = _allocation_config("market_median_zero_exit")

    assert weekly == market
    assert market.lot_notional == Decimal("200")
    assert market.max_long_gross_notional == Decimal("400")
    assert market.max_concurrent_long_positions == 2
