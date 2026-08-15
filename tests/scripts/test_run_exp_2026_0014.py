from scripts.run_exp_2026_0014 import _classify


def _scorecard(candidate: bool, rejected: bool = False) -> dict[str, object]:
    """銘柄判定テスト用scorecardを作る。"""
    return {
        "candidate_passed": candidate,
        "rejection": {
            "median_max_drawdown_improvement_non_positive": rejected,
            "aggregate_final_equity_retention_below_80_percent": False,
            "atr_exit_signals_fewer_than_3": False,
        },
    }


def test_classify_passes_when_two_symbols_meet_fixed_criteria():
    """3銘柄中2銘柄が候補基準を満たす場合に合格候補とすることをテストする。"""
    result = _classify(
        {
            "ETHUSDT": _scorecard(True),
            "SOLUSDT": _scorecard(True),
            "XRPUSDT": _scorecard(False, True),
        }
    )

    assert result["research_status"] == "PASSED_RETROSPECTIVE_VALIDATION"
    assert result["candidate_symbols"] == ["ETHUSDT", "SOLUSDT"]


def test_classify_rejects_when_two_symbols_have_non_positive_dd_effect():
    """2銘柄以上でDD改善が非正なら棄却することをテストする。"""
    result = _classify(
        {
            "ETHUSDT": _scorecard(False, True),
            "SOLUSDT": _scorecard(False, True),
            "XRPUSDT": _scorecard(True),
        }
    )

    assert result["research_status"] == "REJECTED"
