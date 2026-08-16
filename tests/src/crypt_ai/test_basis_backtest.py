from decimal import Decimal

import pandas as pd

from crypt_ai.basis_backtest import (
    BasisBacktestConfig,
    BasisCostModel,
    run_basis_backtest,
)


def _frame(desired: list[int], funding: list[float]) -> pd.DataFrame:
    """テスト用の同期ベーシス入力を作る。"""
    return pd.DataFrame(
        {
            "event_time": pd.date_range("2026-01-01", periods=len(desired), freq="h", tz="UTC"),
            "spot_open": [100] * len(desired),
            "spot_close": [100] * len(desired),
            "perp_open": [100] * len(desired),
            "perp_mark_close": [100] * len(desired),
            "funding_rate": funding,
            "desired_pair_position": desired,
            "is_interpolated": [False] * len(desired),
        }
    )


def test_basis_backtest_accounts_spot_perp_pair_and_funding():
    """現物long・先物shortの両脚とFundingを会計することをテストする。"""
    result = run_basis_backtest(
        {"AAA": _frame([0, 1, 1, 0], [0, 0, 0.001, 0])},
        BasisBacktestConfig(
            initial_equity=Decimal("1000"),
            reserve_cash=Decimal("200"),
            pair_notional=Decimal("100"),
            max_concurrent_pairs=1,
            costs=BasisCostModel(
                spot_fee_rate=Decimal("0"),
                perp_fee_rate=Decimal("0"),
                spot_round_trip_spread=Decimal("0"),
                perp_round_trip_spread=Decimal("0"),
                spot_slippage_per_fill=Decimal("0"),
                perp_slippage_per_fill=Decimal("0"),
            ),
        ),
    )

    assert result.metrics["pair_entry_count"] == 1
    assert result.metrics["pair_exit_count"] == 1
    assert result.metrics["funding_cash_flow"] == "0.100"
    assert result.metrics["final_equity"] == "1000.100"
