#!/usr/bin/env python3
"""EXP-2026-0025の下落ブレイク型ショートをVOID式と比較する。"""

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

from crypt_ai.short_breakout import (  # noqa: E402
    ShortBreakoutConfig,
    run_short_breakout_backtest,
)
from crypt_ai.void_short import VOID_SHORT_SYMBOLS  # noqa: E402
from crypt_ai.void_short_accounting import VoidShortCostModel  # noqa: E402
from crypt_ai.void_short_backtest import (  # noqa: E402
    VoidShortBacktestConfig,
    VoidShortInstrument,
    run_void_short_backtest,
)
from scripts.run_exp_2026_0015 import (  # noqa: E402
    _compare_index_benchmark,
    _load_symbol,
)
from scripts.run_exp_2026_0023 import (  # noqa: E402
    LONG_SLEEVE_EQUITY,
    PORTFOLIO_INITIAL_EQUITY,
    SHORT_BALANCED_STOP_ATR,
    SHORT_SLEEVE_EQUITY,
    _aggregate_comparison,
    _combine_curves,
    _run_long_sleeve,
)


SHORT_BREAKOUT_SMA_BARS = 2400
SHORT_BREAKOUT_DONCHIAN_BARS = 240
SHORT_BREAKOUT_ATR_BARS = 240
SHORT_BREAKOUT_STOP_ATR = Decimal("3")
SHORT_BREAKOUT_MAX_HOLDING_BARS = 168
SHORT_BREAKOUT_ENTRY_LOTS = 4
SHORT_ENTRY_LOT_COUNTS = (1, 1, 1, 1)


def _metrics_with_benchmark(
    metrics: dict[str, object], *, initial_equity: Decimal
) -> dict[str, object]:
    """ショート指標へ4年インデックス基準を追加する。"""

    result = dict(metrics)
    result["index_benchmark"] = _compare_index_benchmark(
        initial_equity=initial_equity,
        final_equity=Decimal(str(result["final_equity"])),
    )
    return result


def _run_breakout_short(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    instrument: VoidShortInstrument,
    *,
    costs: VoidShortCostModel,
    stop_atr_multiplier: Decimal = SHORT_BREAKOUT_STOP_ATR,
) -> dict[str, object]:
    """固定4ロットの下落ブレイク型ショートを実行する。

    Args:
        frame: tradeとmarkの2時間足を結合したDataFrame。
        funding: Funding時刻と率のDataFrame。
        instrument: ZOOMEX銘柄仕様。
        costs: ZOOMEX費用モデル。
        stop_atr_multiplier: エントリー時ATRに対する固定ストップ倍率。

    Returns:
        指標、イベント、評価額曲線を含むショートスリーブ結果。
    """

    result = run_short_breakout_backtest(
        frame,
        funding,
        instrument,
        ShortBreakoutConfig(
            initial_equity=SHORT_SLEEVE_EQUITY,
            costs=costs,
            sma_bars=SHORT_BREAKOUT_SMA_BARS,
            donchian_bars=SHORT_BREAKOUT_DONCHIAN_BARS,
            atr_bars=SHORT_BREAKOUT_ATR_BARS,
            stop_atr_multiplier=stop_atr_multiplier,
            max_holding_bars=SHORT_BREAKOUT_MAX_HOLDING_BARS,
            entry_lot_count=SHORT_BREAKOUT_ENTRY_LOTS,
        ),
    )
    metrics = _metrics_with_benchmark(
        result.metrics, initial_equity=SHORT_SLEEVE_EQUITY
    )
    return {
        "metrics": metrics,
        "events": list(result.events),
        "equity_curve": list(result.equity_curve),
    }


def _run_void_short(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    instrument: VoidShortInstrument,
    *,
    costs: VoidShortCostModel,
) -> dict[str, object]:
    """比較用の1.5 ATR VOID式ショートを250 USDTで実行する。"""

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
    metrics = _metrics_with_benchmark(
        dict(result.metrics), initial_equity=SHORT_SLEEVE_EQUITY
    )
    return {
        "metrics": metrics,
        "events": list(result.events),
        "equity_curve": list(result.equity_curve),
    }


def _enrich_portfolio(result: dict[str, object]) -> dict[str, object]:
    """合算ポートフォリオへイベント種別と数量指標を追加する。"""

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


