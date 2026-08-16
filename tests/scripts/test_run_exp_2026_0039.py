from decimal import Decimal

import pandas as pd

from scripts.run_exp_2026_0039 import (
    HIGH_VOLATILITY_THRESHOLD,
    VOLATILITY_WINDOW_BARS,
    _allocation_config,
    _realized_volatility,
)


def test_exp_0039_fixes_thirty_day_window_and_one_hundred_percent_threshold():
    """30日実現ボラと年率100%閾値を固定することをテストする。"""

    assert VOLATILITY_WINDOW_BARS == 360
    assert HIGH_VOLATILITY_THRESHOLD == 1.0


def test_exp_0039_allocation_accepts_one_or_two_one_hundred_usdt_lots():
    """100 USDT基準で1銘柄最大2ロット・2銘柄を許可することをテストする。"""

    config = _allocation_config("volatility_lot")

    assert config.lot_notional == Decimal("100")
    assert config.per_symbol_max_notional == Decimal("200")
    assert config.max_long_gross_notional == Decimal("400")
    assert config.max_concurrent_long_positions == 2


def test_exp_0039_realized_volatility_uses_population_standard_deviation():
    """一定対数リターンの30日実現ボラティリティがほぼ0になることをテストする。"""

    timestamps = pd.date_range(
        "2026-01-01", periods=VOLATILITY_WINDOW_BARS + 1, freq="2h", tz="UTC"
    )
    close = [100 * (1.001**index) for index in range(len(timestamps))]

    result = _realized_volatility(
        pd.DataFrame({"event_time": timestamps, "close": close})
    )

    assert pd.notna(result.iloc[-1]["realized_volatility"])
    assert result.iloc[-1]["realized_volatility"] < 1e-12
