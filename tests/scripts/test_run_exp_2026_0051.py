from scripts.run_exp_2026_0051 import SIGNAL_CONFIG, _decision


def _summary(*, pnl: str, drawdown: str, entries: int, exits: int) -> dict[str, object]:
    """判定テスト用の最小holdout summaryを作る。"""

    return {
        "net_pnl": pnl,
        "max_drawdown": drawdown,
        "entry_count": entries,
        "exit_count": exits,
    }


def test_preregistered_low_vol_parameters_are_frozen():
    """事前登録したvol、regime、週次、top2条件が固定されていることをテストする。"""

    assert SIGNAL_CONFIG.volatility_window_bars == 360
    assert SIGNAL_CONFIG.regime_window_bars == 2160
    assert SIGNAL_CONFIG.rebalance_bars == 84
    assert SIGNAL_CONFIG.selected_count == 2
    assert SIGNAL_CONFIG.signal_delay_bars == 1


def test_decision_requires_ten_completed_leg_round_trips():
    """holdout完了往復が10未満なら正の損益でも棄却することをテストする。"""

    decision, reasons = _decision(
        _summary(pnl="1", drawdown="-0.01", entries=9, exits=9),
        _summary(pnl="1", drawdown="-0.01", entries=9, exits=9),
    )

    assert decision == "REJECTED"
    assert reasons == ["holdout_completed_leg_round_trips_below_10"]


def test_decision_accepts_positive_base_and_stress_with_bounded_drawdown():
    """全固定条件を満たす場合だけbacktest候補になることをテストする。"""

    decision, reasons = _decision(
        _summary(pnl="1", drawdown="-0.09", entries=10, exits=10),
        _summary(pnl="1", drawdown="-0.09", entries=10, exits=10),
    )

    assert decision == "BACKTEST_CANDIDATE"
    assert reasons == []
