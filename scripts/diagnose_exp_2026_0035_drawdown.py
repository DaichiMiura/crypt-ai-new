#!/usr/bin/env python3
"""EXP-2026-0035 momentum_top2の最大ドローダウン要因を診断する。"""

from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal
import json
from pathlib import Path

import pandas as pd


def _decimal(value: object) -> Decimal:
    """値をDecimalへ変換する。

    Args:
        value: 数値として解釈できる値。

    Returns:
        二進浮動小数点を経由しないDecimal。
    """

    return Decimal(str(value))


def _drawdown_episode(equity: pd.DataFrame) -> dict[str, object]:
    """equity曲線から最大ドローダウン区間を特定する。

    Args:
        equity: event_timeとequityを含む時系列。

    Returns:
        peak、trough、回復時刻と下落率。
    """

    frame = equity.copy()
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    frame["equity"] = pd.to_numeric(frame["equity"], errors="raise")
    frame["running_peak"] = frame["equity"].cummax()
    frame["drawdown"] = frame["equity"] / frame["running_peak"] - 1
    trough_index = frame["drawdown"].idxmin()
    trough = frame.loc[trough_index]
    prior = frame.loc[:trough_index]
    peak_index = prior["equity"].idxmax()
    peak = frame.loc[peak_index]
    recovery = frame[
        (frame.index > trough_index) & (frame["equity"] >= peak["equity"])
    ]
    recovery_time = None if recovery.empty else recovery.iloc[0]["event_time"]
    return {
        "peak_time": peak["event_time"],
        "peak_equity": _decimal(peak["equity"]),
        "trough_time": trough["event_time"],
        "trough_equity": _decimal(trough["equity"]),
        "max_drawdown": _decimal(trough["drawdown"]),
        "recovery_time": recovery_time,
    }


def _reconstruct_trades(events: pd.DataFrame) -> pd.DataFrame:
    """entry、Funding、exitイベントから銘柄別取引損益を再構築する。

    Args:
        events: momentum_top2の監査イベント。

    Returns:
        取引ごとの期間、価格損益、Funding、手数料、純損益。

    Raises:
        ValueError: entryとexitの対応が崩れている場合。
    """

    frame = events.copy()
    frame["event_time"] = pd.to_datetime(frame["event_time"], utc=True)
    open_trades: dict[str, dict[str, object]] = {}
    rows: list[dict[str, object]] = []
    for event in frame.to_dict("records"):
        symbol = str(event["symbol"])
        event_type = event["event_type"]
        if event_type == "ENTRY":
            if symbol in open_trades:
                raise ValueError(f"duplicate open trade: {symbol}")
            open_trades[symbol] = {
                "symbol": symbol,
                "entry_time": event["event_time"],
                "entry_notional": _decimal(event["notional"]),
                "entry_fee": _decimal(event["fee"]),
                "funding": Decimal("0"),
            }
        elif event_type == "FUNDING" and symbol in open_trades:
            open_trades[symbol]["funding"] += _decimal(event["funding_delta"])
        elif event_type == "EXIT":
            if symbol not in open_trades:
                raise ValueError(f"exit without entry: {symbol}")
            trade = open_trades.pop(symbol)
            exit_notional = _decimal(event["notional"])
            exit_fee = _decimal(event["fee"])
            price_pnl = exit_notional - trade["entry_notional"]
            net_pnl = (
                price_pnl + trade["funding"] - trade["entry_fee"] - exit_fee
            )
            rows.append(
                {
                    **trade,
                    "exit_time": event["event_time"],
                    "exit_notional": exit_notional,
                    "exit_fee": exit_fee,
                    "price_pnl": price_pnl,
                    "net_pnl": net_pnl,
                    "return_on_notional": net_pnl / trade["entry_notional"],
                }
            )
    if open_trades:
        raise ValueError(f"unclosed trades: {sorted(open_trades)}")
    return pd.DataFrame(rows)


def _aggregate_symbols(trades: pd.DataFrame) -> list[dict[str, object]]:
    """取引損益を銘柄別に集計する。

    Args:
        trades: 再構築済み取引DataFrame。

    Returns:
        銘柄別の取引数、純損益、勝率、平均損益。
    """

    rows: list[dict[str, object]] = []
    for symbol, group in trades.groupby("symbol", sort=True):
        net = group["net_pnl"].map(_decimal)
        rows.append(
            {
                "symbol": symbol,
                "trade_count": len(group),
                "net_pnl": sum(net, Decimal("0")),
                "win_rate": Decimal(sum(value > 0 for value in net))
                / Decimal(len(net)),
                "average_net_pnl": sum(net, Decimal("0")) / Decimal(len(net)),
            }
        )
    return rows


