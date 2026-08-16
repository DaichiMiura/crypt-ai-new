from decimal import Decimal

from scripts.run_exp_2026_0017 import (
    NORMAL_STOP_CONTROL_ATR,
    NORMAL_STOP_VARIANT_ATR,
)


def test_stop_width_variant_is_one_point_five_atr():
    """通常損切りvariantが1.5 ATRでcontrolが1 ATRであることをテストする。"""
    assert NORMAL_STOP_CONTROL_ATR == Decimal("1")
    assert NORMAL_STOP_VARIANT_ATR == Decimal("1.5")
