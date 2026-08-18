#!/usr/bin/env python3
"""EXP-2026-0058のpremium crowding ML source gateを実行する。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from scripts.run_exp_2026_0054_development import (  # noqa: E402
    InstrumentRule,
    _read_before_cutoff,
    _sha256,
)
from scripts.run_exp_2026_0055_source_gate import load_source_inputs  # noqa: E402
from scripts.run_exp_2026_0056_source_gate import (  # noqa: E402
    ASSET_FOLDS,
    HORIZON,
    NORMALIZED_MARGIN,
    SOURCE_END,
    SOURCE_SYMBOLS,
    TIME_FOLDS,
    TRAIN_WINDOW,
    Sample,
    build_samples,
    evaluate,
    load_15m_source,
)


EXPERIMENT_ID = "EXP-2026-0058"
SNAPSHOT_ID = "DATA-2026-0009"
METADATA_SHA256 = "98202678a17e510834314a9297614b0b4f168b42fd9c8137206e1694eb00af06"
CALIBRATION_FRACTION = 0.20
CALIBRATION_ERROR_QUANTILE = 0.30
HUBER_SLOPE = 0.02
WARMUP_END = pd.Timestamp("2022-02-01T06:00:00Z")
PRICE_CONTROL_INDICES = (4, 6, 8, 9, 14, 16, 17, 18, 20, 21, 22, 23, 24, 25)
PRICE_FEATURE_NAMES = (
    "return_6h", "return_24h", "volatility_6h", "volatility_24h", "volume_z_24h",
    "btc_return_1h", "btc_return_6h", "btc_return_24h", "btc_volatility_24h",
    "cross_sectional_rank", "cross_sectional_median_difference",
    "cross_sectional_dispersion", "utc_hour_sin", "utc_hour_cos",
)
PREMIUM_FEATURE_NAMES = (
    "premium_close", "premium_mean_1h", "premium_mean_6h", "premium_mean_24h",
    "premium_change_1h", "premium_change_6h", "premium_change_24h",
    "premium_std_6h", "premium_std_24h", "premium_z_30d",
    "premium_cross_rank", "premium_cross_median_difference", "premium_cross_dispersion",
)
FUNDING_FEATURE_NAMES = (
    "funding_latest", "funding_mean_3", "funding_mean_9", "funding_latest_change",
)
FEATURE_NAMES = (*PRICE_FEATURE_NAMES, *PREMIUM_FEATURE_NAMES, *FUNDING_FEATURE_NAMES)
FULL_FEATURE_INDICES = tuple(range(len(FEATURE_NAMES)))
ABLATION_FEATURE_INDICES = tuple(range(len(PRICE_FEATURE_NAMES)))


@dataclass(frozen=True)
class CrowdingSample:
    """premium crowding特徴量を持つsource標本。"""

    decision_time: pd.Timestamp
    symbol: str
    features: tuple[float, ...]
    net_return: float
    entry_open: Decimal
    exit_open: Decimal


def load_premium_source(data_dir: Path, metadata_path: Path) -> dict[str, pd.DataFrame]:
    """hash検証後にsource premiumだけを2026年より前まで読む。

    Args:
        data_dir: DATA-2026-0009保存先。
        metadata_path: DATA-2026-0009 metadata。

    Returns:
        source9銘柄のpremium frame。

    Raises:
        ValueError: metadata、封印、hash、系列が不正な場合。
    """

    if _sha256(metadata_path) != METADATA_SHA256:
        raise ValueError("DATA-2026-0009 metadata hash mismatch")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("snapshot_id") != SNAPSHOT_ID:
        raise ValueError("unexpected premium snapshot")
    if metadata.get("sealed_holdout", {}).get("content_opened") is not False:
        raise ValueError("sealed premium target was opened")
    records = {record["symbol"]: record for record in metadata["symbols"]}
    frames: dict[str, pd.DataFrame] = {}
    for symbol in SOURCE_SYMBOLS:
        if records[symbol].get("role") != "source_or_context":
            raise ValueError(f"non-source premium role: {symbol}")
        path = data_dir / symbol / "premium-index-15m.csv"
        if _sha256(path) != records[symbol]["artifact"]["sha256"]:
            raise ValueError(f"premium artifact hash mismatch: {symbol}")
        frame = _read_before_cutoff(path, SOURCE_END)
        required = {"event_time", "open", "high", "low", "close", "is_interpolated"}
        if not required.issubset(frame.columns):
            raise ValueError(f"missing premium columns: {symbol}")
        interpolation = frame["is_interpolated"].astype(str).str.lower()
        if frame["event_time"].duplicated().any() or not interpolation.isin({"false", "0"}).all():
            raise ValueError(f"invalid premium integrity: {symbol}")
        for column in ("open", "high", "low", "close"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        frame = frame.set_index("event_time").sort_index()
        if not frame.index.to_series().diff().dropna().eq(pd.Timedelta(minutes=15)).all():
            raise ValueError(f"premium interval gap: {symbol}")
        if not np.isfinite(frame[["open", "high", "low", "close"]].to_numpy()).all():
            raise ValueError(f"non-finite premium values: {symbol}")
        frames[symbol] = frame
    return frames


def premium_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """各確定足時点のpoint-in-time premium特徴量を作る。

    Args:
        frame: 15分premium frame。

    Returns:
        10個の銘柄内premium特徴量。
    """

    close = frame["close"].astype(float)
    mean_30d = close.rolling(2880, min_periods=2880).mean()
    std_30d = close.rolling(2880, min_periods=2880).std(ddof=0)
    return pd.DataFrame({
        "premium_close": close,
        "premium_mean_1h": close.rolling(4, min_periods=4).mean(),
        "premium_mean_6h": close.rolling(24, min_periods=24).mean(),
        "premium_mean_24h": close.rolling(96, min_periods=96).mean(),
        "premium_change_1h": close.diff(4),
        "premium_change_6h": close.diff(24),
        "premium_change_24h": close.diff(96),
        "premium_std_6h": close.rolling(24, min_periods=24).std(ddof=0),
        "premium_std_24h": close.rolling(96, min_periods=96).std(ddof=0),
        "premium_z_30d": ((close - mean_30d) / std_30d.where(std_30d > 1e-12)).fillna(0.0),
    })


def funding_features(funding: pd.DataFrame, decision_time: pd.Timestamp) -> tuple[float, ...]:
    """判断時刻より前に確定した直近Funding特徴量を返す。

    Args:
        funding: event_time indexのFunding frame。
        decision_time: UTC判断時刻。

    Returns:
        latest、3回平均、9回平均、latest変化。

    Raises:
        ValueError: 直前9回が揃わない場合。
    """

    position = int(funding.index.searchsorted(decision_time, side="left"))
    if position < 9:
        raise ValueError("fewer than 9 prior funding events")
    values = funding["funding_rate"].iloc[position - 9:position].astype(float).to_numpy()
    result = (values[-1], float(np.mean(values[-3:])), float(np.mean(values)), values[-1] - values[-2])
    if not all(math.isfinite(float(value)) for value in result):
        raise ValueError("non-finite funding feature")
    return tuple(float(value) for value in result)


def build_crowding_samples(
    base_samples: list[Sample],
    premium_frames: dict[str, pd.DataFrame],
    funding_frames: dict[str, pd.DataFrame],
) -> tuple[list[CrowdingSample], dict[pd.Timestamp, str]]:
    """base標本へpremium・Funding特徴量をpoint-in-time結合する。

    Args:
        base_samples: 費用込みnet labelを持つsource標本。
        premium_frames: source premium frame。
        funding_frames: source Funding frame。

    Returns:
        31特徴量標本と除外理由。

    Raises:
        ValueError: 元標本が空の場合。
    """

    if not base_samples:
        raise ValueError("base samples are empty")
    calculated = {symbol: premium_feature_frame(premium_frames[symbol]) for symbol in SOURCE_SYMBOLS}
    grouped: dict[pd.Timestamp, list[Sample]] = {}
    for sample in base_samples:
        grouped.setdefault(sample.decision_time, []).append(sample)
    samples: list[CrowdingSample] = []
    exclusions: dict[pd.Timestamp, str] = {}
    for decision_time, group in sorted(grouped.items()):
        if decision_time < WARMUP_END:
            continue
        try:
            if len(group) != len(SOURCE_SYMBOLS):
                raise ValueError("incomplete premium source query")
            completed_time = decision_time - pd.Timedelta(minutes=15)
            current = {
                sample.symbol: float(calculated[sample.symbol].loc[completed_time, "premium_close"])
                for sample in group
            }
            rank_series = pd.Series(current).rank(method="average", ascending=False)
            median = float(np.median(list(current.values())))
            dispersion = float(np.std(list(current.values()), ddof=0))
            cross_ranks = {
                symbol: (
                    0.5 if dispersion <= 1e-12
                    else 1.0 - (float(rank_series.loc[symbol]) - 1.0) / (len(current) - 1)
                )
                for symbol in current
            }
            staged: list[CrowdingSample] = []
            for sample in group:
                premium = calculated[sample.symbol].loc[completed_time]
                local_values = tuple(float(premium[name]) for name in PREMIUM_FEATURE_NAMES[:10])
                premium_cross = (
                    cross_ranks[sample.symbol],
                    current[sample.symbol] - median,
                    dispersion,
                )
                features = (
                    *(float(sample.features[index]) for index in PRICE_CONTROL_INDICES),
                    *local_values,
                    *premium_cross,
                    *funding_features(funding_frames[sample.symbol], decision_time),
                )
                if len(features) != len(FEATURE_NAMES) or not all(
                    math.isfinite(value) for value in features
                ):
                    raise ValueError("invalid premium crowding feature")
                staged.append(CrowdingSample(
                    decision_time, sample.symbol, features, sample.net_return,
                    sample.entry_open, sample.exit_open,
                ))
            samples.extend(staged)
        except (KeyError, ValueError) as error:
            exclusions[decision_time] = str(error)
    return samples, exclusions


def _tree_parameters(objective: str, metric: str) -> dict[str, object]:
    """固定XGBoost parameterを返す。

    Args:
        objective: 学習objective。
        metric: 評価metric。

    Returns:
        共通tree設定。
    """

    parameters: dict[str, object] = {
        "objective": objective,
        "eval_metric": metric,
        "max_depth": 3,
        "eta": 0.05,
        "min_child_weight": 20,
        "subsample": 1.0,
        "colsample_bytree": 1.0,
        "lambda": 10.0,
        "alpha": 0.0,
        "gamma": 0.0,
        "tree_method": "hist",
        "max_bin": 256,
        "seed": 0,
        "nthread": 1,
        "verbosity": 0,
    }
    if objective == "reg:pseudohubererror":
        parameters["huber_slope"] = HUBER_SLOPE
    return parameters


def _ordered(samples: list[CrowdingSample]) -> list[CrowdingSample]:
    """標本をquery時刻・symbol順へ並べる。

    Args:
        samples: 任意順標本。

    Returns:
        決定論的順序の標本。
    """

    return sorted(samples, key=lambda sample: (sample.decision_time, sample.symbol))


def _matrix_values(
    samples: list[CrowdingSample], feature_indices: tuple[int, ...],
) -> np.ndarray:
    """指定特徴量順のmodel matrixを返す。

    Args:
        samples: 標本。
        feature_indices: 使用する特徴量index。

    Returns:
        二次元特徴量配列。
    """

    return np.asarray([
        [sample.features[index] for index in feature_indices] for sample in samples
    ], dtype=float)


def fit_ranker(
    samples: list[CrowdingSample], feature_indices: tuple[int, ...] = FULL_FEATURE_INDICES,
) -> object:
    """費用込みnet return relevanceで固定LambdaRankを学習する。

    Args:
        samples: fit期間のtrain銘柄標本。
        feature_indices: 使用する特徴量index。

    Returns:
        XGBoost booster。

    Raises:
        ValueError: queryが不完全な場合。
    """

    import xgboost as xgb

    rows = _ordered(samples)
    symbols = {sample.symbol for sample in rows}
    grouped: dict[pd.Timestamp, list[CrowdingSample]] = {}
    for sample in rows:
        grouped.setdefault(sample.decision_time, []).append(sample)
    labels: list[float] = []
    groups: list[int] = []
    for group in grouped.values():
        if len(group) != len(symbols):
            raise ValueError("incomplete premium ranking query")
        ranked = sorted(group, key=lambda sample: (sample.net_return, sample.symbol))
        relevance = {sample.symbol: rank for rank, sample in enumerate(ranked)}
        labels.extend(float(relevance[sample.symbol]) for sample in group)
        groups.append(len(group))
    matrix = xgb.DMatrix(_matrix_values(rows, feature_indices), label=np.asarray(labels))
    matrix.set_group(groups)
    return xgb.train(
        _tree_parameters("rank:pairwise", "ndcg@1"), matrix, num_boost_round=300
    )


def fit_huber(
    samples: list[CrowdingSample], feature_indices: tuple[int, ...] = FULL_FEATURE_INDICES,
) -> object:
    """費用込みabsolute net returnの固定Pseudo-Huber modelを学習する。

    Args:
        samples: fit標本。
        feature_indices: 使用する特徴量index。

    Returns:
        XGBoost booster。
    """

    import xgboost as xgb

    rows = _ordered(samples)
    matrix = xgb.DMatrix(
        _matrix_values(rows, feature_indices),
        label=np.asarray([sample.net_return for sample in rows]),
    )
    return xgb.train(
        _tree_parameters("reg:pseudohubererror", "mphe"), matrix, num_boost_round=300
    )


def predict(
    model: object,
    samples: list[CrowdingSample],
    feature_indices: tuple[int, ...] = FULL_FEATURE_INDICES,
) -> np.ndarray:
    """標本順にmodel予測を返す。

    Args:
        model: XGBoost booster。
        samples: 予測標本。
        feature_indices: 使用する特徴量index。

    Returns:
        sample順予測。
    """

    import xgboost as xgb

    return np.asarray(
        model.predict(xgb.DMatrix(_matrix_values(samples, feature_indices))), dtype=float
    )


def _rank_ic(samples: list[CrowdingSample], scores: np.ndarray) -> float:
    """queryごとの費用込みnet return Spearman相関平均を返す。

    Args:
        samples: 評価標本。
        scores: model score。

    Returns:
        平均rank IC。
    """

    frame = pd.DataFrame({
        "time": [sample.decision_time for sample in samples],
        "actual": [sample.net_return for sample in samples],
        "score": scores,
    })
    correlations = [
        group["actual"].corr(group["score"], method="spearman")
        for _, group in frame.groupby("time")
    ]
    finite = [float(value) for value in correlations if pd.notna(value)]
    return float(np.mean(finite)) if finite else float("nan")


def _fit_and_predict_pipeline(
    fit: list[CrowdingSample],
    calibration: list[CrowdingSample],
    evaluation: list[CrowdingSample],
    feature_indices: tuple[int, ...],
) -> tuple[np.ndarray, np.ndarray, float]:
    """固定ranker・Huber・時間calibrationをfitして評価予測を返す。

    Args:
        fit: fit標本。
        calibration: 時間分離calibration標本。
        evaluation: held-out評価標本。
        feature_indices: fullまたはablation特徴量。

    Returns:
        rank score、lower estimate、calibration error q30。
    """

    ranker = fit_ranker(fit, feature_indices)
    huber = fit_huber(fit, feature_indices)
    calibration_expected = predict(huber, calibration, feature_indices)
    errors = np.asarray([sample.net_return for sample in calibration]) - calibration_expected
    error_q30 = float(np.quantile(errors, CALIBRATION_ERROR_QUANTILE))
    return (
        predict(ranker, evaluation, feature_indices),
        predict(huber, evaluation, feature_indices) + error_q30,
        error_q30,
    )


def run_gate(
    samples: list[CrowdingSample],
    rules: dict[str, InstrumentRule],
    mark_frames: dict[str, pd.DataFrame],
    funding_frames: dict[str, pd.DataFrame],
) -> dict[str, object]:
    """固定12-foldのfull pipelineと価格ablationを一度評価する。

    Args:
        samples: 31特徴量source標本。
        rules: 数量規則。
        mark_frames: mark価格。
        funding_frames: Funding。

    Returns:
        fold結果、aggregate、gate判定。
    """

    folds: dict[str, dict[str, object]] = {}
    rank_ics: list[float] = []
    ablation_rank_ics: list[float] = []
    coverage_values: list[bool] = []
    for time_index, (cutoff, end) in enumerate(TIME_FOLDS):
        window_start = cutoff - TRAIN_WINDOW
        split_time = cutoff - TRAIN_WINDOW * CALIBRATION_FRACTION
        for asset_name, held_out in ASSET_FOLDS.items():
            fit = [
                sample for sample in samples
                if sample.symbol not in held_out
                and sample.decision_time >= window_start
                and sample.decision_time + HORIZON < split_time
            ]
            calibration = [
                sample for sample in samples
                if sample.symbol not in held_out
                and sample.decision_time >= split_time
                and sample.decision_time + HORIZON < cutoff
            ]
            evaluation = [
                sample for sample in samples
                if sample.symbol in held_out
                and sample.decision_time >= cutoff
                and sample.decision_time + HORIZON < end
            ]
            scores, lower, error_q30 = _fit_and_predict_pipeline(
                fit, calibration, evaluation, FULL_FEATURE_INDICES
            )
            ablation_scores, ablation_lower, ablation_error_q30 = _fit_and_predict_pipeline(
                fit, calibration, evaluation, ABLATION_FEATURE_INDICES
            )
            base = evaluate(evaluation, scores, lower, rules, mark_frames, funding_frames)
            stress = evaluate(
                evaluation, scores, lower, rules, mark_frames, funding_frames,
                cost_multiplier=Decimal("2"),
            )
            ablation = evaluate(
                evaluation, ablation_scores, ablation_lower, rules, mark_frames, funding_frames
            )
            momentum_scores = np.asarray([sample.features[0] for sample in evaluation])
            momentum = evaluate(
                evaluation, momentum_scores, np.zeros(len(evaluation)), rules,
                mark_frames, funding_frames, momentum=True,
            )
            rank_ic = _rank_ic(evaluation, scores)
            ablation_rank_ic = _rank_ic(evaluation, ablation_scores)
            coverage = np.asarray([sample.net_return for sample in evaluation]) >= lower
            coverage_values.extend(coverage.tolist())
            key = f"T{time_index + 1}-{asset_name}"
            folds[key] = {
                "cutoff": cutoff.isoformat(),
                "end_exclusive": end.isoformat(),
                "held_out_symbols": held_out,
                "fit_rows": len(fit),
                "calibration_rows": len(calibration),
                "evaluation_rows": len(evaluation),
                "calibration_error_q30": error_q30,
                "ablation_calibration_error_q30": ablation_error_q30,
                "lower_estimate_coverage": float(np.mean(coverage)),
                "rank_ic": rank_ic,
                "ablation_rank_ic": ablation_rank_ic,
                "base": base,
                "stress_2x_cost": stress,
                "price_control_ablation": ablation,
                "momentum": momentum,
            }
            rank_ics.append(rank_ic)
            ablation_rank_ics.append(ablation_rank_ic)
    aggregate_net = sum(
        (Decimal(fold["base"]["net_pnl"]) for fold in folds.values()), Decimal("0")
    )
    aggregate_stress = sum(
        (Decimal(fold["stress_2x_cost"]["net_pnl"]) for fold in folds.values()), Decimal("0")
    )
    aggregate_ablation = sum(
        (Decimal(fold["price_control_ablation"]["net_pnl"]) for fold in folds.values()), Decimal("0")
    )
    aggregate_momentum = sum(
        (Decimal(fold["momentum"]["net_pnl"]) for fold in folds.values()), Decimal("0")
    )
    trades = sum(int(fold["base"]["completed_round_trips"]) for fold in folds.values())
    positive_folds = sum(Decimal(fold["base"]["net_pnl"]) > 0 for fold in folds.values())
    symbol_pnl = {
        symbol: sum((
            Decimal(fold["base"]["symbol_net_pnl"].get(symbol, "0"))
            for fold in folds.values()
        ), Decimal("0"))
        for symbol in SOURCE_SYMBOLS
    }
    positive_symbols = sum(value > 0 for value in symbol_pnl.values())
    positive_profits = [max(value, Decimal("0")) for value in symbol_pnl.values()]
    positive_total = sum(positive_profits, Decimal("0"))
    max_contribution = (
        max(positive_profits, default=Decimal("0")) / positive_total
        if positive_total > 0 else Decimal("1")
    )
    rank_ic = float(np.mean(rank_ics))
    ablation_rank_ic = float(np.mean(ablation_rank_ics))
    coverage = float(np.mean(coverage_values))
    checks = {
        "minimum_240_round_trips": trades >= 240,
        "minimum_9_positive_folds": positive_folds >= 9,
        "minimum_6_positive_symbols": positive_symbols >= 6,
        "positive_and_beats_ablation_and_momentum": (
            aggregate_net > 0
            and aggregate_net > aggregate_ablation
            and aggregate_net > aggregate_momentum
        ),
        "positive_stress_2x_cost": aggregate_stress > 0,
        "rank_ic_positive_and_beats_ablation": rank_ic > 0 and rank_ic > ablation_rank_ic,
        "lower_estimate_coverage_between_60_and_80pct": 0.60 <= coverage <= 0.80,
        "maximum_positive_profit_contribution_35pct": max_contribution <= Decimal("0.35"),
        "integrity_errors_zero": True,
    }
    return {
        "experiment_id": EXPERIMENT_ID,
        "stage": "SOURCE_GATE_COMPLETED",
        "sealed_target_opened": False,
        "target_opening_authorized": all(checks.values()),
        "selected_pipeline": "premium_crowding_rank_huber_q30" if all(checks.values()) else None,
        "parameters": {
            "features": FEATURE_NAMES,
            "price_control_features": PRICE_FEATURE_NAMES,
            "huber_slope": HUBER_SLOPE,
            "calibration_error_quantile": CALIBRATION_ERROR_QUANTILE,
            "normalized_margin": NORMALIZED_MARGIN,
            "folds": 12,
            "random_seed": 0,
        },
        "aggregate": {
            "net_pnl": str(aggregate_net),
            "stress_2x_cost_net_pnl": str(aggregate_stress),
            "price_control_ablation_net_pnl": str(aggregate_ablation),
            "momentum_net_pnl": str(aggregate_momentum),
            "completed_round_trips": trades,
            "positive_folds": positive_folds,
            "positive_symbols": positive_symbols,
            "symbol_net_pnl": {symbol: str(value) for symbol, value in symbol_pnl.items()},
            "rank_ic": rank_ic,
            "price_control_ablation_rank_ic": ablation_rank_ic,
            "lower_estimate_coverage": coverage,
            "maximum_positive_profit_contribution": str(max_contribution),
        },
        "gate_checks": checks,
        "folds": folds,
    }


def main() -> None:
    """source標本へpremiumを結合し、固定12-fold gateを保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--trade-data-dir", type=Path, default=Path("data/processed/EXP-2026-0056"))
    parser.add_argument("--trade-metadata", type=Path, default=Path("var/exp-2026-0056-data.json"))
    parser.add_argument(
        "--premium-data-dir", type=Path, default=Path("data/processed/EXP-2026-0058-premium")
    )
    parser.add_argument(
        "--premium-metadata", type=Path, default=Path("var/exp-2026-0058-premium-data.json")
    )
    parser.add_argument("--primary-data-dir", type=Path, default=Path("data/processed/EXP-2026-0054"))
    parser.add_argument("--primary-metadata", type=Path, default=Path("var/exp-2026-0054-data.json"))
    parser.add_argument("--supplement-data-dir", type=Path, default=Path("data/processed/EXP-2026-0055"))
    parser.add_argument("--supplement-metadata", type=Path, default=Path("var/exp-2026-0055-data.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/EXP-2026-0058-source-gate"))
    args = parser.parse_args()
    trade = load_15m_source(args.trade_data_dir, args.trade_metadata)
    premium = load_premium_source(args.premium_data_dir, args.premium_metadata)
    _, mark, funding, rules = load_source_inputs(
        args.primary_data_dir, args.primary_metadata, args.supplement_data_dir,
        args.supplement_metadata, value_cutoff=SOURCE_END,
    )
    base_samples, base_exclusions = build_samples(trade, mark, funding, rules)
    samples, feature_exclusions = build_crowding_samples(base_samples, premium, funding)
    exclusions = {**base_exclusions, **feature_exclusions}
    if exclusions:
        raise ValueError(f"source decision exclusions are prohibited: {len(exclusions)}")
    payload = run_gate(samples, rules, mark, funding)
    payload["sample_count"] = len(samples)
    payload["excluded_decision_times"] = len(exclusions)
    payload["trade_metadata_sha256"] = _sha256(args.trade_metadata)
    payload["premium_metadata_sha256"] = _sha256(args.premium_metadata)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "summary.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "experiment_id": payload["experiment_id"],
        "stage": payload["stage"],
        "sealed_target_opened": payload["sealed_target_opened"],
        "target_opening_authorized": payload["target_opening_authorized"],
        "selected_pipeline": payload["selected_pipeline"],
        "sample_count": payload["sample_count"],
        "excluded_decision_times": payload["excluded_decision_times"],
        "aggregate": payload["aggregate"],
        "gate_checks": payload["gate_checks"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
