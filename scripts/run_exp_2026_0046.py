#!/usr/bin/env python3
"""EXP-2026-0045のロング買い増しを最大3ロットへ制限して比較する。"""

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
    MAX_LOTS_PER_SYMBOL as REFERENCE_MAX_LOTS,
    _attach_entry_atr,
    _ladder_config as _reference_ladder_config,
    _ladder_diagnostics,
)


EXPERIMENT_ID = "EXP-2026-0046"
MAX_LOTS_PER_SYMBOL = 3
MAX_TOTAL_GROSS_NOTIONAL = Decimal("600")
FIXED_DRAWDOWNS_MAX3 = FIXED_DRAWDOWNS[:2]
ATR_MULTIPLIERS_MAX3 = ATR_MULTIPLIERS[:2]
ARMS = ("fixed_200", "fibonacci_fixed_percent_max3", "fibonacci_atr_max3")
REFERENCE_ARMS = ("fibonacci_fixed_percent_max5", "fibonacci_atr_max5")
BENCHMARK_FINAL_EQUITY = INITIAL_EQUITY * (Decimal("1.10") ** 4)


def _ladder_config() -> AllocationConfig:
    """最大3ロット・2銘柄合計600 USDTの設定を作る。"""

    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=Decimal("0"),
        max_long_gross_notional=MAX_TOTAL_GROSS_NOTIONAL,
        max_short_gross_notional=Decimal("0"),
        max_total_gross_notional=MAX_TOTAL_GROSS_NOTIONAL,
        per_symbol_max_notional=LADDER_LOT_NOTIONAL * MAX_LOTS_PER_SYMBOL,
        lot_notional=LADDER_LOT_NOTIONAL,
        max_concurrent_long_positions=2,
        max_concurrent_short_positions=len(SYMBOLS),
    )


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """最大3ロットarmまたは固定200を実行する。

    Args:
        frames: EXP-2026-0042固定シグナルとentry ATR。
        arm: ARMSに登録されたarm名。

    Returns:
        指定armの会計結果。

    Raises:
        ValueError: armが未登録の場合。
    """

    if arm == "fixed_200":
        return run_allocated_portfolio(
            frames, _allocation_config("momentum_top2"), COST_MODEL
        )
    if arm == "fibonacci_fixed_percent_max3":
        return run_long_ladder_portfolio(
            frames,
            _ladder_config(),
            COST_MODEL,
            fixed_drawdowns=FIXED_DRAWDOWNS_MAX3,
            max_lots_per_symbol=MAX_LOTS_PER_SYMBOL,
        )
    if arm == "fibonacci_atr_max3":
        return run_long_ladder_portfolio(
            frames,
            _ladder_config(),
            COST_MODEL,
            atr_multipliers=ATR_MULTIPLIERS_MAX3,
            max_lots_per_symbol=MAX_LOTS_PER_SYMBOL,
        )
    raise ValueError(f"unknown arm: {arm}")


def _run_reference_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """EXP-2026-0045最大5ロットを同じコードとデータで再計算する。"""

    if arm == "fibonacci_fixed_percent_max5":
        return run_long_ladder_portfolio(
            frames,
            _reference_ladder_config(),
            COST_MODEL,
            fixed_drawdowns=FIXED_DRAWDOWNS,
            max_lots_per_symbol=REFERENCE_MAX_LOTS,
        )
    if arm == "fibonacci_atr_max5":
        return run_long_ladder_portfolio(
            frames,
            _reference_ladder_config(),
            COST_MODEL,
            atr_multipliers=ATR_MULTIPLIERS,
            max_lots_per_symbol=REFERENCE_MAX_LOTS,
        )
    raise ValueError(f"unknown reference arm: {arm}")


