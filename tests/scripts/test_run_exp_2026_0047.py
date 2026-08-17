"""EXP-2026-0047ランナーをテストする。"""

from decimal import Decimal

from scripts.run_exp_2026_0047 import (
    ATR_MULTIPLIER,
    FIXED_DRAWDOWN,
    MAX_LOTS_PER_SYMBOL,
    MAX_TOTAL_ALLOCATED_NOTIONAL,
    _split_config,
)


def test_split_entry_parameters_match_preregistration() -> None:
    """分割entryの下落段階と最大ロット数をテストする。"""

    assert FIXED_DRAWDOWN == (Decimal("0.0236"),)
    assert ATR_MULTIPLIER == (Decimal("1"),)
    assert MAX_LOTS_PER_SYMBOL == 2


def test_split_and_fixed_arms_have_same_capital_caps() -> None:
    """分割版が一括200と同じ銘柄・全体元本上限を持つことをテストする。"""

    config = _split_config()

    assert config.per_symbol_max_notional == Decimal("200")
    assert config.max_long_gross_notional == Decimal("400")
    assert config.max_total_gross_notional == MAX_TOTAL_ALLOCATED_NOTIONAL
    assert config.reserve_cash == Decimal("200")
