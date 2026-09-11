"""
Tests for the WebSocket Gateway's event -> channel routing/delivery
(`app/api/websocket/channels.py`), specifically decision #126's new
`EventType.CONTEXT_CHANGED -> "intelligence.context"` entry.

Deliberately its own file, not folded into `test_event_bus.py`:
`test_event_bus.py` tests the Bus's own dispatch mechanics and has no
concept of WebSocket at all. This file tests a different unit — the
Gateway's `EVENT_TO_CHANNEL` routing table plus real client delivery —
same "one file per module under test" convention `test_opportunity_view.py`/
`test_performance_queries.py` already establish elsewhere in this suite.

Real routing/delivery, not a mocked ConnectionManager: uses `TestClient`
as a context manager (runs the app's real lifespan — same established
convention `conftest.py`'s own docstring documents) plus a real
`websocket_connect("/ws")` client session. Publishing has to happen on
the SAME event loop the app's lifespan/WebSocket session are running on,
or this hits the exact "Queue bound to a different event loop" problem
decision #38 already found and `conftest.py` already guards against —
`client.portal` (an `anyio` `BlockingPortal`, populated by `TestClient`'s
own `__enter__`) is the supported mechanism for that, so `bus.publish()`
is invoked via `client.portal.call(...)` rather than a separately-created
event loop (e.g. a bare `asyncio.run(...)` in the test body would
recreate that exact bug).

Real background noise, found empirically while writing these tests, not
assumed: `ContextEngine.start()`'s global loop AND its per-symbol
bootstrap loop both "fire once immediately" (engine.py's own comments,
`_loop()`/`_symbol_loop()`) — so entering `TestClient(app)` against a
migrated database genuinely publishes a real `ContextChanged(symbol=None)`
plus one real `ContextChanged(symbol=<ticker>)` per row in
`scanner_universe_symbols` (6 real seeded rows as of migration 0004 —
AAPL/MSFT/NVDA/AMD/TSLA/SPY, confirmed by querying the migrated DB
directly), all landing on this same new channel. A naive "the very next
message is mine" assertion is flaky against this real traffic — same
category of problem `test_intelligence_routes.py`'s own
`_wait_until_published()` helper was built to handle for a different
engine's real concurrent activity. `_receive_until()` below is the same
idea applied to a WS message stream: keep reading (bounded by count,
not a receive-level timeout — `receive_json()` has no public per-call
timeout without reaching into `WebSocketTestSession`'s own private
`_send_rx`, and a bounded count is enough headroom above the realistic
real-noise ceiling of 7 startup messages) until a message matches.

No database WRITE happens anywhere in this feature (Event Bus ->
WebSocket Gateway has zero persistence) — the real Postgres connection
below is incidental, inherited from the app's own real lifespan
(ContextEngine's bootstrap reads `scanner_universe_symbols`), not
something this feature's own code path needs.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.websocket.channels import EVENT_TO_CHANNEL
from app.event_bus.bus import get_event_bus
from app.event_bus.events import make_envelope
from app.main import app
from app.schemas.events.context import ContextChanged
from app.schemas.events.dev import DevPing
from app.schemas.events.envelope import EventType

# A ticker guaranteed absent from scanner_universe_symbols's real seed
# data (AAPL/MSFT/NVDA/AMD/TSLA/SPY, migration 0004) — sidesteps any
# possible collision with a real per-symbol startup publish without
# needing a payload-content marker for the symbol-scoped test below.
_SYNTHETIC_TEST_SYMBOL = "ZZZZTEST"

# Distinctive payload marker for the global-path test, where symbol
# alone (always None, real and synthetic alike) can't disambiguate —
# no real CalendarProvider output could ever coincidentally match this.
_GLOBAL_TEST_MARKER = "__decision126_test_marker__"


def _receive_until(ws, predicate, max_messages: int = 25) -> dict:
    """Reads WS messages until one matches `predicate`, skipping real
    startup noise from the app's own live ContextEngine (see module
    docstring)."""
    for _ in range(max_messages):
        msg = ws.receive_json()
        if predicate(msg):
            return msg
    raise AssertionError(f"no matching message received within {max_messages} messages")


def test_context_changed_mapping_present():
    """Regression guard for decision #126's own one-line addition — if
    this entry is ever accidentally removed/renamed, this fails loudly
    rather than silently reintroducing the exact gap decision #125
    documented."""
    assert EVENT_TO_CHANNEL[EventType.CONTEXT_CHANGED] == "intelligence.context"


def test_global_context_changed_reaches_intelligence_context_with_null_symbol():
    """Mirrors ContextEngine.evaluate_all()'s real publish shape —
    symbol left unset on the envelope (engine.py, confirmed by direct
    read before writing this test)."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "channel": "intelligence.context"})
            ack = ws.receive_json()
            assert ack == {"channel": "_meta", "subscribed": "intelligence.context"}

            bus = get_event_bus()
            envelope = make_envelope(
                EventType.CONTEXT_CHANGED,
                ContextChanged(providers={"calendar": {"session": _GLOBAL_TEST_MARKER}}),
            )
            client.portal.call(bus.publish, envelope)

            msg = _receive_until(
                ws,
                lambda m: m.get("payload", {}).get("providers", {}).get("calendar", {}).get("session")
                == _GLOBAL_TEST_MARKER,
            )
            assert msg["channel"] == "intelligence.context"
            assert msg["symbol"] is None
            assert msg["event_type"] == "ContextChanged"


