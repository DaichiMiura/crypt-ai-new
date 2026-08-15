from scripts.run_exp_2026_0010 import _classify


def test_classify_rejects_when_combined_drawdown_improvement_is_not_robust():
    """combinedの最大DD改善が2年未満なら棄却することをテストする。"""
    result = _classify(
        {
            "combined_max_drawdown_improved_periods": 1,
            "combined_median_max_drawdown_improvement": -0.04,
            "combined_cagr_at_least_long_periods": 2,
            "short_closed_round_trips": 7,
        }
    )

    assert result["research_status"] == "REJECTED"
    assert result["promotion_status"] == "NOT_ELIGIBLE"


def test_classify_requires_all_candidate_criteria():
    """shortの取引回数だけ満たしても候補に昇格しないことをテストする。"""
    result = _classify(
        {
            "combined_max_drawdown_improved_periods": 3,
            "combined_median_max_drawdown_improvement": 0.01,
            "combined_cagr_at_least_long_periods": 2,
            "short_closed_round_trips": 5,
        }
    )

    assert result["research_status"] == "PASSED_RETROSPECTIVE_VALIDATION"
    assert result["promotion_status"] == "NEEDS_FORWARD_EVIDENCE"
