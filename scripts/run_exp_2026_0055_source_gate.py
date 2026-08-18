#!/usr/bin/env python3
"""EXP-2026-0055のsource-only銘柄転移gateを実行する。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Protocol

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


EXPERIMENT_ID = "EXP-2026-0055"
SOURCE_SYMBOLS = (
    "LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT",
    "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "NEARUSDT",
)
SEALED_TARGET_SYMBOLS = ("BCHUSDT", "LTCUSDT", "DOTUSDT", "DOGEUSDT")
CONTEXT_SYMBOL = "BTCUSDT"
FIT_START = pd.Timestamp("2022-02-01T00:00:00Z")
CALIBRATION_START = pd.Timestamp("2024-01-01T00:00:00Z")
DEVELOPMENT_START = pd.Timestamp("2025-01-01T00:00:00Z")
SEALED_TARGET_START = pd.Timestamp("2026-01-01T00:00:00Z")
HORIZON = pd.Timedelta(hours=6)
EVENT_RETURN_THRESHOLD = 0.0064
PROBABILITY_THRESHOLD = 0.45
LOGISTIC_L2 = 1.0
PLATT_L2 = 1.0
FEATURE_NAMES = (
    "return_1h", "return_3h", "return_6h", "return_12h", "return_24h",
    "volatility_6h", "volatility_24h", "downside_semivolatility_24h",
    "body_1h", "range_1h", "volume_z_24h", "turnover_z_24h",
    "btc_return_6h", "btc_return_24h", "btc_volatility_24h",
    "cross_sectional_rank", "cross_sectional_median_difference",
    "cross_sectional_dispersion", "utc_hour_sin", "utc_hour_cos",
    "btc_direction_volatility_interaction",
)
NOTIONAL = Decimal("100")
INITIAL_EQUITY = Decimal("1000")
TAKER_FEE = Decimal("0.0006")
HALF_SPREAD = Decimal("0.0005")
SLIPPAGE = Decimal("0.0005")


@dataclass(frozen=True)
class Sample:
    """一つの銘柄・判断境界に対応する時点整合済み標本。"""

    decision_time: pd.Timestamp
    symbol: str
    features: tuple[float, ...]
    event: int
    gross_return: float
    entry_open: Decimal
    exit_open: Decimal


class BinaryModel(Protocol):
    """生logitを返す二値分類modelのinterface。"""

    def predict_margin(self, samples: list[Sample]) -> np.ndarray:
        """標本ごとの未校正logitを返す。"""


@dataclass(frozen=True)
class LogisticModel:
    """標準化量と係数を持つL2 logistic model。"""

    means: np.ndarray
    scales: np.ndarray
    active: np.ndarray
    coefficients: np.ndarray

    def predict_margin(self, samples: list[Sample]) -> np.ndarray:
        """標本ごとの未校正logitを返す。

        Args:
            samples: 予測標本。

        Returns:
            sample順のlogit。
        """

        values = np.asarray([sample.features for sample in samples], dtype=float)
        standardized = (values - self.means) / self.scales
        standardized[:, ~self.active] = 0.0
        design = np.column_stack([np.ones(len(standardized)), standardized])
        return design @ self.coefficients


@dataclass(frozen=True)
class XGBoostModel:
    """固定設定XGBoost boosterのadapter。"""

    booster: object

    def predict_margin(self, samples: list[Sample]) -> np.ndarray:
        """標本ごとの未校正logitを返す。

        Args:
            samples: 予測標本。

        Returns:
            sample順のlogit。
        """

        import xgboost as xgb

        matrix = xgb.DMatrix(np.asarray([sample.features for sample in samples], dtype=float))
        return np.asarray(self.booster.predict(matrix, output_margin=True), dtype=float)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    """overflowを避けてsigmoidを計算する。

    Args:
        values: 任意shapeのlogit。

    Returns:
        同じshapeの確率。
    """

    clipped = np.clip(values, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _fit_logistic_coefficients(
    design: np.ndarray,
    labels: np.ndarray,
    *,
    l2: float,
    maximum_iterations: int = 100,
) -> np.ndarray:
    """Newton法で切片を正則化しないL2 logistic係数を求める。

    Args:
        design: 先頭列が1の二次元design matrix。
        labels: 0/1 label。
        l2: 切片以外へ掛けるL2係数。
        maximum_iterations: 最大反復数。

    Returns:
        切片を先頭に持つ係数。

    Raises:
        ValueError: 入力、class、収束が不正な場合。
    """

    if design.ndim != 2 or len(design) != len(labels) or design.shape[1] < 2:
        raise ValueError("invalid logistic design")
    if set(np.unique(labels)) != {0.0, 1.0}:
        raise ValueError("both classes are required")
    base_rate = float(np.mean(labels))
    coefficients = np.zeros(design.shape[1], dtype=float)
    coefficients[0] = math.log(base_rate / (1.0 - base_rate))
    penalty = np.eye(design.shape[1], dtype=float) * l2
    penalty[0, 0] = 0.0
    for _ in range(maximum_iterations):
        probabilities = _sigmoid(design @ coefficients)
        gradient = design.T @ (probabilities - labels) + penalty @ coefficients
        weights = np.maximum(probabilities * (1.0 - probabilities), 1e-9)
        hessian = design.T @ (design * weights[:, None]) + penalty
        step = np.linalg.solve(hessian, gradient)
        coefficients -= step
        if float(np.max(np.abs(step))) < 1e-8:
            return coefficients
    raise ValueError("logistic fit did not converge")


def fit_logistic(samples: list[Sample]) -> LogisticModel:
    """事前登録した標準化L2 logistic modelを学習する。

    Args:
        samples: fit期間のsource標本。

    Returns:
        学習済みmodel。

    Raises:
        ValueError: 標本が空、class不足、または未収束の場合。
    """

    if not samples:
        raise ValueError("logistic training samples are empty")
    values = np.asarray([sample.features for sample in samples], dtype=float)
    labels = np.asarray([sample.event for sample in samples], dtype=float)
    means = values.mean(axis=0)
    raw_scales = values.std(axis=0, ddof=0)
    active = raw_scales > 1e-12
    scales = np.where(active, raw_scales, 1.0)
    standardized = (values - means) / scales
    standardized[:, ~active] = 0.0
    design = np.column_stack([np.ones(len(standardized)), standardized])
    coefficients = _fit_logistic_coefficients(design, labels, l2=LOGISTIC_L2)
    return LogisticModel(means, scales, active, coefficients)


def fit_xgboost(samples: list[Sample]) -> XGBoostModel:
    """事前登録した固定XGBoost binary classifierを学習する。

    Args:
        samples: fit期間のsource標本。

    Returns:
        学習済みadapter。

    Raises:
        ValueError: 標本が空またはclass不足の場合。
    """

    import xgboost as xgb

    if not samples:
        raise ValueError("xgboost training samples are empty")
    labels = np.asarray([sample.event for sample in samples], dtype=float)
    if set(np.unique(labels)) != {0.0, 1.0}:
        raise ValueError("both classes are required")
    matrix = xgb.DMatrix(np.asarray([sample.features for sample in samples], dtype=float), label=labels)
    parameters = {
        "objective": "binary:logistic", "eval_metric": "logloss", "max_depth": 3,
        "eta": 0.05, "min_child_weight": 20, "subsample": 1.0,
        "colsample_bytree": 1.0, "lambda": 10.0, "alpha": 0.0, "gamma": 0.0,
        "tree_method": "hist", "max_bin": 256, "seed": 0, "nthread": 1, "verbosity": 0,
    }
    return XGBoostModel(xgb.train(parameters, matrix, num_boost_round=200))


def fit_platt(margins: np.ndarray, samples: list[Sample]) -> np.ndarray:
    """時間分離したcalibration標本でPlatt係数を学習する。

    Args:
        margins: 未校正modelのcalibration logit。
        samples: calibration標本。

    Returns:
        intercept、slopeの順の係数。
    """

    design = np.column_stack([np.ones(len(margins)), np.asarray(margins, dtype=float)])
    labels = np.asarray([sample.event for sample in samples], dtype=float)
    return _fit_logistic_coefficients(design, labels, l2=PLATT_L2)


def calibrated_probabilities(margins: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
    """Platt係数で確率を校正してclipする。

    Args:
        margins: 未校正logit。
        coefficients: interceptとslope。

    Returns:
        1e-6から1-1e-6の校正済み確率。
    """

    values = _sigmoid(coefficients[0] + coefficients[1] * np.asarray(margins, dtype=float))
    return np.clip(values, 1e-6, 1.0 - 1e-6)


def _log_return(close: pd.Series, hours: int) -> float:
    """末尾closeの指定時間log returnを返す。

    Args:
        close: 連続した1時間close。
        hours: return horizon。

    Returns:
        log return。
    """

    return math.log(float(close.iloc[-1]) / float(close.iloc[-hours - 1]))


def _base_features(
    trade: pd.DataFrame,
    btc_trade: pd.DataFrame,
    decision_time: pd.Timestamp,
) -> tuple[float, ...]:
    """cross-section以外のsource共通18特徴量を作る。

    Args:
        trade: 当該銘柄のtrade Kline。
        btc_trade: BTC context Kline。
        decision_time: UTC判断境界。

    Returns:
        事前登録順の基礎特徴量。

    Raises:
        ValueError: 必要な実測行が連続しない場合。
    """

    expected = pd.date_range(decision_time - pd.Timedelta(hours=25), periods=25, freq="1h")
    local = trade.reindex(expected)
    btc = btc_trade.reindex(expected)
    if local.isna().any().any() or btc.isna().any().any():
        raise ValueError("missing feature-window observation")
    close = local["close"]
    returns = np.diff(np.log(close.to_numpy(dtype=float)))
    btc_returns = np.diff(np.log(btc["close"].to_numpy(dtype=float)))
    last = local.iloc[-1]
    btc_return_24h = _log_return(btc["close"], 24)
    btc_volatility = float(np.std(btc_returns[-24:], ddof=0))
    return (
        *(_log_return(close, hours) for hours in (1, 3, 6, 12, 24)),
        float(np.std(returns[-6:], ddof=0)),
        float(np.std(returns[-24:], ddof=0)),
        float(np.sqrt(np.mean(np.minimum(returns[-24:], 0.0) ** 2))),
        float((last["close"] - last["open"]) / last["open"]),
        float((last["high"] - last["low"]) / last["open"]),
        _zscore_last(local["volume"].iloc[-24:]),
        _zscore_last(local["turnover"].iloc[-24:]),
        _log_return(btc["close"], 6),
        btc_return_24h,
        btc_volatility,
        math.sin(2.0 * math.pi * decision_time.hour / 24.0),
        math.cos(2.0 * math.pi * decision_time.hour / 24.0),
        float(np.sign(btc_return_24h) * btc_volatility),
    )


def build_samples(
    trade_frames: dict[str, pd.DataFrame],
    *,
    symbols: tuple[str, ...] = SOURCE_SYMBOLS,
    decision_start: pd.Timestamp = FIT_START,
    decision_end: pd.Timestamp = SEALED_TARGET_START,
) -> tuple[list[Sample], dict[pd.Timestamp, str]]:
    """source銘柄だけから時点整合済み標本を作る。

    Args:
        trade_frames: source銘柄とBTCのtrade Kline。
        symbols: 同時刻cross-sectionを作るsource銘柄。
        decision_start: 最初の判断境界。
        decision_end: 判断境界exclusive上限。

    Returns:
        pooled標本と判断時刻別除外理由。

    Raises:
        ValueError: sealed target指定または特徴量不整合の場合。
    """

    if set(symbols) & set(SEALED_TARGET_SYMBOLS):
        raise ValueError("sealed target symbols are prohibited in source gate")
    if CONTEXT_SYMBOL not in trade_frames or not set(symbols).issubset(trade_frames):
        raise ValueError("source trade frames are incomplete")
    samples: list[Sample] = []
    exclusions: dict[pd.Timestamp, str] = {}
    decision_times = pd.date_range(decision_start, decision_end, freq="6h", inclusive="left")
    for decision_time in decision_times:
        exit_time = decision_time + HORIZON
        if exit_time >= decision_end:
            continue
        try:
            bases = {
                symbol: _base_features(trade_frames[symbol], trade_frames[CONTEXT_SYMBOL], decision_time)
                for symbol in symbols
            }
            six_hour = {symbol: values[2] for symbol, values in bases.items()}
            ordered = sorted(symbols, key=lambda symbol: (-six_hour[symbol], symbol))
            median = float(np.median(list(six_hour.values())))
            dispersion = float(np.std(list(six_hour.values()), ddof=0))
            if dispersion <= 1e-12:
                raise ValueError("cross-sectional dispersion is too small")
            staged: list[Sample] = []
            for symbol in symbols:
                rank = ordered.index(symbol)
                rank_score = 1.0 - rank / (len(symbols) - 1)
                base = bases[symbol]
                features = (
                    *base[:15], rank_score, six_hour[symbol] - median, dispersion, *base[15:]
                )
                trade = trade_frames[symbol]
                entry_open = Decimal(str(trade.loc[decision_time, "open"]))
                exit_open = Decimal(str(trade.loc[exit_time, "open"]))
                gross_return = float(exit_open / entry_open - Decimal("1"))
                event = int(gross_return > EVENT_RETURN_THRESHOLD)
                if len(features) != len(FEATURE_NAMES) or not all(
                    math.isfinite(value) for value in (*features, gross_return)
                ):
                    raise ValueError("invalid feature or target")
                staged.append(Sample(decision_time, symbol, features, event, gross_return, entry_open, exit_open))
            samples.extend(staged)
        except (KeyError, ValueError) as error:
            exclusions[decision_time] = str(error)
    return samples, exclusions


def _quantity(rule: InstrumentRule, entry_open: Decimal) -> Decimal:
    """100 USDTをquantity stepへ切り下げる。

    Args:
        rule: 取得時点の数量規則。
        entry_open: 未調整entry open。

    Returns:
        条件を満たすquantity、または0。
    """

    units = (NOTIONAL / entry_open / rule.quantity_step).to_integral_value(rounding=ROUND_DOWN)
    quantity = units * rule.quantity_step
    if quantity < rule.minimum_quantity or quantity * entry_open < rule.minimum_notional:
        return Decimal("0")
    return quantity


def evaluate_entries(
    samples: list[Sample],
    enter: np.ndarray,
    scores: np.ndarray,
    rules: dict[str, InstrumentRule],
    mark_frames: dict[str, pd.DataFrame],
    funding_frames: dict[str, pd.DataFrame],
    *,
    cost_multiplier: Decimal = Decimal("1"),
) -> dict[str, object]:
    """単一銘柄foldのentryを固定会計で評価する。

    Args:
        samples: 一つのheld-out銘柄のdevelopment標本。
        enter: 各標本のentry可否。
        scores: 監査用score。
        rules: 数量丸め規則。
        mark_frames: Funding代理mark価格。
        funding_frames: 実績Funding。
        cost_multiplier: fee、spread、slippage倍率。

    Returns:
        損益、drawdown、取引監査行。

    Raises:
        ValueError: 入力長、複数銘柄、または会計入力が不正な場合。
    """

    if len(samples) != len(enter) or len(samples) != len(scores):
        raise ValueError("evaluation lengths differ")
    symbols = {sample.symbol for sample in samples}
    if len(symbols) != 1:
        raise ValueError("one held-out symbol is required")
    equity = INITIAL_EQUITY
    peak = INITIAL_EQUITY
    maximum_drawdown = Decimal("0")
    trades: list[dict[str, object]] = []
    for sample, should_enter, score in zip(samples, enter, scores, strict=True):
        if not bool(should_enter):
            continue
        quantity = _quantity(rules[sample.symbol], sample.entry_open)
        if quantity <= 0:
            raise ValueError("allocation rejection")
        adverse = (HALF_SPREAD + SLIPPAGE) * cost_multiplier
        entry_fill = sample.entry_open * (Decimal("1") + adverse)
        exit_fill = sample.exit_open * (Decimal("1") - adverse)
        fees = quantity * (entry_fill + exit_fill) * TAKER_FEE * cost_multiplier
        funding_cash = Decimal("0")
        settlements = funding_frames[sample.symbol].loc[
            (funding_frames[sample.symbol].index > sample.decision_time)
            & (funding_frames[sample.symbol].index < sample.decision_time + HORIZON)
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
            "decision_time": sample.decision_time.isoformat(), "symbol": sample.symbol,
            "score": float(score), "quantity": str(quantity), "gross_price_pnl": str(gross_price_pnl),
            "funding_cash_flow": str(funding_cash), "fees": str(fees),
            "spread_cost": str(spread_cost), "slippage_cost": str(slippage_cost),
            "net_pnl": str(pnl), "equity": str(equity),
        })
    return {
        "net_pnl": str(equity - INITIAL_EQUITY),
        "completed_round_trips": len(trades),
        "max_drawdown": str(maximum_drawdown),
        "trades": trades,
    }


def _load_symbol(
    data_dir: Path,
    record: dict[str, object],
    symbol: str,
    *,
    value_cutoff: pd.Timestamp = SEALED_TARGET_START,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, InstrumentRule]:
    """hash検証後に一銘柄のtrade、mark、Funding、数量規則を読む。

    Args:
        data_dir: snapshot保存先。
        record: metadata内の銘柄record。
        symbol: 読み込むsource銘柄。
        value_cutoff: 読み込む値のexclusive上限。

    Returns:
        trade、mark、Funding、数量規則。

    Raises:
        ValueError: hashまたは入力値が不正な場合。
    """

    artifacts = record["artifacts"]
    frames: dict[str, pd.DataFrame] = {}
    for source in ("trade", "mark_price"):
        path = data_dir / symbol / f"{source.replace('_', '-')}-1h.csv"
        if _sha256(path) != artifacts[source]["sha256"]:
            raise ValueError(f"artifact hash mismatch: {symbol} {source}")
        frames[source] = _numeric_price_frame(_read_before_cutoff(path, value_cutoff), source)
    funding_path = data_dir / symbol / "funding-rate.csv"
    if _sha256(funding_path) != artifacts["funding"]["sha256"]:
        raise ValueError(f"artifact hash mismatch: {symbol} funding")
    funding = _read_before_cutoff(funding_path, value_cutoff)
    funding["funding_rate"] = pd.to_numeric(funding["funding_rate"], errors="raise")
    funding = funding.set_index("event_time").sort_index()
    lot = record["instrument"]["lotSizeFilter"]
    rule = InstrumentRule(Decimal(lot["qtyStep"]), Decimal(lot["minOrderQty"]), Decimal(lot["minNotionalValue"]))
    return frames["trade"], frames["mark_price"], funding, rule


def load_source_inputs(
    primary_data_dir: Path,
    primary_metadata_path: Path,
    supplement_data_dir: Path,
    supplement_metadata_path: Path,
    *,
    value_cutoff: pd.Timestamp = SEALED_TARGET_START,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, pd.DataFrame], dict[str, InstrumentRule]]:
    """二snapshotからsourceだけを読み、sealed targetを開かない。

    Args:
        primary_data_dir: DATA-2026-0006保存先。
        primary_metadata_path: DATA-2026-0006 metadata。
        supplement_data_dir: DATA-2026-0007保存先。
        supplement_metadata_path: DATA-2026-0007 metadata。
        value_cutoff: 読み込むsource値のexclusive上限。

    Returns:
        trade、mark、Funding、数量規則。

    Raises:
        ValueError: snapshot、封印状態、symbol、hashが不正な場合。
    """

    primary = json.loads(primary_metadata_path.read_text(encoding="utf-8"))
    supplement = json.loads(supplement_metadata_path.read_text(encoding="utf-8"))
    if primary.get("snapshot_id") != "DATA-2026-0006":
        raise ValueError("invalid primary snapshot")
    if supplement.get("snapshot_id") != "DATA-2026-0007":
        raise ValueError("invalid supplement snapshot")
    if supplement.get("sealed_holdout", {}).get("content_opened") is not False:
        raise ValueError("sealed target snapshot was opened")
    primary_records = {record["symbol"]: record for record in primary["symbols"]}
    supplement_records = {record["symbol"]: record for record in supplement["symbols"]}
    trade_frames: dict[str, pd.DataFrame] = {}
    mark_frames: dict[str, pd.DataFrame] = {}
    funding_frames: dict[str, pd.DataFrame] = {}
    rules: dict[str, InstrumentRule] = {}
    primary_symbols = (*SOURCE_SYMBOLS[:4], CONTEXT_SYMBOL)
    supplement_symbols = SOURCE_SYMBOLS[4:]
    for data_dir, records, symbols in (
        (primary_data_dir, primary_records, primary_symbols),
        (supplement_data_dir, supplement_records, supplement_symbols),
    ):
        for symbol in symbols:
            if symbol in SEALED_TARGET_SYMBOLS:
                raise ValueError("sealed target read attempted")
            trade, mark, funding, rule = _load_symbol(
                data_dir, records[symbol], symbol, value_cutoff=value_cutoff
            )
            trade_frames[symbol] = trade
            mark_frames[symbol] = mark
            funding_frames[symbol] = funding
            rules[symbol] = rule
    return trade_frames, mark_frames, funding_frames, rules


def _feature_hash() -> str:
    """事前登録feature順のSHA-256を返す。

    Returns:
        feature名JSONのSHA-256。
    """

    payload = json.dumps(FEATURE_NAMES, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _metric_payload(labels: np.ndarray, probabilities: np.ndarray) -> dict[str, float]:
    """分類確率の主要診断値を返す。

    Args:
        labels: 0/1 label。
        probabilities: 校正済み確率。

    Returns:
        Brier、log loss、precision、recall。
    """

    predicted = probabilities >= PROBABILITY_THRESHOLD
    true_positive = int(np.sum(predicted & (labels == 1)))
    false_positive = int(np.sum(predicted & (labels == 0)))
    false_negative = int(np.sum(~predicted & (labels == 1)))
    return {
        "brier_score": float(np.mean((probabilities - labels) ** 2)),
        "log_loss": float(-np.mean(labels * np.log(probabilities) + (1.0 - labels) * np.log(1.0 - probabilities))),
        "precision_at_probability_gate": true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0,
        "recall_at_probability_gate": true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0,
    }


def run_source_gate(
    samples: list[Sample],
    mark_frames: dict[str, pd.DataFrame],
    funding_frames: dict[str, pd.DataFrame],
    rules: dict[str, InstrumentRule],
) -> dict[str, object]:
    """2 model×9 leave-one-asset-out foldを実行する。

    Args:
        samples: 2022〜2025 source標本。
        mark_frames: source mark Kline。
        funding_frames: source Funding。
        rules: source数量規則。

    Returns:
        model別fold結果、gate、選択結果。
    """

    fitters = {"logistic": fit_logistic, "xgboost": fit_xgboost}
    model_results: dict[str, dict[str, object]] = {}
    for model_name, fitter in fitters.items():
        folds: dict[str, dict[str, object]] = {}
        all_labels: list[int] = []
        all_probabilities: list[float] = []
        all_constant_probabilities: list[float] = []
        for held_out in SOURCE_SYMBOLS:
            fit = [
                sample for sample in samples
                if sample.symbol != held_out and sample.decision_time >= FIT_START
                and sample.decision_time + HORIZON < CALIBRATION_START
            ]
            calibration = [
                sample for sample in samples
                if sample.symbol != held_out and sample.decision_time >= CALIBRATION_START
                and sample.decision_time + HORIZON < DEVELOPMENT_START
            ]
            development = [
                sample for sample in samples
                if sample.symbol == held_out and sample.decision_time >= DEVELOPMENT_START
                and sample.decision_time + HORIZON < SEALED_TARGET_START
            ]
            model = fitter(fit)
            calibration_margins = model.predict_margin(calibration)
            platt = fit_platt(calibration_margins, calibration)
            probabilities = calibrated_probabilities(model.predict_margin(development), platt)
            up_returns = [sample.gross_return for sample in calibration if sample.event == 1]
            non_up_returns = [sample.gross_return for sample in calibration if sample.event == 0]
            mean_up = float(np.mean(up_returns))
            mean_non_up = float(np.mean(non_up_returns))
            expected_returns = probabilities * mean_up + (1.0 - probabilities) * mean_non_up
            enter = (probabilities >= PROBABILITY_THRESHOLD) & (expected_returns > EVENT_RETURN_THRESHOLD)
            base = evaluate_entries(development, enter, probabilities, rules, mark_frames, funding_frames)
            stress = evaluate_entries(
                development, enter, probabilities, rules, mark_frames, funding_frames,
                cost_multiplier=Decimal("2"),
            )
            momentum_enter = np.asarray([sample.features[2] > 0.0 for sample in development], dtype=bool)
            momentum = evaluate_entries(
                development, momentum_enter, np.asarray([sample.features[2] for sample in development]),
                rules, mark_frames, funding_frames,
            )
            labels = np.asarray([sample.event for sample in development], dtype=float)
            constant_probability = float(np.mean([sample.event for sample in fit]))
            metrics = _metric_payload(labels, probabilities)
            folds[held_out] = {
                "sample_counts": {"fit": len(fit), "calibration": len(calibration), "development": len(development)},
                "fit_base_rate": constant_probability,
                "calibration_base_rate": float(np.mean([sample.event for sample in calibration])),
                "platt_intercept": float(platt[0]), "platt_slope": float(platt[1]),
                "calibration_mean_up_gross_return": mean_up,
                "calibration_mean_non_up_gross_return": mean_non_up,
                "metrics": metrics, "base": base, "stress_2x_cost": stress, "momentum": momentum,
            }
            all_labels.extend(labels.astype(int).tolist())
            all_probabilities.extend(probabilities.tolist())
            all_constant_probabilities.extend([constant_probability] * len(labels))
        aggregate_net = sum((Decimal(fold["base"]["net_pnl"]) for fold in folds.values()), Decimal("0"))
        aggregate_stress = sum((Decimal(fold["stress_2x_cost"]["net_pnl"]) for fold in folds.values()), Decimal("0"))
        aggregate_momentum = sum((Decimal(fold["momentum"]["net_pnl"]) for fold in folds.values()), Decimal("0"))
        total_trades = sum(int(fold["base"]["completed_round_trips"]) for fold in folds.values())
        positive_symbols = sum(Decimal(fold["base"]["net_pnl"]) > 0 for fold in folds.values())
        positive_profits = [max(Decimal(fold["base"]["net_pnl"]), Decimal("0")) for fold in folds.values()]
        positive_total = sum(positive_profits, Decimal("0"))
        maximum_contribution = (
            max(positive_profits, default=Decimal("0")) / positive_total if positive_total > 0 else Decimal("1")
        )
        label_array = np.asarray(all_labels, dtype=float)
        probability_array = np.asarray(all_probabilities, dtype=float)
        constant_array = np.asarray(all_constant_probabilities, dtype=float)
        aggregate_brier = float(np.mean((probability_array - label_array) ** 2))
        constant_brier = float(np.mean((constant_array - label_array) ** 2))
        checks = {
            "minimum_100_round_trips": total_trades >= 100,
            "minimum_6_positive_symbols": positive_symbols >= 6,
            "positive_and_beats_momentum": aggregate_net > 0 and aggregate_net > aggregate_momentum,
            "positive_stress_2x_cost": aggregate_stress > 0,
            "brier_beats_constant": aggregate_brier < constant_brier,
            "maximum_positive_profit_contribution_40pct": maximum_contribution <= Decimal("0.4"),
            "integrity_errors_zero": True,
        }
        model_results[model_name] = {
            "folds": folds,
            "aggregate": {
                "net_pnl": str(aggregate_net), "stress_2x_cost_net_pnl": str(aggregate_stress),
                "momentum_net_pnl": str(aggregate_momentum), "completed_round_trips": total_trades,
                "positive_symbols": positive_symbols, "brier_score": aggregate_brier,
                "constant_brier_score": constant_brier,
                "maximum_positive_profit_contribution": str(maximum_contribution),
            },
            "gate_checks": checks,
            "gate_passed": all(checks.values()),
        }
    candidates = [name for name, result in model_results.items() if result["gate_passed"]]
    selected_model: str | None = None
    if candidates:
        rounded = {
            name: Decimal(model_results[name]["aggregate"]["net_pnl"]).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_EVEN
            )
            for name in candidates
        }
        selected_model = max(candidates, key=lambda name: (rounded[name], name == "logistic"))
    return {
        "experiment_id": EXPERIMENT_ID,
        "stage": "SOURCE_GATE_COMPLETED",
        "sealed_target_opened": False,
        "target_opening_authorized": selected_model is not None,
        "selected_model": selected_model,
        "selection_reason": "highest_gate_passing_rounded_net_pnl" if selected_model else "no_model_passed_source_gate",
        "parameters": {
            "features": FEATURE_NAMES, "feature_hash": _feature_hash(),
            "event_return_threshold": EVENT_RETURN_THRESHOLD,
            "probability_threshold": PROBABILITY_THRESHOLD, "final_target_margin": 0.03,
            "model_variants": 2, "random_seed": 0,
        },
        "models": model_results,
    }


def main() -> None:
    """source-only datasetを構築し、gate結果を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--primary-data-dir", type=Path, default=Path("data/processed/EXP-2026-0054"))
    parser.add_argument("--primary-metadata", type=Path, default=Path("var/exp-2026-0054-data.json"))
    parser.add_argument("--supplement-data-dir", type=Path, default=Path("data/processed/EXP-2026-0055"))
    parser.add_argument("--supplement-metadata", type=Path, default=Path("var/exp-2026-0055-data.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/EXP-2026-0055-source-gate"))
    args = parser.parse_args()
    trade, mark, funding, rules = load_source_inputs(
        args.primary_data_dir, args.primary_metadata, args.supplement_data_dir, args.supplement_metadata
    )
    samples, exclusions = build_samples(trade)
    if exclusions:
        raise ValueError(f"source decision exclusions are prohibited: {len(exclusions)}")
    payload = run_source_gate(samples, mark, funding, rules)
    payload["sample_count"] = len(samples)
    payload["excluded_decision_times"] = len(exclusions)
    payload["input_metadata_sha256"] = {
        "DATA-2026-0006": _sha256(args.primary_metadata),
        "DATA-2026-0007": _sha256(args.supplement_metadata),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / "summary.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "experiment_id": payload["experiment_id"], "stage": payload["stage"],
        "sealed_target_opened": payload["sealed_target_opened"],
        "target_opening_authorized": payload["target_opening_authorized"],
        "selected_model": payload["selected_model"], "selection_reason": payload["selection_reason"],
        "sample_count": payload["sample_count"], "excluded_decision_times": payload["excluded_decision_times"],
        "aggregates": {name: result["aggregate"] for name, result in payload["models"].items()},
        "gate_checks": {name: result["gate_checks"] for name, result in payload["models"].items()},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
