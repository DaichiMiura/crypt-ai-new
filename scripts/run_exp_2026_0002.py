#!/usr/bin/env python3
"""EXP-2026-0002の線形補間データによるバックテストを実行する。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypt_ai.research import (  # noqa: E402
    CostModel,
    inspect_hourly_data,
    load_kline_files,
    prepare_signals,
    run_backtest,
    run_buy_and_hold,
    summarize_equity,
)


def main() -> None:
    """補間済みデータを検査し、決定論的バックテスト成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/processed/EXP-2026-0002"),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0002")
    )
    parser.add_argument("--initial-cash", type=Decimal, default=Decimal("1000"))
    parser.add_argument("--fee-rate", type=Decimal, default=Decimal("0.001"))
    parser.add_argument(
        "--round-trip-spread", type=Decimal, default=Decimal("0.0005")
    )
    parser.add_argument(
        "--slippage-per-fill", type=Decimal, default=Decimal("0.0005")
    )
    args = parser.parse_args()

    paths = sorted(args.data_dir.glob("*.csv"))
    frame = load_kline_files(paths)
    quality = inspect_hourly_data(frame)
    if quality["duplicate_count"] or quality["missing_intervals"]:
        raise ValueError(f"refusing to backtest incomplete data: {quality}")
    if not quality["interpolated_rows"]:
        raise ValueError("EXP-2026-0002 requires marked interpolated rows")
    frame = prepare_signals(frame)
    cost_model = CostModel(
        fee_rate=args.fee_rate,
        round_trip_spread=args.round_trip_spread,
        slippage_per_fill=args.slippage_per_fill,
    )
    equity, trades = run_backtest(frame, cost_model, args.initial_cash)
    baseline = run_buy_and_hold(frame, cost_model, args.initial_cash)
    oos_start = pd.Timestamp("2025-01-01T00:00:00Z")
    oos = frame[frame["event_time"] >= oos_start].reset_index(drop=True)
    if oos.empty:
        raise ValueError("reserved OOS period is empty")
    oos_equity, oos_trades = run_backtest(oos, cost_model, args.initial_cash)
    oos_baseline = run_buy_and_hold(oos, cost_model, args.initial_cash)
    signal_changes = frame["desired_position"].ne(
        frame["desired_position"].shift(1).fillna(0)
    )
    synthetic_signal_changes = int(
        (signal_changes & frame["is_interpolated"].astype(bool)).sum()
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    equity.to_csv(args.output_dir / "equity.csv", index=False)
    trades.to_csv(args.output_dir / "trades.csv", index=False)
    baseline.to_csv(args.output_dir / "baseline-equity.csv", index=False)
    oos_equity.to_csv(args.output_dir / "oos-equity.csv", index=False)
    oos_baseline.to_csv(args.output_dir / "oos-baseline-equity.csv", index=False)
    trades_on_interpolated_rows = 0
    if "is_interpolated" in trades.columns:
        trades_on_interpolated_rows = int(
            trades["is_interpolated"].astype(bool).sum()
        )
    summary = {
        "experiment_id": "EXP-2026-0002",
        "data_quality": quality,
        "cost_model": {
            "fee_rate": str(args.fee_rate),
            "round_trip_spread": str(args.round_trip_spread),
            "slippage_per_fill": str(args.slippage_per_fill),
        },
        "metrics": {
            "full_strategy": summarize_equity(equity),
            "full_buy_and_hold": summarize_equity(baseline),
            "oos_strategy": summarize_equity(oos_equity),
            "oos_buy_and_hold": summarize_equity(oos_baseline),
        },
        "trade_count": int(len(trades)),
        "oos_trade_count": int(len(oos_trades)),
        "trades_on_interpolated_rows": trades_on_interpolated_rows,
        "oos_trades_on_interpolated_rows": int(
            oos_trades.get("is_interpolated", pd.Series(dtype=bool))
            .astype(bool)
            .sum()
        ),
        "signal_changes_on_interpolated_rows": synthetic_signal_changes,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
