#!/usr/bin/env python3
"""EXP-2026-0027で下落ブレイク型ショートのストップ幅を比較する。"""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
from pathlib import Path
import sys
from statistics import median

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.void_short import VOID_SHORT_SYMBOLS  # noqa: E402
from crypt_ai.void_short_accounting import VoidShortCostModel  # noqa: E402
from scripts.run_exp_2026_0015 import _load_symbol  # noqa: E402
from scripts.run_exp_2026_0025 import (  # noqa: E402
    SHORT_BREAKOUT_STOP_ATR,
    _run_breakout_short,
)
from scripts.run_exp_2026_0026 import (  # noqa: E402
    reconstruct_trades,
    summarize_trades,
)


WIDER_STOP_ATR = Decimal("6")


def _compare_metrics(
    control: dict[str, object],
    variant: dict[str, object],
    control_trades: list[dict[str, object]],
    variant_trades: list[dict[str, object]],
) -> dict[str, object]:
    """広いストップvariantとcontrolの損益・退出差を計算する。"""

    control_trade_summary = summarize_trades(control_trades)
    variant_trade_summary = summarize_trades(variant_trades)
    result = {
        "final_equity_delta": str(
            Decimal(str(variant["final_equity"]))
            - Decimal(str(control["final_equity"]))
        ),
        "closed_net_pnl_delta": str(
            Decimal(str(variant_trade_summary["closed_net_pnl"]))
            - Decimal(str(control_trade_summary["closed_net_pnl"]))
        ),
        "max_drawdown_delta": str(
            Decimal(str(variant["max_drawdown"]))
            - Decimal(str(control["max_drawdown"]))
        ),
        "entry_count_delta": int(variant["entry_count"])
        - int(control["entry_count"]),
        "stop_loss_count_delta": int(variant["stop_loss_count"])
        - int(control["stop_loss_count"]),
        "sma_exit_count_delta": int(variant["sma_exit_count"])
        - int(control["sma_exit_count"]),
        "time_exit_count_delta": int(variant["time_exit_count"])
        - int(control["time_exit_count"]),
        "max_position_notional_delta": str(
            Decimal(str(variant["max_position_notional"]))
            - Decimal(str(control["max_position_notional"]))
        ),
        "funding_cash_flow_delta": str(
            Decimal(str(variant["total_funding_cash_flow"]))
            - Decimal(str(control["total_funding_cash_flow"]))
        ),
        "fees_delta": str(
            Decimal(str(variant["total_fees"]))
            - Decimal(str(control["total_fees"]))
        ),
        "beats_index_benchmark": bool(
            variant["index_benchmark"]["beats_benchmark"]
        ),
    }
    return result


def _aggregate(comparisons: dict[str, dict[str, object]]) -> dict[str, object]:
    """銘柄別ストップ比較を中央値・合算・改善数へ集計する。"""

    final = [Decimal(str(item["final_equity_delta"])) for item in comparisons.values()]
    pnl = [Decimal(str(item["closed_net_pnl_delta"])) for item in comparisons.values()]
    dd = [Decimal(str(item["max_drawdown_delta"])) for item in comparisons.values()]
    return {
        "symbol_count": len(comparisons),
        "symbols_improved_final_equity": sum(value > 0 for value in final),
        "symbols_worsened_final_equity": sum(value < 0 for value in final),
        "median_final_equity_delta": str(median(final)),
        "sum_final_equity_delta": str(sum(final, Decimal("0"))),
        "symbols_improved_closed_pnl": sum(value > 0 for value in pnl),
        "symbols_worsened_closed_pnl": sum(value < 0 for value in pnl),
        "median_closed_net_pnl_delta": str(median(pnl)),
        "sum_closed_net_pnl_delta": str(sum(pnl, Decimal("0"))),
        "symbols_improved_max_drawdown": sum(value > 0 for value in dd),
        "symbols_worsened_max_drawdown": sum(value < 0 for value in dd),
        "median_max_drawdown_delta": str(median(dd)),
        "sum_stop_loss_count_delta": sum(
            int(item["stop_loss_count_delta"]) for item in comparisons.values()
        ),
        "sum_time_exit_count_delta": sum(
            int(item["time_exit_count_delta"]) for item in comparisons.values()
        ),
        "median_max_position_notional_delta": str(
            median(
                Decimal(str(item["max_position_notional_delta"]))
                for item in comparisons.values()
            )
        ),
    }


def main() -> None:
    """3 ATR controlと6 ATR variantを6銘柄で比較し成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0027")
    )
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    costs = VoidShortCostModel()
    results: dict[str, dict[str, object]] = {}
    comparisons: dict[str, dict[str, object]] = {}
    for symbol in sorted(VOID_SHORT_SYMBOLS):
        frame, funding, instrument = _load_symbol(args.data_dir, metadata, symbol)
        control = _run_breakout_short(
            frame,
            funding,
            instrument,
            costs=costs,
            stop_atr_multiplier=SHORT_BREAKOUT_STOP_ATR,
        )
        variant = _run_breakout_short(
            frame,
            funding,
            instrument,
            costs=costs,
            stop_atr_multiplier=WIDER_STOP_ATR,
        )
        control_trades = reconstruct_trades(
            list(control["events"]), list(control["equity_curve"])
        )
        variant_trades = reconstruct_trades(
            list(variant["events"]), list(variant["equity_curve"])
        )
        comparisons[symbol] = _compare_metrics(
            control["metrics"],
            variant["metrics"],
            control_trades,
            variant_trades,
        )
        results[symbol] = {
            "control": {
                "metrics": control["metrics"],
                "trade_summary": summarize_trades(control_trades),
            },
            "wider_stop": {
                "metrics": variant["metrics"],
                "trade_summary": summarize_trades(variant_trades),
            },
        }
        for name, trades in (
            ("control-3atr", control_trades),
            ("wider-stop-6atr", variant_trades),
        ):
            pd.DataFrame(trades).to_csv(
                args.output_dir / f"{symbol}-{name}-trades.csv", index=False
            )

    payload = {
        "experiment_id": "EXP-2026-0027",
        "status": "BACKTEST_COMPLETED",
        "control": "EXP-0025下落ブレイク、固定3 ATRストップ",
        "variant": "同一条件、固定6 ATRストップ",
        "parameters": {
            "control_stop_atr": str(SHORT_BREAKOUT_STOP_ATR),
            "variant_stop_atr": str(WIDER_STOP_ATR),
            "other_parameters_unchanged": True,
        },
        "comparison": comparisons,
        "aggregate_comparison": _aggregate(comparisons),
        "symbols": results,
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "ストップ幅以外を固定した単一variantであり、最適なATR倍率を探索していない。",
            "2時間足OHLCではバー内イベント順序、ギャップ、部分約定、板の深さを復元できない。",
            "4ロット固定のため、広いストップによる最大損失と最大DDを本番前に別途評価する必要がある。",
            "この比較は研究診断であり、paper・shadow・live承認を意味しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
