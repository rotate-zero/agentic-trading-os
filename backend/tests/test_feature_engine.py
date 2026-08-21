"""
FeatureEngine tests, in three tiers:

1. Pure math (indicators.sma) — no DB, no event loop, always runs.
2. In-memory accumulation through a real EventBus — no DB required, since
   FeatureEngine only needs a DB read on the very first candle it sees for
   a never-before-seen symbol, and this tier deliberately stays inside
   that first candle's warm-up window. Always runs.
3. DB-backed cold-start backfill ("restart survival") — run against a REAL
   local Postgres, not mocked, same standard as test_candle_recorder.py
   (confirmed decisions #34, #37, #38). Skipped, not failed, if Postgres
   isn't reachable, same reasoning as test_candle_recorder.py's own skip.
   Only this specific test is decorated with the skip — the rest of this
   file needs no DB at all.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.feature_engine.engine import FeatureEngine
from app.feature_engine.indicators import aggregate_day, atr, camarilla_pivots, ema, fold_range, gap, session_change, sma, typical_price, volume_point_of_control, vwap_from_accumulator
from app.schemas.events.envelope import EventType
from app.schemas.events.market_data import CandleClosed
from app.services.candle_recorder import CandleRecorder


def _db_available() -> bool:
    try:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:  # noqa: BLE001 — this IS the availability check
        return False


def _clean_test_symbol(ticker: str) -> None:
    session = SessionLocal()
    try:
        session.execute(text(f"DELETE FROM candles WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = '{ticker}')"))
        session.execute(text(f"DELETE FROM symbols WHERE ticker = '{ticker}'"))
        session.commit()
    finally:
        session.close()


async def _publish_candle(bus: EventBus, symbol: str, candle_ts: datetime, close: float, timeframe: str = "1m") -> None:
    payload = CandleClosed(timeframe=timeframe, open=close, high=close, low=close, close=close, volume=10, candle_ts=candle_ts)
    await bus.publish(make_envelope(EventType.CANDLE_CLOSED, payload, symbol=symbol))


# --- Tier 1: pure math ------------------------------------------------------


def test_sma_computes_mean_of_last_n_closes():
    assert sma([1.0, 2.0, 3.0], 3) == 2.0
    assert sma([1.0, 2.0, 3.0, 4.0, 5.0], 3) == 4.0  # uses only the most recent 3


def test_sma_returns_none_during_warmup_not_zero_or_error():
    assert sma([1.0, 2.0], 3) is None


def test_sma_rejects_nonpositive_period():
    with pytest.raises(ValueError):
        sma([1.0, 2.0, 3.0], 0)


def test_ema_matches_hand_computed_recursion():
    """
    period=2, seed_multiplier=3 -> needed=6, window=[1..6]. Seed = mean of
    the OLDEST 2 (mean(1,2)=1.5), then the recursion (k=2/3) is applied
    forward through closes 3,4,5,6 — worked by hand in the PR/commit this
    test came with, not just re-derived from the implementation itself:
    1.5 -> 2.5 -> 3.5 -> 4.5 -> 5.5.
    """
    closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert ema(closes, 2, 3) == pytest.approx(5.5)


def test_ema_warmup_is_stricter_than_sma_at_the_same_period():
    """The whole point of D3's resolution (decision #52): EMA needs
    `period * seed_multiplier` closes, not just `period` — proven directly
    against the boundary, not just asserted in prose."""
    period, multiplier = 3, 5
    needed = period * multiplier
    assert ema([1.0] * (needed - 1), period, multiplier) is None
    assert ema([1.0] * needed, period, multiplier) is not None
    assert sma([1.0] * period, period) is not None  # sma needs far less history at the same period


def test_ema_rejects_nonpositive_period_or_multiplier():
    with pytest.raises(ValueError):
        ema([1.0, 2.0, 3.0], 0, 5)
    with pytest.raises(ValueError):
        ema([1.0, 2.0, 3.0], 2, 0)


# --- typical_price / vwap_from_accumulator (confirmed decision #53) --------


def test_typical_price_is_high_low_close_average():
    assert typical_price(high=12.0, low=8.0, close=10.0) == 10.0  # (12+8+10)/3


def test_vwap_from_accumulator_divides_pv_by_volume():
    assert vwap_from_accumulator(cumulative_pv=1000.0, cumulative_volume=100) == 10.0


def test_vwap_from_accumulator_returns_none_for_zero_volume():
    """Defensive against a zero-volume first bar of a session — division
    by zero must surface as 'not ready', not an exception."""
    assert vwap_from_accumulator(cumulative_pv=0.0, cumulative_volume=0) is None


# --- aggregate_day / camarilla_pivots / fold_range (confirmed decision #56) -


def test_aggregate_day_computes_high_low_close():
    base = _et(2026, 8, 11, 9, 30)
    rows = [
        _flat_row(base, close=100.0, high=105.0, low=95.0),
        _flat_row(base + timedelta(minutes=1), close=102.0, high=103.0, low=101.0),
        _flat_row(base + timedelta(minutes=2), close=98.0, high=99.0, low=90.0),
    ]
    result = aggregate_day(rows)
    assert result == (105.0, 90.0, 98.0)  # (high, low, LAST row's close)


def test_aggregate_day_returns_none_for_empty_list():
    assert aggregate_day([]) is None


def test_camarilla_pivots_matches_hand_computed_values():
    # range=20 -> r1/s1=+-1.833.., r2/s2=+-3.666.., r3/s3=+-5.5, r4/s4=+-11
    result = camarilla_pivots(high=110.0, low=90.0, close=100.0)
    assert result["pp"] == 100.0
    assert result["r1"] == pytest.approx(101.8333333)
    assert result["r4"] == pytest.approx(111.0)
    assert result["s1"] == pytest.approx(98.1666667)
    assert result["s4"] == pytest.approx(89.0)


def test_fold_range_seeds_from_none():
    assert fold_range(None, None, new_high=10.0, new_low=5.0) == (10.0, 5.0)


def test_fold_range_expands_only_when_the_new_bar_actually_widens_it():
    assert fold_range(current_high=10.0, current_low=5.0, new_high=8.0, new_low=6.0) == (10.0, 5.0)  # narrower bar — no change
    assert fold_range(current_high=10.0, current_low=5.0, new_high=12.0, new_low=3.0) == (12.0, 3.0)  # wider bar — both move


# --- volume_point_of_control (confirmed decision #57) -----------------------


def test_volume_point_of_control_matches_hand_computed_bucket():
    """
    Flat rows (high=low=close, matching _flat_row's default) so typical
    price == close exactly. closes=[10,60,60,90], bucket_count=4 ->
    range=[10,90], bucket_size=20 -> buckets [10,30) [30,50) [50,70) [70,90].
    close=10 -> bucket 0 (vol 5); close=60 -> bucket 2 (vol 20 each, two
    rows, total 40); close=90 -> (90-10)/20=4.0, clamped to the LAST
    bucket (3, vol 3) — the exact edge case the clamp exists for. Bucket 2
    wins with 40. VPOC = 10 + 20*(2+0.5) = 60.
    """
    base = _et(2026, 8, 11, 9, 30)
    rows = [
        _flat_row(base, close=10.0, volume=5),
        _flat_row(base + timedelta(minutes=1), close=60.0, volume=20),
        _flat_row(base + timedelta(minutes=2), close=60.0, volume=20),
        _flat_row(base + timedelta(minutes=3), close=90.0, volume=3),
    ]
    assert volume_point_of_control(rows, bucket_count=4) == pytest.approx(60.0)


def test_volume_point_of_control_returns_none_for_empty_list():
    assert volume_point_of_control([]) is None


def test_volume_point_of_control_degenerate_range_returns_the_flat_price():
    base = _et(2026, 8, 11, 9, 30)
    rows = [_flat_row(base, close=50.0, volume=10), _flat_row(base + timedelta(minutes=1), close=50.0, volume=20)]
    assert volume_point_of_control(rows) == 50.0


# --- session_change / gap (confirmed decisions #67/#68) ---------------------


def test_session_change_computes_pct_and_dollar_change_from_pdc():
    result = session_change(close=105.0, pdc=100.0)
    assert result == {"session_pct_change": 5.0, "session_dollar_change": 5.0}


def test_session_change_returns_empty_when_pdc_is_none():
    """No prior trading day in the lookback window yet (fresh
    symbol/deployment) — an honest gap, not an error, same convention
    `pdc` itself already carries."""
    assert session_change(close=105.0, pdc=None) == {}


def test_gap_computes_pct_and_dollar_gap_from_regular_open_and_pdc():
    result = gap(regular_open=102.0, pdc=100.0)
    assert result == {"gap_pct": 2.0, "gap_dollars": 2.0}


def test_gap_returns_empty_when_regular_open_is_none():
    """Before today's regular session has started (pre-market)."""
    assert gap(regular_open=None, pdc=100.0) == {}


def test_gap_returns_empty_when_pdc_is_none():
    """Fresh symbol/deployment, no prior trading day yet."""
    assert gap(regular_open=102.0, pdc=None) == {}


def test_session_change_defined_before_gap_when_only_pdc_is_known():
    """The intentional asymmetry called out in
    docs/architecture/feature-engine-indicator-expansion.md §3: a
    pre-market FeatureSet has `pdc` (previous day already elapsed) but not
    yet `regular_open` (today's regular session hasn't started). Session %
    Change only needs `pdc`, so it's defined; Gap also needs
    `regular_open`, so it isn't yet — not a bug, a real state difference."""
    pdc = 100.0
    assert session_change(close=98.0, pdc=pdc) != {}
    assert gap(regular_open=None, pdc=pdc) == {}


# --- atr (confirmed decisions #67/#68) ---------------------------------------


def _daily_bar(open_: float, high: float, low: float, close: float) -> CandleClosed:
    return CandleClosed(timeframe="1d", open=open_, high=high, low=low, close=close, volume=1000, candle_ts=_et(2026, 8, 10, 20, 0))


def test_atr_computes_wilder_average_true_range_from_the_seed_window():
    # period=2 needs 3 candles. TR(B) = max(110-100, |110-100|, |100-100|)
    # = 10. TR(C) = max(112-104, |112-108|, |104-108|) = 8. ATR = (10+8)/2
    # = 9. ATR% uses the LAST candle's close (110): 9/110*100.
    candles = [
        _daily_bar(100.0, 105.0, 95.0, 100.0),
        _daily_bar(100.0, 110.0, 100.0, 108.0),
        _daily_bar(108.0, 112.0, 104.0, 110.0),
    ]
    result = atr(candles, period=2)
    assert result["atr_2"] == pytest.approx(9.0)
    assert result["atr_2_pct"] == pytest.approx(9.0 / 110.0 * 100.0)


def test_atr_returns_empty_when_fewer_than_period_plus_one_candles():
    """period=14 needs 15 candles (14 True Ranges, each needing a prior
    close) — an honest gap, not a fabricated partial-period average, same
    convention as everywhere else in this engine."""
    candles = [_daily_bar(100.0, 101.0, 99.0, 100.0) for _ in range(14)]  # one short
    assert atr(candles, period=14) == {}


# --- Tier 2: in-memory accumulation, no DB needed ---------------------------


@pytest.mark.asyncio
async def test_feature_engine_publishes_once_warmed_up_and_not_before():
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[3], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        closes = [100.0, 102.0, 104.0]  # SMA(3) on the 3rd close = 102.0
        for i, close in enumerate(closes):
            await _publish_candle(bus, "__TEST_FE_MEM__", base_ts + timedelta(minutes=i), close)
            await asyncio.sleep(0.05)

        assert len(received) == 1  # only the 3rd close had enough history — no premature publish
        features = received[0].payload["features"]
        assert features["sma_3"] == 102.0
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_sma_and_ema_coexist_in_the_same_featureset_with_independent_warmup():
    """
    Confirmed decision #52: one shared window, two indicator families, each
    warming up on its own schedule. period=2 for both, but EMA's
    seed_multiplier=3 means it needs 6 closes where SMA needs 2 — proven
    end-to-end through a real EventBus, not just at the pure-function level
    (test_ema_warmup_is_stricter_than_sma_at_the_same_period already covers
    that in isolation).
    """
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[2], ema_periods=[2], ema_seed_multiplier=3)
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        for i, close in enumerate(closes):
            await _publish_candle(bus, "__TEST_FE_EMA_MIX__", base_ts + timedelta(minutes=i), close)
            await asyncio.sleep(0.05)

        assert len(received) == 5  # 1st candle: neither ready yet (sma_2 needs 2). 2nd-6th: sma_2 ready each time.
        # Before the 6th candle: sma_2 present, ema_2 deliberately absent (still warming up).
        for event in received[:-1]:
            assert "sma_2" in event.payload["features"]
            assert "ema_2" not in event.payload["features"]
        # On the 6th candle: both present, ema_2 matching the hand-computed value
        # test_ema_matches_hand_computed_recursion already verified independently.
        final_features = received[-1].payload["features"]
        assert final_features["sma_2"] == 5.5
        assert final_features["ema_2"] == pytest.approx(5.5)
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_feature_engine_ignores_non_1m_timeframes():
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        await _publish_candle(bus, "__TEST_FE_5M__", datetime.now(timezone.utc), 100.0, timeframe="5m")
        await asyncio.sleep(0.1)
        assert received == []
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_get_snapshot_reflects_latest_computed_values():
    """
    Pure in-memory — sma_periods=[1] deliberately: SMA(1) is just the
    current close, and max_period=1 is the one case _compute_one() skips
    the cold-start DB backfill entirely (`if self._max_period > 1`), so
    this proves get_snapshot()'s own shape/filtering logic in isolation
    from anything DB-related (that's covered separately, with real
    Postgres, in the cold-start test above).
    """
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    try:
        await _publish_candle(bus, "__TEST_FE_SNAP_A__", datetime.now(timezone.utc), 100.0)
        await _publish_candle(bus, "__TEST_FE_SNAP_B__", datetime.now(timezone.utc), 200.0)
        await asyncio.sleep(0.1)

        full = engine.get_snapshot()
        assert full["__TEST_FE_SNAP_A__"]["1m"]["features"]["sma_1"] == 100.0
        assert full["__TEST_FE_SNAP_B__"]["1m"]["features"]["sma_1"] == 200.0

        filtered = engine.get_snapshot(symbol="__TEST_FE_SNAP_A__")
        assert set(filtered.keys()) == {"__TEST_FE_SNAP_A__"}  # B excluded when filtering by A

        assert engine.get_snapshot(symbol="__TEST_FE_NEVER_PUBLISHED__") == {}
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_feature_engine_drops_duplicate_candle_closed():
    """Mirrors tick_ingest.py's own accepted rare-duplicate race (confirmed
    decision #42) — a repeated CandleClosed for the same minute must not
    double-count that close into the SMA window."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[3], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        await _publish_candle(bus, "__TEST_FE_DUP__", base_ts, 100.0)
        await _publish_candle(bus, "__TEST_FE_DUP__", base_ts, 100.0)  # exact duplicate
        await _publish_candle(bus, "__TEST_FE_DUP__", base_ts + timedelta(minutes=1), 102.0)
        await _publish_candle(bus, "__TEST_FE_DUP__", base_ts + timedelta(minutes=2), 104.0)
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[-1].payload["features"]["sma_3"] == 102.0  # (100+102+104)/3, not double-counted
    finally:
        await engine.stop()
        await bus.stop()


# --- Tier 3: DB-backed cold-start backfill ("restart survival") ------------


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
@pytest.mark.asyncio
async def test_feature_engine_backfills_from_persisted_history_on_cold_start():
    """
    Simulates a real restart: prior candles already sit in Postgres
    (written by a normal CandleRecorder in an earlier 'process lifetime'),
    then a BRAND NEW FeatureEngine instance — no in-memory window for this
    symbol yet — sees its first CandleClosed and must backfill from
    persisted history to publish a correct SMA immediately, rather than
    silently resetting to a multi-candle warm-up as if it were a new
    symbol. Same "rebuild from persisted history on startup" requirement
    already decided for Market State Engine (trading-intelligence-
    architecture.md §4), applied here.
    """
    ticker = "__TEST_FE_COLD__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    base_ts = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=10)

    try:
        # Phase 1: an earlier "process" persists 2 prior closes via a real CandleRecorder.
        recorder = CandleRecorder(bus)
        recorder.start()
        try:
            await _publish_candle(bus, ticker, base_ts, 100.0)
            await _publish_candle(bus, ticker, base_ts + timedelta(minutes=1), 102.0)
            await asyncio.sleep(0.3)  # let the write-behind writer actually land both rows
        finally:
            await recorder.stop()
        # Phase 2: a FRESH FeatureEngine — no in-memory window for this symbol —
        # simulating what a real restart looks like.
        engine = FeatureEngine(bus, sma_periods=[3], ema_periods=[])
        engine.start()
        received: list = []
        bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

        try:
            await _publish_candle(bus, ticker, base_ts + timedelta(minutes=2), 104.0)
            await asyncio.sleep(0.2)

            assert len(received) == 1  # correct on the FIRST event after cold start, no re-warm-up needed
            assert received[0].payload["features"]["sma_3"] == 102.0
        finally:
            await engine.stop()
    finally:
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
def test_get_recent_closes_returns_empty_for_a_never_seen_symbol():
    from app.services import candle_store

    result = candle_store.get_recent_closes("__TEST_FE_NEVER_SEEN__", "1m", datetime.now(timezone.utc), limit=10)
    assert result == []


# --- Tier 4: aggregated timeframes — 5m/15m/1h (confirmed decision #51) -----
# Fixed, real trading-session timestamps (same _et() convention as
# test_candle_aggregator.py), not datetime.now() — session_bounds() must
# return a real regular-session window for the aggregated path to run at
# all, which "now" can't guarantee across arbitrary test-run times.


def _et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    from zoneinfo import ZoneInfo
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("America/New_York"))


def _flat_row(ts: datetime, close: float, *, high: float | None = None, low: float | None = None, volume: int = 10) -> CandleClosed:
    """A CandleClosed with explicit high/low (unlike _publish_candle's own
    open=high=low=close shortcut) — needed wherever a test actually checks
    that high/low get used, not just close (aggregate_day, fold_range)."""
    return CandleClosed(
        timeframe="1m", open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=volume, candle_ts=ts,
    )


@pytest.mark.asyncio
async def test_5m_bucket_completes_only_on_its_final_minute_not_before():
    """periods=[1] deliberately — see test_get_snapshot_reflects_latest_
    computed_values's own note on why that sidesteps the DB cold-start
    branch, isolating this test to boundary-detection timing alone."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        base = _et(2026, 8, 11, 9, 30)  # a real Tuesday regular session
        # Minutes :30, :31, :32, :33 — none of these complete the [9:30,9:35) 5m bucket.
        for i in range(4):
            await _publish_candle(bus, "__TEST_FE_5M_EARLY__", base + timedelta(minutes=i), 100.0 + i)
        await asyncio.sleep(0.1)

        timeframes_seen = {e.payload["timeframe"] for e in received}
        assert timeframes_seen == {"1m"}  # only 1m fired — the 5m bucket hasn't closed yet
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_5m_bucket_completion_publishes_5m_features_with_correct_close():
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        base = _et(2026, 8, 11, 9, 30)
        for i in range(5):  # :30 through :34 — :34 is the bucket's last member
            await _publish_candle(bus, "__TEST_FE_5M_COMPLETE__", base + timedelta(minutes=i), 100.0 + i)
        await asyncio.sleep(0.1)

        timeframes_seen = {e.payload["timeframe"] for e in received}
        assert timeframes_seen == {"1m", "5m"}  # exactly one aggregated publish, on the 5th candle

        five_min_events = [e for e in received if e.payload["timeframe"] == "5m"]
        assert len(five_min_events) == 1
        payload = five_min_events[0].payload
        assert payload["close"] == 104.0  # the bucket's own last member's close, matching the 1m payload's close
        assert payload["features"]["sma_1"] == 104.0
        assert datetime.fromisoformat(payload["candle_ts"]) == base  # bucket_start, not the completing candle's own ts
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_1h_boundary_publishes_5m_15m_and_1h_together():
    """The one case worth proving explicitly per test_candle_aggregator.py's
    own equivalent test: a single 1m close at a real hour boundary must
    fan out to all three aggregated timeframes, not just the coarsest."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        base = _et(2026, 8, 11, 9, 30)
        boundary_ts = _et(2026, 8, 11, 10, 29)  # 60 minutes after session open — closes 5m, 15m, AND 1h at once
        # Only need the boundary candle itself for this check — sma_periods=[1]
        # needs no prior history, so nothing earlier in the hour is required.
        await _publish_candle(bus, "__TEST_FE_1H_BOUNDARY__", boundary_ts, 200.0)
        await asyncio.sleep(0.1)

        timeframes_seen = {e.payload["timeframe"] for e in received}
        assert timeframes_seen == {"1m", "5m", "15m", "1h"}
        for e in received:
            if e.payload["timeframe"] != "1m":
                assert e.payload["close"] == 200.0
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
@pytest.mark.asyncio
async def test_aggregated_timeframe_backfills_prior_bars_on_cold_start():
    """
    Same 'simulate a real restart' shape as
    test_feature_engine_backfills_from_persisted_history_on_cold_start,
    one level up: an earlier 'process' persists two full prior 5m buckets'
    worth of real 1m candles via CandleRecorder, then a BRAND NEW
    FeatureEngine — no in-memory 5m window yet — must backfill BOTH prior
    5m closes via candle_aggregator.aggregate_from_recorded() (NOT
    candle_store.get_recent_closes(), which would silently return [] for
    a "5m"-labeled row that never exists — see _compute_aggregated's own
    docstring) to have enough history for sma_3 on the immediately NEXT
    5m bucket's close, correct on the very first aggregated publish after
    cold start — no re-warm-up, same requirement #45 already established
    for the 1m path.
    """
    ticker = "__FE5MCOLD__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    base = _et(2026, 8, 11, 9, 30)

    try:
        # Phase 1: an earlier "process" persists TWO full 5m buckets
        # (09:30-09:34 close=104.0, 09:35-09:39 close=109.0) via a real
        # CandleRecorder.
        recorder = CandleRecorder(bus)
        recorder.start()
        try:
            for i in range(10):
                await _publish_candle(bus, ticker, base + timedelta(minutes=i), 100.0 + i)
            await asyncio.sleep(0.3)  # let the write-behind writer land all 10 rows
        finally:
            await recorder.stop()

        # Phase 2: a FRESH FeatureEngine — simulating a real restart, no
        # in-memory window for this symbol at all — needs sma_3 on 5m,
        # which requires the 2 PRIOR 5m closes above PLUS this third
        # bucket's own close (114.0) to be correct on the first publish.
        engine = FeatureEngine(bus, sma_periods=[3], ema_periods=[])
        engine.start()
        received: list = []
        bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

        try:
            # Third 5m bucket (09:40-09:44), close=114.0.
            for i in range(10, 15):
                await _publish_candle(bus, ticker, base + timedelta(minutes=i), 100.0 + i)
            await asyncio.sleep(0.2)

            five_min_events = [e for e in received if e.payload["timeframe"] == "5m"]
            assert len(five_min_events) == 1  # correct on the FIRST aggregated event after cold start
            assert five_min_events[0].payload["close"] == 114.0
            assert five_min_events[0].payload["features"]["sma_3"] == pytest.approx((104.0 + 109.0 + 114.0) / 3)
        finally:
            await engine.stop()
    finally:
        await bus.stop()
        _clean_test_symbol(ticker)


# --- Tier 5: VWAP (confirmed decision #53) ----------------------------------
# _publish_candle() sets open=high=low=close and volume=10 fixed — so
# typical_price() collapses to plain `close` for every bar here, and VWAP
# reduces to a simple mean of closes (constant volume weight). That's
# deliberate: it keeps these tests hand-verifiable without needing a new
# fixture, and typical_price()'s own H/L/C weighting is already covered
# directly in Tier 1 (test_typical_price_is_high_low_close_average).


@pytest.mark.asyncio
async def test_vwap_absent_pre_market_present_once_regular_session_opens():
    """sma_periods=[1] so SOMETHING publishes every candle regardless of
    VWAP — isolates whether the "vwap" key itself is present, rather than
    whether a publish happens at all."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        await _publish_candle(bus, "__TEST_FE_VWAP_PM__", _et(2026, 8, 11, 8, 0), 50.0)  # pre-market
        await _publish_candle(bus, "__TEST_FE_VWAP_PM__", _et(2026, 8, 11, 9, 30), 100.0)  # session open
        await asyncio.sleep(0.1)

        assert len(received) == 2
        assert "vwap" not in received[0].payload["features"]  # pre-market: excluded, matching vwap.ts
        assert received[1].payload["features"]["vwap"] == 100.0  # first regular bar: vwap == its own close
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_vwap_accumulates_within_a_session_and_resets_at_the_next_one():
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        ticker = "__TEST_FE_VWAP_RST__"
        day1_open = _et(2026, 8, 11, 9, 30)
        await _publish_candle(bus, ticker, day1_open, 100.0)
        await _publish_candle(bus, ticker, day1_open + timedelta(minutes=1), 200.0)
        # Day 2 (2026-08-12, a Wednesday) — a genuinely new regular session.
        day2_open = _et(2026, 8, 12, 9, 30)
        await _publish_candle(bus, ticker, day2_open, 300.0)
        await asyncio.sleep(0.1)

        assert len(received) == 3
        assert received[0].payload["features"]["vwap"] == 100.0
        assert received[1].payload["features"]["vwap"] == pytest.approx(150.0)  # mean(100, 200)
        # Day 2's first bar must NOT be blended with day 1's accumulator —
        # this is the whole point of the test.
        assert received[2].payload["features"]["vwap"] == 300.0
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_vwap_is_identical_across_1m_and_5m_featuresets_on_the_same_close():
    """The core architectural claim in the module docstring: VWAP is
    computed once, from 1m bars, and the SAME value is attached to every
    timeframe's FeatureSet — never recomputed per-timeframe from coarser
    bars, which would let 1m and 5m VWAP silently diverge."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        base = _et(2026, 8, 11, 9, 30)
        for i in range(5):  # :30 through :34 — :34 completes the [9:30,9:35) 5m bucket
            await _publish_candle(bus, "__TEST_FE_VWAP_5M__", base + timedelta(minutes=i), 100.0 + i)
        await asyncio.sleep(0.1)

        by_timeframe = {e.payload["timeframe"]: e.payload["features"]["vwap"] for e in received}
        assert set(by_timeframe) == {"1m", "5m"}
        assert by_timeframe["1m"] == by_timeframe["5m"] == pytest.approx(sum(100.0 + i for i in range(5)) / 5)
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_vwap_publishes_even_while_sma_is_still_warming_up():
    """extra_features (VWAP) merges in BEFORE _apply_close's "nothing
    ready yet" check — proven here with an SMA period (50) this test never
    comes close to satisfying."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[50], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        await _publish_candle(bus, "__TEST_FE_VWAP_WARMUP__", _et(2026, 8, 11, 9, 30), 100.0)
        await asyncio.sleep(0.1)

        assert len(received) == 1  # published on VWAP alone
        features = received[0].payload["features"]
        assert features == {"vwap": 100.0}  # sma_50 genuinely absent — not warmed up, not silently 0
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
@pytest.mark.asyncio
async def test_vwap_backfills_from_persisted_history_on_cold_start():
    """Same 'simulate a real restart mid-session' shape as the SMA/5m cold-
    start tests above: two 1m candles persisted via a real CandleRecorder,
    then a BRAND NEW FeatureEngine's VWAP must include both — not just the
    third candle it directly observes — on its very first computation."""
    ticker = "__FEVWAPCOLD__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    base = _et(2026, 8, 11, 9, 30)

    try:
        recorder = CandleRecorder(bus)
        recorder.start()
        try:
            await _publish_candle(bus, ticker, base, 100.0)
            await _publish_candle(bus, ticker, base + timedelta(minutes=1), 200.0)
            await asyncio.sleep(0.3)
        finally:
            await recorder.stop()

        engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
        engine.start()
        received: list = []
        bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

        try:
            await _publish_candle(bus, ticker, base + timedelta(minutes=2), 300.0)
            await asyncio.sleep(0.2)

            assert len(received) == 1
            # mean(100, 200, 300) — NOT just 300.0, which is what a fresh
            # engine with no backfill would wrongly produce.
            assert received[0].payload["features"]["vwap"] == pytest.approx(200.0)
        finally:
            await engine.stop()
    finally:
        await bus.stop()
        _clean_test_symbol(ticker)


