#!/usr/bin/env python3
"""EXP-2026-0053のwalk-forward volatility予測サイズ調整を実行する。"""

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

from crypt_ai.allocation import AllocationConfig  # noqa: E402
from crypt_ai.portfolio import AllocatedPortfolioResult, run_allocated_portfolio  # noqa: E402
from crypt_ai.research import CostModel  # noqa: E402
from scripts.run_exp_2026_0035 import (  # noqa: E402
    EVALUATION_END,
    EVALUATION_START,
    INITIAL_EQUITY,
    RESERVE_CASH,
    SYMBOLS,
    _load_raw_frames,
    _prepare_momentum_frames,
)
from scripts.run_exp_2026_0042 import _apply_last_rank_exit  # noqa: E402
from scripts.run_exp_2026_0048 import _segment_summary  # noqa: E402
from scripts.run_exp_2026_0052 import (  # noqa: E402
    BASE_COST_MODEL,
    FEATURE_NAMES,
    L2_PENALTY,
    LEARNING_RATE,
    MIN_TRAIN_SAMPLES,
    RETROSPECTIVE_START,
    STRESS_COST_MODEL,
    TARGET_BARS,
    TRAINING_ITERATIONS,
    _matured_samples,
)


EXPERIMENT_ID = "EXP-2026-0053"
LOW_RISK_LOTS = 2
HIGH_RISK_LOTS = 1
RISK_PERCENTILE = 0.75
LOT_NOTIONAL = Decimal("100")
PER_SYMBOL_CAP = Decimal("200")
LONG_CAP = Decimal("400")
TOTAL_CAP = Decimal("800")


def _fit_ridge(
    rows: list[tuple[float, ...]], targets: list[float]
) -> dict[str, object] | None:
    """固定設定の標準化ridge回帰をbatch gradient descentで学習する。

    Args:
        rows: 特徴量行。
        targets: 正の将来実現volatility。

    Returns:
        標準化量と係数。標本不足またはtarget分散ゼロならNone。

    Raises:
        ValueError: 行数、特徴量数、または数値が不正な場合。
    """

    if len(rows) != len(targets):
        raise ValueError("feature and target lengths differ")
    if rows and any(len(row) != len(FEATURE_NAMES) for row in rows):
        raise ValueError("unexpected feature count")
    if any(not math.isfinite(value) for row in rows for value in row):
        raise ValueError("features must be finite")
    if any(not math.isfinite(value) or value < 0 for value in targets):
        raise ValueError("volatility targets must be finite and non-negative")
    if len(rows) < MIN_TRAIN_SAMPLES:
        return None

    count = float(len(rows))
    means = [sum(row[index] for row in rows) / count for index in range(len(FEATURE_NAMES))]
    scales: list[float] = []
    for index, mean in enumerate(means):
        variance = sum((row[index] - mean) ** 2 for row in rows) / count
        scales.append(math.sqrt(variance) if variance > 0 else 1.0)
    target_mean = sum(targets) / count
    target_variance = sum((target - target_mean) ** 2 for target in targets) / count
    if target_variance <= 0:
        return None
    target_scale = math.sqrt(target_variance)
    standardized_rows = [
        tuple((row[index] - means[index]) / scales[index] for index in range(len(FEATURE_NAMES)))
        for row in rows
    ]
    standardized_targets = [(target - target_mean) / target_scale for target in targets]
    weights = [0.0] * len(FEATURE_NAMES)
    intercept = 0.0
    for _ in range(TRAINING_ITERATIONS):
        weight_gradients = [0.0] * len(FEATURE_NAMES)
        intercept_gradient = 0.0
        for row, target in zip(standardized_rows, standardized_targets, strict=True):
            error = intercept + sum(
                weight * value
                for weight, value in zip(weights, row, strict=True)
            ) - target
            intercept_gradient += error
            for index, value in enumerate(row):
                weight_gradients[index] += error * value
        intercept -= LEARNING_RATE * intercept_gradient / count
        for index in range(len(weights)):
            gradient = weight_gradients[index] / count + L2_PENALTY * weights[index]
            weights[index] -= LEARNING_RATE * gradient
    return {
        "means": means,
        "scales": scales,
        "target_mean": target_mean,
        "target_scale": target_scale,
        "weights": weights,
        "intercept": intercept,
    }


