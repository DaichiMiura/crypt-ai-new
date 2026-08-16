#!/usr/bin/env python3
"""EXP-2026-0024のショートスリーブ複利を固定ロットと比較する。"""

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
    VoidShortInstrument,
    run_void_short_backtest,
)
from scripts.run_exp_2026_0015 import _load_symbol  # noqa: E402
from scripts.run_exp_2026_0023 import (  # noqa: E402
    LONG_SLEEVE_EQUITY,
    PORTFOLIO_INITIAL_EQUITY,
    SHORT_BALANCED_STOP_ATR,
    SHORT_SLEEVE_EQUITY,
    _aggregate_comparison,
    _compare_metrics,
    _combine_curves,
    _run_long_sleeve,
)


SHORT_COMPOUND_CONTROL = False
SHORT_COMPOUND_VARIANT = True
SHORT_ENTRY_LOT_COUNTS = (1, 1, 1, 1)
SHORT_MAX_ENTRY_LOT_COUNT = 4


def _short_result_as_mapping(result: object) -> dict[str, object]:
    """ショートバックテスト結果をポートフォリオ合算形式へ変換する。"""

    return {
        "metrics": dict(result.metrics),
        "events": list(result.events),
        "equity_curve": list(result.equity_curve),
    }


def _run_short_sleeve(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    instrument: VoidShortInstrument,
    *,
    compound_profits: bool,
    costs: VoidShortCostModel,
) -> dict[str, object]:
    """1.5 ATRショートを固定スリーブ資産で実行する。

    Args:
        frame: tradeとmarkの2時間足を結合したDataFrame。
        funding: Funding時刻と率のDataFrame。
        instrument: ZOOMEX銘柄仕様。
        compound_profits: 利益後の現在評価額を次回ロットへ反映するか。
        costs: ZOOMEX費用モデル。

    Returns:
        ショートスリーブの指標、イベント、評価額曲線を含む辞書。
    """

    result = run_void_short_backtest(
        frame,
        funding,
        instrument,
        VoidShortBacktestConfig(
            initial_equity=SHORT_SLEEVE_EQUITY,
            costs=costs,
            normal_stop_pullback_atr=SHORT_BALANCED_STOP_ATR,
            entry_lot_counts=SHORT_ENTRY_LOT_COUNTS,
            max_entry_lot_count=SHORT_MAX_ENTRY_LOT_COUNT,
            compound_profits=compound_profits,
        ),
    )
    return _short_result_as_mapping(result)


def _run_portfolio_arm(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    instrument: VoidShortInstrument,
    *,
    symbol: str,
    arm: str,
    costs: VoidShortCostModel,
) -> dict[str, object]:
    """指定armのロング単独またはショート複利ポートフォリオを実行する。

    Args:
        frame: tradeとmarkの2時間足を結合したDataFrame。
        funding: Funding時刻と率のDataFrame。
        instrument: ZOOMEX銘柄仕様。
        symbol: 評価対象の銘柄。
        arm: ``long_only``、``balanced_short_fixed``、または
            ``balanced_short_compounded``。
        costs: ZOOMEX費用モデル。

    Returns:
        ポートフォリオの指標、イベント、評価額曲線を含む辞書。

    Raises:
        ValueError: 未登録のarmが指定された場合。
    """

    if arm == "long_only":
        long_result = _run_long_sleeve(
            frame,
            funding,
            symbol=symbol,
            initial_equity=PORTFOLIO_INITIAL_EQUITY,
            costs=costs,
        )
        return _enrich_portfolio_metrics(_combine_curves(
            symbol=symbol,
            long_result=long_result,
            short_result=None,
        ))
    compound_by_arm = {
        "balanced_short_fixed": SHORT_COMPOUND_CONTROL,
        "balanced_short_compounded": SHORT_COMPOUND_VARIANT,
    }
    if arm not in compound_by_arm:
        raise ValueError(f"unknown arm: {arm}")
    long_result = _run_long_sleeve(
        frame,
        funding,
        symbol=symbol,
        initial_equity=LONG_SLEEVE_EQUITY,
        costs=costs,
    )
    short_result = _run_short_sleeve(
        frame,
        funding,
        instrument,
        compound_profits=compound_by_arm[arm],
        costs=costs,
    )
    return _enrich_portfolio_metrics(_combine_curves(
        symbol=symbol,
        long_result=long_result,
        short_result=short_result,
    ))


