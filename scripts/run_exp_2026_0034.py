#!/usr/bin/env python3
"""EXP-2026-0034のベーシス・ヘッジと現金保持対照群を比較する。"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path

import pandas as pd

from scripts.run_exp_2026_0015 import _load_symbol
from scripts.run_exp_2026_0023 import _run_long_sleeve, _summarize_equity_curve
from scripts.run_exp_2026_0032 import _prepare_frames
from scripts.run_exp_2026_0033 import (
    ARM_MAX_PAIRS,
    BASIS_SYMBOLS,
    INDEX_BENCHMARK,
    RESERVE_CASH,
    _benchmark,
    _combine_arm,
    _run_arm as run_hedge_arm,
    _validate_common_timestamps,
)
from crypt_ai.void_short_accounting import VoidShortCostModel


EXPERIMENT_ID = "EXP-2026-0034"
SYMBOLS = BASIS_SYMBOLS
INITIAL_EQUITY = Decimal("1000")
DEPLOYABLE_EQUITY = INITIAL_EQUITY - RESERVE_CASH
ARM_BUDGET = {
    "long_only": Decimal("0"),
    "hedge_5pct": Decimal("50"),
    "cash_control_5pct": Decimal("50"),
    "hedge_10pct": Decimal("100"),
    "cash_control_10pct": Decimal("100"),
    "hedge_20pct": Decimal("200"),
    "cash_control_20pct": Decimal("200"),
}
HEDGE_ARMS = {"hedge_5pct", "hedge_10pct", "hedge_20pct"}
CASH_CONTROL_ARMS = {
    "cash_control_5pct",
    "cash_control_10pct",
    "cash_control_20pct",
}
PAIRED_ARMS = {
    "hedge_5pct": "cash_control_5pct",
    "hedge_10pct": "cash_control_10pct",
    "hedge_20pct": "cash_control_20pct",
}


def _decimal(value: object) -> Decimal:
    """入力値をDecimalへ変換する。"""

    return Decimal(str(value))


def _run_cash_control(
    long_frames: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
    *,
    arm: str,
    timestamps: list[pd.Timestamp],
) -> dict[str, object]:
    """basis予算を現金として保持し、対応するロングだけを実行する。"""

    budget = ARM_BUDGET[arm]
    long_budget = DEPLOYABLE_EQUITY - budget
    per_symbol_equity = long_budget / Decimal(len(SYMBOLS))
    long_results = {
        symbol: _run_long_sleeve(
            frame,
            funding,
            symbol=symbol,
            initial_equity=per_symbol_equity,
            costs=VoidShortCostModel(),
        )
        for symbol, (frame, funding) in long_frames.items()
    }
    # _combine_armで同じロング会計・イベント構造を使い、後から対照群の
    # 未投資予算を各時刻の現金へ加える。
    base = _combine_arm(
        long_results,
        None,
        pair_budget=Decimal("0"),
        timestamps=timestamps,
    )
    curve = []
    for row in base["equity_curve"]:
        updated = dict(row)
        updated["equity"] = str(_decimal(row["equity"]) + budget)
        updated["idle_cash"] = str(budget)
        curve.append(updated)
    metrics = _summarize_equity_curve(
        symbol="PORTFOLIO",
        initial_equity=INITIAL_EQUITY,
        equity_curve=curve,
        events=base["events"],
        total_fees=_decimal(base["metrics"]["total_fees"]),
        funding_cash_flow=_decimal(base["metrics"]["total_funding_cash_flow"]),
        max_position_notional=max(
            (_decimal(row["position_notional"]) for row in curve),
            default=Decimal("0"),
        ),
    )
    metrics.update(
        {
            "basis_budget": str(budget),
            "idle_cash": str(budget),
            "reserve_cash": str(RESERVE_CASH),
            "benchmark": _benchmark(_decimal(metrics["final_equity"])),
            "long_entry_count": base["metrics"]["long_entry_count"],
            "long_exit_count": base["metrics"]["long_exit_count"],
            "basis_entry_count": 0,
            "basis_exit_count": 0,
            "basis_funding_cash_flow": "0",
        }
    )
    return {
        "metrics": metrics,
        "events": base["events"],
        "equity_curve": curve,
        "long_sleeves": base["long_sleeves"],
        "basis_sleeve": None,
    }


def _run_arm(
    long_frames: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
    basis_frames: dict[str, pd.DataFrame],
    *,
    arm: str,
    timestamps: list[pd.Timestamp],
) -> dict[str, object]:
    """指定armのヘッジまたは現金保持対照群を実行する。"""

    if arm not in ARM_BUDGET:
        raise ValueError(f"unknown arm: {arm}")
    if arm in CASH_CONTROL_ARMS:
        return _run_cash_control(long_frames, arm=arm, timestamps=timestamps)
    return run_hedge_arm(
        long_frames,
        basis_frames,
        arm=arm,
        timestamps=timestamps,
    )


def _compare_hedge_to_cash(
    hedge: dict[str, object], cash: dict[str, object]
) -> dict[str, object]:
    """同一予算のhedgeとcash controlの差を計算する。"""

    hedge_metrics = hedge["metrics"]
    cash_metrics = cash["metrics"]
    final_delta = _decimal(hedge_metrics["final_equity"]) - _decimal(
        cash_metrics["final_equity"]
    )
    dd_delta = _decimal(hedge_metrics["max_drawdown"]) - _decimal(
        cash_metrics["max_drawdown"]
    )
    return {
        "final_equity_delta_hedge_minus_cash": str(final_delta),
        "max_drawdown_delta_hedge_minus_cash": str(dd_delta),
        "fee_delta_hedge_minus_cash": str(
            _decimal(hedge_metrics["total_fees"])
            - _decimal(cash_metrics["total_fees"])
        ),
        "funding_delta_hedge_minus_cash": str(
            _decimal(hedge_metrics["total_funding_cash_flow"])
            - _decimal(cash_metrics["total_funding_cash_flow"])
        ),
        "hedge_final_equity_better": final_delta > 0,
        "hedge_max_drawdown_better": dd_delta > 0,
    }


def main() -> None:
    """同一basis予算のヘッジ群と現金保持群を実行する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--perpetual-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--spot-dir", type=Path, default=Path("data/processed/EXP-2026-0032")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0034")
    )
    args = parser.parse_args()

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    long_frames = {
        symbol: _load_symbol(args.perpetual_dir, metadata, symbol)[:2]
        for symbol in SYMBOLS
    }
    for symbol, (frame, _) in long_frames.items():
        if frame.empty:
            raise ValueError(f"empty long input: {symbol}")
    basis_frames = _prepare_frames(args.spot_dir, args.perpetual_dir)
    timestamps = _validate_common_timestamps(basis_frames, name="basis")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    arms = tuple(ARM_BUDGET)
    results = {
        arm: _run_arm(
            long_frames,
            basis_frames,
            arm=arm,
            timestamps=timestamps,
        )
        for arm in arms
    }
    comparisons = {
        hedge: _compare_hedge_to_cash(results[hedge], results[cash])
        for hedge, cash in PAIRED_ARMS.items()
    }
    for arm, result in results.items():
        pd.DataFrame(result["events"]).to_csv(
            args.output_dir / f"{arm}-events.csv", index=False
        )
        pd.DataFrame(result["equity_curve"]).to_csv(
            args.output_dir / f"{arm}-equity.csv", index=False
        )

    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "parameters": {
            "symbols": SYMBOLS,
            "initial_equity": str(INITIAL_EQUITY),
            "reserve_cash": str(RESERVE_CASH),
            "deployable_equity": str(DEPLOYABLE_EQUITY),
            "basis_budget": {
                arm: str(budget) for arm, budget in ARM_BUDGET.items()
            },
            "hedge_arms": sorted(HEDGE_ARMS),
            "cash_control_arms": sorted(CASH_CONTROL_ARMS),
            "compounding": False,
            "cross_sleeve_reallocation": False,
        },
        "arms": {
            arm: {
                "metrics": result["metrics"],
                "long_sleeves": result["long_sleeves"],
                "basis_sleeve": result["basis_sleeve"],
            }
            for arm, result in results.items()
        },
        "hedge_vs_cash_control": comparisons,
        "benchmark": {
            "benchmark_final_equity": str(INDEX_BENCHMARK),
            "description": "4年間・年率10%複利の基準",
        },
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "EXP-2026-0033と同一の単一過去期間を使った切り分けであり、独立した将来期間の検証ではない。",
            "cash controlは未投資予算を現金保持する理論対照で、取引所の実際の資金拘束や利回りは再現しない。",
            "ロングとbasisは固定スリーブで、動的再配分、片脚約定、清算、数量刻みを完全には再現しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
