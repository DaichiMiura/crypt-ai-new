#!/usr/bin/env python3
"""ZOOMEX公開WebSocketのmicrostructure eventを注文なしで収集する。"""

from __future__ import annotations

import argparse
import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import time
from typing import BinaryIO
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake


SNAPSHOT_ID = "DATA-2026-0010"
ENDPOINT = "wss://stream.zoomex.com/v5/public/linear"
SOURCE_SYMBOLS = (
    "BTCUSDT", "LINKUSDT", "UNIUSDT", "AVAXUSDT", "AAVEUSDT",
    "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "NEARUSDT",
)
SEALED_TARGET_SYMBOLS = ("ETCUSDT", "FILUSDT", "TRXUSDT", "XLMUSDT")
ALL_SYMBOLS = (*SOURCE_SYMBOLS, *SEALED_TARGET_SYMBOLS)
TOPIC_PREFIXES = ("orderbook.1", "publicTrade", "allLiquidation")
HEARTBEAT_SECONDS = 20
MAXIMUM_MESSAGE_BYTES = 1_048_576
MAXIMUM_SESSION_SECONDS = 86_400
RECENT_TRADE_ID_LIMIT = 100_000
ORDERBOOK_PAYLOAD_SYMBOLS = {
    "BTCUSDT": "BTC2USDT",
    "ETHUSDT": "ETH2USDT",
    "SOLUSDT": "SOL2USDT",
}


def subscription_topics() -> tuple[str, ...]:
    """固定14銘柄の公開購読topicを返す。

    Returns:
        orderbook.1、publicTrade、allLiquidationの42 topic。
    """

    return tuple(
        f"{prefix}.{symbol}" for symbol in ALL_SYMBOLS for prefix in TOPIC_PREFIXES
    )


def _utc_now() -> datetime:
    """timezone-aware UTC現在時刻を返す。

    Returns:
        UTC datetime。
    """

    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    """fileのSHA-256を返す。

    Args:
        path: 読み込むfile。

    Returns:
        16進SHA-256。
    """

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str:
    """collector実行時のGit commitを返す。

    Returns:
        HEAD commit、取得不能時はUNKNOWN。
    """

    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


class SecureGzipWriter:
    """0600のgzip NDJSON writer。"""

    def __init__(self, path: Path) -> None:
        """排他的にfileを作成する。

        Args:
            path: 新規gzip path。

        Raises:
            FileExistsError: 同名fileが存在する場合。
        """

        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.path = path
        self._raw: BinaryIO = os.fdopen(descriptor, "wb")
        self._gzip = gzip.GzipFile(filename="", mode="wb", fileobj=self._raw, mtime=0)
        self.count = 0

    def write(self, record: dict[str, object]) -> None:
        """1件をcanonical NDJSONとして追記する。

        Args:
            record: JSON化可能なevent envelope。
        """

        payload = json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        self._gzip.write(payload)
        self.count += 1
        if self.count % 1000 == 0:
            self._gzip.flush()

    def close(self) -> None:
        """gzip footerとraw fileをflushして閉じる。"""

        self._gzip.close()
        if not self._raw.closed:
            self._raw.close()


@dataclass
class CollectorState:
    """1 session内の検査状態と安全な集計値。"""

    message_counts: dict[str, dict[str, int]] = field(default_factory=dict)
    first_exchange_timestamp_ms: dict[str, int] = field(default_factory=dict)
    last_exchange_timestamp_ms: dict[str, int] = field(default_factory=dict)
    orderbook_ready: set[str] = field(default_factory=set)
    last_orderbook_update: dict[str, int] = field(default_factory=dict)
    recent_trade_ids: OrderedDict[str, None] = field(default_factory=OrderedDict)
    parse_errors: int = 0
    schema_errors: int = 0
    duplicate_trade_ids: int = 0
    orderbook_delta_before_snapshot: int = 0
    orderbook_non_increasing_updates: int = 0
    orderbook_sequence_gaps: int = 0
    subscription_acknowledgements: int = 0
    pong_messages: int = 0

    def reset_orderbooks(self) -> None:
        """再接続時に全板状態をsnapshot待ちへ戻す。"""

        self.orderbook_ready.clear()
        self.last_orderbook_update.clear()


def _topic_parts(topic: str) -> tuple[str, str]:
    """固定topicから種別とsymbolを取り出す。

    Args:
        topic: ZOOMEX topic。

    Returns:
        topic種別とsymbol。

    Raises:
        ValueError: 未許可topicの場合。
    """

    for prefix in TOPIC_PREFIXES:
        marker = f"{prefix}."
        if topic.startswith(marker):
            symbol = topic[len(marker):]
            if symbol in ALL_SYMBOLS and topic == f"{prefix}.{symbol}":
                return prefix, symbol
    raise ValueError("unexpected market topic")


