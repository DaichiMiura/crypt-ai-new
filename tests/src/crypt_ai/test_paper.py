from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from crypt_ai.paper import (
    PaperConfig,
    load_paper_config,
    new_paper_state,
    process_paper_bar,
)
from crypt_ai.research import CostModel


def _config(**overrides: object) -> PaperConfig:
    """テスト用paper設定を作る。"""
    values = {
        "strategy_id": "EXP-2026-0012",
        "strategy_version": "test",
        "symbol": "BTC_JPY",
        "start_utc": pd.Timestamp("2026-08-16T00:00:00Z"),
        "initial_cash": Decimal("10000"),
        "cost_model": CostModel(
            Decimal("0.001"), Decimal("0.0005"), Decimal("0.0005")
        ),
        "max_order_notional": Decimal("10000"),
        "max_position_notional": Decimal("10000"),
        "max_daily_loss": Decimal("3000"),
        "max_drawdown": Decimal("4000"),
        "reject_interpolated_bar_orders": True,
    }
    values.update(overrides)
    return PaperConfig(**values)


def _bar(day: str, desired: int, price: int = 100, interpolated: bool = False):
    """テスト用確定日足を作る。"""
    return SimpleNamespace(
        event_time=pd.Timestamp(day),
        open=price,
        high=price + 2,
        low=price - 2,
        close=price,
        desired_atr_position=desired,
        is_interpolated=interpolated,
    )


def test_load_paper_config_uses_smaller_global_and_strategy_limits():
    """口座全体と戦略設定の小さい方を実効上限にすることをテストする。"""
    config = load_paper_config(
        Path("config/paper-risk-limits.yaml"),
        Path("config/strategies/exp-2026-0012-paper.yaml"),
    )

    assert config.strategy_id == "EXP-2026-0012"
    assert config.max_order_notional == Decimal("10000")
    assert config.max_position_notional == Decimal("30000")
    assert config.initial_cash == Decimal("10000")


def test_process_paper_bar_buys_sells_and_ignores_exact_replay():
    """仮想売買を会計し、同じ確定足の再処理で重複約定しないことをテストする。"""
    config = _config()
    state = new_paper_state(config)
    buy_bar = _bar("2026-08-16T00:00:00Z", 1)

    buy_events = process_paper_bar(state, buy_bar, config)
    replay_events = process_paper_bar(state, buy_bar, config)
    sell_events = process_paper_bar(
        state, _bar("2026-08-17T00:00:00Z", 0, price=110), config
    )

    assert [event["event_type"] for event in buy_events].count("fill") == 1
    assert replay_events == []
    assert [event["event_type"] for event in sell_events].count("fill") == 1
    assert Decimal(state.cash) > Decimal("10000")
    assert Decimal(state.quantity) == 0


def test_process_paper_bar_halts_on_conflicting_duplicate():
    """処理済み時刻のOHLCが変わった場合に停止することをテストする。"""
    config = _config()
    state = new_paper_state(config)
    process_paper_bar(state, _bar("2026-08-16T00:00:00Z", 0), config)

    with pytest.raises(ValueError, match="conflicting OHLC"):
        process_paper_bar(
            state, _bar("2026-08-16T00:00:00Z", 0, price=101), config
        )

    assert state.halted is True
    assert "duplicate_bar_conflict" in state.halt_reasons


def test_process_paper_bar_rejects_order_on_interpolated_day():
    """補間日上の仮想注文を拒否してpaperを停止することをテストする。"""
    config = _config()
    state = new_paper_state(config)

    events = process_paper_bar(
        state,
        _bar("2026-08-16T00:00:00Z", 1, interpolated=True),
        config,
    )

    assert any(event["event_type"] == "order_rejected" for event in events)
    assert Decimal(state.quantity) == 0
    assert state.halted is True


def test_process_paper_bar_caps_order_at_strategy_limit():
    """仮想注文を戦略上限で縮小し、残りの現金を保持することをテストする。"""
    config = _config(max_order_notional=Decimal("9999"))
    state = new_paper_state(config)

    events = process_paper_bar(
        state, _bar("2026-08-16T00:00:00Z", 1), config
    )

    assert any(event["event_type"] == "fill" for event in events)
    assert Decimal(state.cash) == Decimal("1")
    assert state.halted is False
