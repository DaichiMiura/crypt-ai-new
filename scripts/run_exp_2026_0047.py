#!/usr/bin/env python3
"""同一最大元本で一括200と100＋100の分割entryを比較する。"""

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
from crypt_ai.long_ladder import run_long_ladder_portfolio  # noqa: E402
from crypt_ai.portfolio import (  # noqa: E402
    AllocatedPortfolioResult,
    run_allocated_portfolio,
)
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
from scripts.run_exp_2026_0042 import _apply_last_rank_exit  # noqa: E402
from scripts.run_exp_2026_0043 import _calendar_year_diagnostics  # noqa: E402
from scripts.run_exp_2026_0044 import (  # noqa: E402
    _exposure_diagnostics,
    _return_to_drawdown,
)
from scripts.run_exp_2026_0045 import (  # noqa: E402
    ATR_MULTIPLIERS,
    FIXED_DRAWDOWNS,
    LADDER_LOT_NOTIONAL,
    _attach_entry_atr,
    _ladder_diagnostics,
)


EXPERIMENT_ID = "EXP-2026-0047"
ARMS = ("fixed_200", "split_fixed_percent_max2", "split_atr_max2")
MAX_LOTS_PER_SYMBOL = 2
MAX_TOTAL_ALLOCATED_NOTIONAL = Decimal("400")
FIXED_DRAWDOWN = (FIXED_DRAWDOWNS[0],)
ATR_MULTIPLIER = (ATR_MULTIPLIERS[0],)
BENCHMARK_FINAL_EQUITY = INITIAL_EQUITY * (Decimal("1.10") ** 4)


def _split_config() -> AllocationConfig:
    """最大2ロット・2銘柄合計400 USDTの配分設定を作る。

    Returns:
        固定100 USDTロットと一括200と同じ最大元本を持つ設定。
    """

    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=Decimal("200"),
        max_long_gross_notional=MAX_TOTAL_ALLOCATED_NOTIONAL,
        max_short_gross_notional=Decimal("0"),
        max_total_gross_notional=MAX_TOTAL_ALLOCATED_NOTIONAL,
        per_symbol_max_notional=LADDER_LOT_NOTIONAL * MAX_LOTS_PER_SYMBOL,
        lot_notional=LADDER_LOT_NOTIONAL,
        max_concurrent_long_positions=2,
        max_concurrent_short_positions=len(SYMBOLS),
    )


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """固定シグナルを一括または分割entryで実行する。

    Args:
        frames: EXP-2026-0042固定シグナルとentry ATR。
        arm: ARMSに登録された比較名。

    Returns:
        指定armのポートフォリオ会計結果。

    Raises:
        ValueError: armが未登録の場合。
    """

    if arm == "fixed_200":
        return run_allocated_portfolio(
            frames, _allocation_config("momentum_top2"), COST_MODEL
        )
    if arm == "split_fixed_percent_max2":
        return run_long_ladder_portfolio(
            frames,
            _split_config(),
            COST_MODEL,
            fixed_drawdowns=FIXED_DRAWDOWN,
            max_lots_per_symbol=MAX_LOTS_PER_SYMBOL,
        )
    if arm == "split_atr_max2":
        return run_long_ladder_portfolio(
            frames,
            _split_config(),
            COST_MODEL,
            atr_multipliers=ATR_MULTIPLIER,
            max_lots_per_symbol=MAX_LOTS_PER_SYMBOL,
        )
    raise ValueError(f"unknown arm: {arm}")


def _decision_rules(
    results: dict[str, AllocatedPortfolioResult],
) -> dict[str, bool]:
    """事前登録した固定200対比の候補条件を判定する。

    Args:
        results: 固定200と2つの分割entry結果。

    Returns:
        分割armごとのBACKTEST_CANDIDATE条件。
    """

    fixed = results["fixed_200"].metrics
    fixed_ratio = _return_to_drawdown(fixed)
    decisions: dict[str, bool] = {}
    for arm in ARMS[1:]:
        metrics = results[arm].metrics
        ratio = _return_to_drawdown(metrics)
        decisions[arm] = bool(
            _decimal(metrics["final_equity"]) >= _decimal(fixed["final_equity"])
            and _decimal(metrics["max_drawdown"])
            >= _decimal(fixed["max_drawdown"])
            and ratio is not None
            and fixed_ratio is not None
            and ratio >= fixed_ratio
            and _decimal(metrics["final_equity"]) >= BENCHMARK_FINAL_EQUITY
        )
    return decisions


def _arm_payload(result: AllocatedPortfolioResult) -> dict[str, object]:
    """arm結果を再現可能な比較指標へまとめる。

    Args:
        result: 集計するポートフォリオ会計結果。

    Returns:
        会計、配分、年別、benchmark指標。
    """

    return {
        "metrics": result.metrics,
        "allocation_diagnostics": _diagnostics(result),
        "ladder_diagnostics": _ladder_diagnostics(result),
        "exposure_diagnostics": _exposure_diagnostics(result),
        "calendar_years": _calendar_year_diagnostics(result.equity_curve),
        "return_to_drawdown": _return_to_drawdown(result.metrics),
        "benchmark": _benchmark(_decimal(result.metrics["final_equity"])),
    }


def main() -> None:
    """同一最大元本の3armを実行して監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0047")
    )
    args = parser.parse_args()
    raw = _load_raw_frames(args.data_dir)
    base = _prepare_momentum_frames(raw, args.data_dir, long_count=2)
    frames = _attach_entry_atr(_apply_last_rank_exit(base))
    results = {arm: _run_arm(frames, arm) for arm in ARMS}
    decisions = _decision_rules(results)
    candidates = [arm for arm, passed in decisions.items() if passed]
    preferred = max(
        candidates,
        key=lambda arm: _return_to_drawdown(results[arm].metrics)
        or Decimal("-Infinity"),
        default=None,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(
            args.output_dir / f"{arm}-events.csv", index=False
        )
        pd.DataFrame(result.equity_curve).to_csv(
            args.output_dir / f"{arm}-equity.csv", index=False
        )
        pd.DataFrame(_calendar_year_diagnostics(result.equity_curve)).to_csv(
            args.output_dir / f"{arm}-calendar-years.csv", index=False
        )
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "parameters": {
            "initial_equity": INITIAL_EQUITY,
            "max_notional_per_symbol": Decimal("200"),
            "max_total_allocated_notional": MAX_TOTAL_ALLOCATED_NOTIONAL,
            "split_lot_notional": LADDER_LOT_NOTIONAL,
            "max_lots_per_symbol": MAX_LOTS_PER_SYMBOL,
            "fixed_drawdown": FIXED_DRAWDOWN,
            "atr_multiplier": ATR_MULTIPLIER,
            "compounding": False,
            "additional_leverage": False,
        },
        "arms": {arm: _arm_payload(result) for arm, result in results.items()},
        "decision_rules": decisions,
        "preferred_candidate": preferred,
        "research_status": "BACKTEST_CANDIDATE" if preferred else "REJECTED",
        "promotion_status": "NOT_ELIGIBLE",
        "paper_shadow_change_status": "UNCHANGED",
        "limitations": [
            "ZOOMEX公開履歴上の結果であり、実約定での有効性は未検証である。",
            "最大投入元本は同じだが、分割armの平均投入元本と市場exposureは一括armより小さくなり得る。",
            "EXP-2026-0045/0046の結果を見た後に設定した後続仮説である。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
