#!/usr/bin/env python3
"""EXP-2026-0012のDonchian退出とATR追随退出を年次比較する。"""

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
    prepare_atr_trailing_exit_signals,
    run_backtest,
    run_buy_and_hold,
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
    """約定履歴から往復損益と費用を再計算する。

    Args:
        trades: `run_backtest`が返すBUY/SELL履歴。

    Returns:
        往復取引統計、未決済状態、総手数料の辞書。

    Raises:
        ValueError: BUY/SELL順序が現物ロングの不変条件に反する場合。
    """

    fees = Decimal("0")
    pnls: list[Decimal] = []
    open_buy = None
    for row in trades.itertuples(index=False):
        fees += Decimal(str(row.fee))
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
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
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
        "total_fees": str(fees),
    }


def _evaluate_period(
    frame: pd.DataFrame,
    year: int,
    cost_model: CostModel,
    initial_cash: Decimal,
    mode: str,
) -> dict[str, object]:
    """一つの暦年で基準またはATR退出を独立資金評価する。

    Args:
        frame: 全履歴で状態を計算済みの日足データ。
        year: 評価対象のUTC暦年。
        cost_model: fee、spread、slippageの仮定。
        initial_cash: 年初にリセットする初期資金。
        mode: `baseline`または`atr`。

    Returns:
        損益、取引統計、退出イベント、成果物を含む辞書。

    Raises:
        ValueError: 指定年が空、またはmodeが未知の場合。
    """

    start = pd.Timestamp(f"{year}-01-01T00:00:00Z")
    end = pd.Timestamp(f"{year + 1}-01-01T00:00:00Z")
    period = frame[
        (frame["event_time"] >= start) & (frame["event_time"] < end)
    ].reset_index(drop=True).copy()
    if period.empty:
        raise ValueError(f"annual period is empty: {year}")
    days_flat_while_baseline_long = int(
        (
            (period["desired_atr_position"] == 0)
            & (period["desired_position"] == 1)
        ).sum()
    )
    if mode == "atr":
        period["desired_position"] = period["desired_atr_position"]
    elif mode != "baseline":
        raise ValueError(f"unknown evaluation mode: {mode}")
    equity, trades = run_backtest(period, cost_model, initial_cash)
    buy_and_hold = run_buy_and_hold(period, cost_model, initial_cash)
    return {
        "strategy": summarize_equity(equity),
        "buy_and_hold": summarize_equity(buy_and_hold),
        "trade_count": int(len(trades)),
        "trade_statistics": _summarize_round_trips(trades),
        "desired_position_at_start": int(period.iloc[0]["desired_position"]),
        "atr_exit_signals": int(period["atr_exit_signal"].sum()),
        "atr_exit_signals_on_interpolated_days": int(
            (period["atr_exit_signal"] & period[INTERPOLATED_COLUMN]).sum()
        ),
        "trades_on_interpolated_days": int(
            trades.get(INTERPOLATED_COLUMN, pd.Series(dtype=bool)).astype(bool).sum()
        ),
        "days_flat_while_baseline_long": days_flat_while_baseline_long,
        "artifacts": {"equity": equity, "trades": trades, "buy_and_hold": buy_and_hold},
    }


def _compare(baseline: dict[str, object], atr: dict[str, object]) -> dict[str, float]:
    """ATR退出と基準退出の主要指標差を計算する。

    Args:
        baseline: Donchian 20日安値退出の年次結果。
        atr: ATR追随退出の年次結果。

    Returns:
        CAGR差、最大DD改善幅、最終資産差を含む辞書。
    """

    base_metrics = baseline["strategy"]
    atr_metrics = atr["strategy"]
    return {
        "cagr_delta": atr_metrics["cagr"] - base_metrics["cagr"],
        "max_drawdown_improvement": atr_metrics["max_drawdown"]
        - base_metrics["max_drawdown"],
        "final_equity_delta": atr_metrics["final_equity"]
        - base_metrics["final_equity"],
    }


