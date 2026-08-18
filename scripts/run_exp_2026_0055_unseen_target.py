#!/usr/bin/env python3
"""EXP-2026-0055の未観測4銘柄を一度だけ評価する。"""

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

from scripts.run_exp_2026_0054_holdout import (  # noqa: E402
    BOOTSTRAP_BLOCK_DAYS,
    BOOTSTRAP_REPETITIONS,
    _circular_block_ci,
    _daily_returns,
)
from scripts.run_exp_2026_0055_source_gate import (  # noqa: E402
    CONTEXT_SYMBOL,
    EVENT_RETURN_THRESHOLD,
    EXPERIMENT_ID,
    FEATURE_NAMES,
    HALF_SPREAD,
    HORIZON,
    INITIAL_EQUITY,
    NOTIONAL,
    PROBABILITY_THRESHOLD,
    SEALED_TARGET_SYMBOLS,
    SLIPPAGE,
    SOURCE_SYMBOLS,
    TAKER_FEE,
    InstrumentRule,
    Sample,
    _base_features,
    _load_symbol,
    _quantity,
    _sha256,
    build_samples,
    calibrated_probabilities,
    fit_platt,
    fit_xgboost,
    load_source_inputs,
)


TARGET_START = pd.Timestamp("2026-01-01T00:00:00Z")
TARGET_END = pd.Timestamp("2026-08-01T00:00:00Z")
RETRAIN_INTERVAL = pd.Timedelta(days=30)
TRAIN_WINDOW = pd.Timedelta(days=730)
FIT_FRACTION = 0.8
FINAL_MARGIN = 0.03
TARGET_METADATA_SHA256 = "99201014870994c824160f6e449e8c61820fa0a6730affefebb946b2c21fd90d"


def authorize_target(registry_path: Path, source_summary_path: Path) -> dict[str, object]:
    """source gate合格とtarget未開封を照合する。

    Args:
        registry_path: EXP-2026-0055台帳。
        source_summary_path: source gate成果物。

    Returns:
        読み込んだ台帳。

    Raises:
        ValueError: model、gate、hash、未開封状態が不一致の場合。
    """

    registry = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    result = registry.get("evaluation", {}).get("source_gate_result", {})
    execution = registry.get("execution_status", {})
    if registry.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("unexpected experiment registry")
    if result.get("selected_model") != "xgboost" or result.get("target_opening_authorized") is not True:
        raise ValueError("source gate did not authorize xgboost")
    if execution.get("sealed_target_opened") is not False:
        raise ValueError("sealed target is already marked opened")
    if _sha256(source_summary_path) != result.get("result_sha256"):
        raise ValueError("source gate result hash mismatch")
    summary = json.loads(source_summary_path.read_text(encoding="utf-8"))
    if summary.get("selected_model") != "xgboost" or summary.get("target_opening_authorized") is not True:
        raise ValueError("source summary does not authorize xgboost")
    return registry


def load_target_inputs(
    data_dir: Path,
    metadata_path: Path,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, InstrumentRule]]:
    """hash検証後に未観測4銘柄を初めて読み込む。

    Args:
        data_dir: DATA-2026-0007保存先。
        metadata_path: DATA-2026-0007 metadata。

    Returns:
        target trade、mark、Funding、数量規則。

    Raises:
        ValueError: metadata hash、snapshot、symbol、artifactが不正な場合。
    """

    if _sha256(metadata_path) != TARGET_METADATA_SHA256:
        raise ValueError("target metadata hash mismatch")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("snapshot_id") != "DATA-2026-0007":
        raise ValueError("unexpected target snapshot")
    if tuple(metadata.get("sealed_target_symbols", ())) != SEALED_TARGET_SYMBOLS:
        raise ValueError("sealed target symbol order changed")
    records = {record["symbol"]: record for record in metadata["symbols"]}
    trade_frames: dict[str, pd.DataFrame] = {}
    mark_frames: dict[str, pd.DataFrame] = {}
    funding_frames: dict[str, pd.DataFrame] = {}
    rules: dict[str, InstrumentRule] = {}
    for symbol in SEALED_TARGET_SYMBOLS:
        trade, mark, funding, rule = _load_symbol(
            data_dir, records[symbol], symbol, value_cutoff=TARGET_END
        )
        trade_frames[symbol] = trade
        mark_frames[symbol] = mark
        funding_frames[symbol] = funding
        rules[symbol] = rule
    return trade_frames, mark_frames, funding_frames, rules


