#!/usr/bin/env python3
"""EXP-2026-0042固定シグナルでequity比例ロットを比較する。"""

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

from crypt_ai.portfolio import (  # noqa: E402
    AllocatedPortfolioResult,
    run_allocated_portfolio,
)
from scripts.run_exp_2026_0035 import (  # noqa: E402
    COST_MODEL,
    INITIAL_EQUITY,
    _allocation_config,
    _benchmark,
    _decimal,
    _diagnostics,
    _load_raw_frames,
    _prepare_momentum_frames,
)
from scripts.run_exp_2026_0042 import _apply_last_rank_exit  # noqa: E402
from scripts.run_exp_2026_0043 import _calendar_year_diagnostics  # noqa: E402


EXPERIMENT_ID = "EXP-2026-0044"
BENCHMARK_FINAL_EQUITY = INITIAL_EQUITY * (Decimal("1.10") ** 4)
ARM_FRACTIONS = {
    "fixed_200": None,
    "equity_20pct_per_slot": Decimal("0.20"),
    "equity_50pct_per_slot": Decimal("0.50"),
}


def _return_to_drawdown(metrics: dict[str, object]) -> Decimal | None:
    """総収益率を最大ドローダウン絶対値で割る。

    Args:
        metrics: return_rateとmax_drawdownを含む会計指標。

    Returns:
        return/DD比。最大DDが0ならNone。
    """

    drawdown = abs(_decimal(metrics["max_drawdown"]))
    if drawdown == 0:
        return None
    return _decimal(metrics["return_rate"]) / drawdown


def _exposure_diagnostics(
    result: AllocatedPortfolioResult,
) -> dict[str, object]:
    """equity曲線からgross/equity比率を集計する。

    Args:
        result: armのポートフォリオ会計結果。

    Returns:
        最大・平均gross/equity比率。
    """

    ratios: list[Decimal] = []
    for row in result.equity_curve:
        equity = _decimal(row["equity"])
        gross = _decimal(row["position_notional"])
        if equity <= 0:
            raise ValueError("equity must remain positive")
        ratios.append(gross / equity)
    return {
        "max_mark_gross_fraction": max(ratios, default=Decimal("0")),
        "average_mark_gross_fraction": (
            sum(ratios, Decimal("0")) / Decimal(len(ratios))
            if ratios
            else Decimal("0")
        ),
    }


def _decision_rules(
    results: dict[str, AllocatedPortfolioResult],
) -> dict[str, bool]:
    """事前登録した20%枠・50%枠の採用条件を判定する。

    Args:
        results: ARM_FRACTIONSの全arm結果。

    Returns:
        arm別の機械的な採用条件判定。
    """

    fixed = results["fixed_200"].metrics
    twenty = results["equity_20pct_per_slot"].metrics
    fifty = results["equity_50pct_per_slot"].metrics
    twenty_ratio = _return_to_drawdown(twenty)
    fifty_ratio = _return_to_drawdown(fifty)
    return {
        "equity_20pct_per_slot_candidate": (
            _decimal(twenty["final_equity"]) > _decimal(fixed["final_equity"])
            and _decimal(twenty["max_drawdown"])
            >= _decimal(fixed["max_drawdown"])
            and _decimal(twenty["final_equity"]) >= BENCHMARK_FINAL_EQUITY
        ),
        "equity_50pct_per_slot_candidate": (
            _decimal(fifty["final_equity"]) > _decimal(twenty["final_equity"])
            and twenty_ratio is not None
            and fifty_ratio is not None
            and fifty_ratio >= twenty_ratio
        ),
    }


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """固定シグナルを指定armの資金配分で実行する。

    Args:
        frames: EXP-2026-0042の固定シグナルDataFrame。
        arm: ARM_FRACTIONSへ登録されたarm名。

    Returns:
        指定資金配分の会計結果。

    Raises:
        ValueError: arm名が未登録の場合。
    """

    if arm not in ARM_FRACTIONS:
        raise ValueError(f"unknown arm: {arm}")
    return run_allocated_portfolio(
        frames,
        _allocation_config("momentum_top2"),
        COST_MODEL,
        entry_equity_fraction=ARM_FRACTIONS[arm],
    )


def main() -> None:
    """3資金配分armを実行し、監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0044")
    )
    args = parser.parse_args()
    raw = _load_raw_frames(args.data_dir)
    base_frames = _prepare_momentum_frames(raw, args.data_dir, long_count=2)
    frozen_frames = _apply_last_rank_exit(base_frames)
    results = {
        arm: _run_arm(frozen_frames, arm)
        for arm in ARM_FRACTIONS
    }
    rules = _decision_rules(results)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    arms: dict[str, object] = {}
    for arm, result in results.items():
        pd.DataFrame(result.events).to_csv(
            args.output_dir / f"{arm}-events.csv", index=False
        )
        pd.DataFrame(result.equity_curve).to_csv(
            args.output_dir / f"{arm}-equity.csv", index=False
        )
        calendar = _calendar_year_diagnostics(result.equity_curve)
        pd.DataFrame(calendar).to_csv(
            args.output_dir / f"{arm}-calendar-years.csv", index=False
        )
        arms[arm] = {
            "entry_equity_fraction": ARM_FRACTIONS[arm],
            "metrics": result.metrics,
            "allocation_diagnostics": _diagnostics(result),
            "exposure_diagnostics": _exposure_diagnostics(result),
            "calendar_years": calendar,
            "return_to_drawdown": _return_to_drawdown(result.metrics),
            "benchmark": _benchmark(_decimal(result.metrics["final_equity"])),
        }
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "frozen_strategy_id": "EXP-2026-0042",
        "parameters": {
            "initial_equity": str(INITIAL_EQUITY),
            "symbols": tuple(frozen_frames),
            "max_concurrent_positions": 2,
            "sizing_timing": "entry only",
            "fee_inclusive_slot_budget": True,
            "additional_leverage": False,
        },
        "arms": arms,
        "decision_rules": rules,
        "research_status": (
            "FULL_ALLOCATION_CANDIDATE"
            if rules["equity_50pct_per_slot_candidate"]
            else "COMPOUNDING_CANDIDATE"
            if rules["equity_20pct_per_slot_candidate"]
            else "KEEP_FIXED_200"
        ),
        "paper_shadow_change_status": "UNCHANGED",
        "limitations": [
            "同じ2022〜2025年データで資金配分だけを比較した結果である。",
            "保有中の建玉は目標比率へresizeせず、新規entry時だけ複利を反映する。",
            "50%枠は複利化と最大投資比率引上げを同時に含む。",
            "Fundingは発生時刻のbar openで評価するよう会計を修正したため、過去のEXP-2026-0042記録値と固定armがわずかに異なる可能性がある。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