def _enrich_portfolio_metrics(result: dict[str, object]) -> dict[str, object]:
    """合算評価額曲線へショート比較用の建玉指標を追加する。"""

    metrics = result["metrics"]
    events = result["events"]
    curve = result["equity_curve"]
    metrics["liquidation_count"] = sum(
        str(event["event_type"]) == "LIQUIDATION" for event in events
    )
    metrics["max_position_quantity"] = str(
        max(Decimal(str(row["position_quantity"])) for row in curve)
    )
    return result


def _compare_pair(
    baseline: dict[str, object],
    candidate: dict[str, object],
    *,
    baseline_short: dict[str, object] | None = None,
    candidate_short: dict[str, object] | None = None,
) -> dict[str, object]:
    """候補armと基準armの損益・DD・エクスポージャー差を計算する。

    Args:
        baseline: ポートフォリオ基準armの指標。
        candidate: ポートフォリオ候補armの指標。
        baseline_short: 比較対象がポートフォリオの場合の基準ショート指標。
        candidate_short: 比較対象がポートフォリオの場合の候補ショート指標。

    Returns:
        ポートフォリオ差分と、指定時はショートスリーブ差分を含む辞書。
    """

    comparison = _compare_metrics(baseline, candidate)
    for field in ("max_position_quantity", "max_position_notional"):
        if field in baseline and field in candidate:
            comparison[f"{field}_delta"] = str(
                Decimal(str(candidate[field])) - Decimal(str(baseline[field]))
            )
    comparison["liquidation_count_delta"] = int(
        candidate.get("liquidation_count", 0)
    ) - int(baseline.get("liquidation_count", 0))
    comparison["symbols_benchmark_candidate"] = bool(
        candidate["index_benchmark"]["beats_benchmark"]
    )
    if (baseline_short is None) != (candidate_short is None):
        raise ValueError("baseline_short and candidate_short must be paired")
    if baseline_short is not None and candidate_short is not None:
        for field, output_name in (
            ("final_equity", "short_final_equity_delta"),
            ("max_drawdown", "short_max_drawdown_delta"),
            ("max_position_quantity", "short_max_position_quantity_delta"),
            ("max_position_notional", "short_max_position_notional_delta"),
        ):
            comparison[output_name] = str(
                Decimal(str(candidate_short[field]))
                - Decimal(str(baseline_short[field]))
            )
    return comparison


def _aggregate_pair(comparisons: dict[str, dict[str, object]]) -> dict[str, object]:
    """銘柄別差分を中央値・合算・改善銘柄数へ集計する。"""

    final_deltas = [Decimal(str(item["final_equity_delta"])) for item in comparisons.values()]
    dd_deltas = [Decimal(str(item["max_drawdown_delta"])) for item in comparisons.values()]
    result = {
        **_aggregate_comparison(comparisons),
        "symbols_worsened_final_equity": sum(value < 0 for value in final_deltas),
        "symbols_worsened_max_drawdown": sum(value < 0 for value in dd_deltas),
        "median_max_position_notional_delta": str(
            median(
                Decimal(str(item["max_position_notional_delta"]))
                for item in comparisons.values()
            )
        ),
        "sum_liquidation_count_delta": sum(
            int(item["liquidation_count_delta"]) for item in comparisons.values()
        ),
    }
    if all("short_max_position_notional_delta" in item for item in comparisons.values()):
        result["median_short_max_position_notional_delta"] = str(
            median(
                Decimal(str(item["short_max_position_notional_delta"]))
                for item in comparisons.values()
            )
        )
        result["sum_short_final_equity_delta"] = str(
            sum(
                (
                    Decimal(str(item["short_final_equity_delta"]))
                    for item in comparisons.values()
                ),
                Decimal("0"),
            )
        )
    return result


