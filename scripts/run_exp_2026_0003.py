#!/usr/bin/env python3
"""EXP-2026-0003の日足Donchian 55/20バックテストを実行する。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypt_ai.research import (  # noqa: E402
    INTERPOLATED_COLUMN,
    CostModel,
    inspect_daily_data,
    prepare_donchian_signals,
    run_backtest,
    run_buy_and_hold,
    summarize_equity,
)


def _read_daily_file(path: Path) -> pd.DataFrame:
    """集約済み日足CSVを読み込み、時刻と数値の型を正規化する。

    Args:
        path: `build_exp_2026_0003_dataset.py`が作成した日足CSV。

    Returns:
        Donchian計算に使える日足データ。
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


def _evaluate(
    frame: pd.DataFrame,
    cost_model: CostModel,
    initial_cash: Decimal,
) -> dict[str, object]:
    """全期間と予約OOS期間の戦略・買い持ち指標を計算する。

    Args:
        frame: Donchianシグナルを付けた日足データ。
        cost_model: fee、spread、slippageの仮定。
        initial_cash: 初期paper現金残高。

    Returns:
        指標、取引数、合成行上の約定数を含む辞書。
    """

    equity, trades = run_backtest(frame, cost_model, initial_cash)
    baseline = run_buy_and_hold(frame, cost_model, initial_cash)
    oos_start = pd.Timestamp("2025-01-01T00:00:00Z")
    oos = frame[frame["event_time"] >= oos_start].reset_index(drop=True)
    if oos.empty:
        raise ValueError("reserved OOS period is empty")
    oos_equity, oos_trades = run_backtest(oos, cost_model, initial_cash)
    oos_baseline = run_buy_and_hold(oos, cost_model, initial_cash)
    signal_changes = frame["signal_position"].ne(
        frame["signal_position"].shift(1).fillna(0)
    )
    synthetic_signal_changes = int(
        (signal_changes & frame[INTERPOLATED_COLUMN].astype(bool)).sum()
    )
    synthetic_trades = int(
        trades.get(INTERPOLATED_COLUMN, pd.Series(dtype=bool)).astype(bool).sum()
    )
    oos_synthetic_trades = int(
        oos_trades.get(INTERPOLATED_COLUMN, pd.Series(dtype=bool))
        .astype(bool)
        .sum()
    )
    return {
        "full_strategy": summarize_equity(equity),
        "full_buy_and_hold": summarize_equity(baseline),
        "oos_strategy": summarize_equity(oos_equity),
        "oos_buy_and_hold": summarize_equity(oos_baseline),
        "trade_count": int(len(trades)),
        "oos_trade_count": int(len(oos_trades)),
        "trades_on_interpolated_days": synthetic_trades,
        "oos_trades_on_interpolated_days": oos_synthetic_trades,
        "signal_changes_on_interpolated_days": synthetic_signal_changes,
        "artifacts": {
            "equity": equity,
            "trades": trades,
            "baseline": baseline,
            "oos_equity": oos_equity,
            "oos_baseline": oos_baseline,
        },
    }


def main() -> None:
    """日足データを検査し、baseと事前登録済みfee感度を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0003")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0003")
    )
    parser.add_argument("--initial-cash", type=Decimal, default=Decimal("1000"))
    args = parser.parse_args()

    paths = sorted(args.data_dir.glob("*.csv"))
    if len(paths) != 1:
        raise ValueError(f"expected exactly one daily CSV: {paths}")
    frame = _read_daily_file(paths[0])
    quality = inspect_daily_data(frame)
    if quality["duplicate_count"] or quality["missing_intervals"]:
        raise ValueError(f"refusing to backtest incomplete daily data: {quality}")
    if not quality["interpolated_rows"]:
        raise ValueError("EXP-2026-0003 requires inherited synthetic-day markers")
    frame = prepare_donchian_signals(frame, entry_window=55, exit_window=20)

    cost_cases = {
        "base": CostModel(Decimal("0.001"), Decimal("0.0005"), Decimal("0.0005")),
        "adverse": CostModel(Decimal("0.0015"), Decimal("0.0005"), Decimal("0.0005")),
        "stress": CostModel(Decimal("0.002"), Decimal("0.0005"), Decimal("0.0005")),
    }
    evaluations: dict[str, dict[str, object]] = {}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, cost_model in cost_cases.items():
        result = _evaluate(frame, cost_model, args.initial_cash)
        artifacts = result.pop("artifacts")
        if name == "base":
            artifacts["equity"].to_csv(args.output_dir / "equity.csv", index=False)
            artifacts["trades"].to_csv(args.output_dir / "trades.csv", index=False)
            artifacts["baseline"].to_csv(
                args.output_dir / "baseline-equity.csv", index=False
            )
            artifacts["oos_equity"].to_csv(
                args.output_dir / "oos-equity.csv", index=False
            )
            artifacts["oos_baseline"].to_csv(
                args.output_dir / "oos-baseline-equity.csv", index=False
            )
        evaluations[name] = {
            "cost_model": {
                "fee_rate": str(cost_model.fee_rate),
                "round_trip_spread": str(cost_model.round_trip_spread),
                "slippage_per_fill": str(cost_model.slippage_per_fill),
            },
            **result,
        }
    summary = {
        "experiment_id": "EXP-2026-0003",
        "method": "Donchian close breakout, entry 55 days, exit 20 days",
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
