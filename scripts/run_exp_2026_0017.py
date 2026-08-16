#!/usr/bin/env python3
"""EXP-2026-0017の通常損切り1.5 ATR比較を6銘柄で実行する。"""

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


NORMAL_STOP_CONTROL_ATR = Decimal("1")
NORMAL_STOP_VARIANT_ATR = Decimal("1.5")


def main() -> None:
    """通常損切り1.0 ATRと1.5 ATRを比較し成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0017")
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
                normal_stop_pullback_atr=NORMAL_STOP_CONTROL_ATR,
            ),
        )
        variant = run_void_short_backtest(
            frame,
            funding,
            instrument,
            VoidShortBacktestConfig(
                initial_equity=args.initial_equity,
                costs=VoidShortCostModel(),
                normal_stop_pullback_atr=NORMAL_STOP_VARIANT_ATR,
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
        for name, result in (("control", control), ("stop-1_5atr", variant)):
            pd.DataFrame(result.events).to_csv(
                args.output_dir / f"{symbol}-{name}-events.csv", index=False
            )
            pd.DataFrame(result.equity_curve).to_csv(
                args.output_dir / f"{symbol}-{name}-equity.csv", index=False
            )

    payload = {
        "experiment_id": "EXP-2026-0017",
        "status": "BACKTEST_COMPLETED",
        "control": f"normal_stop_pullback_atr={NORMAL_STOP_CONTROL_ATR}",
        "variant": f"normal_stop_pullback_atr={NORMAL_STOP_VARIANT_ATR}",
        "comparison": comparisons,
        "aggregate_comparison": _aggregate_deltas(comparisons),
        "symbols": results,
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "EXP-2026-0015と同じ2時間足OHLC全量約定・固定5bpスリッページ・清算代理式を使う。",
            "通常損切り反落幅以外の条件は同一だが、variantでは保有期間と退出理由が変化する。",
            "この比較は損切り幅の診断であり、インデックス超過やpaper承認を意味しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
