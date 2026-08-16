#!/usr/bin/env python3
"""EXP-2026-0018の上限付きフィボナッチ・ロット追加を比較する。"""

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
from scripts.run_exp_2026_0016 import (  # noqa: E402
    _aggregate_deltas,
    _compare_metrics,
)


LOT_COUNTS_CONTROL = (1, 1, 1, 1)
LOT_COUNTS_VARIANT = (1, 1, 2, 3)
MAX_TOTAL_LOT_COUNT_CONTROL = 4
MAX_TOTAL_LOT_COUNT_VARIANT = 7


def _add_exposure_deltas(
    comparison: dict[str, object],
    control: dict[str, object],
    variant: dict[str, object],
) -> dict[str, object]:
    """ロット追加比較へ建玉エクスポージャー差を追加する。"""

    for field in ("max_position_quantity", "max_position_notional"):
        comparison[f"{field}_delta"] = str(
            Decimal(str(variant[field])) - Decimal(str(control[field]))
        )
    comparison["liquidation_count_delta"] = int(
        variant["liquidation_count"]
    ) - int(control["liquidation_count"])
    return comparison


def main() -> None:
    """固定ロット版と1,1,2,3ロット版を6銘柄で比較し成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0018")
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
                entry_lot_counts=LOT_COUNTS_CONTROL,
                max_entry_lot_count=MAX_TOTAL_LOT_COUNT_CONTROL,
            ),
        )
        variant = run_void_short_backtest(
            frame,
            funding,
            instrument,
            VoidShortBacktestConfig(
                initial_equity=args.initial_equity,
                costs=VoidShortCostModel(),
                entry_lot_counts=LOT_COUNTS_VARIANT,
                max_entry_lot_count=MAX_TOTAL_LOT_COUNT_VARIANT,
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
        comparisons[symbol] = _add_exposure_deltas(
            _compare_metrics(control_metrics, variant_metrics),
            control_metrics,
            variant_metrics,
        )
        for name, result in (("control", control), ("fib-lots-1_1_2_3", variant)):
            pd.DataFrame(result.events).to_csv(
                args.output_dir / f"{symbol}-{name}-events.csv", index=False
            )
            pd.DataFrame(result.equity_curve).to_csv(
                args.output_dir / f"{symbol}-{name}-equity.csv", index=False
            )

    payload = {
        "experiment_id": "EXP-2026-0018",
        "status": "BACKTEST_COMPLETED",
        "control": "entry_lot_counts=1,1,1,1; max_total_lot_count=4",
        "variant": "entry_lot_counts=1,1,2,3; max_total_lot_count=7",
        "comparison": comparisons,
        "aggregate_comparison": _aggregate_deltas(comparisons),
        "symbols": results,
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "EXP-2026-0015と同じ2時間足OHLC全量約定・固定5bpスリッページ・清算代理式を使う。",
            "同一バー内で複数の追加指値が触れた場合の実際の約定順序は観測できない。",
            "variantは4つの登録済み水準へ1,1,2,3ロットを割り当て、未約定水準の再配分を行わない。",
            "この比較はロット増加の差分診断であり、paper・shadow・live承認を意味しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
