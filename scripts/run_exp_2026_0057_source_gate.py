#!/usr/bin/env python3
"""EXP-2026-0057の残差ranking source gateを実行する。"""

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

from scripts.run_exp_2026_0054_development import InstrumentRule, _sha256  # noqa: E402
from scripts.run_exp_2026_0055_source_gate import load_source_inputs  # noqa: E402
from scripts.run_exp_2026_0056_source_gate import (  # noqa: E402
    ASSET_FOLDS,
    CONTEXT_SYMBOL,
    DATA_START,
    FEATURE_NAMES as BASE_FEATURE_NAMES,
    HORIZON,
    INITIAL_EQUITY,
    NORMALIZED_MARGIN,
    NOTIONAL,
    SOURCE_END,
    SOURCE_SYMBOLS,
    TIME_FOLDS,
    TRAIN_WINDOW,
    Sample,
    build_samples,
    evaluate,
    load_15m_source,
)


EXPERIMENT_ID = "EXP-2026-0057"
CALIBRATION_FRACTION = 0.20
CALIBRATION_ERROR_QUANTILE = 0.30
HUBER_SLOPE = 0.02
BETA_WARMUP_END = DATA_START + pd.Timedelta(days=31, hours=6)
FEATURE_NAMES = (*BASE_FEATURE_NAMES, "beta_30d", "btc_trend_up", "btc_high_volatility")
MARKET_FEATURE_INDICES = (16, 17, 18, 19, 20, 24, 25, 27, 28)


@dataclass(frozen=True)
class ResidualSample:
    """市場共通成分を分離したsource標本。"""

    decision_time: pd.Timestamp
    symbol: str
    features: tuple[float, ...]
    net_return: float
    residual_return: float
    btc_future_return: float
    entry_open: Decimal
    exit_open: Decimal


def _decision_betas(
    trade_frames: dict[str, pd.DataFrame],
    decision_times: list[pd.Timestamp],
) -> tuple[dict[str, pd.Series], pd.Series]:
    """判断時点までの120個6時間returnからbetaを作る。

    Args:
        trade_frames: sourceとBTCの15分trade Kline。
        decision_times: 昇順UTC判断時刻。

    Returns:
        銘柄別beta seriesとBTC 30日15分volatility。

    Raises:
        ValueError: 必要時刻が欠ける場合。
    """

    completed_times = pd.DatetimeIndex(decision_times) - pd.Timedelta(minutes=15)
    btc_close = trade_frames[CONTEXT_SYMBOL]["close"]
    btc_completed = btc_close.reindex(completed_times)
    if btc_completed.isna().any():
        raise ValueError("missing BTC completed close for beta")
    btc_six_hour = np.log(btc_completed.astype(float)).diff()
    btc_variance = btc_six_hour.rolling(120, min_periods=120).var(ddof=0)
    btc_15m_returns = np.log(btc_close.astype(float)).diff()
    btc_30d_vol = btc_15m_returns.rolling(2880, min_periods=2880).std(ddof=0).reindex(completed_times)
    betas: dict[str, pd.Series] = {}
    for symbol in SOURCE_SYMBOLS:
        local_completed = trade_frames[symbol]["close"].reindex(completed_times)
        if local_completed.isna().any():
            raise ValueError(f"missing completed close for beta: {symbol}")
        local_six_hour = np.log(local_completed.astype(float)).diff()
        covariance = local_six_hour.rolling(120, min_periods=120).cov(btc_six_hour, ddof=0)
        beta = covariance / btc_variance
        beta.index = pd.DatetimeIndex(decision_times)
        betas[symbol] = beta
    btc_30d_vol.index = pd.DatetimeIndex(decision_times)
    return betas, btc_30d_vol


