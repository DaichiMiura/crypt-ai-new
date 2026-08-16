#!/usr/bin/env python3
"""EXP-2026-0042固定仕様の期間・銘柄・費用頑健性を検証する。"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.portfolio import AllocatedPortfolioResult, run_allocated_portfolio  # noqa: E402
from crypt_ai.research import CostModel  # noqa: E402
from scripts.diagnose_exp_2026_0035_drawdown import _reconstruct_trades  # noqa: E402
from scripts.run_exp_2026_0035 import (  # noqa: E402
    COST_MODEL,
    INITIAL_EQUITY,
    _allocation_config,
    _decimal,
    _load_raw_frames,
    _prepare_momentum_frames,
)
from scripts.run_exp_2026_0042 import _apply_last_rank_exit  # noqa: E402


EXPERIMENT_ID = "EXP-2026-0043"
BENCHMARK_FINAL_EQUITY = INITIAL_EQUITY * (Decimal("1.10") ** 4)
DOUBLED_COST_MODEL = CostModel(
    fee_rate=COST_MODEL.fee_rate * 2,
    round_trip_spread=COST_MODEL.round_trip_spread * 2,
    slippage_per_fill=COST_MODEL.slippage_per_fill * 2,
)


def _calendar_year_diagnostics(equity_curve: list[dict[str, object]]) -> list[dict[str, object]]:
    """連続運用equityを暦年単位で集計する。

    Args:
        equity_curve: ポートフォリオの2時間足equity曲線。

    Returns:
        年別の開始・終了資産、増減率、年内最大DD。
    """

    frame = pd.DataFrame(equity_curve)
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    frame["equity"] = pd.to_numeric(frame["equity"], errors="raise")
    rows: list[dict[str, object]] = []
    previous_end: Decimal | None = None
    for year, group in frame.groupby(frame["event_time"].dt.year, sort=True):
        start = (
            _decimal(group.iloc[0]["equity"])
            if previous_end is None
            else previous_end
        )
        end = _decimal(group.iloc[-1]["equity"])
        year_equity = pd.concat(
            [pd.Series([float(start)]), group["equity"].reset_index(drop=True)],
            ignore_index=True,
        )
        running_peak = year_equity.cummax()
        max_drawdown = (year_equity / running_peak - 1).min()
        rows.append(
            {
                "year": int(year),
                "start_equity": start,
                "end_equity": end,
                "return_rate": end / start - 1,
                "max_drawdown": _decimal(max_drawdown),
            }
        )
        previous_end = end
    return rows


def _rolling_12_month_diagnostics(
    equity_curve: list[dict[str, object]],
) -> dict[str, object]:
    """月末equityからrolling 12か月リターンを集計する。

    Args:
        equity_curve: ポートフォリオの2時間足equity曲線。

    Returns:
        12か月窓数、最小値、中央値、正の窓比率。
    """

    frame = pd.DataFrame(equity_curve)
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    frame["equity"] = pd.to_numeric(frame["equity"], errors="raise")
    monthly = frame.set_index("event_time")["equity"].resample("ME").last()
    returns = monthly / monthly.shift(12) - 1
    returns = returns.dropna()
    if returns.empty:
        raise ValueError("rolling 12-month diagnostics require at least 13 months")
    return {
        "window_count": len(returns),
        "minimum_return": _decimal(returns.min()),
        "median_return": _decimal(returns.median()),
        "positive_window_fraction": Decimal(int((returns > 0).sum()))
        / Decimal(len(returns)),
    }


def _symbol_contributions(events: list[dict[str, object]]) -> dict[str, object]:
    """監査イベントから銘柄別純損益と利益集中度を集計する。

    Args:
        events: entry、Funding、exitを含む監査イベント。

    Returns:
        銘柄別取引数・純損益と最大正寄与率。
    """

    trades = _reconstruct_trades(pd.DataFrame(events))
    symbols: list[dict[str, object]] = []
    total = sum(trades["net_pnl"].map(_decimal), Decimal("0"))
    for symbol, group in trades.groupby("symbol", sort=True):
        pnl = sum(group["net_pnl"].map(_decimal), Decimal("0"))
        symbols.append({"symbol": symbol, "trade_count": len(group), "net_pnl": pnl})
    positive = [row["net_pnl"] for row in symbols if row["net_pnl"] > 0]
    largest_share = max(positive, default=Decimal("0")) / total if total > 0 else None
    return {
        "total_net_pnl": total,
        "symbols": symbols,
        "largest_positive_contribution_fraction_of_total_profit": largest_share,
    }


def _trade_signature(result: AllocatedPortfolioResult) -> list[tuple[str, str, str]]:
    """費用stress前後で照合する売買署名を作る。

    Args:
        result: ポートフォリオ実行結果。

    Returns:
        entry・exitの時刻、種類、銘柄タプル。
    """

    return [
        (str(event["event_time"]), str(event["event_type"]), str(event["symbol"]))
        for event in result.events
        if event["event_type"] in {"ENTRY", "EXIT"}
    ]


def main() -> None:
    """固定戦略の期間・銘柄・2倍費用検証を実行して保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0043")
    )
    args = parser.parse_args()
    raw = _load_raw_frames(args.data_dir)
    base_frames = _prepare_momentum_frames(raw, args.data_dir, long_count=2)
    frozen_frames = _apply_last_rank_exit(base_frames)
    config = _allocation_config("momentum_top2")
    normal = run_allocated_portfolio(frozen_frames, config, COST_MODEL)
    doubled = run_allocated_portfolio(frozen_frames, config, DOUBLED_COST_MODEL)
    signatures_match = _trade_signature(normal) == _trade_signature(doubled)
    calendar = _calendar_year_diagnostics(normal.equity_curve)
    rolling = _rolling_12_month_diagnostics(normal.equity_curve)
    contributions = _symbol_contributions(normal.events)
    positive_years = sum(row["return_rate"] > 0 for row in calendar)
    rules = {
        "doubled_cost_beats_benchmark": _decimal(doubled.metrics["final_equity"])
        >= BENCHMARK_FINAL_EQUITY,
        "at_least_3_positive_calendar_years": positive_years >= 3,
        "majority_positive_rolling_windows": rolling["positive_window_fraction"]
        > Decimal("0.5"),
        "single_symbol_contribution_below_70_percent": (
            contributions["largest_positive_contribution_fraction_of_total_profit"]
            is not None
            and contributions[
                "largest_positive_contribution_fraction_of_total_profit"
            ]
            < Decimal("0.70")
        ),
        "trade_signatures_match_under_cost_stress": signatures_match,
    }
    passed = all(rules.values())
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "VALIDATION_COMPLETED",
        "frozen_strategy_id": "EXP-2026-0042",
        "normal_cost_metrics": normal.metrics,
        "doubled_cost_metrics": doubled.metrics,
        "calendar_years": calendar,
        "rolling_12_month": rolling,
        "symbol_contributions": contributions,
        "validation_rules": rules,
        "validation_status": "ROBUSTNESS_VALIDATED" if passed else "WEAKNESS_FOUND",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "期間分解は同じ過去データの診断であり、独立した未来データではない。",
            "2倍費用stressは流動性枯渇や極端なgapを再現しない。",
            "固定200 USDTロットの結果であり、資金量増加時のmarket impactを含まない。",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(calendar).to_csv(args.output_dir / "calendar-years.csv", index=False)
    pd.DataFrame(contributions["symbols"]).to_csv(
        args.output_dir / "symbol-contributions.csv", index=False
    )
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
