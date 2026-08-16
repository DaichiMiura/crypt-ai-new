"""EXP-2026-0041ランナーをテストする。"""

import pandas as pd
import pytest

from scripts.run_exp_2026_0041 import _apply_entry_atr_stop


def _frame() -> pd.DataFrame:
    """ATR stopテスト用DataFrameを作る。"""

    return pd.DataFrame(
        {
            "open": [100, 100, 100, 100, 100, 100, 95, 95],
            "high": [101, 101, 101, 101, 101, 101, 96, 96],
            "low": [99, 99, 99, 99, 99, 95, 94, 94],
            "close": [100, 100, 100, 100, 100, 96, 95, 95],
            "desired_long_position": [0, 0, 0, 0, 1, 1, 1, 0],
        }
    )


def test_atr_stop_exits_on_bar_after_trigger() -> None:
    """ATR stopがtriggerの次足で退出することをテストする。"""

    result = _apply_entry_atr_stop(_frame(), atr_bars=3, atr_multiplier=2.0)

    assert result.loc[5, "entry_atr_stop_trigger"]
    assert result.loc[5, "desired_long_position"] == 1
    assert result.loc[6, "desired_long_position"] == 0


def test_atr_stop_blocks_reentry_until_base_resets() -> None:
    """stop後にbaseが0になるまで再entryしないことをテストする。"""

    result = _apply_entry_atr_stop(_frame(), atr_bars=3, atr_multiplier=2.0)

    assert result.loc[6, "base_desired_long_position"] == 1
    assert result.loc[6, "desired_long_position"] == 0
    assert result.loc[7, "base_desired_long_position"] == 0


def test_atr_stop_rejects_invalid_parameters() -> None:
    """非正のATR設定が拒否されることをテストする。"""

    with pytest.raises(ValueError, match="parameters must be positive"):
        _apply_entry_atr_stop(_frame(), atr_bars=0)
