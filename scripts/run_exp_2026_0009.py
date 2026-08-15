#!/usr/bin/env python3
"""EXP-2026-0009のSMAレジームフィルターを2026年forward-like期間で評価する。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
import sys

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.research import (  # noqa: E402
    INTERPOLATED_COLUMN,
    CostModel,
    inspect_daily_data,
    prepare_donchian_regime_filter_signals,
    prepare_donchian_signals,
    run_backtest,
    run_buy_and_hold,
    summarize_equity,
)
from scripts.run_exp_2026_0007 import (  # noqa: E402
    _read_daily_file,
    _summarize_round_trips,
)


FORWARD_START = pd.Timestamp("2026-01-01T00:00:00Z")
FORWARD_END = pd.Timestamp("2026-07-31T23:59:59Z")
WARMUP_START = pd.Timestamp("2020-01-01T00:00:00Z")


def _evaluate(
    frame: pd.DataFrame, cost_model: CostModel, initial_cash: Decimal
) -> dict[str, object]:
    """登録済みforward期間の戦略、買い持ち、合成行影響を評価する。

    Args:
        frame: 全履歴でシグナル計算済みの日足データ。
        cost_model: fee、spread、slippageの固定仮定。
        initial_cash: forward開始時のpaper初期資金。

    Returns:
        forward期間の損益、取引統計、成果物を含む辞書。

    Raises:
        ValueError: warm-up履歴またはforward日足が不足する場合。
    """

    if frame["event_time"].min() > WARMUP_START:
        raise ValueError("warm-up history starts after the registered date")
    period = frame[
        (frame["event_time"] >= FORWARD_START)
        & (frame["event_time"] <= FORWARD_END)
    ].reset_index(drop=True)
    expected_rows = (FORWARD_END.normalize() - FORWARD_START).days + 1
    if len(period) != expected_rows:
        raise ValueError(
            f"forward period must contain {expected_rows} daily rows: {len(period)}"
        )
    equity, trades = run_backtest(period, cost_model, initial_cash)
    buy_and_hold = run_buy_and_hold(period, cost_model, initial_cash)
    synthetic_trades = int(
        trades.get(INTERPOLATED_COLUMN, pd.Series(dtype=bool)).astype(bool).sum()
    )
    return {
        "strategy": summarize_equity(equity),
        "buy_and_hold": summarize_equity(buy_and_hold),
        "trade_count": int(len(trades)),
        "trade_statistics": _summarize_round_trips(trades),
        "desired_position_at_start": int(period.iloc[0]["desired_position"]),
        "trades_on_interpolated_days": synthetic_trades,
        "artifacts": {"equity": equity, "trades": trades, "buy_and_hold": buy_and_hold},
    }


def _compare(base: dict[str, object], filtered: dict[str, object]) -> dict[str, float]:
    """filteredとbaseのforward主要指標の差を計算する。

    Args:
        base: Donchian単独のforward結果。
        filtered: SMAフィルター付きのforward結果。

    Returns:
        CAGR差、最大DD改善幅、最終資産差を含む辞書。
    """

    base_metrics = base["strategy"]
    filtered_metrics = filtered["strategy"]
    return {
        "cagr_delta": filtered_metrics["cagr"] - base_metrics["cagr"],
        "max_drawdown_improvement": filtered_metrics["max_drawdown"]
        - base_metrics["max_drawdown"],
        "final_equity_delta": filtered_metrics["final_equity"]
        - base_metrics["final_equity"],
    }


def _classify(
    filtered: dict[str, object], comparison: dict[str, float]
) -> dict[str, object]:
    """性能条件と親実験の観測済み制約から暫定ステータスを作る。

    Args:
        filtered: SMAフィルター付きのforward結果。
        comparison: `_compare`が返す指標差分。

    Returns:
        性能条件、棄却条件、research status、promotion statusを含む辞書。
    """

    round_trips = filtered["trade_statistics"]["closed_round_trips"]
    candidate = {
        "max_drawdown_not_worse": comparison["max_drawdown_improvement"] >= 0,
        "cagr_not_worse": comparison["cagr_delta"] >= 0,
        "closed_round_trips_at_least_2": round_trips >= 2,
    }
    rejection = {
        "max_drawdown_worse_than_2_points": comparison["max_drawdown_improvement"]
        < -0.02,
        "cagr_worse_than_5_points": comparison["cagr_delta"] < -0.05,
    }
    performance_candidate = all(candidate.values())
    performance_rejected = any(rejection.values())
    if performance_rejected:
        status = "REJECTED"
    else:
        # 2026年データは親実験で観測済みなので、候補条件を満たしてもforward合格にしない。
        status = "INCONCLUSIVE"
    return {
        "performance_candidate_criteria": candidate,
        "performance_rejection_criteria": rejection,
        "performance_candidate": performance_candidate,
        "research_status": status,
        "promotion_status": (
            "NOT_ELIGIBLE" if status == "REJECTED" else "NEEDS_FORWARD_EVIDENCE"
        ),
        "independence_limitation": "同一2026年データを親実験EXP-2026-0007で観測済みのため、PASSED_FORWARD_TESTにはしない。",
    }


def main() -> None:
    """日足品質を確認し、baseとSMAフィルターをforward-like評価する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0007")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0009")
    )
    parser.add_argument("--initial-cash", type=Decimal, default=Decimal("1000"))
    args = parser.parse_args()

    paths = sorted(path for path in args.data_dir.glob("*.csv") if "1d" in path.name)
    if len(paths) != 1:
        raise ValueError(f"expected exactly one daily CSV: {paths}")
    frame = _read_daily_file(paths[0])
    quality = inspect_daily_data(frame)
    if quality["duplicate_count"] or quality["missing_intervals"]:
        raise ValueError(f"refusing to evaluate incomplete daily data: {quality}")
    if not quality["interpolated_rows"]:
        raise ValueError("EXP-2026-0009 requires inherited synthetic-day markers")
    if frame["event_time"].max() < FORWARD_END.normalize():
        raise ValueError("data does not reach the registered forward end date")

    base_frame = prepare_donchian_signals(frame, entry_window=55, exit_window=20)
    filtered_frame = prepare_donchian_regime_filter_signals(
        frame, entry_window=55, exit_window=20, regime_window=200
    )
    cost_cases = {
        "base": CostModel(Decimal("0.001"), Decimal("0.0005"), Decimal("0.0005")),
        "adverse": CostModel(Decimal("0.0015"), Decimal("0.0005"), Decimal("0.0005")),
        "stress": CostModel(Decimal("0.002"), Decimal("0.0005"), Decimal("0.0005")),
    }
    evaluations: dict[str, dict[str, object]] = {}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for cost_name, cost_model in cost_cases.items():
        base_result = _evaluate(base_frame, cost_model, args.initial_cash)
        filtered_result = _evaluate(filtered_frame, cost_model, args.initial_cash)
        base_artifacts = base_result.pop("artifacts")
        filtered_artifacts = filtered_result.pop("artifacts")
        if cost_name == "base":
            for prefix, artifacts in (
                ("base", base_artifacts),
                ("filtered", filtered_artifacts),
            ):
                artifacts["equity"].to_csv(
                    args.output_dir / f"{prefix}-equity.csv", index=False
                )
                artifacts["trades"].to_csv(
                    args.output_dir / f"{prefix}-trades.csv", index=False
                )
                artifacts["buy_and_hold"].to_csv(
                    args.output_dir / f"{prefix}-buy-and-hold-equity.csv", index=False
                )
        evaluations[cost_name] = {
            "cost_model": {
                "fee_rate": str(cost_model.fee_rate),
                "round_trip_spread": str(cost_model.round_trip_spread),
                "slippage_per_fill": str(cost_model.slippage_per_fill),
            },
            "base": base_result,
            "filtered": filtered_result,
            "comparison": _compare(base_result, filtered_result),
        }

    classification = _classify(
        evaluations["base"]["filtered"], evaluations["base"]["comparison"]
    )
    summary = {
        "experiment_id": "EXP-2026-0009",
        "evaluation_type": "post-selection forward-like test",
        "forward_window": {
            "start_utc": FORWARD_START.isoformat(),
            "end_utc": FORWARD_END.isoformat(),
        },
        "strategy_freeze": {
            "entry_window": 55,
            "exit_window": 20,
            "regime_window": 200,
            "regime_rule": "new entry only when close > SMA200",
            "execution": "next-day open",
        },
        "data_quality": quality,
        "evaluations": evaluations,
        "classification": classification,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