def _run_portfolio_arms(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    instrument: VoidShortInstrument,
    *,
    symbol: str,
    costs: VoidShortCostModel,
) -> dict[str, dict[str, object]]:
    """ロング単独、下落ブレイク併用、VOID式併用の3 armを実行する。"""

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
    breakout_short = _run_breakout_short(frame, funding, instrument, costs=costs)
    void_short = _run_void_short(frame, funding, instrument, costs=costs)
    long_result = _run_long_sleeve(
        frame,
        funding,
        symbol=symbol,
        initial_equity=LONG_SLEEVE_EQUITY,
        costs=costs,
    )
    long_plus_breakout = _enrich_portfolio(
        _combine_curves(
            symbol=symbol,
            long_result=long_result,
            short_result=breakout_short,
        )
    )
    long_plus_void = _enrich_portfolio(
        _combine_curves(
            symbol=symbol,
            long_result=long_result,
            short_result=void_short,
        )
    )
    return {
        "long_only": long_only,
        "short_breakout_only": breakout_short,
        "void_short_only": void_short,
        "long_plus_short_breakout": long_plus_breakout,
        "long_plus_void_short": long_plus_void,
    }


def _compare_pair(
    baseline: dict[str, object], candidate: dict[str, object]
) -> dict[str, object]:
    """候補armと基準armの主要指標・エクスポージャー差を計算する。"""

    result = {
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
    return result


def _aggregate_pairs(comparisons: dict[str, dict[str, object]]) -> dict[str, object]:
    """銘柄別比較を中央値・合算・改善数へ集計する。"""

    final_deltas = [Decimal(str(item["final_equity_delta"])) for item in comparisons.values()]
    dd_deltas = [Decimal(str(item["max_drawdown_delta"])) for item in comparisons.values()]
    return {
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


def main() -> None:
    """下落ブレイク型ショートを6銘柄で比較し成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0025")
    )
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    costs = VoidShortCostModel()
    results: dict[str, dict[str, dict[str, object]]] = {}
    comparisons: dict[str, dict[str, dict[str, object]]] = {
        "short_breakout_vs_void_short": {},
        "long_plus_breakout_vs_long_plus_void": {},
        "long_plus_breakout_vs_long_only": {},
    }
    for symbol in sorted(VOID_SHORT_SYMBOLS):
        frame, funding, instrument = _load_symbol(args.data_dir, metadata, symbol)
        arms = _run_portfolio_arms(
            frame, funding, instrument, symbol=symbol, costs=costs
        )
        results[symbol] = arms
        for arm, result in arms.items():
            pd.DataFrame(result["events"]).to_csv(
                args.output_dir / f"{symbol}-{arm}-events.csv", index=False
            )
            pd.DataFrame(result["equity_curve"]).to_csv(
                args.output_dir / f"{symbol}-{arm}-equity.csv", index=False
            )
        comparisons["short_breakout_vs_void_short"][symbol] = _compare_pair(
            arms["void_short_only"]["metrics"],
            arms["short_breakout_only"]["metrics"],
        )
        comparisons["long_plus_breakout_vs_long_plus_void"][symbol] = _compare_pair(
            arms["long_plus_void_short"]["metrics"],
            arms["long_plus_short_breakout"]["metrics"],
        )
        comparisons["long_plus_breakout_vs_long_only"][symbol] = _compare_pair(
            arms["long_only"]["metrics"],
            arms["long_plus_short_breakout"]["metrics"],
        )

    payload = {
        "experiment_id": "EXP-2026-0025",
        "status": "BACKTEST_COMPLETED",
        "arms": {
            "short_breakout_only": "250 USDTショート、SMA2400、Donchian240、ATR3倍ストップ、4ロット単一エントリー",
            "void_short_only": "250 USDTショート、EXP-0017の1.5 ATR VOID式",
            "long_plus_short_breakout": "ロング750 + 下落ブレイクショート250",
            "long_plus_void_short": "ロング750 + VOID式ショート250",
            "long_only": "ロング1000",
        },
        "parameters": {
            "short_breakout_sma_bars": SHORT_BREAKOUT_SMA_BARS,
            "short_breakout_donchian_bars": SHORT_BREAKOUT_DONCHIAN_BARS,
            "short_breakout_atr_bars": SHORT_BREAKOUT_ATR_BARS,
            "short_breakout_stop_atr": str(SHORT_BREAKOUT_STOP_ATR),
            "short_breakout_max_holding_bars": SHORT_BREAKOUT_MAX_HOLDING_BARS,
            "short_breakout_entry_lots": SHORT_BREAKOUT_ENTRY_LOTS,
        },
        "comparison": comparisons,
        "aggregate_comparison": {
            name: _aggregate_pairs(values) for name, values in comparisons.items()
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
            "EXP-2026-0015と同じZOOMEXアルトコイン2時間足診断であり、EXP-0012のBTC/JPY日足paperを再現しない。",
            "下落ブレイクは次足始値takerエントリー、VOID式はmaker指値エントリーで、約定モデルが異なる。",
            "4ロット単一エントリーは初期ポートフォリオの20%想定元本で、複利・スリーブ再配分は行わない。",
            "2時間足OHLCではバー内のイベント順序、部分約定、板の深さを復元できない。",
            "この比較は研究診断であり、paper・shadow・live承認を意味しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
