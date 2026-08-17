#!/usr/bin/env python3
"""EXP-2026-0054の封印前development比較を実行する。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN
import hashlib
import json
import math
from pathlib import Path
import sys
import numpy as np
import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))


EXPERIMENT_ID = "EXP-2026-0054"
TRADED_SYMBOLS = ("LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT")
CONTEXT_SYMBOL = "BTCUSDT"
EVALUATION_START = pd.Timestamp("2022-02-01T00:00:00Z")
MODEL_SELECTION_START = pd.Timestamp("2025-01-01T00:00:00Z")
SEALED_HOLDOUT_START = pd.Timestamp("2026-01-01T00:00:00Z")
SEALED_HOLDOUT_END = pd.Timestamp("2026-08-01T00:00:00Z")
HORIZON = pd.Timedelta(hours=6)
FEATURE_NAMES = (
    "return_1h", "return_3h", "return_6h", "return_12h", "return_24h",
    "range_1h", "body_1h", "upper_wick_1h", "lower_wick_1h",
    "volume_z_24h", "turnover_z_24h", "volatility_6h", "volatility_24h",
    "downside_semivolatility_24h", "trade_mark_basis", "mark_index_basis",
    "premium_index_close", "latest_funding_rate", "time_to_funding_fraction",
    "btc_return_6h", "btc_return_24h", "btc_volatility_24h",
    "cross_sectional_rank", "cross_sectional_median_difference",
)
RIDGE_ALPHA = 10.0
PREDICTION_THRESHOLD = math.log1p(0.0064)
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
    target: float
    entry_open: Decimal
    exit_open: Decimal


@dataclass(frozen=True)
class InstrumentRule:
    """取得時点の数量丸め規則。"""

    quantity_step: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal


def _sha256(path: Path) -> str:
    """ファイルのSHA-256を返す。

    Args:
        path: 検証対象ファイル。

    Returns:
        16進SHA-256。
    """

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_before_cutoff(path: Path, cutoff: pd.Timestamp) -> pd.DataFrame:
    """時刻列だけで行数を決め、cutoff以降の値を開かずCSVを読む。

    Args:
        path: event_timeを持つCSV。
        cutoff: 値を読み込まないUTC境界。

    Returns:
        cutoffより前の行だけを含むDataFrame。

    Raises:
        ValueError: 時刻が不正、重複、非昇順、またはcutoff以降を含む場合。
    """

    times = pd.to_datetime(pd.read_csv(path, usecols=["event_time"])["event_time"], utc=True)
    if times.empty or times.duplicated().any() or not times.is_monotonic_increasing:
        raise ValueError(f"invalid event-time index: {path}")
    row_count = int((times < cutoff).sum())
    if row_count == 0:
        raise ValueError(f"no rows before sealed cutoff: {path}")
    frame = pd.read_csv(path, nrows=row_count)
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    if not (frame["event_time"] < cutoff).all():
        raise ValueError(f"sealed value crossed cutoff: {path}")
    return frame


def _numeric_price_frame(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    """価格frameを検査してevent_time indexへ変換する。

    Args:
        frame: CSV由来の価格frame。
        source: trade、mark、index、premiumの識別子。

    Returns:
        数値化済みframe。

    Raises:
        ValueError: 補間、欠列、非有限、または非正価格を検出した場合。
    """

    required = {"event_time", "open", "high", "low", "close", "is_interpolated"}
    if source == "trade":
        required |= {"volume", "turnover"}
    if not required.issubset(frame.columns):
        raise ValueError(f"missing {source} columns")
    interpolated = frame["is_interpolated"].astype(str).str.lower().isin({"true", "1"})
    if interpolated.any():
        raise ValueError(f"interpolated {source} rows are prohibited")
    numeric = frame.copy()
    for column in required - {"event_time", "is_interpolated"}:
        numeric[column] = pd.to_numeric(numeric[column], errors="raise")
        if not np.isfinite(numeric[column].to_numpy(dtype=float)).all():
            raise ValueError(f"non-finite {source}.{column}")
    if source != "premium_index" and (numeric[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError(f"non-positive {source} price")
    numeric = numeric.set_index("event_time").sort_index()
    if not numeric.index.is_unique:
        raise ValueError(f"duplicate {source} event time")
    return numeric


def _log_return(close: pd.Series, hours: int) -> float:
    """末尾closeの指定時間log returnを返す。

    Args:
        close: 1時間間隔でhours+1個以上の正のclose。
        hours: return horizon。

    Returns:
        log return。
    """

    return math.log(float(close.iloc[-1]) / float(close.iloc[-hours - 1]))


def _zscore_last(values: pd.Series) -> float:
    """直近値の母標準偏差z-scoreを返す。

    Args:
        values: 24個の非負活動量。

    Returns:
        最後のlog1p値のz-score。

    Raises:
        ValueError: 分散が事前登録下限以下の場合。
    """

    transformed = np.log1p(values.to_numpy(dtype=float))
    scale = float(np.std(transformed, ddof=0))
    if scale <= 1e-12:
        raise ValueError("activity variance is too small")
    return float((transformed[-1] - np.mean(transformed)) / scale)


def _funding_features(funding: pd.DataFrame, decision_time: pd.Timestamp) -> tuple[float, float]:
    """判断時点より前のFundingだけからrateと次回までの比率を作る。

    Args:
        funding: event_time indexとfunding_rateを持つframe。
        decision_time: UTC判断境界。

    Returns:
        最新既知rateと次回までの時間/cadence。

    Raises:
        ValueError: 過去Fundingが2件未満またはcadenceが不正な場合。
    """

    past = funding.loc[funding.index < decision_time]
    if len(past) < 2:
        raise ValueError("insufficient known funding history")
    previous, latest = past.index[-2], past.index[-1]
    cadence = latest - previous
    if cadence <= pd.Timedelta(0):
        raise ValueError("invalid funding cadence")
    time_to = latest + cadence - decision_time
    if time_to < pd.Timedelta(0) or time_to > cadence:
        raise ValueError("funding cadence does not cover decision time")
    return float(past.iloc[-1]["funding_rate"]), float(time_to / cadence)


def _base_features(
    frames: dict[str, pd.DataFrame],
    funding: pd.DataFrame,
    btc_trade: pd.DataFrame,
    decision_time: pd.Timestamp,
) -> tuple[float, ...]:
    """単一銘柄のcross-section以外22特徴量を作る。

    Args:
        frames: trade、mark、index、premium frame。
        funding: 当該銘柄Funding frame。
        btc_trade: BTC trade frame。
        decision_time: UTC判断境界。

    Returns:
        事前登録順の22特徴量。

    Raises:
        ValueError: 必要な実測1時間行が連続していない場合。
    """

    expected = pd.date_range(decision_time - pd.Timedelta(hours=25), periods=25, freq="1h")
    trade = frames["trade"].reindex(expected)
    mark = frames["mark_price"].reindex(expected)
    index = frames["index_price"].reindex(expected)
    premium = frames["premium_index"].reindex(expected)
    btc = btc_trade.reindex(expected)
    if any(value.isna().any().any() for value in (trade, mark, index, premium, btc)):
        raise ValueError("missing feature-window observation")
    close = trade["close"]
    hourly_returns = np.diff(np.log(close.to_numpy(dtype=float)))
    btc_returns = np.diff(np.log(btc["close"].to_numpy(dtype=float)))
    last = trade.iloc[-1]
    funding_rate, time_to_funding = _funding_features(funding, decision_time)
    return (
        *(_log_return(close, hours) for hours in (1, 3, 6, 12, 24)),
        float((last["high"] - last["low"]) / last["open"]),
        float((last["close"] - last["open"]) / last["open"]),
        float((last["high"] - max(last["open"], last["close"])) / last["open"]),
        float((min(last["open"], last["close"]) - last["low"]) / last["open"]),
        _zscore_last(trade["volume"].iloc[-24:]),
        _zscore_last(trade["turnover"].iloc[-24:]),
        float(np.std(hourly_returns[-6:], ddof=0)),
        float(np.std(hourly_returns[-24:], ddof=0)),
        float(np.sqrt(np.mean(np.minimum(hourly_returns[-24:], 0.0) ** 2))),
        math.log(float(trade.iloc[-1]["close"]) / float(mark.iloc[-1]["close"])),
        math.log(float(mark.iloc[-1]["close"]) / float(index.iloc[-1]["close"])),
        float(premium.iloc[-1]["close"]),
        funding_rate,
        time_to_funding,
        _log_return(btc["close"], 6),
        _log_return(btc["close"], 24),
        float(np.std(btc_returns[-24:], ddof=0)),
    )


def build_samples(
    prices: dict[str, dict[str, pd.DataFrame]],
    funding: dict[str, pd.DataFrame],
    *,
    decision_start: pd.Timestamp = EVALUATION_START,
    decision_end: pd.Timestamp = SEALED_HOLDOUT_START,
    exit_end: pd.Timestamp = SEALED_HOLDOUT_START,
) -> tuple[list[Sample], dict[pd.Timestamp, str]]:
    """指定期間の時点整合済み標本を作る。

    Args:
        prices: 銘柄、系列ごとの価格frame。
        funding: 取引銘柄ごとのFunding frame。
        decision_start: 最初の判断境界。
        decision_end: 判断境界のexclusive上限。
        exit_end: exit時刻のexclusive上限。

    Returns:
        pooled標本と判断時刻ごとの除外理由。

    Raises:
        ValueError: 生成特徴量数または数値が不正な場合。
    """

    samples: list[Sample] = []
    exclusions: dict[pd.Timestamp, str] = {}
    decision_times = pd.date_range(decision_start, decision_end, freq="6h", inclusive="left")
    btc_trade = prices[CONTEXT_SYMBOL]["trade"]
    for decision_time in decision_times:
        exit_time = decision_time + HORIZON
        if exit_time >= exit_end:
            continue
        try:
            base = {
                symbol: _base_features(prices[symbol], funding[symbol], btc_trade, decision_time)
                for symbol in TRADED_SYMBOLS
            }
            six_hour = {symbol: values[2] for symbol, values in base.items()}
            ordered = sorted(TRADED_SYMBOLS, key=lambda symbol: (-six_hour[symbol], symbol))
            median = float(np.median(list(six_hour.values())))
            staged: list[Sample] = []
            for symbol in TRADED_SYMBOLS:
                rank = ordered.index(symbol) + 1
                features = (*base[symbol], (4 - rank) / 3, six_hour[symbol] - median)
                trade = prices[symbol]["trade"]
                entry_open = Decimal(str(trade.loc[decision_time, "open"]))
                exit_open = Decimal(str(trade.loc[exit_time, "open"]))
                target = math.log(float(exit_open / entry_open))
                if len(features) != len(FEATURE_NAMES) or not all(math.isfinite(x) for x in (*features, target)):
                    raise ValueError("invalid feature or target")
                staged.append(Sample(decision_time, symbol, features, target, entry_open, exit_open))
            samples.extend(staged)
        except (KeyError, ValueError) as error:
            exclusions[decision_time] = str(error)
    return samples, exclusions


def fit_ridge(samples: list[Sample]) -> dict[str, np.ndarray | float]:
    """事前登録済みclosed-form Ridgeを学習する。

    Args:
        samples: 学習標本。

    Returns:
        標準化量、係数、切片を持つmodel。

    Raises:
        ValueError: 標本が空または行列を解けない場合。
    """

    if not samples:
        raise ValueError("ridge training samples are empty")
    x = np.asarray([sample.features for sample in samples], dtype=float)
    y = np.asarray([sample.target for sample in samples], dtype=float)
    means, scales = x.mean(axis=0), x.std(axis=0, ddof=0)
    active = scales > 1e-12
    safe_scales = np.where(active, scales, 1.0)
    z = (x - means) / safe_scales
    z[:, ~active] = 0.0
    target_mean, target_scale = float(y.mean()), float(y.std(ddof=0))
    if target_scale <= 1e-12:
        raise ValueError("ridge target variance is too small")
    zy = (y - target_mean) / target_scale
    design = np.column_stack([np.ones(len(z)), z])
    penalty = np.eye(design.shape[1]) * RIDGE_ALPHA
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ zy)
    return {"means": means, "scales": safe_scales, "active": active, "target_mean": target_mean,
            "target_scale": target_scale, "intercept": float(coefficients[0]), "weights": coefficients[1:]}


def predict_ridge(model: dict[str, np.ndarray | float], samples: list[Sample]) -> np.ndarray:
    """Ridge modelでgross log returnを予測する。

    Args:
        model: `fit_ridge`のmodel。
        samples: 予測標本。

    Returns:
        sample順の予測配列。
    """

    x = np.asarray([sample.features for sample in samples], dtype=float)
    z = (x - np.asarray(model["means"])) / np.asarray(model["scales"])
    z[:, ~np.asarray(model["active"], dtype=bool)] = 0.0
    standardized = float(model["intercept"]) + z @ np.asarray(model["weights"])
    return float(model["target_mean"]) + standardized * float(model["target_scale"])


def fit_predict_xgboost(train: list[Sample], evaluation: list[Sample]) -> np.ndarray:
    """固定XGBoostを学習しgross log returnを予測する。

    Args:
        train: 学習標本。
        evaluation: 予測標本。

    Returns:
        evaluation順の予測配列。
    """

    import xgboost as xgb

    train_matrix = xgb.DMatrix(np.asarray([row.features for row in train]), label=np.asarray([row.target for row in train]))
    evaluation_matrix = xgb.DMatrix(np.asarray([row.features for row in evaluation]))
    parameters = {"objective": "reg:squarederror", "eval_metric": "rmse", "max_depth": 3,
                  "eta": 0.05, "min_child_weight": 20, "subsample": 1.0,
                  "colsample_bytree": 1.0, "lambda": 10.0, "alpha": 0.0, "gamma": 0.0,
                  "tree_method": "hist", "max_bin": 256, "seed": 0, "nthread": 1, "verbosity": 0}
    model = xgb.train(parameters, train_matrix, num_boost_round=200)
    return model.predict(evaluation_matrix)


def _quantity(rule: InstrumentRule, entry_open: Decimal) -> Decimal:
    """100 USDTをquantity stepへ切り下げる。

    Args:
        rule: 取得時点の数量規則。
        entry_open: 未調整entry open。

    Returns:
        発注quantity。条件未達では0。
    """

    units = (NOTIONAL / entry_open / rule.quantity_step).to_integral_value(rounding=ROUND_DOWN)
    quantity = units * rule.quantity_step
    if quantity < rule.minimum_quantity or quantity * entry_open < rule.minimum_notional:
        return Decimal("0")
    return quantity


def evaluate_predictions(
    samples: list[Sample],
    predictions: np.ndarray,
    rules: dict[str, InstrumentRule],
    price_frames: dict[str, dict[str, pd.DataFrame]],
    funding_frames: dict[str, pd.DataFrame],
    *,
    cost_multiplier: Decimal = Decimal("1"),
    entry_threshold: float = PREDICTION_THRESHOLD,
) -> dict[str, object]:
    """予測top1を固定費用・Funding込みで評価する。

    Args:
        samples: 同時刻4銘柄が揃った評価標本。
        predictions: sample順の予測。
        rules: 数量丸め規則。
        price_frames: mark価格を含むframe。
        funding_frames: 実績Funding frame。
        cost_multiplier: fee、spread、slippage倍率。
        entry_threshold: top1予測が厳密に超える必要がある値。

    Returns:
        PnL、取引数、drawdown、監査行。

    Raises:
        ValueError: 長さ、時刻group、または会計入力が不正な場合。
    """

    if len(samples) != len(predictions):
        raise ValueError("sample and prediction lengths differ")
    by_time: dict[pd.Timestamp, list[tuple[Sample, float]]] = {}
    for sample, prediction in zip(samples, predictions, strict=True):
        by_time.setdefault(sample.decision_time, []).append((sample, float(prediction)))
    equity = INITIAL_EQUITY
    peak = INITIAL_EQUITY
    maximum_drawdown = Decimal("0")
    trades: list[dict[str, object]] = []
    for decision_time, candidates in sorted(by_time.items()):
        if len(candidates) != len(TRADED_SYMBOLS):
            raise ValueError("incomplete cross-sectional decision")
        sample, prediction = min(candidates, key=lambda item: (-item[1], item[0].symbol))
        if not math.isfinite(prediction) or prediction <= entry_threshold:
            continue
        quantity = _quantity(rules[sample.symbol], sample.entry_open)
        if quantity <= 0:
            raise ValueError("allocation rejection")
        adverse = (HALF_SPREAD + SLIPPAGE) * cost_multiplier
        entry_fill = sample.entry_open * (Decimal("1") + adverse)
        exit_fill = sample.exit_open * (Decimal("1") - adverse)
        fee_rate = TAKER_FEE * cost_multiplier
        fees = quantity * (entry_fill + exit_fill) * fee_rate
        funding_cash = Decimal("0")
        funding = funding_frames[sample.symbol]
        settlements = funding.loc[
            (funding.index > decision_time) & (funding.index < decision_time + HORIZON)
        ]
        for settlement_time, row in settlements.iterrows():
            mark_open = Decimal(str(price_frames[sample.symbol]["mark_price"].loc[settlement_time, "open"]))
            funding_cash -= quantity * mark_open * Decimal(str(row["funding_rate"]))
        gross_price_pnl = quantity * (sample.exit_open - sample.entry_open)
        spread_cost = quantity * (sample.entry_open + sample.exit_open) * HALF_SPREAD * cost_multiplier
        slippage_cost = quantity * (sample.entry_open + sample.exit_open) * SLIPPAGE * cost_multiplier
        pnl = gross_price_pnl + funding_cash - fees - spread_cost - slippage_cost
        equity += pnl
        peak = max(peak, equity)
        maximum_drawdown = min(maximum_drawdown, equity / peak - Decimal("1"))
        trades.append({"decision_time": decision_time.isoformat(),
                       "exit_time": (decision_time + HORIZON).isoformat(), "symbol": sample.symbol,
                       "prediction": prediction, "quantity": str(quantity), "funding_cash_flow": str(funding_cash),
                       "gross_price_pnl": str(gross_price_pnl), "fees": str(fees),
                       "spread_cost": str(spread_cost), "slippage_cost": str(slippage_cost),
                       "turnover": str(quantity * (entry_fill + exit_fill)),
                       "net_pnl": str(pnl), "equity": str(equity)})
    pnls = [Decimal(trade["net_pnl"]) for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    consecutive_losses = 0
    maximum_consecutive_losses = 0
    for pnl in pnls:
        consecutive_losses = consecutive_losses + 1 if pnl < 0 else 0
        maximum_consecutive_losses = max(maximum_consecutive_losses, consecutive_losses)
    symbol_net_pnl = {
        symbol: str(sum((Decimal(trade["net_pnl"]) for trade in trades if trade["symbol"] == symbol), Decimal("0")))
        for symbol in TRADED_SYMBOLS
    }
    return {
        "net_pnl": str(equity - INITIAL_EQUITY),
        "return": str(equity / INITIAL_EQUITY - Decimal("1")),
        "completed_round_trips": len(trades),
        "max_drawdown": str(maximum_drawdown),
        "gross_price_pnl": str(sum((Decimal(trade["gross_price_pnl"]) for trade in trades), Decimal("0"))),
        "funding_cash_flow": str(sum((Decimal(trade["funding_cash_flow"]) for trade in trades), Decimal("0"))),
        "fees": str(sum((Decimal(trade["fees"]) for trade in trades), Decimal("0"))),
        "spread_cost": str(sum((Decimal(trade["spread_cost"]) for trade in trades), Decimal("0"))),
        "slippage_cost": str(sum((Decimal(trade["slippage_cost"]) for trade in trades), Decimal("0"))),
        "turnover": str(sum((Decimal(trade["turnover"]) for trade in trades), Decimal("0"))),
        "win_rate": float(len(wins) / len(pnls)) if pnls else None,
        "average_win": str(sum(wins, Decimal("0")) / len(wins)) if wins else None,
        "average_loss": str(sum(losses, Decimal("0")) / len(losses)) if losses else None,
        "worst_trade": str(min(pnls)) if pnls else None,
        "maximum_consecutive_losses": maximum_consecutive_losses,
        "symbol_net_pnl": symbol_net_pnl,
        "trades": trades,
    }


def select_model(results: dict[str, dict[str, object]]) -> tuple[str | None, str]:
    """2025 developmentの事前登録規則で一つのmodelを選ぶ。

    Args:
        results: ridgeとxgboostの評価結果。

    Returns:
        選択modelまたはNoneと判定理由。

    Raises:
        ValueError: 必須modelがない場合。
    """

    if set(results) != {"ridge", "xgboost"}:
        raise ValueError("exactly ridge and xgboost results are required")
    rounded = {name: Decimal(str(value["net_pnl"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
               for name, value in results.items()}
    if max(rounded.values()) <= 0:
        return None, "both_models_nonpositive"
    if len(set(rounded.values())) == 1:
        return None, "rounded_net_pnl_tie"
    return max(rounded, key=rounded.__getitem__), "highest_development_net_pnl"


def _load_inputs(
    data_dir: Path,
    metadata_path: Path,
    *,
    value_cutoff: pd.Timestamp = SEALED_HOLDOUT_START,
) -> tuple[
    dict[str, dict[str, pd.DataFrame]], dict[str, pd.DataFrame], dict[str, InstrumentRule]
]:
    """hash検証後、封印境界より前だけを読み込む。

    Args:
        data_dir: DATA-2026-0006の保存先。
        metadata_path: 取得metadata。
        value_cutoff: 値を読み込むexclusive上限。development既定値はholdout開始。

    Returns:
        価格、Funding、数量規則。

    Raises:
        ValueError: snapshot、封印状態、hash、入力値が不正な場合。
    """

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("snapshot_id") != "DATA-2026-0006" or metadata.get("sealed_holdout", {}).get("content_opened") is not False:
        raise ValueError("invalid or opened data snapshot")
    prices: dict[str, dict[str, pd.DataFrame]] = {}
    funding_frames: dict[str, pd.DataFrame] = {}
    rules: dict[str, InstrumentRule] = {}
    records = {record["symbol"]: record for record in metadata["symbols"]}
    for symbol in (*TRADED_SYMBOLS, CONTEXT_SYMBOL):
        record = records[symbol]
        prices[symbol] = {}
        for source in ("trade", "mark_price", "index_price", "premium_index"):
            filename = f"{source.replace('_', '-')}-1h.csv"
            path = data_dir / symbol / filename
            if _sha256(path) != record["artifacts"][source]["sha256"]:
                raise ValueError(f"artifact hash mismatch: {symbol} {source}")
            prices[symbol][source] = _numeric_price_frame(_read_before_cutoff(path, value_cutoff), source)
        funding_path = data_dir / symbol / "funding-rate.csv"
        if _sha256(funding_path) != record["artifacts"]["funding"]["sha256"]:
            raise ValueError(f"artifact hash mismatch: {symbol} funding")
        funding = _read_before_cutoff(funding_path, value_cutoff)
        funding["funding_rate"] = pd.to_numeric(funding["funding_rate"], errors="raise")
        funding = funding.set_index("event_time").sort_index()
        if not funding.index.is_unique:
            raise ValueError(f"duplicate funding event time: {symbol}")
        funding_frames[symbol] = funding
        if symbol in TRADED_SYMBOLS:
            lot = record["instrument"]["lotSizeFilter"]
            rules[symbol] = InstrumentRule(Decimal(lot["qtyStep"]), Decimal(lot["minOrderQty"]),
                                           Decimal(lot["minNotionalValue"]))
    return prices, funding_frames, rules


def main() -> None:
    """trainと2025 developmentだけを比較し、holdout開封可否を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/EXP-2026-0054"))
    parser.add_argument("--metadata", type=Path, default=Path("var/exp-2026-0054-data.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/EXP-2026-0054-development"))
    args = parser.parse_args()
    prices, funding, rules = _load_inputs(args.data_dir, args.metadata)
    samples, exclusions = build_samples(prices, funding)
    train = [row for row in samples if row.decision_time + HORIZON < MODEL_SELECTION_START]
    development = [row for row in samples if row.decision_time >= MODEL_SELECTION_START]
    if not train or not development:
        raise ValueError("train or development samples are empty")
    ridge_model = fit_ridge(train)
    predictions = {
        "ridge": predict_ridge(ridge_model, development),
        "xgboost": fit_predict_xgboost(train, development),
    }
    results = {name: evaluate_predictions(development, values, rules, prices, funding)
               for name, values in predictions.items()}
    selected, reason = select_model(results)
    payload = {"experiment_id": EXPERIMENT_ID, "stage": "DEVELOPMENT_COMPLETED",
               "sealed_holdout_opened": False, "holdout_authorized": selected is not None,
               "selected_model": selected, "selection_reason": reason,
               "parameters": {"features": FEATURE_NAMES, "prediction_threshold": PREDICTION_THRESHOLD,
                              "ridge_alpha": RIDGE_ALPHA, "xgboost_version": __import__("xgboost").__version__,
                              "variants": 2, "random_seed": 0},
               "sample_counts": {"train": len(train), "development": len(development),
                                 "excluded_decision_times": len(exclusions)},
               "results": results}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("experiment_id", "stage", "sealed_holdout_opened",
                                                    "holdout_authorized", "selected_model", "selection_reason",
                                                    "sample_counts")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