def _remember_trade_id(state: CollectorState, trade_id: str) -> None:
    """bounded LRUで直近trade IDの重複を数える。

    Args:
        state: session state。
        trade_id: ZOOMEX trade ID。
    """

    if trade_id in state.recent_trade_ids:
        state.duplicate_trade_ids += 1
        state.recent_trade_ids.move_to_end(trade_id)
        return
    state.recent_trade_ids[trade_id] = None
    if len(state.recent_trade_ids) > RECENT_TRADE_ID_LIMIT:
        state.recent_trade_ids.popitem(last=False)


def _validate_market_payload(
    payload: dict[str, object], topic_type: str, symbol: str, state: CollectorState,
) -> None:
    """公開market payloadの必須shapeとsequenceを検査する。

    Args:
        payload: parse済みJSON。
        topic_type: orderbook.1、publicTrade、allLiquidation。
        symbol: 固定symbol。
        state: session state。

    Raises:
        ValueError: 必須fieldまたは型が不正な場合。
    """

    exchange_timestamp = payload.get("ts")
    if not isinstance(exchange_timestamp, int):
        raise ValueError("market payload lacks integer ts")
    key = f"{topic_type}.{symbol}"
    state.first_exchange_timestamp_ms.setdefault(key, exchange_timestamp)
    state.last_exchange_timestamp_ms[key] = exchange_timestamp
    data = payload.get("data")
    if topic_type == "orderbook.1":
        expected_payload_symbol = ORDERBOOK_PAYLOAD_SYMBOLS.get(symbol, symbol)
        if not isinstance(data, dict) or data.get("s") != expected_payload_symbol:
            raise ValueError("invalid orderbook data")
        update_id = data.get("u")
        if not isinstance(update_id, int):
            raise ValueError("orderbook lacks integer update id")
        message_type = payload.get("type")
        if message_type == "snapshot":
            state.orderbook_ready.add(symbol)
        elif message_type == "delta":
            if symbol not in state.orderbook_ready:
                state.orderbook_delta_before_snapshot += 1
            previous = state.last_orderbook_update.get(symbol)
            if previous is not None and update_id <= previous:
                state.orderbook_non_increasing_updates += 1
            elif previous is not None and update_id > previous + 1:
                state.orderbook_sequence_gaps += 1
        else:
            raise ValueError("invalid orderbook message type")
        state.last_orderbook_update[symbol] = update_id
    elif topic_type == "publicTrade":
        if not isinstance(data, list):
            raise ValueError("invalid public trade data")
        for trade in data:
            if not isinstance(trade, dict) or trade.get("s") != symbol:
                raise ValueError("invalid public trade item")
            if not all(key in trade for key in ("T", "S", "v", "p", "i")):
                raise ValueError("public trade item lacks fields")
            _remember_trade_id(state, str(trade["i"]))
    else:
        if not isinstance(data, list):
            raise ValueError("invalid liquidation data")
        for liquidation in data:
            if not isinstance(liquidation, dict) or liquidation.get("s") != symbol:
                raise ValueError("invalid liquidation item")
            if not all(key in liquidation for key in ("T", "S", "v", "p")):
                raise ValueError("liquidation item lacks fields")
    state.message_counts.setdefault(symbol, {}).setdefault(topic_type, 0)
    state.message_counts[symbol][topic_type] += 1


def process_incoming(
    raw_message: str | bytes,
    state: CollectorState,
    *,
    received_at_utc: str,
    received_monotonic_ns: int,
    connection_id: str,
) -> tuple[str, dict[str, object]]:
    """受信messageを検査し、保存先とenvelopeを返す。

    Args:
        raw_message: WebSocket message。
        state: session state。
        received_at_utc: 受信直後UTC ISO文字列。
        received_monotonic_ns: process内単調時刻。
        connection_id: 再接続ごとのID。

    Returns:
        `source`、`target`、`control`の保存先とevent envelope。
    """

    try:
        text = raw_message.decode("utf-8") if isinstance(raw_message, bytes) else raw_message
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        state.parse_errors += 1
        return "control", {
            "received_at_utc": received_at_utc,
            "received_monotonic_ns": received_monotonic_ns,
            "connection_id": connection_id,
            "parse_error": True,
            "raw_sha256": hashlib.sha256(bytes(raw_message) if isinstance(raw_message, bytes) else raw_message.encode()).hexdigest(),
        }
    envelope: dict[str, object] = {
        "received_at_utc": received_at_utc,
        "received_monotonic_ns": received_monotonic_ns,
        "connection_id": connection_id,
        "payload": payload,
    }
    if not isinstance(payload, dict):
        state.schema_errors += 1
        envelope["validation_error"] = "JSON root is not object"
        return "control", envelope
    topic = payload.get("topic")
    if not isinstance(topic, str):
        if payload.get("op") in {"subscribe", "pong"} or payload.get("ret_msg") == "pong":
            if payload.get("op") == "subscribe":
                if payload.get("success") is True:
                    state.subscription_acknowledgements += 1
                else:
                    state.schema_errors += 1
                    envelope["validation_error"] = "subscription was not acknowledged"
            else:
                state.pong_messages += 1
        return "control", envelope
    try:
        topic_type, symbol = _topic_parts(topic)
    except ValueError as error:
        state.schema_errors += 1
        envelope["validation_error"] = str(error)
        return "control", envelope
    destination = "target" if symbol in SEALED_TARGET_SYMBOLS else "source"
    try:
        _validate_market_payload(payload, topic_type, symbol, state)
    except ValueError as error:
        state.schema_errors += 1
        envelope["validation_error"] = str(error)
    return destination, envelope


