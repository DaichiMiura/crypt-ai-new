#!/usr/bin/env python3
"""EXP-2026-0014をETH・SOL・XRPの共通期間で比較する。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from statistics import median
import sys

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.research import (  # noqa: E402
    INTERPOLATED_COLUMN,
    CostModel,
    inspect_daily_data,
    prepare_atr_trailing_exit_signals,
    run_backtest,
    run_buy_and_hold,
    summarize_equity,
)
from scripts.run_exp_2026_0012 import (  # noqa: E402
    _read_daily_file,
    _summarize_round_trips,
)


SYMBOLS = ("ETHUSDT", "SOLUSDT", "XRPUSDT")
ANNUAL_WINDOWS = tuple(range(2022, 2026))
EVALUATION_START = pd.Timestamp("2022-01-01T00:00:00Z")
EVALUATION_END = pd.Timestamp("2026-01-01T00:00:00Z")


def _evaluate_period(
    frame: pd.DataFrame,
    year: int,
    cost_model: CostModel,
    initial_cash: Decimal,
    mode: str,
) -> dict[str, object]:
    """一銘柄・一暦年をbaselineまたはATR版として評価する。

    Args:
        frame: 全履歴でシグナルを計算済みの日足。
        year: 評価対象UTC年。
        cost_model: fee、spread、slippageの仮定。
        initial_cash: 年初にリセットする資金。
        mode: `baseline`または`atr`。

    Returns:
        年次成績、取引統計、成果物を含む辞書。

    Raises:
        ValueError: 期間が空、またはmodeが不正な場合。
    """

    start = pd.Timestamp(f"{year}-01-01T00:00:00Z")
    end = pd.Timestamp(f"{year + 1}-01-01T00:00:00Z")
    period = frame[
        (frame["event_time"] >= start) & (frame["event_time"] < end)
    ].reset_index(drop=True).copy()
    if period.empty:
        raise ValueError(f"annual period is empty: {year}")
    if mode == "atr":
        period["desired_position"] = period["desired_atr_position"]
    elif mode != "baseline":
        raise ValueError(f"unknown mode: {mode}")
    equity, trades = run_backtest(period, cost_model, initial_cash)
    buy_and_hold = run_buy_and_hold(period, cost_model, initial_cash)
    return {
        "strategy": summarize_equity(equity),
        "buy_and_hold": summarize_equity(buy_and_hold),
        "trade_count": int(len(trades)),
        "trade_statistics": _summarize_round_trips(trades),
        "desired_position_at_start": int(period.iloc[0]["desired_position"]),
        "atr_exit_signals": int(period["atr_exit_signal"].sum()),
        "trades_on_interpolated_days": int(
            trades.get(INTERPOLATED_COLUMN, pd.Series(dtype=bool)).astype(bool).sum()
        ),
        "artifacts": {"equity": equity, "trades": trades, "buy_and_hold": buy_and_hold},
    }


def _compare(baseline: dict[str, object], atr: dict[str, object]) -> dict[str, float]:
    """ATR版とbaselineの年次主要指標差を計算する。

    Args:
        baseline: Donchian 20日安値exitの年次結果。
        atr: ATR trailing exitの年次結果。

    Returns:
        CAGR差、DD改善幅、最終資産差を含む辞書。
    """

    baseline_metrics = baseline["strategy"]
    atr_metrics = atr["strategy"]
    return {
        "cagr_delta": atr_metrics["cagr"] - baseline_metrics["cagr"],
        "max_drawdown_improvement": atr_metrics["max_drawdown"]
        - baseline_metrics["max_drawdown"],
        "final_equity_delta": atr_metrics["final_equity"]
        - baseline_metrics["final_equity"],
    }


def _symbol_scorecard(
    periods: dict[str, dict[str, object]], initial_cash: Decimal
) -> dict[str, object]:
    """一銘柄の年次比較を事前基準へ集計する。

    Args:
        periods: 年をキーとするbaseline、atr、comparison。
        initial_cash: 損失年の判定に使う初期資金。

    Returns:
        銘柄別候補条件と主要集計。
    """

    comparisons = [item["comparison"] for item in periods.values()]
    improvements = [item["max_drawdown_improvement"] for item in comparisons]
    cagr_deltas = [item["cagr_delta"] for item in comparisons]
    baseline_final = sum(
        period["baseline"]["strategy"]["final_equity"] for period in periods.values()
    )
    atr_final = sum(
        period["atr"]["strategy"]["final_equity"] for period in periods.values()
    )
    atr_exits = sum(period["atr"]["atr_exit_signals"] for period in periods.values())
    scorecard = {
        "period_count": len(periods),
        "max_drawdown_improved_periods": sum(value > 0 for value in improvements),
        "median_max_drawdown_improvement": median(improvements),
        "cagr_at_least_baseline_periods": sum(value >= 0 for value in cagr_deltas),
        "baseline_aggregate_final_equity": baseline_final,
        "atr_aggregate_final_equity": atr_final,
        "aggregate_final_equity_retention": atr_final / baseline_final,
        "atr_exit_signals": atr_exits,
        "atr_closed_round_trips": sum(
            period["atr"]["trade_statistics"]["closed_round_trips"]
            for period in periods.values()
        ),
        "baseline_loss_periods": sum(
            period["baseline"]["strategy"]["final_equity"] < float(initial_cash)
            for period in periods.values()
        ),
    }
    scorecard["candidate"] = {
        "max_drawdown_improved_at_least_2_years": scorecard[
            "max_drawdown_improved_periods"
        ]
        >= 2,
        "median_max_drawdown_improvement_positive": scorecard[
            "median_max_drawdown_improvement"
        ]
        > 0,
        "aggregate_final_equity_retention_at_least_90_percent": scorecard[
            "aggregate_final_equity_retention"
        ]
        >= 0.90,
        "atr_exit_signals_at_least_3": scorecard["atr_exit_signals"] >= 3,
    }
    scorecard["candidate_passed"] = all(scorecard["candidate"].values())
    scorecard["rejection"] = {
        "median_max_drawdown_improvement_non_positive": scorecard[
            "median_max_drawdown_improvement"
        ]
        <= 0,
        "aggregate_final_equity_retention_below_80_percent": scorecard[
            "aggregate_final_equity_retention"
        ]
        < 0.80,
        "atr_exit_signals_fewer_than_3": scorecard["atr_exit_signals"] < 3,
    }
    return scorecard


def _classify(symbol_scorecards: dict[str, dict[str, object]]) -> dict[str, object]:
    """3銘柄中の候補数と棄却数から一般化診断を決める。

    Args:
        symbol_scorecards: 銘柄をキーとする`_symbol_scorecard`結果。

    Returns:
        銘柄別判定、候補・棄却条件、研究・昇格ステータス。
    """

    candidate_symbols = [
        symbol
        for symbol, scorecard in symbol_scorecards.items()
        if scorecard["candidate_passed"]
    ]
    rejection_symbols = [
        symbol
        for symbol, scorecard in symbol_scorecards.items()
        if any(scorecard["rejection"].values())
    ]
    generalized_candidate = len(candidate_symbols) >= 2
    generalized_rejection = len(rejection_symbols) >= 2
    status = (
        "PASSED_RETROSPECTIVE_VALIDATION"
        if generalized_candidate
        else "REJECTED" if generalized_rejection else "INCONCLUSIVE"
    )
    return {
        "candidate_symbols": candidate_symbols,
        "rejection_symbols": rejection_symbols,
        "generalized_candidate": generalized_candidate,
        "generalized_rejection": generalized_rejection,
        "research_status": status,
        "promotion_status": "NEEDS_FORWARD_EVIDENCE",
        "promotion_note": "Global proxyの銘柄別診断だけではpaper銘柄追加やBinance Japan運用を承認しない。",
    }


def main() -> None:
    """3銘柄の日足品質を確認し、固定ATR戦略を費用感度比較する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0014")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0014")
    )
    parser.add_argument("--initial-cash", type=Decimal, default=Decimal("1000"))
    args = parser.parse_args()
    paths = {path.stem.split("-")[0]: path for path in args.data_dir.glob("*-1d.csv")}
    if set(paths) != set(SYMBOLS):
        raise ValueError(f"expected exactly {SYMBOLS}: {sorted(paths)}")
    frames: dict[str, pd.DataFrame] = {}
    quality: dict[str, dict[str, object]] = {}
    for symbol, path in paths.items():
        frame = _read_daily_file(path)
        quality[symbol] = inspect_daily_data(frame)
        if quality[symbol]["duplicate_count"] or quality[symbol]["missing_intervals"]:
            raise ValueError(f"refusing incomplete daily data: {symbol}: {quality[symbol]}")
        if frame.iloc[0]["event_time"] > EVALUATION_START - pd.Timedelta(days=200):
            raise ValueError(f"insufficient warm-up: {symbol}")
        if frame.iloc[-1]["event_time"] < EVALUATION_END - pd.Timedelta(days=1):
            raise ValueError(f"data does not reach evaluation end: {symbol}")
        if bool(frame[INTERPOLATED_COLUMN].any()):
            raise ValueError(f"EXP-2026-0014 does not allow interpolated rows: {symbol}")
        frames[symbol] = prepare_atr_trailing_exit_signals(frame)

    cost_cases = {
        "base": CostModel(Decimal("0.001"), Decimal("0.0005"), Decimal("0.0005")),
        "adverse": CostModel(Decimal("0.0015"), Decimal("0.0005"), Decimal("0.0005")),
        "stress": CostModel(Decimal("0.002"), Decimal("0.0005"), Decimal("0.0005")),
    }
    evaluations: dict[str, dict[str, object]] = {}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for cost_name, cost_model in cost_cases.items():
        symbol_results: dict[str, dict[str, object]] = {}
        for symbol, frame in frames.items():
            periods: dict[str, dict[str, object]] = {}
            for year in ANNUAL_WINDOWS:
                baseline = _evaluate_period(
                    frame, year, cost_model, args.initial_cash, "baseline"
                )
                atr = _evaluate_period(frame, year, cost_model, args.initial_cash, "atr")
                baseline_artifacts = baseline.pop("artifacts")
                atr_artifacts = atr.pop("artifacts")
                if cost_name == "base":
                    symbol_dir = args.output_dir / symbol
                    symbol_dir.mkdir(parents=True, exist_ok=True)
                    for prefix, artifacts in (
                        ("baseline", baseline_artifacts),
                        ("atr", atr_artifacts),
                    ):
                        for name, artifact in artifacts.items():
                            artifact.to_csv(
                                symbol_dir
                                / f"{year}-{prefix}-{name.replace('_', '-')}.csv",
                                index=False,
                            )
                periods[str(year)] = {
                    "baseline": baseline,
                    "atr": atr,
                    "comparison": _compare(baseline, atr),
                }
            symbol_results[symbol] = {
                "periods": periods,
                "scorecard": _symbol_scorecard(periods, args.initial_cash),
            }
        evaluations[cost_name] = {
            "cost_model": {
                "fee_rate": str(cost_model.fee_rate),
                "round_trip_spread": str(cost_model.round_trip_spread),
                "slippage_per_fill": str(cost_model.slippage_per_fill),
            },
            "symbols": symbol_results,
        }

    base_scorecards = {
        symbol: result["scorecard"]
        for symbol, result in evaluations["base"]["symbols"].items()
    }
    summary = {
        "experiment_id": "EXP-2026-0014",
        "evaluation_type": "cross-asset retrospective generalization diagnostic",
        "symbols": list(SYMBOLS),
        "annual_windows": list(ANNUAL_WINDOWS),
        "evaluation_window": {
            "start_utc": EVALUATION_START.isoformat(),
            "end_utc_exclusive": EVALUATION_END.isoformat(),
        },
        "strategy_freeze": {
            "entry_window": 55,
            "baseline_exit_window": 20,
            "regime_window": 200,
            "atr_window": 20,
            "atr_average": "simple moving average",
            "atr_multiplier": 3.0,
            "execution": "next-day open",
        },
        "data_quality": quality,
        "evaluations": evaluations,
        "classification": _classify(base_scorecards),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
