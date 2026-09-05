"""
MarketStateEngine's per-timeframe race fix — decision #99's reconciliation
(a concurrent session's Momentum/VWAP review independently found this
same bug while reviewing the discarded orphan strategy files). Exercises
`_on_features_updated()` and `_compute()` directly, no `EventBus.start()`,
no worker loop, no persistence — both are pure/in-memory by their own
docstrings, so this doesn't need the module-wide Postgres skip
`test_market_state_engine.py` carries for its worker-loop/persistence
tests. `make_envelope()` is pure too (no I/O), used here exactly as
`EventBus.publish()` would internally, without needing a started bus.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.market_state_engine.engine import MarketStateEngine
from app.schemas.events.envelope import EventType
from app.schemas.events.features import FeatureSet

_TS = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)


def _feature_envelope(symbol: str, timeframe: str, sma_20_slope_angle: float):
    payload = FeatureSet(
        timeframe=timeframe, candle_ts=_TS, close=100.0,
        features={"sma_20_slope_angle": sma_20_slope_angle},
    )
    return make_envelope(EventType.FEATURES_UPDATED, payload, symbol=symbol)


@pytest.mark.asyncio
async def test_compute_reads_1m_slot_even_when_5m_arrived_more_recently():
    """The exact regression both this morning's review and decision #99
    describe: before the fix, a bare `_latest_features[symbol]` dict
    meant whichever timeframe's payload was written LAST won, regardless
    of which timeframe `_compute()` should actually be scoring against.
    Here, 1m arrives first (a strongly bullish slope), then 5m arrives
    SECOND with a strongly bearish slope for the same symbol — under the
    old single-key shape, `_compute()` would score off 5m's bearish
    value simply because it was written more recently. It must not."""
    engine = MarketStateEngine(EventBus())

    await engine._on_features_updated(_feature_envelope("TEST", "1m", sma_20_slope_angle=15.0))  # strongly bullish
    await engine._on_features_updated(_feature_envelope("TEST", "5m", sma_20_slope_angle=-15.0))  # strongly bearish, arrives after

    state = engine._compute("TEST")
    assert state is not None
    assert state.timeframe == "1m"
    assert state.trend_score > 50.0  # must still reflect 1m's bullish slope, not 5m's bearish one

    await engine._schedulers["TEST"].stop()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_compute_reads_1m_slot_regardless_of_arrival_order():
    """Same assertion, reversed arrival order (5m first, then 1m) — the
    fix must not be order-dependent in either direction."""
    engine = MarketStateEngine(EventBus())

    await engine._on_features_updated(_feature_envelope("TEST", "5m", sma_20_slope_angle=-15.0))
    await engine._on_features_updated(_feature_envelope("TEST", "1m", sma_20_slope_angle=15.0))

    state = engine._compute("TEST")
    assert state is not None
    assert state.trend_score > 50.0

    await engine._schedulers["TEST"].stop()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_compute_returns_none_when_only_a_slower_timeframe_has_reported():
    """Honest absence, not a fabricated cross-timeframe substitute: a 5m
    (or 15m/1h) FeaturesUpdated alone must not be enough for `_compute()`
    to produce a MarketState — only a 1m arrival can."""
    engine = MarketStateEngine(EventBus())

    await engine._on_features_updated(_feature_envelope("TEST", "5m", sma_20_slope_angle=15.0))

    assert engine._compute("TEST") is None


@pytest.mark.asyncio
async def test_non_1m_arrival_does_not_schedule_a_recompute():
    """A direct, narrowly-scoped consequence of the fix (module
    docstring): once `_compute()` always reads the 1m slot regardless of
    trigger, a 5m/15m/1h arrival scheduling a recompute would only ever
    re-derive the same MarketState from the unchanged 1m payload
    underneath it — a wasted persist and a duplicate publish. Storage
    still happens (previous test's `_latest_features` assertions cover
    that indirectly via `_compute` seeing the 1m payload); only the
    recompute trigger is 1m-only."""
    engine = MarketStateEngine(EventBus())

    await engine._on_features_updated(_feature_envelope("TEST", "5m", sma_20_slope_angle=15.0))
    assert "TEST" not in engine._schedulers

    await engine._on_features_updated(_feature_envelope("TEST", "1m", sma_20_slope_angle=15.0))
    assert "TEST" in engine._schedulers

    await engine._schedulers["TEST"].stop()  # avoid leaking a running scheduler task past this test
    await asyncio.sleep(0)  # let the event loop actually process the cancellation before teardown
