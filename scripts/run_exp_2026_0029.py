#!/usr/bin/env python3
"""EXP-2026-0029のVOID式ショート配分を比較する。"""

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
from crypt_ai.void_short_backtest import (  # noqa: E402
    VoidShortBacktestConfig,
    VoidShortInstrument,
    run_void_short_backtest,
)
from scripts.run_exp_2026_0015 import _load_symbol  # noqa: E402
from scripts.run_exp_2026_0023 import (  # noqa: E402
    PORTFOLIO_INITIAL_EQUITY,
    SHORT_BALANCED_STOP_ATR,
    _combine_curves,
    _run_long_sleeve,
)


SHORT_ENTRY_LOT_COUNTS = (1, 1, 1, 1)
ARM_SPECS: dict[str, tuple[Decimal, Decimal]] = {
    "long_only": (Decimal("1000"), Decimal("0")),
    "hedge_5pct": (Decimal("937.5"), Decimal("62.5")),
    "hedge_10pct": (Decimal("875"), Decimal("125")),
    "hedge_20pct": (Decimal("750"), Decimal("250")),
}


def _run_void_sleeve(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    instrument: VoidShortInstrument,
    *,
    initial_equity: Decimal,
    costs: VoidShortCostModel,
) -> dict[str, object]:
    """指定ショートスリーブ資産で1.5 ATR VOID式を実行する。"""

    result = run_void_short_backtest(
        frame,
        funding,
        instrument,
        VoidShortBacktestConfig(
            initial_equity=initial_equity,
            costs=costs,
            normal_stop_pullback_atr=SHORT_BALANCED_STOP_ATR,
            entry_lot_counts=SHORT_ENTRY_LOT_COUNTS,
            max_entry_lot_count=4,
        ),
    )
    return {
        "metrics": dict(result.metrics),
        "events": list(result.events),
        "equity_curve": list(result.equity_curve),
    }


def _enrich_portfolio(result: dict[str, object]) -> dict[str, object]:
    """ポートフォリオ指標へ退出件数と最大数量を追加する。"""

    metrics = result["metrics"]
    events = result["events"]
    curve = result["equity_curve"]
    event_types = [str(event["event_type"]) for event in events]
    metrics["liquidation_count"] = event_types.count("LIQUIDATION")
    metrics["strategy_exit_count"] = event_types.count("EXIT") + sum(
        event_type in {"NORMAL_STOP", "EMERGENCY_STOP", "TIME_EXIT"}
        for event_type in event_types
    )
    metrics["max_position_quantity"] = str(
        max(Decimal(str(row["position_quantity"])) for row in curve)
    )
    return result


def _run_arms(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    instrument: VoidShortInstrument,
    *,
    symbol: str,
    costs: VoidShortCostModel,
) -> dict[str, dict[str, object]]:
    """long-onlyと3種類のショート配分armを実行する。"""

    results: dict[str, dict[str, object]] = {}
    for arm, (long_equity, short_equity) in ARM_SPECS.items():
        long_result = _run_long_sleeve(
            frame,
            funding,
            symbol=symbol,
            initial_equity=long_equity,
            costs=costs,
        )
        short_result = None
        if short_equity > 0:
            short_result = _run_void_sleeve(
                frame,
                funding,
                instrument,
                initial_equity=short_equity,
                costs=costs,
            )
        results[arm] = _enrich_portfolio(
            _combine_curves(
                symbol=symbol,
                long_result=long_result,
                short_result=short_result,
            )
        )
    return results


def _compare_pair(
    baseline: dict[str, object], candidate: dict[str, object]
) -> dict[str, object]:
    """配分armとlong-onlyの最終資産・DD・費用差を計算する。"""

    return {
        "final_equity_delta": str(
            Decimal(str(candidate["final_equity"]))
            - Decimal(str(baseline["final_equity"]))
        ),
        "max_drawdown_delta": str(
            Decimal(str(candidate["max_drawdown"]))
            - Decimal(str(baseline["max_drawdown"]))
        ),
        "entry_count_delta": int(candidate["entry_count"])
        - int(baseline["entry_count"]),
        "funding_cash_flow_delta": str(
            Decimal(str(candidate["total_funding_cash_flow"]))
            - Decimal(str(baseline["total_funding_cash_flow"]))
        ),
        "fees_delta": str(
            Decimal(str(candidate["total_fees"]))
            - Decimal(str(baseline["total_fees"]))
        ),
        "max_position_notional_delta": str(
            Decimal(str(candidate["max_position_notional"]))
            - Decimal(str(baseline["max_position_notional"]))
        ),
        "liquidation_count_delta": int(candidate.get("liquidation_count", 0))
        - int(baseline.get("liquidation_count", 0)),
        "beats_index_benchmark": bool(
            candidate["index_benchmark"]["beats_benchmark"]
        ),
    }


