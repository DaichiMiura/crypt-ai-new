#!/usr/bin/env python3
"""EXP-2026-0005のDonchian entryとBollinger退出オーバーレイを比較する。"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crypt_ai.research import (  # noqa: E402
    INTERPOLATED_COLUMN,
    CostModel,
    inspect_daily_data,
    prepare_donchian_bollinger_exit_signals,
    prepare_donchian_signals,
    run_backtest,
    run_buy_and_hold,
    summarize_equity,
)


def _read_daily_file(path: Path) -> pd.DataFrame:
    """集約済み日足CSVを読み込み、時刻と数値の型を正規化する。

    Args:
        path: `build_exp_2026_0003_dataset.py`が作成した日足CSV。

    Returns:
        Donchianとボリンジャー計算に使える日足データ。
    """

    frame = pd.read_csv(path)
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame[INTERPOLATED_COLUMN] = (
        frame[INTERPOLATED_COLUMN]
        .fillna(False)
        .astype(str)
        .str.lower()
        .isin(["1", "true", "yes"])
    )
    return frame


def _add_donchian_event_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Donchianの状態変化からentry/exitイベント列を作る。

    Args:
        frame: `prepare_donchian_signals`が返す日足データ。

    Returns:
        entry/exitイベントとoverlay待機列を追加したデータ。
    """

    result = frame.copy()
    previous = result["signal_position"].shift(1).fillna(0)
    result["entry_signal"] = result["signal_position"].eq(1) & previous.eq(0)
    result["exit_signal"] = result["signal_position"].eq(0) & previous.eq(1)
    result["overlay_armed"] = False
    return result


def _summarize_round_trips(trades: pd.DataFrame) -> dict[str, object]:
    """約定履歴から往復損益、勝率、費用を再計算する。

    Args:
        trades: `run_backtest`が返すBUY/SELLの約定履歴。

    Returns:
        クローズ済み往復取引の件数、勝率、平均損益、期待値、費用を含む辞書。

    Raises:
        ValueError: BUY/SELLの順序が現物ロングの不変条件に反する場合。
    """

    total_fees = Decimal("0")
    round_trip_pnls: list[Decimal] = []
    open_buy = None
    for row in trades.itertuples(index=False):
        total_fees += Decimal(str(row.fee))
        if row.side == "BUY":
            if open_buy is not None:
                raise ValueError("trade history contains consecutive BUY fills")
            open_buy = row
        elif row.side == "SELL":
            if open_buy is None:
                raise ValueError("trade history contains SELL without a BUY")
            buy_cost = Decimal(str(open_buy.quantity)) * Decimal(
                str(open_buy.execution_price)
            ) + Decimal(str(open_buy.fee))
            sell_value = Decimal(str(row.quantity)) * Decimal(
                str(row.execution_price)
            ) - Decimal(str(row.fee))
            round_trip_pnls.append(sell_value - buy_cost)
            open_buy = None
        else:
            raise ValueError(f"unknown trade side: {row.side}")

    wins = [pnl for pnl in round_trip_pnls if pnl > 0]
    losses = [pnl for pnl in round_trip_pnls if pnl < 0]
    closed_count = len(round_trip_pnls)
    return {
        "closed_round_trips": closed_count,
        "open_position_at_end": open_buy is not None,
        "win_rate": (len(wins) / closed_count) if closed_count else None,
        "average_win": (
            str(sum(wins, Decimal("0")) / Decimal(len(wins))) if wins else None
        ),
        "average_loss": (
            str(sum(losses, Decimal("0")) / Decimal(len(losses)))
            if losses
            else None
        ),
        "expectancy_per_closed_trade": (
            str(sum(round_trip_pnls, Decimal("0")) / Decimal(closed_count))
            if closed_count
            else None
        ),
        "total_fees": str(total_fees),
    }


def _evaluate(
    frame: pd.DataFrame,
    cost_model: CostModel,
    initial_cash: Decimal,
) -> dict[str, object]:
    """一つのentry/exit定義について全期間とOOS指標を計算する。

    Args:
        frame: entry/exitイベントと`desired_position`を付けた日足データ。
        cost_model: fee、spread、slippageの仮定。
        initial_cash: 初期paper現金残高。

    Returns:
        損益、取引統計、イベント数、合成日足影響、成果物を含む辞書。
    """

    equity, trades = run_backtest(frame, cost_model, initial_cash)
    baseline = run_buy_and_hold(frame, cost_model, initial_cash)
    oos_start = pd.Timestamp("2025-01-01T00:00:00Z")
    oos = frame[frame["event_time"] >= oos_start].reset_index(drop=True)
    if oos.empty:
        raise ValueError("reserved OOS period is empty")
    oos_equity, oos_trades = run_backtest(oos, cost_model, initial_cash)
    oos_baseline = run_buy_and_hold(oos, cost_model, initial_cash)
    event_changes = frame["signal_position"].ne(
        frame["signal_position"].shift(1).fillna(0)
    )
    synthetic_entry_signals = int(
        (frame["entry_signal"] & frame[INTERPOLATED_COLUMN].astype(bool)).sum()
    )
    synthetic_exit_signals = int(
        (frame["exit_signal"] & frame[INTERPOLATED_COLUMN].astype(bool)).sum()
    )
    synthetic_trades = int(
        trades.get(INTERPOLATED_COLUMN, pd.Series(dtype=bool)).astype(bool).sum()
    )
    oos_synthetic_trades = int(
        oos_trades.get(INTERPOLATED_COLUMN, pd.Series(dtype=bool))
        .astype(bool)
        .sum()
    )
    return {
        "full_strategy": summarize_equity(equity),
        "full_buy_and_hold": summarize_equity(baseline),
        "oos_strategy": summarize_equity(oos_equity),
        "oos_buy_and_hold": summarize_equity(oos_baseline),
        "trade_statistics": _summarize_round_trips(trades),
        "oos_trade_statistics": _summarize_round_trips(oos_trades),
        "trade_count": int(len(trades)),
        "oos_trade_count": int(len(oos_trades)),
        "entry_signal_count": int(frame["entry_signal"].sum()),
        "exit_signal_count": int(frame["exit_signal"].sum()),
        "overlay_armed_days": int(frame["overlay_armed"].astype(bool).sum()),
        "signal_changes": int(event_changes.sum()),
        "synthetic_entry_signals": synthetic_entry_signals,
        "synthetic_exit_signals": synthetic_exit_signals,
        "trades_on_interpolated_days": synthetic_trades,
        "oos_trades_on_interpolated_days": oos_synthetic_trades,
        "artifacts": {
            "equity": equity,
            "trades": trades,
            "baseline": baseline,
            "oos_equity": oos_equity,
            "oos_baseline": oos_baseline,
        },
    }


