from scripts.run_exp_2026_0021 import (
    LOT_COUNTS,
    MAX_TOTAL_LOT_COUNT,
    PERSISTENCE_CONTROL_BARS,
    PERSISTENCE_VARIANT_BARS,
)


def test_persistent_downtrend_variant_uses_twelve_bars_with_fixed_lots():
    """持続下降トレンドvariantが12本確認と固定ロットを使うことをテストする。"""
    assert PERSISTENCE_CONTROL_BARS == 1
    assert PERSISTENCE_VARIANT_BARS == 12
    assert LOT_COUNTS == (1, 1, 1, 1)
    assert sum(LOT_COUNTS) == MAX_TOTAL_LOT_COUNT
