#!/usr/bin/env python3
"""EXP-2026-0042固定シグナルでロング段階買い増しを比較する。"""

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
    LONG_ATR_BARS,
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


EXPERIMENT_ID = "EXP-2026-0045"
ARMS = ("fixed_200", "fibonacci_fixed_percent", "fibonacci_atr")
FIXED_DRAWDOWNS = (
    Decimal("0.0236"),
    Decimal("0.0382"),
    Decimal("0.0618"),
    Decimal("0.10"),
)
ATR_MULTIPLIERS = (
    Decimal("1"),
    Decimal("1.618"),
    Decimal("2.618"),
    Decimal("4.236"),
)
LADDER_LOT_NOTIONAL = Decimal("100")
MAX_LOTS_PER_SYMBOL = 5


def _ladder_config() -> AllocationConfig:
    """最大2銘柄・各5ロットの段階買い増し設定を作る。"""

    return AllocationConfig(
        currency="USDT",
        allowed_symbols=SYMBOLS,
        initial_equity=INITIAL_EQUITY,
        reserve_cash=Decimal("0"),
        max_long_gross_notional=INITIAL_EQUITY,
        max_short_gross_notional=Decimal("0"),
        max_total_gross_notional=INITIAL_EQUITY,
        per_symbol_max_notional=LADDER_LOT_NOTIONAL * MAX_LOTS_PER_SYMBOL,
        lot_notional=LADDER_LOT_NOTIONAL,
        max_concurrent_long_positions=2,
        max_concurrent_short_positions=len(SYMBOLS),
    )


