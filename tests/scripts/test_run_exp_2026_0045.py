"""EXP-2026-0045ランナーをテストする。"""

from decimal import Decimal

import pandas as pd

from scripts.run_exp_2026_0045 import (
    ATR_MULTIPLIERS,
    FIXED_DRAWDOWNS,
    LONG_ATR_BARS,
    MAX_LOTS_PER_SYMBOL,
    _attach_entry_atr,
)


def test_preregistered_ladder_parameters_are_fixed() -> None:
    """買い増し段階、ATR期間、最大ロット数をテストする。"""

    assert FIXED_DRAWDOWNS == (
        Decimal("0.0236"),
        Decimal("0.0382"),
        Decimal("0.0618"),
        Decimal("0.10"),
    )
    assert ATR_MULTIPLIERS == (
        Decimal("1"),
        Decimal("1.618"),
        Decimal("2.618"),
        Decimal("4.236"),
    )
    assert LONG_ATR_BARS == 240
    assert MAX_LOTS_PER_SYMBOL == 5


def test_entry_atr_uses_only_previous_completed_bars() -> None:
    """entry ATRが現在足を含まず前足までで計算されることをテストする。"""

    periods = LONG_ATR_BARS + 2
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range(
                "2026-01-01", periods=periods, freq="2h", tz="UTC"
            ),
            "open": [100.0] * periods,
            "high": [101.0] * (periods - 1) + [200.0],
            "low": [99.0] * periods,
            "close": [100.0] * periods,
            "desired_long_position": [0] * periods,
        }
    )

    result = _attach_entry_atr({"AAA": frame})["AAA"]

    assert pd.isna(result.loc[LONG_ATR_BARS - 1, "entry_atr"])
    assert result.loc[LONG_ATR_BARS, "entry_atr"] == 2.0
    assert result.loc[LONG_ATR_BARS + 1, "entry_atr"] == 2.0
