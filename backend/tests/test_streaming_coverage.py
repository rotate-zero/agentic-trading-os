"""Unit tests for the streaming-coverage measurement logic (app/measurement/streaming_coverage.py).

Pure, I/O-free checks: input validation, credential redaction, the bounded collector's classification and
deterministic aggregates (explicit monotonic/UTC values are passed in), report/console rendering and the report
writer. Network behaviour (acks, loss, cancellation, the real command) is in test_streaming_coverage_runtime.py.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

from app.measurement import streaming_coverage as sc

T0 = datetime(2026, 1, 5, 14, 30, 0, tzinfo=timezone.utc)


def utc(seconds: float) -> datetime:
    from datetime import timedelta

    return T0 + timedelta(seconds=seconds)


def event(channel: str, symbol: str | None, payload: object, **extra: object) -> str:
    body: dict = {"channel": channel, "symbol": symbol, "event_type": "X", "payload": payload,
                  "timestamp": "2026-01-05T14:30:00+00:00"}
    body.update(extra)
    return json.dumps(body)


def tick(symbol: str, ts: str = "2026-01-05T14:30:01+00:00") -> str:
    return event(sc.CH_TICK, symbol, {"price": 1.0, "size": 1, "exchange_ts": ts})


def candle(symbol: str, ts: str, tf: str = "1m") -> str:
    return event(sc.CH_CANDLE, symbol, {"timeframe": tf, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1,
                                        "candle_ts": ts})


def feature(symbol: str, ts: str, tf: str = "1m") -> str:
    return event(sc.CH_FEATURES, symbol, {"timeframe": tf, "candle_ts": ts, "close": 1.0, "features": {"sma_9": 1.0}})


def ack(channel: str) -> str:
    return json.dumps({"channel": "_meta", "subscribed": channel})


def started_collector(monitored=("AAPL", "MSFT", "NVDA"), duration=60.0) -> sc.CoverageCollector:
    c = sc.CoverageCollector(tuple(monitored), duration)
    c.begin_setup(100.0)
    for i, ch in enumerate(sc.CHANNELS):
        assert c.process(ack(ch), 100.0 + 0.1 * (i + 1), utc(0.1 * (i + 1))) == "ack"
    assert c.window_started and c.window_start_mono == pytest.approx(100.3)
    return c


# --------------------------------------------------------------------------- redaction

def test_redact_url_removes_userinfo_query_values_and_fragment():
    out = sc.redact_url("https://user:s3cr3t@host.example:8443/base?token=abc&k=1#frag")
    assert "s3cr3t" not in out and "user" not in out and "abc" not in out and "frag" not in out
    assert out == "https://***@host.example:8443/base?token=REDACTED&k=REDACTED"


def test_redactor_scrubs_credentials_from_free_text():
    raw = "http://op:hunter2@127.0.0.1:8000"
    redact = sc.Redactor((raw,))
    text = f"failed to open {raw}/ws: bad auth op:hunter2 api_key=ZZZ999 https://x:y@h/p"
    out = redact(text)
    for secret in ("hunter2", "ZZZ999", "x:y@"):
        assert secret not in out


def test_redactor_keeps_plain_urls_readable():
    assert sc.Redactor(("http://127.0.0.1:8000",))("cannot reach http://127.0.0.1:8000") == "cannot reach http://127.0.0.1:8000"


# --------------------------------------------------------------------------- input validation

def cfg(**kw):
    base = dict(symbols="AAPL,MSFT", scanner_universe=False, duration_s=5)
    base.update(kw)
    return sc.build_config("http://127.0.0.1:8000", **base)


def test_build_config_explicit_symbols_normalised_and_deduplicated():
    c = cfg(symbols=" aapl, MSFT aapl brk.b ")
    assert c.symbols == ("AAPL", "MSFT", "BRK.B") and c.duplicates_dropped == 1
    assert c.symbol_source == "explicit"
    assert c.http_base == "http://127.0.0.1:8000" and c.ws_url == "ws://127.0.0.1:8000/ws"


def test_build_config_scanner_universe_mode():
    c = cfg(symbols=None, scanner_universe=True)
    assert c.symbols is None and c.symbol_source == "scanner_universe"


@pytest.mark.parametrize("kwargs", [
    dict(symbols=None, scanner_universe=False),  # neither
    dict(symbols="AAPL", scanner_universe=True),  # both
    dict(symbols="AAPL,12X"), dict(symbols="TOOLONGX"), dict(symbols=" , "),
    dict(duration_s=0), dict(duration_s=-1), dict(duration_s=float("nan")), dict(duration_s=float("inf")),
    dict(duration_s=True),
    dict(connect_timeout_s=0), dict(http_timeout_s=-3), dict(ack_timeout_s=float("nan")),
])
def test_build_config_rejects_invalid_input(kwargs):
    with pytest.raises(sc.InputError):
        cfg(**kwargs)


@pytest.mark.parametrize("url", ["", "ftp://h", "http://", "127.0.0.1:8000", "http://h:99999", "http://h?token=x", "http://h#f"])
def test_build_config_rejects_bad_urls(url):
    with pytest.raises(sc.InputError):
        sc.build_config(url, symbols="AAPL", scanner_universe=False, duration_s=1)


def test_url_scheme_mapping_path_prefix_and_credentials():
    c = sc.build_config("wss://op:pw@example.com:9000/api/", symbols="AAPL", scanner_universe=False, duration_s=1)
    assert c.http_base == "https://example.com:9000/api" and c.ws_url == "wss://example.com:9000/api/ws"
    assert c.auth_header and c.auth_header.startswith("Basic ")
    assert "pw" not in c.redacted_backend and "op" not in c.redacted_backend.replace("example", "")
    assert "@" not in c.ws_url and "@" not in c.http_base


# --------------------------------------------------------------------------- collector: deterministic values

def test_acks_gate_the_window_and_record_monotonic_latency():
    c = sc.CoverageCollector(("AAPL",), 30.0)
    c.begin_setup(10.0)
    assert c.process(tick("AAPL"), 10.05, utc(0)) == "pre_window"
    assert c.process(ack(sc.CH_TICK), 10.1, utc(0.1)) == "ack"
    assert not c.window_started
    assert c.process(candle("AAPL", "2026-01-05T14:30:00+00:00"), 10.15, utc(0.15)) == "pre_window"
    assert c.process(ack(sc.CH_CANDLE), 10.2, utc(0.2)) == "ack"
    assert c.process(ack(sc.CH_FEATURES), 10.4, utc(0.4)) == "ack"
    assert c.window_started and c.deadline_mono == pytest.approx(40.4)
    assert c.ack_latency_s == pytest.approx({sc.CH_TICK: 0.1, sc.CH_CANDLE: 0.2, sc.CH_FEATURES: 0.4})
    assert c.pre_window_events == {sc.CH_TICK: 1, sc.CH_CANDLE: 1, sc.CH_FEATURES: 0}
    assert c.stats["AAPL"]["ticks"].count == 0 and c.stats["AAPL"]["candles_1m"].count == 0


def test_deterministic_per_symbol_aggregates_interleaved_symbols():
    c = started_collector()
    base = c.window_start_mono
    script = [
        (1.0, tick("AAPL", "2026-01-05T14:30:01+00:00")),
        (1.5, tick("MSFT", "2026-01-05T14:30:01+00:00")),
        (2.0, tick("AAPL", "2026-01-05T14:30:02+00:00")),
        (4.5, tick("AAPL", "2026-01-05T14:30:04+00:00")),
        (5.0, candle("AAPL", "2026-01-05T14:30:00+00:00")),
        (5.1, feature("AAPL", "2026-01-05T14:30:00+00:00")),
        (6.0, candle("MSFT", "2026-01-05T14:30:00+00:00")),
    ]
    for off, raw in script:
        assert c.process(raw, base + off, utc(10 + off)) == "event"

    a = c.stats["AAPL"]["ticks"].as_dict()
    assert a == {
        "count": 3, "first_source_ts": "2026-01-05T14:30:01.000+00:00", "last_source_ts": "2026-01-05T14:30:04.000+00:00",
        "first_received_offset_s": 1.0, "last_received_offset_s": 4.5,
        "first_received_utc": "2026-01-05T14:30:11.000+00:00", "last_received_utc": "2026-01-05T14:30:14.500+00:00",
        "max_gap_s": 2.5,
    }
    assert c.stats["MSFT"]["ticks"].count == 1 and c.stats["MSFT"]["candles_1m"].count == 1
    assert c.stats["AAPL"]["candles_1m"].count == 1 and c.stats["AAPL"]["features_1m"].count == 1
    assert c.stats["NVDA"]["ticks"].count == 0  # zero-event symbol stays present with zero counts


def test_source_time_and_receipt_time_stay_distinct():
    c = started_collector()
    # a 2020 source timestamp received "now": neither value is derived from the other
    c.process(tick("AAPL", "2020-06-01T10:00:00+00:00"), c.window_start_mono + 3.0, utc(99))
    s = c.stats["AAPL"]["ticks"].as_dict()
    assert s["first_source_ts"] == "2020-06-01T10:00:00.000+00:00"
    assert s["first_received_utc"] == "2026-01-05T14:31:39.000+00:00" and s["first_received_offset_s"] == 3.0


def test_timeframe_filtering_counts_only_1m_and_counts_others_separately():
    c = started_collector()
    m = c.window_start_mono
    assert c.process(candle("AAPL", "2026-01-05T14:30:00+00:00", "5m"), m + 1, utc(1)) == "other_timeframe"
    assert c.process(feature("AAPL", "2026-01-05T14:30:00+00:00", "15m"), m + 1, utc(1)) == "other_timeframe"
    assert c.process(candle("AAPL", "2026-01-05T14:30:00+00:00", "1m"), m + 2, utc(2)) == "event"
    assert c.stats["AAPL"]["candles_1m"].count == 1 and c.stats["AAPL"]["features_1m"].count == 0
    assert c.other_timeframe[sc.CH_CANDLE].as_dict() == {"5m": 1}
    assert c.other_timeframe[sc.CH_FEATURES].as_dict() == {"15m": 1}


def test_unmonitored_symbols_are_counted_not_attributed_and_sample_is_bounded():
    c = started_collector()
    m = c.window_start_mono
    for i in range(sc.MAX_UNMONITORED_SAMPLE_SYMBOLS + 15):
        sym = "".join(chr(65 + (i // 26 ** k) % 26) for k in range(3))
        assert c.process(tick(sym), m + 1, utc(1)) == "unmonitored"
    assert c.unmonitored_events[sc.CH_TICK] == sc.MAX_UNMONITORED_SAMPLE_SYMBOLS + 15
    assert len(c.unmonitored_sample) == sc.MAX_UNMONITORED_SAMPLE_SYMBOLS and c.unmonitored_sample_capped
    assert all(c.stats[s]["ticks"].count == 0 for s in c.monitored)


def test_symbol_match_is_exact_case_sensitive():
    c = started_collector()
    assert c.process(tick("aapl"), c.window_start_mono + 1, utc(1)) == "unmonitored"


@pytest.mark.parametrize("raw,kind", [
    ("not json", "invalid_json"),
    ("[1,2]", "not_object"),
    ('"str"', "not_object"),
    ('{"symbol":"AAPL"}', "missing_channel"),
    (json.dumps({"channel": sc.CH_TICK, "payload": {}}), "missing_symbol"),
    (json.dumps({"channel": sc.CH_TICK, "symbol": "AAPL", "payload": [1]}), "missing_payload"),
    (json.dumps({"channel": sc.CH_TICK, "symbol": "AAPL"}), "missing_payload"),
    (json.dumps({"channel": sc.CH_TICK, "symbol": "AAPL", "payload": {"price": 1}}), "bad_source_ts"),
    (json.dumps({"channel": sc.CH_TICK, "symbol": "AAPL", "payload": {"exchange_ts": "yesterday"}}), "bad_source_ts"),
    (json.dumps({"channel": sc.CH_TICK, "symbol": "AAPL", "payload": {"exchange_ts": 1767623400}}), "bad_source_ts"),
    (json.dumps({"channel": sc.CH_CANDLE, "symbol": "AAPL", "payload": {"candle_ts": "2026-01-05T14:30:00+00:00"}}), "bad_timeframe"),
    (json.dumps({"channel": sc.CH_CANDLE, "symbol": "AAPL", "payload": {"timeframe": "1m"}}), "bad_source_ts"),
    (b"\x00\x01", "binary_frame"),
])
def test_malformed_messages_are_counted_by_kind_and_never_attributed(raw, kind):
    c = started_collector()
    assert c.process(raw, c.window_start_mono + 1, utc(1)) == "malformed"
    assert c.malformed == 1 and c.malformed_by_kind.as_dict() == {kind: 1}
    assert all(c.stats["AAPL"][cat].count == 0 for cat in sc.CATEGORIES)
    assert c.samples and c.samples[0]["kind"] == kind


def test_deeply_nested_json_is_malformed_not_a_crash():
    c = started_collector()
    assert c.process("[" * 100000, c.window_start_mono + 1, utc(1)) == "malformed"


def test_unexpected_channel_and_meta_error_are_recorded():
    c = started_collector()
    m = c.window_start_mono
    assert c.process(json.dumps({"channel": "market.tick.snapshot", "symbol": "AAPL", "payload": {}}), m + 1, utc(1)) == "unexpected_channel"
    assert c.process(json.dumps({"channel": "_meta", "error": "expected {action, channel}"}), m + 2, utc(2)) == "meta_error"
    assert c.unexpected_channels.as_dict() == {"market.tick.snapshot": 1} and c.meta_errors == 1
    assert c.setup_error is None  # only a setup-phase rejection is a setup error


def test_setup_phase_rejection_sets_setup_error():
    c = sc.CoverageCollector(("AAPL",), 5.0)
    c.begin_setup(1.0)
    c.process(json.dumps({"channel": "_meta", "error": "nope"}), 1.1, utc(0))
    assert c.setup_error == "subscription_rejected" and not c.window_started


def test_events_after_the_deadline_are_ignored():
    c = started_collector(duration=10.0)
    assert c.process(tick("AAPL"), c.window_start_mono + 10.0, utc(1)) == "event"  # exactly at deadline is inside
    assert c.process(tick("AAPL"), c.window_start_mono + 10.001, utc(1)) == "post_window"
    assert c.stats["AAPL"]["ticks"].count == 1 and c.post_window_ignored == 1


def test_source_timestamp_regressions_duplicates_and_naive_timestamps():
    c = started_collector()
    m = c.window_start_mono
    c.process(candle("AAPL", "2026-01-05T14:31:00+00:00"), m + 1, utc(1))
    c.process(candle("AAPL", "2026-01-05T14:31:00+00:00"), m + 2, utc(2))  # duplicate bar
    c.process(candle("AAPL", "2026-01-05T14:30:00+00:00"), m + 3, utc(3))  # regression
    c.process(tick("MSFT", "2026-01-05T14:30:05"), m + 4, utc(4))  # naive
    c.process(tick("MSFT", "2026-01-05T14:30:05+00:00"), m + 5, utc(5))  # equal source ts on ticks is not a duplicate
    assert c.duplicate_source_ts == {"candles_1m": 1, "features_1m": 0}
    assert c.source_ts_regressions["candles_1m"] == 1 and c.source_ts_regressions["ticks"] == 0
    assert c.naive_source_timestamps == 1
    assert c.stats["AAPL"]["candles_1m"].count == 3
    assert c.stats["AAPL"]["candles_1m"].last_source == datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    assert c.stats["AAPL"]["candles_1m"].as_dict()["last_received_offset_s"] == 3.0


def test_zulu_suffix_source_timestamps_parse():
    c = started_collector()
    assert c.process(tick("AAPL", "2026-01-05T14:30:01Z"), c.window_start_mono + 1, utc(1)) == "event"


# --------------------------------------------------------------------------- bounded memory

def test_retained_state_is_bounded_regardless_of_event_volume():
    c = started_collector(duration=1e9)
    m = c.window_start_mono
    flood = [tick("AAPL"), tick("MSFT"), tick("ZZZZ"), "garbage", candle("NVDA", "2026-01-05T14:30:00+00:00", "5m"),
             json.dumps({"channel": "weird.%d", "symbol": "A", "payload": {}})]
    for i in range(300):
        c.process(flood[i % len(flood)].replace("weird.%d", f"weird.{i}"), m + 1 + i * 0.01, utc(i))
    baseline = c.retained_item_count()
    for i in range(60000):
        raw = flood[i % len(flood)].replace("weird.%d", f"weird.{i}")
        c.process(raw, m + 10 + i * 0.001, utc(i))
    assert len(c.samples) == sc.MAX_ANOMALY_SAMPLES and c.samples_dropped > 0
    assert len(c.unexpected_channels.counts) <= sc.MAX_DISTINCT_LABELS + 1
    # no per-event growth: the only change allowed is the label-capped counters reaching their caps
    assert c.retained_item_count() - baseline <= sc.MAX_DISTINCT_LABELS + 1
    assert c.stats["AAPL"]["ticks"].count == 50 + 10000  # every in-window AAPL tick counted exactly; only aggregates retained


def test_anomaly_samples_truncate_long_excerpts():
    c = started_collector()
    c.process("x" * 5000, c.window_start_mono + 1, utc(1))
    assert len(c.samples[0]["excerpt"]) == sc.MAX_EXCERPT_CHARS


# --------------------------------------------------------------------------- diagnostics reduction

STATUS_BODY = {
    "status": "available", "reason": None, "provider": {"id": "finnhub", "class_name": "FinnhubProvider"}, "connected": True,
    "inventory": {"availability": "available", "reason": None, "basis": "locally_tracked_requests", "count": 2,
                  "symbols": ["AAPL", "TSLA"]},
    "capacity": {"status": "unknown", "limit": None}, "delivery": {"status": "unknown"}, "note": "n",
}


def test_reduce_subscription_status_records_identity_and_inventory_without_capacity_claims():
    r = sc.reduce_subscription_status(STATUS_BODY, ("AAPL", "MSFT"))
    assert r["provider"] == {"id": "finnhub", "class_name": "FinnhubProvider"} and r["connected"] is True
    assert r["inventory"] == {"availability": "available", "reason": None, "basis": "locally_tracked_requests", "count": 2}
    assert r["monitored_locally_listed"] == 1
    assert "capacity" not in r and "delivery" not in r and "symbols" not in (r["inventory"] or {})


def test_reduce_subscription_status_unavailable_inventory_and_bad_shapes():
    body = dict(STATUS_BODY, connected=False,
                inventory={"availability": "unavailable", "reason": "provider_not_connected", "basis": "locally_tracked_requests",
                           "count": None, "symbols": None})
    r = sc.reduce_subscription_status(body, ("AAPL",))
    assert r["inventory"]["availability"] == "unavailable" and r["monitored_locally_listed"] is None
    assert sc.reduce_subscription_status([1], ("AAPL",)) == {"read_ok": False, "error": "unexpected_response_shape"}
    junk = sc.reduce_subscription_status({"status": 5, "provider": "x", "connected": "yes", "inventory": {"count": True}}, ())
    assert junk == {"read_ok": False, "error": "unexpected_response_shape"}
    assert sc.reduce_subscription_status({}, ("AAPL",)) == junk
    assert sc.reduce_subscription_status({**STATUS_BODY, "inventory": {"availability": "available"}}, ()) == junk


def test_compare_identity_flags_changes_and_never_claims_continuity():
    a = sc.reduce_subscription_status(STATUS_BODY, ())
    b = sc.reduce_subscription_status(dict(STATUS_BODY, provider={"id": "polygon", "class_name": "PolygonProvider"}), ())
    same = sc.compare_identity(a, a)
    changed = sc.compare_identity(a, b)
    assert same["comparable"] and same["changed"] is False and "cannot show" in same["note"]
    assert changed["changed"] is True and changed["changed_fields"] == ["provider_class", "provider_id"]
    assert sc.compare_identity(a, {"read_ok": False, "error": "timeout"})["comparable"] is False
    assert sc.compare_identity(None, a)["changed"] is None


# --------------------------------------------------------------------------- report / console / writer

def make_report(*, status="completed", end_reason="window_elapsed"):
    config = sc.build_config("http://127.0.0.1:8000", symbols="AAPL,MSFT,NVDA", scanner_universe=False, duration_s=60)
    c = started_collector(duration=60.0)
    m = c.window_start_mono
    c.process(tick("AAPL", "2026-01-05T14:30:01+00:00"), m + 1.0, utc(11))
    c.process(tick("AAPL", "2026-01-05T14:30:03+00:00"), m + 3.0, utc(13))
    c.process(candle("MSFT", "2026-01-05T14:30:00+00:00"), m + 5.0, utc(15))
    c.process("garbage", m + 6.0, utc(16))
    start = sc.reduce_subscription_status(STATUS_BODY, config.symbols)
    return sc.build_report(
        config=config, collector=c, monitored=config.symbols, status=status, end_reason=end_reason, window_elapsed_s=60.0,
        started_utc=utc(0), ended_utc=utc(61), diagnostics_start=start, diagnostics_end=start,
        connection={"interrupted": False, "reason": None, "close_code": 1000, "close_reason": None, "interrupted_at_offset_s": None})


def test_report_values_are_deterministic_and_complete():
    r1, r2 = make_report(), make_report()
    assert sc.render_json(r1) == sc.render_json(r2)
    assert r1["schema"] == sc.SCHEMA and r1["status"] == "completed"
    assert r1["monitored"] == {"source": "explicit", "duplicates_dropped": 0, "count": 3, "symbols": ["AAPL", "MSFT", "NVDA"]}
    assert r1["totals"]["events"] == {"ticks": 2, "candles_1m": 1, "features_1m": 0}
    assert r1["totals"]["symbols_with_events"] == {"ticks": 1, "candles_1m": 1, "features_1m": 0}
    assert r1["no_events"] == {"ticks": ["MSFT", "NVDA"], "candles_1m": ["AAPL", "NVDA"],
                               "features_1m": ["AAPL", "MSFT", "NVDA"], "all_categories": ["NVDA"]}
    assert [row["symbol"] for row in r1["symbols"]] == ["AAPL", "MSFT", "NVDA"]
    assert r1["anomalies"]["malformed_messages"] == 1 and r1["anomalies"]["malformed_by_kind"] == {"invalid_json": 1}
    assert r1["measurement"]["window_elapsed_s"] == 60.0 and r1["measurement"]["backend"] == "http://127.0.0.1:8000"
    assert r1["channels"]["acknowledged"] == sorted(sc.CHANNELS)
    assert r1["diagnostics"]["identity"]["changed"] is False
    assert r1["interpretation"] == list(sc.INTERPRETATION)
    json.loads(sc.render_json(r1))  # strict JSON (allow_nan=False)


def test_report_without_a_started_window_has_no_per_symbol_section():
    config = sc.build_config("http://127.0.0.1:8000", symbols="AAPL", scanner_universe=False, duration_s=5)
    c = sc.CoverageCollector(("AAPL",), 5.0)
    r = sc.build_report(config=config, collector=c, monitored=("AAPL",), status="failed_setup", end_reason="acknowledgement_timeout",
                        window_elapsed_s=0.0, started_utc=utc(0), ended_utc=utc(1), diagnostics_start=None, diagnostics_end=None,
                        connection={"interrupted": False}, setup_failure={"code": "acknowledgement_timeout", "detail": ""})
    assert "symbols" not in r and "no_events" not in r and r["setup_failure"]["code"] == "acknowledgement_timeout"
    assert "FAILED_SETUP" in sc.render_console(r)


def test_console_table_is_concise_and_lists_zero_event_symbols():
    text = sc.render_console(make_report(), max_rows=2)
    assert "COMPLETED (window_elapsed)" in text and "AAPL" in text and "MSFT" in text
    assert "1 more symbols" in text and "NVDA" in text  # row cap applied; zero-event list still names NVDA
    assert "No observed events (cause not classified)" in text
    full = sc.render_console(make_report(), max_rows=0)
    assert "more symbols" not in full


def test_console_marks_interrupted_connection():
    r = make_report(status="interrupted", end_reason="connection_lost")
    r["connection"] = {"interrupted": True, "reason": "connection_closed", "close_code": 1006, "close_reason": None,
                       "interrupted_at_offset_s": 12.5}
    assert "Connection interrupted: connection_closed (close code 1006) at +12.5s" in sc.render_console(r)


def test_write_report_is_atomic_and_check_report_path_validates(tmp_path):
    target = tmp_path / "out.json"
    sc.check_report_path(str(target))
    sc.write_report(str(target), make_report())
    assert json.loads(target.read_text())["schema"] == sc.SCHEMA
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]  # no temp file left behind
    sc.write_report(str(target), make_report(status="interrupted", end_reason="x"))  # overwrite is allowed
    assert json.loads(target.read_text())["status"] == "interrupted"
    with pytest.raises(sc.InputError):
        sc.check_report_path(str(tmp_path))
    with pytest.raises(sc.InputError):
        sc.check_report_path(str(tmp_path / "missing" / "r.json"))
    if os.name == "posix" and os.geteuid() != 0:
        ro = tmp_path / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        with pytest.raises(sc.InputError):
            sc.check_report_path(str(ro / "r.json"))
