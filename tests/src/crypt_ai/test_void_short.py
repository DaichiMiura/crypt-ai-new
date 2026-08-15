from datetime import timedelta
from decimal import Decimal

import pandas as pd
import pytest

from crypt_ai.void_short import (
    VOID_SHORT_CORE_POLICY,
    VOID_SHORT_FIBONACCI_RATIOS,
    VOID_SHORT_EXECUTION_LEVERAGE,
    VOID_SHORT_SIZING_REFERENCE_LEVERAGE,
    VOID_SHORT_TAKE_PROFIT_RATIOS,
    VOID_SHORT_SYMBOLS,
    VoidShortCorePolicy,
    VoidShortAdverseState,
    VoidShortSetupState,
    VoidShortStopDecision,
    build_void_short_stop_plan,
    build_void_short_fibonacci_levels,
    build_void_short_take_profits,
    evaluate_void_short_stop_bar,
    partition_touched_void_short_levels,
    pending_void_short_limits_must_cancel,
    prepare_void_short_entry_setup,
    prepare_void_short_trend_regime,
    size_void_short_limit_levels,
)


def _trend_frame(close: list[float]) -> pd.DataFrame:
    """テスト用の連続した2時間足を作る。"""
    return pd.DataFrame(
        {
            "event_time": pd.date_range(
                "2022-01-01T00:00:00Z", periods=len(close), freq="2h"
            ),
            "close": close,
            "is_interpolated": [False] * len(close),
        }
    )


def _entry_setup_frame() -> pd.DataFrame:
    """エントリー準備状態の遷移を作れる2時間足を作る。"""
    close = [200.0] * 200 + [100.0] * 200 + [100.0, 106.0, 102.0, 105.0]
    frame = _trend_frame(close)
    frame["high"] = frame["close"] + 1.0
    frame["low"] = frame["close"] - 1.0
    frame.loc[400, ["high", "low"]] = [101.0, 99.0]
    frame.loc[401, ["high", "low"]] = [107.0, 105.0]
    frame.loc[402, ["high", "low"]] = [107.0, 101.0]
    frame.loc[403, ["high", "low"]] = [106.0, 101.0]
    return frame


def test_core_policy_fixes_two_hour_bars_and_fourteen_days():
    """2時間足168本を14日の最大保有期間として固定することをテストする。"""
    assert VOID_SHORT_CORE_POLICY.bar_interval == timedelta(hours=2)
    assert VOID_SHORT_CORE_POLICY.max_holding_bars == 168
    assert VOID_SHORT_CORE_POLICY.max_holding_duration == timedelta(days=14)


def test_core_policy_permits_only_confirmed_limit_short_entry():
    """確定足による既知の指値ショートだけを許可することをテストする。"""
    assert VOID_SHORT_CORE_POLICY.permits_entry(
        symbol="LINKUSDT",
        side="short",
        order_type="limit",
        signal_bar_closed=True,
        setup_confirmed=True,
        state_known=True,
    )


@pytest.mark.parametrize(
    ("overrides"),
    [
        {"side": "long"},
        {"order_type": "market"},
        {"signal_bar_closed": False},
        {"setup_confirmed": False},
        {"state_known": False},
    ],
)
def test_core_policy_rejects_disallowed_or_unknown_entry(overrides):
    """禁止注文と未確定状態をNO_TRADEにすることをテストする。"""
    request = {
        "symbol": "LINKUSDT",
        "side": "short",
        "order_type": "limit",
        "signal_bar_closed": True,
        "setup_confirmed": True,
        "state_known": True,
    }
    request.update(overrides)

    assert VOID_SHORT_CORE_POLICY.permits_entry(**request) is False


def test_core_policy_allows_only_preregistered_symbols():
    """事前登録した6銘柄だけを許可することをテストする。"""
    assert VOID_SHORT_SYMBOLS == {
        "LINKUSDT",
        "UNIUSDT",
        "ADAUSDT",
        "AVAXUSDT",
        "NEARUSDT",
        "AAVEUSDT",
    }
    assert all(VOID_SHORT_CORE_POLICY.permits_symbol(symbol) for symbol in VOID_SHORT_SYMBOLS)
    assert VOID_SHORT_CORE_POLICY.permits_symbol("BTCUSDT") is False
    assert VOID_SHORT_CORE_POLICY.permits_symbol("linkusdt") is False


def test_core_policy_rejects_entry_for_unregistered_symbol():
    """未登録銘柄の新規エントリーを拒否することをテストする。"""
    assert VOID_SHORT_CORE_POLICY.permits_entry(
        symbol="BTCUSDT",
        side="short",
        order_type="limit",
        signal_bar_closed=True,
        setup_confirmed=True,
        state_known=True,
    ) is False


