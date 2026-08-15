#!/usr/bin/env python3
"""EXP-2026-0023のロング・ショート固定スリーブを比較する。"""

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

from crypt_ai.research import prepare_atr_trailing_exit_signals  # noqa: E402
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


LONG_ENTRY_BARS = 660
LONG_EXIT_BARS = 240
LONG_REGIME_BARS = 2400
LONG_ATR_BARS = 240
LONG_ATR_MULTIPLIER = 3.0
PORTFOLIO_INITIAL_EQUITY = Decimal("1000")
LONG_SLEEVE_EQUITY = Decimal("750")
SHORT_SLEEVE_EQUITY = Decimal("250")
SHORT_NORMAL_STOP_ATR = Decimal("1")
SHORT_BALANCED_STOP_ATR = Decimal("1.5")


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


def _summarize_equity_curve(
    *,
    symbol: str,
    initial_equity: Decimal,
    equity_curve: list[dict[str, object]],
    events: list[dict[str, object]],
    total_fees: Decimal,
    funding_cash_flow: Decimal,
    max_position_notional: Decimal,
) -> dict[str, object]:
    """評価額曲線とイベントからスリーブまたはポートフォリオ指標を集計する。

    Args:
        symbol: 評価対象の銘柄。
        initial_equity: 対象スリーブまたはポートフォリオの初期資産。
        equity_curve: 各確定足の評価額と建玉情報。
        events: 売買・Fundingの監査イベント。
        total_fees: 累積売買手数料。
        funding_cash_flow: Fundingの累積キャッシュフロー。
        max_position_notional: 評価期間中の最大想定元本。

    Returns:
        最終資産、収益率、最大DD、取引件数を含む辞書。
    """

    if not equity_curve:
        raise ValueError("equity_curve must not be empty")
    final_equity = _decimal(equity_curve[-1]["equity"])
    event_types = [str(event["event_type"]) for event in events]
    return {
        "symbol": symbol,
        "initial_equity": str(initial_equity),
        "final_equity": str(final_equity),
        "net_pnl": str(final_equity - initial_equity),
        "return_rate": str(final_equity / initial_equity - Decimal("1")),
        "max_drawdown": str(_max_drawdown(equity_curve, initial_equity)),
        "entry_count": event_types.count("ENTRY"),
        "exit_count": event_types.count("EXIT"),
        "funding_event_count": event_types.count("FUNDING"),
        "total_fees": str(total_fees),
        "total_funding_cash_flow": str(funding_cash_flow),
        "max_position_notional": str(max_position_notional),
        "open_position_at_end": _decimal(equity_curve[-1]["position_quantity"]) > 0,
        "index_benchmark": _compare_index_benchmark(
            initial_equity=initial_equity,
            final_equity=final_equity,
        ),
    }


