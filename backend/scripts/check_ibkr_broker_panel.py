"""
Empirical validation of the IBKR broker panel's five routes against a REAL
paper IB Gateway / TWS session -- the first time anything in this repo
exercises backend/app/api/routes/broker.py or BrokerPanel.tsx /
useBrokerStatus.ts against a real IBKR connection.

Design and runbook: docs/architecture/ibkr-broker-panel-validation.md

What it does
------------
Drives the RUNNING backend over HTTP + WebSocket -- the same path the UI
uses -- rather than importing the router:

    POST /broker/connect        POST /broker/subscribe?symbol=
    POST /broker/unsubscribe    GET  /broker/status     POST /broker/disconnect

Read-only side observation: GET /finnhub/status, GET /market-data/status
(so provider takeover side effects are visible) and the /ws `market.tick`
channel (so "subscribed" is checked against ticks actually arriving).

It records expected-vs-observed for every step and writes a Markdown report
you can paste back verbatim. It never edits anything and never fakes a
result: a blocked run says BLOCKED and stops.

Hard boundaries (enforced in code, not just documented)
-------------------------------------------------------
- Paper ports only (4002 Gateway paper, 7497 TWS paper). 4001/7496 (live)
  and any unrecognised port abort before any request is made.
- Loopback host only.
- No order routes exist in broker.py and this script calls none.
- Never reads or prints credentials. Only IBKR_HOST / IBKR_PORT /
  IBKR_CLIENT_ID / IBKR_BACKTEST_CLIENT_ID are read from settings.
- Refuses to run if an IBKR connection it did not create may already exist,
  and only disconnects a connection THIS run created (connect must return
  "connected", not "already_connected").
- You must pass --confirm-client-id <id> equal to the configured client ID:
  a statement that no other active process (another backend, notebook,
  IBKR_BACKTEST_CLIENT_ID user...) is using that ID. The script cannot
  detect that from outside.
- The backend's own connect path (ib_async default) also syncs paper
  account snapshot data into memory; this script never reads it, but you
  must acknowledge it with --ack-startup-account-sync.

Usage
-----
    # terminal 1 (paper Gateway/TWS already logged in; same env as below):
    cd backend
    uvicorn app.main:app 2>&1 | tee backend-run.log

    # terminal 2 (same venv, same backend/.env):
    cd backend
    python scripts/check_ibkr_broker_panel.py \
        --confirm-client-id 1 --ack-startup-account-sync \
        --backend-log backend-run.log

Exit codes: 0 completed, no FAIL | 1 completed with FAIL(s) | 2 BLOCKED.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import re
import socket
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# `python scripts/foo.py` does not put backend/ on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import websockets

SCRIPT_VERSION = "1"
PAPER_PORTS = {4002: "IB Gateway (paper)", 7497: "TWS (paper)"}
LIVE_PORTS = {4001: "IB Gateway (LIVE)", 7496: "TWS (LIVE)"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
BAD_SYMBOL = "ZQXWVK"  # syntactically valid (<=6 letters, like the UI allows), should not resolve
UNSUB_SETTLE_S = 2.0
UNSUB_WINDOW_S = 5.0

PASS, FAIL, INFO, INCONCLUSIVE, SKIPPED = "PASS", "FAIL", "INFO", "INCONCLUSIVE", "SKIPPED"


class Blocked(Exception):
    """A precondition failed. Stop, report, never fake a result."""


@dataclass
class Check:
    id: str
    name: str
    expected: str
    observed: str
    verdict: str
    note: str = ""


@dataclass
class Report:
    started: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    config: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    tick_stats: dict = field(default_factory=dict)
    log_lines: list[str] = field(default_factory=list)
    log_note: str = ""
    blocker: str | None = None
    cleanup: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _short(value, limit: int = 300) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "...(truncated)"


def _connected_of(body) -> bool | None:
    return body.get("connected") if isinstance(body, dict) else None


_ACCT_RE = re.compile(r"\b(?:DU|DF|DI|U|F|I)\d{6,9}\b")
_LOG_KEEP_RE = re.compile(r"IBKR|ib_async|Error \d{3,4}|Traceback|WARNING|ERROR|Exception", re.I)
_CONNECT_LINE_RE = re.compile(r"IBKRAdapter connected to (\S+?):(\d+) \(clientId=(\d+), readonly=(\w+)\)")


class LogTap:
    """Optional: reads only what the backend appended to its log DURING this run."""

    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else None
        self.offset = 0

    def mark(self) -> None:
        if self.path and self.path.exists():
            self.offset = self.path.stat().st_size

    def collect(self) -> tuple[list[str], str]:
        if not self.path:
            return [], "no --backend-log given: IB error codes (e.g. 354/10089 market-data permissions) are not captured"
        if not self.path.exists():
            return [], f"--backend-log {self.path} does not exist"
        with self.path.open("rb") as fh:
            fh.seek(self.offset)
            text = fh.read().decode("utf-8", errors="replace")
        lines = [_ACCT_RE.sub("<acct-redacted>", ln.rstrip()) for ln in text.splitlines() if _LOG_KEEP_RE.search(ln)]
        return lines[:80], f"{len(lines)} matching line(s) during run (showing up to 80; account-like IDs redacted)"


class TickObserver:
    """/ws market.tick subscriber: proves ticks reach the pipeline, not just that a route said 'subscribed'."""

    def __init__(self, ws_url: str, symbol: str) -> None:
        self.ws_url, self.symbol = ws_url, symbol
        self.ticks: list[tuple[float, object, object]] = []
        self.payload_keys: list[str] = []
        self.other_symbols: set[str] = set()
        self.error: str | None = None
        self._ws = None
        self._task: asyncio.Task | None = None
        self._acked = asyncio.Event()

    async def start(self) -> None:
        self._ws = await websockets.connect(self.ws_url, open_timeout=10)
        self._task = asyncio.create_task(self._pump())
        await self._ws.send(json.dumps({"action": "subscribe", "channel": "market.tick"}))
        await asyncio.wait_for(self._acked.wait(), timeout=5)

    async def _pump(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if msg.get("channel") == "_meta":
                    if msg.get("subscribed") == "market.tick":
                        self._acked.set()
                    continue
                if msg.get("channel") != "market.tick":
                    continue
                payload = msg.get("payload") or {}
                if msg.get("symbol") == self.symbol:
                    if not self.payload_keys:
                        self.payload_keys = sorted(payload.keys())
                    self.ticks.append((time.monotonic(), payload.get("price"), payload.get("size")))
                else:
                    self.other_symbols.add(str(msg.get("symbol")))
        except Exception as exc:  # noqa: BLE001 -- recorded, surfaced in the report
            self.error = f"{type(exc).__name__}: {exc}"

    def count_since(self, t0: float) -> int:
        return sum(1 for t, _, _ in self.ticks if t >= t0)

    async def stop(self) -> None:
        if self._ws is not None:
            await self._ws.close()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)


def _market_session() -> tuple[str, bool | None]:
    """(label, regular_session_now). Uses the repo's own MarketClock; never guesses."""
    try:
        from app.core.market_clock import get_market_clock

        clock = get_market_clock()
        return str(clock.current_session().value), bool(clock.is_regular_session())
    except Exception as exc:  # noqa: BLE001
        return f"unknown ({type(exc).__name__})", None


