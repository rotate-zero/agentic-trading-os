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

from app.api.routes.intelligence import _parse_level_key
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
        # Daily Levels Stage 2 (confirmed decision #63) — daily_levels_state
        # also has a FK to symbols; without this, deleting the symbols row
        # below throws a real ForeignKeyViolation for any ticker Daily
        # Levels has ever persisted a row for (found via the full-suite
        # run that exposed this, not assumed ahead of time).
        session.execute(
            text("DELETE FROM daily_levels_state WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"),
            {"t": ticker},
        )
        session.execute(text("DELETE FROM candles WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"), {"t": ticker})
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
        session.commit()
    finally:
        session.close()


async def _wait_until_published(client: httpx.AsyncClient, ticker: str, timeout: float = 8.0) -> dict:
    """
    Polls GET /intelligence/state until sma_9 AND its level_interaction
    both show up for this symbol, instead of a fixed sleep guessing how
    long N cold-start DB reads plus two engines' worker loops will take —
    that guess got genuinely flaky under concurrent multi-symbol load (two
    symbols' worth of cold-start backfills competing for the same event
    loop ran measurably slower than one), which is exactly the kind of
    test the fixed-sleep version looked like it passed right up until it
    didn't.

    Waits for level_interaction specifically, not just sma_9's own value —
    found as a real, if intermittent, gap: FeatureEngine and
    LevelInteractionEngine are separate subscribers processing
    independently (FeaturesUpdated -> LevelInteractionChanged is a second
    hop, not synchronous with the first), so sma_9 can legitimately appear
    in /state slightly before its level_interaction does. Decision #52/#53
    (EMA, VWAP) added real per-candle work to FeatureEngine, which was
    enough to occasionally expose the gap under load that a leaner
    FeatureEngine mostly outran without anyone noticing it was there.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get("/intelligence/state", params={"symbol": ticker})
        body = resp.json()
        node = body.get("timeframes", {}).get("1m", {}).get("units", {}).get("sma", {}).get("9")
        if node is not None and "level_interaction" in node:
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"sma_9 + level_interaction never both appeared for {ticker} within {timeout}s")


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


def _et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    from zoneinfo import ZoneInfo
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/New_York"))


# The full set of level keys one previous-day computation publishes at
# once (confirmed decisions #56, #57): PDH/PDL/PDC, all nine Camarilla
# pivots, and VPOC. Used by tests below that need to wait for ALL of them,
# not just one or two — see _wait_until_level_interactions_present's own
# docstring for why that distinction is load-bearing, not pedantic.
_PREVIOUS_DAY_LEVEL_KEYS = frozenset(
    {"pdh", "pdl", "pdc", "cam_pp", "cam_r1", "cam_r2", "cam_r3", "cam_r4", "cam_s1", "cam_s2", "cam_s3", "cam_s4", "vpoc"}
)


def _find_unit_node(units: dict, raw_key: str) -> dict | None:
    """
    Locates whatever node GET /intelligence/state actually nests a given
    RAW FeatureSet key under — mirroring _parse_level_key's own grouping
    by importing and calling it directly, not reimplementing its rule
    here where the two could quietly drift apart. "sma_9" nests under
    units["sma"]["9"]; "cam_r1" nests under units["camarilla"]["r1"]
    (decision #66's grouping fix); "pdh"/"vwap" are their own flat
    units["pdh"]/units["vwap"], no nesting. Returns None if not present
    yet — same "absent means not published yet" reading the route
    itself uses, not a KeyError.
    """
    unit_key, period = _parse_level_key(raw_key)
    node = units.get(unit_key)
    if node is None or period is None:
        return node
    return node.get(period)


async def _wait_until_level_interactions_present(
    client: httpx.AsyncClient, ticker: str, level_keys: frozenset[str], timeout: float = 8.0
) -> dict:
    """
    Waits until EVERY key in `level_keys` has a level_interaction block in
    GET /intelligence/state — not just one or two of them.

    Found as a real, reproducible ForeignKeyViolation, not a hypothetical:
    LevelInteractionEngine processes one FeaturesUpdated's worth of level
    keys in a single asyncio.to_thread() call (_process_one) — a dozen
    sequential, blocking DB writes for previous-day levels + Camarilla
    together. Within that call, a given key's in-memory update and its DB
    persist happen back-to-back with nothing in between (verified directly
    against _process_one's own source, not assumed) — but the EVENT LOOP
    is free to service an HTTP request concurrently while that background
    thread is still midway through the OTHER eleven keys. A test that
    waits for only "pdh" to show up and then immediately deletes the
    symbol row can race a still-in-flight persist for "cam_s3" landing
    moments later — exactly what broke this suite's very first version of
    these tests. Waiting for ALL the keys a test actually touches closes
    the gap: by the time the LAST one appears in-memory, its own persist
    call (synchronous, same thread, same iteration) has already happened
    too.

    Looks each RAW key up via _find_unit_node rather than `k in units`
    directly (decision #66): every caller of this helper passes raw
    FeatureSet-shaped keys like "cam_pp", but the route groups Camarilla's
    nine of those under one "camarilla" family now, not nine flat
    top-level units — a literal `k in units` membership check would never
    find them and this helper would just time out.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get("/intelligence/state", params={"symbol": ticker})
        body = resp.json()
        units = body.get("timeframes", {}).get("1m", {}).get("units", {})
        if all((node := _find_unit_node(units, k)) is not None and "level_interaction" in node for k in level_keys):
            return body
        await asyncio.sleep(0.05)
    raise AssertionError(f"not all of {sorted(level_keys)} got level_interaction for {ticker} within {timeout}s")


async def _publish(bus, ticker: str, ts: datetime, close: float, *, high: float | None = None, low: float | None = None) -> None:
    payload = CandleClosed(
        timeframe="1m", open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=10, candle_ts=ts,
    )
    await bus.publish(make_envelope(EventType.CANDLE_CLOSED, payload, symbol=ticker))


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
        assert resp.json() == {"symbol": "__T_INTEL_NEVER_SEEN__", "timeframes": {}, "daily_levels": []}


# --- GET /intelligence/series (confirmed decision #54, Stage 1) ------------


@pytest.mark.asyncio
async def test_intelligence_series_rejects_unsupported_timeframe():
    """"1d" is real for /market/candles but not for this route — Feature
    Engine never aggregates to it (module docstring's own reasoning)."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/intelligence/series", params={"symbol": "ANY", "timeframe": "1d"})
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_intelligence_series_empty_for_never_recorded_symbol():
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/intelligence/series", params={"symbol": "__T_SERIES_NEVER_SEEN__", "timeframe": "1m"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "__T_SERIES_NEVER_SEEN__"
        assert body["timeframe"] == "1m"
        assert body["series"]["sma_9"] == []


@pytest.mark.asyncio
async def test_intelligence_series_reflects_real_persisted_candles():
    """
    Real end-to-end: a real CandleRecorder persists genuine 1m rows via a
    real Event Bus, then GET /intelligence/series reads them back through
    candle_store — proving the route's DB read actually sees what
    CandleRecorder actually wrote, not a mocked stand-in for either side.
    """
    ticker = "__T_SERIESB__"
    _clean_test_symbol(ticker)

    try:
        async with app.router.lifespan_context(app):
            bus = get_event_bus()
            base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=20)

            closes = [100.0, 101.0, 102.0]
            for i, close in enumerate(closes):
                payload = CandleClosed(
                    timeframe="1m", open=close, high=close, low=close, close=close, volume=10,
                    candle_ts=base_ts + timedelta(minutes=i),
                )
                await bus.publish(make_envelope(EventType.CANDLE_CLOSED, payload, symbol=ticker))

            await _wait_until_candles_persisted(ticker, expected_count=3)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/intelligence/series", params={"symbol": ticker, "timeframe": "1m"})

            assert resp.status_code == 200
            series = resp.json()["series"]
            # sma_50 (the largest default period) never warms up on 3
            # candles — genuinely empty, not missing from the response.
            assert series["sma_50"] == []
            # vwap needs only regular-session volume — all 3 candles
            # qualify if base_ts happens to land in regular hours; if not
            # (this test runs at an arbitrary wall-clock time), it's
            # legitimately empty too. Either way, the KEY must exist.
            assert "vwap" in series
    finally:
        _clean_test_symbol(ticker)


# --- Previous-day levels, Camarilla, pre-market H/L (confirmed decision #56) -
# Fixed real trading-day timestamps (_et helper above), not datetime.now() —
# these levels need an actual "previous day" to exist relative to "today,"
# which "now" can't guarantee across arbitrary test-run times the way it
# could for SMA/VWAP's own tests (which don't care what day it is).


@pytest.mark.asyncio
async def test_new_level_types_get_level_interaction_tracking_automatically():
    """
    The explicit requirement behind decision #56: PDH/PDL/PDC + Camarilla
    + pre-market H/L (and VPOC, decision #57) must get
    LevelInteractionEngine's touch/reject/conquer tracking, the same as
    SMA/EMA/VWAP already do — without that engine needing to know these
    key NAMES in advance (it's generic over FeatureSet.features — decision
    #46). Proven by checking GET /intelligence/state actually returns a
    level_interaction block for "pdh" and "cam_pp" specifically, not by
    re-testing the touch/reject/conquer state machine itself
    (level_interaction_engine.py's own test suite already covers that
    machinery directly, and it's the identical code path regardless of
    which key triggers it).
    """
    ticker = "__T_NEWLVL__"
    _clean_test_symbol(ticker)

    try:
        async with app.router.lifespan_context(app):
            bus = get_event_bus()

            # "Yesterday" (a real Tuesday) — high=105, low=95, close=102 (last).
            yesterday = _et(2026, 8, 11, 9, 30)
            for i, close in enumerate([100.0, 105.0, 95.0, 102.0]):
                await _publish(bus, ticker, yesterday + timedelta(minutes=i), close)
            await _wait_until_candles_persisted(ticker, expected_count=4)

            # "Today" (Wednesday) — first candle triggers the previous-day lookup.
            today = _et(2026, 8, 12, 9, 30)
            await _publish(bus, ticker, today, 101.0)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                body = await _wait_until_level_interactions_present(client, ticker, _PREVIOUS_DAY_LEVEL_KEYS)

            units = body["timeframes"]["1m"]["units"]
            assert units["pdh"]["value"] == 105.0
            assert units["pdl"]["value"] == 95.0
            assert units["pdc"]["value"] == 102.0
            # cam_pp groups under "camarilla" (decision #66's panel-grouping
            # fix), not its own flat "cam_pp" unit — the level_interaction
            # engine tracked it under the RAW key "cam_pp" regardless of how
            # this route groups it for display (see _wait_until_level_interactions_present's
            # own use of _PREVIOUS_DAY_LEVEL_KEYS just above), so only the
            # response-shape lookup changes here, not what's being proven.
            assert units["camarilla"]["pp"]["value"] == pytest.approx((105.0 + 95.0 + 102.0) / 3)
            # VPOC (decision #57) — same 4-candle "yesterday," bucketed;
            # not hand-verified to a specific bucket here (that's covered
            # directly in test_feature_engine.py's own VPOC tests) — this
            # just confirms it publishes and gets tracked, same as
            # everything else in this test.
            assert "vpoc" in units
            assert 95.0 <= units["vpoc"]["value"] <= 105.0  # within the previous day's own range, at minimum
            assert "level_interaction" in units["vpoc"]
            # The actual proof: LevelInteractionEngine tracked a key it was
            # never told about by name, same shape SMA's own tracking has.
            assert units["pdh"]["level_interaction"]["zone"] in {"above", "below", "inside_aura"}
            assert units["camarilla"]["pp"]["level_interaction"]["zone"] in {"above", "below", "inside_aura"}
    finally:
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_previous_day_levels_skip_a_weekend_gap():
    """"Previous day" means the most recent day with data, not literally
    yesterday's calendar date — Monday's previous day is Friday, not
    Sunday, since nothing was ever recorded on a day the market didn't
    open. Matches frontend/src/indicators/sessions.ts's own definition."""
    ticker = "__T_WKEND__"
    _clean_test_symbol(ticker)

    try:
        async with app.router.lifespan_context(app):
            bus = get_event_bus()

            friday = _et(2026, 8, 7, 9, 30)  # a real Friday
            for i, close in enumerate([50.0, 60.0, 40.0, 55.0]):
                await _publish(bus, ticker, friday + timedelta(minutes=i), close)
            await _wait_until_candles_persisted(ticker, expected_count=4)

            monday = _et(2026, 8, 10, 9, 30)  # the following Monday — no weekend data exists at all
            await _publish(bus, ticker, monday, 56.0)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                body = await _wait_until_level_interactions_present(client, ticker, _PREVIOUS_DAY_LEVEL_KEYS)

            units = body["timeframes"]["1m"]["units"]
            assert units["pdh"]["value"] == 60.0  # Friday's high, correctly reached across the weekend gap
            assert units["pdl"]["value"] == 40.0
            assert units["pdc"]["value"] == 55.0
    finally:
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_premarket_high_low_freezes_once_regular_session_starts():
    ticker = "__T_PMFREEZE__"
    _clean_test_symbol(ticker)

    try:
        async with app.router.lifespan_context(app):
            bus = get_event_bus()
            base = _et(2026, 8, 11, 8, 0)  # pre-market
            await _publish(bus, ticker, base, 100.0, high=102.0, low=98.0)
            await _publish(bus, ticker, base + timedelta(minutes=1), 100.0, high=105.0, low=99.0)  # widens the high
            await _wait_until_candles_persisted(ticker, expected_count=2)

            # Regular session opens — no more pre-market bars will ever
            # arrive for today, so pmh/pml should stay exactly where
            # pre-market left them.
            regular_open = _et(2026, 8, 11, 9, 30)
            await _publish(bus, ticker, regular_open, 101.0, high=110.0, low=90.0)  # a WIDER regular-session bar — must NOT affect pmh/pml

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                body = None
                deadline = asyncio.get_event_loop().time() + 8.0
                while asyncio.get_event_loop().time() < deadline:
                    resp = await client.get("/intelligence/state", params={"symbol": ticker})
                    candidate = resp.json()
                    units = candidate.get("timeframes", {}).get("1m", {}).get("units", {})
                    # Parsed-datetime comparison, not a raw string match —
                    # avoids depending on exactly how the offset gets
                    # formatted, while still correctly confirming the
                    # regular-session candle (not just the pre-market ones)
                    # has actually landed before asserting anything. This
                    # matters here specifically: catching pmh/pml at a
                    # transient state right after the 2nd pre-market candle
                    # but BEFORE the regular-session one would give a false
                    # pass even if regular-session candles were incorrectly
                    # folded in too — the exact bug this test exists to catch.
                    candle_ts_raw = units.get("pmh", {}).get("candle_ts")
                    if candle_ts_raw and datetime.fromisoformat(candle_ts_raw) >= regular_open:
                        body = candidate
                        break
                    await asyncio.sleep(0.05)
                assert body is not None, "the regular-session candle's update never landed within 8s"

                units = body["timeframes"]["1m"]["units"]
                assert units["pmh"]["value"] == 105.0  # frozen at pre-market's own high — NOT 110 from the regular-session bar
                assert units["pml"]["value"] == 98.0

                # Same cleanup-race concern _wait_until_level_interactions_present's
                # own docstring explains, applied here too before _clean_test_symbol
                # runs: pmh/pml is only 2 keys (lower risk than Camarilla's 12), but
                # sma/ema/vwap are also active on this symbol with default periods —
                # wait for the two THIS test actually asserts against, at minimum.
                await _wait_until_level_interactions_present(client, ticker, frozenset({"pmh", "pml"}))
    finally:
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_daily_levels_appear_in_intelligence_state():
    """
    Daily Levels (decision #59/#60/#61) end-to-end through the real route
    and real singleton FeatureEngine — the engine-level mechanics
    (clustering correctness, same-candle validity, the three provider-
    failure paths) are already covered thoroughly by test_daily_levels.py;
    this test's only job is confirming the ROUTE actually surfaces
    whatever FeatureEngine.get_snapshot() computed, symbol-scoped at the
    top level rather than nested under a timeframe (module docstring).
    """
    from app.services import broker_registry

    ticker = "__T_INTEL_DL__"
    _clean_test_symbol(ticker)

    class _FakeHistoricalProvider:
        async def get_historical(self, symbol, timeframe, start, end):
            base = end - timedelta(days=5)
            return [
                CandleClosed(timeframe="1d", open=100.10, high=100.10, low=100.10, close=100.10, volume=1000, candle_ts=base),
                CandleClosed(timeframe="1d", open=100.20, high=100.20, low=100.20, close=100.20, volume=1000, candle_ts=base + timedelta(days=1)),
            ]

        async def disconnect(self) -> None:
            # The real app's lifespan shutdown calls disconnect() on every
            # registered provider (broker_registry.get_all_active_providers())
            # — real providers implement this for real cleanup; this fake
            # just needs to not blow up when it's called.
            pass

    original_provider = broker_registry.get_historical_provider()
    broker_registry.set_historical_provider(_FakeHistoricalProvider())
    try:
        async with app.router.lifespan_context(app):
            bus = get_event_bus()
            now = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
            await _publish(bus, ticker, now, 130.0)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                body = None
                deadline = asyncio.get_event_loop().time() + 8.0
                while asyncio.get_event_loop().time() < deadline:
                    resp = await client.get("/intelligence/state", params={"symbol": ticker})
                    candidate = resp.json()
                    if candidate.get("daily_levels"):
                        body = candidate
                        break
                    await asyncio.sleep(0.05)
                assert body is not None, "daily_levels never appeared within 8s"

                # Top-level, not nested under timeframes — the module
                # docstring's whole reason this isn't per-timeframe.
                assert "daily_levels" in body
                assert "timeframes" in body
                levels = body["daily_levels"]
                assert len(levels) == 1
                assert levels[0]["strength"] == 4  # open==close per fixture candle — same shape test_daily_levels.py's engine test uses
                assert levels[0]["distinct_candle_count"] == 2
                assert levels[0]["price"] == pytest.approx(100.15, abs=0.01)
                # Stage 2 (decision #63) derives level_id from the persisted
                # row's own DB identity, not a per-symbol rank counter —
                # same fix already applied in test_daily_levels.py's own
                # equivalent assertion, for the same reason.
                level_id = levels[0]["level_id"]
                assert level_id.startswith(f"{ticker}-DL-")
                assert level_id.removeprefix(f"{ticker}-DL-").isdigit()
    finally:
        if original_provider is not None:
            broker_registry.set_historical_provider(original_provider)
        else:
            broker_registry.clear_historical_provider()
        _clean_test_symbol(ticker)


@pytest.mark.asyncio
async def test_daily_levels_carry_level_interaction_once_touched():
    """Stage 3 (confirmed decision #64) — closes the gap decision #61
    explicitly flagged ("No level_interaction data attached to these yet").
    Publishes a SECOND candle whose close actually lands inside the daily
    level's aura (unlike test_daily_levels_appear_in_intelligence_state's
    own candle, which stays far away on purpose to keep that test
    focused on the clustering/route wiring alone) and confirms the route
    attaches real, per-timeframe interaction data — not just that the
    field exists."""
    from app.services import broker_registry

    ticker = "__T_INTEL_DLLI__"
    _clean_test_symbol(ticker)

    class _FakeHistoricalProvider:
        async def get_historical(self, symbol, timeframe, start, end):
            base = end - timedelta(days=5)
            return [
                CandleClosed(timeframe="1d", open=100.10, high=100.10, low=100.10, close=100.10, volume=1000, candle_ts=base),
                CandleClosed(timeframe="1d", open=100.20, high=100.20, low=100.20, close=100.20, volume=1000, candle_ts=base + timedelta(days=1)),
            ]

        async def disconnect(self) -> None:
            pass

    original_provider = broker_registry.get_historical_provider()
    broker_registry.set_historical_provider(_FakeHistoricalProvider())
    try:
        async with app.router.lifespan_context(app):
            bus = get_event_bus()
            now = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
            # First candle: far away, just gets Daily Levels computed.
            await _publish(bus, ticker, now, 130.0)
            # Second candle: close lands right on the clustered level
            # (~100.15, per the same fixture shape used elsewhere) —
            # a real touch, which LevelInteractionEngine should pick up
            # via its new daily_levels loop with zero special-casing.
            await _publish(bus, ticker, now + timedelta(minutes=1), 100.15)

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                body = None
                deadline = asyncio.get_event_loop().time() + 8.0
                while asyncio.get_event_loop().time() < deadline:
                    resp = await client.get("/intelligence/state", params={"symbol": ticker})
                    candidate = resp.json()
                    levels = candidate.get("daily_levels", [])
                    if levels and levels[0].get("level_interaction"):
                        body = candidate
                        break
                    await asyncio.sleep(0.05)
                assert body is not None, "level_interaction never appeared on the daily_levels entry within 8s"

                level = body["daily_levels"][0]
                assert "1m" in level["level_interaction"]
                assert level["level_interaction"]["1m"]["zone"] == "inside_aura"
                assert level["level_interaction"]["1m"]["touch_count_today"] == 1
    finally:
        if original_provider is not None:
            broker_registry.set_historical_provider(original_provider)
        else:
            broker_registry.clear_historical_provider()
        _clean_test_symbol(ticker)