def _attach_entry_atr(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """各足open時点で利用可能な20日ATRを追加する。

    Args:
        frames: 同期済みOHLCシグナル。

    Returns:
        前足までのtrue range単純平均を`entry_atr`へ追加した銘柄別frame。
    """

    result: dict[str, pd.DataFrame] = {}
    for symbol, source in frames.items():
        frame = source.copy()
        previous_close = frame["close"].shift(1)
        true_range = pd.concat(
            (
                frame["high"] - frame["low"],
                (frame["high"] - previous_close).abs(),
                (frame["low"] - previous_close).abs(),
            ),
            axis=1,
        ).max(axis=1)
        frame["entry_atr"] = (
            true_range.rolling(LONG_ATR_BARS, min_periods=LONG_ATR_BARS)
            .mean()
            .shift(1)
        )
        result[symbol] = frame
    return result


def _run_arm(
    frames: dict[str, pd.DataFrame], arm: str
) -> AllocatedPortfolioResult:
    """固定シグナルを指定資金投入方法で実行する。

    Args:
        frames: EXP-2026-0042のシグナルとentry ATR。
        arm: ARMSに登録された比較名。

    Returns:
        指定armの会計結果。

    Raises:
        ValueError: armが未登録の場合。
    """

    if arm == "fixed_200":
        return run_allocated_portfolio(
            frames, _allocation_config("momentum_top2"), COST_MODEL
        )
    if arm == "fibonacci_fixed_percent":
        return run_long_ladder_portfolio(
            frames,
            _ladder_config(),
            COST_MODEL,
            fixed_drawdowns=FIXED_DRAWDOWNS,
            max_lots_per_symbol=MAX_LOTS_PER_SYMBOL,
        )
    if arm == "fibonacci_atr":
        return run_long_ladder_portfolio(
            frames,
            _ladder_config(),
            COST_MODEL,
            atr_multipliers=ATR_MULTIPLIERS,
            max_lots_per_symbol=MAX_LOTS_PER_SYMBOL,
        )
    raise ValueError(f"unknown arm: {arm}")


def _ladder_diagnostics(result: AllocatedPortfolioResult) -> dict[str, object]:
    """段階買い増し回数と損失退出を集計する。

    Args:
        result: 集計対象arm。

    Returns:
        初回、追加、trigger、拒否、損失退出、費用の集計。
    """

    entries = [event for event in result.events if event["event_type"] == "ENTRY"]
    additions = [event for event in result.events if event["event_type"] == "ADD"]
    triggers = [
        event for event in result.events if event["event_type"] == "ADD_TRIGGER"
    ]
    rejections = [
        event for event in result.events if event["event_type"] == "ORDER_REJECTED"
    ]
    exits = [event for event in result.events if event["event_type"] == "EXIT"]
    fees = sum(
        (
            _decimal(event["fee"])
            for event in result.events
            if event["event_type"] in {"ENTRY", "ADD", "EXIT"}
        ),
        Decimal("0"),
    )
    return {
        "initial_entry_count": len(entries),
        "addition_count": len(additions),
        "trigger_count": len(triggers),
        "rejection_count": len(rejections),
        "rejection_reasons": {
            reason: sum(event.get("allocation_reason") == reason for event in rejections)
            for reason in sorted(
                {str(event.get("allocation_reason")) for event in rejections}
            )
        },
        "added_position_loss_exit_count": sum(
            bool(event.get("had_additions"))
            and _decimal(event.get("pnl", "0")) < 0
            for event in exits
        ),
        "total_trading_fees": fees,
    }


def _decision_rules(
    results: dict[str, AllocatedPortfolioResult],
) -> dict[str, bool]:
    """事前登録した固定200対比の採否条件を判定する。"""

    fixed = results["fixed_200"].metrics
    fixed_ratio = _return_to_drawdown(fixed)
    rules: dict[str, bool] = {}
    for arm in ARMS[1:]:
        metrics = results[arm].metrics
        ratio = _return_to_drawdown(metrics)
        rules[f"{arm}_candidate"] = bool(
            _decimal(metrics["final_equity"]) > _decimal(fixed["final_equity"])
            and _decimal(metrics["max_drawdown"])
            >= _decimal(fixed["max_drawdown"])
            and ratio is not None
            and fixed_ratio is not None
            and ratio >= fixed_ratio
        )
    return rules


def main() -> None:
    """3armを実行し、監査成果物と判定を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0045")
    )
    args = parser.parse_args()
    raw = _load_raw_frames(args.data_dir)
    base = _prepare_momentum_frames(raw, args.data_dir, long_count=2)
    frames = _attach_entry_atr(_apply_last_rank_exit(base))
    results = {arm: _run_arm(frames, arm) for arm in ARMS}
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
            "metrics": result.metrics,
            "allocation_diagnostics": _diagnostics(result),
            "ladder_diagnostics": _ladder_diagnostics(result),
            "exposure_diagnostics": _exposure_diagnostics(result),
            "calendar_years": calendar,
            "return_to_drawdown": _return_to_drawdown(result.metrics),
            "benchmark": _benchmark(_decimal(result.metrics["final_equity"])),
        }

    candidates = [arm for arm in ARMS[1:] if rules[f"{arm}_candidate"]]
    preferred = max(
        candidates,
        key=lambda arm: _return_to_drawdown(results[arm].metrics) or Decimal("-Infinity"),
        default=None,
    )
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "status": "BACKTEST_COMPLETED",
        "frozen_strategy_id": "EXP-2026-0042",
        "parameters": {
            "fixed_drawdowns": FIXED_DRAWDOWNS,
            "atr_multipliers": ATR_MULTIPLIERS,
            "atr_bars": LONG_ATR_BARS,
            "ladder_lot_notional": LADDER_LOT_NOTIONAL,
            "max_lots_per_symbol": MAX_LOTS_PER_SYMBOL,
            "trigger": "completed bar low",
            "execution": "next bar open",
            "compounding": False,
        },
        "arms": arms,
        "decision_rules": rules,
        "preferred_candidate": preferred,
        "research_status": "LADDER_CANDIDATE" if preferred else "KEEP_FIXED_200",
        "paper_shadow_change_status": "UNCHANGED",
        "limitations": [
            "同じ2022〜2025年データで資金投入方法だけを比較した結果である。",
            "買い増しは下落段階の指値約定ではなく、確定足で発火した次足open約定である。",
            "最大2銘柄が各5ロットへ達すると元本1000 USDTを使い切り、手数料分のcash不足で一部注文が拒否され得る。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
