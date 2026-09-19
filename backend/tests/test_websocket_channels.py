"""
Tests for the WebSocket Gateway's event -> channel routing/delivery
(`app/api/websocket/channels.py`), covering decision #126's
`EventType.CONTEXT_CHANGED -> "intelligence.context"` entry and this
delivery's (decision #146) analogous
`EventType.MARKET_STATE_CHANGED -> "intelligence.market-state"` entry —
same gap shape, one engine later, added to the same file rather than a
new one since both exercise the identical unit under test
(`EVENT_TO_CHANNEL` routing + real WS delivery), per this module's own
"one file per module under test" convention below.

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

`MarketStateChanged` has no equivalent startup noise, confirmed by direct
read before writing its tests below: `MarketStateEngine.start()`
(`app/market_state_engine/engine.py`) only calls
`self._bus.subscribe(EventType.FEATURES_UPDATED, ...)` — no "fire once
immediately" bootstrap loop the way `ContextEngine.start()` has. Real
`FeaturesUpdated` traffic only exists once ticks are actually flowing
(a live Finnhub/Polygon connection, or aggregation from recorded 1m
candles), neither of which `TestClient(app)`'s own lifespan triggers on
its own — so `test_market_state_changed_*` below don't need
`_receive_until`'s noise-skipping for correctness, but reuse it anyway
for the same reason `test_unrelated_event_does_not_reach_...` already
does: a bounded read loop is strictly safer than a bare
`ws.receive_json()` assuming the very next message is always the one
just published, and costs nothing when the channel happens to be quiet.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.websocket.channels import EVENT_TO_CHANNEL
from app.event_bus.bus import get_event_bus
from app.event_bus.events import make_envelope
from app.main import app
from app.schemas.events.context import ContextChanged
from app.schemas.events.dev import DevPing
from app.schemas.events.envelope import EventType
from app.schemas.events.market_state import CrossSymbolState, MarketState

# A ticker guaranteed absent from scanner_universe_symbols's real seed
# data (AAPL/MSFT/NVDA/AMD/TSLA/SPY, migration 0004) — sidesteps any
# possible collision with a real per-symbol startup publish without
# needing a payload-content marker for the symbol-scoped test below.
_SYNTHETIC_TEST_SYMBOL = "ZZZZTEST"

# Distinctive payload marker for the global-path test, where symbol
# alone (always None, real and synthetic alike) can't disambiguate —
# no real CalendarProvider output could ever coincidentally match this.
_GLOBAL_TEST_MARKER = "__decision126_test_marker__"

# Distinctive `timeframe` marker for the MarketStateChanged tests below —
# MarketState/CrossSymbolState carry no free-text field suited to
# ContextChanged's provider-dict marker trick above (`_GLOBAL_TEST_MARKER`),
# so `timeframe` (a plain `str`, no enum constraint — schemas/events/
# market_state.py, confirmed by direct read) stands in instead. No real
# MarketStateEngine compute could ever coincidentally emit this value; real
# timeframes are 1m/5m/15m/1h only (engine.py/scoring.py).
_MARKET_STATE_TEST_TIMEFRAME = "1m-market-state-changed-websocket-channel-test-marker"


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


def test_market_state_changed_mapping_present():
    """Regression guard for this delivery's own (decision #146) one-line
    addition — if this
    entry is ever accidentally removed/renamed, this fails loudly rather
    than silently reintroducing the exact gap this delivery closed, same
    role `test_context_changed_mapping_present` plays for decision #126."""
    assert EVENT_TO_CHANNEL[EventType.MARKET_STATE_CHANGED] == "intelligence.market-state"


