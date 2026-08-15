from scripts.run_exp_2026_0013 import _classify


def test_classify_marks_supportive_result_inconclusive_due_to_independence():
    """性能候補でも観測済み期間ならforward合格にしないことをテストする。"""
    result = _classify(
        {"atr_exit_signals": 2},
        {
            "max_drawdown_improvement": 0.03,
            "cagr_delta": 0.02,
            "final_equity_retention": 0.95,
        },
    )

    assert result["performance_candidate"] is True
    assert result["research_status"] == "INCONCLUSIVE"
    assert result["promotion_status"] == "NEEDS_FORWARD_EVIDENCE"


def test_classify_rejects_material_cagr_deterioration():
    """CAGRが5ポイント超悪化するATR退出を棄却することをテストする。"""
    result = _classify(
        {"atr_exit_signals": 2},
        {
            "max_drawdown_improvement": 0.01,
            "cagr_delta": -0.06,
            "final_equity_retention": 0.90,
        },
    )

    assert result["research_status"] == "REJECTED"
    assert result["promotion_status"] == "NOT_ELIGIBLE"
