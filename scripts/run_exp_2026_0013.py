#!/usr/bin/env python3
"""EXP-2026-0013の固定ATR退出を2026年forward-like期間で評価する。"""

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
    prepare_atr_trailing_exit_signals,
    run_backtest,
    run_buy_and_hold,
    summarize_equity,
)
from scripts.run_exp_2026_0012 import (  # noqa: E402
    _read_daily_file,
    _summarize_round_trips,
)


FORWARD_START = pd.Timestamp("2026-01-01T00:00:00Z")
FORWARD_END = pd.Timestamp("2026-07-31T23:59:59Z")
WARMUP_START = pd.Timestamp("2020-01-01T00:00:00Z")


def _evaluate(
    frame: pd.DataFrame,
    cost_model: CostModel,
    initial_cash: Decimal,
    mode: str,
) -> dict[str, object]:
    """登録期間でbaselineまたはATR退出を独立資金評価する。

    Args:
        frame: 全履歴でシグナルとATR状態を計算済みの日足データ。
        cost_model: fee、spread、slippageの固定仮定。
        initial_cash: 期間開始時にリセットする初期資金。
        mode: `baseline`または`atr`。

    Returns:
        損益、取引統計、ATRイベント、成果物を含む辞書。

    Raises:
        ValueError: warm-up、期間行数、またはmodeが不正な場合。
    """

    if frame["event_time"].min() > WARMUP_START:
        raise ValueError("warm-up history starts after the registered date")
    period = frame[
        (frame["event_time"] >= FORWARD_START)
        & (frame["event_time"] <= FORWARD_END)
    ].reset_index(drop=True).copy()
    expected_rows = (FORWARD_END.normalize() - FORWARD_START).days + 1
    if len(period) != expected_rows:
        raise ValueError(
            f"forward-like period must contain {expected_rows} rows: {len(period)}"
        )
    flat_days = int(
        (
            (period["desired_atr_position"] == 0)
            & (period["desired_position"] == 1)
        ).sum()
    )
    if mode == "atr":
        period["desired_position"] = period["desired_atr_position"]
    elif mode != "baseline":
        raise ValueError(f"unknown evaluation mode: {mode}")
    equity, trades = run_backtest(period, cost_model, initial_cash)
    buy_and_hold = run_buy_and_hold(period, cost_model, initial_cash)
    return {
        "strategy": summarize_equity(equity),
        "buy_and_hold": summarize_equity(buy_and_hold),
        "trade_count": int(len(trades)),
        "trade_statistics": _summarize_round_trips(trades),
        "desired_position_at_start": int(period.iloc[0]["desired_position"]),
        "atr_exit_signals": int(period["atr_exit_signal"].sum()),
        "atr_exit_signals_on_interpolated_days": int(
            (period["atr_exit_signal"] & period[INTERPOLATED_COLUMN]).sum()
        ),
        "trades_on_interpolated_days": int(
            trades.get(INTERPOLATED_COLUMN, pd.Series(dtype=bool)).astype(bool).sum()
        ),
        "days_flat_while_baseline_long": flat_days,
        "artifacts": {"equity": equity, "trades": trades, "buy_and_hold": buy_and_hold},
    }


def _compare(baseline: dict[str, object], atr: dict[str, object]) -> dict[str, float]:
    """ATR退出とbaselineの主要指標差を計算する。

    Args:
        baseline: Donchian 20日安値退出の結果。
        atr: ATR追随退出の結果。

    Returns:
        CAGR差、DD改善幅、最終資産差・維持率。
    """

    baseline_metrics = baseline["strategy"]
    atr_metrics = atr["strategy"]
    return {
        "cagr_delta": atr_metrics["cagr"] - baseline_metrics["cagr"],
        "max_drawdown_improvement": atr_metrics["max_drawdown"]
        - baseline_metrics["max_drawdown"],
        "final_equity_delta": atr_metrics["final_equity"]
        - baseline_metrics["final_equity"],
        "final_equity_retention": atr_metrics["final_equity"]
        / baseline_metrics["final_equity"],
    }


