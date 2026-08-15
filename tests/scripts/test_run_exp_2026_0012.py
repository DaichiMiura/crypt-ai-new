from scripts.run_exp_2026_0012 import _classify


def test_classify_accepts_atr_exit_that_meets_all_criteria():
    """ATR退出が全候補条件を満たす場合に過去候補となることをテストする。"""
    result = _classify(
        {
            "max_drawdown_improved_periods": 3,
            "median_max_drawdown_improvement": 0.03,
            "aggregate_final_equity_retention": 0.90,
            "cagr_at_least_baseline_periods": 2,
            "atr_exit_signals": 3,
        }
    )

    assert result["research_status"] == "PASSED_RETROSPECTIVE_VALIDATION"
    assert result["promotion_status"] == "NEEDS_FORWARD_EVIDENCE"


def test_classify_rejects_atr_exit_without_drawdown_improvement():
    """ATR退出のDD改善が一貫しない場合に棄却することをテストする。"""
    result = _classify(
        {
            "max_drawdown_improved_periods": 1,
            "median_max_drawdown_improvement": -0.01,
            "aggregate_final_equity_retention": 0.95,
            "cagr_at_least_baseline_periods": 3,
            "atr_exit_signals": 5,
        }
    )

    assert result["research_status"] == "REJECTED"
    assert result["promotion_status"] == "NOT_ELIGIBLE"
