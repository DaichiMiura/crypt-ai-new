#!/usr/bin/env python3
"""EXP-2026-0033のロングとベーシス・ヘッジを統合して比較する。"""

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

from crypt_ai.basis_backtest import (  # noqa: E402
    BasisBacktestConfig,
    BasisBacktestResult,
    BasisCostModel,
    run_basis_backtest,
)
from crypt_ai.void_short_accounting import VoidShortCostModel  # noqa: E402
from scripts.run_exp_2026_0015 import _load_symbol  # noqa: E402
from scripts.run_exp_2026_0023 import (  # noqa: E402
    _run_long_sleeve,
    _summarize_equity_curve,
)
from scripts.run_exp_2026_0032 import (  # noqa: E402
    _prepare_frames,
    SYMBOLS as BASIS_SYMBOLS,
)


EXPERIMENT_ID = "EXP-2026-0033"
SYMBOLS = BASIS_SYMBOLS
INITIAL_EQUITY = Decimal("1000")
RESERVE_CASH = Decimal("200")
DEPLOYABLE_EQUITY = INITIAL_EQUITY - RESERVE_CASH
PAIR_NOTIONAL = Decimal("24")
ARM_BASIS_BUDGET = {
    "long_only": Decimal("0"),
    "hedge_5pct": Decimal("50"),
    "hedge_10pct": Decimal("100"),
    "hedge_20pct": Decimal("200"),
}
ARM_MAX_PAIRS = {
    "long_only": 0,
    "hedge_5pct": 1,
    "hedge_10pct": 2,
    "hedge_20pct": 4,
}
INDEX_BENCHMARK = INITIAL_EQUITY * (Decimal("1.10") ** 4)


def _decimal(value: object) -> Decimal:
    """入力値を二進浮動小数点を経由せずDecimalへ変換する。"""

    return Decimal(str(value))


def _max_drawdown(
    equity_curve: list[dict[str, object]], initial_equity: Decimal
) -> Decimal:
    """評価額曲線から最大ドローダウンを計算する。"""

    peak = initial_equity
    drawdown = Decimal("0")
    for row in equity_curve:
        equity = _decimal(row["equity"])
        peak = max(peak, equity)
        drawdown = min(drawdown, equity / peak - Decimal("1"))
    return drawdown


def _benchmark(final_equity: Decimal) -> dict[str, object]:
    """4年間・年率10%複利のインデックス基準と比較する。"""

    return {
        "annual_return": "0.10",
        "years": 4,
        "benchmark_final_equity": str(INDEX_BENCHMARK),
        "excess_equity": str(final_equity - INDEX_BENCHMARK),
        "beats_benchmark": final_equity >= INDEX_BENCHMARK,
    }


def _validate_common_timestamps(
    frames: dict[str, pd.DataFrame], *, name: str
) -> list[pd.Timestamp]:
    """複数銘柄の評価時刻が同一であることを検査する。"""

    if not frames:
        raise ValueError(f"{name} frames must not be empty")
    timestamp_sets = {
        symbol: tuple(frame["event_time"])
        for symbol, frame in frames.items()
    }
    first = next(iter(timestamp_sets.values()))
    if any(timestamps != first for timestamps in timestamp_sets.values()):
        raise ValueError(f"{name} timestamps differ across symbols")
    return list(first)


def _empty_pair_curve(timestamps: list[pd.Timestamp]) -> list[dict[str, object]]:
    """basis枠を使わないarmのゼロ評価額曲線を作る。"""

    return [
        {
            "event_time": timestamp.isoformat(),
            "cash": "0",
            "equity": "0",
            "open_pair_count": 0,
            "gross_pair_notional": "0",
        }
        for timestamp in timestamps
    ]


def _run_pair_sleeve(
    basis_frames: dict[str, pd.DataFrame],
    *,
    initial_equity: Decimal,
    max_concurrent_pairs: int,
) -> BasisBacktestResult | None:
    """指定armのbasisスリーブを実行し、long-onlyでは空枠を返す。"""

    if max_concurrent_pairs == 0:
        return None
    return run_basis_backtest(
        basis_frames,
        BasisBacktestConfig(
            initial_equity=initial_equity,
            reserve_cash=Decimal("0"),
            pair_notional=PAIR_NOTIONAL,
            max_concurrent_pairs=max_concurrent_pairs,
            # シグナルは事前登録済みのbasis_framesをそのまま使う。
            costs=BasisCostModel(),
        ),
    )