def build_target_samples(
    trade_frames: dict[str, pd.DataFrame],
    btc_trade: pd.DataFrame,
) -> tuple[list[Sample], dict[pd.Timestamp, str]]:
    """未観測4銘柄の固定期間標本を作る。

    Args:
        trade_frames: target trade Kline。
        btc_trade: source snapshot由来のBTC context。

    Returns:
        target標本と判断時刻別除外理由。

    Raises:
        ValueError: target universeまたは特徴量が不正な場合。
    """

    if set(trade_frames) != set(SEALED_TARGET_SYMBOLS):
        raise ValueError("exact sealed target universe is required")
    samples: list[Sample] = []
    exclusions: dict[pd.Timestamp, str] = {}
    decisions = pd.date_range(TARGET_START, TARGET_END, freq="6h", inclusive="left")
    for decision_time in decisions:
        exit_time = decision_time + HORIZON
        if exit_time >= TARGET_END:
            continue
        try:
            bases = {
                symbol: _base_features(trade_frames[symbol], btc_trade, decision_time)
                for symbol in SEALED_TARGET_SYMBOLS
            }
            six_hour = {symbol: values[2] for symbol, values in bases.items()}
            ordered = sorted(SEALED_TARGET_SYMBOLS, key=lambda symbol: (-six_hour[symbol], symbol))
            median = float(np.median(list(six_hour.values())))
            dispersion = float(np.std(list(six_hour.values()), ddof=0))
            if dispersion <= 1e-12:
                raise ValueError("cross-sectional dispersion is too small")
            staged: list[Sample] = []
            for symbol in SEALED_TARGET_SYMBOLS:
                rank = ordered.index(symbol)
                base = bases[symbol]
                features = (
                    *base[:15], 1.0 - rank / 3.0, six_hour[symbol] - median,
                    dispersion, *base[15:]
                )
                entry_open = Decimal(str(trade_frames[symbol].loc[decision_time, "open"]))
                exit_open = Decimal(str(trade_frames[symbol].loc[exit_time, "open"]))
                gross_return = float(exit_open / entry_open - Decimal("1"))
                if len(features) != len(FEATURE_NAMES) or not all(
                    math.isfinite(value) for value in (*features, gross_return)
                ):
                    raise ValueError("invalid target feature or label")
                staged.append(Sample(
                    decision_time, symbol, features,
                    int(gross_return > EVENT_RETURN_THRESHOLD), gross_return,
                    entry_open, exit_open,
                ))
            samples.extend(staged)
        except (KeyError, ValueError) as error:
            exclusions[decision_time] = str(error)
    return samples, exclusions