def test_core_policy_blocks_same_bar_normal_exit():
    """約定バー内の通常決済を許可しないことをテストする。"""
    assert VOID_SHORT_CORE_POLICY.can_evaluate_normal_exit(0) is False
    assert VOID_SHORT_CORE_POLICY.can_evaluate_normal_exit(1) is True


def test_core_policy_forces_exit_at_bar_168():
    """167本では継続し168本で時間切れになることをテストする。"""
    assert VOID_SHORT_CORE_POLICY.time_exit_due(167) is False
    assert VOID_SHORT_CORE_POLICY.time_exit_due(168) is True


def test_core_policy_rejects_safety_relaxation():
    """成行注文や本番取引を許可する設定変更を拒否することをテストする。"""
    with pytest.raises(ValueError, match="limit"):
        VoidShortCorePolicy(entry_order_type="market")
    with pytest.raises(ValueError, match="live"):
        VoidShortCorePolicy(live_trading_enabled=True)
    with pytest.raises(ValueError, match="allowed_symbols"):
        VoidShortCorePolicy(allowed_symbols=frozenset({"BTCUSDT"}))


def test_core_policy_rejects_negative_holding_bars():
    """負の保有バー数を拒否することをテストする。"""
    with pytest.raises(ValueError, match="non-negative"):
        VOID_SHORT_CORE_POLICY.time_exit_due(-1)


def test_trend_regime_requires_sma200_below_sma400():
    """SMA200がSMA400を下回る場合だけ下落トレンドにすることをテストする。"""
    frame = _trend_frame([200.0] * 200 + [100.0] * 201)

    result = prepare_void_short_trend_regime(frame)

    assert bool(result.loc[398, "trend_state_known_at_close"]) is False
    assert bool(result.loc[399, "trend_state_known_at_close"]) is True
    assert bool(result.loc[399, "downtrend_regime_at_close"]) is True
    assert bool(result.loc[399, "downtrend_regime_for_bar"]) is False
    assert bool(result.loc[400, "downtrend_regime_for_bar"]) is True


def test_trend_regime_treats_equal_smas_as_no_trade():
    """SMA200とSMA400が同値なら下落トレンドにしないことをテストする。"""
    result = prepare_void_short_trend_regime(_trend_frame([100.0] * 401))

    assert bool(result.loc[399, "trend_state_known_at_close"]) is True
    assert bool(result.loc[399, "downtrend_regime_at_close"]) is False
    assert bool(result.loc[400, "downtrend_regime_for_bar"]) is False


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda frame: frame.drop(index=10).reset_index(drop=True), "continuous"),
        (lambda frame: frame.assign(is_interpolated=True), "interpolated"),
        (lambda frame: frame.assign(close=0), "positive"),
    ],
)
def test_trend_regime_rejects_unusable_data(mutation, message):
    """欠損・補間・非正価格のデータを拒否することをテストする。"""
    frame = mutation(_trend_frame([100.0] * 401))

    with pytest.raises(ValueError, match=message):
        prepare_void_short_trend_regime(frame)


def test_entry_setup_requires_rally_pullback_and_rebound_in_order():
    """2 ATR上昇、1 ATR下落、0.5 ATR反発の順で準備完了することをテストする。"""
    result = prepare_void_short_entry_setup(_entry_setup_frame())

    assert result.loc[400, "entry_setup_state_at_close"] == VoidShortSetupState.WAIT_RALLY
    assert result.loc[401, "entry_setup_state_at_close"] == VoidShortSetupState.WAIT_PULLBACK
    assert result.loc[402, "entry_setup_state_at_close"] == VoidShortSetupState.WAIT_REBOUND
    assert result.loc[403, "entry_setup_state_at_close"] == VoidShortSetupState.READY
    assert bool(result.loc[403, "entry_setup_ready_at_close"]) is True
    assert bool(result.loc[403, "entry_setup_ready_for_bar"]) is False


