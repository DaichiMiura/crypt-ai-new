"""VOID式ショート約定会計のテスト。"""

from decimal import Decimal

import pytest

from crypt_ai.void_short_accounting import (
    VoidShortCostModel,
    VoidShortLiquidity,
    VoidShortPosition,
    apply_void_short_funding,
    close_void_short,
    open_void_short_maker,
    open_void_short_taker,
)


def test_maker_entry_and_exit_include_both_fees():
    """makerの新規・決済手数料を確定損益へ含めることをテストする。"""
    costs = VoidShortCostModel(taker_slippage_rate=Decimal("0"))
    position = open_void_short_maker(
        VoidShortPosition(),
        quantity=Decimal("2"),
        limit_price=Decimal("100"),
        costs=costs,
    )
    position = close_void_short(
        position,
        quantity=Decimal("2"),
        raw_price=Decimal("90"),
        liquidity=VoidShortLiquidity.MAKER,
        costs=costs,
    )

    assert position.realized_net_pnl == Decimal("19.9240")
    assert position.trading_fees == Decimal("0.0760")


def test_multiple_entries_use_weighted_average_price():
    """複数の指値約定が数量加重平均建値になることをテストする。"""
    costs = VoidShortCostModel()
    position = open_void_short_maker(
        VoidShortPosition(),
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
        costs=costs,
    )
    position = open_void_short_maker(
        position, quantity=Decimal("3"), limit_price=Decimal("120"), costs=costs
    )

    assert position.quantity == Decimal("4")
    assert position.average_entry_price == Decimal("115")


def test_taker_cover_applies_adverse_slippage_and_fee():
    """市場買戻しへ上方スリッページとtaker手数料を課すことをテストする。"""
    costs = VoidShortCostModel(taker_slippage_rate=Decimal("0.001"))
    position = open_void_short_maker(
        VoidShortPosition(),
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
        costs=costs,
    )
    position = close_void_short(
        position,
        quantity=Decimal("1"),
        raw_price=Decimal("110"),
        liquidity=VoidShortLiquidity.TAKER,
        costs=costs,
    )

    assert position.realized_net_pnl == Decimal("-10.1960660")
    assert position.trading_fees == Decimal("0.0860660")


def test_taker_short_entry_applies_downward_slippage_and_fee():
    """市場売りエントリーへ下方スリッページとtaker手数料を課すことをテストする。"""
    costs = VoidShortCostModel(taker_slippage_rate=Decimal("0.001"))
    position = open_void_short_taker(
        VoidShortPosition(),
        quantity=Decimal("1"),
        raw_price=Decimal("100"),
        costs=costs,
    )

    assert position.average_entry_price == Decimal("99.900")
    assert position.cash_flow == Decimal("99.840060")
    assert position.trading_fees == Decimal("0.059940")


def test_positive_funding_is_income_for_short():
    """正のFunding率をショートの受取として計上することをテストする。"""
    costs = VoidShortCostModel()
    position = open_void_short_maker(
        VoidShortPosition(),
        quantity=Decimal("2"),
        limit_price=Decimal("100"),
        costs=costs,
    )
    position = apply_void_short_funding(
        position, mark_price=Decimal("110"), funding_rate=Decimal("0.0001")
    )

    assert position.funding_cash_flow == Decimal("0.0220")
    assert position.cash_flow == Decimal("199.9820")


def test_close_rejects_quantity_above_position():
    """保有数量を超えるreduce-only決済を拒否することをテストする。"""
    costs = VoidShortCostModel()
    position = open_void_short_maker(
        VoidShortPosition(),
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
        costs=costs,
    )

    with pytest.raises(ValueError, match="must not exceed"):
        close_void_short(
            position,
            quantity=Decimal("2"),
            raw_price=Decimal("90"),
            liquidity=VoidShortLiquidity.MAKER,
            costs=costs,
        )
