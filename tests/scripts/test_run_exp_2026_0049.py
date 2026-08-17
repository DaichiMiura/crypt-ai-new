from scripts.run_exp_2026_0049 import SIGNAL_CONFIG, _decision


def _summary(*, pnl: str, drawdown: str, entries: int, exits: int) -> dict[str, object]:
    """判定テスト用の最小OOS summaryを作る。"""

    return {
        "net_pnl": pnl,
        "max_drawdown": drawdown,
        "entry_count": entries,
        "exit_count": exits,
    }


def test_preregistered_signal_parameters_are_frozen():
    """事前登録した選択条件がrunnerで固定されていることをテストする。"""

    assert SIGNAL_CONFIG.lookback_events == 6
    assert SIGNAL_CONFIG.holding_events == 6
    assert SIGNAL_CONFIG.rebalance_events == 6
    assert SIGNAL_CONFIG.beta_window_bars == 360
    assert SIGNAL_CONFIG.max_beta_gap == 0.25
    assert SIGNAL_CONFIG.minimum_projected_carry == 0.0048


def test_decision_rejects_insufficient_oos_round_trips():
    """OOS完了往復が30未満なら利益が正でも棄却することをテストする。"""

    decision, reasons = _decision(
        _summary(pnl="1", drawdown="-0.01", entries=29, exits=29),
        _summary(pnl="1", drawdown="-0.01", entries=29, exits=29),
    )

    assert decision == "REJECTED"
    assert reasons == ["oos_completed_round_trips_below_30"]


def test_decision_requires_positive_base_and_stress_results():
    """基本費用とstressの両方が正の場合だけ候補になることをテストする。"""

    decision, reasons = _decision(
        _summary(pnl="1", drawdown="-0.01", entries=30, exits=30),
        _summary(pnl="1", drawdown="-0.01", entries=30, exits=30),
    )

    assert decision == "BACKTEST_CANDIDATE"
    assert reasons == []
