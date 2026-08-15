from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.run_exp_2026_0009 import _classify


def test_classify_marks_no_trade_period_inconclusive():
    """取引なしで性能差が出ても独立forward合格にしないことをテストする。"""
    filtered = {"trade_statistics": {"closed_round_trips": 0}}
    comparison = {
        "cagr_delta": 0.19,
        "max_drawdown_improvement": 0.14,
        "final_equity_delta": 115.0,
    }

    result = _classify(filtered, comparison)

    assert result["performance_candidate"] is False
    assert result["research_status"] == "INCONCLUSIVE"
    assert result["promotion_status"] == "NEEDS_FORWARD_EVIDENCE"