def residualize_samples(
    samples: list[Sample],
    trade_frames: dict[str, pd.DataFrame],
) -> tuple[list[ResidualSample], dict[pd.Timestamp, str]]:
    """point-in-time betaとregimeを加え残差labelを作る。

    Args:
        samples: EXP-2026-0056のsource標本。
        trade_frames: sourceとBTCの15分trade Kline。

    Returns:
        29特徴量標本と除外理由。

    Raises:
        ValueError: target混入または元標本groupが不完全な場合。
    """

    if not samples:
        raise ValueError("source samples are empty")
    decision_times = sorted({sample.decision_time for sample in samples})
    betas, btc_30d_vol = _decision_betas(trade_frames, decision_times)
    btc_trade = trade_frames[CONTEXT_SYMBOL]
    residualized: list[ResidualSample] = []
    exclusions: dict[pd.Timestamp, str] = {}
    grouped: dict[pd.Timestamp, list[Sample]] = {}
    for sample in samples:
        grouped.setdefault(sample.decision_time, []).append(sample)
    for decision_time, group in sorted(grouped.items()):
        if decision_time < BETA_WARMUP_END:
            continue
        try:
            if len(group) != len(SOURCE_SYMBOLS):
                raise ValueError("incomplete residual source query")
            btc_entry = float(btc_trade.loc[decision_time, "open"])
            btc_exit = float(btc_trade.loc[decision_time + HORIZON, "open"])
            btc_future = btc_exit / btc_entry - 1.0
            staged: list[ResidualSample] = []
            for sample in group:
                beta = float(betas[sample.symbol].loc[decision_time])
                vol_30d = float(btc_30d_vol.loc[decision_time])
                btc_vol_24h = float(sample.features[20])
                if not all(math.isfinite(value) for value in (beta, vol_30d, btc_future)) or vol_30d <= 1e-12:
                    raise ValueError("invalid beta or BTC regime")
                features = (
                    *sample.features, beta, float(sample.features[18] > 0.0),
                    float(btc_vol_24h > vol_30d),
                )
                residual = sample.net_return - beta * btc_future
                if len(features) != len(FEATURE_NAMES) or not all(
                    math.isfinite(value) for value in (*features, residual)
                ):
                    raise ValueError("invalid residual feature or label")
                staged.append(ResidualSample(
                    decision_time, sample.symbol, features, sample.net_return,
                    residual, btc_future, sample.entry_open, sample.exit_open,
                ))
            residualized.extend(staged)
        except (KeyError, ValueError) as error:
            exclusions[decision_time] = str(error)
    return residualized, exclusions


def _tree_parameters(objective: str, metric: str) -> dict[str, object]:
    """固定XGBoost parameterを返す。

    Args:
        objective: 学習objective。
        metric: 評価metric。

    Returns:
        共通tree設定。
    """

    parameters: dict[str, object] = {
        "objective": objective, "eval_metric": metric, "max_depth": 3, "eta": 0.05,
        "min_child_weight": 20, "subsample": 1.0, "colsample_bytree": 1.0,
        "lambda": 10.0, "alpha": 0.0, "gamma": 0.0, "tree_method": "hist",
        "max_bin": 256, "seed": 0, "nthread": 1, "verbosity": 0,
    }
    if objective == "reg:pseudohubererror":
        parameters["huber_slope"] = HUBER_SLOPE
    return parameters


def _ordered(samples: list[ResidualSample]) -> list[ResidualSample]:
    """標本をquery時刻・symbol順へ並べる。

    Args:
        samples: 任意順標本。

    Returns:
        決定論的順序の標本。
    """

    return sorted(samples, key=lambda sample: (sample.decision_time, sample.symbol))


def fit_ranker(samples: list[ResidualSample]) -> object:
    """残差relevanceで固定LambdaRankを学習する。

    Args:
        samples: fit期間のtrain銘柄標本。

    Returns:
        XGBoost booster。

    Raises:
        ValueError: queryが不完全な場合。
    """

    import xgboost as xgb

    rows = _ordered(samples)
    symbols = {sample.symbol for sample in rows}
    grouped: dict[pd.Timestamp, list[ResidualSample]] = {}
    for sample in rows:
        grouped.setdefault(sample.decision_time, []).append(sample)
    labels: list[float] = []
    groups: list[int] = []
    for group in grouped.values():
        if len(group) != len(symbols):
            raise ValueError("incomplete residual ranking query")
        ranked = sorted(group, key=lambda sample: (sample.residual_return, sample.symbol))
        relevance = {sample.symbol: rank for rank, sample in enumerate(ranked)}
        labels.extend(float(relevance[sample.symbol]) for sample in group)
        groups.append(len(group))
    matrix = xgb.DMatrix(np.asarray([sample.features for sample in rows]), label=np.asarray(labels))
    matrix.set_group(groups)
    return xgb.train(_tree_parameters("rank:pairwise", "ndcg@1"), matrix, num_boost_round=300)


