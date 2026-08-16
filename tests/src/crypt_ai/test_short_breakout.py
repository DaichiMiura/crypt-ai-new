from decimal import Decimal

import pandas as pd
import pytest

from crypt_ai.short_breakout import (
    ShortBreakoutConfig,
    prepare_short_breakout_signals,
    run_short_breakout_backtest,
)
from crypt_ai.void_short_backtest import VoidShortInstrument


def _frame(closes: list[float]) -> pd.DataFrame:
    """テスト用の連続2時間足を作る。"""
    timestamps = pd.date_range("2022-01-01", periods=len(closes), freq="2h", tz="UTC")
    return pd.DataFrame(
        {
            "event_time": timestamps,
            "open": closes,
            "high": [value + 1 for value in closes],
            "low": [value - 1 for value in closes],
            "close": closes,
            "is_interpolated": [False] * len(closes),
        }
    )


def test_breakout_signal_excludes_current_bar_from_donchian_window():
    """下落ブレイク判定が現在足をDonchian下限へ含めないことをテストする。"""
    frame = _frame([100, 101, 102, 90, 89, 88])

    prepared = prepare_short_breakout_signals(
        frame, sma_bars=3, donchian_bars=2, atr_bars=2
    )

    assert prepared.loc[3, "donchian_low"] == 100
    assert bool(prepared.loc[3, "entry_signal_at_close"]) is True


def test_short_breakout_config_rejects_non_positive_windows():
    """下落ブレイク設定の非正な窓幅を拒否することをテストする。"""
    with pytest.raises(ValueError, match="sma_bars"):
        ShortBreakoutConfig(sma_bars=0)


def test_short_breakout_opens_single_position_without_additional_entries():
    """下落ブレイクが保有中に追加エントリーしないことをテストする。"""
    frame = _frame([100, 101, 102, 90, 89, 88, 87, 86, 95, 96])
    funding = pd.DataFrame({"event_time": [], "funding_rate": []})
    instrument = VoidShortInstrument(
        symbol="TESTUSDT",
        tick_size=Decimal("0.01"),
        qty_step=Decimal("0.01"),
        min_order_qty=Decimal("0.01"),
        min_order_notional=Decimal("1"),
    )
    result = run_short_breakout_backtest(
        frame,
        funding,
        instrument,
        ShortBreakoutConfig(
            initial_equity=Decimal("250"),
            evaluation_start=pd.Timestamp("2022-01-01T00:00:00Z"),
            evaluation_end=pd.Timestamp("2022-01-02T00:00:00Z"),
            sma_bars=3,
            donchian_bars=2,
            atr_bars=2,
            stop_atr_multiplier=Decimal("3"),
            max_holding_bars=20,
            entry_lot_count=1,
        ),
    )

    assert result.metrics["entry_count"] == 1