# --- Tier 6: Session % Change / Gap / ATR (confirmed decisions #67/#68) -----


@pytest.mark.asyncio
async def test_session_change_and_gap_absent_when_no_previous_day_known():
    """Fresh symbol, no persisted history at all — `pdc` is None, so both
    families stay absent end-to-end rather than publishing a fabricated 0.
    sma_periods=[1] so something publishes regardless of these two."""
    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
    engine.start()

    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        await _publish_candle(bus, "__TEST_FE_SESSGAP_NOPDC__", _et(2026, 8, 11, 9, 30), 100.0)
        await _publish_candle(bus, "__TEST_FE_SESSGAP_NOPDC__", _et(2026, 8, 11, 9, 31), 101.0)
        await asyncio.sleep(0.1)

        assert len(received) == 2
        for envelope in received:
            features = envelope.payload["features"]
            assert "session_pct_change" not in features
            assert "session_dollar_change" not in features
            assert "gap_pct" not in features
            assert "gap_dollars" not in features
    finally:
        await engine.stop()
        await bus.stop()


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
@pytest.mark.asyncio
async def test_gap_captures_regular_open_once_and_freezes_through_the_session():
    """Session % Change and Gap both key off the SAME `pdc`, but only
    Session % Change keeps tracking `close` afterward — the core
    architectural claim the whole distinction rests on
    (feature-engine-indicator-expansion.md §2/§3), proven end-to-end here
    rather than only at the pure-function level above."""
    ticker = "__FEGAPFRZ__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()

    try:
        recorder = CandleRecorder(bus)
        recorder.start()
        try:
            day1 = _et(2026, 8, 11, 9, 30)  # prior trading day — establishes pdc = 100.0
            await _publish_candle(bus, ticker, day1, 100.0)
            await asyncio.sleep(0.2)
        finally:
            await recorder.stop()

        engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
        engine.start()
        received: list = []
        bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

        try:
            day2 = _et(2026, 8, 12, 9, 30)  # today's regular session
            await _publish_candle(bus, ticker, day2, 110.0)  # first regular candle: establishes gap
            await _publish_candle(bus, ticker, day2 + timedelta(minutes=1), 120.0)  # close keeps moving
            await asyncio.sleep(0.2)

            assert len(received) == 2
            first, second = (e.payload["features"] for e in received)

            # Gap = 110 vs pdc 100 = +10% / +$10 — captured on the FIRST
            # regular candle and must stay IDENTICAL on the second, even
            # though close moved from 110 to 120.
            assert first["gap_pct"] == pytest.approx(10.0)
            assert first["gap_dollars"] == pytest.approx(10.0)
            assert second["gap_pct"] == pytest.approx(10.0)
            assert second["gap_dollars"] == pytest.approx(10.0)

            # Session % Change, by contrast, tracks `close` continuously.
            assert first["session_pct_change"] == pytest.approx(10.0)
            assert second["session_pct_change"] == pytest.approx(20.0)
        finally:
            await engine.stop()
    finally:
        await bus.stop()
        _clean_test_symbol(ticker)


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
@pytest.mark.asyncio
async def test_gap_backfills_regular_open_on_cold_start():
    """Same 'simulate a real restart mid-morning' shape as VWAP's own
    cold-start test above: day2's FIRST regular-session candle is
    persisted BEFORE a brand-new FeatureEngine ever starts — that
    engine's first LIVE observation is day2's SECOND candle, at a
    different close entirely. Gap must still reflect the backfilled first
    candle's open, not mistake the second candle it directly saw for the
    regular open."""
    ticker = "__FEGAPCOLD__"
    _clean_test_symbol(ticker)
    bus = EventBus()
    await bus.start()
    day1 = _et(2026, 8, 11, 9, 30)
    day2 = _et(2026, 8, 12, 9, 30)

    try:
        recorder = CandleRecorder(bus)
        recorder.start()
        try:
            await _publish_candle(bus, ticker, day1, 100.0)  # prior day — pdc
            await _publish_candle(bus, ticker, day2, 108.0)  # today's ACTUAL regular open
            await asyncio.sleep(0.2)
        finally:
            await recorder.stop()

        engine = FeatureEngine(bus, sma_periods=[1], ema_periods=[])
        engine.start()
        received: list = []
        bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

        try:
            await _publish_candle(bus, ticker, day2 + timedelta(minutes=1), 150.0)  # this engine's first LIVE candle
            await asyncio.sleep(0.2)

            assert len(received) == 1
            features = received[0].payload["features"]
            # regular_open backfilled as 108.0 (day2's persisted FIRST
            # candle), not 150.0 (the only candle this engine instance
            # directly observed live).
            assert features["gap_pct"] == pytest.approx(8.0)     # (108-100)/100 * 100
            assert features["gap_dollars"] == pytest.approx(8.0)
            # Session % Change is unaffected by the backfill — it still
            # tracks the LIVE close.
            assert features["session_pct_change"] == pytest.approx(50.0)  # (150-100)/100 * 100
        finally:
            await engine.stop()
    finally:
        await bus.stop()
        _clean_test_symbol(ticker)