def fit_huber(samples: list[ResidualSample], target: str) -> object:
    """残差またはBTC marketの固定Pseudo-Huber modelを学習する。

    Args:
        samples: fit標本。
        target: `residual`または`market`。

    Returns:
        XGBoost booster。

    Raises:
        ValueError: targetが不正な場合。
    """

    import xgboost as xgb

    rows = _ordered(samples)
    if target == "residual":
        features = np.asarray([sample.features for sample in rows])
        labels = np.asarray([sample.residual_return for sample in rows])
    elif target == "market":
        unique: dict[pd.Timestamp, ResidualSample] = {}
        for sample in rows:
            unique.setdefault(sample.decision_time, sample)
        market_rows = [unique[key] for key in sorted(unique)]
        features = np.asarray([[sample.features[index] for index in MARKET_FEATURE_INDICES] for sample in market_rows])
        labels = np.asarray([sample.btc_future_return for sample in market_rows])
    else:
        raise ValueError("unknown Huber target")
    matrix = xgb.DMatrix(features, label=labels)
    return xgb.train(
        _tree_parameters("reg:pseudohubererror", "mphe"), matrix, num_boost_round=300
    )


def predict(model: object, samples: list[ResidualSample], *, market: bool = False) -> np.ndarray:
    """標本順にmodel予測を返す。

    Args:
        model: XGBoost booster。
        samples: 予測標本。
        market: market特徴量だけを使う場合True。

    Returns:
        sample順予測。
    """

    import xgboost as xgb

    if market:
        values = np.asarray([[sample.features[index] for index in MARKET_FEATURE_INDICES] for sample in samples])
    else:
        values = np.asarray([sample.features for sample in samples])
    return np.asarray(model.predict(xgb.DMatrix(values)), dtype=float)


def _rank_ic(samples: list[ResidualSample], scores: np.ndarray, *, momentum: bool = False) -> float:
    """queryごとの残差Spearman相関平均を返す。

    Args:
        samples: 評価標本。
        scores: model score。
        momentum: momentum scoreと明示する場合True。

    Returns:
        平均rank IC。
    """

    del momentum
    frame = pd.DataFrame({
        "time": [sample.decision_time for sample in samples],
        "actual": [sample.residual_return for sample in samples], "score": scores,
    })
    correlations = [group["actual"].corr(group["score"], method="spearman") for _, group in frame.groupby("time")]
    finite = [float(value) for value in correlations if pd.notna(value)]
    return float(np.mean(finite)) if finite else float("nan")


