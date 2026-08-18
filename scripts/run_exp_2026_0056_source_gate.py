#!/usr/bin/env python3
"""EXP-2026-0056のranking＋quantile source gateを実行する。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
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
    _numeric_price_frame,
    _read_before_cutoff,
    _sha256,
    _zscore_last,
)
from scripts.run_exp_2026_0055_source_gate import load_source_inputs  # noqa: E402


EXPERIMENT_ID = "EXP-2026-0056"
SNAPSHOT_ID = "DATA-2026-0008"
METADATA_SHA256 = "82a3c6439120277d016975f4bc6b9ab10f226d8dd376e1caf3a02c0aa816eaaa"
SOURCE_SYMBOLS = (
    "LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT",
    "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "NEARUSDT",
)
CONTEXT_SYMBOL = "BTCUSDT"
SEALED_TARGET_SYMBOLS = ("ETCUSDT", "FILUSDT", "TRXUSDT", "XLMUSDT")
ASSET_FOLDS = {
    "A": ("AAVEUSDT", "ETHUSDT", "SOLUSDT"),
    "B": ("ADAUSDT", "LINKUSDT", "UNIUSDT"),
    "C": ("AVAXUSDT", "NEARUSDT", "XRPUSDT"),
}
TIME_FOLDS = (
    (pd.Timestamp("2024-01-01T00:00:00Z"), pd.Timestamp("2024-07-01T00:00:00Z")),
    (pd.Timestamp("2024-07-01T00:00:00Z"), pd.Timestamp("2025-01-01T00:00:00Z")),
    (pd.Timestamp("2025-01-01T00:00:00Z"), pd.Timestamp("2025-07-01T00:00:00Z")),
    (pd.Timestamp("2025-07-01T00:00:00Z"), pd.Timestamp("2026-01-01T00:00:00Z")),
)
DATA_START = pd.Timestamp("2022-01-01T00:00:00Z")
SOURCE_END = pd.Timestamp("2026-01-01T00:00:00Z")
HORIZON = pd.Timedelta(hours=6)
TRAIN_WINDOW = pd.Timedelta(days=730)
QUANTILE_ALPHA = 0.40
NORMALIZED_MARGIN = 0.25
FEATURE_NAMES = (
    "return_15m", "return_30m", "return_1h", "return_3h", "return_6h", "return_12h", "return_24h",
    "volatility_1h", "volatility_6h", "volatility_24h",
    "downside_semivolatility_6h", "downside_semivolatility_24h",
    "body_15m", "range_15m", "volume_z_24h", "turnover_z_24h",
    "btc_return_1h", "btc_return_6h", "btc_return_24h",
    "btc_volatility_6h", "btc_volatility_24h",
    "cross_sectional_rank", "cross_sectional_median_difference", "cross_sectional_dispersion",
    "utc_hour_sin", "utc_hour_cos",
)
NOTIONAL = Decimal("100")
INITIAL_EQUITY = Decimal("1000")
TAKER_FEE = Decimal("0.0006")
HALF_SPREAD = Decimal("0.0005")
SLIPPAGE = Decimal("0.0005")


@dataclass(frozen=True)
class Sample:
    """銘柄・判断時刻ごとの特徴量と正確な会計label。"""

    decision_time: pd.Timestamp
    symbol: str
    features: tuple[float, ...]
    net_return: float
    entry_open: Decimal
    exit_open: Decimal


def _quantity(rule: InstrumentRule, entry_open: Decimal) -> Decimal:
    """100 USDTをquantity stepへ切り下げる。

    Args:
        rule: 数量規則。
        entry_open: raw entry open。

    Returns:
        条件を満たすquantity、または0。
    """

    units = (NOTIONAL / entry_open / rule.quantity_step).to_integral_value(rounding=ROUND_DOWN)
    quantity = units * rule.quantity_step
    if quantity < rule.minimum_quantity or quantity * entry_open < rule.minimum_notional:
        return Decimal("0")
    return quantity


def _log_return(close: pd.Series, intervals: int) -> float:
    """末尾closeの指定15分区間log returnを返す。

    Args:
        close: 連続close。
        intervals: 15分区間数。

    Returns:
        log return。
    """

    return math.log(float(close.iloc[-1]) / float(close.iloc[-intervals - 1]))


def _base_features(
    trade: pd.DataFrame,
    btc_trade: pd.DataFrame,
    decision_time: pd.Timestamp,
) -> tuple[float, ...]:
    """cross-section以外の23特徴量を確定15分足から作る。

    Args:
        trade: 当該銘柄15分trade Kline。
        btc_trade: BTC 15分trade Kline。
        decision_time: UTC判断境界。

    Returns:
        local、BTC、時刻特徴量。

    Raises:
        ValueError: 97観測windowが連続していない場合。
    """

    expected = pd.date_range(
        decision_time - pd.Timedelta(hours=24, minutes=15), periods=97, freq="15min"
    )
    local = trade.reindex(expected)
    btc = btc_trade.reindex(expected)
    if local.isna().any().any() or btc.isna().any().any():
        raise ValueError("missing 15m feature-window observation")
    close = local["close"]
    returns = np.diff(np.log(close.to_numpy(dtype=float)))
    btc_returns = np.diff(np.log(btc["close"].to_numpy(dtype=float)))
    last = local.iloc[-1]
    return (
        *(_log_return(close, intervals) for intervals in (1, 2, 4, 12, 24, 48, 96)),
        float(np.std(returns[-4:], ddof=0)), float(np.std(returns[-24:], ddof=0)),
        float(np.std(returns[-96:], ddof=0)),
        float(np.sqrt(np.mean(np.minimum(returns[-24:], 0.0) ** 2))),
        float(np.sqrt(np.mean(np.minimum(returns[-96:], 0.0) ** 2))),
        float((last["close"] - last["open"]) / last["open"]),
        float((last["high"] - last["low"]) / last["open"]),
        _zscore_last(local["volume"].iloc[-96:]), _zscore_last(local["turnover"].iloc[-96:]),
        _log_return(btc["close"], 4), _log_return(btc["close"], 24), _log_return(btc["close"], 96),
        float(np.std(btc_returns[-24:], ddof=0)), float(np.std(btc_returns[-96:], ddof=0)),
        math.sin(2.0 * math.pi * decision_time.hour / 24.0),
        math.cos(2.0 * math.pi * decision_time.hour / 24.0),
    )


def _trade_net_return(
    symbol: str,
    decision_time: pd.Timestamp,
    entry_open: Decimal,
    exit_open: Decimal,
    rules: dict[str, InstrumentRule],
    mark_frames: dict[str, pd.DataFrame],
    funding_frames: dict[str, pd.DataFrame],
    *,
    cost_multiplier: Decimal = Decimal("1"),
) -> float:
    """固定100 USDT longの正確な費用込みnet returnを返す。

    Args:
        symbol: 対象銘柄。
        decision_time: entry時刻。
        entry_open: raw entry open。
        exit_open: raw exit open。
        rules: 数量規則。
        mark_frames: Funding代理mark。
        funding_frames: 実績Funding。
        cost_multiplier: fee、spread、slippage倍率。

    Returns:
        raw entry notional基準net return。

    Raises:
        ValueError: quantityまたはFunding代理値を構築できない場合。
    """

    quantity = _quantity(rules[symbol], entry_open)
    if quantity <= 0:
        raise ValueError("allocation rejection")
    adverse = (HALF_SPREAD + SLIPPAGE) * cost_multiplier
    entry_fill = entry_open * (Decimal("1") + adverse)
    exit_fill = exit_open * (Decimal("1") - adverse)
    fees = quantity * (entry_fill + exit_fill) * TAKER_FEE * cost_multiplier
    funding_cash = Decimal("0")
    settlements = funding_frames[symbol].loc[
        (funding_frames[symbol].index > decision_time)
        & (funding_frames[symbol].index < decision_time + HORIZON)
    ]
    for settlement_time, row in settlements.iterrows():
        mark_open = Decimal(str(mark_frames[symbol].loc[settlement_time, "open"]))
        funding_cash -= quantity * mark_open * Decimal(str(row["funding_rate"]))
    spread = quantity * (entry_open + exit_open) * HALF_SPREAD * cost_multiplier
    slippage = quantity * (entry_open + exit_open) * SLIPPAGE * cost_multiplier
    pnl = quantity * (exit_open - entry_open) + funding_cash - fees - spread - slippage
    return float(pnl / (quantity * entry_open))


def build_samples(
    trade_frames: dict[str, pd.DataFrame],
    mark_frames: dict[str, pd.DataFrame],
    funding_frames: dict[str, pd.DataFrame],
    rules: dict[str, InstrumentRule],
    *,
    symbols: tuple[str, ...] = SOURCE_SYMBOLS,
    decision_start: pd.Timestamp = DATA_START + pd.Timedelta(hours=30),
    decision_end: pd.Timestamp = SOURCE_END,
) -> tuple[list[Sample], dict[pd.Timestamp, str]]:
    """sourceだけから時点整合済み15分標本を作る。

    Args:
        trade_frames: sourceとBTCの15分足。
        mark_frames: sourceの1時間mark。
        funding_frames: source Funding。
        rules: source数量規則。
        symbols: cross-section source universe。
        decision_start: 最初の判断境界。
        decision_end: exclusive終了境界。

    Returns:
        標本と除外理由。

    Raises:
        ValueError: target混入またはuniverse不整合の場合。
    """

    if set(symbols) & set(SEALED_TARGET_SYMBOLS):
        raise ValueError("sealed targets are prohibited in source gate")
    if CONTEXT_SYMBOL not in trade_frames or not set(symbols).issubset(trade_frames):
        raise ValueError("source 15m inputs are incomplete")
    samples: list[Sample] = []
    exclusions: dict[pd.Timestamp, str] = {}
    for decision_time in pd.date_range(decision_start, decision_end, freq="6h", inclusive="left"):
        exit_time = decision_time + HORIZON
        if exit_time >= decision_end:
            continue
        try:
            bases = {
                symbol: _base_features(trade_frames[symbol], trade_frames[CONTEXT_SYMBOL], decision_time)
                for symbol in symbols
            }
            six_hour = {symbol: values[4] for symbol, values in bases.items()}
            ordered = sorted(symbols, key=lambda symbol: (-six_hour[symbol], symbol))
            median = float(np.median(list(six_hour.values())))
            dispersion = float(np.std(list(six_hour.values()), ddof=0))
            if dispersion <= 1e-12:
                raise ValueError("cross-sectional dispersion is too small")
            staged: list[Sample] = []
            for symbol in symbols:
                rank = ordered.index(symbol)
                base = bases[symbol]
                features = (
                    *base[:21], 1.0 - rank / (len(symbols) - 1),
                    six_hour[symbol] - median, dispersion, *base[21:]
                )
                entry = Decimal(str(trade_frames[symbol].loc[decision_time, "open"]))
                exit_price = Decimal(str(trade_frames[symbol].loc[exit_time, "open"]))
                net_return = _trade_net_return(
                    symbol, decision_time, entry, exit_price, rules, mark_frames, funding_frames
                )
                if len(features) != len(FEATURE_NAMES) or not all(
                    math.isfinite(value) for value in (*features, net_return)
                ):
                    raise ValueError("invalid 15m feature or net label")
                staged.append(Sample(decision_time, symbol, features, net_return, entry, exit_price))
            samples.extend(staged)
        except (KeyError, ValueError) as error:
            exclusions[decision_time] = str(error)
    return samples, exclusions


def load_15m_source(
    data_dir: Path,
    metadata_path: Path,
) -> dict[str, pd.DataFrame]:
    """hash検証後にsource15分足だけを2026年より前まで読む。

    Args:
        data_dir: DATA-2026-0008保存先。
        metadata_path: DATA-2026-0008 metadata。

    Returns:
        sourceとBTCのtrade frame。

    Raises:
        ValueError: metadata、封印、hashが不正な場合。
    """

    if _sha256(metadata_path) != METADATA_SHA256:
        raise ValueError("DATA-2026-0008 metadata hash mismatch")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("snapshot_id") != SNAPSHOT_ID:
        raise ValueError("unexpected 15m snapshot")
    if metadata.get("sealed_holdout", {}).get("content_opened") is not False:
        raise ValueError("sealed target snapshot was opened")
    records = {record["symbol"]: record for record in metadata["symbols"]}
    frames: dict[str, pd.DataFrame] = {}
    for symbol in (CONTEXT_SYMBOL, *SOURCE_SYMBOLS):
        path = data_dir / symbol / "trade-15m.csv"
        if _sha256(path) != records[symbol]["artifacts"]["trade_15m"]["sha256"]:
            raise ValueError(f"15m artifact hash mismatch: {symbol}")
        frame = _read_before_cutoff(path, SOURCE_END)
        frames[symbol] = _numeric_price_frame(frame, "trade")
    return frames


def _ordered(samples: list[Sample]) -> list[Sample]:
    """標本をquery時刻・symbol順へ並べる。

    Args:
        samples: 任意順標本。

    Returns:
        決定論的順序の標本。
    """

    return sorted(samples, key=lambda sample: (sample.decision_time, sample.symbol))


def fit_ranker(samples: list[Sample]) -> object:
    """固定LambdaRank modelを学習する。

    Args:
        samples: train銘柄が各時刻で揃う標本。

    Returns:
        XGBoost booster。

    Raises:
        ValueError: query groupが不完全な場合。
    """

    import xgboost as xgb

    rows = _ordered(samples)
    groups: list[int] = []
    labels: list[float] = []
    by_time: dict[pd.Timestamp, list[Sample]] = {}
    for sample in rows:
        by_time.setdefault(sample.decision_time, []).append(sample)
    expected_size = len({sample.symbol for sample in rows})
    for group in by_time.values():
        if len(group) != expected_size:
            raise ValueError("incomplete ranking train query")
        ranked = sorted(group, key=lambda sample: (sample.net_return, sample.symbol))
        relevance = {sample.symbol: rank for rank, sample in enumerate(ranked)}
        labels.extend(float(relevance[sample.symbol]) for sample in group)
        groups.append(len(group))
    matrix = xgb.DMatrix(np.asarray([sample.features for sample in rows]), label=np.asarray(labels))
    matrix.set_group(groups)
    parameters = {
        "objective": "rank:pairwise", "eval_metric": "ndcg@1", "max_depth": 3,
        "eta": 0.05, "min_child_weight": 20, "subsample": 1.0, "colsample_bytree": 1.0,
        "lambda": 10.0, "alpha": 0.0, "gamma": 0.0, "tree_method": "hist",
        "max_bin": 256, "seed": 0, "nthread": 1, "verbosity": 0,
    }
    return xgb.train(parameters, matrix, num_boost_round=300)


def fit_quantile(samples: list[Sample]) -> object:
    """固定40% quantile modelを学習する。

    Args:
        samples: train標本。

    Returns:
        XGBoost booster。
    """

    import xgboost as xgb

    rows = _ordered(samples)
    matrix = xgb.QuantileDMatrix(
        np.asarray([sample.features for sample in rows]),
        label=np.asarray([sample.net_return for sample in rows]), max_bin=256,
    )
    parameters = {
        "objective": "reg:quantileerror", "quantile_alpha": QUANTILE_ALPHA,
        "eval_metric": "quantile", "max_depth": 3, "eta": 0.05,
        "min_child_weight": 20, "subsample": 1.0, "colsample_bytree": 1.0,
        "lambda": 10.0, "alpha": 0.0, "gamma": 0.0, "tree_method": "hist",
        "max_bin": 256, "seed": 0, "nthread": 1, "verbosity": 0,
    }
    return xgb.train(parameters, matrix, num_boost_round=300)


def predict(model: object, samples: list[Sample]) -> np.ndarray:
    """boosterで標本順の予測を返す。

    Args:
        model: XGBoost booster。
        samples: 予測標本。

    Returns:
        sample順予測。
    """

    import xgboost as xgb

    matrix = xgb.DMatrix(np.asarray([sample.features for sample in samples]))
    return np.asarray(model.predict(matrix), dtype=float)


def evaluate(
    samples: list[Sample],
    rank_scores: np.ndarray,
    quantiles: np.ndarray,
    rules: dict[str, InstrumentRule],
    mark_frames: dict[str, pd.DataFrame],
    funding_frames: dict[str, pd.DataFrame],
    *,
    cost_multiplier: Decimal = Decimal("1"),
    momentum: bool = False,
) -> dict[str, object]:
    """held-out groupのtop1を固定会計で評価する。

    Args:
        samples: 評価group標本。
        rank_scores: rankerまたはmomentum score。
        quantiles: q40予測。momentum時は未使用。
        rules: 数量規則。
        mark_frames: mark価格。
        funding_frames: Funding。
        cost_multiplier: 費用倍率。
        momentum: baselineならTrue。

    Returns:
        損益、取引数、drawdown、銘柄別寄与、監査行。

    Raises:
        ValueError: 長さ、group、allocation、reserveが不正な場合。
    """

    if len(samples) != len(rank_scores) or len(samples) != len(quantiles):
        raise ValueError("evaluation lengths differ")
    grouped: dict[pd.Timestamp, list[tuple[Sample, float, float]]] = {}
    for sample, score, quantile in zip(samples, rank_scores, quantiles, strict=True):
        grouped.setdefault(sample.decision_time, []).append((sample, float(score), float(quantile)))
    group_size = len({sample.symbol for sample in samples})
    equity = INITIAL_EQUITY
    peak = INITIAL_EQUITY
    maximum_drawdown = Decimal("0")
    trades: list[dict[str, object]] = []
    for decision_time, candidates in sorted(grouped.items()):
        if len(candidates) != group_size:
            raise ValueError("incomplete evaluation query")
        ordered = sorted(candidates, key=lambda item: (-item[1], item[0].symbol))
        sample, score, quantile = ordered[0]
        score_scale = float(np.std([item[1] for item in candidates], ddof=0))
        margin = (score - ordered[1][1]) / max(score_scale, 1e-12)
        if momentum:
            if score <= 0 or not math.isfinite(score):
                continue
        elif not all(math.isfinite(value) for value in (score, quantile, margin)) or margin < NORMALIZED_MARGIN or quantile <= 0:
            continue
        if equity - NOTIONAL < Decimal("200"):
            raise ValueError("reserve cash rejection")
        quantity = _quantity(rules[sample.symbol], sample.entry_open)
        if quantity <= 0:
            raise ValueError("allocation rejection")
        net_return = Decimal(str(_trade_net_return(
            sample.symbol, decision_time, sample.entry_open, sample.exit_open,
            rules, mark_frames, funding_frames, cost_multiplier=cost_multiplier,
        )))
        pnl = net_return * quantity * sample.entry_open
        equity += pnl
        peak = max(peak, equity)
        maximum_drawdown = min(maximum_drawdown, equity / peak - Decimal("1"))
        trades.append({
            "decision_time": decision_time.isoformat(), "exit_time": (decision_time + HORIZON).isoformat(),
            "symbol": sample.symbol, "rank_score": score, "normalized_margin": margin,
            "predicted_q40_net_return": quantile, "quantity": str(quantity),
            "realized_net_return": str(net_return), "net_pnl": str(pnl), "equity": str(equity),
        })
    symbols = sorted({sample.symbol for sample in samples})
    return {
        "net_pnl": str(equity - INITIAL_EQUITY), "completed_round_trips": len(trades),
        "max_drawdown": str(maximum_drawdown),
        "symbol_net_pnl": {
            symbol: str(sum((Decimal(trade["net_pnl"]) for trade in trades if trade["symbol"] == symbol), Decimal("0")))
            for symbol in symbols
        },
        "trades": trades,
    }


def _rank_ic(samples: list[Sample], scores: np.ndarray) -> float:
    """queryごとのSpearman相関平均を返す。

    Args:
        samples: 評価標本。
        scores: 予測またはmomentum score。

    Returns:
        有限queryの平均Spearman相関。
    """

    frame = pd.DataFrame({
        "time": [sample.decision_time for sample in samples],
        "actual": [sample.net_return for sample in samples], "score": scores,
    })
    values = [group["actual"].corr(group["score"], method="spearman") for _, group in frame.groupby("time")]
    finite = [float(value) for value in values if pd.notna(value)]
    return float(np.mean(finite)) if finite else float("nan")


def _pinball(labels: np.ndarray, predictions: np.ndarray) -> float:
    """固定alphaの平均pinball lossを返す。

    Args:
        labels: 実現net return。
        predictions: q40予測。

    Returns:
        平均pinball loss。
    """

    residual = labels - predictions
    return float(np.mean(np.maximum(QUANTILE_ALPHA * residual, (QUANTILE_ALPHA - 1.0) * residual)))


def run_gate(
    samples: list[Sample],
    rules: dict[str, InstrumentRule],
    mark_frames: dict[str, pd.DataFrame],
    funding_frames: dict[str, pd.DataFrame],
) -> dict[str, object]:
    """固定12 asset×time OOS foldを実行する。

    Args:
        samples: source全標本。
        rules: 数量規則。
        mark_frames: mark価格。
        funding_frames: Funding。

    Returns:
        fold結果、aggregate、gate判定。
    """

    folds: dict[str, dict[str, object]] = {}
    all_labels: list[float] = []
    all_quantiles: list[float] = []
    all_constants: list[float] = []
    rank_ics: list[float] = []
    momentum_ics: list[float] = []
    for time_index, (cutoff, end) in enumerate(TIME_FOLDS):
        for asset_name, held_out in ASSET_FOLDS.items():
            train = [
                sample for sample in samples
                if sample.symbol not in held_out
                and sample.decision_time >= cutoff - TRAIN_WINDOW
                and sample.decision_time + HORIZON < cutoff
            ]
            evaluation = [
                sample for sample in samples
                if sample.symbol in held_out and sample.decision_time >= cutoff
                and sample.decision_time + HORIZON < end
            ]
            ranker = fit_ranker(train)
            quantile_model = fit_quantile(train)
            rank_scores = predict(ranker, evaluation)
            q40 = predict(quantile_model, evaluation)
            base = evaluate(evaluation, rank_scores, q40, rules, mark_frames, funding_frames)
            stress = evaluate(
                evaluation, rank_scores, q40, rules, mark_frames, funding_frames,
                cost_multiplier=Decimal("2"),
            )
            momentum_scores = np.asarray([sample.features[4] for sample in evaluation], dtype=float)
            momentum = evaluate(
                evaluation, momentum_scores, np.zeros(len(evaluation)), rules, mark_frames,
                funding_frames, momentum=True,
            )
            labels = np.asarray([sample.net_return for sample in evaluation], dtype=float)
            constant = float(np.quantile([sample.net_return for sample in train], QUANTILE_ALPHA))
            rank_ic = _rank_ic(evaluation, rank_scores)
            momentum_ic = _rank_ic(evaluation, momentum_scores)
            key = f"T{time_index + 1}-{asset_name}"
            folds[key] = {
                "cutoff": cutoff.isoformat(), "end_exclusive": end.isoformat(),
                "held_out_symbols": held_out, "train_rows": len(train), "evaluation_rows": len(evaluation),
                "rank_ic": rank_ic, "momentum_rank_ic": momentum_ic,
                "q40_pinball_loss": _pinball(labels, q40),
                "constant_q40_pinball_loss": _pinball(labels, np.full(len(labels), constant)),
                "base": base, "stress_2x_cost": stress, "momentum": momentum,
            }
            all_labels.extend(labels.tolist())
            all_quantiles.extend(q40.tolist())
            all_constants.extend([constant] * len(labels))
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
    labels_array = np.asarray(all_labels)
    quantiles_array = np.asarray(all_quantiles)
    constants_array = np.asarray(all_constants)
    aggregate_rank_ic = float(np.mean(rank_ics))
    aggregate_momentum_ic = float(np.mean(momentum_ics))
    quantile_loss = _pinball(labels_array, quantiles_array)
    constant_loss = _pinball(labels_array, constants_array)
    checks = {
        "minimum_240_round_trips": trades >= 240,
        "minimum_9_positive_folds": positive_folds >= 9,
        "minimum_6_positive_symbols": positive_symbols >= 6,
        "positive_and_beats_momentum": aggregate_net > 0 and aggregate_net > aggregate_momentum,
        "positive_stress_2x_cost": aggregate_stress > 0,
        "rank_ic_positive_and_beats_momentum": aggregate_rank_ic > 0 and aggregate_rank_ic > aggregate_momentum_ic,
        "q40_pinball_beats_constant": quantile_loss < constant_loss,
        "maximum_positive_profit_contribution_35pct": max_contribution <= Decimal("0.35"),
        "integrity_errors_zero": True,
    }
    return {
        "experiment_id": EXPERIMENT_ID, "stage": "SOURCE_GATE_COMPLETED",
        "sealed_target_opened": False, "target_opening_authorized": all(checks.values()),
        "selected_pipeline": "xgboost_rank_pairwise_plus_q40" if all(checks.values()) else None,
        "parameters": {
            "features": FEATURE_NAMES, "quantile_alpha": QUANTILE_ALPHA,
            "normalized_margin": NORMALIZED_MARGIN, "folds": 12, "random_seed": 0,
        },
        "aggregate": {
            "net_pnl": str(aggregate_net), "stress_2x_cost_net_pnl": str(aggregate_stress),
            "momentum_net_pnl": str(aggregate_momentum), "completed_round_trips": trades,
            "positive_folds": positive_folds, "positive_symbols": positive_symbols,
            "symbol_net_pnl": {symbol: str(value) for symbol, value in symbol_pnl.items()},
            "rank_ic": aggregate_rank_ic, "momentum_rank_ic": aggregate_momentum_ic,
            "q40_pinball_loss": quantile_loss, "constant_q40_pinball_loss": constant_loss,
            "maximum_positive_profit_contribution": str(max_contribution),
        },
        "gate_checks": checks, "folds": folds,
    }


def main() -> None:
    """source15分足を構築し、固定12-fold gateを保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/EXP-2026-0056"))
    parser.add_argument("--metadata", type=Path, default=Path("var/exp-2026-0056-data.json"))
    parser.add_argument("--primary-data-dir", type=Path, default=Path("data/processed/EXP-2026-0054"))
    parser.add_argument("--primary-metadata", type=Path, default=Path("var/exp-2026-0054-data.json"))
    parser.add_argument("--supplement-data-dir", type=Path, default=Path("data/processed/EXP-2026-0055"))
    parser.add_argument("--supplement-metadata", type=Path, default=Path("var/exp-2026-0055-data.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/EXP-2026-0056-source-gate"))
    args = parser.parse_args()
    trade = load_15m_source(args.data_dir, args.metadata)
    _, mark, funding, rules = load_source_inputs(
        args.primary_data_dir, args.primary_metadata, args.supplement_data_dir,
        args.supplement_metadata, value_cutoff=SOURCE_END,
    )
    samples, exclusions = build_samples(trade, mark, funding, rules)
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
