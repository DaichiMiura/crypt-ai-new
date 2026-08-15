#!/usr/bin/env python3
"""EXP-2026-0006の固定戦略を2021〜2025年の年次区間で比較する。"""

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
    prepare_donchian_bollinger_exit_signals,
    prepare_donchian_signals,
    run_backtest,
    run_buy_and_hold,
    summarize_equity,
)


ANNUAL_WINDOWS = tuple(range(2021, 2026))


def _read_daily_file(path: Path) -> pd.DataFrame:
    """集約済み日足CSVを読み込み、時刻と数値の型を正規化する。

    Args:
        path: `build_exp_2026_0003_dataset.py`が作成した日足CSV。

    Returns:
        固定戦略の計算に使える日足データ。
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
    """約定履歴から往復損益、期待値、費用を再計算する。

    Args:
        trades: `run_backtest`が返すBUY/SELLの約定履歴。

    Returns:
        往復取引数、未決済状態、期待値、手数料を含む辞書。

    Raises:
        ValueError: BUY/SELLの順序が現物ロングの不変条件に反する場合。
    """

    total_fees = Decimal("0")
    round_trip_pnls: list[Decimal] = []
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
            round_trip_pnls.append(sell_value - buy_cost)
            open_buy = None
        else:
            raise ValueError(f"unknown trade side: {row.side}")

    closed_count = len(round_trip_pnls)
    return {
        "closed_round_trips": closed_count,
        "open_position_at_end": open_buy is not None,
        "expectancy_per_closed_trade": (
            str(sum(round_trip_pnls, Decimal("0")) / Decimal(closed_count))
            if closed_count
            else None
        ),
        "total_fees": str(total_fees),
    }


def _evaluate_period(
    frame: pd.DataFrame,
    year: int,
    cost_model: CostModel,
    initial_cash: Decimal,
) -> dict[str, object]:
    """一つの暦年を独立資金でバックテストする。

    Args:
        frame: 全履歴で状態計算済みの戦略日足データ。
        year: 評価するUTC暦年。
        cost_model: fee、spread、slippageの仮定。
        initial_cash: 年初にリセットするpaper現金残高。

    Returns:
        年次損益、取引統計、合成日足影響、成果物を含む辞書。

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
        "artifacts": {
            "equity": equity,
            "trades": trades,
            "buy_and_hold": buy_and_hold,
        },
    }


def _compare_period(
    base: dict[str, object],
    overlay: dict[str, object],
) -> dict[str, float]:
    """一つの暦年でoverlayとDonchian単独の差を計算する。

    Args:
        base: Donchian 55/20単独の年次結果。
        overlay: Donchian entry＋Bollinger exitの年次結果。

    Returns:
        CAGR差、最大DD改善幅、最終資産差を含む辞書。
    """

    base_metrics = base["strategy"]
    overlay_metrics = overlay["strategy"]
    return {
        "cagr_delta": overlay_metrics["cagr"] - base_metrics["cagr"],
        "max_drawdown_improvement": overlay_metrics["max_drawdown"]
        - base_metrics["max_drawdown"],
        "final_equity_delta": overlay_metrics["final_equity"]
        - base_metrics["final_equity"],
    }


def _scorecard(periods: dict[str, dict[str, object]]) -> dict[str, object]:
    """年次比較から事前登録した安定性スコアを集計する。

    Args:
        periods: 年をキーとするbase・overlay・comparisonの評価結果。

    Returns:
        DD改善年数、CAGR優位年数、中央値、合計往復数を含む辞書。
    """

    comparisons = [period["comparison"] for period in periods.values()]
    overlay_results = [period["overlay"] for period in periods.values()]
    dd_improvements = [item["max_drawdown_improvement"] for item in comparisons]
    cagr_deltas = [item["cagr_delta"] for item in comparisons]
    overlay_cagrs = [item["strategy"]["cagr"] for item in overlay_results]
    total_round_trips = sum(
        item["trade_statistics"]["closed_round_trips"] for item in overlay_results
    )
    return {
        "period_count": len(periods),
        "max_drawdown_improved_periods": sum(value > 0 for value in dd_improvements),
        "median_max_drawdown_improvement": median(dd_improvements),
        "cagr_at_least_base_periods": sum(value >= 0 for value in cagr_deltas),
        "positive_overlay_cagr_periods": sum(value > 0 for value in overlay_cagrs),
        "overlay_closed_round_trips": total_round_trips,
    }


def main() -> None:
    """固定戦略を年次区間で評価し、費用感度と成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0003")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0006")
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
        raise ValueError("EXP-2026-0006 requires inherited synthetic-day markers")

    base_frame = prepare_donchian_signals(frame, entry_window=55, exit_window=20)
    overlay_frame = prepare_donchian_bollinger_exit_signals(
        frame, entry_window=55, band_window=20, std_multiplier=2.0
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
            overlay_result = _evaluate_period(
                overlay_frame, year, cost_model, args.initial_cash
            )
            base_artifacts = base_result.pop("artifacts")
            overlay_artifacts = overlay_result.pop("artifacts")
            if cost_name == "base":
                base_artifacts["equity"].to_csv(
                    args.output_dir / f"{year}-base-equity.csv", index=False
                )
                base_artifacts["trades"].to_csv(
                    args.output_dir / f"{year}-base-trades.csv", index=False
                )
                overlay_artifacts["equity"].to_csv(
                    args.output_dir / f"{year}-overlay-equity.csv", index=False
                )
                overlay_artifacts["trades"].to_csv(
                    args.output_dir / f"{year}-overlay-trades.csv", index=False
                )
                base_artifacts["buy_and_hold"].to_csv(
                    args.output_dir / f"{year}-buy-and-hold-equity.csv", index=False
                )
            periods[str(year)] = {
                "base": base_result,
                "overlay": overlay_result,
                "comparison": _compare_period(base_result, overlay_result),
            }
        evaluations[cost_name] = {
            "periods": periods,
            "scorecard": _scorecard(periods),
        }
    summary = {
        "experiment_id": "EXP-2026-0006",
        "evaluation_type": "retrospective temporal stability diagnostic",
        "annual_windows": list(ANNUAL_WINDOWS),
        "data_quality": quality,
        "evaluations": evaluations,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