def run_gate(
    samples: list[ResidualSample],
    rules: dict[str, InstrumentRule],
    mark_frames: dict[str, pd.DataFrame],
    funding_frames: dict[str, pd.DataFrame],
) -> dict[str, object]:
    """固定12-foldの残差pipelineを一度評価する。

    Args:
        samples: 29特徴量source標本。
        rules: 数量規則。
        mark_frames: mark価格。
        funding_frames: Funding。

    Returns:
        fold結果、aggregate、gate判定。
    """

    folds: dict[str, dict[str, object]] = {}
    rank_ics: list[float] = []
    momentum_ics: list[float] = []
    coverage_values: list[bool] = []
    for time_index, (cutoff, end) in enumerate(TIME_FOLDS):
        window_start = cutoff - TRAIN_WINDOW
        split_time = cutoff - TRAIN_WINDOW * CALIBRATION_FRACTION
        for asset_name, held_out in ASSET_FOLDS.items():
            fit = [
                sample for sample in samples if sample.symbol not in held_out
                and sample.decision_time >= window_start
                and sample.decision_time + HORIZON < split_time
            ]
            calibration = [
                sample for sample in samples if sample.symbol not in held_out
                and sample.decision_time >= split_time
                and sample.decision_time + HORIZON < cutoff
            ]
            evaluation = [
                sample for sample in samples if sample.symbol in held_out
                and sample.decision_time >= cutoff
                and sample.decision_time + HORIZON < end
            ]
            ranker = fit_ranker(fit)
            residual_model = fit_huber(fit, "residual")
            market_model = fit_huber(fit, "market")
            calibration_expected = (
                predict(residual_model, calibration)
                + np.asarray([sample.features[26] for sample in calibration])
                * predict(market_model, calibration, market=True)
            )
            calibration_errors = (
                np.asarray([sample.net_return for sample in calibration]) - calibration_expected
            )
            error_q30 = float(np.quantile(calibration_errors, CALIBRATION_ERROR_QUANTILE))
            rank_scores = predict(ranker, evaluation)
            expected = (
                predict(residual_model, evaluation)
                + np.asarray([sample.features[26] for sample in evaluation])
                * predict(market_model, evaluation, market=True)
            )
            lower = expected + error_q30
            base = evaluate(evaluation, rank_scores, lower, rules, mark_frames, funding_frames)
            stress = evaluate(
                evaluation, rank_scores, lower, rules, mark_frames, funding_frames,
                cost_multiplier=Decimal("2"),
            )
            momentum_scores = np.asarray([sample.features[4] for sample in evaluation])
            momentum = evaluate(
                evaluation, momentum_scores, np.zeros(len(evaluation)), rules,
                mark_frames, funding_frames, momentum=True,
            )
            residual_momentum = np.asarray([
                sample.features[4] - sample.features[26] * sample.features[17]
                for sample in evaluation
            ])
            rank_ic = _rank_ic(evaluation, rank_scores)
            momentum_ic = _rank_ic(evaluation, residual_momentum, momentum=True)
            coverage = np.asarray([sample.net_return for sample in evaluation]) >= lower
            coverage_values.extend(coverage.tolist())
            key = f"T{time_index + 1}-{asset_name}"
            folds[key] = {
                "cutoff": cutoff.isoformat(), "end_exclusive": end.isoformat(),
                "held_out_symbols": held_out, "fit_rows": len(fit),
                "calibration_rows": len(calibration), "evaluation_rows": len(evaluation),
                "calibration_error_q30": error_q30, "lower_estimate_coverage": float(np.mean(coverage)),
                "residual_rank_ic": rank_ic, "residual_momentum_rank_ic": momentum_ic,
                "base": base, "stress_2x_cost": stress, "momentum": momentum,
            }
            rank_ics.append(rank_ic)
            momentum_ics.append(momentum_ic)
    aggregate_net = sum((Decimal(fold["base"]["net_pnl"]) for fold in folds.values()), Decimal("0"))
    aggregate_stress = sum((Decimal(fold["stress_2x_cost"]["net_pnl"]) for fold in folds.values()), Decimal("0"))
    aggregate_momentum = sum((Decimal(fold["momentum"]["net_pnl"]) for fold in folds.values()), Decimal("0"))
    trades = sum(int(fold["base"]["completed_round_trips"]) for fold in folds.values())
    positive_folds = sum(Decimal(fold["base"]["net_pnl"]) > 0 for fold in folds.values())
    symbol_pnl = {
        symbol: sum((Decimal(fold["base"]["symbol_net_pnl"].get(symbol, "0")) for fold in folds.values()), Decimal("0"))
        for symbol in SOURCE_SYMBOLS
    }
    positive_symbols = sum(value > 0 for value in symbol_pnl.values())
    positive_profits = [max(value, Decimal("0")) for value in symbol_pnl.values()]
    positive_total = sum(positive_profits, Decimal("0"))
    max_contribution = max(positive_profits, default=Decimal("0")) / positive_total if positive_total > 0 else Decimal("1")
    rank_ic = float(np.mean(rank_ics))
    momentum_ic = float(np.mean(momentum_ics))
    coverage = float(np.mean(coverage_values))
    checks = {
        "minimum_240_round_trips": trades >= 240,
        "minimum_9_positive_folds": positive_folds >= 9,
        "minimum_6_positive_symbols": positive_symbols >= 6,
        "positive_and_beats_momentum": aggregate_net > 0 and aggregate_net > aggregate_momentum,
        "positive_stress_2x_cost": aggregate_stress > 0,
        "residual_rank_ic_positive_and_beats_momentum": rank_ic > 0 and rank_ic > momentum_ic,
        "lower_estimate_coverage_between_60_and_80pct": 0.60 <= coverage <= 0.80,
        "maximum_positive_profit_contribution_35pct": max_contribution <= Decimal("0.35"),
        "integrity_errors_zero": True,
    }
    return {
        "experiment_id": EXPERIMENT_ID, "stage": "SOURCE_GATE_COMPLETED",
        "sealed_target_opened": False, "target_opening_authorized": all(checks.values()),
        "selected_pipeline": "residual_rank_huber_market_q30" if all(checks.values()) else None,
        "parameters": {
            "features": FEATURE_NAMES, "huber_slope": HUBER_SLOPE,
            "calibration_error_quantile": CALIBRATION_ERROR_QUANTILE,
            "normalized_margin": NORMALIZED_MARGIN, "folds": 12, "random_seed": 0,
        },
        "aggregate": {
            "net_pnl": str(aggregate_net), "stress_2x_cost_net_pnl": str(aggregate_stress),
            "momentum_net_pnl": str(aggregate_momentum), "completed_round_trips": trades,
            "positive_folds": positive_folds, "positive_symbols": positive_symbols,
            "symbol_net_pnl": {symbol: str(value) for symbol, value in symbol_pnl.items()},
            "residual_rank_ic": rank_ic, "residual_momentum_rank_ic": momentum_ic,
            "lower_estimate_coverage": coverage,
            "maximum_positive_profit_contribution": str(max_contribution),
        },
        "gate_checks": checks, "folds": folds,
    }


