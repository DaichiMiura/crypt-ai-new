from datetime import timedelta

import pandas as pd
import pytest

from crypt_ai.void_short import (
    VOID_SHORT_CORE_POLICY,
    VOID_SHORT_SYMBOLS,
    VoidShortCorePolicy,
    prepare_void_short_trend_regime,
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