def test_update_atr_freezes_for_the_day_then_recomputes_the_next_day():
    """_update_atr's own once-per-(symbol, ET day) gate, tested directly
    against the shared cache rather than through the async worker loop —
    same "poke internal state directly" convention
    test_daily_levels.py's own restart/reconciliation tests already use.
    No bus start, no event loop needed: `_update_atr` is plain sync and
    touches nothing but `self._daily_candle_cache`/`self._atr_state`."""
    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    ticker = "__TEST_FE_ATR_FREEZE__"

    day1_candles = [_daily_bar(100.0 + i, 101.0 + i, 99.0 + i, 100.0 + i) for i in range(15)]
    engine._daily_candle_cache[ticker] = day1_candles

    day1_ts = _et(2026, 8, 11, 9, 31)
    first = engine._update_atr(ticker, day1_ts)
    assert "atr_14" in first and "atr_14_pct" in first

    # Simulate the shared cache changing LATER the same day (e.g. a
    # Daily Levels re-fetch on some future refresh path) — ATR must NOT
    # pick this up until tomorrow's own reset.
    engine._daily_candle_cache[ticker] = [_daily_bar(1.0, 1.0, 1.0, 1.0) for _ in range(15)]
    same_day_later = engine._update_atr(ticker, day1_ts + timedelta(minutes=1))
    assert same_day_later == first

    day2_ts = _et(2026, 8, 12, 9, 31)
    next_day = engine._update_atr(ticker, day2_ts)
    assert next_day != first  # recomputed fresh from the mutated cache
    assert next_day["atr_14"] == pytest.approx(0.0)  # every mutated bar is flat — TR is 0 everywhere