# --------------------------------------------------------------------------
# preflight (no request that changes anything happens before this passes)
# --------------------------------------------------------------------------

def preflight_static(args: argparse.Namespace, report: Report) -> dict:
    if not re.fullmatch(r"[A-Z]{1,6}", args.symbol):
        raise Blocked(f"--symbol {args.symbol!r} must be 1-6 uppercase letters (the UI input allows max 6).")
    if not args.ack_startup_account_sync:
        raise Blocked(
            "Missing --ack-startup-account-sync. The backend's connect path (ib_async connectAsync default "
            "fetchFields) pulls positions, open/completed orders, executions and account updates for the paper "
            "account into memory on every connect. This script never reads that data, but it cannot prevent it. "
            "Pass the flag to acknowledge."
        )
    try:
        from app.core.config import get_settings

        s = get_settings()
    except Exception as exc:  # noqa: BLE001
        raise Blocked(f"Could not load backend settings ({type(exc).__name__}). Run from backend/ in the backend venv.") from exc

    cfg = {
        "host": s.ibkr_host,
        "port": s.ibkr_port,
        "client_id": s.ibkr_client_id,
        "backtest_client_id": s.ibkr_backtest_client_id,
    }
    report.config = dict(cfg)
    if cfg["port"] in LIVE_PORTS:
        raise Blocked(f"Configured port {cfg['port']} is {LIVE_PORTS[cfg['port']]}. Paper only -- refusing. Not changing config.")
    if cfg["port"] not in PAPER_PORTS:
        raise Blocked(f"Configured port {cfg['port']} is not a recognised paper port ({sorted(PAPER_PORTS)}). Refusing.")
    if cfg["host"] not in LOOPBACK_HOSTS:
        raise Blocked(f"Configured host {cfg['host']!r} is not loopback. Refusing.")
    bt = cfg["backtest_client_id"]
    if bt not in (None, "") and str(bt).strip() == str(cfg["client_id"]):
        raise Blocked(f"IBKR_BACKTEST_CLIENT_ID ({bt}) equals IBKR_CLIENT_ID ({cfg['client_id']}): the two would collide. Not altering config.")
    if args.confirm_client_id != cfg["client_id"]:
        raise Blocked(
            f"--confirm-client-id {args.confirm_client_id} != configured IBKR_CLIENT_ID {cfg['client_id']}. "
            "Confirm the configured value is not used by any other active process."
        )
    return cfg


