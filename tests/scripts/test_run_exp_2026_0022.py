from scripts.run_exp_2026_0022 import (
    LOT_COUNTS,
    MAX_TOTAL_LOT_COUNT,
    SMA_PROXIMITY_CONTROL,
    SMA_PROXIMITY_VARIANT,
)


def test_sma_proximity_variant_changes_only_the_entry_filter():
    """SMA接近variantだけがエントリーフィルターを有効にすることをテストする。"""
    assert SMA_PROXIMITY_CONTROL is False
    assert SMA_PROXIMITY_VARIANT is True
    assert LOT_COUNTS == (1, 1, 1, 1)
    assert sum(LOT_COUNTS) == MAX_TOTAL_LOT_COUNT