def adaptive_probabilities(
    source_samples: list[Sample],
    target_samples: list[Sample],
) -> tuple[np.ndarray, np.ndarray, list[dict[str, object]]]:
    """30日ごとにsourceだけで再学習しtarget確率を返す。

    Args:
        source_samples: source9銘柄の時点整合標本。
        target_samples: target4銘柄の評価標本。

    Returns:
        target順の確率、期待gross return、model監査記録。

    Raises:
        ValueError: 期間欠落、class不足、予測未充足の場合。
    """

    probabilities = np.full(len(target_samples), np.nan, dtype=float)
    expected_returns = np.full(len(target_samples), np.nan, dtype=float)
    audit: list[dict[str, object]] = []
    cutoffs: list[pd.Timestamp] = []
    cutoff = TARGET_START
    while cutoff < TARGET_END:
        cutoffs.append(cutoff)
        cutoff += RETRAIN_INTERVAL
    for position, cutoff in enumerate(cutoffs):
        expiration = min(cutoff + RETRAIN_INTERVAL, TARGET_END)
        window_start = cutoff - TRAIN_WINDOW
        split_time = window_start + TRAIN_WINDOW * FIT_FRACTION
        fit = [
            sample for sample in source_samples
            if sample.decision_time >= window_start and sample.decision_time + HORIZON < split_time
        ]
        calibration = [
            sample for sample in source_samples
            if sample.decision_time >= split_time and sample.decision_time + HORIZON < cutoff
        ]
        indices = [
            index for index, sample in enumerate(target_samples)
            if sample.decision_time >= cutoff and sample.decision_time < expiration
        ]
        if not fit or not calibration or not indices:
            raise ValueError(f"empty adaptive partition at {cutoff.isoformat()}")
        model = fit_xgboost(fit)
        platt = fit_platt(model.predict_margin(calibration), calibration)
        period_samples = [target_samples[index] for index in indices]
        period_probabilities = calibrated_probabilities(model.predict_margin(period_samples), platt)
        mean_up = float(np.mean([sample.gross_return for sample in calibration if sample.event == 1]))
        mean_non_up = float(np.mean([sample.gross_return for sample in calibration if sample.event == 0]))
        probabilities[indices] = period_probabilities
        expected_returns[indices] = period_probabilities * mean_up + (1.0 - period_probabilities) * mean_non_up
        audit.append({
            "sequence": position, "cutoff": cutoff.isoformat(), "expiration": expiration.isoformat(),
            "window_start": window_start.isoformat(), "split_time": split_time.isoformat(),
            "fit_rows": len(fit), "calibration_rows": len(calibration),
            "fit_event_rate": float(np.mean([sample.event for sample in fit])),
            "calibration_event_rate": float(np.mean([sample.event for sample in calibration])),
            "platt_intercept": float(platt[0]), "platt_slope": float(platt[1]),
            "calibration_mean_up_gross_return": mean_up,
            "calibration_mean_non_up_gross_return": mean_non_up,
            "target_prediction_rows": len(indices), "source_symbols": SOURCE_SYMBOLS,
        })
    if not np.isfinite(probabilities).all() or not np.isfinite(expected_returns).all():
        raise ValueError("adaptive prediction coverage is incomplete")
    return probabilities, expected_returns, audit