def _episode_contributions(
    trades: pd.DataFrame, peak_time: pd.Timestamp, trough_time: pd.Timestamp
) -> list[dict[str, object]]:
    """最大DD区間中に決済された取引を銘柄別に集計する。

    Args:
        trades: 再構築済み取引DataFrame。
        peak_time: 最大DDの起点。
        trough_time: 最大DDの底。

    Returns:
        区間内決済の銘柄別損益と件数。
    """

    episode = trades[
        (trades["exit_time"] > peak_time) & (trades["exit_time"] <= trough_time)
    ]
    pnl: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: defaultdict[str, int] = defaultdict(int)
    for row in episode.to_dict("records"):
        pnl[str(row["symbol"])] += _decimal(row["net_pnl"])
        counts[str(row["symbol"])] += 1
    return [
        {"symbol": symbol, "trade_count": counts[symbol], "net_pnl": value}
        for symbol, value in sorted(pnl.items(), key=lambda item: item[1])
    ]


def _report_artifact(
    equity: pd.DataFrame,
    payload: dict[str, object],
) -> dict[str, object]:
    """診断結果からポータブルHTML用のレポートartifactを作る。

    Args:
        equity: 元の2時間足equity曲線。
        payload: 診断結果payload。

    Returns:
        Data Analytics report contractに準拠するartifact。
    """

    curve = equity.copy()
    curve["event_time"] = pd.to_datetime(curve["event_time"], utc=True)
    curve["equity"] = pd.to_numeric(curve["equity"], errors="raise")
    curve = curve.set_index("event_time").resample("MS").last().dropna().reset_index()
    curve["running_peak"] = curve["equity"].cummax()
    curve_rows = [
        {
            "month": row.event_time.strftime("%Y-%m"),
            "equity": round(float(row.equity), 2),
            "running_peak": round(float(row.running_peak), 2),
        }
        for row in curve.itertuples()
    ]
    contributions = [
        {
            "symbol": row["symbol"],
            "net_pnl": round(float(row["net_pnl"]), 2),
            "trade_count": row["trade_count"],
        }
        for row in payload["drawdown_episode_closed_trade_contributions"]
    ]
    worst = [
        {
            "symbol": row["symbol"],
            "entry_date": row["entry_time"].strftime("%Y-%m-%d"),
            "exit_date": row["exit_time"].strftime("%Y-%m-%d"),
            "net_pnl": round(float(row["net_pnl"]), 2),
            "return_rate": round(float(row["return_on_notional"]), 4),
        }
        for row in payload["worst_trades"][:6]
    ]
    episode = payload["drawdown_episode"]
    total_decline = episode["trough_equity"] - episode["peak_equity"]
    link_loss = next(
        row["net_pnl"]
        for row in payload["drawdown_episode_closed_trade_contributions"]
        if row["symbol"] == "LINKUSDT"
    )
    return {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": "4銘柄top2の最大ドローダウン診断",
            "generatedAt": "2026-08-16T00:00:00Z",
            "sources": [
                {
                    "id": "exp0035",
                    "label": "EXP-2026-0035バックテスト成果物",
                    "path": "artifacts/EXP-2026-0035/momentum_top2-equity.csv",
                    "query": {
                        "engine": "DuckDB",
                        "language": "sql",
                        "sql": "SELECT date_trunc('month', event_time::TIMESTAMPTZ) AS month, arg_max(equity, event_time) AS equity FROM read_csv_auto('artifacts/EXP-2026-0035/momentum_top2-equity.csv') GROUP BY 1 ORDER BY 1",
                    },
                },
                {
                    "id": "diagnostic",
                    "label": "最大DD診断結果",
                    "path": "artifacts/EXP-2026-0035-drawdown-diagnostic/summary.json",
                    "query": {
                        "engine": "DuckDB",
                        "language": "sql",
                        "sql": "SELECT * FROM read_json_auto('artifacts/EXP-2026-0035-drawdown-diagnostic/summary.json')",
                    },
                },
                {
                    "id": "diagnostic-trades",
                    "label": "再構築済み取引明細",
                    "path": "artifacts/EXP-2026-0035-drawdown-diagnostic/trades.csv",
                    "query": {
                        "engine": "DuckDB",
                        "language": "sql",
                        "sql": "SELECT symbol, entry_time, exit_time, net_pnl, return_on_notional FROM read_csv_auto('artifacts/EXP-2026-0035-drawdown-diagnostic/trades.csv') ORDER BY net_pnl ASC",
                    },
                },
            ],
            "charts": [
                {
                    "id": "equity-curve",
                    "title": "月末資産と過去最高資産",
                    "subtitle": "2022年2月から2025年12月、USDT",
                    "type": "line",
                    "intent": "trend",
                    "dataset": "monthly_equity",
                    "sourceId": "exp0035",
                    "encodings": {
                        "x": {"field": "month", "type": "temporal"},
                        "y": {
                            "fields": ["equity", "running_peak"],
                            "type": "quantitative",
                            "format": "number",
                        },
                    },
                    "xAxisTitle": "月",
                    "yAxisTitle": "USDT",
                    "valueFormat": "number",
                    "layout": "full",
                },
                {
                    "id": "episode-contribution",
                    "title": "最大DD区間中に決済した取引の銘柄別純損益",
                    "subtitle": "2022年4月1日から2023年8月7日、手数料・Funding込み、USDT",
                    "type": "bar",
                    "intent": "comparison",
                    "dataset": "episode_contributions",
                    "sourceId": "diagnostic-trades",
                    "encodings": {
                        "x": {"field": "symbol", "type": "nominal"},
                        "y": {
                            "field": "net_pnl",
                            "type": "quantitative",
                            "format": "number",
                        },
                    },
                    "xAxisTitle": "銘柄",
                    "yAxisTitle": "純損益（USDT）",
                    "valueFormat": "number",
                    "layout": "full",
                },
            ],
            "tables": [
                {
                    "id": "worst-trades",
                    "title": "損失上位6取引",
                    "subtitle": "固定200 USDTロット、手数料・Funding込み",
                    "dataset": "worst_trades",
                    "sourceId": "diagnostic-trades",
                    "defaultSort": {"field": "net_pnl", "direction": "asc"},
                    "density": "spacious",
                    "layout": "full",
                    "columns": [
                        {"field": "symbol", "label": "銘柄", "type": "text"},
                        {"field": "entry_date", "label": "entry", "type": "date"},
                        {"field": "exit_date", "label": "exit", "type": "date"},
                        {
                            "field": "net_pnl",
                            "label": "純損益（USDT）",
                            "format": "number",
                            "movement": True,
                        },
                        {
                            "field": "return_rate",
                            "label": "元本比",
                            "format": "percent",
                            "movement": True,
                        },
                    ],
                }
            ],
            "blocks": [
                {
                    "id": "title",
                    "type": "markdown",
                    "body": "# 4銘柄top2の最大ドローダウン診断",
                },
                {
                    "id": "summary",
                    "type": "markdown",
                    "sourceId": "diagnostic",
                    "body": (
                        "## Executive Summary\n\n"
                        f"- **最大DDは一度の暴落ではなく、約16か月続いた損失局面です。** 資産は{episode['peak_equity']:.2f} USDTから{episode['trough_equity']:.2f} USDTへ低下し、最大DDは{episode['max_drawdown']:.2%}でした。\n"
                        f"- **LINKの決済損失が最大の集中要因です。** 区間内のLINK純損失は{link_loss:.2f} USDTで、区間全体の資産減少{total_decline:.2f} USDTの約{abs(link_loss / total_decline):.1%}に相当します。\n"
                        "- **ただしLINKだけを除外する根拠にはなりません。** 4銘柄すべてが区間内で損失となり、全期間では全銘柄が純利益でした。問題は銘柄固有というより、週次退出が急反転へ遅れる構造にあります。"
                    ),
                },
                {
                    "id": "definition",
                    "type": "markdown",
                    "body": "## 何を診断したか\n\n対象はEXP-2026-0035の4銘柄top2です。最大DDは、各2時間足の資産をそれ以前の最高資産と比較して測定しました。取引損益はentryとexitを対応付け、手数料と保有中のFundingを含めて再構築しています。",
                },
                {"id": "equity-chart", "type": "chart", "chartId": "equity-curve"},
                {
                    "id": "duration-finding",
                    "type": "markdown",
                    "sourceId": "diagnostic",
                    "body": "## 損失は長期化したが、その後は回復した\n\n最大DDは2022年4月1日に始まり、2023年8月7日に底を付け、2023年12月9日に元の高値を回復しました。最大DDだけを見ると恒久的な破綻に見えますが、実際には長い低迷期間です。したがって、単純な銘柄除外よりも、急反転時の週次退出遅延を抑えられるかを検証する方が再利用可能です。",
                },
                {"id": "contribution-chart", "type": "chart", "chartId": "episode-contribution"},
                {
                    "id": "contribution-finding",
                    "type": "markdown",
                    "sourceId": "diagnostic",
                    "body": "## LINKへ損失が集中したが、全銘柄が下落に参加した\n\n区間内決済損益はLINK -180.91、AAVE -55.89、AVAX -55.31、UNI -32.73 USDTでした。LINKが最大ですが、全銘柄がマイナスです。また全期間ではLINKを含む4銘柄すべてが純利益でした。LINKの事後除外は過学習になりやすく、採用しません。",
                },
                {"id": "worst-table", "type": "table", "tableId": "worst-trades"},
                {
                    "id": "next",
                    "type": "markdown",
                    "body": "## 次に検証すること\n\n1. 4銘柄top2と固定ロットは維持します。\n2. 新しいentry条件は追加せず、週次保有中に価格が大きく反転した場合だけ早期退出する単一ルールを事前登録します。\n3. LINK除外や閾値の総当たりは行いません。候補ルールは1つに固定し、元のtop2と同額で比較します。",
                },
                {
                    "id": "questions",
                    "type": "markdown",
                    "body": "## Further Questions\n\n- 早期退出が大損を削る一方、後の反発利益まで失わないか。\n- 退出条件を価格ベース、ATRベース、相対順位低下のどれにすると経済的説明が最も明確か。\n- 最大DDだけでなく最終資産とインデックス基準を維持できるか。",
                },
                {
                    "id": "caveats",
                    "type": "markdown",
                    "body": "## Caveats and Assumptions\n\n区間内銘柄寄与は、その期間中に決済した取引の実現損益です。最大DDの底に残る未実現損益は銘柄別に配賦していないため、寄与の合計はpeak-to-trough減少額と一致しません。この診断は単一過去期間に基づき、直接paper・shadow・liveへ進める根拠にはなりません。",
                },
            ],
        },
        "snapshot": {
            "version": 1,
            "generatedAt": "2026-08-16T00:00:00Z",
            "status": "ready",
            "datasets": {
                "monthly_equity": curve_rows,
                "episode_contributions": contributions,
                "worst_trades": worst,
            },
        },
        "sources": [],
    }


