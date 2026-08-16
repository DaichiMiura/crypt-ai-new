from decimal import Decimal

import pandas as pd
import pytest

from crypt_ai.basis import BasisSignalConfig, prepare_basis_signals


def test_basis_signal_enters_above_premium_and_exits_after_convergence():
    """先物プレミアム上昇を次足でentryし、収束後に次足でexitすることをテストする。"""
    frame = pd.DataFrame(
        {
            "event_time": pd.date_range("2026-01-01", periods=5, freq="h", tz="UTC"),
            "spot_close": [100, 100, 100, 100, 100],
            "perp_mark_close": [100, 100.6, 100.6, 100.05, 100.05],
        }
    )

    result = prepare_basis_signals(
        frame,
        BasisSignalConfig(
            entry_basis=Decimal("0.005"),
            exit_basis=Decimal("0.001"),
            max_holding_bars=10,
        ),
    )

    assert bool(result.loc[1, "basis_entry_signal"]) is True
    assert result.loc[1, "desired_pair_position"] == 0
    assert result.loc[2, "desired_pair_position"] == 1
    assert bool(result.loc[3, "basis_exit_signal"]) is True
    assert result.loc[4, "desired_pair_position"] == 0


def test_basis_signal_rejects_invalid_threshold_order():
    """entry閾値がexit閾値以下の設定を拒否することをテストする。"""
    with pytest.raises(ValueError, match="entry_basis"):
        BasisSignalConfig(
            entry_basis=Decimal("0.001"),
            exit_basis=Decimal("0.005"),
        )