def evaluate_target(
    samples: list[Sample],
    scores: np.ndarray,
    expected_returns: np.ndarray,
    rules: dict[str, InstrumentRule],
    mark_frames: dict[str, pd.DataFrame],
    funding_frames: dict[str, pd.DataFrame],
    *,
    cost_multiplier: Decimal = Decimal("1"),
    momentum: bool = False,
) -> dict[str, object]:
    """4銘柄top1を固定会計で評価する。

    Args:
        samples: target4銘柄の標本。
        scores: 校正確率またはmomentum score。
        expected_returns: MLの期待gross return。momentum時は未使用。
        rules: 数量丸め規則。
        mark_frames: Funding代理mark価格。
        funding_frames: 実績Funding。
        cost_multiplier: fee、spread、slippage倍率。
        momentum: momentum baselineならTrue。

    Returns:
        損益、drawdown、銘柄別寄与、取引監査行。

    Raises:
        ValueError: 入力長、group、資金、会計入力が不正な場合。
    """

    if len(samples) != len(scores) or len(samples) != len(expected_returns):
        raise ValueError("target evaluation lengths differ")
    grouped: dict[pd.Timestamp, list[tuple[Sample, float, float]]] = {}
    for sample, score, expected in zip(samples, scores, expected_returns, strict=True):
        grouped.setdefault(sample.decision_time, []).append((sample, float(score), float(expected)))
    equity = INITIAL_EQUITY
    peak = INITIAL_EQUITY
    maximum_drawdown = Decimal("0")
    trades: list[dict[str, object]] = []
    for decision_time, candidates in sorted(grouped.items()):
        if len(candidates) != len(SEALED_TARGET_SYMBOLS):
            raise ValueError("incomplete target cross-section")
        ordered = sorted(candidates, key=lambda item: (-item[1], item[0].symbol))
        sample, score, expected = ordered[0]
        if momentum:
            if not math.isfinite(score) or score <= 0.0:
                continue
            margin = score - ordered[1][1]
        else:
            margin = score - ordered[1][1]
            if (
                not math.isfinite(score) or not math.isfinite(expected)
                or score < PROBABILITY_THRESHOLD or margin < FINAL_MARGIN
                or expected <= EVENT_RETURN_THRESHOLD
            ):
                continue
        quantity = _quantity(rules[sample.symbol], sample.entry_open)
        if quantity <= 0 or equity - NOTIONAL < Decimal("200"):
            raise ValueError("allocation or reserve-cash rejection")
        adverse = (HALF_SPREAD + SLIPPAGE) * cost_multiplier
        entry_fill = sample.entry_open * (Decimal("1") + adverse)
        exit_fill = sample.exit_open * (Decimal("1") - adverse)
        fees = quantity * (entry_fill + exit_fill) * TAKER_FEE * cost_multiplier
        funding_cash = Decimal("0")
        settlements = funding_frames[sample.symbol].loc[
            (funding_frames[sample.symbol].index > decision_time)
            & (funding_frames[sample.symbol].index < decision_time + HORIZON)
        ]
        for settlement_time, row in settlements.iterrows():
            mark_open = Decimal(str(mark_frames[sample.symbol].loc[settlement_time, "open"]))
            funding_cash -= quantity * mark_open * Decimal(str(row["funding_rate"]))
        gross_price_pnl = quantity * (sample.exit_open - sample.entry_open)
        spread_cost = quantity * (sample.entry_open + sample.exit_open) * HALF_SPREAD * cost_multiplier
        slippage_cost = quantity * (sample.entry_open + sample.exit_open) * SLIPPAGE * cost_multiplier
        pnl = gross_price_pnl + funding_cash - fees - spread_cost - slippage_cost
        equity += pnl
        peak = max(peak, equity)
        maximum_drawdown = min(maximum_drawdown, equity / peak - Decimal("1"))
        trades.append({
            "decision_time": decision_time.isoformat(), "exit_time": (decision_time + HORIZON).isoformat(),
            "symbol": sample.symbol, "score": score, "second_score": ordered[1][1], "margin": margin,
            "expected_gross_return": expected, "quantity": str(quantity),
            "gross_price_pnl": str(gross_price_pnl), "funding_cash_flow": str(funding_cash),
            "fees": str(fees), "spread_cost": str(spread_cost), "slippage_cost": str(slippage_cost),
            "net_pnl": str(pnl), "equity": str(equity),
        })
    symbol_net_pnl = {
        symbol: str(sum((Decimal(trade["net_pnl"]) for trade in trades if trade["symbol"] == symbol), Decimal("0")))
        for symbol in SEALED_TARGET_SYMBOLS
    }
    symbol_trades = {
        symbol: sum(trade["symbol"] == symbol for trade in trades) for symbol in SEALED_TARGET_SYMBOLS
    }
    return {
        "net_pnl": str(equity - INITIAL_EQUITY), "completed_round_trips": len(trades),
        "max_drawdown": str(maximum_drawdown), "symbol_net_pnl": symbol_net_pnl,
        "symbol_completed_round_trips": symbol_trades, "trades": trades,
    }


def _calibration_bins(labels: np.ndarray, probabilities: np.ndarray) -> list[dict[str, object]]:
    """固定10分割の確率校正表を返す。

    Args:
        labels: target event label。
        probabilities: target校正確率。

    Returns:
        非空binの件数、平均確率、event率。
    """

    bins: list[dict[str, object]] = []
    indices = np.minimum((probabilities * 10).astype(int), 9)
    for index in range(10):
        selected = indices == index
        if selected.any():
            bins.append({
                "lower": index / 10, "upper": (index + 1) / 10,
                "count": int(selected.sum()), "mean_probability": float(probabilities[selected].mean()),
                "event_rate": float(labels[selected].mean()),
            })
    return bins


