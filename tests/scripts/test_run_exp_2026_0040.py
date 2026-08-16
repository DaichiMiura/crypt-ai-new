"""EXP-2026-0040ランナーをテストする。"""

from decimal import Decimal

import pytest

from scripts.run_exp_2026_0040 import (
    BASE_SYMBOLS,
    EXPANDED_SYMBOLS,
    _allocation_config,
)


def test_expanded_universe_adds_ada_and_near() -> None:
    """拡張ユニバースがADAとNEARを追加していることをテストする。"""

    assert set(EXPANDED_SYMBOLS) - set(BASE_SYMBOLS) == {"ADAUSDT", "NEARUSDT"}


def test_top2_arms_have_equal_capital_limits() -> None:
    """4銘柄top2と6銘柄top2の投資上限が同じことをテストする。"""

    base = _allocation_config("base_4_top2")
    expanded = _allocation_config("expanded_6_top2")

    assert base.max_long_gross_notional == Decimal("400")
    assert expanded.max_long_gross_notional == Decimal("400")


def test_unknown_arm_is_rejected() -> None:
    """未登録armが拒否されることをテストする。"""

    with pytest.raises(ValueError, match="unknown arm"):
        _allocation_config("unknown")
