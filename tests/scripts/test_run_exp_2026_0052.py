import math

from scripts.run_exp_2026_0052 import (
    FEATURE_NAMES,
    MIN_TRAIN_SAMPLES,
    _decision,
    _fit_logistic,
    _matured_samples,
    _predict_probability,
)


def _summary(*, pnl: str, drawdown: str, entries: int = 12, exits: int = 12) -> dict[str, object]:
    """判定テスト用の期間summaryを作る。"""

    return {
        "net_pnl": pnl,
        "max_drawdown": drawdown,
        "entry_count": entries,
        "exit_count": exits,
    }


def test_logistic_refuses_insufficient_training_samples():
    """最低標本数未満ではfail closedでモデルを作らない。"""

    rows = [(0.0,) * len(FEATURE_NAMES)] * (MIN_TRAIN_SAMPLES - 1)
    labels = [0, 1] * ((MIN_TRAIN_SAMPLES - 1) // 2) + [0]

    assert _fit_logistic(rows, labels) is None


def test_training_samples_exclude_unfinished_future_labels():
    """判断足より後に終わるlabelを学習へ混入させない。"""

    samples = [
        {"label_end_index": 99, "name": "past"},
        {"label_end_index": 100, "name": "current_open_known"},
        {"label_end_index": 101, "name": "future"},
    ]

    assert [row["name"] for row in _matured_samples(samples, 100)] == [
        "past",
        "current_open_known",
    ]


def test_logistic_learns_a_deterministic_separation():
    """固定学習が単純な正負labelを決定論的に分離する。"""

    rows = []
    labels = []
    for index in range(MIN_TRAIN_SAMPLES):
        sign = -1.0 if index < MIN_TRAIN_SAMPLES // 2 else 1.0
        rows.append((sign, 0.0, 1.0, 0.1))
        labels.append(int(sign > 0))

    first = _fit_logistic(rows, labels)
    second = _fit_logistic(rows, labels)

    assert first == second
    assert first is not None
    assert _predict_probability(first, (-1.0, 0.0, 1.0, 0.1)) < 0.5
    assert _predict_probability(first, (1.0, 0.0, 1.0, 0.1)) > 0.5


def test_logistic_predictions_are_finite_for_extreme_inputs():
    """極端な有限特徴量でも予測確率が有限範囲に収まる。"""

    rows = [
        ((-1.0 if index % 2 == 0 else 1.0), 0.0, 1.0, 0.1)
        for index in range(MIN_TRAIN_SAMPLES)
    ]
    labels = [index % 2 for index in range(MIN_TRAIN_SAMPLES)]
    model = _fit_logistic(rows, labels)

    assert model is not None
    probability = _predict_probability(model, (1e100, -1e100, 4.0, 1e50))
    assert math.isfinite(probability)
    assert 0.0 <= probability <= 1.0


def test_decision_requires_improvement_and_positive_stress():
    """baseline非改善とstress損失を独立した棄却理由にする。"""

    decision, reasons = _decision(
        _summary(pnl="5", drawdown="-0.10"),
        _summary(pnl="5", drawdown="-0.11"),
        _summary(pnl="-1", drawdown="-0.11"),
    )

    assert decision == "REJECTED"
    assert reasons == [
        "evaluation_net_pnl_not_above_baseline",
        "evaluation_max_drawdown_worse_than_baseline",
        "stress_evaluation_net_pnl_nonpositive",
    ]


def test_decision_accepts_only_all_fixed_conditions():
    """全固定条件を満たす場合だけretrospective validation通過とする。"""

    decision, reasons = _decision(
        _summary(pnl="5", drawdown="-0.10"),
        _summary(pnl="6", drawdown="-0.09"),
        _summary(pnl="1", drawdown="-0.10"),
    )

    assert decision == "PASSED_RETROSPECTIVE_VALIDATION"
    assert reasons == []
