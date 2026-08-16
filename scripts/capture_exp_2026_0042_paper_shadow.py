#!/usr/bin/env python3
"""EXP-2026-0042の公開データpaper/shadow観測を開始する。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

from scripts.download_zoomex_exp_2026_0015_data import _get_json  # noqa: E402


SYMBOLS = ("LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT")
LOOKBACK_BARS = 360
REBALANCE_DELTA = pd.Timedelta(days=7)
REBALANCE_ANCHOR = pd.Timestamp("2022-02-01T00:00:00Z")
BAR_DELTA = pd.Timedelta(hours=2)
LOT_NOTIONAL = Decimal("200")
FEE_RATE = Decimal("0.0006")
HALF_SPREAD = Decimal("0.0005")
SLIPPAGE = Decimal("0.0005")


def _normalize_closed_bars(
    rows: list[list[str]], server_time: pd.Timestamp
) -> pd.DataFrame:
    """API Klineを確定済み2時間足へ正規化する。

    Args:
        rows: ZOOMEX Kline配列。
        server_time: API応答のサーバー時刻。

    Returns:
        時刻昇順の確定済みOHLC。

    Raises:
        ValueError: warm-up、重複、連続性、数値が不正な場合。
    """

    frame = pd.DataFrame(
        {
            "event_time": pd.to_datetime(
                [int(row[0]) for row in rows], unit="ms", utc=True
            ),
            "open": [row[1] for row in rows],
            "high": [row[2] for row in rows],
            "low": [row[3] for row in rows],
            "close": [row[4] for row in rows],
        }
    ).sort_values("event_time")
    frame = frame[frame["event_time"] + BAR_DELTA <= server_time].reset_index(drop=True)
    if len(frame) < LOOKBACK_BARS + 2:
        raise ValueError("insufficient closed-bar warmup")
    if frame["event_time"].duplicated().any():
        raise ValueError("duplicate closed bars")
    expected = pd.date_range(
        frame.iloc[0]["event_time"], frame.iloc[-1]["event_time"], freq=BAR_DELTA
    )
    if list(frame["event_time"]) != list(expected):
        raise ValueError("closed 2-hour bars are not continuous")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def _latest_targets(frames: dict[str, pd.DataFrame]) -> dict[str, object]:
    """固定仕様から最新のshadow targetを計算する。

    Args:
        frames: 共通時刻を持つ銘柄別確定足。

    Returns:
        最新足、rebalance選定、最下位退出後target。

    Raises:
        ValueError: 銘柄、時刻、またはwarm-upが不正な場合。
    """

    if tuple(frames) != SYMBOLS:
        raise ValueError("symbols differ from frozen strategy")
    times = [tuple(frame["event_time"]) for frame in frames.values()]
    if any(times[0] != value for value in times[1:]):
        raise ValueError("timestamps differ")
    latest = frames[SYMBOLS[0]].iloc[-1]["event_time"]
    periods = int((latest - REBALANCE_ANCHOR) // REBALANCE_DELTA)
    rebalance_time = REBALANCE_ANCHOR + periods * REBALANCE_DELTA
    matches = frames[SYMBOLS[0]].index[
        frames[SYMBOLS[0]]["event_time"] == rebalance_time
    ]
    if matches.empty or int(matches[0]) < LOOKBACK_BARS:
        raise ValueError("latest rebalance lacks warmup")
    rebalance_index = int(matches[0])

    def returns_at(index: int) -> dict[str, float]:
        """指定足の30日リターンを計算する。"""

        return {
            symbol: float(
                frames[symbol].iloc[index]["close"]
                / frames[symbol].iloc[index - LOOKBACK_BARS]["close"]
                - 1
            )
            for symbol in SYMBOLS
        }

    rebalance_returns = returns_at(rebalance_index)
    median = float(pd.Series(rebalance_returns).median())
    ordered = sorted(SYMBOLS, key=lambda symbol: (-rebalance_returns[symbol], symbol))
    selected = ordered[:2] if median > 0 else []
    target = list(selected)
    triggers: dict[str, str] = {}
    for index in range(rebalance_index + 1, len(times[0])):
        values = returns_at(index)
        last_symbol = sorted(SYMBOLS, key=lambda symbol: (-values[symbol], symbol))[-1]
        if last_symbol in target:
            target.remove(last_symbol)
            triggers[last_symbol] = frames[last_symbol].iloc[index]["event_time"].isoformat()
    return {
        "latest_closed_bar": latest.isoformat(),
        "rebalance_time": rebalance_time.isoformat(),
        "market_median_momentum": median,
        "base_selected_symbols": selected,
        "target_symbols": target,
        "last_rank_exit_triggers": triggers,
        "latest_close": {
            symbol: str(frames[symbol].iloc[-1]["close"]) for symbol in SYMBOLS
        },
    }


def _decimal(value: object) -> Decimal:
    """値をDecimalへ変換する。

    Args:
        value: 数値として解釈できる値。

    Returns:
        二進浮動小数点を経由しないDecimal。
    """

    return Decimal(str(value))


def _execution_price(open_price: object, side: str) -> Decimal:
    """paper成行のspread・slippage込み約定価格を計算する。

    Args:
        open_price: 次足open。
        side: `buy`または`sell`。

    Returns:
        不利側へ調整した仮想約定価格。

    Raises:
        ValueError: sideまたは価格が不正な場合。
    """

    price = _decimal(open_price)
    if price <= 0:
        raise ValueError("execution open price must be positive")
    if side == "buy":
        return price * (Decimal("1") + HALF_SPREAD + SLIPPAGE)
    if side == "sell":
        return price * (Decimal("1") - HALF_SPREAD - SLIPPAGE)
    raise ValueError(f"unknown execution side: {side}")


def _event_id(event: dict[str, object]) -> str:
    """paper台帳イベントの決定論的IDを作る。

    Args:
        event: ID以外のイベントpayload。

    Returns:
        SHA-256の先頭24文字。
    """

    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def _apply_pending_orders(
    state: dict[str, object], frames: dict[str, pd.DataFrame]
) -> list[dict[str, object]]:
    """予約targetを次の確定足openでpaper約定する。

    Args:
        state: 永続paper状態。
        frames: 最新確定足を含む銘柄別DataFrame。

    Returns:
        追記するENTRY・EXITイベント。

    Raises:
        ValueError: 予約時刻、残高、position状態が不正な場合。
    """

    pending_from = pd.Timestamp(state["pending_from_bar"])
    execution_time = pending_from + BAR_DELTA
    latest = frames[SYMBOLS[0]].iloc[-1]["event_time"]
    if latest < execution_time:
        return []
    positions = state["positions"]
    if not isinstance(positions, dict):
        raise ValueError("paper positions must be a mapping")
    targets = set(state["pending_target_symbols"])
    cash = _decimal(state["cash_usdt"])
    events: list[dict[str, object]] = []
    for symbol in sorted(set(positions) - targets):
        rows = frames[symbol][frames[symbol]["event_time"] == execution_time]
        if rows.empty:
            raise ValueError(f"missing paper exit bar: {symbol}")
        position = positions.pop(symbol)
        price = _execution_price(rows.iloc[0]["open"], "sell")
        quantity = _decimal(position["quantity"])
        fee = quantity * price * FEE_RATE
        realized = quantity * (price - _decimal(position["entry_price"])) - fee
        cash += realized
        event = {
            "event_time": execution_time.isoformat(),
            "event_type": "EXIT",
            "symbol": symbol,
            "quantity": str(quantity),
            "execution_price": str(price),
            "fee": str(fee),
            "realized_pnl_after_exit_fee": str(realized),
            "source": "paper",
        }
        events.append({"event_id": _event_id(event), **event})
    for symbol in sorted(targets - set(positions)):
        rows = frames[symbol][frames[symbol]["event_time"] == execution_time]
        if rows.empty:
            raise ValueError(f"missing paper entry bar: {symbol}")
        price = _execution_price(rows.iloc[0]["open"], "buy")
        quantity = LOT_NOTIONAL / price
        fee = LOT_NOTIONAL * FEE_RATE
        if cash - fee < Decimal("200"):
            raise ValueError("paper reserve cash would be breached")
        cash -= fee
        positions[symbol] = {
            "quantity": str(quantity),
            "entry_price": str(price),
            "entry_notional": str(LOT_NOTIONAL),
            "opened_at": execution_time.isoformat(),
            "last_funding_time": execution_time.isoformat(),
        }
        event = {
            "event_time": execution_time.isoformat(),
            "event_type": "ENTRY",
            "symbol": symbol,
            "quantity": str(quantity),
            "execution_price": str(price),
            "fee": str(fee),
            "source": "paper",
        }
        events.append({"event_id": _event_id(event), **event})
    state["cash_usdt"] = str(cash)
    state["positions"] = positions
    state["last_execution_bar"] = execution_time.isoformat()
    return events


def _mark_equity(
    state: dict[str, object], frames: dict[str, pd.DataFrame]
) -> Decimal:
    """最新closeでpaper equityを時価評価する。

    Args:
        state: cashとpositionを含むpaper状態。
        frames: 最新確定足を含む銘柄別DataFrame。

    Returns:
        cashと未実現損益の合計。
    """

    equity = _decimal(state["cash_usdt"])
    for symbol, position in state["positions"].items():
        close = _decimal(frames[symbol].iloc[-1]["close"])
        equity += _decimal(position["quantity"]) * (
            close - _decimal(position["entry_price"])
        )
    return equity


def _apply_funding(
    state: dict[str, object], frames: dict[str, pd.DataFrame]
) -> list[dict[str, object]]:
    """公開Funding履歴を保有longのpaper cashへ反映する。

    Args:
        state: positionと最終Funding時刻を含むpaper状態。
        frames: Funding時点のcloseを含む銘柄別DataFrame。

    Returns:
        新規に反映したFundingイベント。

    Raises:
        ValueError: Funding時刻に対応する価格がない場合。
    """

    cash = _decimal(state["cash_usdt"])
    latest_close_time = frames[SYMBOLS[0]].iloc[-1]["event_time"] + BAR_DELTA
    events: list[dict[str, object]] = []
    for symbol, position in state["positions"].items():
        payload = _get_json(
            "/cloud/trade/v3/market/funding/history",
            {"category": "linear", "symbol": symbol, "limit": 200},
        )
        last_time = pd.Timestamp(position["last_funding_time"])
        records = sorted(
            payload["result"]["list"],
            key=lambda row: int(row["fundingRateTimestamp"]),
        )
        for record in records:
            funding_time = pd.Timestamp(
                int(record["fundingRateTimestamp"]), unit="ms", tz="UTC"
            )
            if funding_time <= last_time or funding_time > latest_close_time:
                continue
            price_rows = frames[symbol][frames[symbol]["event_time"] == funding_time]
            if price_rows.empty:
                raise ValueError(f"missing funding valuation bar: {symbol}")
            notional = _decimal(position["quantity"]) * _decimal(
                price_rows.iloc[0]["close"]
            )
            rate = _decimal(record["fundingRate"])
            cash_flow = -(notional * rate)
            cash += cash_flow
            event = {
                "event_time": funding_time.isoformat(),
                "event_type": "FUNDING",
                "symbol": symbol,
                "notional": str(notional),
                "funding_rate": str(rate),
                "cash_flow": str(cash_flow),
                "source": "paper",
            }
            events.append({"event_id": _event_id(event), **event})
            position["last_funding_time"] = funding_time.isoformat()
    state["cash_usdt"] = str(cash)
    return sorted(events, key=lambda event: (event["event_time"], event["symbol"]))


def _append_events(path: Path, events: list[dict[str, object]]) -> None:
    """paperイベントを追記専用JSONL台帳へ保存する。

    Args:
        path: 台帳ファイル。
        events: 時系列順イベント。
    """

    if not events:
        return
    with path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def main() -> None:
    """公開Klineを取得しshadow観測と初回paper予約を保存する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime-dir", type=Path, default=Path("var/paper-shadow/EXP-2026-0042")
    )
    args = parser.parse_args()
    frames: dict[str, pd.DataFrame] = {}
    server_times: list[pd.Timestamp] = []
    for symbol in SYMBOLS:
        payload = _get_json(
            "/cloud/trade/v3/market/kline",
            {"category": "linear", "symbol": symbol, "interval": "120", "limit": 500},
        )
        server_time = pd.Timestamp(int(payload["time"]), unit="ms", tz="UTC")
        server_times.append(server_time)
        frames[symbol] = _normalize_closed_bars(payload["result"]["list"], server_time)
    snapshot = _latest_targets(frames)
    snapshot.update(
        {
            "strategy_id": "EXP-2026-0042",
            "strategy_version": "1.0.0-frozen",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "venue_server_time_max": max(server_times).isoformat(),
            "source": "ZOOMEX public V3 REST",
            "real_orders_allowed": False,
            "order_intents": [
                {"symbol": symbol, "side": "long", "notional_usdt": "200"}
                for symbol in snapshot["target_symbols"]
            ],
        }
    )
    args.runtime_dir.mkdir(parents=True, exist_ok=True)
    with (args.runtime_dir / "shadow-snapshots.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    state_path = args.runtime_dir / "paper-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        events = _apply_pending_orders(state, frames)
        events.extend(_apply_funding(state, frames))
        _append_events(args.runtime_dir / "paper-ledger.jsonl", events)
        state["status"] = "RUNNING"
        state["pending_target_symbols"] = snapshot["target_symbols"]
        state["pending_from_bar"] = snapshot["latest_closed_bar"]
        state["last_observed_bar"] = snapshot["latest_closed_bar"]
        state["equity_usdt"] = str(_mark_equity(state, frames))
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(state_path)
    else:
        state = {
                    "strategy_id": "EXP-2026-0042",
                    "status": "AWAITING_NEXT_COMPLETED_BAR",
                    "cash_usdt": "1000",
                    "equity_usdt": "1000",
                    "positions": {},
                    "pending_target_symbols": snapshot["target_symbols"],
                    "pending_from_bar": snapshot["latest_closed_bar"],
                    "last_observed_bar": snapshot["latest_closed_bar"],
                    "real_orders_allowed": False,
                }
        state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
