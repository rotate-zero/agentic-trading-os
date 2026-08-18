"""
Daily Levels tests (confirmed decision #59), two tiers:

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
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.feature_engine.engine import FeatureEngine
from app.feature_engine.indicators import DailyCandlePoint, cluster_daily_levels
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
    Counts calls so tests can assert the once-per-day cache/gate
    actually works, not just that it eventually returns the right data."""

    def __init__(self, candles_by_symbol: dict[str, list[CandleClosed]]) -> None:
        self._candles_by_symbol = candles_by_symbol
        self.call_count = 0
        self.calls: list[tuple[str, str, datetime, datetime]] = []

    async def get_historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[CandleClosed]:
        self.call_count += 1
        self.calls.append((symbol, timeframe, start, end))
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


@pytest.mark.asyncio
async def test_daily_levels_populate_from_the_registered_historical_provider():
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

        assert fake.call_count == 1
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
        assert daily_levels[0]["level_id"] == "__TEST_DL__-DL-1"

        # A second candle the SAME (ET) day must not trigger a second fetch —
        # this IS the caching/gate design doc §2 asked for.
        await _publish_1m_candle(bus, "__TEST_DL__", now + timedelta(minutes=1), 130.5)
        await asyncio.sleep(0.1)
        assert fake.call_count == 1
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
            await _publish_1m_candle(bus, "__TEST_DL_NOPROV__", now + timedelta(minutes=i), close)
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
async def test_provider_error_leaves_prior_levels_in_place_instead_of_wiping_them():
    """An unexpected provider failure on a LATER day must not erase a
    symbol's already-computed levels — same 'stale beats silently empty'
    reasoning as the docstring in engine.py's _maybe_refresh_daily_levels."""

    class _FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def get_historical(self, symbol, timeframe, start, end):
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
        await _publish_1m_candle(bus, "__TEST_DL_FLAKY__", now, 130.0)
        await asyncio.sleep(0.1)
        assert len(received[-1].payload["daily_levels"]) == 1

        # Force a same-process "new day" by directly resetting this
        # symbol's cached for_day, simulating the next calendar day's
        # first candle without needing to wait a literal day.
        engine._daily_levels_state["__TEST_DL_FLAKY__"]["for_day"] = None

        await _publish_1m_candle(bus, "__TEST_DL_FLAKY__", now + timedelta(minutes=1), 131.0)
        await asyncio.sleep(0.1)
        assert flaky.calls == 2
        # Still one level published — the flaky second fetch's exception
        # must not have wiped state to empty.
        assert len(received[-1].payload["daily_levels"]) == 1
    finally:
        await engine.stop()
        await bus.stop()
