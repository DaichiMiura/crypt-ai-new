#!/usr/bin/env python3
"""EXP-2026-0007の固定戦略を未観測期間でforward評価する。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypt_ai.research import (  # noqa: E402
    INTERPOLATED_COLUMN,
    CostModel,
    inspect_daily_data,
    prepare_donchian_bollinger_exit_signals,
    prepare_donchian_signals,
    run_backtest,
    run_buy_and_hold,
    summarize_equity,
)


FORWARD_START = pd.Timestamp("2026-01-01T00:00:00Z")
FORWARD_END = pd.Timestamp("2026-07-31T23:59:59Z")
WARMUP_START = pd.Timestamp("2020-01-01T00:00:00Z")


def _read_daily_file(path: Path) -> pd.DataFrame:
    """日足CSVを読み込み、時刻・価格・合成行フラグを正規化する。

    Args:
        path: `build_exp_2026_0007_dataset.py`が生成した日足CSV。

    Returns:
        固定戦略の計算に使える日足データ。

    Raises:
        ValueError: 必須数値列を数値化できない場合。
    """

    frame = pd.read_csv(path)
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame[INTERPOLATED_COLUMN] = (
        frame[INTERPOLATED_COLUMN]
        .fillna(False)
        .astype(str)
        .str.lower()
        .isin(["1", "true", "yes"])
    )
    return frame


def _summarize_round_trips(trades: pd.DataFrame) -> dict[str, object]:
    """約定履歴から往復取引数、勝率、期待値、費用を再計算する。

    Args:
        trades: `run_backtest`が返すBUY/SELLの約定履歴。

    Returns:
        往復取引の統計と未決済状態を含む辞書。

    Raises:
        ValueError: 現物ロングのBUY/SELL順序に違反する場合。
    """

    total_fees = Decimal("0")
    pnls: list[Decimal] = []
    open_buy = None
    for row in trades.itertuples(index=False):
        total_fees += Decimal(str(row.fee))
        if row.side == "BUY":
            if open_buy is not None:
                raise ValueError("trade history contains consecutive BUY fills")
            open_buy = row
        elif row.side == "SELL":
            if open_buy is None:
                raise ValueError("trade history contains SELL without a BUY")
            buy_cost = Decimal(str(open_buy.quantity)) * Decimal(
                str(open_buy.execution_price)
            ) + Decimal(str(open_buy.fee))
            sell_value = Decimal(str(row.quantity)) * Decimal(
                str(row.execution_price)
            ) - Decimal(str(row.fee))
            pnls.append(sell_value - buy_cost)
            open_buy = None
        else:
            raise ValueError(f"unknown trade side: {row.side}")
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    count = len(pnls)
    return {
        "closed_round_trips": count,
        "open_position_at_end": open_buy is not None,
        "win_rate": len(wins) / count if count else None,
        "average_win": str(sum(wins, Decimal("0")) / len(wins)) if wins else None,
        "average_loss": (
            str(sum(losses, Decimal("0")) / len(losses)) if losses else None
        ),
        "expectancy_per_closed_trade": (
            str(sum(pnls, Decimal("0")) / count) if count else None
        ),
        "total_fees": str(total_fees),
    }


def _evaluate(
    frame: pd.DataFrame, cost_model: CostModel, initial_cash: Decimal
) -> dict[str, object]:
    """forward期間の戦略、買い持ち、合成行影響を評価する。

    Args:
        frame: 全履歴でシグナル計算済みの日足データ。
        cost_model: fee、spread、slippageの固定仮定。
        initial_cash: forward開始時のpaper初期資金。

    Returns:
        forward期間の損益、取引統計、成果物を含む辞書。

    Raises:
        ValueError: forward期間が空、またはwarm-up履歴が不足する場合。
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
        "artifacts": {
            "equity": equity,
            "trades": trades,
            "buy_and_hold": buy_and_hold,
        },
    }


def _compare(base: dict[str, object], overlay: dict[str, object]) -> dict[str, float]:
    """overlayとbaseのforward主要指標の差を計算する。

    Args:
        base: Donchian単独のforward結果。
        overlay: Bollinger退出overlayのforward結果。

    Returns:
        CAGR、最大DD、最終資産の差分。
    """

    base_metrics = base["strategy"]
    overlay_metrics = overlay["strategy"]
    return {
        "cagr_delta": overlay_metrics["cagr"] - base_metrics["cagr"],
        "max_drawdown_improvement": overlay_metrics["max_drawdown"]
        - base_metrics["max_drawdown"],
        "final_equity_delta": overlay_metrics["final_equity"]
        - base_metrics["final_equity"],
    }


