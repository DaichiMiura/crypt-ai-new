#!/usr/bin/env python3
"""EXP-2026-0037の週次退出とモメンタム0早期退出を比較する。"""

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

from crypt_ai.allocation import AllocationConfig  # noqa: E402
from crypt_ai.portfolio import AllocatedPortfolioResult, run_allocated_portfolio  # noqa: E402
from scripts.run_exp_2026_0035 import (  # noqa: E402
    COST_MODEL,
    INITIAL_EQUITY,
    RESERVE_CASH,
    SYMBOLS,
    _benchmark,
    _decimal,
    _diagnostics,
    _load_raw_frames,
    _prepare_momentum_frames,
)


EXPERIMENT_ID = "EXP-2026-0037"
LOT_NOTIONAL = Decimal("200")
TOTAL_CAP = Decimal("400")
ARMS = ("cash_control", "weekly_exit", "momentum_zero_exit")


def _allocation_config(arm: str) -> AllocationConfig:
    """週次・早期退出armに共通する固定配分設定を作る。

    Args:
        arm: `ARMS`へ登録されたarm名。

    Returns:
        1銘柄200 USDT、最大2銘柄のロング配分設定。

    Raises:
        ValueError: arm名が未登録の場合。
    """

    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm}")
    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=RESERVE_CASH,
        max_long_gross_notional=(TOTAL_CAP if arm != "cash_control" else Decimal("0")),
        max_short_gross_notional=Decimal("0"),
        max_total_gross_notional=TOTAL_CAP,
        per_symbol_max_notional=LOT_NOTIONAL,
        lot_notional=LOT_NOTIONAL,
        max_concurrent_long_positions=2,
        max_concurrent_short_positions=len(SYMBOLS),
    )


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """指定退出armの固定ロットポートフォリオを実行する。"""

    source = (
        frames
        if arm != "cash_control"
        else {
            symbol: frame.assign(desired_long_position=0)
            for symbol, frame in frames.items()
        }
    )
    return run_allocated_portfolio(source, _allocation_config(arm), COST_MODEL)


def _ranking_signature(
    frames: dict[str, pd.DataFrame],
) -> list[tuple[str, str, int, bool]]:
    """リバランス時刻・銘柄・順位・市場regimeの署名を返す。"""

    signature: list[tuple[str, str, int, bool]] = []
    for symbol in sorted(frames):
        selected = frames[symbol][frames[symbol]["rebalance_signal"]]
        signature.extend(
            (
                row.event_time.isoformat(),
                symbol,
                int(row.cross_sectional_rank),
                bool(row.market_regime_ok),
            )
            for row in selected.itertuples(index=False)
        )
    return sorted(signature)


def _compare(
    weekly: AllocatedPortfolioResult,
    early: AllocatedPortfolioResult,
    *,
    early_exit_count: int,
    ranking_identical: bool,
) -> dict[str, object]:
    """早期退出と週次退出の主要指標差を計算する。"""

    weekly_metrics = weekly.metrics
    early_metrics = early.metrics
    return {
        "ranking_signature_identical": ranking_identical,
        "early_exit_signal_count": early_exit_count,
        "final_equity_delta_early_minus_weekly": str(
            _decimal(early_metrics["final_equity"])
            - _decimal(weekly_metrics["final_equity"])
        ),
        "max_drawdown_delta_early_minus_weekly": str(
            _decimal(early_metrics["max_drawdown"])
            - _decimal(weekly_metrics["max_drawdown"])
        ),
        "fee_delta_early_minus_weekly": str(
            _decimal(early_metrics["total_fees"])
            - _decimal(weekly_metrics["total_fees"])
        ),
        "funding_delta_early_minus_weekly": str(
            _decimal(early_metrics["funding_cash_flow"])
            - _decimal(weekly_metrics["funding_cash_flow"])
        ),
        "early_final_equity_improved": _decimal(early_metrics["final_equity"])
        > _decimal(weekly_metrics["final_equity"]),
        "early_max_drawdown_improved": _decimal(early_metrics["max_drawdown"])
        > _decimal(weekly_metrics["max_drawdown"]),
    }


def main() -> None:
    """週次退出とモメンタム0早期退出を実行し、監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0037")
    )
    args = parser.parse_args()

    raw_frames = _load_raw_frames(args.data_dir)
    weekly_frames = _prepare_momentum_frames(
        raw_frames,
        args.data_dir,
        long_count=2,
        early_exit_on_nonpositive=False,
    )
    early_frames = _prepare_momentum_frames(
        raw_frames,
        args.data_dir,
        long_count=2,
        early_exit_on_nonpositive=True,
    )
    ranking_identical = _ranking_signature(weekly_frames) == _ranking_signature(
        early_frames
    )
    if not ranking_identical:
        raise ValueError("weekly and early-exit ranking signatures differ")
    early_exit_count = sum(
        int(frame["momentum_early_exit_signal"].sum())
        for frame in early_frames.values()
    )
    results = {
        "cash_control": _run_arm(weekly_frames, "cash_control"),
        "weekly_exit": _run_arm(weekly_frames, "weekly_exit"),
        "momentum_zero_exit": _run_arm(early_frames, "momentum_zero_exit"),
    }
    comparison = _compare(
        results["weekly_exit"],
        results["momentum_zero_exit"],
        early_exit_count=early_exit_count,
        ranking_identical=ranking_identical,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for label, frames in (("weekly", weekly_frames), ("early", early_frames)):
        for symbol, frame in frames.items():
            frame[
                [
                    "event_time",
                    "close",
                    "momentum_return",
                    "cross_sectional_rank",
                    "market_median_momentum",
                    "market_regime_ok",
                    "rebalance_signal",
                    "momentum_early_exit_signal",
                    "desired_long_position",
                    "funding_rate",
                ]
            ].to_csv(args.output_dir / f"{label}-{symbol}-signals.csv", index=False)
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(
            args.output_dir / f"{arm}-events.csv", index=False
        )
        pd.DataFrame(result.equity_curve).to_csv(
            args.output_dir / f"{arm}-equity.csv", index=False
        )

    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "parameters": {
            "symbols": SYMBOLS,
            "signal_source": "EXP-2026-0035 momentum_top2",
            "early_exit_rule": "selected symbol 30-day momentum <= 0",
            "reentry_rule": "wait until next scheduled 7-day rebalance",
            "initial_equity": str(INITIAL_EQUITY),
            "reserve_cash": str(RESERVE_CASH),
            "lot_notional": str(LOT_NOTIONAL),
            "max_long_gross_notional": str(TOTAL_CAP),
            "compounding": False,
            "cost_model": {
                "fee_rate": str(COST_MODEL.fee_rate),
                "round_trip_spread": str(COST_MODEL.round_trip_spread),
                "slippage_per_fill": str(COST_MODEL.slippage_per_fill),
            },
        },
        "arms": {
            arm: {
                "metrics": result.metrics,
                "diagnostics": _diagnostics(result),
                "benchmark": _benchmark(_decimal(result.metrics["final_equity"])),
            }
            for arm, result in results.items()
        },
        "early_exit_comparison": comparison,
        "research_status": "COMPLETED_NO_BALANCED_ARM",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "同じ30日モメンタムをentry順位、市場regime、早期退出に重複利用している。",
            "単一過去期間の退出比較であり、paper・shadow・live運用を承認しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
