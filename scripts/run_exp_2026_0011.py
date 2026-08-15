#!/usr/bin/env python3
"""EXP-2026-0011のfull-sizeとentry時ボラティリティ縮小を年次比較する。"""

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
    prepare_volatility_scaled_regime_signals,
    run_backtest,
    run_buy_and_hold,
    run_fractional_entry_backtest,
    summarize_equity,
)


ANNUAL_WINDOWS = tuple(range(2021, 2026))


def _read_daily_file(path: Path) -> pd.DataFrame:
    """日足CSVを読み込み、時刻・価格・補間フラグを正規化する。

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
    """約定履歴から往復損益、費用、entry投資比率を再計算する。

    Args:
        trades: full-sizeまたはfractional backtestのBUY/SELL履歴。

    Returns:
        往復取引統計、未決済状態、総手数料、entry投資比率の辞書。

    Raises:
        ValueError: BUY/SELLの順序が現物ロングの不変条件に反する場合。
    """

    total_fees = Decimal("0")
    pnls: list[Decimal] = []
    entry_exposures: list[float] = []
    open_buy = None
    for row in trades.itertuples(index=False):
        total_fees += Decimal(str(row.fee))
        if row.side == "BUY":
            if open_buy is not None:
                raise ValueError("trade history contains consecutive BUY fills")
            open_buy = row
            entry_exposures.append(float(getattr(row, "target_exposure", 1.0)))
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
        "entry_exposures": entry_exposures,
    }


def _evaluate_period(
    frame: pd.DataFrame,
    year: int,
    cost_model: CostModel,
    initial_cash: Decimal,
    mode: str,
) -> dict[str, object]:
    """一つの暦年でfull-sizeまたはscaledを独立資金評価する。

    Args:
        frame: 全履歴でシグナルと投資比率を計算済みの日足データ。
        year: 評価対象のUTC暦年。
        cost_model: fee、spread、slippageの固定仮定。
        initial_cash: 年初にリセットするpaper初期資金。
        mode: `full`または`scaled`。

    Returns:
        損益、取引統計、合成日足影響、成果物を含む辞書。

    Raises:
        ValueError: 指定年が空、または未知のmodeの場合。
    """

    start = pd.Timestamp(f"{year}-01-01T00:00:00Z")
    end = pd.Timestamp(f"{year + 1}-01-01T00:00:00Z")
    period = frame[
        (frame["event_time"] >= start) & (frame["event_time"] < end)
    ].reset_index(drop=True)
    if period.empty:
        raise ValueError(f"annual period is empty: {year}")
    if mode == "full":
        equity, trades = run_backtest(period, cost_model, initial_cash)
    elif mode == "scaled":
        equity, trades = run_fractional_entry_backtest(
            period, cost_model, initial_cash
        )
    else:
        raise ValueError(f"unknown evaluation mode: {mode}")
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
        "desired_exposure_at_start": float(period.iloc[0]["desired_exposure"]),
        "trades_on_interpolated_days": synthetic_trades,
        "artifacts": {"equity": equity, "trades": trades, "buy_and_hold": buy_and_hold},
    }


def _compare(full: dict[str, object], scaled: dict[str, object]) -> dict[str, float]:
    """scaledとfull-sizeの主要指標差を計算する。

    Args:
        full: full-sizeの年次結果。
        scaled: volatility-scaledの年次結果。

    Returns:
        CAGR差、最大DD改善幅、最終資産差を含む辞書。
    """

    full_metrics = full["strategy"]
    scaled_metrics = scaled["strategy"]
    return {
        "cagr_delta": scaled_metrics["cagr"] - full_metrics["cagr"],
        "max_drawdown_improvement": scaled_metrics["max_drawdown"]
        - full_metrics["max_drawdown"],
        "final_equity_delta": scaled_metrics["final_equity"]
        - full_metrics["final_equity"],
    }


def _scorecard(
    periods: dict[str, dict[str, object]], initial_cash: Decimal
) -> dict[str, object]:
    """年次比較から事前登録した損失抑制と収益維持の基準を集計する。

    Args:
        periods: 年をキーとするfull、scaled、comparisonの結果。
        initial_cash: 各年の損失判定に使う初期資金。

    Returns:
        DD改善、合算資産維持率、損失年、縮小entry件数を含む辞書。
    """

    comparisons = [period["comparison"] for period in periods.values()]
    dd_improvements = [item["max_drawdown_improvement"] for item in comparisons]
    full_final = sum(
        period["full"]["strategy"]["final_equity"] for period in periods.values()
    )
    scaled_final = sum(
        period["scaled"]["strategy"]["final_equity"] for period in periods.values()
    )
    initial = float(initial_cash)
    loss_periods = [
        period
        for period in periods.values()
        if period["full"]["strategy"]["final_equity"] < initial
    ]
    all_entry_exposures = [
        exposure
        for period in periods.values()
        for exposure in period["scaled"]["trade_statistics"]["entry_exposures"]
    ]
    return {
        "period_count": len(periods),
        "max_drawdown_improved_periods": sum(value > 0 for value in dd_improvements),
        "median_max_drawdown_improvement": median(dd_improvements),
        "full_aggregate_final_equity": full_final,
        "scaled_aggregate_final_equity": scaled_final,
        "aggregate_final_equity_retention": scaled_final / full_final,
        "full_loss_periods": len(loss_periods),
        "scaled_worse_in_full_loss_periods": sum(
            period["scaled"]["strategy"]["final_equity"]
            < period["full"]["strategy"]["final_equity"]
            for period in loss_periods
        ),
        "scaled_entries": len(all_entry_exposures),
        "scaled_entries_below_full": sum(value < 1.0 for value in all_entry_exposures),
        "median_entry_exposure": (
            median(all_entry_exposures) if all_entry_exposures else None
        ),
        "minimum_entry_exposure": (
            min(all_entry_exposures) if all_entry_exposures else None
        ),
        "maximum_entry_exposure": (
            max(all_entry_exposures) if all_entry_exposures else None
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
        "median_max_drawdown_improvement_at_least_3_points": scorecard[
            "median_max_drawdown_improvement"
        ]
        >= 0.03,
        "aggregate_final_equity_retention_at_least_90_percent": scorecard[
            "aggregate_final_equity_retention"
        ]
        >= 0.90,
        "no_worse_full_loss_period": scorecard[
            "scaled_worse_in_full_loss_periods"
        ]
        == 0,
        "at_least_5_reduced_entries": scorecard["scaled_entries_below_full"] >= 5,
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
        "aggregate_final_equity_retention_below_80_percent": scorecard[
            "aggregate_final_equity_retention"
        ]
        < 0.80,
        "scaled_worse_in_a_full_loss_period": scorecard[
            "scaled_worse_in_full_loss_periods"
        ]
        > 0,
        "no_reduced_entries": scorecard["scaled_entries_below_full"] == 0,
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
        "promotion_note": "既観測Global proxyの結果だけではBinance Japanのpaper・shadow・liveを承認しない。",
    }


def main() -> None:
    """日足品質を確認し、full-sizeとentry時volatility sizingを評価する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0003")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0011")
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
        raise ValueError("EXP-2026-0011 requires inherited synthetic-day markers")

    signals = prepare_volatility_scaled_regime_signals(
        frame,
        entry_window=55,
        exit_window=20,
        regime_window=200,
        volatility_window=20,
        target_annual_volatility=0.40,
    )
    cost_cases = {
        "base": CostModel(Decimal("0.001"), Decimal("0.0005"), Decimal("0.0005")),
        "adverse": CostModel(
            Decimal("0.0015"), Decimal("0.0005"), Decimal("0.0005")
        ),
        "stress": CostModel(Decimal("0.002"), Decimal("0.0005"), Decimal("0.0005")),
    }
    evaluations: dict[str, dict[str, object]] = {}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for cost_name, cost_model in cost_cases.items():
        periods: dict[str, dict[str, object]] = {}
        for year in ANNUAL_WINDOWS:
            full_result = _evaluate_period(
                signals, year, cost_model, args.initial_cash, "full"
            )
            scaled_result = _evaluate_period(
                signals, year, cost_model, args.initial_cash, "scaled"
            )
            full_artifacts = full_result.pop("artifacts")
            scaled_artifacts = scaled_result.pop("artifacts")
            if cost_name == "base":
                for prefix, artifacts in (
                    ("full", full_artifacts),
                    ("scaled", scaled_artifacts),
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
                "full": full_result,
                "scaled": scaled_result,
                "comparison": _compare(full_result, scaled_result),
            }
        evaluations[cost_name] = {
            "cost_model": {
                "fee_rate": str(cost_model.fee_rate),
                "round_trip_spread": str(cost_model.round_trip_spread),
                "slippage_per_fill": str(cost_model.slippage_per_fill),
            },
            "periods": periods,
            "scorecard": _scorecard(periods, args.initial_cash),
        }

    classification = _classify(evaluations["base"]["scorecard"])
    summary = {
        "experiment_id": "EXP-2026-0011",
        "evaluation_type": "retrospective entry-time volatility sizing diagnostic",
        "annual_windows": list(ANNUAL_WINDOWS),
        "strategy_freeze": {
            "entry_window": 55,
            "exit_window": 20,
            "regime_window": 200,
            "volatility_window": 20,
            "target_annual_volatility": 0.40,
            "maximum_exposure": 1.0,
            "rebalance_while_holding": False,
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