def _aggregate(comparisons: dict[str, dict[str, object]]) -> dict[str, object]:
    """6銘柄の配分差を中央値・合算・改善数へ集計する。"""

    final = [Decimal(str(item["final_equity_delta"])) for item in comparisons.values()]
    dd = [Decimal(str(item["max_drawdown_delta"])) for item in comparisons.values()]
    return {
        "symbol_count": len(comparisons),
        "symbols_improved_final_equity": sum(value > 0 for value in final),
        "symbols_worsened_final_equity": sum(value < 0 for value in final),
        "median_final_equity_delta": str(median(final)),
        "sum_final_equity_delta": str(sum(final, Decimal("0"))),
        "symbols_improved_max_drawdown": sum(value > 0 for value in dd),
        "symbols_worsened_max_drawdown": sum(value < 0 for value in dd),
        "median_max_drawdown_delta": str(median(dd)),
        "sum_entry_count_delta": sum(
            int(item["entry_count_delta"]) for item in comparisons.values()
        ),
        "median_max_position_notional_delta": str(
            median(
                Decimal(str(item["max_position_notional_delta"]))
                for item in comparisons.values()
            )
        ),
    }


def main() -> None:
    """ショート配分3 armを6銘柄で比較し成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0029")
    )
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    costs = VoidShortCostModel()
    results: dict[str, dict[str, dict[str, object]]] = {}
    comparisons: dict[str, dict[str, dict[str, object]]] = {
        "hedge_5pct_vs_long_only": {},
        "hedge_10pct_vs_long_only": {},
        "hedge_20pct_vs_long_only": {},
    }
    for symbol in sorted(VOID_SHORT_SYMBOLS):
        frame, funding, instrument = _load_symbol(args.data_dir, metadata, symbol)
        arms = _run_arms(
            frame,
            funding,
            instrument,
            symbol=symbol,
            costs=costs,
        )
        results[symbol] = arms
        for arm, result in arms.items():
            pd.DataFrame(result["events"]).to_csv(
                args.output_dir / f"{symbol}-{arm}-events.csv", index=False
            )
            pd.DataFrame(result["equity_curve"]).to_csv(
                args.output_dir / f"{symbol}-{arm}-equity.csv", index=False
            )
        baseline = arms["long_only"]["metrics"]
        for arm in ("hedge_5pct", "hedge_10pct", "hedge_20pct"):
            comparisons[f"{arm}_vs_long_only"][symbol] = _compare_pair(
                baseline,
                arms[arm]["metrics"],
            )

    payload = {
        "experiment_id": "EXP-2026-0029",
        "status": "BACKTEST_COMPLETED",
        "arms": {
            arm: {
                "long_sleeve_equity": str(long_equity),
                "short_sleeve_equity": str(short_equity),
                "short_ratio": str(short_equity / PORTFOLIO_INITIAL_EQUITY),
            }
            for arm, (long_equity, short_equity) in ARM_SPECS.items()
        },
        "parameters": {
            "short_stop_atr": str(SHORT_BALANCED_STOP_ATR),
            "short_entry_lot_counts": SHORT_ENTRY_LOT_COUNTS,
            "cross_sleeve_reallocation": False,
        },
        "comparison": comparisons,
        "aggregate_comparison": {
            name: _aggregate(values) for name, values in comparisons.items()
        },
        "symbols": {
            symbol: {
                arm: result["metrics"] for arm, result in arms.items()
            }
            for symbol, arms in results.items()
        },
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "EXP-2026-0012のBTC/JPY日足paperではなく、ZOOMEXアルトコイン2時間足の共通データ診断である。",
            "ショート配分の比較であり、シグナル・損切り・Funding・費用モデルは変更しない。",
            "小さい配分でも同じ4ロット相対サイズを使うため、実際の取引所最小数量や流動性は別途確認が必要である。",
            "この比較はヘッジ配分診断であり、paper・shadow・live承認を意味しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
