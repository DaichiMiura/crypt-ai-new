#!/usr/bin/env python3
"""EXP-2026-0016の通常損切り無効化比較を6銘柄で実行する。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
import sys
from statistics import median

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.void_short import VOID_SHORT_SYMBOLS  # noqa: E402
from crypt_ai.void_short_accounting import VoidShortCostModel  # noqa: E402
from crypt_ai.void_short_backtest import (  # noqa: E402
    VoidShortBacktestConfig,
    run_void_short_backtest,
)
from scripts.run_exp_2026_0015 import (  # noqa: E402
    _compare_index_benchmark,
    _load_symbol,
)


def _compare_metrics(
    control: dict[str, object], variant: dict[str, object]
) -> dict[str, object]:
    """通常損切りvariantとcontrolの主要指標差を計算する。

    Args:
        control: 通常損切り有効版のmetrics。
        variant: 通常損切り無効版のmetrics。

    Returns:
        最終資産、収益率、最大DD、イベント数のvariant-control差。

    Raises:
        ValueError: 必須指標が欠落している場合。
    """

    fields = ("final_equity", "net_pnl", "max_drawdown")
    if any(field not in control or field not in variant for field in fields):
        raise ValueError("control and variant metrics are incomplete")
    return {
        "final_equity_delta": str(
            Decimal(str(variant["final_equity"]))
            - Decimal(str(control["final_equity"]))
        ),
        "net_pnl_delta": str(
            Decimal(str(variant["net_pnl"]))
            - Decimal(str(control["net_pnl"]))
        ),
        "max_drawdown_delta": str(
            Decimal(str(variant["max_drawdown"]))
            - Decimal(str(control["max_drawdown"]))
        ),
        "normal_stop_count_delta": int(variant["normal_stop_count"])
        - int(control["normal_stop_count"]),
        "emergency_stop_count_delta": int(variant["emergency_stop_count"])
        - int(control["emergency_stop_count"]),
        "time_exit_count_delta": int(variant["time_exit_count"])
        - int(control["time_exit_count"]),
    }


def _aggregate_deltas(comparisons: dict[str, dict[str, object]]) -> dict[str, object]:
    """6銘柄のvariant-control差を中央値と合算で集計する。"""

    equity_deltas = [
        Decimal(str(item["final_equity_delta"])) for item in comparisons.values()
    ]
    pnl_deltas = [Decimal(str(item["net_pnl_delta"])) for item in comparisons.values()]
    return {
        "symbol_count": len(comparisons),
        "symbols_improved": sum(value > 0 for value in equity_deltas),
        "symbols_worsened": sum(value < 0 for value in equity_deltas),
        "median_final_equity_delta": str(median(equity_deltas)),
        "median_net_pnl_delta": str(median(pnl_deltas)),
        "sum_final_equity_delta": str(sum(equity_deltas, Decimal("0"))),
        "sum_net_pnl_delta": str(sum(pnl_deltas, Decimal("0"))),
    }


def main() -> None:
    """通常損切り有効版と無効版を同一入力で比較し成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0016")
    )
    parser.add_argument("--initial-equity", type=Decimal, default=Decimal("1000"))
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, object]] = {}
    comparisons: dict[str, dict[str, object]] = {}

    for symbol in sorted(VOID_SHORT_SYMBOLS):
        frame, funding, instrument = _load_symbol(args.data_dir, metadata, symbol)
        control = run_void_short_backtest(
            frame,
            funding,
            instrument,
            VoidShortBacktestConfig(
                initial_equity=args.initial_equity,
                costs=VoidShortCostModel(),
                normal_stop_enabled=True,
            ),
        )
        variant = run_void_short_backtest(
            frame,
            funding,
            instrument,
            VoidShortBacktestConfig(
                initial_equity=args.initial_equity,
                costs=VoidShortCostModel(),
                normal_stop_enabled=False,
            ),
        )
        control_metrics = dict(control.metrics)
        variant_metrics = dict(variant.metrics)
        control_metrics["index_benchmark"] = _compare_index_benchmark(
            initial_equity=args.initial_equity,
            final_equity=Decimal(str(control.metrics["final_equity"])),
        )
        variant_metrics["index_benchmark"] = _compare_index_benchmark(
            initial_equity=args.initial_equity,
            final_equity=Decimal(str(variant.metrics["final_equity"])),
        )
        results[symbol] = {"control": control_metrics, "variant": variant_metrics}
        comparisons[symbol] = _compare_metrics(control_metrics, variant_metrics)
        for name, result in (("control", control), ("no-normal-stop", variant)):
            pd.DataFrame(result.events).to_csv(
                args.output_dir / f"{symbol}-{name}-events.csv", index=False
            )
            pd.DataFrame(result.equity_curve).to_csv(
                args.output_dir / f"{symbol}-{name}-equity.csv", index=False
            )

    payload = {
        "experiment_id": "EXP-2026-0016",
        "status": "BACKTEST_COMPLETED",
        "control": "normal_stop_enabled=true",
        "variant": "normal_stop_enabled=false",
        "comparison": comparisons,
        "aggregate_comparison": _aggregate_deltas(comparisons),
        "symbols": results,
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "EXP-2026-0015と同じ2時間足OHLC全量約定・固定5bpスリッページ・清算代理式を使う。",
            "通常損切り以外の条件は同一だが、variantでは保有期間と緊急停止発生が変化する。",
            "この比較は損切りの寄与診断であり、インデックス超過やpaper承認を意味しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
