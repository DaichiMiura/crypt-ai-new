from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.run_exp_2026_0008 import _classify


def test_classify_marks_regime_filter_as_retrospective_candidate():
    """事前登録した年次基準を満たす場合に過去検証合格とすることをテストする。"""
    scorecard = {
        "max_drawdown_improved_periods": 3,
        "median_max_drawdown_improvement": 0.04,
        "cagr_at_least_base_periods": 4,
        "filtered_closed_round_trips": 11,
    }

    result = _classify(scorecard)

    assert result["research_status"] == "PASSED_RETROSPECTIVE_VALIDATION"
    assert result["promotion_status"] == "NEEDS_FORWARD_EVIDENCE"