def _classify(
    atr: dict[str, object], comparison: dict[str, float]
) -> dict[str, object]:
    """性能基準と独立性制約からforward-like判定を作る。

    Args:
        atr: ATR退出の評価結果。
        comparison: `_compare`が返すbaselineとの差分。

    Returns:
        候補・棄却条件、研究・昇格ステータス。
    """

    candidate = {
        "max_drawdown_not_worse": comparison["max_drawdown_improvement"] >= 0,
        "final_equity_retention_at_least_90_percent": comparison[
            "final_equity_retention"
        ]
        >= 0.90,
        "at_least_1_atr_exit_signal": atr["atr_exit_signals"] >= 1,
    }
    rejection = {
        "max_drawdown_worse_than_2_points": comparison[
            "max_drawdown_improvement"
        ]
        < -0.02,
        "cagr_worse_than_5_points": comparison["cagr_delta"] < -0.05,
        "final_equity_retention_below_80_percent": comparison[
            "final_equity_retention"
        ]
        < 0.80,
    }
    performance_candidate = all(candidate.values())
    if any(rejection.values()):
        status = "REJECTED"
    else:
        status = "INCONCLUSIVE"
    return {
        "performance_candidate_criteria": candidate,
        "performance_rejection_criteria": rejection,
        "performance_candidate": performance_candidate,
        "research_status": status,
        "promotion_status": (
            "NOT_ELIGIBLE" if status == "REJECTED" else "NEEDS_FORWARD_EVIDENCE"
        ),
        "independence_limitation": "2026年1〜7月は別実験で観測済みのためPASSED_FORWARD_TESTにはしない。",
    }


def main() -> None:
    """日足品質を確認し、固定ATR退出をforward-like評価する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0007")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0013")
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
    if frame["event_time"].max() < FORWARD_END.normalize():
        raise ValueError("data does not reach the registered period end")
    signals = prepare_atr_trailing_exit_signals(frame)
    cost_cases = {
        "base": CostModel(Decimal("0.001"), Decimal("0.0005"), Decimal("0.0005")),
        "adverse": CostModel(Decimal("0.0015"), Decimal("0.0005"), Decimal("0.0005")),
        "stress": CostModel(Decimal("0.002"), Decimal("0.0005"), Decimal("0.0005")),
    }
    evaluations: dict[str, dict[str, object]] = {}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for cost_name, cost_model in cost_cases.items():
        baseline = _evaluate(signals, cost_model, args.initial_cash, "baseline")
        atr = _evaluate(signals, cost_model, args.initial_cash, "atr")
        baseline_artifacts = baseline.pop("artifacts")
        atr_artifacts = atr.pop("artifacts")
        if cost_name == "base":
            for prefix, artifacts in (
                ("baseline", baseline_artifacts),
                ("atr", atr_artifacts),
            ):
                for name, artifact in artifacts.items():
                    artifact.to_csv(
                        args.output_dir
                        / f"{prefix}-{name.replace('_', '-')}.csv",
                        index=False,
                    )
        comparison = _compare(baseline, atr)
        evaluations[cost_name] = {
            "cost_model": {
                "fee_rate": str(cost_model.fee_rate),
                "round_trip_spread": str(cost_model.round_trip_spread),
                "slippage_per_fill": str(cost_model.slippage_per_fill),
            },
            "baseline": baseline,
            "atr": atr,
            "comparison": comparison,
        }
    summary = {
        "experiment_id": "EXP-2026-0013",
        "evaluation_type": "post-selection forward-like ATR exit diagnostic",
        "forward_like_window": {
            "start_utc": FORWARD_START.isoformat(),
            "end_utc": FORWARD_END.isoformat(),
        },
        "strategy_freeze": {
            "entry_window": 55,
            "baseline_exit_window": 20,
            "regime_window": 200,
            "atr_window": 20,
            "atr_average": "simple moving average",
            "atr_multiplier": 3.0,
            "execution": "next-day open",
        },
        "data_quality": quality,
        "evaluations": evaluations,
        "classification": _classify(
            evaluations["base"]["atr"], evaluations["base"]["comparison"]
        ),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
