"""
Route-level tests for decision #122's two new GET /intelligence routes:
`/strategy-outcomes` and `/opportunity-conflicts`. Both close the same
gap — decisions #120/#121 built real capabilities but explicitly skipped
adding a route, citing parallel-track collision risk that's now gone.

Deliberately split into two very different test styles, matching each
route's own dependency:

- `/strategy-outcomes` is a plain synchronous DB read (no background
  engine, no EventBus subscriber) — a sync `TestClient(app)` is enough,
  same posture as test_market_routes.py. Populated-case rows are
  inserted directly via the real `StrategyOutcomeRecord` ORM model
  (`test_performance_intelligence.py`'s own `_make_outcome`-style
  field set, adapted to construct the ORM row directly rather than via
  `record_strategy_outcome()` — that write path belongs to the sibling
  parallel track's own footprint for this delivery, and this route's
  job is proving "a row that's actually in the table round-trips
  through the route," not re-proving the write path). Ordering/limit
  assertions use far-future (year 2099) `exit_filled_at` timestamps for
  our own synthetic rows so they're deterministically the newest in the
  table regardless of any other content — no assumption that the table
  is otherwise perfectly empty except for the one dedicated test that
  checks that literal condition.

- `/opportunity-conflicts` is a thin wrapper over `OpportunityCache`
  (an async EventBus subscriber) — same httpx `ASGITransport` + real
  `app.router.lifespan_context(app)` coordination
  `test_intelligence_routes.py` already established for exactly this
  "publish onto the real bus, then hit the route on the same event
  loop" need, and the same `_make_opportunity()`-shaped real
  `OpportunityCreated` publishing `test_opportunity_cache.py`/
  `test_opportunity_view.py` already use. This deliberately does NOT
  re-test the conflict/agreement classification algorithm itself
  (0/1-opportunity absence, malformed-direction exclusion, BUY-before-
  SELL ordering, ...) — that's `test_opportunity_view.py`'s job as of
  decision #121, and duplicating it here would just be the same
  coverage under a slower HTTP-level harness. This file only proves the
  route itself forwards to `get_opportunity_conflicts()` correctly,
  including `symbol` filtering.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.event_bus.bus import get_event_bus
from app.event_bus.events import make_envelope
from app.main import app
from app.models.trading_intelligence import StrategyOutcomeRecord
from app.schemas.events.envelope import EventType
from app.strategy_engine.base_strategy import Opportunity

# --- GET /intelligence/strategy-outcomes ------------------------------------

_STRATEGY_NAME = "__TEST_ROUTE_STRATEGY_OUTCOMES__"


def _db_available() -> bool:
    try:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        return False


def _clean_own_rows() -> None:
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM strategy_outcomes WHERE strategy_name = :n"), {"n": _STRATEGY_NAME})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _cleanup():
    if _db_available():
        _clean_own_rows()
    yield
    if _db_available():
        _clean_own_rows()


def _sample_market_state() -> dict:
    return {
        "trend_score": 62.5,
        "volatility_regime_score": 41.0,
        "volume_regime_score": 58.0,
        "vwap_relationship_score": 70.0,
        "acceleration_score": 12.0,
    }


def _sample_context() -> dict:
    return {"gap_day": False, "session_type": "regular", "vix_regime": "normal"}


def _make_outcome_record(exit_filled_at: datetime, **overrides) -> StrategyOutcomeRecord:
    now = datetime(2026, 9, 10, 14, 35, tzinfo=timezone.utc)
    fields = dict(
        outcome_id=uuid.uuid4(),
        opportunity_id=uuid.uuid4(),
        schema_version=1,
        strategy_name=_STRATEGY_NAME,
        strategy_version="orb_v1",
        symbol="AAPL",
        origin="auto",
        is_backtest=False,
        backtest_run_id=None,
        trading_day=date(2026, 9, 10),
        setup_detected_at=now,
        signal_confirmed_at=now,
        decided_at=now,
        entry_filled_at=now,
        exit_filled_at=exit_filled_at,
        holding_seconds=2100,
        direction="BUY",
        entry_price=228.50,
        entry_qty=100,
        exit_price=230.10,
        exit_qty=100,
        commission_total=1.00,
        slippage_entry=0.02,
        realized_pnl=159.00,
        realized_r=1.6,
        exit_reason="target",
        structural_invalidation=227.80,
        structural_target=230.50,
        final_stop=227.80,
        final_target=230.50,
        confidence_at_signal=0.72,
        evidence={"conditions": {"or_break": True}, "reason": "opening range breakout", "basis": "live"},
        market_state_at_entry=_sample_market_state(),
        context_at_entry=_sample_context(),
        market_state_at_exit=_sample_market_state(),
        context_at_exit=_sample_context(),
        feature_snapshot_id=None,
    )
    fields.update(overrides)
    return StrategyOutcomeRecord(**fields)


def _insert(records: list[StrategyOutcomeRecord]) -> None:
    session = SessionLocal()
    try:
        for record in records:
            session.add(record)
        session.commit()
    finally:
        session.close()


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_strategy_outcomes_table_actually_empty_returns_honest_empty_collection():
    """The real, current production state (decision #120/#121: no
    Execution Engine/Position Monitor exists to write here yet) — a
    genuinely empty table returns `{"outcomes": []}`, not an error and
    not a fabricated placeholder. Deliberately wipes the WHOLE table
    rather than scoping to `_STRATEGY_NAME` like the other tests below:
    proving "empty" honestly requires the table to actually BE empty,
    and doing so is safe precisely because nothing else in this codebase
    writes to `strategy_outcomes` at all (confirmed by decision #120's
    own "no live caller wired" note) — there is no real data anywhere
    for this to clobber.
    """
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM strategy_outcomes"))
        session.commit()
    finally:
        session.close()

    with TestClient(app) as client:
        resp = client.get("/intelligence/strategy-outcomes")

    assert resp.status_code == 200
    assert resp.json() == {"outcomes": []}


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_strategy_outcomes_populated_case_round_trips_actual_fields():
    # UUIDs generated up front and read back from these local variables,
    # not from the ORM object after `_insert()` closes its session — the
    # row is detached once its session closes, and touching an
    # unloaded/expired attribute on a detached instance raises
    # SQLAlchemy's DetachedInstanceError (found by actually running this
    # suite, not assumed).
    outcome_id = uuid.uuid4()
    opportunity_id = uuid.uuid4()
    row = _make_outcome_record(
        exit_filled_at=datetime(2026, 9, 10, 15, 10, tzinfo=timezone.utc),
        outcome_id=outcome_id,
        opportunity_id=opportunity_id,
    )
    _insert([row])

    with TestClient(app) as client:
        resp = client.get("/intelligence/strategy-outcomes", params={"limit": 500})

    assert resp.status_code == 200
    body = resp.json()
    match = next(o for o in body["outcomes"] if o["strategy_name"] == _STRATEGY_NAME)

    # Verify actual returned fields — not just status code / list length.
    assert match["outcome_id"] == str(outcome_id)
    assert match["opportunity_id"] == str(opportunity_id)
    assert match["symbol"] == "AAPL"
    assert match["direction"] == "BUY"
    assert match["origin"] == "auto"
    assert match["is_backtest"] is False
    assert match["entry_price"] == 228.50
    assert match["entry_qty"] == 100
    assert match["exit_price"] == 230.10
    assert match["exit_qty"] == 100
    assert match["realized_pnl"] == 159.00
    assert match["realized_r"] == 1.6
    assert match["exit_reason"] == "target"
    assert match["structural_invalidation"] == 227.80
    assert match["structural_target"] == 230.50
    assert match["confidence_at_signal"] == 0.72
    assert match["evidence"] == {"conditions": {"or_break": True}, "reason": "opening range breakout", "basis": "live"}
    assert match["market_state_at_entry"] == _sample_market_state()
    assert match["context_at_entry"] == _sample_context()
    assert match["trading_day"] == "2026-09-10"
    assert match["exit_filled_at"].startswith("2026-09-10T15:10:00")
    assert match["feature_snapshot_id"] is None
    assert match["backtest_run_id"] is None


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_strategy_outcomes_orders_by_exit_filled_at_descending():
    # Year-2099 timestamps: guaranteed to be the newest rows in the table
    # regardless of anything else present, so this doesn't depend on the
    # table being otherwise empty (only the dedicated test above asserts
    # that literal condition). UUIDs captured in local variables up
    # front — see the populated-case test above for why (detached
    # instance after `_insert()` closes its session).
    older_id, newer_id = uuid.uuid4(), uuid.uuid4()
    older = _make_outcome_record(exit_filled_at=datetime(2099, 1, 1, 10, 0, tzinfo=timezone.utc), outcome_id=older_id)
    newer = _make_outcome_record(exit_filled_at=datetime(2099, 1, 1, 16, 0, tzinfo=timezone.utc), outcome_id=newer_id)
    _insert([older, newer])

    with TestClient(app) as client:
        resp = client.get("/intelligence/strategy-outcomes", params={"limit": 2})

    body = resp.json()
    assert [o["outcome_id"] for o in body["outcomes"]] == [str(newer_id), str(older_id)]


@pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")
def test_strategy_outcomes_limit_caps_returned_rows():
    # ids[0]/[1]/[2] correspond to 09:00/10:00/11:00 respectively. Local
    # variables again, for the same detached-instance reason as above.
    ids = [uuid.uuid4() for _ in range(3)]
    rows = [
        _make_outcome_record(exit_filled_at=datetime(2099, 1, 2, hour, 0, tzinfo=timezone.utc), outcome_id=ids[i])
        for i, hour in enumerate((9, 10, 11))  # 3 rows, all guaranteed newest-in-table
    ]
    _insert(rows)

    with TestClient(app) as client:
        resp = client.get("/intelligence/strategy-outcomes", params={"limit": 2})

    body = resp.json()
    assert len(body["outcomes"]) == 2
    # The two most recent of our three (11:00, then 10:00) — 09:00 is
    # correctly clamped out by `limit`.
    assert [o["outcome_id"] for o in body["outcomes"]] == [str(ids[2]), str(ids[1])]


# --- GET /intelligence/opportunity-conflicts --------------------------------


def _make_opportunity(strategy: str, direction: str, confidence: float) -> Opportunity:
    return Opportunity(
        strategy=strategy,
        version=f"{strategy.lower()}_v1",
        direction=direction,
        confidence=confidence,
        structural_invalidation=100.0,
        structural_target=105.0,
        evidence={"conditions": {}, "reason": "test fixture", "basis": "live"},
        setup_detected_at=datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_opportunity_conflicts_route_reflects_real_cache_agreement():
    ticker = "__T_INTEL_CONFLICTS_AGREE__"
    async with app.router.lifespan_context(app):
        bus = get_event_bus()
        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB", "BUY", 0.6), symbol=ticker))
        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("Momentum", "BUY", 0.55), symbol=ticker))
        await asyncio.sleep(0.05)  # let the normal-lane queue dispatch (same wait test_opportunity_cache.py uses)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/intelligence/opportunity-conflicts")

    assert resp.status_code == 200
    body = resp.json()
    assert ticker in body["agreements"]
    assert body["agreements"][ticker]["direction"] == "BUY"
    assert body["agreements"][ticker]["count"] == 2
    assert {s["strategy"] for s in body["agreements"][ticker]["strategies"]} == {"ORB", "Momentum"}
    assert ticker not in body["conflicts"]


@pytest.mark.asyncio
async def test_opportunity_conflicts_route_symbol_filter_forwards_correctly():
    ticker_conflict = "__T_INTEL_CONFLICTS_MIX__"
    ticker_other = "__T_INTEL_CONFLICTS_OTHER__"
    async with app.router.lifespan_context(app):
        bus = get_event_bus()
        # ticker_conflict: real BUY vs SELL conflict.
        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB", "BUY", 0.6), symbol=ticker_conflict))
        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("Reversal", "SELL", 0.5), symbol=ticker_conflict))
        # ticker_other: a real agreement, present in the cache but must
        # NOT leak into a ticker_conflict-filtered response.
        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB", "BUY", 0.7), symbol=ticker_other))
        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, _make_opportunity("Momentum", "BUY", 0.65), symbol=ticker_other))
        await asyncio.sleep(0.05)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/intelligence/opportunity-conflicts", params={"symbol": ticker_conflict})

    assert resp.status_code == 200
    body = resp.json()
    assert ticker_conflict in body["conflicts"]
    assert set(body["conflicts"][ticker_conflict]["by_direction"].keys()) == {"BUY", "SELL"}
    assert ticker_other not in body["agreements"]
    assert ticker_other not in body["conflicts"]