def _combine_arm(
    long_results: dict[str, dict[str, object]],
    pair_result: BasisBacktestResult | None,
    *,
    pair_budget: Decimal,
    timestamps: list[pd.Timestamp],
) -> dict[str, object]:
    """4銘柄ロング、basisスリーブ、予備資金を一つの曲線へ合算する。"""

    long_curves = {
        symbol: result["equity_curve"]
        for symbol, result in long_results.items()
    }
    expected_times = [timestamp.isoformat() for timestamp in timestamps]
    for symbol, curve in long_curves.items():
        if [row["event_time"] for row in curve] != expected_times:
            raise ValueError(f"long equity timestamps differ: {symbol}")
    pair_curve = (
        list(pair_result.equity_curve)
        if pair_result is not None
        else _empty_pair_curve(timestamps)
    )
    if [row["event_time"] for row in pair_curve] != expected_times:
        raise ValueError("pair equity timestamps differ from long equity")

    curve: list[dict[str, object]] = []
    for index, timestamp in enumerate(expected_times):
        long_equity = sum(
            (_decimal(curve_rows[index]["equity"]) for curve_rows in long_curves.values()),
            Decimal("0"),
        )
        long_notional = sum(
            (
                _decimal(curve_rows[index]["position_notional"])
                for curve_rows in long_curves.values()
            ),
            Decimal("0"),
        )
        pair_row = pair_curve[index]
        curve.append(
            {
                "event_time": timestamp,
                "equity": str(
                    RESERVE_CASH + long_equity + _decimal(pair_row["equity"])
                ),
                "position_notional": str(
                    long_notional + _decimal(pair_row["gross_pair_notional"])
                ),
                "position_quantity": str(
                    sum(
                        (
                            _decimal(curve_rows[index]["position_quantity"])
                            for curve_rows in long_curves.values()
                        ),
                        Decimal("0"),
                    )
                    + Decimal(str(pair_row["open_pair_count"])),
                ),
                "long_equity": str(long_equity),
                "basis_equity": str(pair_row["equity"]),
                "reserve_cash": str(RESERVE_CASH),
                "open_pair_count": int(pair_row["open_pair_count"]),
            }
        )

    events: list[dict[str, object]] = []
    for symbol, result in long_results.items():
        events.extend({**event, "sleeve": "long", "symbol": symbol} for event in result["events"])
    if pair_result is not None:
        events.extend({**event, "sleeve": "basis"} for event in pair_result.events)
    events.sort(key=lambda event: (str(event["event_time"]), str(event.get("sleeve", ""))))

    total_fees = sum(
        (_decimal(result["metrics"]["total_fees"]) for result in long_results.values()),
        Decimal("0"),
    )
    total_funding = sum(
        (
            _decimal(result["metrics"]["total_funding_cash_flow"])
            for result in long_results.values()
        ),
        Decimal("0"),
    )
    if pair_result is not None:
        total_fees += _decimal(pair_result.metrics["total_fees"])
        total_funding += _decimal(pair_result.metrics["funding_cash_flow"])
    metrics = _summarize_equity_curve(
        symbol="PORTFOLIO",
        initial_equity=INITIAL_EQUITY,
        equity_curve=curve,
        events=events,
        total_fees=total_fees,
        funding_cash_flow=total_funding,
        max_position_notional=max(
            (_decimal(row["position_notional"]) for row in curve),
            default=Decimal("0"),
        ),
    )
    metrics["basis_budget"] = str(pair_budget)
    metrics["reserve_cash"] = str(RESERVE_CASH)
    metrics["benchmark"] = _benchmark(_decimal(metrics["final_equity"]))
    metrics["long_entry_count"] = sum(
        int(result["metrics"]["entry_count"]) for result in long_results.values()
    )
    metrics["long_exit_count"] = sum(
        int(result["metrics"]["exit_count"]) for result in long_results.values()
    )
    metrics["basis_entry_count"] = (
        sum(event["event_type"] == "ENTRY_PAIR" for event in events)
        if pair_result is not None
        else 0
    )
    metrics["basis_exit_count"] = (
        sum(event["event_type"] == "EXIT_PAIR" for event in events)
        if pair_result is not None
        else 0
    )
    metrics["basis_funding_cash_flow"] = (
        str(pair_result.metrics["funding_cash_flow"])
        if pair_result is not None
        else "0"
    )
    return {
        "metrics": metrics,
        "events": events,
        "equity_curve": curve,
        "long_sleeves": {
            symbol: result["metrics"] for symbol, result in long_results.items()
        },
        "basis_sleeve": pair_result.metrics if pair_result is not None else None,
    }


