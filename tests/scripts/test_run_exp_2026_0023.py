from decimal import Decimal

from scripts.run_exp_2026_0023 import (
    LONG_ATR_BARS,
    LONG_ENTRY_BARS,
    LONG_EXIT_BARS,
    LONG_REGIME_BARS,
    LONG_SLEEVE_EQUITY,
    PORTFOLIO_INITIAL_EQUITY,
    SHORT_BALANCED_STOP_ATR,
    SHORT_SLEEVE_EQUITY,
    _max_drawdown,
)


def test_long_parameters_are_time_scaled_to_two_hour_bars():
    """ロングの55日・20日・200日・20日ATRを2時間足へ換算していることをテストする。"""
    assert LONG_ENTRY_BARS == 55 * 12
    assert LONG_EXIT_BARS == 20 * 12
    assert LONG_REGIME_BARS == 200 * 12
    assert LONG_ATR_BARS == 20 * 12


def test_portfolio_sleeves_keep_short_at_twenty_percent_gross_notional():
    """75/25固定スリーブがショート最大想定元本20%を意図することをテストする。"""
    assert PORTFOLIO_INITIAL_EQUITY == Decimal("1000")
    assert LONG_SLEEVE_EQUITY == Decimal("750")
    assert SHORT_SLEEVE_EQUITY == Decimal("250")
    assert SHORT_BALANCED_STOP_ATR == Decimal("1.5")


def test_max_drawdown_uses_sleeve_initial_equity():
    """スリーブ初期資産を基準に最大DDを計算することをテストする。"""
    curve = [
        {"equity": "750", "position_quantity": "0", "position_notional": "0"},
        {"equity": "675", "position_quantity": "1", "position_notional": "100"},
    ]

    assert _max_drawdown(curve, Decimal("750")) == Decimal("-0.1")
