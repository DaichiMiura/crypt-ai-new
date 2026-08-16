from decimal import Decimal

import pandas as pd
import pytest

from crypt_ai.allocation import AllocationConfig
from crypt_ai.portfolio import run_allocated_long_portfolio, run_allocated_portfolio
from crypt_ai.research import CostModel


def _config(**overrides: object) -> AllocationConfig:
    """テスト用の複数銘柄配分設定を作る。"""
    values: dict[str, object] = {
        "currency": "JPY",
        "allowed_symbols": ("AAA", "BBB", "CCC"),
        "initial_equity": Decimal("100000"),
        "reserve_cash": Decimal("80000"),
        "max_long_gross_notional": Decimal("20000"),
        "max_short_gross_notional": Decimal("10000"),
        "max_total_gross_notional": Decimal("20000"),
        "per_symbol_max_notional": Decimal("10000"),
        "lot_notional": Decimal("10000"),
        "max_concurrent_long_positions": 2,
        "max_concurrent_short_positions": 1,
    }
    values.update(overrides)
    return AllocationConfig(**values)


def _frame(desired: list[int], close: list[int], interpolated: list[bool] | None = None):
    """テスト用の同期日足シグナルを作る。"""
    return pd.DataFrame(
        {
            "event_time": pd.date_range("2026-01-01", periods=len(desired), freq="D", tz="UTC"),
            "open": close,
            "close": close,
            "desired_position": desired,
            "is_interpolated": interpolated or [False] * len(desired),
        }
    )


def test_portfolio_uses_allocator_to_diversify_fixed_lots():
    """同時保有数と合計元本の上限で固定ロットを分散することをテストする。"""
    result = run_allocated_long_portfolio(
        {
            "AAA": _frame([0, 1, 1, 0], [100, 100, 110, 110]),
            "BBB": _frame([0, 1, 1, 0], [100, 100, 90, 90]),
            "CCC": _frame([0, 1, 1, 0], [100, 100, 105, 105]),
        },
        _config(),
        CostModel(Decimal("0"), Decimal("0"), Decimal("0")),
    )

    entries = [event for event in result.events if event["event_type"] == "ENTRY"]
    rejections = [
        event for event in result.events if event["event_type"] == "ORDER_REJECTED"
    ]
    assert [event["symbol"] for event in entries] == ["AAA", "BBB"]
    assert rejections[0]["allocation_reason"] == "max_concurrent_positions"
    assert result.metrics["entry_count"] == 2
    assert result.metrics["allocation_rejection_count"] == 1


def test_portfolio_releases_allocation_on_exit():
    """決済後に別銘柄が解放されたロット枠を利用できることをテストする。"""
    result = run_allocated_long_portfolio(
        {
            "AAA": _frame([0, 1, 0, 0], [100, 100, 110, 110]),
            "BBB": _frame([0, 0, 1, 0], [100, 100, 100, 110]),
        },
        _config(max_concurrent_long_positions=1),
        CostModel(Decimal("0"), Decimal("0"), Decimal("0")),
    )

    entries = [event for event in result.events if event["event_type"] == "ENTRY"]
    exits = [event for event in result.events if event["event_type"] == "EXIT"]
    assert [event["symbol"] for event in entries] == ["AAA", "BBB"]
    assert [event["symbol"] for event in exits] == ["AAA", "BBB"]


def test_portfolio_uses_requested_lot_count_at_entry():
    """entry時のdesired lot countを元本へ反映し、保有中は固定することをテストする。"""

    frame = _frame([0, 1, 1, 0], [100, 100, 110, 110]).assign(
        desired_long_lot_count=[1, 2, 1, 1]
    )
    result = run_allocated_long_portfolio(
        {"AAA": frame},
        _config(
            allowed_symbols=("AAA",),
            max_long_gross_notional=Decimal("20000"),
            max_total_gross_notional=Decimal("20000"),
            per_symbol_max_notional=Decimal("20000"),
            max_concurrent_long_positions=1,
        ),
        CostModel(Decimal("0"), Decimal("0"), Decimal("0")),
    )

    entry = next(event for event in result.events if event["event_type"] == "ENTRY")
    assert entry["lot_count"] == 2
    assert entry["notional"] == "20000"
    assert result.metrics["final_equity"] == "102000"
    assert result.equity_curve[-1]["allocated_gross_notional"] == "0"


def test_portfolio_rejects_interpolated_new_entry():
    """補間バー上の新規注文を拒否することをテストする。"""
    result = run_allocated_long_portfolio(
        {"AAA": _frame([0, 1], [100, 100], [False, True])},
        _config(
            allowed_symbols=("AAA",),
            max_long_gross_notional=Decimal("10000"),
            max_total_gross_notional=Decimal("10000"),
            per_symbol_max_notional=Decimal("10000"),
        ),
        CostModel(Decimal("0"), Decimal("0"), Decimal("0")),
    )

    assert result.metrics["entry_count"] == 0
    assert result.events[-1]["allocation_reason"] == "interpolated_bar"


def test_portfolio_requires_synchronized_frames():
    """銘柄間で時刻が同期していない入力を拒否することをテストする。"""
    with pytest.raises(ValueError, match="identical timestamps"):
        run_allocated_long_portfolio(
            {
                "AAA": _frame([0, 1], [100, 100]),
                "BBB": _frame([0, 1], [100, 100]).assign(
                    event_time=pd.date_range(
                        "2026-01-02", periods=2, freq="D", tz="UTC"
                    )
                ),
            },
            _config(),
            CostModel(Decimal("0"), Decimal("0"), Decimal("0")),
        )


def test_portfolio_accounts_short_pnl_and_releases_short_allocation():
    """ショートの含み損益と決済時の配分解放をテストする。"""
    frame = _frame([0, 0, 0, 0], [100, 100, 90, 90]).assign(
        desired_short_position=[0, 1, 1, 0]
    )
    result = run_allocated_portfolio(
        {"AAA": frame},
        _config(
            max_long_gross_notional=Decimal("10000"),
            max_short_gross_notional=Decimal("10000"),
            max_total_gross_notional=Decimal("10000"),
        ),
        CostModel(Decimal("0"), Decimal("0"), Decimal("0")),
    )

    entries = [event for event in result.events if event["event_type"] == "ENTRY"]
    exits = [event for event in result.events if event["event_type"] == "EXIT"]
    assert entries[0]["side"] == "short"
    assert exits[0]["side"] == "short"
    assert exits[0]["pnl"] == "1000"
    assert result.metrics["final_equity"] == "101000"
    assert result.metrics["short_entry_count"] == 1
    assert result.metrics["short_realized_pnl"] == "1000"
    assert result.equity_curve[-1]["allocated_gross_notional"] == "0"


def test_portfolio_applies_funding_to_existing_short_before_exit():
    """既存ショートへFundingを適用し、決済前に受払を会計することをテストする。"""
    frame = _frame([0, 0, 0], [100, 100, 100]).assign(
        desired_short_position=[0, 1, 0],
        funding_rate=[0, 0, 0.001],
    )
    result = run_allocated_portfolio(
        {"AAA": frame},
        _config(
            max_long_gross_notional=Decimal("0"),
            max_short_gross_notional=Decimal("10000"),
            max_total_gross_notional=Decimal("10000"),
        ),
        CostModel(Decimal("0"), Decimal("0"), Decimal("0")),
    )

    assert result.metrics["funding_cash_flow"] == "10.000"
    assert result.metrics["final_equity"] == "100010.000"
    funding_events = [event for event in result.events if event["event_type"] == "FUNDING"]
    assert funding_events[0]["side"] == "short"
