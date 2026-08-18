import json
import gzip
import stat

from scripts.collect_zoomex_realtime_microstructure import (
    ALL_SYMBOLS,
    CollectorState,
    ORDERBOOK_PAYLOAD_SYMBOLS,
    SEALED_TARGET_SYMBOLS,
    SOURCE_SYMBOLS,
    SecureGzipWriter,
    _request_shutdown,
    _session_status,
    process_incoming,
    subscription_topics,
)


def _process(payload: dict[str, object], state: CollectorState | None = None):
    """固定受信時刻でpayloadを処理する。"""

    return process_incoming(
        json.dumps(payload),
        state or CollectorState(),
        received_at_utc="2026-08-18T00:00:00+00:00",
        received_monotonic_ns=123,
        connection_id="connection",
    )


def test_subscription_topics_are_public_and_fixed():
    """購読対象が14銘柄×3公開topicだけである。"""

    topics = subscription_topics()

    assert len(topics) == len(ALL_SYMBOLS) * 3
    assert len(set(topics)) == len(topics)
    assert all(any(topic.startswith(prefix) for prefix in (
        "orderbook.1.", "publicTrade.", "allLiquidation.",
    )) for topic in topics)
    assert all("private" not in topic.lower() and "order" not in topic.removeprefix("orderbook") for topic in topics)


def test_target_payload_is_routed_to_sealed_file():
    """target market payloadをsourceではなく封印先へ分離する。"""

    symbol = SEALED_TARGET_SYMBOLS[0]
    destination, envelope = _process({
        "topic": f"publicTrade.{symbol}",
        "type": "snapshot",
        "ts": 1,
        "data": [{"T": 1, "s": symbol, "S": "Buy", "v": "1", "p": "2", "i": "id"}],
    })

    assert destination == "target"
    assert envelope["payload"]["data"][0]["s"] == symbol


def test_source_orderbook_requires_snapshot_after_reset():
    """再接続後のsnapshot前deltaを完全性errorとして数える。"""

    state = CollectorState()
    symbol = SOURCE_SYMBOLS[0]
    payload_symbol = ORDERBOOK_PAYLOAD_SYMBOLS.get(symbol, symbol)
    delta = {
        "topic": f"orderbook.1.{symbol}", "type": "delta", "ts": 1,
        "data": {"s": payload_symbol, "b": [["1", "2"]], "a": [["2", "3"]], "u": 10},
    }
    snapshot = {
        "topic": f"orderbook.1.{symbol}", "type": "snapshot", "ts": 2,
        "data": {"s": payload_symbol, "b": [["1", "2"]], "a": [["2", "3"]], "u": 11},
    }

    assert _process(delta, state)[0] == "source"
    assert state.orderbook_delta_before_snapshot == 1
    assert _process(snapshot, state)[0] == "source"
    state.reset_orderbooks()
    assert symbol not in state.orderbook_ready


def test_duplicate_trade_id_is_counted_without_dropping_raw_message():
    """重複trade IDを数えつつ両messageを保存対象にする。"""

    state = CollectorState()
    symbol = SOURCE_SYMBOLS[1]
    payload = {
        "topic": f"publicTrade.{symbol}", "type": "snapshot", "ts": 1,
        "data": [{"T": 1, "s": symbol, "S": "Sell", "v": "1", "p": "2", "i": "same"}],
    }

    first = _process(payload, state)
    second = _process(payload, state)

    assert first[0] == second[0] == "source"
    assert state.duplicate_trade_ids == 1
    assert state.message_counts[symbol]["publicTrade"] == 2


def test_parse_error_records_hash_not_raw_market_text():
    """不正JSONはraw値を表示せずhashだけを保存する。"""

    state = CollectorState()
    destination, envelope = process_incoming(
        "not-json", state,
        received_at_utc="2026-08-18T00:00:00+00:00",
        received_monotonic_ns=123,
        connection_id="connection",
    )

    assert destination == "control"
    assert envelope["parse_error"] is True
    assert "raw_sha256" in envelope and "raw" not in envelope
    assert state.parse_errors == 1


def test_session_without_market_data_is_incomplete():
    """接続だけ成功してmarket eventがなければ完了扱いしない。"""

    assert _session_status(
        smoke_only=True, elapsed=True, connection_count=1,
        subscription_acknowledgements=1, market_records=0,
        parse_errors=0, schema_errors=0,
    ) == "INCOMPLETE"
    assert _session_status(
        smoke_only=True, elapsed=True, connection_count=1,
        subscription_acknowledgements=1, market_records=10,
        parse_errors=0, schema_errors=0,
    ) == "SMOKE_ONLY_COMPLETE"
    assert _session_status(
        smoke_only=True, elapsed=True, connection_count=1,
        subscription_acknowledgements=1, market_records=10,
        parse_errors=0, schema_errors=1,
    ) == "INCOMPLETE"


def test_shutdown_handler_only_sets_stop_flag():
    """終了signalを例外化せず安全な停止要求へ変換する。"""

    import scripts.collect_zoomex_realtime_microstructure as collector

    collector._SHUTDOWN_REQUESTED = False
    _request_shutdown(15, None)

    assert collector._SHUTDOWN_REQUESTED is True
    collector._SHUTDOWN_REQUESTED = False


def test_zoomex_internal_orderbook_alias_is_explicitly_accepted():
    """BTC板payloadの観測済み内部aliasだけを許可する。"""

    state = CollectorState()
    destination, envelope = _process({
        "topic": "orderbook.1.BTCUSDT", "type": "snapshot", "ts": 1,
        "data": {"s": "BTC2USDT", "b": [["1", "2"]], "a": [["2", "3"]], "u": 1},
    }, state)

    assert destination == "source"
    assert "validation_error" not in envelope
    assert state.message_counts["BTCUSDT"]["orderbook.1"] == 1


def test_secure_writer_uses_private_permissions_and_valid_gzip(tmp_path):
    """raw event fileを0600の有効なgzipとして保存する。"""

    path = tmp_path / "events.jsonl.gz"
    writer = SecureGzipWriter(path)
    writer.write({"event": 1})
    writer.close()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        assert json.loads(handle.readline()) == {"event": 1}
