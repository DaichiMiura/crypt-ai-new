import pandas as pd
import pytest

from crypt_ai.pairs_mean_reversion import (
    PairMeanReversionConfig,
    prepare_pair_mean_reversion_signals,
)


def _frame(closes: list[float]) -> pd.DataFrame:
    """テスト用の同期した価格・Fundingフレームを作る。"""

    return pd.DataFrame(
        {
            "event_time": pd.date_range("2026-01-01T00:00:00Z", periods=len(closes), freq="2h"),
            "open": closes,
            "close": closes,
            "funding_rate": [0.0001] * len(closes),
            "funding_event": [index % 4 == 0 for index in range(len(closes))],
        }
    )


def test_pair_signal_enters_on_next_bar_after_current_spread_spike():
    """現在closeの乖離を観測後、次バーで両脚を逆方向に建てることをテストする。"""

    near = _frame([100, 101, 102, 103, 104, 105, 106])
    avax = _frame([50, 50.5, 51, 51.5, 65, 52.5, 53])
    result = prepare_pair_mean_reversion_signals(
        avax,
        near,
        PairMeanReversionConfig(
            regression_window_bars=4,
            spread_window_bars=4,
            entry_z=1.0,
            exit_z=0.25,
            stop_z=10.0,
            max_holding_bars=10,
            signal_delay_bars=1,
        ),
    )

    assert result["AVAXUSDT"].loc[4, "pair_signal_action"] == "enter_avax_short"
    assert result["AVAXUSDT"].loc[4, "desired_short_position"] == 0
    assert result["AVAXUSDT"].loc[5, "desired_short_position"] == 1
    assert result["NEARUSDT"].loc[5, "desired_long_position"] == 1


def test_pair_signal_rejects_unsynchronized_timestamps():
    """片脚の時刻がずれた入力を拒否することをテストする。"""

    avax = _frame([50, 51, 52, 53, 54])
    near = _frame([100, 101, 102, 103, 104])
    near.loc[4, "event_time"] += pd.Timedelta(hours=2)

    with pytest.raises(ValueError, match="timestamps must be identical"):
        prepare_pair_mean_reversion_signals(
            avax,
            near,
            PairMeanReversionConfig(
                regression_window_bars=3,
                spread_window_bars=3,
                max_holding_bars=3,
            ),
        )


def test_pair_config_rejects_misordered_z_thresholds():
    """exit、entry、stopの順序が不正な設定を拒否することをテストする。"""

    with pytest.raises(ValueError, match="z thresholds"):
        PairMeanReversionConfig(entry_z=2, exit_z=3, stop_z=4)
