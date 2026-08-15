from decimal import Decimal

from scripts.run_exp_2026_0019 import (
    LOT_COUNTS_CONTROL,
    LOT_COUNTS_VARIANT,
    MAX_TOTAL_LOT_COUNT_VARIANT,
)


def test_fibonacci_lot_variant_is_capped_at_five_lots():
    """variantが1,1,1,2ロットで合計5ロット上限であることをテストする。"""
    assert LOT_COUNTS_CONTROL == (1, 1, 1, 1)
    assert LOT_COUNTS_VARIANT == (1, 1, 1, 2)
    assert sum(LOT_COUNTS_VARIANT) == MAX_TOTAL_LOT_COUNT_VARIANT


def test_fibonacci_lot_variant_has_one_additional_lot():
    """variantがcontrolより1ロットだけ多いことをテストする。"""
    assert Decimal(sum(LOT_COUNTS_VARIANT) - sum(LOT_COUNTS_CONTROL)) == Decimal("1")
