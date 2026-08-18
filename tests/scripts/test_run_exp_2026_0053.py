from scripts.run_exp_2026_0052 import FEATURE_NAMES, MIN_TRAIN_SAMPLES
from scripts.run_exp_2026_0053 import (
    _decision,
    _fit_ridge,
    _nearest_rank_percentile,
    _predict_volatility,
)


def _summary(*, pnl: str, drawdown: str, entries: int = 12, exits: int = 12) -> dict[str, object]:
    """判定テスト用の期間summaryを作る。"""

    return {
        "net_pnl": pnl,
        "max_drawdown": drawdown,
        "entry_count": entries,
        "exit_count": exits,
    }


def test_ridge_refuses_insufficient_training_samples():
    """最低標本数未満では安全側へ倒せるようモデルを作らない。"""

    rows = [(0.0,) * len(FEATURE_NAMES)] * (MIN_TRAIN_SAMPLES - 1)
    targets = [float(index) for index in range(MIN_TRAIN_SAMPLES - 1)]

    assert _fit_ridge(rows, targets) is None


def test_ridge_learns_deterministic_positive_relation():
    """固定ridge学習が単純なvolatility関係を決定論的に学習する。"""

    rows = []
    targets = []
    for index in range(MIN_TRAIN_SAMPLES):
        value = index / MIN_TRAIN_SAMPLES
        rows.append((value, 0.0, 1.0, 0.1))
        targets.append(0.01 + value * 0.02)

    first = _fit_ridge(rows, targets)
    second = _fit_ridge(rows, targets)

    assert first == second
    assert first is not None
    assert _predict_volatility(first, (0.8, 0.0, 1.0, 0.1)) > _predict_volatility(
        first, (0.2, 0.0, 1.0, 0.1)
    )


def test_nearest_rank_percentile_is_fixed():
    """75th percentileが補間せず固定順位を返す。"""

    assert _nearest_rank_percentile([4.0, 1.0, 3.0, 2.0], 0.75) == 3.0


def test_decision_rejects_profit_retention_and_drawdown_failure():
    """利益維持とDD改善の不足を独立した棄却理由にする。"""

    decision, reasons = _decision(
        _summary(pnl="100", drawdown="-0.10"),
        _summary(pnl="89", drawdown="-0.08"),
        _summary(pnl="1", drawdown="-0.08"),
    )

    assert decision == "REJECTED"
    assert reasons == [
        "evaluation_net_pnl_retention_below_90pct",
        "evaluation_drawdown_improvement_below_3_points",
    ]


def test_decision_requires_same_completed_trade_count():
    """元本縮小が元戦略の取引数を削減した場合は棄却する。"""

    decision, reasons = _decision(
        _summary(pnl="100", drawdown="-0.10", entries=12, exits=12),
        _summary(pnl="95", drawdown="-0.06", entries=11, exits=11),
        _summary(pnl="1", drawdown="-0.06", entries=11, exits=11),
    )

    assert decision == "REJECTED"
    assert reasons == ["evaluation_completed_round_trips_below_baseline"]


def test_decision_accepts_all_fixed_conditions():
    """全固定条件を満たす場合だけretrospective validation通過とする。"""

    decision, reasons = _decision(
        _summary(pnl="100", drawdown="-0.10"),
        _summary(pnl="90", drawdown="-0.07"),
        _summary(pnl="1", drawdown="-0.08"),
    )

    assert decision == "PASSED_RETROSPECTIVE_VALIDATION"
    assert reasons == []