def _safe_manifest_summary(manifest: dict[str, object]) -> dict[str, object]:
    """market値を含まないsession要約を返す。

    Args:
        manifest: 完全session manifest。

    Returns:
        status、件数、端点、hash、errorだけの要約。
    """

    return {
        key: manifest[key]
        for key in (
            "snapshot_id", "session_id", "status", "smoke_only", "started_at_utc",
            "ended_at_utc", "duration_seconds", "connection_count", "reconnect_count",
            "heartbeats_sent", "message_counts", "first_exchange_timestamp_ms",
            "last_exchange_timestamp_ms", "parse_errors", "schema_errors",
            "duplicate_trade_ids", "orderbook_delta_before_snapshot",
            "orderbook_non_increasing_updates", "orderbook_sequence_gaps", "artifacts",
            "orders_sent", "authentication_used", "sealed_target_content_displayed",
        )
    }


def _session_status(
    *, smoke_only: bool, elapsed: bool, connection_count: int,
    subscription_acknowledgements: int, market_records: int,
    parse_errors: int, schema_errors: int,
) -> str:
    """接続とmarket受信を満たしたsession statusを返す。

    Args:
        smoke_only: smoke sessionの場合True。
        elapsed: 指定durationまで処理した場合True。
        connection_count: 成功した接続数。
        subscription_acknowledgements: 成功subscribe応答数。
        market_records: sourceとtargetの保存record数。
        parse_errors: JSON parse error数。
        schema_errors: payload schema error数。

    Returns:
        完了または不完全status。
    """

    complete = (
        elapsed and connection_count > 0
        and subscription_acknowledgements > 0 and market_records > 0
        and parse_errors == 0 and schema_errors == 0
    )
    if not complete:
        return "INCOMPLETE"
    return "SMOKE_ONLY_COMPLETE" if smoke_only else "FORWARD_SESSION_COMPLETE"