async def preflight_network(cfg: dict, args: argparse.Namespace, client: httpx.AsyncClient) -> None:
    # plain TCP connect, closed immediately: no API handshake, consumes no client ID
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(cfg["host"], cfg["port"]), timeout=3)
        writer.close()
        await writer.wait_closed()
    except (OSError, asyncio.TimeoutError) as exc:
        raise Blocked(
            f"Nothing accepting connections on {cfg['host']}:{cfg['port']} ({type(exc).__name__}). "
            f"Paper {PAPER_PORTS[cfg['port']]} is not running, not logged in, or the API socket is disabled. Stopping."
        ) from exc
    try:
        r = await client.get("/health")
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise Blocked(f"Backend not reachable at {args.base_url}/health ({type(exc).__name__}). Start uvicorn first.") from exc


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

class Runner:
    def __init__(self, args: argparse.Namespace, cfg: dict, client: httpx.AsyncClient, report: Report, tap: LogTap) -> None:
        self.args, self.cfg, self.client, self.report, self.tap = args, cfg, client, report, tap
        self.symbol = args.symbol
        self.state = {"created": False, "subscribed": False, "disconnected": False, "stray_subscribed": set()}
        self.snap: dict[str, dict] = {}
        self.observer: TickObserver | None = None

    async def api(self, method: str, path: str, **params):
        t0 = time.monotonic()
        try:
            r = await self.client.request(method, path, params=params or None)
            try:
                body = r.json()
            except ValueError:
                body = r.text[:300]
            return r.status_code, body, (time.monotonic() - t0) * 1000
        except httpx.HTTPError as exc:
            return None, f"{type(exc).__name__}: {exc}", (time.monotonic() - t0) * 1000

    def check(self, cid: str, name: str, expected: str, observed: str, ok: bool | None, note: str = "", *, verdict: str | None = None) -> Check:
        v = verdict or (INFO if ok is None else PASS if ok else FAIL)
        c = Check(cid, name, expected, observed, v, note)
        self.report.checks.append(c)
        return c

    @staticmethod
    def obs(st, body, ms: float | None = None) -> str:
        tail = f" ({ms:.0f} ms)" if ms is not None else ""
        return f"HTTP {st} {_short(body, 200)}{tail}"

    async def provider_snapshot(self, label: str) -> dict:
        _, fh, _ = await self.api("GET", "/finnhub/status")
        _, md, _ = await self.api("GET", "/market-data/status")
        _, br, _ = await self.api("GET", "/broker/status")
        snap = {"finnhub": fh, "market_data": md, "broker": br}
        self.snap[label] = snap
        return snap

    # ---- steps ----------------------------------------------------------

    async def execute(self) -> None:
        try:
            await self._baseline()
            if self.args.probe_preconnect:
                await self._preconnect_probe()
            await self._connect()
            await self._while_connected()
            await self._disconnect()
        finally:
            await self._cleanup()
            self._derive_findings()

    async def _baseline(self) -> None:
        snap = await self.provider_snapshot("baseline")
        fh_c, md, br_c = _connected_of(snap["finnhub"]), snap["market_data"], _connected_of(snap["broker"])
        md_c = _connected_of(md)
        md_stream = md_c is True and isinstance(md, dict) and md.get("role") == "historical+streaming"
        other_streaming = fh_c is True or md_stream
        other_any = fh_c is True or md_c is True
        self.check(
            "S1", "Baseline GET /broker/status (before any IBKR connect)",
            "connected=false (UI: 'Not connected -- normal resting state')",
            f"broker={snap['broker']} | finnhub={snap['finnhub']} | market-data={md}",
            None if br_c else (br_c is False),
            "INFO when true: see derived findings (status is provider-agnostic in broker.py).",
        )
        if br_c is True and not other_streaming:
            raise Blocked(
                "GET /broker/status is already connected=true and no Finnhub/Polygon streaming provider explains it: "
                "an IBKR session this run did not create may exist. Refusing to touch it."
            )
        if other_any and not self.args.allow_provider_takeover:
            raise Blocked(
                "Finnhub and/or Polygon are connected in this backend. POST /broker/connect makes IBKR take over "
                "streaming (Finnhub gets disconnected) and POST /broker/disconnect later clears both registry roles "
                "without restoring them, so you would need to restart the backend afterwards. Re-run with "
                "--allow-provider-takeover to accept that side effect."
            )

    async def _preconnect_probe(self) -> None:
        br_c = _connected_of(self.snap["baseline"]["broker"])
        st, body, ms = await self.api("POST", "/broker/subscribe", symbol=self.symbol)
        if st == 200:
            self.state["stray_subscribed"].add(self.symbol)
        exp = "400 'Not connected -- call POST /broker/connect first'" if br_c is False else "200 via whichever provider is streaming (route is not IBKR-specific)"
        self.check("S2a", "PRE-connect POST /broker/subscribe", exp, self.obs(st, body, ms), (st == 400) if br_c is False else None)
        st, body, ms = await self.api("POST", "/broker/unsubscribe", symbol=self.symbol)
        if st == 200:
            self.state["stray_subscribed"].discard(self.symbol)
        self.check("S2b", "PRE-connect POST /broker/unsubscribe", "400 'Not connected' when no streaming provider", self.obs(st, body, ms), (st == 400) if br_c is False else None)

    async def _connect(self) -> None:
        self.tap.mark()
        st, body, ms = await self.api("POST", "/broker/connect")
        status = body.get("status") if isinstance(body, dict) else None
        if st == 200 and status == "connected":
            self.state["created"] = True
            self.check("S3", "POST /broker/connect (first)", "200 {'status':'connected'}", self.obs(st, body, ms), True)
            return
        if st == 200 and status == "already_connected":
            self.check("S3", "POST /broker/connect (first)", "200 {'status':'connected'}", self.obs(st, body, ms), None,
                       "Not created by this run -> nothing further is touched.", verdict=INFO)
            raise Blocked("connect returned already_connected: a connection this run did not create exists. Not subscribing/disconnecting on it.")
        self.check("S3", "POST /broker/connect (first)", "200 {'status':'connected'}", self.obs(st, body, ms), False,
                   "Real backend detail preserved verbatim above.")
        raise Blocked(f"IBKR connect did not succeed: {self.obs(st, body)}. If this mentions login/2FA/timeout/client id, that is the blocker; nothing was altered.")

    async def _while_connected(self) -> None:
        st, body, ms = await self.api("GET", "/broker/status")
        self.check("S4", "GET /broker/status after connect", "{'connected': true}", self.obs(st, body, ms), _connected_of(body) is True)
        snap = await self.provider_snapshot("after_connect")
        self.check("S5", "Provider side effects after IBKR connect (read-only)", "IBKR owns streaming+historical (broker.py); Finnhub disconnected by takeover",
                   f"finnhub={snap['finnhub']} | market-data={snap['market_data']}", None)

        st, body, ms = await self.api("POST", "/broker/connect")
        status = body.get("status") if isinstance(body, dict) else None
        self.check("S6", "POST /broker/connect again (idempotency)", "200 {'status':'already_connected'}", self.obs(st, body, ms), st == 200 and status == "already_connected")

        try:
            ws_url = self.args.base_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/") + "/ws"
            self.observer = TickObserver(ws_url, self.symbol)
            await self.observer.start()
            self.check("S7", "WS /ws subscribe market.tick", "ack {'channel':'_meta','subscribed':'market.tick'}", "ack received", True)
        except Exception as exc:  # noqa: BLE001
            self.observer = None
            self.check("S7", "WS /ws subscribe market.tick", "ack received", f"{type(exc).__name__}: {exc}", False, "Tick flow cannot be observed; S10/S11 will be SKIPPED.")

        st, body, ms = await self.api("POST", "/broker/subscribe", symbol=BAD_SYMBOL)
        if st == 200:
            self.state["stray_subscribed"].add(BAD_SYMBOL)
        detail = body.get("detail") if isinstance(body, dict) else None
        self.check("S8", f"POST /broker/subscribe unresolvable symbol {BAD_SYMBOL}", "400 with SymbolNotFoundError detail (not a 200)", self.obs(st, body, ms),
                   st == 400 and isinstance(detail, str) and BAD_SYMBOL in detail)

        t_sub = time.monotonic()
        st, body, ms = await self.api("POST", "/broker/subscribe", symbol=self.symbol)
        ok = st == 200 and isinstance(body, dict) and body.get("status") == "subscribed" and body.get("symbol") == self.symbol
        if ok:
            self.state["subscribed"] = True
        self.check("S9", f"POST /broker/subscribe {self.symbol}", f"200 {{'status':'subscribed','symbol':'{self.symbol}'}}", self.obs(st, body, ms), ok)
        if not ok:
            return

        st, body, ms = await self.api("POST", "/broker/subscribe", symbol=self.symbol)
        self.check("S9b", "POST /broker/subscribe same symbol again", "200 (adapter skips already-subscribed)", self.obs(st, body, ms), None)

        await self._observe_ticks(t_sub)
        await self._unsubscribe()

        st, body, ms = await self.api("GET", "/broker/status")
        self.check("S13", "GET /broker/status after unsubscribe", "{'connected': true} (connection stays up)", self.obs(st, body, ms), _connected_of(body) is True)

    async def _observe_ticks(self, t_sub: float) -> None:
        session, regular = _market_session()
        self.report.context["us_session_at_run"] = session
        if self.observer is None:
            self.check("S10", "Ticks reach /ws market.tick", "ticks for symbol", "observer unavailable", None, verdict=SKIPPED)
            return
        await asyncio.sleep(self.args.observe_seconds)
        obs = self.observer
        ticks = [t for t in obs.ticks if t[0] >= t_sub]
        n = len(ticks)
        stats = {"symbol": self.symbol, "window_s": self.args.observe_seconds, "ticks": n, "us_session": session,
                 "payload_keys": obs.payload_keys, "other_symbols_seen": sorted(obs.other_symbols), "ws_error": obs.error}
        if n:
            prices = [p for _, p, _ in ticks if isinstance(p, (int, float))]
            stats.update(first_tick_latency_s=round(ticks[0][0] - t_sub, 2),
                         price_min=min(prices) if prices else None, price_max=max(prices) if prices else None,
                         all_prices_positive=all(p > 0 for p in prices) if prices else None)
        self.report.tick_stats = stats
        if n:
            self.check("S10", f"Ticks reach /ws market.tick within {self.args.observe_seconds}s", "> 0 ticks for the symbol", f"{n} ticks; first after {stats['first_tick_latency_s']}s", True)
        elif regular:
            self.check("S10", f"Ticks reach /ws market.tick within {self.args.observe_seconds}s", "> 0 ticks during the regular US session", "0 ticks",
                       False, "Regular session + 0 ticks: check backend log for IB error 354/10089/10167 (market-data permission/delayed) before blaming the adapter.")
        else:
            self.check("S10", f"Ticks reach /ws market.tick within {self.args.observe_seconds}s", "ticks not guaranteed outside the regular session",
                       f"0 ticks (US session: {session})", None, "Cannot prove or disprove tick flow now; re-run in the regular session.", verdict=INCONCLUSIVE)

    async def _unsubscribe(self) -> None:
        st, body, ms = await self.api("POST", "/broker/unsubscribe", symbol=self.symbol)
        ok = st == 200 and isinstance(body, dict) and body.get("status") == "unsubscribed"
        if ok:
            self.state["subscribed"] = False
        self.check("S11a", f"POST /broker/unsubscribe {self.symbol}", "200 {'status':'unsubscribed'}", self.obs(st, body, ms), ok)
        if self.observer is not None and ok:
            await asyncio.sleep(UNSUB_SETTLE_S)
            t0 = time.monotonic()
            await asyncio.sleep(UNSUB_WINDOW_S)
            after = self.observer.count_since(t0)
            had_ticks = bool(self.report.tick_stats.get("ticks"))
            if after == 0 and not had_ticks:
                self.check("S11b", "Ticks stop after unsubscribe", "0 ticks in the window", "0 (but 0 before too)", None, "No prior ticks -> cannot show the stop.", verdict=INCONCLUSIVE)
            else:
                self.check("S11b", f"Ticks stop after unsubscribe (after {UNSUB_SETTLE_S:.0f}s settle, {UNSUB_WINDOW_S:.0f}s window)", "0 ticks", f"{after} ticks", after == 0)
        st, body, ms = await self.api("POST", "/broker/unsubscribe", symbol=self.symbol)
        self.check("S12", "POST /broker/unsubscribe again (already unsubscribed)", "200 (adapter no-op) -- recorded, not judged", self.obs(st, body, ms), None)

    async def _disconnect(self) -> None:
        if not self.state["created"]:
            return
        st, body, ms = await self.api("POST", "/broker/disconnect")
        ok = st == 200 and isinstance(body, dict) and body.get("status") == "disconnected"
        if ok:
            self.state["disconnected"] = True
        self.check("S14a", "POST /broker/disconnect", "200 {'status':'disconnected'}", self.obs(st, body, ms), ok)
        await asyncio.sleep(1)
        st, body, ms = await self.api("GET", "/broker/status")
        self.check("S14b", "GET /broker/status after disconnect", "{'connected': false}", self.obs(st, body, ms), _connected_of(body) is False)
        snap = await self.provider_snapshot("after_disconnect")
        self.check("S15", "Provider state after IBKR disconnect (read-only)", "recorded: is any streaming provider restored?",
                   f"finnhub={snap['finnhub']} | market-data={snap['market_data']}", None)
        st, body, ms = await self.api("POST", "/broker/subscribe", symbol=self.symbol)
        if st == 200:
            self.state["stray_subscribed"].add(self.symbol)
        self.check("S16", "POST /broker/subscribe after disconnect", "400 'Not connected -- call POST /broker/connect first'", self.obs(st, body, ms), st == 400)
        st, body, ms = await self.api("POST", "/broker/disconnect")
        self.check("S17", "POST /broker/disconnect again (nothing connected)", "200 disconnected (route tolerates None) -- recorded", self.obs(st, body, ms), None)

    async def _cleanup(self) -> None:
        """Only undoes what THIS run created."""
        if self.observer is not None:
            await self.observer.stop()
        for sym in sorted(self.state["stray_subscribed"]):
            st, body, _ = await self.api("POST", "/broker/unsubscribe", symbol=sym)
            self.report.cleanup.append(f"unsubscribed stray {sym}: HTTP {st}")
        if self.state["subscribed"]:
            st, body, _ = await self.api("POST", "/broker/unsubscribe", symbol=self.symbol)
            self.report.cleanup.append(f"unsubscribed {self.symbol}: HTTP {st}")
        if self.state["created"] and not self.state["disconnected"]:
            st, body, _ = await self.api("POST", "/broker/disconnect")
            self.report.cleanup.append(f"disconnected the connection this run created: HTTP {st} {_short(body, 120)}")
        if not self.report.cleanup:
            self.report.cleanup.append("nothing left to clean up")

    def _derive_findings(self) -> None:
        f = self.report.findings
        base, after_c, after_d = self.snap.get("baseline"), self.snap.get("after_connect"), self.snap.get("after_disconnect")
        if base:
            fh_c, md = _connected_of(base["finnhub"]), base["market_data"]
            other = fh_c is True or (isinstance(md, dict) and md.get("connected") is True and md.get("role") == "historical+streaming")
            if _connected_of(base["broker"]) is True and other:
                f.append("OBSERVED: /broker/status returned connected=true BEFORE any IBKR connect, while Finnhub/Polygon was the streaming provider. "
                         "The route reports whichever provider streams, not IBKR. BrokerPanel.tsx would show '● Connected' and disable its Connect button.")
        if base and after_c and _connected_of(base["finnhub"]) is True and _connected_of(after_c["finnhub"]) is False:
            f.append("OBSERVED: IBKR connect took over streaming and Finnhub reported disconnected afterwards (by design in broker_registry.take_over_streaming).")
        if base and after_c and _connected_of(base["market_data"]) is True and _connected_of(after_c["market_data"]) is True:
            f.append(f"OBSERVED: /market-data/status still reports connected after the IBKR takeover ({_short(after_c['market_data'], 120)}); "
                     "broker.py set IBKR as the historical provider, so the Polygon adapter is presumably still connected but no longer registered -- verify.")
        if after_d and base and (_connected_of(base["finnhub"]) is True or _connected_of(base["market_data"]) is True):
            if _connected_of(after_d["finnhub"]) is not True:
                f.append("OBSERVED: after /broker/disconnect no Finnhub streaming was restored (registry cleared, not reverted). A backend restart is needed to get it back.")
        for c in self.report.checks:
            if c.verdict == FAIL:
                f.append(f"FAIL {c.id}: expected [{c.expected}] observed [{c.observed}]" + (f" -- {c.note}" if c.note else ""))


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def verify_connect_line(report: Report, cfg: dict, args: argparse.Namespace) -> None:
    """The script reads settings from ITS OWN environment; the backend may have been started with another.
    The backend's own connect log line is the only after-the-fact evidence of what it really used."""
    s3 = next((i for i, c in enumerate(report.checks) if c.id == "S3" and c.verdict == PASS), None)
    if s3 is None or not args.backend_log:
        return
    for ln in report.log_lines:
        m = _CONNECT_LINE_RE.search(ln)
        if m:
            host, port, cid, ro = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
            want = (cfg["host"], cfg["port"], cfg["client_id"])
            ok = (host, port, cid) == want and ro == "True"
            report.checks.insert(s3 + 1, Check(
                "S3b", "Backend really connected with the confirmed host/port/client ID, read-only",
                f"{want[0]}:{want[1]} clientId={want[2]} readonly=True", f"{host}:{port} clientId={cid} readonly={ro}",
                PASS if ok else FAIL,
                "" if ok else "Backend env differs from this script's env: the client-ID confirmation did NOT cover what the backend used."))
            return
    report.checks.insert(s3 + 1, Check("S3b", "Backend connect log line", "line 'IBKRAdapter connected to ...' present in --backend-log",
                                       "not found", INFO, "Cannot confirm what host/port/client ID the backend actually used."))


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render(report: Report, args: argparse.Namespace) -> str:
    fails = sum(1 for c in report.checks if c.verdict == FAIL)
    status = "BLOCKED" if report.blocker else ("COMPLETED with FAIL(s)" if fails else "COMPLETED")
    try:
        import ib_async

        ibv = getattr(ib_async, "__version__", "?")
    except Exception:  # noqa: BLE001
        ibv = "?"
    out = [
        "# IBKR broker panel -- empirical validation report",
        "",
        f"- Result: **{status}**",
        f"- Started (UTC): {report.started} | script v{SCRIPT_VERSION} | python {platform.python_version()} | ib_async {ibv}",
        f"- Configured (read from settings; only these fields): host `{report.config.get('host', '(not loaded)')}` "
        f"port `{report.config.get('port', '(not loaded)')}` ({PAPER_PORTS.get(report.config.get('port'), 'unrecognised/not loaded')}) "
        f"client_id `{report.config.get('client_id', '(not loaded)')}` "
        f"backtest_client_id `{report.config.get('backtest_client_id') or '(unset)'}`",
        f"- Operator confirmed client ID: `{args.confirm_client_id}` | symbol `{args.symbol}` | observe window {args.observe_seconds}s | US session at run: `{report.context.get('us_session_at_run', 'n/a')}`",
        "",
    ]
    if report.blocker:
        out += ["## BLOCKER", "", report.blocker, ""]
    if report.checks:
        out += ["## Checks", "", "| ID | Check | Expected | Observed | Verdict |", "|---|---|---|---|---|"]
        for c in report.checks:
            note = f" _({_cell(c.note)})_" if c.note else ""
            out.append(f"| {c.id} | {_cell(c.name)} | {_cell(c.expected)} | {_cell(c.observed)}{note} | **{c.verdict}** |")
        out.append("")
    if report.tick_stats:
        out += ["## Tick observation", "", "```json", json.dumps(report.tick_stats, indent=2, default=str), "```", ""]
    if report.findings:
        out += ["## Derived findings", ""] + [f"- {x}" for x in report.findings] + [""]
    out += ["## Cleanup (only what this run created)", ""] + [f"- {x}" for x in report.cleanup] + [""]
    out += ["## Backend log excerpt", "", f"_{report.log_note}_", ""]
    if report.log_lines:
        out += ["```", *report.log_lines, "```", ""]
    out += [
        "## Not covered by this script",
        "",
        "- BrokerPanel.tsx / useBrokerStatus.ts rendering (manual checklist in docs/architecture/ibkr-broker-panel-validation.md).",
        "- Unexpected mid-session Gateway drop, reconnect after disconnect, and multi-window polling.",
        "- Any order routes (none exist in broker.py; none were called).",
        "",
    ]
    return "\n".join(out)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--symbol", default="SPY", help="harmless liquid symbol, 1-6 uppercase letters (default SPY)")
    p.add_argument("--observe-seconds", type=int, default=20)
    p.add_argument("--confirm-client-id", type=int, required=True,
                   help="must equal the configured IBKR_CLIENT_ID: you assert no other active process uses it")
    p.add_argument("--ack-startup-account-sync", action="store_true",
                   help="acknowledge the backend's connect path syncs paper account snapshot data into memory (never read here)")
    p.add_argument("--allow-provider-takeover", action="store_true",
                   help="accept that IBKR takes over from a connected Finnhub/Polygon and they are not restored afterwards")
    p.add_argument("--probe-preconnect", action="store_true",
                   help="also call subscribe/unsubscribe BEFORE connecting (shows which provider they reach)")
    p.add_argument("--backend-log", default=None, help="path to the tee'd uvicorn log to capture IB errors during the run")
    p.add_argument("--report", default=None, help="output .md path (default ./ibkr-broker-panel-validation-<UTC>.md)")
    return p.parse_args()


async def amain(args: argparse.Namespace) -> int:
    report = Report()
    tap = LogTap(args.backend_log)
    try:
        cfg = preflight_static(args, report)
        async with httpx.AsyncClient(base_url=args.base_url, timeout=45) as client:
            await preflight_network(cfg, args, client)
            await Runner(args, cfg, client, report, tap).execute()
    except Blocked as exc:
        report.blocker = str(exc)
    report.log_lines, report.log_note = tap.collect()
    if report.config:
        verify_connect_line(report, report.config, args)
        for c in report.checks:
            if c.id == "S3b" and c.verdict == FAIL:
                report.findings.append(f"FAIL S3b: expected [{c.expected}] observed [{c.observed}] -- {c.note}")
    if not report.cleanup:
        report.cleanup.append("no state-changing request was made")
    text = render(report, args)
    path = Path(args.report or f"ibkr-broker-panel-validation-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.md")
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[report written to {path.resolve()}]")
    if report.blocker:
        return 2
    return 1 if any(c.verdict == FAIL for c in report.checks) else 0


def main() -> int:
    return asyncio.run(amain(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