def main() -> None:
    """最大DDと取引損益を診断してJSON・CSVへ保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact-dir", type=Path, default=Path("artifacts/EXP-2026-0035")
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/EXP-2026-0035-drawdown-diagnostic"),
    )
    args = parser.parse_args()
    equity = pd.read_csv(args.artifact_dir / "momentum_top2-equity.csv")
    events = pd.read_csv(args.artifact_dir / "momentum_top2-events.csv")
    episode = _drawdown_episode(equity)
    trades = _reconstruct_trades(events)
    contributions = _episode_contributions(
        trades, episode["peak_time"], episode["trough_time"]
    )
    worst = trades.sort_values("net_pnl").head(10).to_dict("records")
    payload = {
        "source_experiment_id": "EXP-2026-0035",
        "arm": "momentum_top2",
        "drawdown_episode": episode,
        "all_period_symbol_contributions": _aggregate_symbols(trades),
        "drawdown_episode_closed_trade_contributions": contributions,
        "worst_trades": worst,
        "reconciliation": {
            "closed_trade_net_pnl": sum(
                trades["net_pnl"].map(_decimal), Decimal("0")
            ),
            "source_final_minus_initial_equity": _decimal(equity.iloc[-1]["equity"])
            - _decimal(equity.iloc[0]["equity"]),
        },
        "limitations": [
            "区間寄与は最大DD区間中に決済された取引の実現損益であり、底時点の未実現損益を銘柄へ配賦しない。",
            "単一過去期間の診断であり、ここから直接売買条件を採用しない。",
        ],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(args.output_dir / "trades.csv", index=False)
    (args.output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "artifact.json").write_text(
        json.dumps(_report_artifact(equity, payload), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