def test_entry_setup_becomes_available_on_next_bar():
    """準備完了イベントと価格アンカーを次のバーへ遅延することをテストする。"""
    frame = _entry_setup_frame()
    next_row = frame.iloc[[-1]].copy()
    next_row["event_time"] += timedelta(hours=2)
    next_row[["close", "high", "low"]] = [104.0, 105.0, 103.0]
    result = prepare_void_short_entry_setup(pd.concat([frame, next_row], ignore_index=True))

    assert bool(result.loc[404, "entry_setup_ready_for_bar"]) is True
    assert result.loc[404, "rally_start_price_for_bar"] == 99.0
    assert result.loc[404, "rally_peak_price_for_bar"] == 107.0
    assert result.loc[404, "rebound_low_price_for_bar"] == 101.0
    assert (
        result.loc[404, "rally_start_time_for_bar"]
        < result.loc[404, "rally_peak_time_for_bar"]
        < result.loc[404, "rebound_low_time_for_bar"]
    )


def test_entry_setup_resets_when_downtrend_is_absent():
    """下落トレンドでなければ準備状態をNO_TRADEへ戻すことをテストする。"""
    frame = _trend_frame([100.0] * 401)
    frame["high"] = frame["close"] + 1.0
    frame["low"] = frame["close"] - 1.0

    result = prepare_void_short_entry_setup(frame)

    assert set(result["entry_setup_state_at_close"]) == {VoidShortSetupState.NO_TRADE}
    assert not result["entry_setup_ready_at_close"].any()


def test_entry_setup_rejects_invalid_ohlc_relationship():
    """終値が高値を上回る不正なOHLCを拒否することをテストする。"""
    frame = _entry_setup_frame()
    frame.loc[403, "high"] = 100.0

    with pytest.raises(ValueError, match="relationship"):
        prepare_void_short_entry_setup(frame)


def test_fibonacci_levels_use_fixed_ratios_and_round_sell_prices_up():
    """固定比率の理論価格をtick sizeへ切り上げることをテストする。"""
    levels = build_void_short_fibonacci_levels(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        current_price=Decimal("112"),
        tick_size=Decimal("0.1"),
    )

    assert tuple(level.ratio for level in levels) == VOID_SHORT_FIBONACCI_RATIOS
    assert tuple(level.raw_price for level in levels) == (
        Decimal("114.720"),
        Decimal("117.640"),
        Decimal("122.360"),
        Decimal("130.0"),
    )
    assert tuple(level.limit_price for level in levels) == (
        Decimal("114.8"),
        Decimal("117.7"),
        Decimal("122.4"),
        Decimal("130.0"),
    )


def test_fibonacci_levels_discard_marketable_sell_limits():
    """現在価格以下の売り指値を個別に破棄することをテストする。"""
    levels = build_void_short_fibonacci_levels(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        current_price=Decimal("118"),
        tick_size=Decimal("0.1"),
    )

    assert tuple(level.ratio for level in levels) == (
        Decimal("0.618"),
        Decimal("1.0"),
    )


def test_fibonacci_levels_return_empty_when_all_are_marketable():
    """全指値が現在価格以下ならNO_TRADE相当の空結果にすることをテストする。"""
    levels = build_void_short_fibonacci_levels(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        current_price=Decimal("131"),
        tick_size=Decimal("0.1"),
    )

    assert levels == ()


def test_fibonacci_levels_reject_invalid_anchor_order():
    """3アンカーの価格順序が不正なら指値を作らないことをテストする。"""
    with pytest.raises(ValueError, match="anchors"):
        build_void_short_fibonacci_levels(
            rally_start_price=Decimal("100"),
            rally_peak_price=Decimal("120"),
            rebound_low_price=Decimal("121"),
            current_price=Decimal("110"),
            tick_size=Decimal("0.1"),
        )


def test_pending_fibonacci_limits_expire_at_bar_168():
    """未約定指値が167本では有効で168本目に失効することをテストする。"""
    assert pending_void_short_limits_must_cancel(
        pending_bars=167, downtrend_regime=True, state_known=True
    ) is False
    assert pending_void_short_limits_must_cancel(
        pending_bars=168, downtrend_regime=True, state_known=True
    ) is True


def test_pending_fibonacci_limits_cancel_on_unknown_or_invalid_regime():
    """状態不明またはSMA条件無効で未約定指値を取り消すことをテストする。"""
    assert pending_void_short_limits_must_cancel(
        pending_bars=1, downtrend_regime=False, state_known=True
    ) is True
    assert pending_void_short_limits_must_cancel(
        pending_bars=1, downtrend_regime=True, state_known=False
    ) is True


def test_fibonacci_levels_are_touched_independently():
    """高値に到達した指値だけを独立して抽出することをテストする。"""
    levels = build_void_short_fibonacci_levels(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        current_price=Decimal("112"),
        tick_size=Decimal("0.1"),
    )

    touched, pending = partition_touched_void_short_levels(
        levels, bar_high=Decimal("118")
    )

    assert tuple(level.ratio for level in touched) == (
        Decimal("0.236"),
        Decimal("0.382"),
    )
    assert tuple(level.ratio for level in pending) == (
        Decimal("0.618"),
        Decimal("1.0"),
    )