def test_update_atr_absent_when_shared_cache_not_yet_populated():
    """Fresh symbol, `_maybe_refresh_daily_levels` hasn't run for it yet
    (or ran into one of its own honest-gap cases) — `_daily_candle_cache`
    has nothing for this symbol, so ATR stays honestly absent rather than
    fabricating a value, same convention `atr()` itself already carries."""
    engine = FeatureEngine(EventBus(), sma_periods=[], ema_periods=[])
    assert engine._update_atr("__TEST_FE_ATR_NOCACHE__", _et(2026, 8, 11, 9, 31)) == {}


# --- Tier 3: ATR reuses Daily Levels' shared fetch (confirmed decision #68, D1) --


class _FakeHistoricalProvider:
    """Duck-typed MarketDataProvider stand-in, same shape as
    test_daily_levels.py's own — get_historical() only, counting calls so
    these tests can assert the shared-cache claim directly (ATR must NOT
    cause a second fetch), not just that both features' numbers come out
    right independently."""

    def __init__(self, candles_by_symbol: dict[str, list[CandleClosed]]) -> None:
        self._candles_by_symbol = candles_by_symbol
        self.call_count = 0

    async def get_historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[CandleClosed]:
        self.call_count += 1
        return self._candles_by_symbol.get(symbol, [])


