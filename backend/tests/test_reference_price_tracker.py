"""P2 reference provenance: delivery order is distinct from source age."""
from datetime import datetime, timedelta, timezone

import pytest

from app.event_bus.bus import EventBus
from app.governor.reference_price import ReferencePriceTracker
from app.schemas.events.envelope import EventEnvelope, EventType
from app.trade_planning.planner import plan_entry
from app.trade_planning.proposal import serialize_proposal
from tests.test_trade_planning import opportunity

NOW = datetime(2026, 9, 22, 15, tzinfo=timezone.utc)


def tick(price=100.0, source=None, local=NOW):
    return EventEnvelope(event_type=EventType.PRICE_UPDATED, symbol="AAPL", timestamp=local,
                         payload={"price": price, **({"exchange_ts": source} if source is not None else {})})


def test_last_delivered_price_retains_old_source_clock_with_fresh_envelope():
    tracker = ReferencePriceTracker()
    tracker.start(EventBus())
    tracker._on_price_updated(tick(source=NOW.isoformat()))
    old = NOW - timedelta(hours=2)
    tracker._on_price_updated(tick(99.5, old.isoformat()))
    reference = tracker.get_observation("AAPL")
    assert tracker.get("AAPL") == 99.5
    assert reference.observed_at == NOW
    assert reference.exchange_ts == old
    plan = plan_entry("AAPL", opportunity("BUY", 99.0, 110.0), reference, 1000.0, NOW + timedelta(seconds=1))
    proposal = serialize_proposal(plan)
    assert proposal["reference_age_seconds"] == "1.0"
    assert proposal["reference_source_age_seconds"] == "7201.0"
    tracker.stop()
    assert tracker.get("AAPL") is None
    assert tracker.get_observation("AAPL") is None


@pytest.mark.parametrize("source", [None, "invalid", "2026-09-22T15:00:00", 123])
def test_missing_or_invalid_source_time_never_uses_receive_time(source):
    tracker = ReferencePriceTracker()
    tracker.start(EventBus())
    tracker._on_price_updated(tick(source=source))
    reference = tracker.get_observation("AAPL")
    assert reference.observed_at == NOW
    assert reference.exchange_ts is None
    proposal = serialize_proposal(plan_entry("AAPL", opportunity(), reference, 1000.0, NOW))
    assert proposal["reference_exchange_ts"] is None
    assert proposal["reference_source_age_seconds"] is None


def test_tracker_ignores_unobserved_missing_symbol_missing_price_and_stopped_events():
    tracker = ReferencePriceTracker()
    assert tracker.get_observation("AAPL") is None
    tracker._on_price_updated(tick())
    assert tracker.get("AAPL") is None
    tracker.start(EventBus())
    tracker._on_price_updated(EventEnvelope(event_type=EventType.PRICE_UPDATED, payload={"price": 100.0}))
    tracker._on_price_updated(EventEnvelope(event_type=EventType.PRICE_UPDATED, symbol="AAPL", payload={}))
    assert tracker.get("AAPL") is None
