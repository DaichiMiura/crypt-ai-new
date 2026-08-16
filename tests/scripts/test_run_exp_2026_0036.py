from decimal import Decimal

from scripts.run_exp_2026_0036 import (
    ARM_LOT_NOTIONAL,
    _allocation_config,
)


def test_exp_0036_compares_only_one_hundred_and_two_hundred_usdt_lots():
    """上位2銘柄の100・200 USDTロットを固定比較することをテストする。"""

    assert ARM_LOT_NOTIONAL["top2_lot_100"] == Decimal("100")
    assert ARM_LOT_NOTIONAL["top2_lot_200"] == Decimal("200")


def test_exp_0036_allocation_keeps_two_symbols_and_scales_caps():
    """ロット額に合わせて銘柄上限と総上限だけを比例変更することをテストする。"""

    small = _allocation_config("top2_lot_100")
    large = _allocation_config("top2_lot_200")

    assert small.max_concurrent_long_positions == 2
    assert large.max_concurrent_long_positions == 2
    assert small.per_symbol_max_notional == Decimal("100")
    assert large.per_symbol_max_notional == Decimal("200")
    assert small.max_long_gross_notional == Decimal("200")
    assert large.max_long_gross_notional == Decimal("400")