def _daily_candle_ago(days_ago: int, close: float, as_of: datetime) -> CandleClosed:
    ts = as_of - timedelta(days=days_ago)
    return CandleClosed(timeframe="1d", open=close, high=close + 1.0, low=close - 1.0, close=close, volume=1000, candle_ts=ts)


def _clean_atr_symbol(ticker: str) -> None:
    """FK-safe cleanup order — daily_levels_state references symbols.id
    with no ON DELETE CASCADE (same reason test_daily_levels.py's own
    _delete_daily_levels_rows_for exists), so it has to go first."""
    session = SessionLocal()
    try:
        symbol_id = session.execute(text("SELECT id FROM symbols WHERE ticker = :t"), {"t": ticker}).scalar()
        if symbol_id is not None:
            session.execute(text("DELETE FROM daily_levels_state WHERE symbol_id = :sid"), {"sid": symbol_id})
        session.execute(text(f"DELETE FROM candles WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = '{ticker}')"))
        session.execute(text(f"DELETE FROM symbols WHERE ticker = '{ticker}'"))
        session.commit()
    finally:
        session.close()


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
@pytest.mark.asyncio
async def test_atr_reuses_daily_levels_shared_fetch_without_a_second_provider_call():
    """The actual point of decision #68's D1: ATR must read Daily Levels'
    own fetch, not trigger a second one. `fake.call_count == 1` after
    ATR AND Daily Levels both appear in the same FeatureSet is the direct
    proof, not an inference from timing."""
    from app.services import broker_registry

    ticker = "__TESTFEATR1__"
    _clean_atr_symbol(ticker)
    broker_registry.clear_all()

    now = _et(2026, 8, 11, 15, 0)
    # 20 flat-price days: comfortably more than ATR's default period+1
    # (15), and — since every close is identical — well over Daily
    # Levels' own min_distinct_candles (2), so one fetch genuinely feeds
    # both readiness thresholds at once rather than only ATR's.
    fake = _FakeHistoricalProvider({ticker: [_daily_candle_ago(d, 100.0, now) for d in range(1, 21)]})
    broker_registry.set_historical_provider(fake)

    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[], ema_periods=[])
    engine.start()
    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        await _publish_candle(bus, ticker, now, 130.0)
        await asyncio.sleep(0.2)

        assert fake.call_count == 1  # the shared-cache claim, proven directly
        assert len(received) == 1
        features = received[0].payload["features"]
        assert "atr_14" in features
        assert "atr_14_pct" in features
        assert len(received[0].payload["daily_levels"]) >= 1  # same fetch also fed Daily Levels
    finally:
        await engine.stop()
        await bus.stop()
        broker_registry.clear_all()
        _clean_atr_symbol(ticker)


@pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")
@pytest.mark.asyncio
async def test_atr_absent_when_too_few_prior_daily_candles_even_though_daily_levels_populates():
    """Each feature reads the SAME shared cache but has its OWN
    readiness threshold — Daily Levels only needs 2 distinct candles
    (default `daily_levels_min_distinct_candles`) to cluster something,
    while ATR(14) needs 15. 5 candles is enough for the former, nowhere
    near enough for the latter — proves the shared cache doesn't force
    one readiness gate onto both consumers."""
    from app.services import broker_registry

    ticker = "__TESTFEATR2__"
    _clean_atr_symbol(ticker)
    broker_registry.clear_all()

    now = _et(2026, 8, 11, 15, 0)
    fake = _FakeHistoricalProvider({ticker: [_daily_candle_ago(d, 100.0, now) for d in range(1, 6)]})  # only 5
    broker_registry.set_historical_provider(fake)

    bus = EventBus()
    await bus.start()
    engine = FeatureEngine(bus, sma_periods=[], ema_periods=[])
    engine.start()
    received: list = []
    bus.subscribe(EventType.FEATURES_UPDATED, lambda e: received.append(e))

    try:
        await _publish_candle(bus, ticker, now, 130.0)
        await asyncio.sleep(0.2)

        assert len(received) == 1
        features = received[0].payload["features"]
        assert "atr_14" not in features
        assert "atr_14_pct" not in features
        assert len(received[0].payload["daily_levels"]) >= 1  # Daily Levels' own lower bar is met
    finally:
        await engine.stop()
        await bus.stop()
        broker_registry.clear_all()
        _clean_atr_symbol(ticker)