async def collect_session(
    output_root: Path, duration_seconds: int, *, smoke_only: bool,
) -> dict[str, object]:
    """固定topicを指定秒数収集してsession manifestを返す。

    Args:
        output_root: session directoryを作るroot。
        duration_seconds: 収集秒数。
        smoke_only: 研究coverageに含めない接続確認の場合True。

    Returns:
        market値を含まないsession manifest。

    Raises:
        ValueError: durationが安全範囲外の場合。
    """

    if not 5 <= duration_seconds <= MAXIMUM_SESSION_SECONDS:
        raise ValueError("duration_seconds must be between 5 and 86400")
    started = _utc_now()
    session_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:12]}"
    session_dir = output_root / ("smoke" if smoke_only else "forward") / session_id
    session_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(session_dir, 0o700)
    paths = {
        "source": session_dir / "source-events.jsonl.gz",
        "target": session_dir / "sealed-target-events.jsonl.gz",
        "control": session_dir / "control-events.jsonl.gz",
    }
    writers = {name: SecureGzipWriter(path) for name, path in paths.items()}
    state = CollectorState()
    started_monotonic = time.monotonic()
    deadline = started_monotonic + duration_seconds
    connection_count = 0
    reconnect_count = 0
    heartbeats_sent = 0
    network_errors: list[str] = []
    completed = False
    try:
        while time.monotonic() < deadline:
            connection_id = uuid4().hex
            state.reset_orderbooks()
            try:
                async with connect(
                    ENDPOINT,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    max_size=MAXIMUM_MESSAGE_BYTES,
                    max_queue=1024,
                    proxy=None,
                ) as websocket:
                    connection_count += 1
                    if connection_count > 1:
                        reconnect_count += 1
                    subscribe = {"op": "subscribe", "args": list(subscription_topics())}
                    await websocket.send(json.dumps(subscribe, separators=(",", ":")))
                    writers["control"].write({
                        "sent_at_utc": _utc_now().isoformat(),
                        "connection_id": connection_id,
                        "outgoing": {"op": "subscribe", "topic_count": len(subscribe["args"])},
                    })
                    last_heartbeat = time.monotonic()
                    while time.monotonic() < deadline:
                        now = time.monotonic()
                        timeout = min(deadline - now, HEARTBEAT_SECONDS - (now - last_heartbeat))
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=max(timeout, 0.001))
                        except TimeoutError:
                            if time.monotonic() >= deadline:
                                break
                            await websocket.send('{"op":"ping"}')
                            last_heartbeat = time.monotonic()
                            heartbeats_sent += 1
                            writers["control"].write({
                                "sent_at_utc": _utc_now().isoformat(),
                                "connection_id": connection_id,
                                "outgoing": {"op": "ping"},
                            })
                            continue
                        received = _utc_now().isoformat()
                        destination, envelope = process_incoming(
                            raw,
                            state,
                            received_at_utc=received,
                            received_monotonic_ns=time.monotonic_ns(),
                            connection_id=connection_id,
                        )
                        writers[destination].write(envelope)
            except (ConnectionClosed, InvalidHandshake, OSError, TimeoutError, EOFError) as error:
                network_errors.append(type(error).__name__)
                if len(network_errors) > 20:
                    network_errors = network_errors[-20:]
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(min(2.0, remaining))
        completed = True
    finally:
        for writer in writers.values():
            writer.close()
    ended = _utc_now()
    artifacts = {
        name: {
            "path": str(path.relative_to(session_dir)),
            "bytes": path.stat().st_size,
            "records": writers[name].count,
            "sha256": _sha256(path),
        }
        for name, path in paths.items()
    }
    market_records = writers["source"].count + writers["target"].count
    manifest: dict[str, object] = {
        "schema_version": 1,
        "snapshot_id": SNAPSHOT_ID,
        "session_id": session_id,
        "status": _session_status(
            smoke_only=smoke_only,
            elapsed=completed,
            connection_count=connection_count,
            subscription_acknowledgements=state.subscription_acknowledgements,
            market_records=market_records,
            parse_errors=state.parse_errors,
            schema_errors=state.schema_errors,
        ),
        "smoke_only": smoke_only,
        "started_at_utc": started.isoformat(),
        "ended_at_utc": ended.isoformat(),
        "duration_seconds": duration_seconds,
        "endpoint": ENDPOINT,
        "topics": subscription_topics(),
        "source_symbols": SOURCE_SYMBOLS,
        "sealed_target_symbols": SEALED_TARGET_SYMBOLS,
        "orderbook_payload_symbol_aliases": ORDERBOOK_PAYLOAD_SYMBOLS,
        "code_commit": _git_commit(),
        "websockets_version": version("websockets"),
        "connection_count": connection_count,
        "reconnect_count": reconnect_count,
        "heartbeats_sent": heartbeats_sent,
        "network_error_types": network_errors,
        "message_counts": state.message_counts,
        "first_exchange_timestamp_ms": state.first_exchange_timestamp_ms,
        "last_exchange_timestamp_ms": state.last_exchange_timestamp_ms,
        "parse_errors": state.parse_errors,
        "schema_errors": state.schema_errors,
        "duplicate_trade_ids": state.duplicate_trade_ids,
        "recent_trade_id_window": RECENT_TRADE_ID_LIMIT,
        "orderbook_delta_before_snapshot": state.orderbook_delta_before_snapshot,
        "orderbook_non_increasing_updates": state.orderbook_non_increasing_updates,
        "orderbook_sequence_gaps": state.orderbook_sequence_gaps,
        "subscription_acknowledgements": state.subscription_acknowledgements,
        "pong_messages": state.pong_messages,
        "artifacts": artifacts,
        "authentication_used": False,
        "orders_sent": False,
        "sealed_target_content_displayed": False,
    }
    manifest_path = session_dir / "manifest.json"
    descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return _safe_manifest_summary(manifest)


def main() -> None:
    """CLI引数を検査し、公開market data収集sessionを実行する。"""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root", type=Path, default=Path("data/raw/DATA-2026-0010")
    )
    parser.add_argument("--duration-seconds", type=int, default=3600)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    summary = asyncio.run(
        collect_session(args.output_root, args.duration_seconds, smoke_only=args.smoke)
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
