"""Runtime tests for the streaming-coverage command against a CONTROLLED local WebSocket/HTTP server.

The fake backend speaks the real gateway protocol shape (`{"action":"subscribe","channel":...}` ->
`{"channel":"_meta","subscribed":...}`, then `{channel, symbol, event_type, payload, timestamp}` envelopes) and serves
GET /market/subscription-status and GET /scanner/universe on the same port. No provider, database or Finnhub key is
involved. The real command is also run end to end as a subprocess (scripts/measure_streaming_coverage.py).
This is SYNTHETIC verification only; it says nothing about a real feed.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from app.measurement import streaming_coverage as sc

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCRIPT = BACKEND_DIR / "scripts" / "measure_streaming_coverage.py"
PASSWORD = "s3cr3t-pw-do-not-print"

STATUS_A = {
    "status": "available", "reason": None, "provider": {"id": "finnhub", "class_name": "FinnhubProvider"}, "connected": True,
    "inventory": {"availability": "available", "reason": None, "basis": "locally_tracked_requests", "count": 2,
                  "symbols": ["AAPL", "MSFT"]},
    "capacity": {"status": "unknown", "limit": None}, "delivery": {"status": "unknown"}, "note": "n",
}
STATUS_B = dict(STATUS_A, provider={"id": "polygon", "class_name": "PolygonProvider"})


def env(channel, symbol, payload, event_type="X"):
    return json.dumps({"channel": channel, "symbol": symbol, "event_type": event_type, "payload": payload,
                       "timestamp": "2026-01-05T14:30:00+00:00"})


def tick(symbol, ts="2026-01-05T14:30:01+00:00"):
    return env(sc.CH_TICK, symbol, {"price": 10.0, "size": 5, "exchange_ts": ts}, "PriceUpdated")


def candle(symbol, ts="2026-01-05T14:30:00+00:00", tf="1m"):
    return env(sc.CH_CANDLE, symbol, {"timeframe": tf, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 9, "candle_ts": ts},
               "CandleClosed")


def feature(symbol, ts="2026-01-05T14:30:00+00:00", tf="1m"):
    return env(sc.CH_FEATURES, symbol, {"timeframe": tf, "candle_ts": ts, "close": 2.0, "features": {"sma_9": 1.5}},
               "FeaturesUpdated")


class FakeBackend:
    def __init__(self, *, script=None, status_bodies=None, universe=None, on_ack=None, withhold_ack=None,
                 ws_http_status=None):
        self.script = script
        self.status_bodies = list(status_bodies if status_bodies is not None else [STATUS_A, STATUS_A])
        self.universe = universe if universe is not None else {"symbols": ["AAPL", "MSFT", "NVDA"]}
        self.on_ack = on_ack or {}
        self.withhold_ack = withhold_ack
        self.ws_http_status = ws_http_status
        self.port = 0
        self.http_paths: list[str] = []
        self.http_auth: list[str | None] = []
        self.ws_auth: list[str | None] = []
        self.subscribes: list[str] = []
        self.other_client_messages: list[dict] = []
        self.closed_codes: list[int | None] = []
        self.ws_connections = 0
        self.acked = asyncio.Event()
        self._status_calls = 0
        self._universe_calls = 0
        self.script_error: BaseException | None = None

    # -- HTTP + handshake ----------------------------------------------------
    def process_request(self, connection, request):
        path = request.path
        auth = request.headers.get("Authorization")
        if path == sc.WS_PATH:
            self.ws_auth.append(auth)
            if self.ws_http_status:
                return connection.respond(HTTPStatus(self.ws_http_status), "rejected\n")
            return None
        self.http_paths.append(path)
        self.http_auth.append(auth)
        if path == sc.STATUS_PATH:
            idx = min(self._status_calls, len(self.status_bodies) - 1)
            self._status_calls += 1
            body = self.status_bodies[idx]
        elif path == sc.UNIVERSE_PATH:
            self._universe_calls += 1
            body = self.universe[self._universe_calls - 1] if isinstance(self.universe, list) else self.universe
        else:
            return connection.respond(HTTPStatus.NOT_FOUND, "nope\n")
        if isinstance(body, int):
            return connection.respond(HTTPStatus(body), "error\n")
        response = connection.respond(HTTPStatus.OK, json.dumps(body))
        response.headers["Content-Type"] = "application/json"
        return response

    # -- WebSocket -----------------------------------------------------------
    async def handler(self, ws):
        self.ws_connections += 1
        acked = 0
        script_task = None
        try:
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("action") == "subscribe":
                    channel = msg["channel"]
                    self.subscribes.append(channel)
                    if channel == self.withhold_ack:
                        continue
                    await ws.send(json.dumps({"channel": "_meta", "subscribed": channel}))
                    acked += 1
                    hook = self.on_ack.get(acked)
                    if hook:
                        await hook(ws)
                    if acked == len(sc.CHANNELS):
                        self.acked.set()
                        if self.script:
                            script_task = asyncio.create_task(self._run_script(ws))
                else:
                    self.other_client_messages.append(msg)
        except ConnectionClosed:
            pass
        finally:
            self.closed_codes.append(ws.close_code)
            if script_task:
                script_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await script_task

    async def _run_script(self, ws):
        try:
            await self.script(ws)
        except ConnectionClosed:
            pass
        except Exception as exc:  # noqa: BLE001
            self.script_error = exc


@pytest.fixture
async def start_backend():
    stack = contextlib.AsyncExitStack()

    async def start(**kwargs) -> FakeBackend:
        backend = FakeBackend(**kwargs)
        server = await stack.enter_async_context(serve(backend.handler, "127.0.0.1", 0, process_request=backend.process_request))
        backend.port = server.sockets[0].getsockname()[1]
        return backend

    yield start
    await stack.aclose()


async def until(predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached in time")


def make_measurement(backend, *, symbols="AAPL,MSFT,NVDA", universe=False, duration=0.8, ack_timeout=3.0, url=None):
    config = sc.build_config(
        url or f"http://127.0.0.1:{backend.port}", symbols=None if universe else symbols, scanner_universe=universe,
        duration_s=duration, ack_timeout_s=ack_timeout, http_timeout_s=3.0, connect_timeout_s=3.0)
    return sc.Measurement(config)


# =========================================================================== end-to-end, in-process

async def test_end_to_end_interleaved_filtered_zero_event_and_malformed(start_backend):
    async def script(ws):
        for msg in [tick("AAPL"), tick("MSFT"), tick("AAPL", "2026-01-05T14:30:02+00:00"), candle("AAPL"), candle("AAPL", tf="5m"),
                    feature("AAPL"), feature("MSFT", tf="5m"), tick("AAPL", "2026-01-05T14:30:03+00:00"), "garbage",
                    tick("ZZZ"), json.dumps({"channel": sc.CH_TICK, "symbol": "MSFT", "payload": {"price": 1}})]:
            await ws.send(msg)

    backend = await start_backend(script=script)
    m = make_measurement(backend)
    code = await m.run()
    r = m.report()

    assert code == sc.EXIT_OK and r["status"] == "completed" and r["end_reason"] == "window_elapsed"
    rows = {row["symbol"]: row for row in r["symbols"]}
    assert (rows["AAPL"]["ticks"]["count"], rows["MSFT"]["ticks"]["count"], rows["NVDA"]["ticks"]["count"]) == (3, 1, 0)
    assert (rows["AAPL"]["candles_1m"]["count"], rows["AAPL"]["features_1m"]["count"]) == (1, 1)
    assert rows["MSFT"]["features_1m"]["count"] == 0  # its only feature update was 5m -> filtered
    assert r["no_events"]["all_categories"] == ["NVDA"]
    assert r["no_events"]["ticks"] == ["NVDA"] and r["no_events"]["candles_1m"] == ["MSFT", "NVDA"]
    a = r["anomalies"]
    assert a["malformed_messages"] == 2 and a["malformed_by_kind"] == {"bad_source_ts": 1, "invalid_json": 1}
    assert a["other_timeframe_filtered"] == {"features.updated": {"5m": 1}, "market.candle": {"5m": 1}}
    assert a["unmonitored_symbol_events"]["market.tick"] == 1 and a["unmonitored_symbol_sample"] == ["ZZZ"]
    assert r["measurement"]["window_elapsed_s"] == 0.8 and r["connection"]["interrupted"] is False

    # the measurement socket subscribed ONLY to the three existing channels and sent nothing else
    assert backend.subscribes == list(sc.CHANNELS) and backend.other_client_messages == []
    assert backend.http_paths == [sc.STATUS_PATH, sc.STATUS_PATH]  # read-only GETs, begin and end
    await until(lambda: backend.closed_codes)
    assert backend.closed_codes == [1000]  # clean close on timeout
    assert backend.script_error is None


async def test_window_begins_only_after_all_acknowledgements(start_backend):
    async def early(ws):  # arrives after the FIRST ack but before the last
        await ws.send(tick("AAPL"))
        await ws.send(candle("AAPL"))

    async def script(ws):
        await ws.send(tick("AAPL"))

    backend = await start_backend(script=script, on_ack={1: early})
    m = make_measurement(backend, symbols="AAPL", duration=0.5)
    await m.run()
    r = m.report()
    assert r["anomalies"]["pre_window_events"] == {"features.updated": 0, "market.candle": 1, "market.tick": 1}
    row = r["symbols"][0]
    assert row["ticks"]["count"] == 1 and row["candles_1m"]["count"] == 0
    assert set(r["channels"]["ack_latency_s"]) == set(sc.CHANNELS)


async def test_delayed_events_are_attributed_by_monotonic_receipt_offset(start_backend):
    async def script(ws):
        await asyncio.sleep(0.5)
        await ws.send(tick("AAPL", "2026-01-05T14:00:00+00:00"))

    backend = await start_backend(script=script)
    m = make_measurement(backend, symbols="AAPL", duration=1.0)
    await m.run()
    t = m.report()["symbols"][0]["ticks"]
    assert t["count"] == 1 and 0.45 <= t["first_received_offset_s"] <= 1.0
    assert t["first_source_ts"] == "2026-01-05T14:00:00.000+00:00"  # source time untouched by receipt time
    assert t["first_received_utc"] != t["first_source_ts"]


async def test_zero_events_is_an_honest_completed_result(start_backend):
    backend = await start_backend()
    m = make_measurement(backend, duration=0.4)
    code = await m.run()
    r = m.report()
    assert code == sc.EXIT_OK and r["status"] == "completed"
    assert r["totals"]["events"] == {"ticks": 0, "candles_1m": 0, "features_1m": 0}
    assert r["no_events"]["all_categories"] == ["AAPL", "MSFT", "NVDA"]
    assert "cause is not classified" in " ".join(r["interpretation"])


async def test_scanner_universe_is_captured_once_and_reported(start_backend):
    backend = await start_backend(universe=[{"symbols": ["NVDA", "AAPL", "AAPL"]}, {"symbols": ["TSLA"]}])
    m = make_measurement(backend, universe=True, duration=0.4)
    code = await m.run()
    r = m.report()
    assert code == sc.EXIT_OK
    assert r["monitored"]["source"] == "scanner_universe" and r["monitored"]["symbols"] == ["AAPL", "NVDA"]
    assert backend.http_paths == [sc.UNIVERSE_PATH, sc.STATUS_PATH, sc.STATUS_PATH]  # one capture only


async def test_subscription_diagnostics_recorded_at_begin_and_end_without_capacity_claims(start_backend):
    backend = await start_backend(status_bodies=[STATUS_A, STATUS_B])
    m = make_measurement(backend, duration=0.3)
    await m.run()
    d = m.report()["diagnostics"]
    assert d["start"]["provider"]["id"] == "finnhub" and d["end"]["provider"]["id"] == "polygon"
    assert d["start"]["inventory"]["availability"] == "available" and d["start"]["monitored_locally_listed"] == 2
    assert d["identity"]["changed"] is True and "cannot show" in d["identity"]["note"]
    dumped = json.dumps(m.report())
    assert '"capacity"' not in dumped and '"delivery"' not in dumped


async def test_end_diagnostics_failure_is_recorded_without_failing_the_window(start_backend):
    backend = await start_backend(status_bodies=[STATUS_A, 500])
    m = make_measurement(backend, duration=0.3)
    code = await m.run()
    r = m.report()
    assert code == sc.EXIT_OK and r["status"] == "completed"
    assert r["diagnostics"]["end"] == {"read_ok": False, "error": "http_status_500"}
    assert r["diagnostics"]["identity"]["comparable"] is False


async def test_malformed_end_diagnostics_is_recorded_without_failing_the_window(start_backend):
    backend = await start_backend(status_bodies=[STATUS_A, {}])
    m = make_measurement(backend, duration=0.3)
    assert await m.run() == sc.EXIT_OK
    assert m.report()["diagnostics"]["end"] == {"read_ok": False, "error": "unexpected_response_shape"}


# =========================================================================== precondition / setup failures

async def test_empty_scanner_universe_is_a_precondition_failure_before_any_connection(start_backend):
    backend = await start_backend(universe={"symbols": []})
    m = make_measurement(backend, universe=True)
    code = await m.run()
    r = m.report()
    assert code == sc.EXIT_SETUP_FAILED and r["status"] == "failed_setup" and r["setup_failure"]["code"] == "empty_monitored_set"
    assert backend.ws_connections == 0 and backend.http_paths == [sc.UNIVERSE_PATH]
    assert "symbols" not in r


@pytest.mark.parametrize("universe", [500, {"symbols": "AAPL"}, {"symbols": ["AAPL", "12"]}, {"nope": []}, ["AAPL"]])
async def test_unusable_universe_response_fails_setup(start_backend, universe):
    backend = await start_backend(universe=universe)
    m = make_measurement(backend, universe=True)
    assert await m.run() == sc.EXIT_SETUP_FAILED
    assert m.report()["setup_failure"]["code"] == "universe_read_failed" and backend.ws_connections == 0


async def test_unreadable_start_diagnostics_fail_setup_before_connecting(start_backend):
    backend = await start_backend(status_bodies=[500])
    m = make_measurement(backend)
    assert await m.run() == sc.EXIT_SETUP_FAILED
    assert m.report()["setup_failure"] == {"code": "start_diagnostics_unavailable", "detail": "http_status_500"}
    assert backend.ws_connections == 0


async def test_malformed_start_diagnostics_fail_setup_before_connecting(start_backend):
    backend = await start_backend(status_bodies=[{}])
    m = make_measurement(backend)
    assert await m.run() == sc.EXIT_SETUP_FAILED
    assert m.report()["setup_failure"] == {
        "code": "start_diagnostics_unavailable", "detail": "unexpected_response_shape",
    }
    assert backend.ws_connections == 0


async def test_missing_acknowledgement_times_out_and_closes_cleanly(start_backend):
    backend = await start_backend(withhold_ack=sc.CH_FEATURES)
    m = make_measurement(backend, ack_timeout=0.4)
    t0 = time.monotonic()
    code = await m.run()
    r = m.report()
    assert code == sc.EXIT_SETUP_FAILED and r["setup_failure"]["code"] == "acknowledgement_timeout"
    assert "features.updated" in r["setup_failure"]["detail"] and time.monotonic() - t0 < 3
    assert "symbols" not in r  # the window never began, so no zero-event claims are made
    await until(lambda: backend.closed_codes)
    assert backend.closed_codes == [1000]


async def test_subscription_rejection_fails_setup(start_backend):
    async def reject(ws):
        await ws.send(json.dumps({"channel": "_meta", "error": "expected {action, channel}"}))

    backend = await start_backend(on_ack={1: reject})
    m = make_measurement(backend)
    assert await m.run() == sc.EXIT_SETUP_FAILED
    assert m.report()["setup_failure"]["code"] == "subscription_rejected"


async def test_websocket_handshake_failure_fails_setup(start_backend):
    backend = await start_backend(ws_http_status=403)
    m = make_measurement(backend)
    assert await m.run() == sc.EXIT_SETUP_FAILED
    assert m.report()["setup_failure"]["code"] == "websocket_connect_failed"


# =========================================================================== interruption

async def test_connection_loss_marks_measurement_interrupted_and_keeps_partial_counts(start_backend):
    async def script(ws):
        await ws.send(tick("AAPL"))
        await ws.send(tick("AAPL", "2026-01-05T14:30:02+00:00"))
        await asyncio.sleep(0.3)
        ws.transport.abort()

    backend = await start_backend(script=script)
    m = make_measurement(backend, symbols="AAPL,MSFT", duration=3.0)
    t0 = time.monotonic()
    code = await m.run()
    r = m.report()
    assert time.monotonic() - t0 < 2.5
    assert code == sc.EXIT_INTERRUPTED and r["status"] == "interrupted" and r["end_reason"] == "connection_lost"
    assert r["connection"]["interrupted"] is True and r["connection"]["close_code"] == 1006
    assert 0.2 <= r["connection"]["interrupted_at_offset_s"] < 2.0 and r["measurement"]["window_elapsed_s"] < 2.0
    assert r["symbols"][0]["ticks"]["count"] == 2  # partial evidence is retained
    assert r["diagnostics"]["end"]["read_ok"] is True  # end snapshot still attempted after the loss


async def test_server_initiated_close_code_is_reported(start_backend):
    async def script(ws):
        await ws.send(tick("AAPL"))
        await ws.close(1011, "backend restarting")

    backend = await start_backend(script=script)
    m = make_measurement(backend, symbols="AAPL", duration=3.0)
    assert await m.run() == sc.EXIT_INTERRUPTED
    c = m.report()["connection"]
    assert c["close_code"] == 1011 and c["close_reason"] == "backend restarting"


async def test_cancellation_closes_the_connection_cleanly_and_leaves_a_partial_report(start_backend):
    async def script(ws):
        await ws.send(tick("AAPL"))

    backend = await start_backend(script=script)
    m = make_measurement(backend, symbols="AAPL", duration=30.0)
    task = asyncio.create_task(m.run())
    await backend.acked.wait()
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    r = m.report()
    assert r["status"] == "interrupted" and r["end_reason"] == "cancelled" and m.exit_code == sc.EXIT_INTERRUPTED
    assert r["symbols"][0]["ticks"]["count"] == 1 and r["measurement"]["window_elapsed_s"] < 5
    await until(lambda: backend.closed_codes)
    assert backend.closed_codes == [1000]


async def test_stop_request_ends_the_window_early_and_closes_cleanly(start_backend):
    backend = await start_backend()
    m = make_measurement(backend, duration=30.0)
    stop = asyncio.Event()
    task = asyncio.create_task(m.run(stop))
    await backend.acked.wait()
    await asyncio.sleep(0.15)
    stop.set()
    assert await asyncio.wait_for(task, 5) == sc.EXIT_INTERRUPTED
    r = m.report()
    assert r["status"] == "interrupted" and r["end_reason"] == "stop_requested" and r["measurement"]["window_elapsed_s"] < 5
    await until(lambda: backend.closed_codes)
    assert backend.closed_codes == [1000] and r["diagnostics"]["end"]["read_ok"] is True


# =========================================================================== bounded memory over a real socket

async def test_flood_of_ticks_keeps_collector_state_bounded(start_backend):
    total = 20000

    async def script(ws):
        for i in range(total):
            ts = (datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc) + timedelta(seconds=i // 3)).isoformat()  # monotonic per symbol
            await ws.send(tick(("AAPL", "MSFT", "NVDA")[i % 3], ts))
            if i % 500 == 0:
                await asyncio.sleep(0)
        await ws.send("garbage")

    backend = await start_backend(script=script)
    m = make_measurement(backend, duration=2.5)
    task = asyncio.create_task(m.run())
    await until(lambda: m.collector and m.collector.window_started)
    baseline = m.collector.retained_item_count()
    await task
    r = m.report()
    assert r["totals"]["events"]["ticks"] == total and m.collector.retained_item_count() == baseline + 2  # only the one garbage sample + its kind counter
    assert r["anomalies"]["malformed_messages"] == 1 and r["anomalies"]["source_ts_regressions"]["ticks"] == 0


# =========================================================================== credentials

def basic(user, pw):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


async def test_credentials_are_sent_as_basic_auth_but_never_reported(start_backend):
    backend = await start_backend()
    m = make_measurement(backend, duration=0.3, url=f"http://op:{PASSWORD}@127.0.0.1:{backend.port}")
    assert await m.run() == sc.EXIT_OK
    assert backend.http_auth == [basic("op", PASSWORD)] * 2 and backend.ws_auth == [basic("op", PASSWORD)]
    dumped = sc.render_json(m.report()) + sc.render_console(m.report())
    assert PASSWORD not in dumped and "op:" not in dumped


async def test_failure_messages_never_contain_credentials(start_backend):
    backend = await start_backend(ws_http_status=403)
    m = make_measurement(backend, url=f"http://op:{PASSWORD}@127.0.0.1:{backend.port}")
    assert await m.run() == sc.EXIT_SETUP_FAILED
    dumped = sc.render_json(m.report()) + sc.render_console(m.report())
    assert PASSWORD not in dumped


async def test_unreachable_backend_error_is_redacted():
    config = sc.build_config(f"http://op:{PASSWORD}@127.0.0.1:1", symbols="AAPL", scanner_universe=False, duration_s=1,
                             http_timeout_s=1.0)
    m = sc.Measurement(config)
    assert await m.run() == sc.EXIT_SETUP_FAILED
    assert PASSWORD not in sc.render_json(m.report())


# =========================================================================== amain (in-process CLI logic)

async def run_amain(argv, **kw):
    import io

    out, err = io.StringIO(), io.StringIO()
    code = await sc.amain(argv, out=out, err=err, **kw)
    return code, out.getvalue(), err.getvalue()


async def test_amain_invalid_inputs_exit_2_without_touching_the_backend(start_backend, tmp_path):
    backend = await start_backend()
    base = ["--backend-url", f"http://127.0.0.1:{backend.port}"]
    cases = [
        base + ["--symbols", "AAPL", "--duration", "0"],
        base + ["--symbols", "AAPL", "--duration", "-5"],
        base + ["--duration", "5"],
        base + ["--symbols", "AAPL", "--scanner-universe", "--duration", "5"],
        base + ["--symbols", "bad!", "--duration", "5"],
        base + ["--symbols", "AAPL", "--duration", "5", "--json-report", str(tmp_path / "missing" / "r.json")],
        base + ["--symbols", "AAPL", "--duration", "5", "--json-report", str(tmp_path)],
        base + ["--symbols", "AAPL", "--duration", "5", "--table-rows", "-1"],
        ["--backend-url", "ftp://x", "--symbols", "AAPL", "--duration", "5"],
        base + ["--symbols", "AAPL"],  # missing --duration (argparse)
    ]
    for argv in cases:
        code, _, err = await run_amain(argv)
        assert code == sc.EXIT_INVALID_INPUT, argv
        assert err
    assert backend.ws_connections == 0 and backend.http_paths == []


async def test_amain_report_write_failure_exits_5(start_backend, tmp_path, monkeypatch):
    backend = await start_backend()

    def boom(path, report):
        raise OSError("disk full")

    monkeypatch.setattr(sc, "write_report", boom)
    code, out, err = await run_amain(["--backend-url", f"http://127.0.0.1:{backend.port}", "--symbols", "AAPL", "--duration", "0.3",
                                      "--json-report", str(tmp_path / "r.json")])
    assert code == sc.EXIT_REPORT_WRITE_FAILED and "could not write JSON report" in err and "COMPLETED" in out


# =========================================================================== the real command, end to end

async def run_cli(args, *, env_extra=None):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(SCRIPT), *args, cwd=str(BACKEND_DIR), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    return proc


async def finish(proc, timeout=30):
    out, err = await asyncio.wait_for(proc.communicate(), timeout)
    return proc.returncode, out.decode(), err.decode()


async def test_cli_end_to_end_console_table_and_json_report(start_backend, tmp_path):
    async def script(ws):
        await ws.send(tick("AAPL"))
        await ws.send(tick("MSFT"))
        await ws.send(candle("AAPL"))
        await ws.send(feature("AAPL"))
        await ws.send("garbage")

    backend = await start_backend(script=script)
    report_path = tmp_path / "coverage.json"
    proc = await run_cli(["--backend-url", f"http://127.0.0.1:{backend.port}", "--symbols", "AAPL,MSFT,NVDA", "--duration", "1",
                          "--json-report", str(report_path)])
    code, out, err = await finish(proc)
    assert code == 0 and err == ""
    assert "COMPLETED (window_elapsed)" in out and "SYMBOL" in out and "NVDA" in out and f"JSON report written: {report_path}" in out
    report = json.loads(report_path.read_text())
    assert report["schema"] == sc.SCHEMA and report["status"] == "completed"
    assert report["totals"]["events"] == {"ticks": 2, "candles_1m": 1, "features_1m": 1}
    assert report["no_events"]["all_categories"] == ["NVDA"] and report["anomalies"]["malformed_messages"] == 1
    assert backend.subscribes == list(sc.CHANNELS)
    await until(lambda: backend.closed_codes)
    assert backend.closed_codes == [1000]


async def test_cli_invalid_input_exits_2_without_contacting_backend(start_backend):
    backend = await start_backend()
    proc = await run_cli(["--backend-url", f"http://127.0.0.1:{backend.port}", "--symbols", "AAPL", "--duration", "0"])
    code, out, err = await finish(proc)
    assert code == 2 and "must be a positive, finite number" in err and out == ""
    assert backend.http_paths == [] and backend.ws_connections == 0


async def test_cli_empty_universe_exits_3_and_still_writes_a_failure_report(start_backend, tmp_path):
    backend = await start_backend(universe={"symbols": []})
    report_path = tmp_path / "r.json"
    proc = await run_cli(["--backend-url", f"http://127.0.0.1:{backend.port}", "--scanner-universe", "--duration", "5",
                          "--json-report", str(report_path)])
    code, out, _ = await finish(proc)
    assert code == 3 and "empty_monitored_set" in out and backend.ws_connections == 0
    report = json.loads(report_path.read_text())
    assert report["status"] == "failed_setup" and report["setup_failure"]["code"] == "empty_monitored_set"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
async def test_cli_signal_interrupts_cleanly_exit_4_with_partial_report(start_backend, tmp_path, sig):
    async def script(ws):
        await ws.send(tick("AAPL"))

    backend = await start_backend(script=script)
    report_path = tmp_path / "r.json"
    proc = await run_cli(["--backend-url", f"http://127.0.0.1:{backend.port}", "--symbols", "AAPL,MSFT", "--duration", "60",
                          "--json-report", str(report_path)])
    await asyncio.wait_for(backend.acked.wait(), 20)
    await asyncio.sleep(0.4)
    proc.send_signal(sig)
    code, out, err = await finish(proc, 20)
    assert code == 4 and "INTERRUPTED (stop_requested)" in out
    report = json.loads(report_path.read_text())
    assert report["status"] == "interrupted" and report["symbols"][0]["ticks"]["count"] == 1
    assert report["measurement"]["window_elapsed_s"] < 30
    await until(lambda: backend.closed_codes)
    assert backend.closed_codes == [1000]


async def test_cli_connection_loss_exits_4(start_backend, tmp_path):
    async def script(ws):
        await asyncio.sleep(0.2)
        ws.transport.abort()

    backend = await start_backend(script=script)
    proc = await run_cli(["--backend-url", f"http://127.0.0.1:{backend.port}", "--symbols", "AAPL", "--duration", "30"])
    code, out, _ = await finish(proc)
    assert code == 4 and "Connection interrupted" in out and "INTERRUPTED (connection_lost)" in out


async def test_cli_never_prints_credentials(start_backend):
    backend = await start_backend(ws_http_status=403)
    proc = await run_cli(["--backend-url", f"http://op:{PASSWORD}@127.0.0.1:{backend.port}", "--symbols", "AAPL", "--duration", "1"])
    code, out, err = await finish(proc)
    assert code == 3 and PASSWORD not in out + err


async def test_cli_help_lists_the_documented_options():
    proc = await run_cli(["--help"])
    code, out, _ = await finish(proc)
    assert code == 0
    for flag in ("--backend-url", "--symbols", "--scanner-universe", "--duration", "--json-report"):
        assert flag in out