def _run_arm(
    long_frames: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
    basis_frames: dict[str, pd.DataFrame],
    *,
    arm: str,
    timestamps: list[pd.Timestamp],
) -> dict[str, object]:
    """固定資金配分armのロング・basis統合バックテストを実行する。"""

    if arm not in ARM_BASIS_BUDGET:
        raise ValueError(f"unknown arm: {arm}")
    pair_budget = ARM_BASIS_BUDGET[arm]
    long_budget = DEPLOYABLE_EQUITY - pair_budget
    per_symbol_equity = long_budget / Decimal(len(SYMBOLS))
    costs = VoidShortCostModel()
    long_results = {
        symbol: _run_long_sleeve(
            frame,
            funding,
            symbol=symbol,
            initial_equity=per_symbol_equity,
            costs=costs,
        )
        for symbol, (frame, funding) in long_frames.items()
    }
    pair_result = _run_pair_sleeve(
        basis_frames,
        initial_equity=pair_budget,
        max_concurrent_pairs=ARM_MAX_PAIRS[arm],
    )
    return _combine_arm(
        long_results,
        pair_result,
        pair_budget=pair_budget,
        timestamps=timestamps,
    )


def _compare_to_baseline(
    baseline: dict[str, object], candidate: dict[str, object]
) -> dict[str, object]:
    """候補armとlong-only armの主要指標差を計算する。"""

    return {
        "final_equity_delta": str(
            _decimal(candidate["final_equity"]) - _decimal(baseline["final_equity"])
        ),
        "max_drawdown_delta": str(
            _decimal(candidate["max_drawdown"]) - _decimal(baseline["max_drawdown"])
        ),
        "total_fees_delta": str(
            _decimal(candidate["total_fees"]) - _decimal(baseline["total_fees"])
        ),
        "total_funding_cash_flow_delta": str(
            _decimal(candidate["total_funding_cash_flow"])
            - _decimal(baseline["total_funding_cash_flow"])
        ),
        "final_equity_improved": _decimal(candidate["final_equity"])
        > _decimal(baseline["final_equity"]),
        "max_drawdown_improved": _decimal(candidate["max_drawdown"])
        > _decimal(baseline["max_drawdown"]),
    }


def main() -> None:
    """4銘柄のロングとbasis armを実行し、監査成果物を保存する。"""

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
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0033")
    )
    args = parser.parse_args()

    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    long_frames = {
        symbol: _load_symbol(args.perpetual_dir, metadata, symbol)[:2]
        for symbol in SYMBOLS
    }
    for symbol, (frame, _) in long_frames.items():
        if frame.empty or frame["event_time"].isna().any():
            raise ValueError(f"invalid long input: {symbol}")
    basis_frames = _prepare_frames(args.spot_dir, args.perpetual_dir)
    basis_timestamps = _validate_common_timestamps(basis_frames, name="basis")
    timestamps = basis_timestamps

    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = {
        arm: _run_arm(
            long_frames,
            basis_frames,
            arm=arm,
            timestamps=timestamps,
        )
        for arm in ARM_BASIS_BUDGET
    }
    baseline = results["long_only"]["metrics"]
    comparisons = {
        arm: _compare_to_baseline(baseline, result["metrics"])
        for arm, result in results.items()
        if arm != "long_only"
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
            "pair_notional_per_leg": str(PAIR_NOTIONAL),
            "basis_budgets": {
                arm: str(budget) for arm, budget in ARM_BASIS_BUDGET.items()
            },
            "max_concurrent_pairs": ARM_MAX_PAIRS,
            "long_allocation_per_symbol": "armごとに(long deployable - basis budget) / 4",
            "compounding": False,
            "cross_sleeve_reallocation": False,
        },
        "arms": {
            arm: {
                "basis_budget": str(ARM_BASIS_BUDGET[arm]),
                "max_concurrent_pairs": ARM_MAX_PAIRS[arm],
                "metrics": result["metrics"],
                "long_sleeves": result["long_sleeves"],
                "basis_sleeve": result["basis_sleeve"],
            }
            for arm, result in results.items()
        },
        "comparison_to_long_only": comparisons,
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "4銘柄のロングスリーブへ均等配分した固定資金モデルであり、銘柄間の動的な再配分はしない。",
            "ロングとbasisは独立スリーブで会計し、片脚約定、清算、数量刻み、証拠金、借入・送金費用を完全には再現しない。",
            "現物データが取得できた4銘柄だけを共通ユニバースとした。",
            "単一の過去期間に対する診断であり、paper・shadow・live運用を承認しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
