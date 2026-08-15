#!/usr/bin/env python3
"""EXP-2026-0008の長期SMAレジームフィルターを年次比較する。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from statistics import median
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypt_ai.research import (  # noqa: E402
    INTERPOLATED_COLUMN,
    CostModel,
    inspect_daily_data,
    prepare_donchian_regime_filter_signals,
    prepare_donchian_signals,
    run_backtest,
    run_buy_and_hold,
    summarize_equity,
)


ANNUAL_WINDOWS = tuple(range(2021, 2026))


def _read_daily_file(path: Path) -> pd.DataFrame:
    """日足CSVを読み込み、時刻・価格・合成行フラグを正規化する。

    Args:
        path: 日足CSVへのパス。

    Returns:
        戦略計算に使える日足データ。
    """

    frame = pd.read_csv(path)
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame[INTERPOLATED_COLUMN] = (
        frame[INTERPOLATED_COLUMN]
        .fillna(False)
        .astype(str)
        .str.lower()
        .isin(["1", "true", "yes"])
    )
    return frame


def _summarize_round_trips(trades: pd.DataFrame) -> dict[str, object]:
    """約定履歴から往復取引数、勝率、期待値、費用を再計算する。

    Args:
        trades: `run_backtest`が返すBUY/SELLの約定履歴。

    Returns:
        往復取引の統計、未決済状態、総手数料を含む辞書。

    Raises:
        ValueError: BUY/SELLの順序が現物ロングの不変条件に反する場合。
    """

    total_fees = Decimal("0")
    pnls: list[Decimal] = []
    open_buy = None
    for row in trades.itertuples(index=False):
        total_fees += Decimal(str(row.fee))
        if row.side == "BUY":
            if open_buy is not None:
                raise ValueError("trade history contains consecutive BUY fills")
            open_buy = row
        elif row.side == "SELL":
            if open_buy is None:
                raise ValueError("trade history contains SELL without a BUY")
            buy_cost = Decimal(str(open_buy.quantity)) * Decimal(
                str(open_buy.execution_price)
            ) + Decimal(str(open_buy.fee))
            sell_value = Decimal(str(row.quantity)) * Decimal(
                str(row.execution_price)
            ) - Decimal(str(row.fee))
            pnls.append(sell_value - buy_cost)
            open_buy = None
        else:
            raise ValueError(f"unknown trade side: {row.side}")
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    count = len(pnls)
    return {
        "closed_round_trips": count,
        "open_position_at_end": open_buy is not None,
        "win_rate": len(wins) / count if count else None,
        "average_win": str(sum(wins, Decimal("0")) / len(wins)) if wins else None,
        "average_loss": (
            str(sum(losses, Decimal("0")) / len(losses)) if losses else None
        ),
        "expectancy_per_closed_trade": (
            str(sum(pnls, Decimal("0")) / count) if count else None
        ),
        "total_fees": str(total_fees),
    }


def _evaluate_period(
    frame: pd.DataFrame, year: int, cost_model: CostModel, initial_cash: Decimal
) -> dict[str, object]:
    """一つの暦年を独立資金で評価する。

    Args:
        frame: 全履歴でシグナル計算済みの日足データ。
        year: 評価対象のUTC暦年。
        cost_model: fee、spread、slippageの固定仮定。
        initial_cash: 年初にリセットするpaper初期資金。

    Returns:
        損益、取引統計、合成日足影響、成果物を含む辞書。

    Raises:
        ValueError: 指定年の日足が空の場合。
    """

    start = pd.Timestamp(f"{year}-01-01T00:00:00Z")
    end = pd.Timestamp(f"{year + 1}-01-01T00:00:00Z")
    period = frame[
        (frame["event_time"] >= start) & (frame["event_time"] < end)
    ].reset_index(drop=True)
    if period.empty:
        raise ValueError(f"annual period is empty: {year}")
    equity, trades = run_backtest(period, cost_model, initial_cash)
    buy_and_hold = run_buy_and_hold(period, cost_model, initial_cash)
    synthetic_trades = int(
        trades.get(INTERPOLATED_COLUMN, pd.Series(dtype=bool)).astype(bool).sum()
    )
    return {
        "strategy": summarize_equity(equity),
        "buy_and_hold": summarize_equity(buy_and_hold),
        "trade_count": int(len(trades)),
        "trade_statistics": _summarize_round_trips(trades),
        "desired_position_at_start": int(period.iloc[0]["desired_position"]),
        "trades_on_interpolated_days": synthetic_trades,
        "artifacts": {"equity": equity, "trades": trades, "buy_and_hold": buy_and_hold},
    }


def _compare(base: dict[str, object], filtered: dict[str, object]) -> dict[str, float]:
    """filteredとbaseの年次主要指標の差を計算する。

    Args:
        base: Donchian単独の年次結果。
        filtered: SMAフィルター付きの年次結果。

    Returns:
        CAGR差、最大DD改善幅、最終資産差を含む辞書。
    """

    base_metrics = base["strategy"]
    filtered_metrics = filtered["strategy"]
    return {
        "cagr_delta": filtered_metrics["cagr"] - base_metrics["cagr"],
        "max_drawdown_improvement": filtered_metrics["max_drawdown"]
        - base_metrics["max_drawdown"],
        "final_equity_delta": filtered_metrics["final_equity"]
        - base_metrics["final_equity"],
    }


def _scorecard(periods: dict[str, dict[str, object]]) -> dict[str, object]:
    """年次比較から事前登録した安定性スコアを集計する。

    Args:
        periods: 年をキーとするbase、filtered、comparisonの評価結果。

    Returns:
        DD改善年数、中央値、CAGR優位年数、往復取引数を含む辞書。
    """

    comparisons = [period["comparison"] for period in periods.values()]
    filtered_results = [period["filtered"] for period in periods.values()]
    dd_improvements = [item["max_drawdown_improvement"] for item in comparisons]
    cagr_deltas = [item["cagr_delta"] for item in comparisons]
    return {
        "period_count": len(periods),
        "max_drawdown_improved_periods": sum(value > 0 for value in dd_improvements),
        "median_max_drawdown_improvement": median(dd_improvements),
        "cagr_at_least_base_periods": sum(value >= 0 for value in cagr_deltas),
        "filtered_closed_round_trips": sum(
            item["trade_statistics"]["closed_round_trips"] for item in filtered_results
        ),
    }


def _classify(scorecard: dict[str, object]) -> dict[str, object]:
    """事前登録した候補・棄却条件から暫定ステータスを作る。

    Args:
        scorecard: `_scorecard`が返す年次集計。

    Returns:
        research status、promotion status、各条件の判定を含む辞書。
    """

    candidate = {
        "max_drawdown_improved_at_least_3_years": scorecard[
            "max_drawdown_improved_periods"
        ]
        >= 3,
        "median_max_drawdown_improvement_positive": scorecard[
            "median_max_drawdown_improvement"
        ]
        > 0,
        "cagr_at_least_base_in_at_least_2_years": scorecard[
            "cagr_at_least_base_periods"
        ]
        >= 2,
        "closed_round_trips_at_least_5": scorecard["filtered_closed_round_trips"]
        >= 5,
    }
    rejection = {
        "max_drawdown_improved_in_fewer_than_2_years": scorecard[
            "max_drawdown_improved_periods"
        ]
        < 2,
        "median_max_drawdown_improvement_non_positive": scorecard[
            "median_max_drawdown_improvement"
        ]
        <= 0,
        "cagr_below_base_in_all_years": scorecard["cagr_at_least_base_periods"] == 0,
    }
    if all(candidate.values()):
        research_status = "PASSED_RETROSPECTIVE_VALIDATION"
    elif any(rejection.values()):
        research_status = "REJECTED"
    else:
        research_status = "INCONCLUSIVE"
    return {
        "candidate_criteria": candidate,
        "rejection_criteria": rejection,
        "research_status": research_status,
        "promotion_status": (
            "NOT_ELIGIBLE"
            if research_status == "REJECTED"
            else "NEEDS_FORWARD_EVIDENCE"
        ),
        "promotion_note": "過去proxyの結果だけではBinance Japanのpaper・shadow・liveを承認しない。",
    }


def main() -> None:
    """日足品質を確認し、baseとSMAフィルターを費用感度評価する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0003")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0008")
    )
    parser.add_argument("--initial-cash", type=Decimal, default=Decimal("1000"))
    args = parser.parse_args()

    paths = sorted(args.data_dir.glob("*.csv"))
    if len(paths) != 1:
        raise ValueError(f"expected exactly one daily CSV: {paths}")
    frame = _read_daily_file(paths[0])
    quality = inspect_daily_data(frame)
    if quality["duplicate_count"] or quality["missing_intervals"]:
        raise ValueError(f"refusing to evaluate incomplete daily data: {quality}")
    if not quality["interpolated_rows"]:
        raise ValueError("EXP-2026-0008 requires inherited synthetic-day markers")

    base_frame = prepare_donchian_signals(frame, entry_window=55, exit_window=20)
    filtered_frame = prepare_donchian_regime_filter_signals(
        frame, entry_window=55, exit_window=20, regime_window=200
    )
    cost_cases = {
        "base": CostModel(Decimal("0.001"), Decimal("0.0005"), Decimal("0.0005")),
        "adverse": CostModel(Decimal("0.0015"), Decimal("0.0005"), Decimal("0.0005")),
        "stress": CostModel(Decimal("0.002"), Decimal("0.0005"), Decimal("0.0005")),
    }
    evaluations: dict[str, dict[str, object]] = {}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for cost_name, cost_model in cost_cases.items():
        periods: dict[str, dict[str, object]] = {}
        for year in ANNUAL_WINDOWS:
            base_result = _evaluate_period(
                base_frame, year, cost_model, args.initial_cash
            )
            filtered_result = _evaluate_period(
                filtered_frame, year, cost_model, args.initial_cash
            )
            base_artifacts = base_result.pop("artifacts")
            filtered_artifacts = filtered_result.pop("artifacts")
            if cost_name == "base":
                for prefix, artifacts in (
                    ("base", base_artifacts),
                    ("filtered", filtered_artifacts),
                ):
                    artifacts["equity"].to_csv(
                        args.output_dir / f"{year}-{prefix}-equity.csv", index=False
                    )
                    artifacts["trades"].to_csv(
                        args.output_dir / f"{year}-{prefix}-trades.csv", index=False
                    )
                    artifacts["buy_and_hold"].to_csv(
                        args.output_dir
                        / f"{year}-{prefix}-buy-and-hold-equity.csv",
                        index=False,
                    )
            periods[str(year)] = {
                "base": base_result,
                "filtered": filtered_result,
                "comparison": _compare(base_result, filtered_result),
            }
        evaluations[cost_name] = {
            "cost_model": {
                "fee_rate": str(cost_model.fee_rate),
                "round_trip_spread": str(cost_model.round_trip_spread),
                "slippage_per_fill": str(cost_model.slippage_per_fill),
            },
            "periods": periods,
            "scorecard": _scorecard(periods),
        }

    classification = _classify(evaluations["base"]["scorecard"])
    summary = {
        "experiment_id": "EXP-2026-0008",
        "evaluation_type": "retrospective temporal stability diagnostic",
        "annual_windows": list(ANNUAL_WINDOWS),
        "strategy_freeze": {
            "entry_window": 55,
            "exit_window": 20,
            "regime_window": 200,
            "regime_rule": "new entry only when close > SMA200",
            "execution": "next-day open",
        },
        "data_quality": quality,
        "evaluations": evaluations,
        "classification": classification,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