def _comparison(
    base: dict[str, object],
    overlay: dict[str, object],
) -> dict[str, float]:
    """overlayとDonchian単独の主要指標差分を計算する。

    Args:
        base: Donchian 55/20単独の評価結果。
        overlay: Donchian entryとBollinger退出の評価結果。

    Returns:
        OOSと全期間のCAGR差、最大DD改善幅を含む辞書。
    """

    base_oos = base["oos_strategy"]
    overlay_oos = overlay["oos_strategy"]
    base_full = base["full_strategy"]
    overlay_full = overlay["full_strategy"]
    return {
        "oos_cagr_delta": overlay_oos["cagr"] - base_oos["cagr"],
        "oos_max_drawdown_improvement": overlay_oos["max_drawdown"]
        - base_oos["max_drawdown"],
        "full_cagr_delta": overlay_full["cagr"] - base_full["cagr"],
        "full_max_drawdown_improvement": overlay_full["max_drawdown"]
        - base_full["max_drawdown"],
    }


def main() -> None:
    """日足データを検査し、baseとoverlayの費用感度を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0003")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0005")
    )
    parser.add_argument("--initial-cash", type=Decimal, default=Decimal("1000"))
    args = parser.parse_args()

    paths = sorted(args.data_dir.glob("*.csv"))
    if len(paths) != 1:
        raise ValueError(f"expected exactly one daily CSV: {paths}")
    frame = _read_daily_file(paths[0])
    quality = inspect_daily_data(frame)
    if quality["duplicate_count"] or quality["missing_intervals"]:
        raise ValueError(f"refusing to backtest incomplete daily data: {quality}")
    if not quality["interpolated_rows"]:
        raise ValueError("EXP-2026-0005 requires inherited synthetic-day markers")

    base_frame = _add_donchian_event_columns(
        prepare_donchian_signals(frame, entry_window=55, exit_window=20)
    )
    overlay_frame = prepare_donchian_bollinger_exit_signals(
        frame, entry_window=55, band_window=20, std_multiplier=2.0
    )
    cost_cases = {
        "base": CostModel(Decimal("0.001"), Decimal("0.0005"), Decimal("0.0005")),
        "adverse": CostModel(Decimal("0.0015"), Decimal("0.0005"), Decimal("0.0005")),
        "stress": CostModel(Decimal("0.002"), Decimal("0.0005"), Decimal("0.0005")),
    }
    evaluations: dict[str, dict[str, object]] = {}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for cost_name, cost_model in cost_cases.items():
        base_result = _evaluate(base_frame, cost_model, args.initial_cash)
        overlay_result = _evaluate(overlay_frame, cost_model, args.initial_cash)
        base_artifacts = base_result.pop("artifacts")
        overlay_artifacts = overlay_result.pop("artifacts")
        if cost_name == "base":
            for prefix, artifacts in (
                ("base", base_artifacts),
                ("overlay", overlay_artifacts),
            ):
                artifacts["equity"].to_csv(
                    args.output_dir / f"{prefix}-equity.csv", index=False
                )
                artifacts["trades"].to_csv(
                    args.output_dir / f"{prefix}-trades.csv", index=False
                )
                artifacts["baseline"].to_csv(
                    args.output_dir / f"{prefix}-buy-and-hold-equity.csv", index=False
                )
                artifacts["oos_equity"].to_csv(
                    args.output_dir / f"{prefix}-oos-equity.csv", index=False
                )
                artifacts["oos_baseline"].to_csv(
                    args.output_dir / f"{prefix}-oos-buy-and-hold-equity.csv",
                    index=False,
                )
        evaluations[cost_name] = {
            "cost_model": {
                "fee_rate": str(cost_model.fee_rate),
                "round_trip_spread": str(cost_model.round_trip_spread),
                "slippage_per_fill": str(cost_model.slippage_per_fill),
            },
            "base": base_result,
            "overlay": overlay_result,
            "comparison": _comparison(base_result, overlay_result),
        }
    summary = {
        "experiment_id": "EXP-2026-0005",
        "base_method": "Donchian entry 55 days / Donchian exit 20 days",
        "overlay_method": "Donchian entry 55 days / Bollinger 20 days, 2σ, armed lower-band breach then middle-band exit",
        "data_quality": quality,
        "evaluations": evaluations,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