def decide_status(
    candidate: dict[str, object],
    momentum: dict[str, object],
    stress: dict[str, object],
    bootstrap: dict[str, dict[str, float]],
    *,
    excluded_decision_times: int,
) -> tuple[str, list[str]]:
    """事前登録target gateを機械判定する。

    Args:
        candidate: XGBoost基本費用結果。
        momentum: momentum基本費用結果。
        stress: XGBoost費用2倍結果。
        bootstrap: block別95% CI。
        excluded_decision_times: 除外判断時刻数。

    Returns:
        暫定statusと棄却・上限制約理由。
    """

    reasons: list[str] = []
    candidate_pnl = Decimal(candidate["net_pnl"])
    if candidate_pnl <= 0:
        reasons.append("target_net_pnl_nonpositive")
    if candidate_pnl <= Decimal(momentum["net_pnl"]):
        reasons.append("target_did_not_beat_momentum")
    if int(candidate["completed_round_trips"]) < 40:
        reasons.append("target_completed_round_trips_below_40")
    if any(int(count) < 5 for count in candidate["symbol_completed_round_trips"].values()):
        reasons.append("target_symbol_round_trips_below_5")
    positive_symbols = sum(Decimal(value) > 0 for value in candidate["symbol_net_pnl"].values())
    if positive_symbols < 3:
        reasons.append("target_positive_symbols_below_3")
    if Decimal(stress["net_pnl"]) <= 0:
        reasons.append("target_stress_net_pnl_nonpositive")
    if Decimal(candidate["max_drawdown"]) <= Decimal("-0.10"):
        reasons.append("target_max_drawdown_not_better_than_minus_10pct")
    if excluded_decision_times:
        reasons.append("target_decision_times_excluded")
    if reasons:
        return "REJECTED", reasons
    if any(interval["lower"] <= 0 for interval in bootstrap.values()):
        return "INCONCLUSIVE", ["bootstrap_lower_bound_not_positive"]
    return "PASSED_FORWARD_TEST", []


def run_target(
    source_trade: dict[str, pd.DataFrame],
    target_trade: dict[str, pd.DataFrame],
    target_mark: dict[str, pd.DataFrame],
    target_funding: dict[str, pd.DataFrame],
    target_rules: dict[str, InstrumentRule],
) -> dict[str, object]:
    """固定済みadaptive XGBoostを未観測4銘柄で評価する。

    Args:
        source_trade: source9銘柄とBTCのtrade Kline。
        target_trade: target4銘柄のtrade Kline。
        target_mark: target4銘柄のmark Kline。
        target_funding: target4銘柄のFunding。
        target_rules: target4銘柄の数量規則。

    Returns:
        完全なtarget評価成果物。

    Raises:
        ValueError: sourceまたはtarget標本に除外がある場合。
    """

    source_samples, source_exclusions = build_samples(
        source_trade, decision_end=TARGET_END
    )
    target_samples, target_exclusions = build_target_samples(
        target_trade, source_trade[CONTEXT_SYMBOL]
    )
    if source_exclusions or target_exclusions:
        raise ValueError(
            f"decision exclusions are prohibited: source={len(source_exclusions)} target={len(target_exclusions)}"
        )
    probabilities, expected_returns, model_audit = adaptive_probabilities(source_samples, target_samples)
    candidate = evaluate_target(
        target_samples, probabilities, expected_returns, target_rules, target_mark, target_funding
    )
    stress = evaluate_target(
        target_samples, probabilities, expected_returns, target_rules, target_mark, target_funding,
        cost_multiplier=Decimal("2"),
    )
    momentum_scores = np.asarray([sample.features[2] for sample in target_samples], dtype=float)
    momentum = evaluate_target(
        target_samples, momentum_scores, np.zeros(len(target_samples)),
        target_rules, target_mark, target_funding, momentum=True,
    )
    differences = _daily_returns(candidate, TARGET_START, TARGET_END) - _daily_returns(
        momentum, TARGET_START, TARGET_END
    )
    bootstrap = {
        f"{days * 24}h": {
            "lower": interval[0], "upper": interval[1],
            "repetitions": BOOTSTRAP_REPETITIONS, "seed": 0,
        }
        for days in BOOTSTRAP_BLOCK_DAYS
        for interval in [_circular_block_ci(differences, days)]
    }
    status, reasons = decide_status(
        candidate, momentum, stress, bootstrap,
        excluded_decision_times=len(target_exclusions),
    )
    labels = np.asarray([sample.event for sample in target_samples], dtype=float)
    return {
        "experiment_id": EXPERIMENT_ID, "stage": "UNSEEN_TARGET_COMPLETED",
        "sealed_target_opened": True, "selected_model": "xgboost",
        "provisional_research_status": status, "decision_reasons": reasons,
        "promotion_status": "NOT_ELIGIBLE", "independent_validation_status": "PENDING",
        "sample_counts": {
            "source_rows": len(source_samples), "target_rows": len(target_samples),
            "source_excluded_decision_times": len(source_exclusions),
            "target_excluded_decision_times": len(target_exclusions),
        },
        "diagnostics": {
            "brier_score": float(np.mean((probabilities - labels) ** 2)),
            "log_loss": float(-np.mean(labels * np.log(probabilities) + (1.0 - labels) * np.log(1.0 - probabilities))),
            "calibration_bins": _calibration_bins(labels, probabilities),
        },
        "model_audit": model_audit,
        "results": {"xgboost": candidate, "xgboost_stress_2x_cost": stress, "momentum": momentum},
        "bootstrap_ml_minus_momentum": bootstrap,
    }


