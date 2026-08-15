from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.run_exp_2026_0007 import _classify


def test_classify_rejects_overlay_with_large_forward_deterioration():
    """forward期間でoverlayのDDとCAGRが大幅悪化した場合に棄却することをテストする。"""
    base = {"strategy": {"cagr": -0.19, "max_drawdown": -0.15}}
    overlay = {
        "strategy": {"cagr": -0.59, "max_drawdown": -0.44},
        "trade_statistics": {"closed_round_trips": 2},
    }
    comparison = {
        "cagr_delta": -0.40,
        "max_drawdown_improvement": -0.29,
        "final_equity_delta": -286.0,
    }

    result = _classify(overlay, base, comparison)

    assert result["research_status"] == "REJECTED"
    assert result["promotion_status"] == "NOT_ELIGIBLE"


def test_classify_requires_three_closed_round_trips_for_candidate():
    """closed round tripsが3件未満なら候補条件を満たさないことをテストする。"""
    base = {"strategy": {"cagr": 0.1, "max_drawdown": -0.2}}
    overlay = {
        "strategy": {"cagr": 0.1, "max_drawdown": -0.2},
        "trade_statistics": {"closed_round_trips": 2},
    }
    comparison = {
        "cagr_delta": 0.0,
        "max_drawdown_improvement": 0.0,
        "final_equity_delta": 0.0,
    }

    result = _classify(overlay, base, comparison)

    assert result["candidate_criteria"]["closed_round_trips_at_least_3"] is False
    assert result["research_status"] == "INCONCLUSIVE"
    assert result["promotion_status"] == "NEEDS_FORWARD_EVIDENCE"