def test_position_size_uses_twenty_percent_equity_per_level_without_profit_growth():
    """利益後も初期資産の20%を超えて1ロットを増額しないことをテストする。"""
    levels = build_void_short_fibonacci_levels(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        current_price=Decimal("112"),
        tick_size=Decimal("0.1"),
    )

    sized = size_void_short_limit_levels(
        levels,
        initial_equity=Decimal("100000"),
        current_equity=Decimal("120000"),
        qty_step=Decimal("0.001"),
        min_order_qty=Decimal("0.001"),
        min_order_notional=Decimal("5"),
    )

    assert VOID_SHORT_SIZING_REFERENCE_LEVERAGE == Decimal("20")
    assert VOID_SHORT_EXECUTION_LEVERAGE == Decimal("1")
    assert len(sized) == 4
    assert all(level.lot_count == 1 for level in sized)
    assert all(level.notional <= Decimal("20000") for level in sized)
    assert sum(level.notional for level in sized) <= Decimal("80000")


def test_position_size_shrinks_after_equity_loss():
    """現在資産が減少したら1ロットも20%へ縮小することをテストする。"""
    levels = build_void_short_fibonacci_levels(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        current_price=Decimal("118"),
        tick_size=Decimal("0.1"),
    )

    sized = size_void_short_limit_levels(
        levels,
        initial_equity=Decimal("100000"),
        current_equity=Decimal("50000"),
        qty_step=Decimal("0.001"),
        min_order_qty=Decimal("0.001"),
        min_order_notional=Decimal("5"),
    )

    assert len(sized) == 2
    assert all(level.notional <= Decimal("10000") for level in sized)
    assert sum(level.notional for level in sized) <= Decimal("20000")


def test_position_size_does_not_redistribute_removed_levels():
    """除外された指値のロットを残った水準へ再配分しないことをテストする。"""
    levels = build_void_short_fibonacci_levels(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        current_price=Decimal("118"),
        tick_size=Decimal("0.1"),
    )

    sized = size_void_short_limit_levels(
        levels,
        initial_equity=Decimal("100000"),
        current_equity=Decimal("100000"),
        qty_step=Decimal("0.001"),
        min_order_qty=Decimal("0.001"),
        min_order_notional=Decimal("5"),
    )

    assert len(sized) == 2
    assert all(level.notional <= Decimal("20000") for level in sized)
    assert sum(level.notional for level in sized) <= Decimal("40000")


def test_position_size_rounds_quantity_down_and_rejects_exchange_minimums():
    """数量を切り下げ、取引所最小条件を満たさない候補を破棄することをテストする。"""
    levels = build_void_short_fibonacci_levels(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        current_price=Decimal("112"),
        tick_size=Decimal("0.1"),
    )
    sized = size_void_short_limit_levels(
        levels,
        initial_equity=Decimal("100"),
        current_equity=Decimal("100"),
        qty_step=Decimal("0.1"),
        min_order_qty=Decimal("1"),
        min_order_notional=Decimal("10"),
    )

    assert sized == ()


def test_take_profits_split_position_between_0382_and_0618():
    """0.382で半分、0.618で残量を利確することをテストする。"""
    take_profits = build_void_short_take_profits(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        average_entry_price=Decimal("125"),
        open_quantity=Decimal("10"),
        tick_size=Decimal("0.1"),
        qty_step=Decimal("0.001"),
    )

    assert tuple(item.ratio for item in take_profits) == VOID_SHORT_TAKE_PROFIT_RATIOS
    assert tuple(item.raw_price for item in take_profits) == (
        Decimal("112.360"),
        Decimal("107.640"),
    )
    assert tuple(item.limit_price for item in take_profits) == (
        Decimal("112.3"),
        Decimal("107.6"),
    )
    assert tuple(item.quantity for item in take_profits) == (
        Decimal("5.000"),
        Decimal("5.000"),
    )


def test_take_profits_assign_all_quantity_to_deep_valid_level():
    """浅い利確が平均建値以上なら深い利確へ全数量を割り当てることをテストする。"""
    take_profits = build_void_short_take_profits(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        average_entry_price=Decimal("110"),
        open_quantity=Decimal("10"),
        tick_size=Decimal("0.1"),
        qty_step=Decimal("0.001"),
    )

    assert len(take_profits) == 1
    assert take_profits[0].ratio == Decimal("0.618")
    assert take_profits[0].quantity == Decimal("10")


