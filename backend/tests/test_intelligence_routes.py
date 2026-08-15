"""
Tests for GET /intelligence/state (confirmed decision #47).

Uses a genuinely different pattern than the rest of this suite, worth
explaining: this route reads from the app's real singleton engines
(get_feature_engine()/get_level_interaction_engine()), started by the
app's own lifespan — so unlike test_market_routes.py's sync
`TestClient(app)` tests, this needs the SAME event loop driving both (a)
publishing synthetic events onto the real Event Bus and (b) the HTTP
request, or the engines' background worker tasks never get a chance to
run between the two. Sync TestClient can't do that (its portal thread
runs on a different loop than a bus obtained via asyncio.run() in the
test body would create — precisely the "Queue bound to a different event
loop" problem conftest.py's singleton-reset fixture exists to prevent).

Solved with httpx's ASGITransport plus the app's own lifespan_context as
an async context manager, inside one `@pytest.mark.asyncio` test — real
lifespan startup, real event publishing, and the real HTTP call all share
one event loop, so the background engines actually get to process
between "publish" and "request."

Needs real Postgres (FeatureEngine's default periods, [9, 20, 50], mean
its cold-start path does a real DB read on the first candle for any new
symbol) — skipped, not failed, if unreachable, same posture as every
other DB-backed test in this suite.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.event_bus.bus import get_event_bus
from app.event_bus.events import make_envelope
from app.main import app
from app.schemas.events.envelope import EventType
from app.schemas.events.market_data import CandleClosed


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


def _clean_test_symbol(ticker: str) -> None:
    session = SessionLocal()
    try:
        session.execute(
            text(
                "DELETE FROM level_interaction_events WHERE symbol_id IN "
                "(SELECT id FROM symbols WHERE ticker = :t)"
            ),
            {"t": ticker},
        )
        session.execute(
            text(
                "DELETE FROM level_interaction_state WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"
            ),
            {"t": ticker},
        )
        session.execute(text("DELETE FROM candles WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"), {"t": ticker})
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
        session.commit()
    finally:
        session.close()


async def _wait_until_published(client: httpx.AsyncClient, ticker: str, timeout: float = 8.0) -> dict:
    """
    Polls GET /intelligence/state until sma_9 actually shows up for this
    symbol, instead of a fixed sleep guessing how long N cold-start DB
    reads plus two engines' worker loops will take — that guess got
    genuinely flaky under concurrent multi-symbol load (two symbols'
    worth of cold-start backfills competing for the same event loop ran
    measurably slower than one), which is exactly the kind of test the
    fixed-sleep version looked like it passed right up until it didn't.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get("/intelligence/state", params={"symbol": ticker})
        body = resp.json()
        if body.get("timeframes", {}).get("1m", {}).get("units", {}).get("sma", {}).get("9") is not None:
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"sma_9 never appeared for {ticker} within {timeout}s")