def test_per_symbol_market_state_changed_reaches_intelligence_market_state_with_real_symbol():
    """Mirrors `MarketStateEngine._worker_loop`'s real per-symbol publish
    shape (engine.py, confirmed by direct read before writing this test) —
    `envelope.symbol` set to the real ticker. Uses a synthetic ticker (see
    module docstring re: `_SYNTHETIC_TEST_SYMBOL`) even though no real
    startup noise exists for this event today (see module docstring) —
    matches this file's own defensive posture rather than assuming quiet."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "channel": "intelligence.market-state"})
            ack = ws.receive_json()
            assert ack == {"channel": "_meta", "subscribed": "intelligence.market-state"}

            bus = get_event_bus()
            envelope = make_envelope(
                EventType.MARKET_STATE_CHANGED,
                MarketState(
                    timeframe=_MARKET_STATE_TEST_TIMEFRAME,
                    candle_ts=datetime(2026, 1, 5, 14, 31, tzinfo=timezone.utc),
                    trend_score=61.0,
                    volatility_regime_score=40.0,
                    volume_regime_score=55.0,
                    vwap_relationship_score=70.0,
                    acceleration_score=3.5,
                ),
                symbol=_SYNTHETIC_TEST_SYMBOL,
            )
            client.portal.call(bus.publish, envelope)

            msg = _receive_until(
                ws,
                lambda m: m.get("payload", {}).get("timeframe") == _MARKET_STATE_TEST_TIMEFRAME
                and m.get("symbol") == _SYNTHETIC_TEST_SYMBOL,
            )
            assert msg["channel"] == "intelligence.market-state"
            assert msg["event_type"] == "MarketStateChanged"
            assert msg["symbol"] == _SYNTHETIC_TEST_SYMBOL
            assert msg["payload"]["trend_score"] == 61.0


def test_cross_symbol_market_state_changed_reaches_intelligence_market_state_with_sentinel_symbol():
    """Mirrors `MarketStateEngine._worker_loop`'s real cross-symbol publish
    shape (engine.py, confirmed by direct read) — `envelope.symbol` set to
    the real `_CROSS_SYMBOL_SENTINEL` value (`"__MARKET__"`), NOT null or
    absent, unlike `ContextChanged`'s own market-wide shape. This is the
    exact convention this delivery's own `EVENT_TO_CHANNEL` comment
    documents: a subscriber distinguishes the two `MarketStateChanged`
    shapes by comparing `envelope.symbol` to the literal sentinel string,
    never by checking for None/absent — asserted explicitly below, not
    merely relied upon."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "channel": "intelligence.market-state"})
            ws.receive_json()  # ack

            bus = get_event_bus()
            envelope = make_envelope(
                EventType.MARKET_STATE_CHANGED,
                CrossSymbolState(
                    timeframe=_MARKET_STATE_TEST_TIMEFRAME,
                    candle_ts=datetime(2026, 1, 5, 14, 31, tzinfo=timezone.utc),
                    spy_direction_score=58.0,
                    qqq_direction_score=62.0,
                    iwm_direction_score=49.0,
                    trend_alignment_score=71.0,
                    risk_on_score=66.0,
                    qqq_leadership_score=52.0,
                    iwm_confirmation_score=45.0,
                ),
                symbol="__MARKET__",
            )
            client.portal.call(bus.publish, envelope)

            msg = _receive_until(
                ws,
                lambda m: m.get("payload", {}).get("timeframe") == _MARKET_STATE_TEST_TIMEFRAME
                and m.get("symbol") == "__MARKET__",
            )
            assert msg["channel"] == "intelligence.market-state"
            assert msg["event_type"] == "MarketStateChanged"
            assert msg["symbol"] == "__MARKET__"
            assert msg["symbol"] is not None  # explicit: unlike ContextChanged's market-wide shape
            assert msg["payload"]["risk_on_score"] == 66.0


def test_unrelated_event_does_not_reach_intelligence_market_state_channel():
    """Channel isolation for the new channel — same structural proof
    `test_unrelated_event_does_not_reach_intelligence_context_channel`
    already gives for `intelligence.context`: a client subscribed only to
    `intelligence.market-state` is never in `dev.ping`'s subscriber set."""
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"action": "subscribe", "channel": "intelligence.market-state"})
            ws.receive_json()  # ack

            bus = get_event_bus()
            client.portal.call(bus.publish, make_envelope(EventType.DEV_PING, DevPing(message="unrelated", lane="normal")))
            client.portal.call(
                bus.publish,
                make_envelope(
                    EventType.MARKET_STATE_CHANGED,
                    MarketState(
                        timeframe=_MARKET_STATE_TEST_TIMEFRAME,
                        candle_ts=datetime(2026, 1, 5, 14, 31, tzinfo=timezone.utc),
                        trend_score=61.0,
                        volatility_regime_score=40.0,
                        volume_regime_score=55.0,
                        vwap_relationship_score=70.0,
                    ),
                    symbol=_SYNTHETIC_TEST_SYMBOL,
                ),
            )

            seen_event_types: set[str] = set()
            for _ in range(25):
                msg = ws.receive_json()
                assert msg["channel"] == "intelligence.market-state"  # this socket only ever subscribed to this one
                seen_event_types.add(msg["event_type"])
                if msg.get("payload", {}).get("timeframe") == _MARKET_STATE_TEST_TIMEFRAME:
                    break
            else:
                raise AssertionError("marked MarketStateChanged never arrived within 25 messages")

            assert "DevPing" not in seen_event_types