def _run_long_sleeve(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    *,
    symbol: str,
    initial_equity: Decimal,
    costs: VoidShortCostModel,
) -> dict[str, object]:
    """2時間足へ時間換算したEXP-0012ロングスリーブを実行する。

    ロングは確定足のDonchian・SMA・ATR状態を次足始値へ遅延し、ZOOMEXのtaker
    費用とFunding（正の率ではロングが支払う）を反映する。これはEXP-0012の
    BTC/JPY日足paperをそのまま再現するものではなく、共通2時間足上の診断である。

    Args:
        frame: tradeとmarkの2時間足を結合したDataFrame。
        funding: Funding時刻と率のDataFrame。
        symbol: 評価対象の銘柄。
        initial_equity: ロングスリーブの初期資産。
        costs: ZOOMEX費用モデル。

    Returns:
        ロングスリーブの指標、イベント、評価額曲線を含む辞書。
    """

    prepared = prepare_atr_trailing_exit_signals(
        frame[["event_time", "open", "high", "low", "close"]].copy(),
        entry_window=LONG_ENTRY_BARS,
        baseline_exit_window=LONG_EXIT_BARS,
        regime_window=LONG_REGIME_BARS,
        atr_window=LONG_ATR_BARS,
        atr_multiplier=LONG_ATR_MULTIPLIER,
    )
    eval_frame = prepared[
        (prepared["event_time"] >= pd.Timestamp("2022-02-01T00:00:00Z"))
        & (prepared["event_time"] < pd.Timestamp("2026-01-01T00:00:00Z"))
    ].reset_index(drop=True)
    if eval_frame.empty:
        raise ValueError(f"evaluation period is empty: {symbol}")
    funding_by_time = {
        timestamp: _decimal(rate)
        for timestamp, rate in zip(
            pd.to_datetime(funding["event_time"], utc=True),
            funding["funding_rate"],
            strict=True,
        )
    }
    mark_close_by_time = {
        timestamp: _decimal(mark_close)
        for timestamp, mark_close in zip(
            pd.to_datetime(frame["event_time"], utc=True),
            frame["mark_close"],
            strict=True,
        )
    }
    cash = initial_equity
    quantity = Decimal("0")
    total_fees = Decimal("0")
    funding_cash_flow = Decimal("0")
    max_position_notional = Decimal("0")
    events: list[dict[str, object]] = []
    equity_curve: list[dict[str, object]] = []

    for row in eval_frame.itertuples(index=False):
        timestamp = pd.Timestamp(row.event_time)
        mark_close = mark_close_by_time[timestamp]
        if quantity > 0 and timestamp in funding_by_time:
            amount = -quantity * mark_close * funding_by_time[timestamp]
            cash += amount
            funding_cash_flow += amount
            events.append(
                {
                    "event_time": timestamp.isoformat(),
                    "event_type": "FUNDING",
                    "quantity": str(quantity),
                    "funding_delta": str(amount),
                }
            )

        desired = int(row.desired_atr_position)
        raw_open = _decimal(row.open)
        if desired == 1 and quantity == 0:
            execution_price = raw_open * (Decimal("1") + costs.taker_slippage_rate)
            fee = cash * costs.taker_fee_rate
            quantity = (cash - fee) / execution_price
            cash = Decimal("0")
            total_fees += fee
            events.append(
                {
                    "event_time": timestamp.isoformat(),
                    "event_type": "ENTRY",
                    "quantity": str(quantity),
                    "reference_price": str(raw_open),
                    "execution_price": str(execution_price),
                    "fee_delta": str(fee),
                }
            )
        elif desired == 0 and quantity > 0:
            execution_price = raw_open * (Decimal("1") - costs.taker_slippage_rate)
            gross = quantity * execution_price
            fee = gross * costs.taker_fee_rate
            cash += gross - fee
            total_fees += fee
            events.append(
                {
                    "event_time": timestamp.isoformat(),
                    "event_type": "EXIT",
                    "quantity": str(quantity),
                    "reference_price": str(raw_open),
                    "execution_price": str(execution_price),
                    "fee_delta": str(fee),
                }
            )
            quantity = Decimal("0")

        notional = quantity * mark_close
        max_position_notional = max(max_position_notional, notional)
        equity_curve.append(
            {
                "event_time": timestamp.isoformat(),
                "equity": str(cash + notional),
                "position_quantity": str(quantity),
                "position_notional": str(notional),
            }
        )

    metrics = _summarize_equity_curve(
        symbol=symbol,
        initial_equity=initial_equity,
        equity_curve=equity_curve,
        events=events,
        total_fees=total_fees,
        funding_cash_flow=funding_cash_flow,
        max_position_notional=max_position_notional,
    )
    return {"metrics": metrics, "events": events, "equity_curve": equity_curve}


