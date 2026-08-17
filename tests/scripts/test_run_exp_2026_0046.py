"""EXP-2026-0046ランナーをテストする。"""

from decimal import Decimal

from scripts.run_exp_2026_0046 import (
    ATR_MULTIPLIERS_MAX3,
    FIXED_DRAWDOWNS_MAX3,
    MAX_LOTS_PER_SYMBOL,
    MAX_TOTAL_GROSS_NOTIONAL,
    _ladder_config,
)


def test_max3_parameters_match_preregistration() -> None:
    """最大3ロット版の段階と合計元本をテストする。"""

    assert FIXED_DRAWDOWNS_MAX3 == (Decimal("0.0236"), Decimal("0.0382"))
    assert ATR_MULTIPLIERS_MAX3 == (Decimal("1"), Decimal("1.618"))
    assert MAX_LOTS_PER_SYMBOL == 3
    assert MAX_TOTAL_GROSS_NOTIONAL == Decimal("600")


def test_max3_allocation_caps_each_symbol_at_three_lots() -> None:
    """配分設定が1銘柄300、全体600 USDTへ制限されることをテストする。"""

    config = _ladder_config()

    assert config.per_symbol_max_notional == Decimal("300")
    assert config.max_long_gross_notional == Decimal("600")
    assert config.max_total_gross_notional == Decimal("600")