def _scorecard(periods: dict[str, dict[str, object]]) -> dict[str, object]:
    """年次比較を事前登録した候補・棄却指標へ集計する。

    Args:
        periods: 年をキーとするbaseline、atr、comparisonの結果。

    Returns:
        DD改善、合算資産維持率、CAGR、退出件数を含む辞書。
    """

    comparisons = [period["comparison"] for period in periods.values()]
    improvements = [item["max_drawdown_improvement"] for item in comparisons]
    baseline_final = sum(
        period["baseline"]["strategy"]["final_equity"] for period in periods.values()
    )
    atr_final = sum(
        period["atr"]["strategy"]["final_equity"] for period in periods.values()
    )
    return {
        "period_count": len(periods),
        "max_drawdown_improved_periods": sum(value > 0 for value in improvements),
        "median_max_drawdown_improvement": median(improvements),
        "baseline_aggregate_final_equity": baseline_final,
        "atr_aggregate_final_equity": atr_final,
        "aggregate_final_equity_retention": atr_final / baseline_final,
        "cagr_at_least_baseline_periods": sum(
            item["cagr_delta"] >= 0 for item in comparisons
        ),
        "atr_exit_signals": sum(
            period["atr"]["atr_exit_signals"] for period in periods.values()
        ),
    }


def _classify(scorecard: dict[str, object]) -> dict[str, object]:
    """事前登録基準から研究ステータスを決定する。

    Args:
        scorecard: `_scorecard`が返す年次集計。

    Returns:
        候補条件、棄却条件、研究・昇格ステータス。
    """

    candidate = {
        "max_drawdown_improved_at_least_3_years": scorecard["max_drawdown_improved_periods"] >= 3,
        "median_max_drawdown_improvement_at_least_3_points": scorecard[
            "median_max_drawdown_improvement"
        ]
        >= 0.03,
        "aggregate_final_equity_retention_at_least_90_percent": scorecard[
            "aggregate_final_equity_retention"
        ]
        >= 0.90,
        "cagr_at_least_baseline_in_at_least_2_years": scorecard[
            "cagr_at_least_baseline_periods"
        ]
        >= 2,
        "atr_exit_signals_at_least_3": scorecard["atr_exit_signals"] >= 3,
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
        "cagr_below_baseline_in_all_years": scorecard["cagr_at_least_baseline_periods"] == 0,
        "no_atr_exit_signals": scorecard["atr_exit_signals"] == 0,
    }
    status = (
        "PASSED_RETROSPECTIVE_VALIDATION"
        if all(candidate.values())
        else "REJECTED" if any(rejection.values()) else "INCONCLUSIVE"
    )
    return {
        "candidate_criteria": candidate,
        "rejection_criteria": rejection,
        "research_status": status,
        "promotion_status": "NOT_ELIGIBLE" if status == "REJECTED" else "NEEDS_FORWARD_EVIDENCE",
        "promotion_note": "既観測Global proxyの結果だけではpaper・shadow・liveへ昇格しない。",
    }


def main() -> None:
    """日足品質を確認し、基準退出とATR退出を費用感度評価する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=Path("data/processed/EXP-2026-0003"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/EXP-2026-0012"))
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
        raise ValueError("EXP-2026-0012 requires inherited synthetic-day markers")
    signals = prepare_atr_trailing_exit_signals(frame)
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
            baseline = _evaluate_period(signals, year, cost_model, args.initial_cash, "baseline")
            atr = _evaluate_period(signals, year, cost_model, args.initial_cash, "atr")
            baseline_artifacts = baseline.pop("artifacts")
            atr_artifacts = atr.pop("artifacts")
            if cost_name == "base":
                for prefix, artifacts in (("baseline", baseline_artifacts), ("atr", atr_artifacts)):
                    for name, artifact in artifacts.items():
                        artifact.to_csv(
                            args.output_dir
                            / f"{year}-{prefix}-{name.replace('_', '-')}.csv",
                            index=False,
                        )
            periods[str(year)] = {
                "baseline": baseline,
                "atr": atr,
                "comparison": _compare(baseline, atr),
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
    summary = {
        "experiment_id": "EXP-2026-0012",
        "evaluation_type": "retrospective ATR trailing exit diagnostic",
        "annual_windows": list(ANNUAL_WINDOWS),
        "strategy_freeze": {
            "entry_window": 55,
            "baseline_exit_window": 20,
            "regime_window": 200,
            "atr_window": 20,
            "atr_average": "simple moving average",
            "atr_multiplier": 3.0,
            "trigger": "close below ratcheted stop",
            "execution": "next-day open",
        },
        "data_quality": quality,
        "evaluations": evaluations,
        "classification": _classify(evaluations["base"]["scorecard"]),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