def _predict_volatility(model: dict[str, object], row: tuple[float, ...]) -> float:
    """ridgeモデルから非負の将来volatility予測を返す。

    Args:
        model: `_fit_ridge`が返したモデル。
        row: 未標準化の特徴量。

    Returns:
        0以上の予測実現volatility。

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
    standardized_prediction = float(model["intercept"]) + sum(
        float(weight) * value
        for weight, value in zip(weights, standardized, strict=True)
    )
    prediction = float(model["target_mean"]) + standardized_prediction * float(
        model["target_scale"]
    )
    return max(0.0, prediction)


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    """結果に依存しないnearest-rank percentileを返す。

    Args:
        values: 非空の有限数値。
        percentile: 0より大きく1以下のpercentile。

    Returns:
        昇順のceil(p*n)番目の値。

    Raises:
        ValueError: 入力またはpercentileが不正な場合。
    """

    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError("percentile values must be non-empty and finite")
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be in (0, 1]")
    ordered = sorted(values)
    return ordered[math.ceil(percentile * len(ordered)) - 1]


def _prepare_volatility_sizing(
    base_frames: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], pd.DataFrame]:
    """逐次volatility予測からbaselineとrisk-sized frameを作る。

    Args:
        base_frames: EXP-0042の最下位退出適用前top2シグナル。

    Returns:
        2 lots baseline、予測サイズ候補、週次予測監査表。

    Raises:
        ValueError: 銘柄間時刻、必須値、または価格が不正な場合。
    """

    symbols = tuple(sorted(base_frames))
    if not symbols:
        raise ValueError("base frames are empty")
    times = [tuple(pd.to_datetime(base_frames[symbol]["event_time"], utc=True)) for symbol in symbols]
    if any(times[0] != value for value in times[1:]):
        raise ValueError("volatility sizing timestamps differ")
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
            if index + TARGET_BARS >= len(frame):
                continue
            feature_row = tuple(float(frame.iloc[index][name]) for name in FEATURE_NAMES)
            future_close = pd.to_numeric(
                frame.iloc[index : index + TARGET_BARS + 1]["close"], errors="coerce"
            )
            future_returns = future_close.pct_change(fill_method=None).dropna()
            target = float(future_returns.std(ddof=0))
            if not all(math.isfinite(value) for value in feature_row) or not math.isfinite(target):
                continue
            samples.append(
                {
                    "decision_index": index,
                    "label_end_index": index + TARGET_BARS,
                    "symbol": symbol,
                    "features": feature_row,
                    "target": target,
                }
            )

    lot_counts: dict[tuple[int, str], int] = {}
    predictions: list[dict[str, object]] = []
    sample_lookup = {
        (int(sample["decision_index"]), str(sample["symbol"])): sample
        for sample in samples
    }
    for index in rebalance_indices:
        matured = _matured_samples(samples, index)
        targets = [float(sample["target"]) for sample in matured]
        model = _fit_ridge(
            [tuple(sample["features"]) for sample in matured], targets
        )
        threshold = (
            _nearest_rank_percentile(targets, RISK_PERCENTILE)
            if model is not None
            else None
        )
        for symbol in symbols:
            frame = enriched[symbol]
            feature_row = tuple(float(frame.iloc[index][name]) for name in FEATURE_NAMES)
            finite = all(math.isfinite(value) for value in feature_row)
            predicted = _predict_volatility(model, feature_row) if model is not None and finite else None
            high_risk = predicted is None or threshold is None or predicted >= threshold
            lot_count = HIGH_RISK_LOTS if high_risk else LOW_RISK_LOTS
            lot_counts[(index, symbol)] = lot_count
            realized = sample_lookup.get((index, symbol))
            predictions.append(
                {
                    "event_time": frame.iloc[index]["event_time"],
                    "decision_index": index,
                    "symbol": symbol,
                    "base_selected": bool(
                        index + 1 < len(frame)
                        and int(frame.iloc[index + 1]["desired_long_position"])
                    ),
                    "train_sample_count": len(matured),
                    "predicted_volatility": predicted,
                    "risk_threshold": threshold,
                    "high_risk": high_risk,
                    "desired_long_lot_count": lot_count,
                    "realized_target": (
                        float(realized["target"]) if realized is not None else None
                    ),
                    "label_end_index": (
                        int(realized["label_end_index"]) if realized is not None else None
                    ),
                    **{name: feature_row[position] for position, name in enumerate(FEATURE_NAMES)},
                }
            )

    baseline: dict[str, pd.DataFrame] = {}
    candidate: dict[str, pd.DataFrame] = {}
    rebalance_set = set(rebalance_indices)
    for symbol in symbols:
        source = enriched[symbol]
        baseline_frame = source.copy()
        baseline_frame["desired_long_lot_count"] = LOW_RISK_LOTS
        baseline[symbol] = baseline_frame

        frame = source.copy()
        current_lot_count = HIGH_RISK_LOTS
        counts: list[int] = []
        predicted_values: list[float | None] = []
        high_risk_values: list[bool] = []
        prediction_lookup = {
            int(row["decision_index"]): row
            for row in predictions
            if row["symbol"] == symbol
        }
        for index in range(len(frame)):
            if index in rebalance_set:
                current_lot_count = lot_counts[(index, symbol)]
                current_prediction = prediction_lookup[index]["predicted_volatility"]
                current_high_risk = bool(prediction_lookup[index]["high_risk"])
            elif index == 0:
                current_prediction = None
                current_high_risk = True
            counts.append(current_lot_count)
            predicted_values.append(current_prediction)
            high_risk_values.append(current_high_risk)
        frame["desired_long_lot_count"] = counts
        frame["predicted_volatility"] = predicted_values
        frame["predicted_high_risk"] = high_risk_values
        candidate[symbol] = frame
    return _apply_last_rank_exit(baseline), _apply_last_rank_exit(candidate), pd.DataFrame(predictions)


def _allocation_config() -> AllocationConfig:
    """100 USDT lotとEXP-0042相当上限の配分設定を返す。

    Returns:
        1銘柄最大2 lotsの研究配分設定。
    """

    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=RESERVE_CASH,
        max_long_gross_notional=LONG_CAP,
        max_short_gross_notional=Decimal("0"),
        max_total_gross_notional=TOTAL_CAP,
        per_symbol_max_notional=PER_SYMBOL_CAP,
        lot_notional=LOT_NOTIONAL,
        max_concurrent_long_positions=2,
        max_concurrent_short_positions=len(SYMBOLS),
    )


def _run_arm(
    frames: dict[str, pd.DataFrame], cost_model: CostModel
) -> AllocatedPortfolioResult:
    """指定lot数シグナルを共通配分・会計で実行する。

    Args:
        frames: 銘柄別のlot数付きシグナル。
        cost_model: 基本またはstress費用モデル。

    Returns:
        配分済みportfolio結果。
    """

    return run_allocated_portfolio(frames, _allocation_config(), cost_model)


def _decision(
    baseline: dict[str, object], candidate: dict[str, object], stress: dict[str, object]
) -> tuple[str, list[str]]:
    """事前登録したrisk-sizing棄却条件を評価する。

    Args:
        baseline: EXP-0042相当の2025指標。
        candidate: risk sizingの2025基本費用指標。
        stress: risk sizingの2025費用2倍指標。

    Returns:
        research statusと棄却理由。
    """

    reasons: list[str] = []
    baseline_completed = min(int(baseline["entry_count"]), int(baseline["exit_count"]))
    candidate_completed = min(int(candidate["entry_count"]), int(candidate["exit_count"]))
    if candidate_completed < baseline_completed:
        reasons.append("evaluation_completed_round_trips_below_baseline")
    baseline_pnl = Decimal(str(baseline["net_pnl"]))
    candidate_pnl = Decimal(str(candidate["net_pnl"]))
    if candidate_pnl < baseline_pnl * Decimal("0.90"):
        reasons.append("evaluation_net_pnl_retention_below_90pct")
    drawdown_improvement = Decimal(str(candidate["max_drawdown"])) - Decimal(
        str(baseline["max_drawdown"])
    )
    if drawdown_improvement < Decimal("0.03"):
        reasons.append("evaluation_drawdown_improvement_below_3_points")
    if Decimal(str(stress["net_pnl"])) <= 0:
        reasons.append("stress_evaluation_net_pnl_nonpositive")
    return ("PASSED_RETROSPECTIVE_VALIDATION" if not reasons else "REJECTED", reasons)


def main() -> None:
    """baselineとvolatility risk sizingを実行して成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/EXP-2026-0053"))
    args = parser.parse_args()
    raw = _load_raw_frames(args.data_dir)
    base = _prepare_momentum_frames(raw, args.data_dir, long_count=2)
    baseline_frames, candidate_frames, predictions = _prepare_volatility_sizing(base)
    results = {
        "baseline": _run_arm(baseline_frames, BASE_COST_MODEL),
        "volatility_sizing": _run_arm(candidate_frames, BASE_COST_MODEL),
        "volatility_sizing_stress_2x_cost": _run_arm(candidate_frames, STRESS_COST_MODEL),
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
        summaries["volatility_sizing"]["evaluation_2025"],
        summaries["volatility_sizing_stress_2x_cost"]["evaluation_2025"],
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(args.output_dir / "walk-forward-volatility-predictions.csv", index=False)
    for symbol, frame in candidate_frames.items():
        columns = [
            "event_time", "momentum_return", "market_median_momentum",
            "cross_sectional_rank", "realized_volatility_84", "rebalance_signal",
            "predicted_volatility", "predicted_high_risk", "desired_long_lot_count",
            "base_desired_long_position", "last_rank_exit_trigger",
            "desired_long_position", "funding_rate",
        ]
        frame[columns].to_csv(args.output_dir / f"{symbol}-sizing-signals.csv", index=False)
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(args.output_dir / f"{arm}-events.csv", index=False)
        pd.DataFrame(result.equity_curve).to_csv(args.output_dir / f"{arm}-equity.csv", index=False)

    evaluation = predictions[
        pd.to_datetime(predictions["event_time"], utc=True) >= RETROSPECTIVE_START
    ].dropna(subset=["predicted_volatility", "realized_target"])
    selected = evaluation[evaluation["base_selected"]]
    mae = (
        (evaluation["predicted_volatility"] - evaluation["realized_target"]).abs().mean()
        if not evaluation.empty else None
    )
    high_risk_selected = int(selected["high_risk"].sum()) if not selected.empty else 0
    baseline_eval = summaries["baseline"]["evaluation_2025"]
    candidate_eval = summaries["volatility_sizing"]["evaluation_2025"]
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
            "risk_percentile": RISK_PERCENTILE,
            "normal_lots": LOW_RISK_LOTS,
            "high_risk_lots": HIGH_RISK_LOTS,
            "lot_notional": str(LOT_NOTIONAL),
            "training_iterations": TRAINING_ITERATIONS,
            "learning_rate": LEARNING_RATE,
            "l2_penalty": L2_PENALTY,
            "variants": 1,
            "random_seed": 0,
        },
        "prediction_diagnostics_2025": {
            "base_candidate_count": int(len(selected)),
            "high_risk_candidate_count": high_risk_selected,
            "high_risk_candidate_rate": (
                str(Decimal(high_risk_selected) / Decimal(len(selected)))
                if len(selected) else None
            ),
            "volatility_mae": str(Decimal(str(mae))) if mae is not None else None,
            "last_train_sample_count": (
                int(evaluation.iloc[-1]["train_sample_count"]) if not evaluation.empty else 0
            ),
        },
        "comparison_2025": {
            "net_pnl_retention": str(
                Decimal(str(candidate_eval["net_pnl"]))
                / Decimal(str(baseline_eval["net_pnl"]))
            ),
            "max_drawdown_improvement": str(
                Decimal(str(candidate_eval["max_drawdown"]))
                - Decimal(str(baseline_eval["max_drawdown"]))
            ),
        },
        "arms": summaries,
        "limitations": [
            "2025年は既存研究で観測済みのretrospective evaluationであり未観測OOSではない。",
            "固定4銘柄の少数標本であり、他銘柄や他期間への一般化を示さない。",
            "予測targetは価格volatilityで、Funding、途中退出、約定品質を直接表さない。",
            "保有中のlot数は変更せず、新規entry時の予測だけを使う。",
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