def _classify(
    overlay: dict[str, object], base: dict[str, object], comparison: dict[str, float]
) -> dict[str, object]:
    """事前登録したforward基準から研究上の暫定判定を作る。

    Args:
        overlay: overlayのforward結果。
        base: baseのforward結果。
        comparison: `_compare`が返す指標差分。

    Returns:
        候補条件、棄却条件、暫定research statusを含む辞書。
    """

    round_trips = overlay["trade_statistics"]["closed_round_trips"]
    candidate = {
        "data_and_accounting_assumed_pass": True,
        "max_drawdown_not_worse": comparison["max_drawdown_improvement"] >= 0,
        "cagr_not_worse": comparison["cagr_delta"] >= 0,
        "closed_round_trips_at_least_3": round_trips >= 3,
    }
    rejection = {
        "max_drawdown_worse_than_2_points": comparison["max_drawdown_improvement"]
        < -0.02,
        "cagr_worse_than_5_points": comparison["cagr_delta"] < -0.05,
    }
    if all(candidate.values()):
        status = "PASSED_FORWARD_TEST"
    elif any(rejection.values()):
        status = "REJECTED"
    else:
        status = "INCONCLUSIVE"
    promotion_status = "NOT_ELIGIBLE" if status == "REJECTED" else "NEEDS_FORWARD_EVIDENCE"
    return {
        "candidate_criteria": candidate,
        "rejection_criteria": rejection,
        "research_status": status,
        "promotion_status": promotion_status,
        "promotion_note": "Global proxyのforward結果だけではBinance Japanのpaper・shadow・liveを承認しない。",
    }


def main() -> None:
    """日足品質を確認し、固定戦略をforward期間で費用感度評価する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0007")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0007")
    )
    parser.add_argument("--initial-cash", type=Decimal, default=Decimal("1000"))
    args = parser.parse_args()

    paths = sorted(args.data_dir.glob("*.csv"))
    daily_paths = [path for path in paths if "1d" in path.name]
    if len(daily_paths) != 1:
        raise ValueError(f"expected exactly one daily CSV: {daily_paths}")
    frame = _read_daily_file(daily_paths[0])
    quality = inspect_daily_data(frame)
    if quality["duplicate_count"] or quality["missing_intervals"]:
        raise ValueError(f"refusing to evaluate incomplete daily data: {quality}")
    if frame["event_time"].max() < FORWARD_END.normalize():
        raise ValueError("data does not reach the registered forward end date")

    base_frame = prepare_donchian_signals(frame, entry_window=55, exit_window=20)
    overlay_frame = prepare_donchian_bollinger_exit_signals(
        frame, entry_window=55, band_window=20, std_multiplier=2.0
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
        overlay_result = _evaluate(overlay_frame, cost_model, args.initial_cash)
        base_artifacts = base_result.pop("artifacts")
        overlay_artifacts = overlay_result.pop("artifacts")
        if cost_name == "base":
            for prefix, artifacts in (
                ("base", base_artifacts),
                ("overlay", overlay_artifacts),
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
        comparison = _compare(base_result, overlay_result)
        evaluations[cost_name] = {
            "cost_model": {
                "fee_rate": str(cost_model.fee_rate),
                "round_trip_spread": str(cost_model.round_trip_spread),
                "slippage_per_fill": str(cost_model.slippage_per_fill),
            },
            "base": base_result,
            "overlay": overlay_result,
            "comparison": comparison,
        }

    classification = _classify(
        evaluations["base"]["overlay"],
        evaluations["base"]["base"],
        evaluations["base"]["comparison"],
    )
    summary = {
        "experiment_id": "EXP-2026-0007",
        "evaluation_type": "preregistered forward test",
        "forward_window": {
            "start_utc": FORWARD_START.isoformat(),
            "end_utc": FORWARD_END.isoformat(),
        },
        "strategy_freeze": {
            "entry_window": 55,
            "base_exit_window": 20,
            "overlay_band_window": 20,
            "overlay_std_multiplier": 2.0,
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