def main() -> None:
    """開封gateを検査し、target成果物を一度だけ新規作成する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--primary-data-dir", type=Path, default=Path("data/processed/EXP-2026-0054"))
    parser.add_argument("--primary-metadata", type=Path, default=Path("var/exp-2026-0054-data.json"))
    parser.add_argument("--supplement-data-dir", type=Path, default=Path("data/processed/EXP-2026-0055"))
    parser.add_argument("--supplement-metadata", type=Path, default=Path("var/exp-2026-0055-data.json"))
    parser.add_argument("--registry", type=Path, default=Path("experiments/registry/EXP-2026-0055-hypothesis.yaml"))
    parser.add_argument("--source-summary", type=Path, default=Path("artifacts/EXP-2026-0055-source-gate/summary.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/EXP-2026-0055-unseen-target/summary.json"))
    args = parser.parse_args()
    sentinel = args.output.with_suffix(".opened.json")
    if args.output.exists() or sentinel.exists():
        raise ValueError("unseen target output already exists; refusing a second primary run")
    authorize_target(args.registry, args.source_summary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with sentinel.open("x", encoding="utf-8") as handle:
        json.dump({
            "experiment_id": EXPERIMENT_ID, "status": "SEALED_TARGET_OPENING",
            "registry_sha256": _sha256(args.registry),
            "source_summary_sha256": _sha256(args.source_summary),
            "target_metadata_sha256": _sha256(args.supplement_metadata),
            "opened_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        }, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    source_trade, _, _, _ = load_source_inputs(
        args.primary_data_dir, args.primary_metadata,
        args.supplement_data_dir, args.supplement_metadata,
        value_cutoff=TARGET_END,
    )
    target_trade, target_mark, target_funding, target_rules = load_target_inputs(
        args.supplement_data_dir, args.supplement_metadata
    )
    payload = run_target(
        source_trade, target_trade, target_mark, target_funding, target_rules
    )
    payload["opening_sentinel_sha256"] = _sha256(sentinel)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "experiment_id": payload["experiment_id"], "stage": payload["stage"],
        "sealed_target_opened": payload["sealed_target_opened"],
        "selected_model": payload["selected_model"],
        "provisional_research_status": payload["provisional_research_status"],
        "decision_reasons": payload["decision_reasons"],
        "sample_counts": payload["sample_counts"],
        "result_summary": {
            name: {key: value for key, value in result.items() if key != "trades"}
            for name, result in payload["results"].items()
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