def main() -> None:
    """ショート複利を固定ロットと比較し監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0024")
    )
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    costs = VoidShortCostModel()
    arms = ("long_only", "balanced_short_fixed", "balanced_short_compounded")
    results: dict[str, dict[str, dict[str, object]]] = {}
    comparisons: dict[str, dict[str, dict[str, object]]] = {
        "balanced_short_compounded_vs_fixed": {},
        "balanced_short_fixed_vs_long_only": {},
        "balanced_short_compounded_vs_long_only": {},
    }

    for symbol in sorted(VOID_SHORT_SYMBOLS):
        frame, funding, instrument = _load_symbol(args.data_dir, metadata, symbol)
        results[symbol] = {}
        for arm in arms:
            result = _run_portfolio_arm(
                frame,
                funding,
                instrument,
                symbol=symbol,
                arm=arm,
                costs=costs,
            )
            results[symbol][arm] = result
            pd.DataFrame(result["events"]).to_csv(
                args.output_dir / f"{symbol}-{arm}-events.csv", index=False
            )
            pd.DataFrame(result["equity_curve"]).to_csv(
                args.output_dir / f"{symbol}-{arm}-equity.csv", index=False
            )
        long_metrics = results[symbol]["long_only"]["metrics"]
        fixed_metrics = results[symbol]["balanced_short_fixed"]["metrics"]
        compounded_metrics = results[symbol]["balanced_short_compounded"]["metrics"]
        comparisons["balanced_short_compounded_vs_fixed"][symbol] = _compare_pair(
            fixed_metrics,
            compounded_metrics,
            baseline_short=results[symbol]["balanced_short_fixed"]["short_sleeve"],
            candidate_short=results[symbol]["balanced_short_compounded"][
                "short_sleeve"
            ],
        )
        comparisons["balanced_short_fixed_vs_long_only"][symbol] = _compare_pair(
            long_metrics, fixed_metrics
        )
        comparisons["balanced_short_compounded_vs_long_only"][symbol] = _compare_pair(
            long_metrics, compounded_metrics
        )

    payload = {
        "experiment_id": "EXP-2026-0024",
        "status": "BACKTEST_COMPLETED",
        "control": "balanced_short_fixed: long750 + short250; compound_profits=false",
        "variant": "balanced_short_compounded: long750 + short250; compound_profits=true",
        "parameters": {
            "short_normal_stop_pullback_atr": str(SHORT_BALANCED_STOP_ATR),
            "short_entry_lot_counts": SHORT_ENTRY_LOT_COUNTS,
            "short_max_entry_lot_count": SHORT_MAX_ENTRY_LOT_COUNT,
            "long_sleeve_equity": str(LONG_SLEEVE_EQUITY),
            "short_sleeve_equity": str(SHORT_SLEEVE_EQUITY),
        },
        "comparison": comparisons,
        "aggregate_comparison": {
            name: _aggregate_pair(values) for name, values in comparisons.items()
        },
        "symbols": {
            symbol: {
                arm: {
                    "metrics": result["metrics"],
                    "long_sleeve": result.get("long_sleeve"),
                    "short_sleeve": result.get("short_sleeve"),
                }
                for arm, result in arm_results.items()
            }
            for symbol, arm_results in results.items()
        },
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "EXP-2026-0023と同じZOOMEXアルトコイン2時間足への診断で、EXP-0012のBTC/JPY日足paperをそのまま再現しない。",
            "ショート複利はセットアップ作成時の現在ショートスリーブ評価額を基準にするが、スリーブ間の再配分や外部入金は行わない。",
            "2時間足OHLCではバー内のイベント順序、部分約定、板の深さを復元できない。",
            "固定ロットとの比較は複利の差分診断であり、paper・shadow・live承認を意味しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
