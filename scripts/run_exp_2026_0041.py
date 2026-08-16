#!/usr/bin/env python3
"""EXP-2026-0041のentry基準ATR早期退出を比較する。"""

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
    LONG_ATR_BARS,
    LONG_ATR_MULTIPLIER,
    SYMBOLS,
    _allocation_config,
    _benchmark,
    _decimal,
    _diagnostics,
    _load_raw_frames,
    _prepare_momentum_frames,
)


EXPERIMENT_ID = "EXP-2026-0041"
ATR_BARS = LONG_ATR_BARS
ATR_MULTIPLIER = LONG_ATR_MULTIPLIER
ARMS = ("base_4_top2", "entry_atr_stop")


def _true_range(frame: pd.DataFrame) -> pd.Series:
    """OHLCからtrue rangeを計算する。

    Args:
        frame: high、low、closeを含むDataFrame。

    Returns:
        各足のtrue range。
    """

    previous_close = frame["close"].shift(1)
    return pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _apply_entry_atr_stop(
    frame: pd.DataFrame,
    *,
    atr_bars: int = ATR_BARS,
    atr_multiplier: float = ATR_MULTIPLIER,
) -> pd.DataFrame:
    """baseシグナルへentry時固定ATR stopを追加する。

    Args:
        frame: OHLCとdesired_long_positionを含む時系列。
        atr_bars: ATRのrolling本数。
        atr_multiplier: entry価格から差し引くATR倍率。

    Returns:
        次足退出のdesired position、ATR、stop価格、triggerを追加した時系列。

    Raises:
        ValueError: 必須列、ATR設定、OHLC、またはbase positionが不正な場合。
    """

    required = {"open", "high", "low", "close", "desired_long_position"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing ATR stop columns: {sorted(missing)}")
    if atr_bars <= 0 or not math.isfinite(atr_multiplier) or atr_multiplier <= 0:
        raise ValueError("ATR stop parameters must be positive")
    result = frame.copy()
    for column in ("open", "high", "low", "close"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if result[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError("invalid ATR stop OHLC")
    base = pd.to_numeric(result["desired_long_position"], errors="coerce")
    if base.isna().any() or not base.isin([0, 1]).all():
        raise ValueError("desired_long_position must contain only 0 or 1")
    result["entry_atr"] = _true_range(result).rolling(atr_bars).mean().shift(1)
    desired: list[int] = []
    stop_prices: list[float | None] = []
    triggers: list[bool] = []
    holding = False
    stopped = False
    pending_exit = False
    stop_price: float | None = None
    previous_base = 0
    for index, row in result.iterrows():
        base_position = int(base.loc[index])
        trigger = False
        if base_position == 0:
            holding = False
            stopped = False
            pending_exit = False
            stop_price = None
            current_desired = 0
        elif pending_exit:
            holding = False
            stopped = True
            pending_exit = False
            current_desired = 0
        elif not holding and not stopped and previous_base == 0:
            entry_atr = float(row["entry_atr"])
            if math.isfinite(entry_atr) and entry_atr > 0:
                holding = True
                stop_price = float(row["open"]) - atr_multiplier * entry_atr
                current_desired = 1
            else:
                stopped = True
                current_desired = 0
        elif holding:
            current_desired = 1
        else:
            current_desired = 0
        if holding and stop_price is not None and float(row["close"]) <= stop_price:
            trigger = True
            pending_exit = True
        desired.append(current_desired)
        stop_prices.append(stop_price)
        triggers.append(trigger)
        previous_base = base_position
    result["base_desired_long_position"] = base.astype(int)
    result["desired_long_position"] = desired
    result["entry_atr_stop_price"] = stop_prices
    result["entry_atr_stop_trigger"] = triggers
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
        candidate: ATR stop追加後の結果。

    Returns:
        equity・最大DD差と事前登録条件の判定。
    """

    base_final = _decimal(baseline.metrics["final_equity"])
    candidate_final = _decimal(candidate.metrics["final_equity"])
    drawdown_delta = _decimal(candidate.metrics["max_drawdown"]) - _decimal(
        baseline.metrics["max_drawdown"]
    )
    benchmark = INITIAL_EQUITY * (Decimal("1.10") ** 4)
    return {
        "final_equity_delta": str(candidate_final - base_final),
        "final_equity_retention": str(candidate_final / base_final),
        "max_drawdown_delta": str(drawdown_delta),
        "drawdown_improved_by_5_points": drawdown_delta >= Decimal("0.05"),
        "retains_95_percent_final_equity": candidate_final >= base_final * Decimal("0.95"),
        "beats_benchmark": candidate_final >= benchmark,
        "decision_rule_passed": (
            drawdown_delta >= Decimal("0.05")
            and candidate_final >= base_final * Decimal("0.95")
            and candidate_final >= benchmark
        ),
    }


def main() -> None:
    """baseとATR早期退出を実行して監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0041")
    )
    args = parser.parse_args()
    raw = _load_raw_frames(args.data_dir)
    base_frames = _prepare_momentum_frames(raw, args.data_dir, long_count=2)
    stop_frames = {
        symbol: _apply_entry_atr_stop(frame)
        for symbol, frame in base_frames.items()
    }
    arm_frames = {"base_4_top2": base_frames, "entry_atr_stop": stop_frames}
    results = {arm: _run_arm(frames, arm) for arm, frames in arm_frames.items()}
    comparison = _comparison(results["base_4_top2"], results["entry_atr_stop"])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(
            args.output_dir / f"{arm}-events.csv", index=False
        )
        pd.DataFrame(result.equity_curve).to_csv(
            args.output_dir / f"{arm}-equity.csv", index=False
        )
    for symbol, frame in stop_frames.items():
        frame[
            [
                "event_time",
                "close",
                "base_desired_long_position",
                "desired_long_position",
                "entry_atr",
                "entry_atr_stop_price",
                "entry_atr_stop_trigger",
            ]
        ].to_csv(args.output_dir / f"{symbol}-stop-signals.csv", index=False)
    trigger_count = sum(
        int(frame["entry_atr_stop_trigger"].sum()) for frame in stop_frames.values()
    )
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "parameters": {
            "symbols": SYMBOLS,
            "atr_bars": ATR_BARS,
            "atr_multiplier": ATR_MULTIPLIER,
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
        "stop_diagnostics": {"trigger_count": trigger_count},
        "comparison": comparison,
        "research_status": (
            "BACKTEST_CANDIDATE" if comparison["decision_rule_passed"] else "REJECTED"
        ),
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "ATR240・3倍は既存ロング定数の流用であり、このstop向けに最適化していない。",
            "closeでtriggerを確認して次足始値で退出するため、急落中のgap損失は残る。",
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