def _combine_curves(
    *,
    symbol: str,
    long_result: dict[str, object],
    short_result: dict[str, object] | None,
) -> dict[str, object]:
    """ロング・ショート各スリーブの評価額を同一時刻で合算する。"""

    long_curve = long_result["equity_curve"]
    if short_result is None:
        curve = list(long_curve)
        events = [{**event, "sleeve": "long"} for event in long_result["events"]]
        metrics = _summarize_equity_curve(
            symbol=symbol,
            initial_equity=PORTFOLIO_INITIAL_EQUITY,
            equity_curve=curve,
            events=events,
            total_fees=_decimal(long_result["metrics"]["total_fees"]),
            funding_cash_flow=_decimal(
                long_result["metrics"]["total_funding_cash_flow"]
            ),
            max_position_notional=_decimal(
                long_result["metrics"]["max_position_notional"]
            ),
        )
        return {"metrics": metrics, "events": events, "equity_curve": curve}

    short_curve = short_result["equity_curve"]
    long_by_time = {row["event_time"]: row for row in long_curve}
    short_by_time = {row["event_time"]: row for row in short_curve}
    if set(long_by_time) != set(short_by_time):
        raise ValueError(f"long and short equity timestamps differ: {symbol}")
    curve: list[dict[str, object]] = []
    for timestamp in long_by_time:
        long_row = long_by_time[timestamp]
        short_row = short_by_time[timestamp]
        curve.append(
            {
                "event_time": timestamp,
                "equity": str(
                    _decimal(long_row["equity"]) + _decimal(short_row["equity"])
                ),
                "position_quantity": str(
                    _decimal(long_row["position_quantity"])
                    + _decimal(short_row["position_quantity"])
                ),
                "position_notional": str(
                    _decimal(long_row["position_notional"])
                    + _decimal(short_row["position_notional"])
                ),
            }
        )
    events = [
        {**event, "sleeve": "long"} for event in long_result["events"]
    ] + [{**event, "sleeve": "short"} for event in short_result["events"]]
    metrics = _summarize_equity_curve(
        symbol=symbol,
        initial_equity=PORTFOLIO_INITIAL_EQUITY,
        equity_curve=curve,
        events=events,
        total_fees=_decimal(long_result["metrics"]["total_fees"])
        + _decimal(short_result["metrics"]["total_fees"]),
        funding_cash_flow=_decimal(long_result["metrics"]["total_funding_cash_flow"])
        + _decimal(short_result["metrics"]["total_funding_cash_flow"]),
        max_position_notional=max(
            _decimal(row["position_notional"]) for row in curve
        ),
    )
    return {
        "metrics": metrics,
        "events": events,
        "equity_curve": curve,
        "long_sleeve": long_result["metrics"],
        "short_sleeve": short_result["metrics"],
    }


def _run_arm(
    frame: pd.DataFrame,
    funding: pd.DataFrame,
    instrument: VoidShortInstrument,
    *,
    symbol: str,
    arm: str,
    costs: VoidShortCostModel,
) -> dict[str, object]:
    """指定armのロング・ショートスリーブを実行する。"""

    if arm == "long_only":
        long_result = _run_long_sleeve(
            frame,
            funding,
            symbol=symbol,
            initial_equity=PORTFOLIO_INITIAL_EQUITY,
            costs=costs,
        )
        return _combine_curves(
            symbol=symbol,
            long_result=long_result,
            short_result=None,
        )
    if arm not in {"control_short", "balanced_short"}:
        raise ValueError(f"unknown arm: {arm}")
    stop_atr = (
        SHORT_NORMAL_STOP_ATR
        if arm == "control_short"
        else SHORT_BALANCED_STOP_ATR
    )
    long_result = _run_long_sleeve(
        frame,
        funding,
        symbol=symbol,
        initial_equity=LONG_SLEEVE_EQUITY,
        costs=costs,
    )
    short_result = run_void_short_backtest(
        frame,
        funding,
        instrument,
        VoidShortBacktestConfig(
            initial_equity=SHORT_SLEEVE_EQUITY,
            costs=costs,
            normal_stop_pullback_atr=stop_atr,
            entry_lot_counts=(1, 1, 1, 1),
            max_entry_lot_count=4,
        ),
    )
    return _combine_curves(
        symbol=symbol,
        long_result=long_result,
        short_result={
            "metrics": short_result.metrics,
            "events": list(short_result.events),
            "equity_curve": list(short_result.equity_curve),
        },
    )


def _compare_metrics(
    baseline: dict[str, object], candidate: dict[str, object]
) -> dict[str, object]:
    """候補armとlong_onlyの主要指標差を計算する。"""

    return {
        "final_equity_delta": str(
            _decimal(candidate["final_equity"]) - _decimal(baseline["final_equity"])
        ),
        "max_drawdown_delta": str(
            _decimal(candidate["max_drawdown"])
            - _decimal(baseline["max_drawdown"])
        ),
        "entry_count_delta": int(candidate["entry_count"])
        - int(baseline["entry_count"]),
        "funding_cash_flow_delta": str(
            _decimal(candidate["total_funding_cash_flow"])
            - _decimal(baseline["total_funding_cash_flow"])
        ),
        "beats_index_benchmark": bool(candidate["index_benchmark"]["beats_benchmark"]),
    }


