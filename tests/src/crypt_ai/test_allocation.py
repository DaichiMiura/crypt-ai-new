from decimal import Decimal
from pathlib import Path

import pytest

from crypt_ai.allocation import (
    AllocationConfig,
    AllocationState,
    PortfolioAllocator,
    load_allocation_config,
)


def _config(**overrides: object) -> AllocationConfig:
    """テスト用の複数銘柄配分設定を作る。"""
    values: dict[str, object] = {
        "currency": "JPY",
        "allowed_symbols": ("BTCUSDT", "ETHUSDT", "LINKUSDT"),
        "initial_equity": Decimal("100000"),
        "reserve_cash": Decimal("20000"),
        "max_long_gross_notional": Decimal("60000"),
        "max_short_gross_notional": Decimal("10000"),
        "max_total_gross_notional": Decimal("70000"),
        "per_symbol_max_notional": Decimal("10000"),
        "lot_notional": Decimal("10000"),
        "max_concurrent_long_positions": 3,
        "max_concurrent_short_positions": 1,
    }
    values.update(overrides)
    return AllocationConfig(**values)


def test_load_allocation_config_reads_flexible_assets_and_lot_amount():
    """資産一覧、上限、1ロット元本をYAMLから読み込むことをテストする。"""
    config = load_allocation_config(Path("config/allocation.yaml"))

    assert config.allowed_symbols == (
        "BTCUSDT",
        "ETHUSDT",
        "LINKUSDT",
        "UNIUSDT",
        "ADAUSDT",
        "AVAXUSDT",
    )
    assert config.initial_equity == Decimal("100000")
    assert config.reserve_cash == Decimal("20000")
    assert config.lot_notional == Decimal("10000")


def test_try_open_distributes_fixed_lots_across_symbols():
    """同じ固定ロットを複数銘柄へ配分し、状態へ合算することをテストする。"""
    allocator = PortfolioAllocator(_config())
    state = AllocationState(Decimal("100000"))

    first = allocator.try_open(state, side="long", symbol="BTCUSDT")
    second = allocator.try_open(state, side="long", symbol="ETHUSDT")

    assert first.accepted is True
    assert second.accepted is True
    assert state.long_positions == {
        "BTCUSDT": Decimal("10000"),
        "ETHUSDT": Decimal("10000"),
    }
    assert state.long_gross_notional == Decimal("20000")


def test_allocator_rejects_unknown_asset_and_caps_one_symbol():
    """未登録資産と1銘柄上限超過を拒否することをテストする。"""
    allocator = PortfolioAllocator(_config())
    state = AllocationState(Decimal("100000"))

    unknown = allocator.try_open(state, side="long", symbol="SOLUSDT")
    first = allocator.try_open(state, side="long", symbol="BTCUSDT")
    second = allocator.try_open(state, side="long", symbol="BTCUSDT")

    assert unknown.reason == "unknown_symbol"
    assert first.accepted is True
    assert second.reason == "per_symbol_cap"


def test_allocator_enforces_side_total_and_concurrent_caps():
    """ロング・ショートの元本上限と同時保有数上限を拒否することをテストする。"""
    allocator = PortfolioAllocator(_config(per_symbol_max_notional=Decimal("70000")))
    state = AllocationState(Decimal("100000"))

    assert allocator.try_open(state, side="short", symbol="BTCUSDT").accepted is True
    assert allocator.try_open(state, side="short", symbol="ETHUSDT").reason == (
        "max_concurrent_positions"
    )
    assert allocator.try_open(state, side="long", symbol="ETHUSDT", lot_count=7).reason == (
        "long_cap"
    )


def test_allocator_enforces_total_cap_and_reserve_cash():
    """口座全体上限と予備資金を残す境界を拒否することをテストする。"""
    allocator = PortfolioAllocator(
        _config(
            max_long_gross_notional=Decimal("100000"),
            max_total_gross_notional=Decimal("100000"),
            per_symbol_max_notional=Decimal("100000"),
        )
    )
    state = AllocationState(Decimal("100000"))

    accepted = allocator.try_open(state, side="long", symbol="BTCUSDT", lot_count=8)
    rejected = allocator.try_open(state, side="long", symbol="ETHUSDT")

    assert accepted.accepted is True
    assert rejected.reason == "reserve_cash"


def test_release_returns_capacity_for_another_symbol():
    """決済で元本と同時保有枠を解放することをテストする。"""
    allocator = PortfolioAllocator(_config(max_concurrent_long_positions=1))
    state = AllocationState(Decimal("100000"))

    assert allocator.try_open(state, side="long", symbol="BTCUSDT").accepted is True
    assert allocator.try_open(state, side="long", symbol="ETHUSDT").reason == (
        "max_concurrent_positions"
    )

    allocator.release(state, side="long", symbol="BTCUSDT")
    decision = allocator.try_open(state, side="long", symbol="ETHUSDT")

    assert decision.accepted is True
    assert state.long_positions == {"ETHUSDT": Decimal("10000")}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("initial_equity", Decimal("0")),
        ("reserve_cash", Decimal("100001")),
        ("lot_notional", Decimal("0")),
        ("max_concurrent_long_positions", 0),
    ],
)
def test_allocation_config_rejects_invalid_limits(field, value):
    """不正な資産・上限・ロット設定を拒否することをテストする。"""
    with pytest.raises(ValueError):
        AllocationConfig(**{**_config().__dict__, field: value})


def test_evaluate_order_does_not_mutate_state():
    """判定だけではポジション状態を変更しないことをテストする。"""
    allocator = PortfolioAllocator(_config())
    state = AllocationState(Decimal("100000"))

    decision = allocator.evaluate_order(state, side="long", symbol="BTCUSDT")

    assert decision.accepted is True
    assert state.long_positions == {}


def test_allocation_config_allows_zero_side_cap_for_long_only_profiles():
    """long-only設定でショート上限をゼロにできることをテストする。"""
    config = _config(max_short_gross_notional=Decimal("0"))

    assert config.max_short_gross_notional == Decimal("0")


def test_state_rejects_over_closing_position():
    """保有元本を超える決済を拒否することをテストする。"""
    state = AllocationState(
        Decimal("100000"), long_positions={"BTCUSDT": Decimal("10000")}
    )

    with pytest.raises(ValueError, match="exceeds"):
        state.close_position("long", "BTCUSDT", Decimal("10001"))