def main() -> None:
    """source標本を残差化し、固定12-fold gateを保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/EXP-2026-0056"))
    parser.add_argument("--metadata", type=Path, default=Path("var/exp-2026-0056-data.json"))
    parser.add_argument("--primary-data-dir", type=Path, default=Path("data/processed/EXP-2026-0054"))
    parser.add_argument("--primary-metadata", type=Path, default=Path("var/exp-2026-0054-data.json"))
    parser.add_argument("--supplement-data-dir", type=Path, default=Path("data/processed/EXP-2026-0055"))
    parser.add_argument("--supplement-metadata", type=Path, default=Path("var/exp-2026-0055-data.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/EXP-2026-0057-source-gate"))
    args = parser.parse_args()
    trade = load_15m_source(args.data_dir, args.metadata)
    _, mark, funding, rules = load_source_inputs(
        args.primary_data_dir, args.primary_metadata, args.supplement_data_dir,
        args.supplement_metadata, value_cutoff=SOURCE_END,
    )
    base_samples, base_exclusions = build_samples(trade, mark, funding, rules)
    samples, residual_exclusions = residualize_samples(base_samples, trade)
    exclusions = {**base_exclusions, **residual_exclusions}
    if exclusions:
        raise ValueError(f"source decision exclusions are prohibited: {len(exclusions)}")
    payload = run_gate(samples, rules, mark, funding)
    payload["sample_count"] = len(samples)
    payload["excluded_decision_times"] = len(exclusions)
    payload["input_metadata_sha256"] = _sha256(args.metadata)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "summary.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "experiment_id": payload["experiment_id"], "stage": payload["stage"],
        "sealed_target_opened": payload["sealed_target_opened"],
        "target_opening_authorized": payload["target_opening_authorized"],
        "selected_pipeline": payload["selected_pipeline"], "sample_count": payload["sample_count"],
        "excluded_decision_times": payload["excluded_decision_times"],
        "aggregate": payload["aggregate"], "gate_checks": payload["gate_checks"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
