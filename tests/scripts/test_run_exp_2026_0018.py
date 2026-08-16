from decimal import Decimal

from scripts.run_exp_2026_0018 import (
    LOT_COUNTS_CONTROL,
    LOT_COUNTS_VARIANT,
    MAX_TOTAL_LOT_COUNT_VARIANT,
    _add_exposure_deltas,
)


def test_fibonacci_lot_variant_is_capped_at_seven_lots():
    """variantが1,1,2,3ロットで合計7ロット上限であることをテストする。"""
    assert LOT_COUNTS_CONTROL == (1, 1, 1, 1)
    assert LOT_COUNTS_VARIANT == (1, 1, 2, 3)
    assert sum(LOT_COUNTS_VARIANT) == MAX_TOTAL_LOT_COUNT_VARIANT


def test_exposure_delta_reports_variant_minus_control():
    """最大建玉数量と想定元本のvariant差分を計算することをテストする。"""
    result = _add_exposure_deltas(
        {},
        {
            "max_position_quantity": "10",
            "max_position_notional": "1000",
            "liquidation_count": 0,
        },
        {
            "max_position_quantity": "16",
            "max_position_notional": "1600",
            "liquidation_count": 1,
        },
    )

    assert Decimal(result["max_position_quantity_delta"]) == Decimal("6")
    assert Decimal(result["max_position_notional_delta"]) == Decimal("600")
    assert result["liquidation_count_delta"] == 1