def test_symbol_context_changed_reaches_intelligence_context_with_symbol_set():
    """Mirrors ContextEngine.evaluate_for_symbol()'s real publish shape —
    symbol set to the ticker on the envelope (engine.py, confirmed by
    direct read before writing this test). Uses a synthetic ticker
    (see module docstring) so real per-symbol startup traffic for the
    6 actually-seeded symbols can't coincidentally match."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "channel": "intelligence.context"})
            ws.receive_json()  # ack

            bus = get_event_bus()
            envelope = make_envelope(
                EventType.CONTEXT_CHANGED,
                ContextChanged(providers={"fundamentals": {"sector": None}, "news": {"present": False}}),
                symbol=_SYNTHETIC_TEST_SYMBOL,
            )
            client.portal.call(bus.publish, envelope)

            msg = _receive_until(ws, lambda m: m.get("symbol") == _SYNTHETIC_TEST_SYMBOL)
            assert msg["channel"] == "intelligence.context"
            assert msg["event_type"] == "ContextChanged"
            assert msg["payload"]["providers"]["news"]["present"] is False


def test_unrelated_event_does_not_reach_intelligence_context_channel():
    """Channel isolation, proven structurally rather than via an
    arbitrary receive-timeout: `ConnectionManager` tracks subscribers
    per-channel (manager.py), so a client subscribed only to
    "intelligence.context" is never in "dev.ping"'s subscriber set —
    publishing DevPing (routed to "dev.ping", confirmed in
    EVENT_TO_CHANNEL) has nothing to deliver to this socket, regardless
    of how many other real messages (this app's own startup noise, or
    the DevPing itself) pass through in between. Proven by publishing
    the unrelated event, then a real marked ContextChanged, and
    confirming every message actually delivered to this socket up to
    and including the marked one is a genuine ContextChanged on this
    channel — never a "dev.ping"-shaped message, which would be
    structurally impossible via this channel-scoped subscriber design
    but is asserted directly rather than merely assumed."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "channel": "intelligence.context"})
            ws.receive_json()  # ack

            bus = get_event_bus()
            client.portal.call(bus.publish, make_envelope(EventType.DEV_PING, DevPing(message="unrelated", lane="normal")))
            client.portal.call(
                bus.publish,
                make_envelope(
                    EventType.CONTEXT_CHANGED,
                    ContextChanged(providers={"calendar": {"session": _GLOBAL_TEST_MARKER}}),
                ),
            )

            seen_event_types: set[str] = set()
            for _ in range(25):
                msg = ws.receive_json()
                assert msg["channel"] == "intelligence.context"  # this socket only ever subscribed to this one
                seen_event_types.add(msg["event_type"])
                if msg.get("payload", {}).get("providers", {}).get("calendar", {}).get("session") == _GLOBAL_TEST_MARKER:
                    break
            else:
                raise AssertionError("marked ContextChanged never arrived within 25 messages")

            assert "DevPing" not in seen_event_types