def _aggregate_comparison(comparisons: dict[str, dict[str, object]]) -> dict[str, object]:
    """銘柄別arm差分を中央値・合算・改善銘柄数へ集計する。"""

    final_deltas = [_decimal(value["final_equity_delta"]) for value in comparisons.values()]
    dd_deltas = [_decimal(value["max_drawdown_delta"]) for value in comparisons.values()]
    return {
        "symbol_count": len(comparisons),
        "symbols_improved_final_equity": sum(value > 0 for value in final_deltas),
        "symbols_improved_max_drawdown": sum(value > 0 for value in dd_deltas),
        "median_final_equity_delta": str(median(final_deltas)),
        "sum_final_equity_delta": str(sum(final_deltas, Decimal("0"))),
        "median_max_drawdown_delta": str(median(dd_deltas)),
        "sum_entry_count_delta": sum(
            int(value["entry_count_delta"]) for value in comparisons.values()
        ),
    }


def main() -> None:
    """3つの固定armを6銘柄で評価し監査成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0023")
    )
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    costs = VoidShortCostModel()
    arms = ("long_only", "control_short", "balanced_short")
    results: dict[str, dict[str, dict[str, object]]] = {}
    comparisons: dict[str, dict[str, dict[str, object]]] = {
        "control_short_vs_long_only": {},
        "balanced_short_vs_long_only": {},
        "balanced_short_vs_control_short": {},
    }

    for symbol in sorted(VOID_SHORT_SYMBOLS):
        frame, funding, instrument = _load_symbol(args.data_dir, metadata, symbol)
        results[symbol] = {}
        for arm in arms:
            result = _run_arm(
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
        control_metrics = results[symbol]["control_short"]["metrics"]
        balanced_metrics = results[symbol]["balanced_short"]["metrics"]
        comparisons["control_short_vs_long_only"][symbol] = _compare_metrics(
            long_metrics, control_metrics
        )
        comparisons["balanced_short_vs_long_only"][symbol] = _compare_metrics(
            long_metrics, balanced_metrics
        )
        comparisons["balanced_short_vs_control_short"][symbol] = _compare_metrics(
            control_metrics, balanced_metrics
        )

    payload = {
        "experiment_id": "EXP-2026-0023",
        "status": "BACKTEST_COMPLETED",
        "arms": {
            "long_only": "EXP-0012ロジックの2時間足換算、初期資産1000",
            "control_short": "ロング750 + EXP-0015ショート250、通常損切り1.0 ATR",
            "balanced_short": "ロング750 + EXP-0017ショート250、通常損切り1.5 ATR",
        },
        "parameters": {
            "long_entry_bars": LONG_ENTRY_BARS,
            "long_exit_bars": LONG_EXIT_BARS,
            "long_regime_bars": LONG_REGIME_BARS,
            "long_atr_bars": LONG_ATR_BARS,
            "long_atr_multiplier": LONG_ATR_MULTIPLIER,
            "long_sleeve_equity": str(LONG_SLEEVE_EQUITY),
            "short_sleeve_equity": str(SHORT_SLEEVE_EQUITY),
        },
        "comparison": comparisons,
        "aggregate_comparison": {
            name: _aggregate_comparison(values)
            for name, values in comparisons.items()
        },
        "symbols": {
            symbol: {
                arm: result["metrics"]
                for arm, result in arm_results.items()
            }
            for symbol, arm_results in results.items()
        },
        "research_status": "INCONCLUSIVE",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "EXP-0012のBTC/JPY日足spot paperをそのまま再現せず、ZOOMEXアルトコイン2時間足へ時間換算した診断である。",
            "ロングはtaker売買とFundingを反映し、ショートは既存のmaker指値・taker決済・Funding会計を使うため、約定方式が同一ではない。",
            "ロング750・ショート250の固定スリーブは単一の未最適化資金配分で、再配分・複利・税金を扱わない。",
            "2時間足OHLCではバー内のイベント順序、部分約定、板の深さを復元できない。",
            "この比較はポートフォリオ補完性の研究診断であり、paper・shadow・live承認を意味しない。",
        ],
    }
    # 成果物にはJSON化できない詳細curveを含めず、必要なCSVだけを残す。
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
