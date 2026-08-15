"""VOID式ショートrunnerの基礎テスト。"""

from decimal import Decimal

import pandas as pd
import pytest

from crypt_ai.void_short_accounting import VoidShortPosition
from crypt_ai.void_short_backtest import (
    VoidShortBacktestConfig,
    VoidShortInstrument,
    _mark_equity,
    _validate_instrument,
)


def test_mark_equity_includes_short_unrealized_pnl():
    """ショート建玉のmark評価額を初期資産へ反映することをテストする。"""
    position = VoidShortPosition(
        quantity=Decimal("2"),
        average_entry_price=Decimal("100"),
        cash_flow=Decimal("200"),
    )

    assert _mark_equity(Decimal("1000"), position, Decimal("90")) == Decimal("1020")


def test_backtest_config_rejects_non_positive_initial_equity():
    """初期資産0以下のrunner設定を拒否することをテストする。"""
    with pytest.raises(ValueError, match="initial_equity"):
        VoidShortBacktestConfig(initial_equity=Decimal("0"))


def test_backtest_config_accepts_wider_normal_stop_pullback():
    """runnerが1.5 ATR通常損切り設定を受け付けることをテストする。"""
    config = VoidShortBacktestConfig(normal_stop_pullback_atr=Decimal("1.5"))

    assert config.normal_stop_pullback_atr == Decimal("1.5")


def test_backtest_config_accepts_capped_fibonacci_lot_counts():
    """runnerが合計7ロットの1,1,2,3設定を受け付けることをテストする。"""
    config = VoidShortBacktestConfig(
        entry_lot_counts=(1, 1, 2, 3),
        max_entry_lot_count=7,
    )

    assert config.entry_lot_counts == (1, 1, 2, 3)


def test_backtest_config_accepts_profit_compounding():
    """runnerが利益時の現在資産ベース設定を受け付けることをテストする。"""
    config = VoidShortBacktestConfig(compound_profits=True)

    assert config.compound_profits is True


def test_backtest_config_accepts_persistent_downtrend_bars():
    """runnerが12本の下降トレンド確認設定を受け付けることをテストする。"""
    config = VoidShortBacktestConfig(downtrend_persistence_bars=12)

    assert config.downtrend_persistence_bars == 12


def test_backtest_config_accepts_sma_proximity_filter():
    """runnerがSMA200接近フィルター設定を受け付けることをテストする。"""
    config = VoidShortBacktestConfig(require_sma_proximity=True)

    assert config.require_sma_proximity is True


def test_instrument_requires_positive_tick_size():
    """銘柄仕様のtick sizeが正であることを検査することをテストする。"""
    instrument = VoidShortInstrument(
        symbol="LINKUSDT",
        tick_size=Decimal("0"),
        qty_step=Decimal("0.1"),
        min_order_qty=Decimal("0.1"),
        min_order_notional=Decimal("5"),
    )

    with pytest.raises(ValueError, match="tick_size"):
        _validate_instrument(instrument)
