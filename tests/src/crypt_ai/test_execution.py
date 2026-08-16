from decimal import Decimal

import pytest

from crypt_ai.allocation import AllocationConfig, AllocationState, PortfolioAllocator
from crypt_ai.execution import (
    OrderRejected,
    ZoomexInstrument,
    build_entry_order,
    build_exit_order,
)


def _allocator() -> tuple[PortfolioAllocator, AllocationState]:
    """テスト用アロケータと状態を作る。"""
    config = AllocationConfig(
        currency="USDT",
        allowed_symbols=("AAAUSDT",),
        initial_equity=Decimal("100000"),
        reserve_cash=Decimal("20000"),
        max_long_gross_notional=Decimal("60000"),
        max_short_gross_notional=Decimal("10000"),
        max_total_gross_notional=Decimal("70000"),
        per_symbol_max_notional=Decimal("10000"),
        lot_notional=Decimal("10000"),
        max_concurrent_long_positions=1,
        max_concurrent_short_positions=1,
    )
    return PortfolioAllocator(config), AllocationState(Decimal("100000"))


def _instrument() -> ZoomexInstrument:
    """テスト用ZOOMEX銘柄仕様を作る。"""
    return ZoomexInstrument(
        symbol="AAAUSDT",
        tick_size=Decimal("0.1"),
        qty_step=Decimal("0.1"),
        min_order_qty=Decimal("0.1"),
        min_order_notional=Decimal("5"),
    )


def test_build_long_entry_rounds_buy_price_and_quantity():
    """long新規注文をtick・qtyStepへ切り下げることをテストする。"""
    allocator, state = _allocator()

    order = build_entry_order(
        allocator,
        state,
        _instrument(),
        side="long",
        reference_price=Decimal("100.07"),
    )

    assert order.exchange_side == "buy"
    assert order.price == Decimal("100.0")
    assert order.quantity == Decimal("100")
    assert order.estimated_notional == Decimal("10000.0")
    assert state.long_positions == {}


def test_build_short_entry_rounds_sell_price_and_does_not_mutate_state():
    """short新規注文を作り、約定前は配分状態を変更しないことをテストする。"""
    allocator, state = _allocator()

    order = build_entry_order(
        allocator,
        state,
        _instrument(),
        side="short",
        reference_price=Decimal("100.07"),
    )

    assert order.exchange_side == "sell"
    assert order.price == Decimal("100.1")
    assert order.quantity == Decimal("99.9")
    assert order.reduce_only is False
    assert state.short_positions == {}


def test_build_exit_order_is_reduce_only_and_uses_opposite_side():
    """決済注文をreduce-onlyとし、建玉と逆方向へ送ることをテストする。"""
    order = build_exit_order(
        _instrument(),
        side="short",
        quantity=Decimal("99.97"),
        reference_price=Decimal("90.04"),
    )

    assert order.exchange_side == "buy"
    assert order.price == Decimal("90.0")
    assert order.quantity == Decimal("99.9")
    assert order.reduce_only is True


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"side": "long", "reference_price": Decimal("0")}, "invalid_price"),
        ({"side": "long", "reference_price": Decimal("100"), "lot_count": 2}, "allocation:per_symbol_cap"),
        ({"side": "long", "reference_price": Decimal("100"), "order_type": "stop"}, "invalid_order_type"),
    ],
)
def test_entry_preflight_rejects_unsafe_candidates(kwargs, reason):
    """不正価格、配分上限、未対応注文種別を拒否することをテストする。"""
    allocator, state = _allocator()

    with pytest.raises(OrderRejected, match=reason):
        build_entry_order(allocator, state, _instrument(), **kwargs)


def test_instrument_record_is_converted_from_zoomex_metadata():
    """ZOOMEX instruments-infoレコードを注文仕様へ変換することをテストする。"""
    instrument = ZoomexInstrument.from_instrument_record(
        {
            "symbol": "AAAUSDT",
            "priceFilter": {"tickSize": "0.1"},
            "lotSizeFilter": {
                "qtyStep": "0.1",
                "minOrderQty": "0.1",
                "minNotionalValue": "5",
            },
        }
    )

    assert instrument.tick_size == Decimal("0.1")
    assert instrument.min_order_notional == Decimal("5")
