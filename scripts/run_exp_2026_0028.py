#!/usr/bin/env python3
"""EXP-2026-0028のショートヘッジarmをポートフォリオで比較する。"""

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
    LONG_SLEEVE_EQUITY,
    PORTFOLIO_INITIAL_EQUITY,
    SHORT_BALANCED_STOP_ATR,
    SHORT_SLEEVE_EQUITY,
    _combine_curves,
    _run_long_sleeve,
)
from scripts.run_exp_2026_0025 import (  # noqa: E402
    SHORT_BREAKOUT_STOP_ATR,
    _run_breakout_short,
)


BREAKOUT_WIDER_STOP_ATR = Decimal("6")
SHORT_ENTRY_LOT_COUNTS = (1, 1, 1, 1)


def _run_void_sleeve(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    instrument: VoidShortInstrument,
    *,
    costs: VoidShortCostModel,
) -> dict[str, object]:
    """1.5 ATR VOID式ショートを250 USDTスリーブで実行する。"""

    result = run_void_short_backtest(
        frame,
        funding,
        instrument,
        VoidShortBacktestConfig(
            initial_equity=SHORT_SLEEVE_EQUITY,
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
    metrics["strategy_exit_count"] = sum(
        event_type in {"EXIT", "STOP_LOSS", "SMA_EXIT", "TIME_EXIT"}
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
    """4つの資金配分armを同一銘柄で実行する。"""

    long_only = _enrich_portfolio(
        _combine_curves(
            symbol=symbol,
            long_result=_run_long_sleeve(
                frame,
                funding,
                symbol=symbol,
                initial_equity=PORTFOLIO_INITIAL_EQUITY,
                costs=costs,
            ),
            short_result=None,
        )
    )
    long_result = _run_long_sleeve(
        frame,
        funding,
        symbol=symbol,
        initial_equity=LONG_SLEEVE_EQUITY,
        costs=costs,
    )
    void_short = _run_void_sleeve(frame, funding, instrument, costs=costs)
    breakout_3 = _run_breakout_short(
        frame,
        funding,
        instrument,
        costs=costs,
        stop_atr_multiplier=SHORT_BREAKOUT_STOP_ATR,
    )
    breakout_6 = _run_breakout_short(
        frame,
        funding,
        instrument,
        costs=costs,
        stop_atr_multiplier=BREAKOUT_WIDER_STOP_ATR,
    )
    return {
        "long_only": long_only,
        "long_plus_void_balanced": _enrich_portfolio(
            _combine_curves(
                symbol=symbol,
                long_result=long_result,
                short_result=void_short,
            )
        ),
        "long_plus_breakout_3atr": _enrich_portfolio(
            _combine_curves(
                symbol=symbol,
                long_result=long_result,
                short_result=breakout_3,
            )
        ),
        "long_plus_breakout_6atr": _enrich_portfolio(
            _combine_curves(
                symbol=symbol,
                long_result=long_result,
                short_result=breakout_6,
            )
        ),
    }


def _compare_pair(
    baseline: dict[str, object], candidate: dict[str, object]
) -> dict[str, object]:
    """候補ポートフォリオと基準ポートフォリオの差を計算する。"""

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
    """銘柄別ポートフォリオ差を中央値・合算・改善数へ集計する。"""

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
    """4つのショートarmを6銘柄でロングと合算し成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0028")
    )
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    costs = VoidShortCostModel()
    results: dict[str, dict[str, dict[str, object]]] = {}
    comparisons: dict[str, dict[str, dict[str, object]]] = {
        "breakout_6atr_vs_long_only": {},
        "breakout_6atr_vs_void_balanced": {},
        "breakout_6atr_vs_breakout_3atr": {},
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
        metrics = {arm: result["metrics"] for arm, result in arms.items()}
        comparisons["breakout_6atr_vs_long_only"][symbol] = _compare_pair(
            metrics["long_only"], metrics["long_plus_breakout_6atr"]
        )
        comparisons["breakout_6atr_vs_void_balanced"][symbol] = _compare_pair(
            metrics["long_plus_void_balanced"], metrics["long_plus_breakout_6atr"]
        )
        comparisons["breakout_6atr_vs_breakout_3atr"][symbol] = _compare_pair(
            metrics["long_plus_breakout_3atr"], metrics["long_plus_breakout_6atr"]
        )

    payload = {
        "experiment_id": "EXP-2026-0028",
        "status": "BACKTEST_COMPLETED",
        "arms": {
            "long_only": "ロング1000",
            "long_plus_void_balanced": "ロング750 + VOID式1.5 ATRショート250",
            "long_plus_breakout_3atr": "ロング750 + 下落ブレイク3 ATRショート250",
            "long_plus_breakout_6atr": "ロング750 + 下落ブレイク6 ATRショート250",
        },
        "parameters": {
            "long_sleeve_equity": str(LONG_SLEEVE_EQUITY),
            "short_sleeve_equity": str(SHORT_SLEEVE_EQUITY),
            "breakout_3atr": str(SHORT_BREAKOUT_STOP_ATR),
            "breakout_6atr": str(BREAKOUT_WIDER_STOP_ATR),
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
            "ロングはtaker次足始値、VOID式はmaker指値、下落ブレイクはtaker次足始値で約定モデルが異なる。",
            "6 ATRは最大DDと想定元本が増える可能性があり、ポートフォリオ改善だけでpaper承認しない。",
            "固定75/25スリーブであり、スリーブ間再配分・複利・税金は扱わない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