def _decision_rules(
    results: dict[str, AllocatedPortfolioResult],
    references: dict[str, AllocatedPortfolioResult],
) -> dict[str, dict[str, bool]]:
    """固定200と対応する最大5ロット版に対する条件を判定する。

    Args:
        results: 最大3ロット2armと固定200。
        references: 最大5ロット2arm。

    Returns:
        最大3ロットarmごとの条件と総合判定。
    """

    fixed = results["fixed_200"].metrics
    fixed_ratio = _return_to_drawdown(fixed)
    pairs = {
        "fibonacci_fixed_percent_max3": "fibonacci_fixed_percent_max5",
        "fibonacci_atr_max3": "fibonacci_atr_max5",
    }
    decisions: dict[str, dict[str, bool]] = {}
    for arm, reference_arm in pairs.items():
        metrics = results[arm].metrics
        reference = references[reference_arm].metrics
        ratio = _return_to_drawdown(metrics)
        versus_fixed = bool(
            _decimal(metrics["final_equity"]) > _decimal(fixed["final_equity"])
            and _decimal(metrics["max_drawdown"])
            >= _decimal(fixed["max_drawdown"])
            and ratio is not None
            and fixed_ratio is not None
            and ratio >= fixed_ratio
        )
        versus_max5 = bool(
            _decimal(metrics["max_drawdown"])
            - _decimal(reference["max_drawdown"])
            >= Decimal("0.05")
            and _decimal(metrics["final_equity"])
            >= _decimal(reference["final_equity"]) * Decimal("0.90")
            and _decimal(metrics["final_equity"]) >= BENCHMARK_FINAL_EQUITY
        )
        decisions[arm] = {
            "versus_fixed_200": versus_fixed,
            "versus_same_max5_arm": versus_max5,
            "final_candidate": versus_fixed and versus_max5,
        }
    return decisions


def _arm_payload(result: AllocatedPortfolioResult) -> dict[str, object]:
    """会計結果を比較用の監査情報へまとめる。"""

    calendar = _calendar_year_diagnostics(result.equity_curve)
    return {
        "metrics": result.metrics,
        "allocation_diagnostics": _diagnostics(result),
        "ladder_diagnostics": _ladder_diagnostics(result),
        "exposure_diagnostics": _exposure_diagnostics(result),
        "calendar_years": calendar,
        "return_to_drawdown": _return_to_drawdown(result.metrics),
        "benchmark": _benchmark(_decimal(result.metrics["final_equity"])),
    }


def main() -> None:
    """最大3ロットを固定200・最大5ロットと比較して保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0046")
    )
    args = parser.parse_args()
    raw = _load_raw_frames(args.data_dir)
    base = _prepare_momentum_frames(raw, args.data_dir, long_count=2)
    frames = _attach_entry_atr(_apply_last_rank_exit(base))
    results = {arm: _run_arm(frames, arm) for arm in ARMS}
    references = {arm: _run_reference_arm(frames, arm) for arm in REFERENCE_ARMS}
    decisions = _decision_rules(results, references)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for arm, result in {**results, **references}.items():
        pd.DataFrame(result.events).to_csv(
            args.output_dir / f"{arm}-events.csv", index=False
        )
        pd.DataFrame(result.equity_curve).to_csv(
            args.output_dir / f"{arm}-equity.csv", index=False
        )
        pd.DataFrame(_calendar_year_diagnostics(result.equity_curve)).to_csv(
            args.output_dir / f"{arm}-calendar-years.csv", index=False
        )
    candidates = [arm for arm, values in decisions.items() if values["final_candidate"]]
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "parameters": {
            "max_lots_per_symbol": MAX_LOTS_PER_SYMBOL,
            "max_total_gross_notional": MAX_TOTAL_GROSS_NOTIONAL,
            "fixed_drawdowns": FIXED_DRAWDOWNS_MAX3,
            "atr_multipliers": ATR_MULTIPLIERS_MAX3,
            "lot_notional": LADDER_LOT_NOTIONAL,
            "trigger": "completed bar low",
            "execution": "next bar open",
        },
        "arms": {arm: _arm_payload(result) for arm, result in results.items()},
        "max5_references": {
            arm: _arm_payload(result) for arm, result in references.items()
        },
        "decision_rules": decisions,
        "research_status": "MAX3_CANDIDATE" if candidates else "KEEP_FIXED_200",
        "candidate_arms": candidates,
        "paper_shadow_change_status": "UNCHANGED",
        "limitations": [
            "EXP-2026-0045の結果を見た後に設定した後続仮説である。",
            "最大ロット数の変更と後半2段階の削除を同時に含む。",
            "過去データ上の比較だけでpaper/shadowを変更しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
