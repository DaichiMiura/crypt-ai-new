#!/usr/bin/env python3
"""EXP-2026-0002の線形補間データによるバックテストを実行する。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypt_ai.exp_2026_0001 import (  # noqa: E402
    CostModel,
    inspect_hourly_data,
    load_kline_files,
    prepare_signals,
    run_backtest,
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
    args.output_dir.mkdir(parents=True, exist_ok=True)
    equity.to_csv(args.output_dir / "equity.csv", index=False)
    trades.to_csv(args.output_dir / "trades.csv", index=False)
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
        "metrics": summarize_equity(equity),
        "trade_count": int(len(trades)),
        "trades_on_interpolated_rows": trades_on_interpolated_rows,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
