#!/usr/bin/env python3
"""EXP-2026-0054の選択済みRidgeをsealed holdoutで一度だけ評価する。"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import yaml

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from scripts.run_exp_2026_0054_development import (  # noqa: E402
    EVALUATION_START,
    EXPERIMENT_ID,
    HORIZON,
    INITIAL_EQUITY,
    MODEL_SELECTION_START,
    SEALED_HOLDOUT_END,
    SEALED_HOLDOUT_START,
    _load_inputs,
    _sha256,
    build_samples,
    evaluate_predictions,
    fit_ridge,
    predict_ridge,
)


BOOTSTRAP_REPETITIONS = 10_000
BOOTSTRAP_BLOCK_DAYS = (1, 3, 7)


def _authorize_holdout(registry_path: Path, development_summary: Path) -> dict[str, object]:
    """登録済みdevelopment gateと未開封状態を照合する。

    Args:
        registry_path: EXP-2026-0054台帳。
        development_summary: 再現済みdevelopment成果物。

    Returns:
        読み込んだ台帳。

    Raises:
        ValueError: Ridge選択、成果物hash、または未開封条件が不一致の場合。
    """

    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    result = registry.get("evaluation", {}).get("development_result", {})
    execution = registry.get("execution_status", {})
    if registry.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("unexpected experiment registry")
    if result.get("selected_model") != "ridge" or result.get("holdout_authorized_by_preregistered_gate") is not True:
        raise ValueError("development gate did not authorize Ridge")
    if result.get("sealed_holdout_opened") is not False or execution.get("sealed_holdout_opened") is not False:
        raise ValueError("sealed holdout is already marked opened")
    if _sha256(development_summary) != result.get("result_sha256"):
        raise ValueError("development result hash mismatch")
    payload = json.loads(development_summary.read_text(encoding="utf-8"))
    if payload.get("selected_model") != "ridge" or payload.get("holdout_authorized") is not True:
        raise ValueError("development summary does not authorize Ridge")
    return registry


def _daily_returns(
    result: dict[str, object],
    start: pd.Timestamp = SEALED_HOLDOUT_START,
    end: pd.Timestamp = SEALED_HOLDOUT_END,
) -> np.ndarray:
    """取引PnLから暦日strategy returnを再構築する。

    Args:
        result: `evaluate_predictions`の結果。
        start: 最初のUTC日。
        end: exclusive終了日。

    Returns:
        各日の開始equity基準return。
    """

    pnl_by_day: dict[pd.Timestamp, Decimal] = {}
    for trade in result["trades"]:
        day = pd.Timestamp(trade.get("exit_time", trade["decision_time"])).floor("D")
        pnl_by_day[day] = pnl_by_day.get(day, Decimal("0")) + Decimal(trade["net_pnl"])
    equity = INITIAL_EQUITY
    returns: list[float] = []
    for day in pd.date_range(start.floor("D"), end.floor("D"), freq="1D", inclusive="left"):
        pnl = pnl_by_day.get(day, Decimal("0"))
        returns.append(float(pnl / equity))
        equity += pnl
    return np.asarray(returns, dtype=float)


def _circular_block_ci(
    differences: np.ndarray,
    block_days: int,
    *,
    repetitions: int = BOOTSTRAP_REPETITIONS,
    seed: int = 0,
) -> tuple[float, float]:
    """平均日次return差のcircular block bootstrap CIを返す。

    Args:
        differences: 候補からbaselineを引いた日次return。
        block_days: block長（日）。
        repetitions: 再標本化回数。
        seed: 固定乱数seed。

    Returns:
        2.5%点と97.5%点。

    Raises:
        ValueError: 入力、block、反復数が不正な場合。
    """

    values = np.asarray(differences, dtype=float)
    if values.ndim != 1 or len(values) == 0 or not np.isfinite(values).all():
        raise ValueError("bootstrap differences must be finite and non-empty")
    if block_days <= 0 or block_days > len(values) or repetitions <= 0:
        raise ValueError("invalid bootstrap configuration")
    rng = np.random.default_rng(seed)
    statistics = np.empty(repetitions, dtype=float)
    block_count = math.ceil(len(values) / block_days)
    offsets = np.arange(block_days)
    for repetition in range(repetitions):
        starts = rng.integers(0, len(values), size=block_count)
        indices = ((starts[:, None] + offsets) % len(values)).ravel()[: len(values)]
        statistics[repetition] = float(values[indices].mean())
    lower, upper = np.quantile(statistics, [0.025, 0.975])
    return float(lower), float(upper)


def _provisional_decision(
    candidate: dict[str, object],
    momentum: dict[str, object],
    stress: dict[str, object],
    bootstrap: dict[str, dict[str, float]],
    *,
    excluded_decision_times: int,
) -> tuple[str, list[str]]:
    """事前登録hard gateとbootstrap上限制約を機械判定する。

    Args:
        candidate: Ridge基本費用結果。
        momentum: momentum基本費用結果。
        stress: Ridge費用2倍結果。
        bootstrap: block別95% CI。
        excluded_decision_times: 欠測等で除外した判断時刻数。

    Returns:
        暫定research statusと理由。
    """

    reasons: list[str] = []
    candidate_pnl = Decimal(candidate["net_pnl"])
    if candidate_pnl <= 0:
        reasons.append("holdout_net_pnl_nonpositive")
    if candidate_pnl <= Decimal(momentum["net_pnl"]):
        reasons.append("holdout_did_not_beat_momentum")
    if int(candidate["completed_round_trips"]) < 30:
        reasons.append("holdout_completed_round_trips_below_30")
    if Decimal(stress["net_pnl"]) <= 0:
        reasons.append("stress_holdout_net_pnl_nonpositive")
    if Decimal(candidate["max_drawdown"]) <= Decimal("-0.10"):
        reasons.append("holdout_max_drawdown_not_better_than_minus_10pct")
    if excluded_decision_times:
        reasons.append("holdout_decision_times_excluded")
    if reasons:
        return "REJECTED", reasons
    if any(interval["lower"] <= 0 for interval in bootstrap.values()):
        return "INCONCLUSIVE", ["bootstrap_lower_bound_not_positive"]
    return "PASSED_FORWARD_TEST", []


def run_holdout(
    data_dir: Path,
    metadata_path: Path,
) -> dict[str, object]:
    """全固定条件でRidge、momentum、stressを一度評価する。

    Args:
        data_dir: DATA-2026-0006保存先。
        metadata_path: 取得metadata。

    Returns:
        暫定判定と完全な監査結果。

    Raises:
        ValueError: trainまたはholdout標本が空の場合。
    """

    prices, funding, rules = _load_inputs(
        data_dir, metadata_path, value_cutoff=SEALED_HOLDOUT_END
    )
    training, training_exclusions = build_samples(
        prices,
        funding,
        decision_start=EVALUATION_START,
        decision_end=SEALED_HOLDOUT_START,
        exit_end=SEALED_HOLDOUT_START,
    )
    holdout, holdout_exclusions = build_samples(
        prices,
        funding,
        decision_start=SEALED_HOLDOUT_START,
        decision_end=SEALED_HOLDOUT_END,
        exit_end=SEALED_HOLDOUT_END,
    )
    if not training or not holdout:
        raise ValueError("training or holdout samples are empty")
    model = fit_ridge(training)
    ridge_predictions = predict_ridge(model, holdout)
    momentum_predictions = np.asarray([sample.features[2] for sample in holdout], dtype=float)
    candidate = evaluate_predictions(holdout, ridge_predictions, rules, prices, funding)
    stress = evaluate_predictions(
        holdout,
        ridge_predictions,
        rules,
        prices,
        funding,
        cost_multiplier=Decimal("2"),
    )
    momentum = evaluate_predictions(
        holdout,
        momentum_predictions,
        rules,
        prices,
        funding,
        entry_threshold=0.0,
    )
    differences = _daily_returns(candidate) - _daily_returns(momentum)
    bootstrap = {
        f"{block_days * 24}h": {
            "lower": interval[0],
            "upper": interval[1],
            "repetitions": BOOTSTRAP_REPETITIONS,
            "seed": 0,
        }
        for block_days in BOOTSTRAP_BLOCK_DAYS
        for interval in [_circular_block_ci(differences, block_days)]
    }
    status, reasons = _provisional_decision(
        candidate,
        momentum,
        stress,
        bootstrap,
        excluded_decision_times=len(holdout_exclusions),
    )
    targets = np.asarray([sample.target for sample in holdout], dtype=float)
    return {
        "experiment_id": EXPERIMENT_ID,
        "stage": "SEALED_HOLDOUT_COMPLETED",
        "sealed_holdout_opened": True,
        "selected_model": "ridge",
        "provisional_research_status": status,
        "decision_reasons": reasons,
        "promotion_status": "NOT_ELIGIBLE",
        "independent_validation_status": "PENDING",
        "sample_counts": {
            "refit_training_rows": len(training),
            "holdout_rows": len(holdout),
            "refit_training_excluded_decision_times": len(training_exclusions),
            "holdout_excluded_decision_times": len(holdout_exclusions),
        },
        "diagnostics": {
            "gross_return_mae": float(np.mean(np.abs(ridge_predictions - targets))),
            "directional_accuracy": float(np.mean((ridge_predictions > 0) == (targets > 0))),
        },
        "results": {
            "ridge": candidate,
            "ridge_stress_2x_cost": stress,
            "momentum": momentum,
        },
        "bootstrap_ml_minus_momentum": bootstrap,
    }


def main() -> None:
    """開封gateを検査し、holdout成果物を新規作成する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/EXP-2026-0054"))
    parser.add_argument("--metadata", type=Path, default=Path("var/exp-2026-0054-data.json"))
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("experiments/registry/EXP-2026-0054-hypothesis.yaml"),
    )
    parser.add_argument(
        "--development-summary",
        type=Path,
        default=Path("artifacts/EXP-2026-0054-development/summary.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/EXP-2026-0054-holdout/summary.json"),
    )
    args = parser.parse_args()
    sentinel = args.output.with_suffix(".opened.json")
    if args.output.exists() or sentinel.exists():
        raise ValueError("holdout output already exists; refusing a second primary run")
    _authorize_holdout(args.registry, args.development_summary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with sentinel.open("x", encoding="utf-8") as handle:
        json.dump(
            {
                "experiment_id": EXPERIMENT_ID,
                "status": "SEALED_HOLDOUT_OPENING",
                "registry_sha256": _sha256(args.registry),
                "development_summary_sha256": _sha256(args.development_summary),
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
        handle.write("\n")
    payload = run_holdout(args.data_dir, args.metadata)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "experiment_id": payload["experiment_id"],
                "stage": payload["stage"],
                "sealed_holdout_opened": payload["sealed_holdout_opened"],
                "selected_model": payload["selected_model"],
                "provisional_research_status": payload["provisional_research_status"],
                "decision_reasons": payload["decision_reasons"],
                "sample_counts": payload["sample_counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