def test_take_profits_return_empty_without_profitable_level():
    """平均建値より下の利確水準がなければ空結果にすることをテストする。"""
    take_profits = build_void_short_take_profits(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        average_entry_price=Decimal("105"),
        open_quantity=Decimal("10"),
        tick_size=Decimal("0.1"),
        qty_step=Decimal("0.001"),
    )

    assert take_profits == ()


def test_take_profits_put_quantity_rounding_residual_in_second_exit():
    """数量丸めの残量を0.618側へ含めることをテストする。"""
    take_profits = build_void_short_take_profits(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        average_entry_price=Decimal("125"),
        open_quantity=Decimal("1.001"),
        tick_size=Decimal("0.1"),
        qty_step=Decimal("0.001"),
    )

    assert take_profits[0].quantity == Decimal("0.500")
    assert take_profits[1].quantity == Decimal("0.501")
    assert sum(item.quantity for item in take_profits) == Decimal("1.001")


def test_take_profits_reject_quantity_outside_qty_step():
    """qtyStepに整合しない建玉数量を拒否することをテストする。"""
    with pytest.raises(ValueError, match="align"):
        build_void_short_take_profits(
            rally_start_price=Decimal("100"),
            rally_peak_price=Decimal("120"),
            average_entry_price=Decimal("125"),
            open_quantity=Decimal("1.0005"),
            tick_size=Decimal("0.1"),
            qty_step=Decimal("0.001"),
        )


def test_stop_plan_uses_1618_and_2618_extensions():
    """通常監視と緊急停止を1.618・2.618で作ることをテストする。"""
    plan = build_void_short_stop_plan(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        tick_size=Decimal("0.1"),
    )

    assert plan.arm_price == Decimal("142.4")
    assert plan.emergency_price == Decimal("162.4")


def test_stop_arms_without_same_bar_normal_exit():
    """1.618到達バーでは押し戻しても通常損切りしないことをテストする。"""
    plan = build_void_short_stop_plan(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        tick_size=Decimal("0.1"),
    )

    result = evaluate_void_short_stop_bar(
        plan=plan,
        state=VoidShortAdverseState(),
        mark_high=Decimal("145"),
        mark_close=Decimal("140"),
        atr=Decimal("2"),
        liquidation_price=Decimal("190"),
    )

    assert result.decision == VoidShortStopDecision.HOLD
    assert result.state.armed is True
    assert result.state.peak_mark_price == Decimal("145")


def test_stop_exits_after_one_atr_pullback_on_later_bar():
    """監視開始後の最高値から1 ATR下落して通常損切りすることをテストする。"""
    plan = build_void_short_stop_plan(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        tick_size=Decimal("0.1"),
    )
    state = VoidShortAdverseState(armed=True, peak_mark_price=Decimal("145"))

    result = evaluate_void_short_stop_bar(
        plan=plan,
        state=state,
        mark_high=Decimal("147"),
        mark_close=Decimal("144"),
        atr=Decimal("2"),
        liquidation_price=Decimal("190"),
    )

    assert result.decision == VoidShortStopDecision.NORMAL_STOP_NEXT_BAR
    assert result.state.peak_mark_price == Decimal("147")


def test_stop_emergency_exit_does_not_wait_for_pullback():
    """2.618到達時は押し戻しを待たず緊急停止することをテストする。"""
    plan = build_void_short_stop_plan(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        tick_size=Decimal("0.1"),
    )

    result = evaluate_void_short_stop_bar(
        plan=plan,
        state=VoidShortAdverseState(),
        mark_high=Decimal("163"),
        mark_close=Decimal("162"),
        atr=Decimal("2"),
        liquidation_price=Decimal("190"),
    )

    assert result.decision == VoidShortStopDecision.EMERGENCY_STOP


def test_stop_records_liquidation_before_strategy_exit():
    """同一バーで清算価格へ達した場合は清算を優先することをテストする。"""
    plan = build_void_short_stop_plan(
        rally_start_price=Decimal("100"),
        rally_peak_price=Decimal("120"),
        rebound_low_price=Decimal("110"),
        tick_size=Decimal("0.1"),
    )

    result = evaluate_void_short_stop_bar(
        plan=plan,
        state=VoidShortAdverseState(armed=True, peak_mark_price=Decimal("150")),
        mark_high=Decimal("170"),
        mark_close=Decimal("160"),
        atr=Decimal("2"),
        liquidation_price=Decimal("165"),
    )

    assert result.decision == VoidShortStopDecision.LIQUIDATION