async def _wait_until_candles_persisted(ticker: str, expected_count: int = 9, timeout: float = 8.0) -> None:
    """
    Separate from _wait_until_published() on purpose: sma_9 appearing
    only proves FeatureEngine finished — it says NOTHING about whether
    CandleRecorder has too. They're independent, concurrent subscribers
    to the same CandleClosed event, deliberately decoupled from each
    other (confirmed decision #45 — FeatureEngine reads the current
    candle's close straight from the event payload precisely so it
    never has to wait on CandleRecorder's write). That's the right
    design, but it means "FeatureEngine is done" is NOT a safe signal
    that it's safe to delete the underlying symbol row yet — found as a
    real, reproducible foreign-key violation in this test's own cleanup
    (candles written after cleanup believed it had nothing left to
    race), not a hypothetical. Every test in this file that touches
    CandleRecorder's data has to wait for THIS too before tearing
    anything down.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        session = SessionLocal()
        try:
            count = session.execute(
                text(
                    "SELECT count(*) FROM candles c JOIN symbols s ON s.id = c.symbol_id WHERE s.ticker = :t"
                ),
                {"t": ticker},
            ).scalar_one()
        finally:
            session.close()
        if count >= expected_count:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"only {count} of {expected_count} candles persisted for {ticker} within {timeout}s")


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


@pytest.mark.asyncio
async def test_intelligence_state_merges_feature_and_level_interaction_data():
    ticker = "__T_INTEL_A__"
    _clean_test_symbol(ticker)

    try:
        async with app.router.lifespan_context(app):
            bus = get_event_bus()
            base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=20)

            # 9 equal closes: SMA(9) == the close itself on the 9th candle,
            # which lands exactly on the level — a touch begins on the
            # very first FeaturesUpdated this process ever sees for this
            # symbol (cold-start-unknown-origin — already covered on its
            # own merits in test_level_interaction_engine.py; here it's
            # just a convenient way to exercise the "holding" branch of
            # the MERGE logic specifically).
            for i in range(9):
                payload = CandleClosed(
                    timeframe="1m", open=100.0, high=100.0, low=100.0, close=100.0, volume=10,
                    candle_ts=base_ts + timedelta(minutes=i),
                )
                await bus.publish(make_envelope(EventType.CANDLE_CLOSED, payload, symbol=ticker))

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                body = await _wait_until_published(client, ticker)
            await _wait_until_candles_persisted(ticker)

            assert body["symbol"] == ticker

            sma9 = body["timeframes"]["1m"]["units"]["sma"]["9"]
            assert sma9["value"] == 100.0
            assert sma9["level_interaction"]["zone"] == "inside_aura"
            assert sma9["level_interaction"]["touch_count_today"] == 1
            assert sma9["level_interaction"]["holding"]["anchor_price"] == 100.0
            assert sma9["level_interaction"]["distance_pct"] == 0.0  # top-level now, not nested under "holding" — decision #49
    finally:
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_intelligence_state_symbol_filter_does_not_leak_between_symbols():
    ticker_a = "__T_INTEL_B__"
    ticker_b = "__T_INTEL_C__"
    _clean_test_symbol(ticker_a)
    _clean_test_symbol(ticker_b)

    try:
        async with app.router.lifespan_context(app):
            bus = get_event_bus()
            base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=20)

            for ticker, close in [(ticker_a, 50.0), (ticker_b, 75.0)]:
                for i in range(9):
                    payload = CandleClosed(
                        timeframe="1m", open=close, high=close, low=close, close=close, volume=10,
                        candle_ts=base_ts + timedelta(minutes=i),
                    )
                    await bus.publish(make_envelope(EventType.CANDLE_CLOSED, payload, symbol=ticker))

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # Wait for BOTH symbols, not just the one under assertion —
                # letting the lifespan block (and therefore shutdown) start
                # while ticker_b is still mid-processing was exactly the
                # gap that raced shutdown against in-flight DB writes in
                # earlier runs of this test. Waiting for everything this
                # test actually published, before tearing anything down,
                # is the correct fix — independent of how robust shutdown
                # itself is.
                body = await _wait_until_published(client, ticker_a)
                await _wait_until_published(client, ticker_b)
            await _wait_until_candles_persisted(ticker_a)
            await _wait_until_candles_persisted(ticker_b)

            assert body["timeframes"]["1m"]["units"]["sma"]["9"]["value"] == 50.0  # ticker_a's value, not ticker_b's 75.0
    finally:
        _clean_test_symbol(ticker_a)
        _clean_test_symbol(ticker_b)


@pytest.mark.asyncio
async def test_intelligence_state_empty_for_never_seen_symbol():
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/intelligence/state", params={"symbol": "__T_INTEL_NEVER_SEEN__"})

        assert resp.status_code == 200
        assert resp.json() == {"symbol": "__T_INTEL_NEVER_SEEN__", "timeframes": {}}
