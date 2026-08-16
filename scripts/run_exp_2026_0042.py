#!/usr/bin/env python3
"""EXP-2026-0042の保有中最下位転落退出を比較する。"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import math
from pathlib import Path
import sys

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.portfolio import AllocatedPortfolioResult, run_allocated_portfolio  # noqa: E402
from scripts.run_exp_2026_0035 import (  # noqa: E402
    COST_MODEL,
    INITIAL_EQUITY,
    SYMBOLS,
    _allocation_config,
    _benchmark,
    _decimal,
    _diagnostics,
    _load_raw_frames,
    _prepare_momentum_frames,
)


EXPERIMENT_ID = "EXP-2026-0042"
LAST_RANK = len(SYMBOLS)
ARMS = ("base_4_top2", "live_rank_last_exit")


def _live_ranks(frames: dict[str, pd.DataFrame]) -> dict[str, list[int | None]]:
    """各足のモメンタムを全銘柄で順位付けする。

    Args:
        frames: 同一時刻のmomentum_returnを持つ銘柄別DataFrame。

    Returns:
        銘柄別の毎足順位。計算不能な足はNone。

    Raises:
        ValueError: 銘柄数、行数、時刻、または必須列が不正な場合。
    """

    if len(frames) < 2:
        raise ValueError("live rank requires at least two symbols")
    symbols = tuple(sorted(frames))
    lengths = {len(frame) for frame in frames.values()}
    if len(lengths) != 1:
        raise ValueError("live rank frame lengths differ")
    times = [
        tuple(pd.to_datetime(frames[symbol]["event_time"], utc=True))
        for symbol in symbols
    ]
    if any(times[0] != value for value in times[1:]):
        raise ValueError("live rank timestamps differ")
    ranks: dict[str, list[int | None]] = {
        symbol: [None] * len(frames[symbol]) for symbol in symbols
    }
    for index in range(next(iter(lengths))):
        values: dict[str, float] = {}
        for symbol in symbols:
            if "momentum_return" not in frames[symbol].columns:
                raise ValueError(f"missing momentum_return: {symbol}")
            value = float(frames[symbol].iloc[index]["momentum_return"])
            if math.isfinite(value):
                values[symbol] = value
        if len(values) != len(symbols):
            continue
        ordered = sorted(symbols, key=lambda symbol: (-values[symbol], symbol))
        for rank, symbol in enumerate(ordered, start=1):
            ranks[symbol][index] = rank
    return ranks


def _apply_last_rank_exit(
    frames: dict[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """保有銘柄が毎足順位の最下位になった次足で退出する。

    Args:
        frames: base desired positionとモメンタムを含む銘柄別DataFrame。

    Returns:
        live rank、trigger、退出後desired positionを追加したDataFrame。

    Raises:
        ValueError: base desired positionが0・1以外の場合。
    """

    ranks = _live_ranks(frames)
    result: dict[str, pd.DataFrame] = {}
    for symbol, source in frames.items():
        frame = source.copy()
        base = pd.to_numeric(frame["desired_long_position"], errors="coerce")
        if base.isna().any() or not base.isin([0, 1]).all():
            raise ValueError("desired_long_position must contain only 0 or 1")
        desired: list[int] = []
        triggers: list[bool] = []
        holding = False
        stopped = False
        pending_exit = False
        previous_base = 0
        for index, base_value in enumerate(base.astype(int)):
            trigger = False
            if base_value == 0:
                holding = False
                stopped = False
                pending_exit = False
                current_desired = 0
            elif pending_exit:
                holding = False
                stopped = True
                pending_exit = False
                current_desired = 0
            elif not holding and not stopped and previous_base == 0:
                holding = True
                current_desired = 1
            elif holding:
                current_desired = 1
            else:
                current_desired = 0
            if holding and ranks[symbol][index] == len(frames):
                trigger = True
                pending_exit = True
            desired.append(current_desired)
            triggers.append(trigger)
            previous_base = base_value
        frame["base_desired_long_position"] = base.astype(int)
        frame["live_cross_sectional_rank"] = pd.array(ranks[symbol], dtype="Int64")
        frame["last_rank_exit_trigger"] = triggers
        frame["desired_long_position"] = desired
        result[symbol] = frame
    return result


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """指定armをEXP-0035と同じ配分・費用で実行する。

    Args:
        frames: 銘柄別の実行用DataFrame。
        arm: `ARMS`へ登録されたarm名。

    Returns:
        配分ポートフォリオの実行結果。

    Raises:
        ValueError: arm名が未登録の場合。
    """

    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm}")
    return run_allocated_portfolio(frames, _allocation_config("momentum_top2"), COST_MODEL)


def _comparison(
    baseline: AllocatedPortfolioResult, candidate: AllocatedPortfolioResult
) -> dict[str, object]:
    """候補の採否条件とbaseとの差を計算する。

    Args:
        baseline: 元の4銘柄top2結果。
        candidate: 最下位転落退出の結果。

    Returns:
        equity・最大DD差と事前登録条件の判定。
    """

    base_final = _decimal(baseline.metrics["final_equity"])
    candidate_final = _decimal(candidate.metrics["final_equity"])
    drawdown_delta = _decimal(candidate.metrics["max_drawdown"]) - _decimal(
        baseline.metrics["max_drawdown"]
    )
    benchmark = INITIAL_EQUITY * (Decimal("1.10") ** 4)
    passed = (
        drawdown_delta >= Decimal("0.05")
        and candidate_final >= base_final * Decimal("0.95")
        and candidate_final >= benchmark
    )
    return {
        "final_equity_delta": str(candidate_final - base_final),
        "final_equity_retention": str(candidate_final / base_final),
        "max_drawdown_delta": str(drawdown_delta),
        "drawdown_improved_by_5_points": drawdown_delta >= Decimal("0.05"),
        "retains_95_percent_final_equity": candidate_final >= base_final * Decimal("0.95"),
        "beats_benchmark": candidate_final >= benchmark,
        "decision_rule_passed": passed,
    }


def main() -> None:
    """baseと最下位転落退出を実行して監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0042")
    )
    args = parser.parse_args()
    raw = _load_raw_frames(args.data_dir)
    base_frames = _prepare_momentum_frames(raw, args.data_dir, long_count=2)
    exit_frames = _apply_last_rank_exit(base_frames)
    arm_frames = {"base_4_top2": base_frames, "live_rank_last_exit": exit_frames}
    results = {arm: _run_arm(frames, arm) for arm, frames in arm_frames.items()}
    comparison = _comparison(results["base_4_top2"], results["live_rank_last_exit"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(
            args.output_dir / f"{arm}-events.csv", index=False
        )
        pd.DataFrame(result.equity_curve).to_csv(
            args.output_dir / f"{arm}-equity.csv", index=False
        )
    for symbol, frame in exit_frames.items():
        frame[
            [
                "event_time",
                "momentum_return",
                "live_cross_sectional_rank",
                "base_desired_long_position",
                "desired_long_position",
                "last_rank_exit_trigger",
            ]
        ].to_csv(args.output_dir / f"{symbol}-rank-signals.csv", index=False)
    trigger_count = sum(
        int(frame["last_rank_exit_trigger"].sum()) for frame in exit_frames.values()
    )
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "parameters": {
            "symbols": SYMBOLS,
            "last_rank": LAST_RANK,
            "lookback_bars": 360,
            "stop_execution": "next bar open",
            "initial_equity": str(INITIAL_EQUITY),
            "lot_notional": "200",
        },
        "arms": {
            arm: {
                "metrics": result.metrics,
                "diagnostics": _diagnostics(result),
                "benchmark": _benchmark(_decimal(result.metrics["final_equity"])),
            }
            for arm, result in results.items()
        },
        "exit_diagnostics": {"trigger_count": trigger_count},
        "comparison": comparison,
        "research_status": (
            "BACKTEST_CANDIDATE" if comparison["decision_rule_passed"] else "REJECTED"
        ),
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "4銘柄内順位のため、ユニバース固有の相対評価である。",
            "最下位転落後も次足始値までの価格変動を受ける。",
            "単一過去期間の比較であり、paper・shadow・live運用を承認しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
