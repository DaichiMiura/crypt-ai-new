#!/usr/bin/env python3
"""EXP-2026-0026でEXP-0025のトレード損益を分解する。"""

from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path
import sys
from statistics import mean, median

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "src"))

from crypt_ai.void_short import VOID_SHORT_SYMBOLS  # noqa: E402
from crypt_ai.void_short_accounting import VoidShortCostModel  # noqa: E402
from scripts.run_exp_2026_0015 import _load_symbol  # noqa: E402
from scripts.run_exp_2026_0025 import _run_breakout_short  # noqa: E402


PORTFOLIO_INITIAL_EQUITY = Decimal("1000")
BAR_HOURS = Decimal("2")


def _decimal(value: object, *, default: Decimal = Decimal("0")) -> Decimal:
    """欠損可能なイベント値をDecimalへ変換する。"""

    if value is None or pd.isna(value):
        return default
    return Decimal(str(value))


def _equity_before(
    equity_rows: list[dict[str, object]], timestamp: pd.Timestamp, initial: Decimal
) -> Decimal:
    """指定時刻直前の評価額を返す。"""

    previous = [
        _decimal(row["equity"])
        for row in equity_rows
        if pd.Timestamp(row["event_time"]) < timestamp
    ]
    return previous[-1] if previous else initial


def reconstruct_trades(
    events: list[dict[str, object]],
    equity_curve: list[dict[str, object]],
    *,
    initial_equity: Decimal = PORTFOLIO_INITIAL_EQUITY,
) -> list[dict[str, object]]:
    """監査イベントと評価額曲線から1トレード単位の記録を再構成する。

    Args:
        events: ENTRY、Funding、退出イベントの時系列。
        equity_curve: 各バーのmark評価額。
        initial_equity: エントリー前評価額のフォールバック値。

    Returns:
        退出理由、損益、保有期間、Funding、手数料を持つトレード記録。

    Raises:
        ValueError: ENTRYの重複、退出イベントの孤立、評価額曲線の欠落がある場合。
    """

    ordered_events = sorted(events, key=lambda event: event["event_time"])
    ordered_curve = sorted(equity_curve, key=lambda row: row["event_time"])
    curve_by_time = {
        pd.Timestamp(row["event_time"]): _decimal(row["equity"])
        for row in ordered_curve
    }
    active: dict[str, object] | None = None
    trades: list[dict[str, object]] = []
    for event in ordered_events:
        event_type = str(event["event_type"])
        timestamp = pd.Timestamp(event["event_time"])
        fee_delta = _decimal(event.get("fee_delta"))
        if event_type == "ENTRY":
            if active is not None:
                raise ValueError("ENTRY occurred while another trade was active")
            active = {
                "entry_time": timestamp,
                "entry_price": _decimal(event.get("execution_price")),
                "entry_reference_price": _decimal(event.get("reference_price")),
                "quantity": _decimal(event.get("quantity")),
                "entry_fee": fee_delta,
                "fees": fee_delta,
                "funding_cash_flow": Decimal("0"),
                "equity_before_entry": _equity_before(
                    ordered_curve, timestamp, initial_equity
                ),
            }
            continue
        if event_type == "FUNDING":
            if active is None:
                raise ValueError("FUNDING occurred without an active trade")
            active["funding_cash_flow"] += _decimal(event.get("funding_delta"))
            active["fees"] += fee_delta
            continue
        if event_type not in {"STOP_LOSS", "SMA_EXIT", "TIME_EXIT"}:
            continue
        if active is None:
            raise ValueError(f"{event_type} occurred without an active trade")
        if timestamp not in curve_by_time:
            raise ValueError(f"exit timestamp missing from equity curve: {timestamp}")
        entry_time = active["entry_time"]
        hold_bars = int((timestamp - entry_time) / pd.Timedelta(hours=2))
        exit_equity = curve_by_time[timestamp]
        trade = {
            "entry_time": entry_time.isoformat(),
            "exit_time": timestamp.isoformat(),
            "exit_reason": event_type,
            "entry_price": str(active["entry_price"]),
            "entry_reference_price": str(active["entry_reference_price"]),
            "exit_reference_price": str(_decimal(event.get("reference_price"))),
            "quantity": str(active["quantity"]),
            "entry_notional": str(
                active["entry_price"] * active["quantity"]
            ),
            "entry_notional_ratio_of_portfolio": str(
                active["entry_price"]
                * active["quantity"]
                / PORTFOLIO_INITIAL_EQUITY
            ),
            "equity_before_entry": str(active["equity_before_entry"]),
            "equity_after_exit": str(exit_equity),
            "net_pnl": str(exit_equity - active["equity_before_entry"]),
            "hold_bars": hold_bars,
            "funding_cash_flow": str(active["funding_cash_flow"]),
            "fees": str(active["fees"] + fee_delta),
        }
        trades.append(trade)
        active = None
    if active is not None:
        last_row = ordered_curve[-1]
        last_timestamp = pd.Timestamp(last_row["event_time"])
        if last_timestamp not in curve_by_time:
            raise ValueError("last equity timestamp is missing")
        hold_bars = int(
            (last_timestamp - active["entry_time"]) / pd.Timedelta(hours=2)
        )
        exit_equity = curve_by_time[last_timestamp]
        trades.append(
            {
                "entry_time": active["entry_time"].isoformat(),
                "exit_time": last_timestamp.isoformat(),
                "exit_reason": "OPEN",
                "entry_price": str(active["entry_price"]),
                "entry_reference_price": str(active["entry_reference_price"]),
                "exit_reference_price": "",
                "quantity": str(active["quantity"]),
                "entry_notional": str(
                    active["entry_price"] * active["quantity"]
                ),
                "entry_notional_ratio_of_portfolio": str(
                    active["entry_price"]
                    * active["quantity"]
                    / PORTFOLIO_INITIAL_EQUITY
                ),
                "equity_before_entry": str(active["equity_before_entry"]),
                "equity_after_exit": str(exit_equity),
                "net_pnl": str(exit_equity - active["equity_before_entry"]),
                "hold_bars": hold_bars,
                "funding_cash_flow": str(active["funding_cash_flow"]),
                "fees": str(active["fees"]),
            }
        )
    return trades


