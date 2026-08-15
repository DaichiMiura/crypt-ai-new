#!/usr/bin/env python3
"""EXP-2026-0010のlong、short、combined価格proxyを年次比較する。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
import sys
from statistics import median

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.research import (  # noqa: E402
    INTERPOLATED_COLUMN,
    CostModel,
    inspect_daily_data,
    prepare_donchian_long_short_regime_signals,
    run_buy_and_hold,
    run_long_short_backtest,
    summarize_equity,
)


ANNUAL_WINDOWS = tuple(range(2021, 2026))


def _read_daily_file(path: Path) -> pd.DataFrame:
    """日足CSVを読み込み、時刻・価格・合成行フラグを正規化する。

    Args:
        path: 日足CSVへのパス。

    Returns:
        long/short価格proxy計算に使える日足データ。
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
    """long/short約定履歴から往復損益、勝率、期待値、費用を再計算する。

    Args:
        trades: `run_long_short_backtest`が返す約定履歴。

    Returns:
        closed round trips、勝率、期待値、未決済状態、総手数料を含む辞書。

    Raises:
        ValueError: long/shortのentryとexitの順序が不正な場合。
    """

    total_fees = Decimal("0")
    pnls: list[Decimal] = []
    open_trade = None
    for row in trades.itertuples(index=False):
        total_fees += Decimal(str(row.fee))
        if row.side in {"BUY", "SELL_SHORT"}:
            if open_trade is not None:
                raise ValueError("trade history contains consecutive entries")
            open_trade = row
        elif row.side in {"SELL", "BUY_TO_COVER"}:
            if open_trade is None:
                raise ValueError("trade history contains exit without entry")
            quantity = Decimal(str(open_trade.quantity))
            entry_price = Decimal(str(open_trade.execution_price))
            entry_fee = Decimal(str(open_trade.fee))
            exit_price = Decimal(str(row.execution_price))
            exit_fee = Decimal(str(row.fee))
            if open_trade.side == "BUY" and row.side == "SELL":
                pnl = quantity * exit_price - exit_fee - quantity * entry_price - entry_fee
            elif open_trade.side == "SELL_SHORT" and row.side == "BUY_TO_COVER":
                pnl = quantity * entry_price - entry_fee - quantity * exit_price - exit_fee
            else:
                raise ValueError("trade history mixes long and short sides")
            pnls.append(pnl)
            open_trade = None
        else:
            raise ValueError(f"unknown trade side: {row.side}")
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    count = len(pnls)
    return {
        "closed_round_trips": count,
        "open_position_at_end": open_trade is not None,
        "open_side_at_end": open_trade.side if open_trade is not None else None,
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
    frame: pd.DataFrame,
    desired_column: str,
    year: int,
    cost_model: CostModel,
    initial_cash: Decimal,
) -> dict[str, object]:
    """一つの暦年で指定ポジション系列を独立資金評価する。

    Args:
        frame: long/shortシグナル列を含む日足データ。
        desired_column: `-1/0/1`の次日position列名。
        year: 評価対象のUTC暦年。
        cost_model: fee、spread、slippageの固定仮定。
        initial_cash: 年初にリセットするpaper初期資金。

    Returns:
        損益、取引統計、合成日足影響、成果物を含む辞書。

    Raises:
        ValueError: 指定年の日足またはposition列が不足する場合。
    """

    if desired_column not in frame:
        raise ValueError(f"missing desired position column: {desired_column}")
    start = pd.Timestamp(f"{year}-01-01T00:00:00Z")
    end = pd.Timestamp(f"{year + 1}-01-01T00:00:00Z")
    period = frame[
        (frame["event_time"] >= start) & (frame["event_time"] < end)
    ].reset_index(drop=True)
    if period.empty:
        raise ValueError(f"annual period is empty: {year}")
    period = period.copy()
    period["desired_position"] = period[desired_column].astype(int)
    equity, trades = run_long_short_backtest(period, cost_model, initial_cash)
    buy_and_hold = run_buy_and_hold(period, cost_model, initial_cash)
    synthetic_trades = int(
        trades.get(INTERPOLATED_COLUMN, pd.Series(dtype=bool)).astype(bool).sum()
    )
    return {
        "strategy": summarize_equity(equity),
        "buy_and_hold": summarize_equity(buy_and_hold),
        "trade_count": int(len(trades)),
        "trade_statistics": _summarize_round_trips(trades),
        "desired_position_at_start": int(period.iloc[0][desired_column]),
        "trades_on_interpolated_days": synthetic_trades,
        "artifacts": {"equity": equity, "trades": trades, "buy_and_hold": buy_and_hold},
    }


def _compare(long_result: dict[str, object], combined_result: dict[str, object]) -> dict[str, float]:
    """combinedとlong-onlyの主要指標差を計算する。

    Args:
        long_result: long-onlyの年次結果。
        combined_result: long/short combinedの年次結果。

    Returns:
        CAGR差、最大DD改善幅、最終資産差を含む辞書。
    """

    long_metrics = long_result["strategy"]
    combined_metrics = combined_result["strategy"]
    return {
        "cagr_delta": combined_metrics["cagr"] - long_metrics["cagr"],
        "max_drawdown_improvement": combined_metrics["max_drawdown"]
        - long_metrics["max_drawdown"],
        "final_equity_delta": combined_metrics["final_equity"]
        - long_metrics["final_equity"],
    }


