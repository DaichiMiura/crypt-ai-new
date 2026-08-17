import pandas as pd
import pytest

from crypt_ai.low_volatility import (
    LowVolatilitySignalConfig,
    prepare_low_volatility_signals,
)


def _frames(closes: dict[str, list[float]]) -> dict[str, pd.DataFrame]:
    """テスト用の同期した低vol入力を作る。"""

    size = len(next(iter(closes.values())))
    timestamps = pd.date_range("2026-01-01T00:00:00Z", periods=size, freq="2h")
    return {
        symbol: pd.DataFrame(
            {
                "event_time": timestamps,
                "open": values,
                "close": values,
                "funding_rate": [0.0001] * size,
                "funding_event": [index % 4 == 0 for index in range(size)],
            }
        )
        for symbol, values in closes.items()
    }


def test_low_vol_signal_selects_lowest_vol_and_delays_one_bar():
    """正のregimeで最低vol銘柄を選び、次バーから保有することをテストする。"""

    frames = _frames(
        {
            "AAA": [100, 101, 102, 103, 104, 105, 106, 107],
            "BBB": [100, 105, 101, 108, 106, 110, 107, 112],
            "CCC": [100, 103, 101, 105, 102, 107, 104, 109],
        }
    )
    result = prepare_low_volatility_signals(
        frames,
        LowVolatilitySignalConfig(
            volatility_window_bars=2,
            regime_window_bars=4,
            rebalance_bars=2,
            selected_count=1,
            signal_delay_bars=1,
            annualization_bars=12,
        ),
        start_trading_at=pd.Timestamp("2026-01-01T08:00:00Z"),
    )

    assert result["AAA"].loc[4, "low_vol_reason"] == "accepted"
    assert result["AAA"].loc[4, "low_vol_selected"] == True  # noqa: E712
    assert result["AAA"].loc[4, "desired_long_position"] == 0
    assert result["AAA"].loc[5, "desired_long_position"] == 1
    assert result["BBB"].loc[5, "desired_long_position"] == 0


def test_low_vol_signal_stays_in_cash_in_negative_regime():
    """市場180日相当return中央値が非正なら全銘柄cashにすることをテストする。"""

    frames = _frames(
        {
            "AAA": [100, 99, 98, 97, 96, 95, 94],
            "BBB": [100, 98, 99, 96, 97, 94, 95],
            "CCC": [100, 97, 98, 95, 96, 93, 94],
        }
    )
    result = prepare_low_volatility_signals(
        frames,
        LowVolatilitySignalConfig(
            volatility_window_bars=2,
            regime_window_bars=4,
            rebalance_bars=2,
            selected_count=1,
            annualization_bars=12,
        ),
        start_trading_at=pd.Timestamp("2026-01-01T08:00:00Z"),
    )

    assert result["AAA"].loc[4, "low_vol_reason"] == "market_regime_nonpositive"
    assert sum(result["AAA"]["desired_long_position"]) == 0


def test_low_vol_signal_rejects_timestamp_mismatch():
    """銘柄間で時刻が一致しない入力を拒否することをテストする。"""

    frames = _frames({"AAA": [100] * 6, "BBB": [100] * 6})
    frames["BBB"].loc[5, "event_time"] += pd.Timedelta(hours=2)
    with pytest.raises(ValueError, match="identical timestamps"):
        prepare_low_volatility_signals(
            frames,
            LowVolatilitySignalConfig(
                volatility_window_bars=2,
                regime_window_bars=4,
                rebalance_bars=2,
                selected_count=1,
                annualization_bars=12,
            ),
            start_trading_at=pd.Timestamp("2026-01-01T08:00:00Z"),
        )