def summarize_trades(trades: list[dict[str, object]]) -> dict[str, object]:
    """トレード集合の損益、勝率、保有期間、費用を集計する。"""

    if not trades:
        return {
            "trade_count": 0,
            "closed_trade_count": 0,
            "open_trade_count": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "total_net_pnl": "0",
            "closed_net_pnl": "0",
            "median_net_pnl": None,
            "mean_net_pnl": None,
            "profit_factor": None,
            "mean_hold_bars": None,
            "total_funding_cash_flow": "0",
            "total_fees": "0",
            "mean_entry_notional": None,
            "max_entry_notional": None,
        }
    closed = [trade for trade in trades if trade["exit_reason"] != "OPEN"]
    pnls = [Decimal(str(trade["net_pnl"])) for trade in trades]
    closed_pnls = [Decimal(str(trade["net_pnl"])) for trade in closed]
    wins = sum(pnl > 0 for pnl in closed_pnls)
    losses = sum(pnl < 0 for pnl in closed_pnls)
    positive = sum((pnl for pnl in closed_pnls if pnl > 0), Decimal("0"))
    negative = sum((pnl for pnl in closed_pnls if pnl < 0), Decimal("0"))
    notionals = [Decimal(str(trade["entry_notional"])) for trade in trades]
    return {
        "trade_count": len(trades),
        "closed_trade_count": len(closed),
        "open_trade_count": len(trades) - len(closed),
        "wins": wins,
        "losses": losses,
        "win_rate": str(Decimal(wins) / Decimal(len(closed))) if closed else None,
        "total_net_pnl": str(sum(pnls, Decimal("0"))),
        "closed_net_pnl": str(sum(closed_pnls, Decimal("0"))),
        "median_net_pnl": str(median(closed_pnls)) if closed else None,
        "mean_net_pnl": str(mean(closed_pnls)) if closed else None,
        "profit_factor": str(positive / abs(negative)) if negative else None,
        "mean_hold_bars": str(mean(int(trade["hold_bars"]) for trade in trades)),
        "total_funding_cash_flow": str(
            sum(
                (Decimal(str(trade["funding_cash_flow"])) for trade in trades),
                Decimal("0"),
            )
        ),
        "total_fees": str(
            sum((Decimal(str(trade["fees"])) for trade in trades), Decimal("0"))
        ),
        "mean_entry_notional": str(mean(notionals)),
        "max_entry_notional": str(max(notionals)),
    }


def _group_trades(
    trades: list[dict[str, object]], key: str
) -> dict[str, dict[str, object]]:
    """トレードを指定キーで分けて各グループを集計する。"""

    groups: defaultdict[str, list[dict[str, object]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade[key])].append(trade)
    return {name: summarize_trades(group) for name, group in sorted(groups.items())}


def main() -> None:
    """EXP-0025のトレード損益を6銘柄で分解し成果物を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir", type=Path, default=Path("data/processed/EXP-2026-0015")
    )
    parser.add_argument(
        "--metadata", type=Path, default=Path("var/exp-2026-0015-data.json")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/EXP-2026-0026")
    )
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    costs = VoidShortCostModel()
    all_trades: list[dict[str, object]] = []
    symbols: dict[str, dict[str, object]] = {}
    for symbol in sorted(VOID_SHORT_SYMBOLS):
        frame, funding, instrument = _load_symbol(args.data_dir, metadata, symbol)
        result = _run_breakout_short(frame, funding, instrument, costs=costs)
        trades = reconstruct_trades(
            list(result["events"]),
            list(result["equity_curve"]),
        )
        for trade in trades:
            trade["symbol"] = symbol
        all_trades.extend(trades)
        symbols[symbol] = {
            "strategy_metrics": result["metrics"],
            "trade_summary": summarize_trades(trades),
            "by_exit_reason": _group_trades(trades, "exit_reason"),
        }
        pd.DataFrame(trades).to_csv(
            args.output_dir / f"{symbol}-trades.csv", index=False
        )

    payload = {
        "experiment_id": "EXP-2026-0026",
        "status": "DIAGNOSTIC_COMPLETED",
        "parent_experiment": "EXP-2026-0025 short_breakout_only",
        "diagnostic_scope": {
            "signal_and_parameters_unchanged": True,
            "symbols": sorted(VOID_SHORT_SYMBOLS),
            "trade_count": len(all_trades),
        },
        "aggregate": {
            "all_trades": summarize_trades(all_trades),
            "by_symbol": _group_trades(all_trades, "symbol"),
            "by_exit_reason": _group_trades(all_trades, "exit_reason"),
        },
        "symbols": symbols,
        "research_status": "DIAGNOSTIC_ONLY",
        "promotion_status": "NOT_ELIGIBLE",
        "limitations": [
            "トレード損益はエントリー直前評価額から退出後評価額への差分で、バー内の約定順序を復元しない。",
            "固定4ロットのサイズ影響は観測された実績を分解するだけで、別ロット数の再計算やパラメータ探索は行わない。",
            "この診断はEXP-0025の下落ブレイク型だけを対象とし、VOID式のトレード単位損益比較は別成果物を直接引用しない。",
            "診断結果はpaper・shadow・live承認を意味しない。",
        ],
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
