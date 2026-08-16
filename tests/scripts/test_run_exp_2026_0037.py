from decimal import Decimal

from scripts.run_exp_2026_0037 import (
    ARMS,
    LOT_NOTIONAL,
    TOTAL_CAP,
    _allocation_config,
)


def test_exp_0037_keeps_lot_and_cap_equal_between_exit_arms():
    """週次退出と早期退出で200 USDTロット・400 USDT上限を固定することをテストする。"""

    assert ARMS == ("cash_control", "weekly_exit", "momentum_zero_exit")
    assert LOT_NOTIONAL == Decimal("200")
    assert TOTAL_CAP == Decimal("400")

    weekly = _allocation_config("weekly_exit")
    early = _allocation_config("momentum_zero_exit")
    assert weekly == early
    assert early.max_concurrent_long_positions == 2
