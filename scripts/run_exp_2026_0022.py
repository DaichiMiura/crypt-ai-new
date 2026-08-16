#!/usr/bin/env python3
"""EXP-2026-0022のSMA200接近条件を比較する。"""

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
from scripts.run_exp_2026_0018 import _add_exposure_deltas  # noqa: E402


SMA_PROXIMITY_CONTROL = False
SMA_PROXIMITY_VARIANT = True
LOT_COUNTS = (1, 1, 1, 1)
MAX_TOTAL_LOT_COUNT = 4


def _add_entry_count_delta(
    comparison: dict[str, object],
    control: dict[str, object],
    variant: dict[str, object],
) -> dict[str, object]:
    """SMA接近比較へエントリー数差を追加する。"""

    comparison["entry_count_delta"] = int(variant["entry_count"]) - int(
        control["entry_count"]
    )
    return comparison


def main() -> None:
    """SMA接近条件なし・ありを6銘柄で比較し成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0022")
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
                require_sma_proximity=SMA_PROXIMITY_CONTROL,
                entry_lot_counts=LOT_COUNTS,
                max_entry_lot_count=MAX_TOTAL_LOT_COUNT,
            ),
        )
        variant = run_void_short_backtest(
            frame,
            funding,
            instrument,
            VoidShortBacktestConfig(
                initial_equity=args.initial_equity,
                costs=VoidShortCostModel(),
                require_sma_proximity=SMA_PROXIMITY_VARIANT,
                entry_lot_counts=LOT_COUNTS,
                max_entry_lot_count=MAX_TOTAL_LOT_COUNT,
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
        comparisons[symbol] = _add_entry_count_delta(
            _add_exposure_deltas(
                _compare_metrics(control_metrics, variant_metrics),
                control_metrics,
                variant_metrics,
            ),
            control_metrics,
            variant_metrics,
        )
        for name, result in (("control", control), ("sma-proximity", variant)):
            pd.DataFrame(result.events).to_csv(
                args.output_dir / f"{symbol}-{name}-events.csv", index=False
            )
            pd.DataFrame(result.equity_curve).to_csv(
                args.output_dir / f"{symbol}-{name}-equity.csv", index=False
            )

    payload = {
        "experiment_id": "EXP-2026-0022",
        "status": "BACKTEST_COMPLETED",
        "control": "require_sma_proximity=false; entry_lot_counts=1,1,1,1",
        "variant": "require_sma_proximity=true; SMA200 proximity=0.5 ATR; entry_lot_counts=1,1,1,1",
        "comparison": comparisons,
        "aggregate_comparison": _aggregate_deltas(comparisons),
        "symbols": results,
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "EXP-2026-0015と同じ2時間足OHLC全量約定・固定5bpスリッページ・清算代理式を使う。",
            "variantは準備完了した確定足のhighがSMA200 - 0.5 ATR以上で、closeがSMA200以下の場合だけ次足に指値を作る。",
            "SMAの種類、接近幅、価格測定方法の最適化や複数variantの探索は行っていない。",
            "この比較はSMA接近条件の差分診断であり、paper・shadow・live承認を意味しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
