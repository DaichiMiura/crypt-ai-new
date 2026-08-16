#!/usr/bin/env python3
"""EXP-2026-0036の上位2銘柄ロングを100・200 USDTロットで比較する。"""

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


EXPERIMENT_ID = "EXP-2026-0036"
ARM_LOT_NOTIONAL = {
    "cash_control": Decimal("100"),
    "top2_lot_100": Decimal("100"),
    "top2_lot_200": Decimal("200"),
}
ACTIVE_ARMS = {"top2_lot_100", "top2_lot_200"}


def _allocation_config(arm: str) -> AllocationConfig:
    """arm名から上位2銘柄用の固定ロット配分設定を作る。

    Args:
        arm: `ARM_LOT_NOTIONAL`へ登録されたarm名。

    Returns:
        1銘柄1ロット、最大2銘柄の配分設定。

    Raises:
        ValueError: arm名が未登録の場合。
    """

    if arm not in ARM_LOT_NOTIONAL:
        raise ValueError(f"unknown arm: {arm}")
    lot = ARM_LOT_NOTIONAL[arm]
    total_cap = lot * Decimal("2")
    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=RESERVE_CASH,
        max_long_gross_notional=(total_cap if arm in ACTIVE_ARMS else Decimal("0")),
        max_short_gross_notional=Decimal("0"),
        max_total_gross_notional=total_cap,
        per_symbol_max_notional=lot,
        lot_notional=lot,
        max_concurrent_long_positions=2,
        max_concurrent_short_positions=len(SYMBOLS),
    )


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """指定ロットの上位2銘柄ロングを実行する。"""

    source = (
        frames
        if arm in ACTIVE_ARMS
        else {
            symbol: frame.assign(desired_long_position=0)
            for symbol, frame in frames.items()
        }
    )
    return run_allocated_portfolio(source, _allocation_config(arm), COST_MODEL)


def _trade_signature(result: AllocatedPortfolioResult) -> list[tuple[str, str, str]]:
    """ロット額に依存しない売買時刻・種別・銘柄の署名を返す。"""

    return [
        (
            str(event["event_time"]),
            str(event["event_type"]),
            str(event["symbol"]),
        )
        for event in result.events
        if event["event_type"] in {"ENTRY", "EXIT"}
    ]


def _scale_comparison(
    smaller: AllocatedPortfolioResult,
    larger: AllocatedPortfolioResult,
) -> dict[str, object]:
    """100 USDTロットと200 USDTロットの損益・DD比率を計算する。"""

    small_metrics = smaller.metrics
    large_metrics = larger.metrics
    small_pnl = _decimal(small_metrics["net_pnl"])
    large_pnl = _decimal(large_metrics["net_pnl"])
    small_dd = abs(_decimal(small_metrics["max_drawdown"]))
    large_dd = abs(_decimal(large_metrics["max_drawdown"]))
    return {
        "trade_signature_identical": _trade_signature(smaller)
        == _trade_signature(larger),
        "net_pnl_ratio_lot100_to_lot200": str(
            small_pnl / large_pnl if large_pnl != 0 else Decimal("0")
        ),
        "max_drawdown_ratio_lot100_to_lot200": str(
            small_dd / large_dd if large_dd != 0 else Decimal("0")
        ),
        "final_equity_delta_lot100_minus_lot200": str(
            _decimal(small_metrics["final_equity"])
            - _decimal(large_metrics["final_equity"])
        ),
        "max_drawdown_delta_lot100_minus_lot200": str(
            _decimal(small_metrics["max_drawdown"])
            - _decimal(large_metrics["max_drawdown"])
        ),
    }


def main() -> None:
    """同一シグナルを100・200 USDTロットで実行し、監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0036")
    )
    args = parser.parse_args()

    raw_frames = _load_raw_frames(args.data_dir)
    frames = _prepare_momentum_frames(raw_frames, args.data_dir, long_count=2)
    results = {
        arm: _run_arm(frames, arm) for arm in ARM_LOT_NOTIONAL
    }
    comparison = _scale_comparison(
        results["top2_lot_100"], results["top2_lot_200"]
    )
    if not comparison["trade_signature_identical"]:
        raise ValueError("lot100 and lot200 trade signatures differ")

    args.output_dir.mkdir(parents=True, exist_ok=True)
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
                "desired_long_position",
                "funding_rate",
            ]
        ].to_csv(args.output_dir / f"{symbol}-signals.csv", index=False)
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
            "initial_equity": str(INITIAL_EQUITY),
            "reserve_cash": str(RESERVE_CASH),
            "arm_lot_notional": {
                arm: str(lot) for arm, lot in ARM_LOT_NOTIONAL.items()
            },
            "max_concurrent_positions": 2,
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
        "lot_scale_comparison": comparison,
        "research_status": "COMPLETED_NO_BALANCED_ARM",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "ロット額だけを変えた固定元本・非複利の比較であり、損益が概ね線形に縮小する設計である。",
            "単一過去期間の結果であり、paper・shadow・live運用を承認しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