def _scorecard(periods: dict[str, dict[str, object]]) -> dict[str, object]:
    """年次比較から事前登録したcombinedとshortの基準を集計する。

    Args:
        periods: 年をキーとするlong、short、combinedの結果。

    Returns:
        DD改善年数、中央値、CAGR優位年数、short往復数を含む辞書。
    """

    comparisons = [period["comparison"] for period in periods.values()]
    short_results = [period["short"] for period in periods.values()]
    dd_improvements = [item["max_drawdown_improvement"] for item in comparisons]
    cagr_deltas = [item["cagr_delta"] for item in comparisons]
    return {
        "period_count": len(periods),
        "combined_max_drawdown_improved_periods": sum(
            value > 0 for value in dd_improvements
        ),
        "combined_median_max_drawdown_improvement": median(dd_improvements),
        "combined_cagr_at_least_long_periods": sum(
            value >= 0 for value in cagr_deltas
        ),
        "short_closed_round_trips": sum(
            item["trade_statistics"]["closed_round_trips"] for item in short_results
        ),
    }


def _classify(scorecard: dict[str, object]) -> dict[str, object]:
    """事前登録したcombined・short基準から研究上の暫定判定を作る。

    Args:
        scorecard: `_scorecard`が返す年次集計。

    Returns:
        候補条件、棄却条件、research status、promotion statusを含む辞書。
    """

    candidate = {
        "combined_dd_improved_at_least_3_years": scorecard[
            "combined_max_drawdown_improved_periods"
        ]
        >= 3,
        "combined_median_dd_improvement_positive": scorecard[
            "combined_median_max_drawdown_improvement"
        ]
        > 0,
        "combined_cagr_at_least_long_in_at_least_2_years": scorecard[
            "combined_cagr_at_least_long_periods"
        ]
        >= 2,
        "short_round_trips_at_least_5": scorecard["short_closed_round_trips"] >= 5,
    }
    rejection = {
        "combined_dd_improved_in_fewer_than_2_years": scorecard[
            "combined_max_drawdown_improved_periods"
        ]
        < 2,
        "combined_median_dd_non_positive": scorecard[
            "combined_median_max_drawdown_improvement"
        ]
        <= 0,
        "short_round_trips_zero": scorecard["short_closed_round_trips"] == 0,
    }
    if all(candidate.values()):
        status = "PASSED_RETROSPECTIVE_VALIDATION"
    elif any(rejection.values()):
        status = "REJECTED"
    else:
        status = "INCONCLUSIVE"
    return {
        "candidate_criteria": candidate,
        "rejection_criteria": rejection,
        "research_status": status,
        "promotion_status": (
            "NOT_ELIGIBLE" if status == "REJECTED" else "NEEDS_FORWARD_EVIDENCE"
        ),
        "promotion_note": "合成shortの価格proxyだけではBinance Japan margin/futuresのpaper・shadow・liveを承認しない。",
    }


def main() -> None:
    """日足品質を確認し、long・short・combinedを費用感度評価する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0003")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0010")
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
        raise ValueError("EXP-2026-0010 requires inherited synthetic-day markers")

    signals = prepare_donchian_long_short_regime_signals(
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
            long_result = _evaluate_period(
                signals, "desired_long_position", year, cost_model, args.initial_cash
            )
            short_result = _evaluate_period(
                signals, "desired_short_position", year, cost_model, args.initial_cash
            )
            combined_result = _evaluate_period(
                signals, "desired_position", year, cost_model, args.initial_cash
            )
            artifacts = {
                "long": long_result.pop("artifacts"),
                "short": short_result.pop("artifacts"),
                "combined": combined_result.pop("artifacts"),
            }
            if cost_name == "base":
                for prefix, item in artifacts.items():
                    item["equity"].to_csv(
                        args.output_dir / f"{year}-{prefix}-equity.csv", index=False
                    )
                    item["trades"].to_csv(
                        args.output_dir / f"{year}-{prefix}-trades.csv", index=False
                    )
                    item["buy_and_hold"].to_csv(
                        args.output_dir
                        / f"{year}-{prefix}-buy-and-hold-equity.csv",
                        index=False,
                    )
            periods[str(year)] = {
                "long": long_result,
                "short": short_result,
                "combined": combined_result,
                "comparison": _compare(long_result, combined_result),
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
        "experiment_id": "EXP-2026-0010",
        "evaluation_type": "retrospective price-only short diagnostic",
        "annual_windows": list(ANNUAL_WINDOWS),
        "strategy_freeze": {
            "entry_window": 55,
            "exit_window": 20,
            "regime_window": 200,
            "long_rule": "new long only when close > SMA200 and 55-day high breakout",
            "short_rule": "new short only when close < SMA200 and 55-day low breakdown",
            "combined_rule": "one position at a time; same-day long/short conflict stays flat",
            "execution": "next-day open",
        },
        "data_quality": quality,
        "synthetic_short_limitations": {
            "funding_rate_included": False,
            "borrow_interest_included": False,
            "maintenance_margin_included": False,
            "liquidation_included": False,
            "mark_index_basis_included": False,
            "leverage": 1.0,
        },
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
