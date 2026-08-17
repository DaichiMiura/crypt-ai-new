from scripts.run_exp_2026_0050 import SIGNAL_CONFIG, _decision


def _summary(*, pnl: str, drawdown: str, leg_entries: int, leg_exits: int) -> dict[str, object]:
    """判定テスト用の最小holdout summaryを作る。"""

    return {
        "net_pnl": pnl,
        "max_drawdown": drawdown,
        "entry_count": leg_entries,
        "exit_count": leg_exits,
    }


def test_preregistered_pair_parameters_are_frozen():
    """事前登録したpair、窓、閾値、時間切れが固定されていることをテストする。"""

    assert SIGNAL_CONFIG.regression_window_bars == 720
    assert SIGNAL_CONFIG.spread_window_bars == 360
    assert SIGNAL_CONFIG.entry_z == 2.0
    assert SIGNAL_CONFIG.exit_z == 0.5
    assert SIGNAL_CONFIG.stop_z == 4.0
    assert SIGNAL_CONFIG.max_holding_bars == 168


def test_decision_counts_two_legs_as_one_pair_round_trip():
    """40脚のentry/exitを20 pair往復として候補判定することをテストする。"""

    decision, reasons = _decision(
        _summary(pnl="1", drawdown="-0.01", leg_entries=40, leg_exits=40),
        _summary(pnl="1", drawdown="-0.01", leg_entries=40, leg_exits=40),
    )

    assert decision == "BACKTEST_CANDIDATE"
    assert reasons == []


def test_decision_rejects_nonpositive_stress_holdout():
    """基本費用が正でもstressが非正なら棄却することをテストする。"""

    decision, reasons = _decision(
        _summary(pnl="1", drawdown="-0.01", leg_entries=40, leg_exits=40),
        _summary(pnl="0", drawdown="-0.01", leg_entries=40, leg_exits=40),
    )

    assert decision == "REJECTED"
    assert reasons == ["stress_holdout_net_pnl_nonpositive"]
