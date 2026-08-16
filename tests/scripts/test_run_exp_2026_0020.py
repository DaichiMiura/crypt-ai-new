from scripts.run_exp_2026_0020 import (
    COMPOUND_CONTROL,
    COMPOUND_VARIANT,
    LOT_COUNTS,
    MAX_TOTAL_LOT_COUNT,
)


def test_profit_compounding_is_the_only_variant_change():
    """variantが利益時複利だけを有効化することをテストする。"""
    assert COMPOUND_CONTROL is False
    assert COMPOUND_VARIANT is True
    assert LOT_COUNTS == (1, 1, 1, 1)
    assert MAX_TOTAL_LOT_COUNT == 4
