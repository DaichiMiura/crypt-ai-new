from scripts.run_exp_2026_0011 import _classify


def test_classify_accepts_balanced_drawdown_and_return_retention():
    """DD改善と収益維持の全条件を満たす場合に過去候補とすることをテストする。"""
    result = _classify(
        {
            "max_drawdown_improved_periods": 4,
            "median_max_drawdown_improvement": 0.08,
            "aggregate_final_equity_retention": 0.92,
            "scaled_worse_in_full_loss_periods": 0,
            "scaled_entries_below_full": 8,
        }
    )

    assert result["research_status"] == "PASSED_RETROSPECTIVE_VALIDATION"
    assert result["promotion_status"] == "NEEDS_FORWARD_EVIDENCE"


def test_classify_rejects_size_reduction_that_worsens_a_loss_year():
    """full-size損失年を悪化させるサイズ調整を棄却することをテストする。"""
    result = _classify(
        {
            "max_drawdown_improved_periods": 4,
            "median_max_drawdown_improvement": 0.08,
            "aggregate_final_equity_retention": 0.92,
            "scaled_worse_in_full_loss_periods": 1,
            "scaled_entries_below_full": 8,
        }
    )

    assert result["research_status"] == "REJECTED"
    assert result["promotion_status"] == "NOT_ELIGIBLE"
