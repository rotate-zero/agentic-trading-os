"""
Daily Levels tests (confirmed decision #59), three tiers:

1. Pure clustering math (indicators/daily_levels.py) — no DB, no event
   loop, always runs. Includes a direct regression test for the
   arithmetic error caught during design review (an early proposal's
   worked example doesn't actually hold under its own stated rule — see
   that module's docstring and daily-levels-design.md §1).
2. In-memory engine wiring, through a real EventBus, using a FAKE
   historical provider registered via broker_registry — no DB, no real
   Polygon key required (this sandbox has no network access to Polygon
   at all; a real-key empirical check of daily-bar depth/rate limits
   remains an outstanding Stage 1 prerequisite — design doc §2 / decision
   #59's D1 — that only Saqib's own environment can actually run).
   Always runs.
3. Stage 2 (confirmed decision #63) — day-over-day identity
   reconciliation and restart-survival, against real local Postgres
   (`daily_levels_state`, migration 0003). Test tickers here are kept
   <=16 chars deliberately: `symbols.ticker` is VARCHAR(16), and an
   early version of these tests tripped a real StringDataRightTruncation
   from _get_or_create_symbol_id's INSERT before this was caught and fixed.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.feature_engine.engine import FeatureEngine
from app.feature_engine.indicators import ClusteredLevel, DailyCandlePoint, cluster_daily_levels
from app.models.daily_levels import DailyLevelState
from app.models.market_data import Symbol
from app.schemas.events.envelope import EventType
from app.schemas.events.market_data import CandleClosed
from app.services import broker_registry

# --- Tier 1: pure clustering math -------------------------------------------


def test_cluster_reproduces_the_corrected_worked_example():
    """The original 100.10/100.20/100.40 example — one 3-point level at
    ~100.23. Regression test for the arithmetic error an early proposal's
    OWN worked example didn't actually satisfy under its own stated rule
    (design doc §1): that proposal tested each candidate against the
    cluster's STALE running average, which rejects 100.40 (0.25 away from
    the then-current 100.15 average, outside a ~0.2003 tolerance). This
    module's corrected rule — validate the whole tentative cluster against
    the average IT would produce — must accept all three."""
    points = [DailyCandlePoint(0, 100.10), DailyCandlePoint(1, 100.20), DailyCandlePoint(2, 100.40)]
    levels = cluster_daily_levels(points, cluster_pct=0.002, min_distinct_candles=2)
    assert len(levels) == 1
    assert levels[0].price == pytest.approx(100.233333, abs=1e-4)
    assert levels[0].strength == 3
    assert levels[0].distinct_candle_count == 3


def test_naive_stale_average_rule_would_have_rejected_the_third_point():
    """Documents the bug directly, independent of this module's actual
    (correct) implementation — hand-computed, so a future refactor that
    accidentally reintroduces the stale-average check would be caught by
    test_cluster_reproduces_the_corrected_worked_example above, not
    silently passed."""
    stale_avg_after_first_two = (100.10 + 100.20) / 2  # 100.15
    tolerance_at_that_average = stale_avg_after_first_two * 0.002  # ~0.2003
    distance_of_third_point = abs(100.40 - stale_avg_after_first_two)  # 0.25
    assert distance_of_third_point > tolerance_at_that_average


def test_same_candle_pair_alone_is_not_a_valid_level():
    """§1.1 — one candle's own open+close landing close together must not
    manufacture a strength-2 level from a single candle."""
    points = [DailyCandlePoint(0, 100.10), DailyCandlePoint(0, 100.12)]
    levels = cluster_daily_levels(points, cluster_pct=0.002, min_distinct_candles=2)
    assert levels == []


def test_same_candle_pair_plus_a_second_candle_is_valid():
    """§1.1's own worked example: candle 0's open+close plus candle 1's
    open — strength 3 (total points), distinct_candle_count 2 (valid)."""
    points = [DailyCandlePoint(0, 100.10), DailyCandlePoint(0, 100.15), DailyCandlePoint(1, 100.20)]
    levels = cluster_daily_levels(points, cluster_pct=0.002, min_distinct_candles=2)
    assert len(levels) == 1
    assert levels[0].strength == 3
    assert levels[0].distinct_candle_count == 2


def test_isolated_point_is_discarded_not_rescued():
    """§1.2, resolved explicitly on Saqib's own question: no bias/
    relaxation mechanism. A point with no partner anywhere in the data is
    discarded outright, not force-included into the nearest cluster."""
    points = [DailyCandlePoint(0, 100.10), DailyCandlePoint(1, 100.20), DailyCandlePoint(2, 150.00)]
    levels = cluster_daily_levels(points, cluster_pct=0.002, min_distinct_candles=2)
    assert len(levels) == 1
    assert levels[0].strength == 2  # the 100.10/100.20 pair only; 150.00 discarded


def test_a_rejected_point_still_gets_a_fair_shot_as_the_next_seed():
    """The single sorted pass finds MULTIPLE separate levels — a point
    that fails to extend one cluster isn't just discarded, it becomes the
    seed of the next cluster attempt against everything still unused."""
    points = [
        DailyCandlePoint(0, 100.10), DailyCandlePoint(1, 100.15), DailyCandlePoint(2, 100.20),
        DailyCandlePoint(3, 150.00), DailyCandlePoint(4, 150.10),
    ]
    levels = cluster_daily_levels(points, cluster_pct=0.002, min_distinct_candles=2)
    assert len(levels) == 2
    prices = sorted(lvl.price for lvl in levels)
    assert prices[0] == pytest.approx(100.15, abs=0.01)
    assert prices[1] == pytest.approx(150.05, abs=0.01)


def test_empty_input_returns_no_levels():
    assert cluster_daily_levels([], cluster_pct=0.002, min_distinct_candles=2) == []


def test_min_distinct_candles_is_configurable():
    """A same-candle pair alone becomes valid if the caller explicitly
    lowers the gate to 1 — confirms the gate is a real, respected
    parameter, not a hardcoded assumption."""
    points = [DailyCandlePoint(0, 100.10), DailyCandlePoint(0, 100.12)]
    levels = cluster_daily_levels(points, cluster_pct=0.002, min_distinct_candles=1)
    assert len(levels) == 1
    assert levels[0].distinct_candle_count == 1


# --- Tier 2: engine wiring, in-memory, fake provider ------------------------


class _FakeHistoricalProvider:
    """Duck-typed MarketDataProvider stand-in — get_historical() only,
    since that's the only method _maybe_refresh_daily_levels calls.
    Counts calls PER TIMEFRAME (not just a single total) so tests can
    assert the once-per-day 1d-fetch cache/gate actually works
    specifically, without being thrown off by _maybe_refresh_premarket_baseline
    ALSO legitimately calling this same provider for "1m" data on every
    candle it processes — a real, independent second consumer of this
    interface, not something these tests were ever asserting about."""

    def __init__(self, candles_by_symbol: dict[str, list[CandleClosed]]) -> None:
        self._candles_by_symbol = candles_by_symbol
        self.call_count = 0
        self.calls: list[tuple[str, str, datetime, datetime]] = []
        self.calls_by_timeframe: dict[str, int] = {}

    async def get_historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[CandleClosed]:
        self.call_count += 1
        self.calls.append((symbol, timeframe, start, end))
        self.calls_by_timeframe[timeframe] = self.calls_by_timeframe.get(timeframe, 0) + 1
        return self._candles_by_symbol.get(symbol, [])


def _daily_candle(days_ago: int, open_: float, close: float, as_of: datetime) -> CandleClosed:
    ts = as_of - timedelta(days=days_ago)
    return CandleClosed(timeframe="1d", open=open_, high=max(open_, close), low=min(open_, close), close=close, volume=1000, candle_ts=ts)


async def _publish_1m_candle(bus: EventBus, symbol: str, candle_ts: datetime, close: float) -> None:
    payload = CandleClosed(timeframe="1m", open=close, high=close, low=close, close=close, volume=10, candle_ts=candle_ts)
    await bus.publish(make_envelope(EventType.CANDLE_CLOSED, payload, symbol=symbol))


@pytest.fixture(autouse=True)
def _reset_broker_registry():
    broker_registry.clear_all()
    yield
    broker_registry.clear_all()


def _delete_daily_levels_rows_for(ticker: str) -> None:
    session = SessionLocal()
    try:
        symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one_or_none()
        if symbol_id is not None:
            session.execute(DailyLevelState.__table__.delete().where(DailyLevelState.symbol_id == symbol_id))
            session.execute(Symbol.__table__.delete().where(Symbol.id == symbol_id))
        session.commit()
    finally:
        session.close()


@pytest.fixture
def _clean_daily_levels_symbol():
    """Stage 2 (decision #63) tests, unlike Stage 1's, actually persist
    real symbols/daily_levels_state rows — real Postgres, not reset
    between separate pytest invocations, so leftover rows from a prior
    run of THIS test can otherwise be picked up by the next run's
    restart-survival check and produce confusing, order-dependent
    failures (this happened once during development — see the
    reconciliation/restart tests' own tickers for why they're short).
    Returns the cleanup function so a test can call it once up front
    (defensive, in case a prior run left something) and doesn't need a
    second call after — Postgres isn't rolled back between tests in this
    suite, so leaving the fresh rows in place after a PASSING run is
    fine and matches how the rest of this file already behaves."""
    return _delete_daily_levels_rows_for


@pytest.mark.asyncio
async def test_daily_levels_populate_from_the_registered_historical_provider(_clean_daily_levels_symbol):
    # Stage 2 (decision #63) persists real rows for this ticker now —
    # clean up any from a prior run of this exact test in this same
    # (not-reset-between-invocations) Postgres, or the restart-survival
    # check would short-circuit past the provider before it's even set up.
    _clean_daily_levels_symbol("__TEST_DL__")

    now = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
    # Two candles close enough to cluster (0.2% default), one far away —
    # same shape as the isolated-point unit test above, exercised here
    # end-to-end through the engine instead of the pure function directly.
    fake = _FakeHistoricalProvider({
        "__TEST_DL__": [
            _daily_candle(5, 100.10, 100.10, now),
            _daily_candle(4, 100.20, 100.20, now),
            _daily_candle(3, 150.00, 150.00, now),
        ]
    })
    broker_registry.set_historical_provider(fake)

    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        await _publish_1m_candle(bus, "__TEST_DL__", now, 130.0)
        await asyncio.sleep(0.1)

        assert fake.calls_by_timeframe.get("1d", 0) == 1  # the daily-levels 1d fetch specifically — premarket's own separate 1m fetch also happens now, correctly, and isn't what this assertion is about
        assert len(received) == 1
        daily_levels = received[0].payload["daily_levels"]
        assert len(daily_levels) == 1
        # Each fixture candle has open == close, so each contributes TWO
        # coincident points — strength 4 (2 candles x 2 points), not 2;
        # the far-away 150.00 candle also has open == close, so its two
        # points collapse into ONE candle's worth (distinct_candle_count
        # 1) and correctly fail the >= 2 validity gate on their own.
        assert daily_levels[0]["strength"] == 4
        assert daily_levels[0]["distinct_candle_count"] == 2
        assert daily_levels[0]["price"] == pytest.approx(100.15, abs=0.01)
        # Stage 2 (decision #63) derives level_id from the persisted row's
        # own DB identity, not a per-symbol rank counter — the numeric
        # suffix is whatever the shared daily_levels_state.id sequence
        # currently is, not deterministically "1" the way Stage 1's
        # rank-based minting was. Check the stable part (symbol + "-DL-"
        # prefix, a real integer suffix), not an exact value.
        level_id = daily_levels[0]["level_id"]
        assert level_id.startswith("__TEST_DL__-DL-")
        assert level_id.removeprefix("__TEST_DL__-DL-").isdigit()

        # A second candle the SAME (ET) day must not trigger a second fetch —
        # this IS the caching/gate design doc §2 asked for.
        await _publish_1m_candle(bus, "__TEST_DL__", now + timedelta(minutes=1), 130.5)
        await asyncio.sleep(0.1)
        assert fake.calls_by_timeframe.get("1d", 0) == 1  # the daily-levels 1d fetch specifically — premarket's own separate 1m fetch also happens now, correctly, and isn't what this assertion is about
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_no_historical_provider_connected_yields_empty_daily_levels_not_a_crash():
    now = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[3], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        for i, close in enumerate([100.0, 102.0, 104.0]):
            await _publish_1m_candle(bus, "__TEST_DLNOPRV__", now + timedelta(minutes=i), close)
            await asyncio.sleep(0.05)

        # VWAP accumulates during regular session regardless of Daily
        # Levels (`now` is set to a regular-session UTC hour), so every
        # candle publishes something — the point of this test is that
        # daily_levels itself stays an honest empty list throughout, not
        # that publishing stops.
        assert len(received) == 3
        assert all(e.payload["daily_levels"] == [] for e in received)
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_get_daily_levels_reclusters_from_cached_candles_at_a_different_lookback(_clean_daily_levels_symbol):
    """Confirmed decision #62 — the lookback selector. Feeds enough
    distinct daily candles that a SHORTER lookback genuinely changes
    which points are available to cluster, not just re-returning the
    same result with a different label."""
    _clean_daily_levels_symbol("__TEST_DL_LKBK__")  # same not-reset-between-runs reasoning as the test above
    now = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
    fake = _FakeHistoricalProvider({
        "__TEST_DL_LKBK__": [
            # Oldest: a pair that only clusters with EACH OTHER, far from
            # the recent cluster below — present in the full history but
            # should drop out entirely once sliced to the most recent 2 candles.
            _daily_candle(10, 200.00, 200.00, now),
            _daily_candle(9, 200.05, 200.05, now),
            # Most recent two candles — a separate, closer-to-current-price cluster.
            _daily_candle(2, 100.10, 100.10, now),
            _daily_candle(1, 100.20, 100.20, now),
        ]
    })
    broker_registry.set_historical_provider(fake)

    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[], ema_periods=[])
    engine.start()
    try:
        await _publish_1m_candle(bus, "__TEST_DL_LKBK__", now, 130.0)
        await asyncio.sleep(0.1)
        assert fake.calls_by_timeframe.get("1d", 0) == 1  # the one and only 1d fetch — premarket's separate 1m fetch is a different, legitimate call

        # No lookback override — the full cached default: both clusters.
        default_levels = engine.get_daily_levels("__TEST_DL_LKBK__")
        assert len(default_levels) == 2

        # A short lookback (2 days) should only see the two MOST RECENT
        # candles — just the 100.10/100.20 cluster, the far-away 200.00
        # pair sliced away entirely. Zero additional provider calls.
        short_levels = engine.get_daily_levels("__TEST_DL_LKBK__", lookback_days=2)
        assert fake.calls_by_timeframe.get("1d", 0) == 1  # still just the one 1d fetch — re-clustering is free
        assert len(short_levels) == 1
        assert short_levels[0].price == pytest.approx(100.15, abs=0.01)

        # A lookback LARGER than what's actually cached must clamp, not
        # error or return nothing.
        clamped_levels = engine.get_daily_levels("__TEST_DL_LKBK__", lookback_days=999)
        assert len(clamped_levels) == 2
    finally:
        await engine.stop()
        await bus.stop()


def test_get_daily_levels_returns_empty_for_a_symbol_with_no_state():
    """Never-seen symbol — no KeyError, just an honest empty list, same
    'nothing computed yet, not zero' convention as get_snapshot()."""
    bus = EventBus()
    engine = FeatureEngine(bus, sma_periods=[], ema_periods=[])
    assert engine.get_daily_levels("__TEST_DL_NOST__") == []
    assert engine.get_daily_levels("__TEST_DL_NOST__", lookback_days=30) == []


def test_reconciliation_carries_level_id_forward_archives_and_mints_fresh(_clean_daily_levels_symbol):
    """The actual point of Stage 2 (design doc §4, confirmed decision
    #63) — calls _reconcile_and_persist_daily_levels directly across two
    simulated days for the same symbol, rather than fabricating full
    daily candle histories through the async pipeline, to test the
    matching algorithm itself precisely:
      - A level whose price drifts slightly (within tolerance) keeps its
        level_id — the whole reason this exists over rank-based keying.
      - A level that disappears entirely gets archived, not deleted.
      - A genuinely new cluster mints a brand-new level_id.
    """
    ticker = "__TEST_DL_RCN__"
    assert len(ticker) <= 16
    _clean_daily_levels_symbol(ticker)

    bus = EventBus()
    engine = FeatureEngine(bus, sma_periods=[], ema_periods=[])
    day1 = date(2026, 8, 17)
    day2 = date(2026, 8, 18)

    # Day 1: two levels — one that will persist (drifting slightly), one
    # that will disappear entirely on day 2.
    day1_clusters = [
        ClusteredLevel(price=100.00, strength=3, distinct_candle_count=3),
        ClusteredLevel(price=150.00, strength=2, distinct_candle_count=2),
    ]
    day1_levels = engine._reconcile_and_persist_daily_levels(ticker, day1, day1_clusters)
    assert len(day1_levels) == 2
    id_for_100 = next(lvl.level_id for lvl in day1_levels if abs(lvl.price - 100.00) < 0.01)
    id_for_150 = next(lvl.level_id for lvl in day1_levels if abs(lvl.price - 150.00) < 0.01)
    assert id_for_100 != id_for_150

    # Day 2: the ~100 level drifted to 100.05 (within the default 0.2%
    # match tolerance — well inside it), the ~150 level is GONE, and a
    # genuinely new ~200 level appeared.
    day2_clusters = [
        ClusteredLevel(price=100.05, strength=4, distinct_candle_count=3),
        ClusteredLevel(price=200.00, strength=2, distinct_candle_count=2),
    ]
    day2_levels = engine._reconcile_and_persist_daily_levels(ticker, day2, day2_clusters)
    assert len(day2_levels) == 2

    drifted = next(lvl for lvl in day2_levels if abs(lvl.price - 100.05) < 0.01)
    assert drifted.level_id == id_for_100  # SAME identity despite the price move — the whole point
    assert drifted.strength == 4  # and its other fields DID update to today's values

    brand_new = next(lvl for lvl in day2_levels if abs(lvl.price - 200.00) < 0.01)
    assert brand_new.level_id != id_for_100
    assert brand_new.level_id != id_for_150

    # The ~150 level must be archived in the DB, not deleted — design
    # doc §4's own "unmatched survivor is archived" language, checked
    # directly against the persisted row, not inferred from the API.
    session = SessionLocal()
    try:
        row = session.execute(select(DailyLevelState).where(DailyLevelState.level_id == id_for_150)).scalar_one()
        assert row.status == "archived"
        assert row.archived_day == day2
    finally:
        session.close()


@pytest.mark.asyncio
async def test_restart_survival_loads_todays_levels_without_a_second_provider_call(_clean_daily_levels_symbol):
    """Design doc §9's own Stage 2 requirement: rebuild level_id state
    from persisted history on a FRESH process, same standing pattern as
    every other engine in this codebase. Simulates a restart by
    constructing a completely NEW FeatureEngine instance (empty
    in-memory cache) pointed at a DIFFERENT fake provider that would
    return visibly different data if it were ever called — proving the
    restart-survival DB check short-circuits before reaching it, not
    just that the numbers happen to match."""
    ticker = "__TEST_DL_RST__"
    assert len(ticker) <= 16
    _clean_daily_levels_symbol(ticker)

    now = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
    original_provider = _FakeHistoricalProvider({
        ticker: [
            _daily_candle(5, 100.10, 100.10, now),
            _daily_candle(4, 100.20, 100.20, now),
        ]
    })
    broker_registry.set_historical_provider(original_provider)

    bus_a = EventBus()
    await bus_a.start()
    engine_a = FeatureEngine(bus_a, sma_periods=[], ema_periods=[])
    engine_a.start()
    try:
        await _publish_1m_candle(bus_a, ticker, now, 130.0)
        await asyncio.sleep(0.1)
        original_levels = engine_a.get_daily_levels(ticker)
        assert len(original_levels) == 1
        assert original_provider.calls_by_timeframe.get("1d", 0) == 1
    finally:
        await engine_a.stop()
        await bus_a.stop()

    # "Restart": a provider that would produce a DIFFERENT result if
    # called — if the restart-survival check has a bug and falls through
    # to a real fetch anyway, this test would catch it via a mismatched
    # price, not just a call-count assertion.
    poisoned_provider = _FakeHistoricalProvider({
        ticker: [
            _daily_candle(5, 999.00, 999.00, now),
            _daily_candle(4, 999.10, 999.10, now),
        ]
    })
    broker_registry.set_historical_provider(poisoned_provider)

    bus_b = EventBus()
    await bus_b.start()
    engine_b = FeatureEngine(bus_b, sma_periods=[], ema_periods=[])  # fresh instance — empty in-memory cache
    engine_b.start()
    received: list = []
    bus_b.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))
    try:
        await _publish_1m_candle(bus_b, ticker, now + timedelta(minutes=1), 130.5)
        await asyncio.sleep(0.1)

        assert poisoned_provider.calls_by_timeframe.get("1d", 0) == 0  # the 1d fetch is never reached — the whole point; premarket's own unrelated 1m fetch against this provider doesn't affect that claim
        restored_levels = engine_b.get_daily_levels(ticker)
        assert len(restored_levels) == 1
        assert restored_levels[0].level_id == original_levels[0].level_id
        assert restored_levels[0].price == pytest.approx(original_levels[0].price, abs=0.001)
        assert received[-1].payload["daily_levels"][0]["level_id"] == original_levels[0].level_id
    finally:
        await engine_b.stop()
        await bus_b.stop()


@pytest.mark.asyncio
async def test_provider_error_leaves_prior_levels_in_place_instead_of_wiping_them(_clean_daily_levels_symbol):
    """An unexpected provider failure on a LATER day must not erase a
    symbol's already-computed levels — same 'stale beats silently empty'
    reasoning as the docstring in engine.py's _maybe_refresh_daily_levels."""
    _clean_daily_levels_symbol("__TEST_DL_FLKY__")  # same not-reset-between-runs reasoning as the tests above

    class _FlakyProvider:
        """`self.calls` counts 1d-relevant calls only — premarket's own
        separate 1m fetch against this same provider (a real, unrelated
        second consumer now) must not consume this test's carefully
        sequenced "first call ok, second call fails" 1d-specific budget;
        it gets an unconditional empty result instead, which is harmless
        to premarket's own error handling (it just finds no data for
        this symbol this cycle, same as any other cold start)."""

        def __init__(self) -> None:
            self.calls = 0

        async def get_historical(self, symbol, timeframe, start, end):
            if timeframe != "1d":
                return []
            self.calls += 1
            if self.calls == 1:
                now_local = end
                return [
                    _daily_candle(5, 100.10, 100.10, now_local),
                    _daily_candle(4, 100.20, 100.20, now_local),
                ]
            # A genuinely UNEXPECTED failure (connection blip, timeout,
            # whatever) — NOT HistoricalDataUnavailableError, which means
            # a structural/permanent incapability and is correctly wiped
            # to empty rather than preserved (SymbolNotFoundError is the
            # same). This is the "stale beats silently empty" path.
            raise ConnectionError("simulated transient network failure")

    flaky = _FlakyProvider()
    broker_registry.set_historical_provider(flaky)

    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        now = datetime.now(timezone.utc).replace(hour=15, minute=0, second=0, microsecond=0)
        await _publish_1m_candle(bus, "__TEST_DL_FLKY__", now, 130.0)
        await asyncio.sleep(0.1)
        assert len(received[-1].payload["daily_levels"]) == 1

        # Force a same-process "new day": clearing the in-memory cache
        # alone isn't enough post-Stage-2 (decision #63) — the restart-
        # survival DB check (_load_confirmed_daily_levels_for_today)
        # would still find TODAY's just-persisted row and short-circuit
        # past the provider entirely, since the real calendar date
        # hasn't actually changed. Directly back-date that row's
        # last_confirmed_day so the DB check genuinely misses, the same
        # as it would the morning after a real rollover.
        engine._daily_levels_state["__TEST_DL_FLKY__"]["for_day"] = None
        from app.db.session import SessionLocal
        from app.models.daily_levels import DailyLevelState
        from app.models.market_data import Symbol
        from sqlalchemy import select, update

        session = SessionLocal()
        symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == "__TEST_DL_FLKY__")).scalar_one()
        session.execute(
            update(DailyLevelState)
            .where(DailyLevelState.symbol_id == symbol_id)
            .values(last_confirmed_day=date(2020, 1, 1))
        )
        session.commit()
        session.close()

        await _publish_1m_candle(bus, "__TEST_DL_FLKY__", now + timedelta(minutes=1), 131.0)
        await asyncio.sleep(0.1)
        assert flaky.calls == 2
        # Still one level published — the flaky second fetch's exception
        # must not have wiped state to empty.
        assert len(received[-1].payload["daily_levels"]) == 1
    finally:
        await engine.stop()
        await bus.stop()
