#!/usr/bin/env python3
"""EXP-2026-0052の時点整合ML trade-quality filterを実行する。"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path
import sys

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.portfolio import AllocatedPortfolioResult, run_allocated_portfolio  # noqa: E402
from crypt_ai.research import CostModel  # noqa: E402
from scripts.run_exp_2026_0035 import (  # noqa: E402
    EVALUATION_END,
    EVALUATION_START,
    _allocation_config,
    _load_raw_frames,
    _prepare_momentum_frames,
)
from scripts.run_exp_2026_0042 import _apply_last_rank_exit  # noqa: E402
from scripts.run_exp_2026_0048 import _segment_summary  # noqa: E402


EXPERIMENT_ID = "EXP-2026-0052"
RETROSPECTIVE_START = pd.Timestamp("2025-01-01T00:00:00Z")
FEATURE_NAMES = (
    "momentum_return",
    "market_median_momentum",
    "cross_sectional_rank",
    "realized_volatility_84",
)
TARGET_BARS = 84
MIN_TRAIN_SAMPLES = 80
PROBABILITY_THRESHOLD = 0.50
TRAINING_ITERATIONS = 800
LEARNING_RATE = 0.05
L2_PENALTY = 0.01
ROUND_TRIP_HURDLE = 0.0032
BASE_COST_MODEL = CostModel(
    fee_rate=Decimal("0.0006"),
    round_trip_spread=Decimal("0.001"),
    slippage_per_fill=Decimal("0.0005"),
)
STRESS_COST_MODEL = CostModel(
    fee_rate=Decimal("0.0012"),
    round_trip_spread=Decimal("0.002"),
    slippage_per_fill=Decimal("0.001"),
)


def _sigmoid(value: float) -> float:
    """overflowを避けてlogistic sigmoidを計算する。

    Args:
        value: 線形予測値。

    Returns:
        0から1の予測確率。
    """

    if value >= 0:
        inverse = math.exp(-value)
        return 1.0 / (1.0 + inverse)
    exponent = math.exp(value)
    return exponent / (1.0 + exponent)


def _fit_logistic(
    rows: list[tuple[float, ...]], labels: list[int]
) -> dict[str, object] | None:
    """固定設定のL2 logistic回帰をbatch gradient descentで学習する。

    Args:
        rows: 特徴量行。
        labels: 0または1の教師label。

    Returns:
        標準化量と係数。標本不足または単一classならNone。

    Raises:
        ValueError: 行数、特徴量数、値、labelが不正な場合。
    """

    if len(rows) != len(labels):
        raise ValueError("feature and label lengths differ")
    if rows and any(len(row) != len(FEATURE_NAMES) for row in rows):
        raise ValueError("unexpected feature count")
    if any(label not in (0, 1) for label in labels):
        raise ValueError("labels must contain only 0 or 1")
    if any(not math.isfinite(value) for row in rows for value in row):
        raise ValueError("features must be finite")
    if len(rows) < MIN_TRAIN_SAMPLES or len(set(labels)) < 2:
        return None

    count = float(len(rows))
    means = [sum(row[index] for row in rows) / count for index in range(len(FEATURE_NAMES))]
    scales = []
    for index, mean in enumerate(means):
        variance = sum((row[index] - mean) ** 2 for row in rows) / count
        scales.append(math.sqrt(variance) if variance > 0 else 1.0)
    standardized = [
        tuple((row[index] - means[index]) / scales[index] for index in range(len(FEATURE_NAMES)))
        for row in rows
    ]
    weights = [0.0] * len(FEATURE_NAMES)
    intercept = 0.0
    for _ in range(TRAINING_ITERATIONS):
        weight_gradients = [0.0] * len(FEATURE_NAMES)
        intercept_gradient = 0.0
        for row, label in zip(standardized, labels, strict=True):
            error = _sigmoid(intercept + sum(w * x for w, x in zip(weights, row, strict=True))) - label
            intercept_gradient += error
            for index, value in enumerate(row):
                weight_gradients[index] += error * value
        intercept -= LEARNING_RATE * intercept_gradient / count
        for index in range(len(weights)):
            gradient = weight_gradients[index] / count + L2_PENALTY * weights[index]
            weights[index] -= LEARNING_RATE * gradient
    return {"means": means, "scales": scales, "weights": weights, "intercept": intercept}


def _predict_probability(model: dict[str, object], row: tuple[float, ...]) -> float:
    """学習済みlogisticモデルで1標本の確率を返す。

    Args:
        model: `_fit_logistic`が返したモデル。
        row: 未標準化の特徴量。

    Returns:
        label 1の予測確率。

    Raises:
        ValueError: 特徴量が非有限または次元不一致の場合。
    """

    if len(row) != len(FEATURE_NAMES) or any(not math.isfinite(value) for value in row):
        raise ValueError("invalid prediction features")
    means = list(model["means"])
    scales = list(model["scales"])
    weights = list(model["weights"])
    standardized = [
        (value - float(means[index])) / float(scales[index])
        for index, value in enumerate(row)
    ]
    linear = float(model["intercept"]) + sum(
        float(weight) * value
        for weight, value in zip(weights, standardized, strict=True)
    )
    return _sigmoid(linear)


def _matured_samples(
    samples: list[dict[str, object]], decision_index: int
) -> list[dict[str, object]]:
    """判断時点までにlabel終端が到来した標本だけを返す。

    Args:
        samples: `label_end_index`を持つ学習候補。
        decision_index: 現在の判断足index。

    Returns:
        未来の価格を含まない学習標本。
    """

    return [
        sample
        for sample in samples
        if int(sample["label_end_index"]) <= decision_index
    ]


def _prepare_ml_filter(
    base_frames: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """matured labelだけで逐次学習し、EXP-0042候補をfilterする。

    Args:
        base_frames: EXP-0042の最下位退出適用前top2シグナル。

    Returns:
        ML採否後に最下位退出を適用したframeと週次予測監査表。

    Raises:
        ValueError: 銘柄間時刻、必須値、または価格が不正な場合。
    """

    symbols = tuple(sorted(base_frames))
    if not symbols:
        raise ValueError("base frames are empty")
    times = [tuple(pd.to_datetime(base_frames[symbol]["event_time"], utc=True)) for symbol in symbols]
    if any(times[0] != value for value in times[1:]):
        raise ValueError("ML filter timestamps differ")
    enriched: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame = base_frames[symbol].copy()
        returns = pd.to_numeric(frame["close"], errors="coerce").pct_change(fill_method=None)
        frame["realized_volatility_84"] = returns.rolling(TARGET_BARS).std(ddof=0)
        enriched[symbol] = frame

    rebalance_indices = [
        index
        for index, value in enumerate(enriched[symbols[0]]["rebalance_signal"])
        if bool(value)
    ]
    samples: list[dict[str, object]] = []
    for index in rebalance_indices:
        for symbol in symbols:
            frame = enriched[symbol]
            if index + TARGET_BARS + 1 >= len(frame):
                continue
            feature_row = tuple(float(frame.iloc[index][name]) for name in FEATURE_NAMES)
            if not all(math.isfinite(value) for value in feature_row):
                continue
            entry_open = float(frame.iloc[index + 1]["open"])
            exit_open = float(frame.iloc[index + TARGET_BARS + 1]["open"])
            if entry_open <= 0 or exit_open <= 0:
                raise ValueError("ML target prices must be positive")
            samples.append(
                {
                    "decision_index": index,
                    "label_end_index": index + TARGET_BARS + 1,
                    "symbol": symbol,
                    "features": feature_row,
                    "label": int(exit_open / entry_open - 1.0 > ROUND_TRIP_HURDLE),
                }
            )

    accepted: dict[tuple[int, str], bool] = {}
    predictions: list[dict[str, object]] = []
    sample_lookup = {
        (int(sample["decision_index"]), str(sample["symbol"])): sample
        for sample in samples
    }
    for index in rebalance_indices:
        matured = _matured_samples(samples, index)
        model = _fit_logistic(
            [tuple(sample["features"]) for sample in matured],
            [int(sample["label"]) for sample in matured],
        )
        for symbol in symbols:
            frame = enriched[symbol]
            feature_row = tuple(float(frame.iloc[index][name]) for name in FEATURE_NAMES)
            finite = all(math.isfinite(value) for value in feature_row)
            probability = _predict_probability(model, feature_row) if model is not None and finite else None
            base_selected = bool(int(frame.iloc[index]["desired_long_position"]))
            is_accepted = bool(
                base_selected
                and probability is not None
                and probability >= PROBABILITY_THRESHOLD
            )
            accepted[(index, symbol)] = is_accepted
            realized = sample_lookup.get((index, symbol))
            predictions.append(
                {
                    "event_time": frame.iloc[index]["event_time"],
                    "decision_index": index,
                    "symbol": symbol,
                    "base_selected": base_selected,
                    "train_sample_count": len(matured),
                    "train_positive_count": sum(int(sample["label"]) for sample in matured),
                    "probability": probability,
                    "ml_accepted": is_accepted,
                    "realized_label": (
                        int(realized["label"]) if realized is not None else None
                    ),
                    "label_end_index": (
                        int(realized["label_end_index"]) if realized is not None else None
                    ),
                    **{name: feature_row[position] for position, name in enumerate(FEATURE_NAMES)},
                }
            )

    filtered: dict[str, pd.DataFrame] = {}
    rebalance_set = set(rebalance_indices)
    for symbol in symbols:
        frame = enriched[symbol].copy()
        current_accept = False
        desired: list[int] = []
        probabilities: list[float | None] = []
        current_probability: float | None = None
        prediction_lookup = {
            int(row["decision_index"]): row["probability"]
            for row in predictions
            if row["symbol"] == symbol
        }
        for index, base_value in enumerate(frame["desired_long_position"].astype(int)):
            if index in rebalance_set:
                current_accept = accepted[(index, symbol)]
                current_probability = prediction_lookup[index]
            desired.append(int(base_value == 1 and current_accept))
            probabilities.append(current_probability)
        frame["exp_0042_desired_long_position"] = frame["desired_long_position"].astype(int)
        frame["ml_probability"] = probabilities
        frame["ml_accepted"] = desired
        frame["desired_long_position"] = desired
        filtered[symbol] = frame
    return _apply_last_rank_exit(filtered), pd.DataFrame(predictions)


def _run_arm(
    frames: dict[str, pd.DataFrame], cost_model: CostModel
) -> AllocatedPortfolioResult:
    """EXP-0042の配分境界で指定シグナルを会計する。

    Args:
        frames: 銘柄別シグナル。
        cost_model: 基本またはstress費用モデル。

    Returns:
        配分済みportfolio結果。
    """

    return run_allocated_portfolio(frames, _allocation_config("momentum_top2"), cost_model)


def _decision(
    baseline: dict[str, object], candidate: dict[str, object], stress: dict[str, object]
) -> tuple[str, list[str]]:
    """事前登録したretrospective棄却条件を評価する。

    Args:
        baseline: EXP-0042の2025指標。
        candidate: ML filterの2025基本費用指標。
        stress: ML filterの2025費用2倍指標。

    Returns:
        research statusと棄却理由。
    """

    reasons: list[str] = []
    completed = min(int(candidate["entry_count"]), int(candidate["exit_count"]))
    if completed < 10:
        reasons.append("evaluation_completed_leg_round_trips_below_10")
    if Decimal(str(candidate["net_pnl"])) <= Decimal(str(baseline["net_pnl"])):
        reasons.append("evaluation_net_pnl_not_above_baseline")
    if Decimal(str(candidate["max_drawdown"])) < Decimal(str(baseline["max_drawdown"])):
        reasons.append("evaluation_max_drawdown_worse_than_baseline")
    if Decimal(str(stress["net_pnl"])) <= 0:
        reasons.append("stress_evaluation_net_pnl_nonpositive")
    return ("PASSED_RETROSPECTIVE_VALIDATION" if not reasons else "REJECTED", reasons)


def main() -> None:
    """baselineとML filterを実行して監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/EXP-2026-0052"))
    args = parser.parse_args()
    raw = _load_raw_frames(args.data_dir)
    base = _prepare_momentum_frames(raw, args.data_dir, long_count=2)
    baseline_frames = _apply_last_rank_exit(base)
    filtered_frames, predictions = _prepare_ml_filter(base)
    results = {
        "baseline": _run_arm(baseline_frames, BASE_COST_MODEL),
        "ml_filter": _run_arm(filtered_frames, BASE_COST_MODEL),
        "ml_filter_stress_2x_cost": _run_arm(filtered_frames, STRESS_COST_MODEL),
    }
    summaries = {
        arm: {
            "full": result.metrics,
            "development": _segment_summary(result, EVALUATION_START, RETROSPECTIVE_START),
            "evaluation_2025": _segment_summary(result, RETROSPECTIVE_START, EVALUATION_END),
        }
        for arm, result in results.items()
    }
    decision, reasons = _decision(
        summaries["baseline"]["evaluation_2025"],
        summaries["ml_filter"]["evaluation_2025"],
        summaries["ml_filter_stress_2x_cost"]["evaluation_2025"],
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(args.output_dir / "walk-forward-predictions.csv", index=False)
    for symbol, frame in filtered_frames.items():
        columns = [
            "event_time", "momentum_return", "market_median_momentum",
            "cross_sectional_rank", "realized_volatility_84", "rebalance_signal",
            "exp_0042_desired_long_position", "base_desired_long_position",
            "ml_probability", "ml_accepted",
            "last_rank_exit_trigger", "desired_long_position", "funding_rate",
        ]
        frame[columns].to_csv(args.output_dir / f"{symbol}-ml-signals.csv", index=False)
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(args.output_dir / f"{arm}-events.csv", index=False)
        pd.DataFrame(result.equity_curve).to_csv(args.output_dir / f"{arm}-equity.csv", index=False)
    evaluation_predictions = predictions[
        pd.to_datetime(predictions["event_time"], utc=True) >= RETROSPECTIVE_START
    ]
    selected = evaluation_predictions[evaluation_predictions["base_selected"]]
    scored = evaluation_predictions.dropna(subset=["probability", "realized_label"])
    clipped = scored["probability"].clip(lower=1e-15, upper=1 - 1e-15)
    log_loss = (
        -(
            scored["realized_label"] * clipped.map(math.log)
            + (1 - scored["realized_label"]) * (1 - clipped).map(math.log)
        ).mean()
        if not scored.empty
        else None
    )
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "research_decision": decision,
        "rejection_reasons": reasons,
        "promotion_status": "NOT_ELIGIBLE",
        "parameters": {
            "features": FEATURE_NAMES,
            "target_bars": TARGET_BARS,
            "minimum_train_samples": MIN_TRAIN_SAMPLES,
            "probability_threshold": PROBABILITY_THRESHOLD,
            "training_iterations": TRAINING_ITERATIONS,
            "learning_rate": LEARNING_RATE,
            "l2_penalty": L2_PENALTY,
            "round_trip_hurdle": ROUND_TRIP_HURDLE,
            "variants": 1,
            "random_seed": 0,
        },
        "prediction_diagnostics_2025": {
            "base_candidate_count": int(len(selected)),
            "accepted_count": int(selected["ml_accepted"].sum()),
            "acceptance_rate": (
                str(Decimal(int(selected["ml_accepted"].sum())) / Decimal(len(selected)))
                if len(selected) else None
            ),
            "last_train_sample_count": (
                int(evaluation_predictions.iloc[-1]["train_sample_count"])
                if not evaluation_predictions.empty else 0
            ),
            "realized_positive_rate": (
                str(Decimal(str(scored["realized_label"].mean())))
                if not scored.empty else None
            ),
            "log_loss": str(Decimal(str(log_loss))) if log_loss is not None else None,
        },
        "arms": summaries,
        "limitations": [
            "2025年は既存研究で観測済みのretrospective evaluationであり未観測OOSではない。",
            "固定4銘柄の少数標本であり、他銘柄や他期間への一般化を示さない。",
            "学習targetは固定84本open-to-open returnで、途中退出とFundingを直接表さない。",
            "ZOOMEX公開履歴上の研究であり、実約定の有効性は未検証。",
            "実注文、認証情報、paper、shadow、live設定は変更していない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
