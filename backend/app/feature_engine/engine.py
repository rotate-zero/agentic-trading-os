"""
FeatureEngine — Phase 4 kickoff. Computes SMA and EMA (decision #52) from
persisted/live 1m candles and publishes them as FeaturesUpdated, extending
Feature Engine from the frontend-only, view-only concept it's been through
Phase 3 (confirmed decisions #40/#41; frontend/src/indicators/sma.ts stays
exactly what it was — a chart overlay nothing downstream reads) into the
one canonical backend feature source Strategy/Decision Engine, and first
up the planned Level Interaction Engine, are meant to read (system-design.md
§1's "single canonical Feature Engine path" non-goal boundary; principle 8,
"compute once, consume everywhere").

Scoped to 1m as the only EVENT-DRIVEN trigger: CandleClosed only ever
fires for 1m (TickIngestBridge's fixed bucket size — see
candle_recorder.py's own docstring). 5m/15m/1h SMA is now also computed
(confirmed decision #51) but only as a DERIVED side effect of that same 1m
event — never its own independent trigger — the moment a 1m close happens
to complete a higher-timeframe bucket. A CandleClosed for any timeframe
other than 1m is still ignored, not an error; there's no such thing as a
directly-published 5m CandleClosed for this engine to receive.

State model — in-memory rolling window per (symbol, timeframe), not a DB
read on every candle:
    Considered computing SMA by re-querying candle_store on every single
    CandleClosed (mirroring CandleRecorder's read-side counterpart). Two
    problems with that: (1) it's a DB round trip on every close across
    ~100 symbols for no reason once warmed up, and (2) it creates a real
    ordering race against CandleRecorder — both subscribe to the SAME
    CandleClosed event, each decoupled via its own internal queue, so
    nothing guarantees CandleRecorder's write for the CURRENT candle lands
    before FeatureEngine would try to read it back.
    Instead: each symbol keeps a bounded in-memory deque of recent closes.
    The current candle's close is already known synchronously from the
    event payload itself — no DB dependency for it at all. A DB read
    (candle_store.get_recent_closes) only ever happens once per symbol,
    lazily, the first time this process sees that symbol after startup —
    backfilling *prior* closes, which by construction already finished
    closing (and had a full candle-interval to be persisted) before now.
    This is the same "has memory, rebuilt from persisted history on
    startup" shape already decided for Market State Engine
    (trading-intelligence-architecture.md §4), applied here.

    One window, two indicator families (decision #52): the deque holds raw
    closes only, never a derived value — SMA and EMA both read from the
    SAME window, each just slicing the trailing portion it actually needs
    (sma() needs `period`; ema() needs `period * seed_multiplier`, always
    >= SMA's own requirement at the periods this project actually
    configures). The window's maxlen is sized to the LARGER of the two
    families' needs (`self._window_capacity`), not SMA's alone.

Same subscribe-fast / process-slow split as CandleRecorder: the Event Bus
subscriber callback only enqueues (near-instant, no I/O); a background task
drains the queue and does the (occasional, cold-start-only) blocking DB
read via asyncio.to_thread, then computes and publishes on the loop. A
slow/blocked computation here must never delay CandleClosed fan-out to
other subscribers on the same lane.

Per-symbol isolation: EventBus._safe_call already stops one subscriber's
exception from affecting other subscribers or crashing the bus (event_bus/
bus.py). Within this engine's own loop, each item's failure (bad candle,
DB hiccup, insufficient history) is additionally caught and logged
individually, so one bad symbol/candle can't stall the other ~100 behind
it in this engine's own queue.

Duplicate / out-of-order guard: this engine has no DB unique constraint to
lean on the way CandleRecorder does (confirmed decision #42) — a repeated
or out-of-order CandleClosed for a candle_ts already applied to a symbol's
window is dropped rather than silently double-counted into the SMA.

Aggregated timeframes — 5m/15m/1h (confirmed decision #51, Stage 2 of
docs/architecture/feature-engine-chart-migration.md): every 1m CandleClosed
is also checked against each aggregated width via
candle_aggregator.completes_bucket() — the SAME bucket-boundary formula
candle_aggregator.py already uses for GET /market/candles, reused rather
than re-derived, so the two can't quietly disagree about where a bucket
starts. This is "Option A2" from that doc: event-triggered, not polled,
but delegating the actual bar construction to already-tested aggregation
logic rather than maintaining a second parallel accumulator.

Race avoided, not just noted: a higher-timeframe bucket's CLOSE — the only
field FeaturesUpdated actually carries alongside the SMA(s) (see
schemas/events/features.py's own docstring: close-only, no full OHLC) —
is, by definition, this same 1m candle's own close, already in hand from
the event payload. No DB read happens at bucket-completion time at all, so
there's no ordering race against CandleRecorder to avoid in the first
place. candle_aggregator.aggregate_from_recorded() IS used, but only for
cold-start backfill of PRIOR bars — fully elapsed, long-since-persisted,
no race there, only history.

VWAP (confirmed decision #53) is a genuinely different shape from
SMA/EMA, not a third variation on the same rolling-window pattern:
- Keyed by SYMBOL alone, not (symbol, timeframe) — VWAP is a session-level
  statistic, the same number regardless of which chart timeframe someone's
  looking at (real trading platforms don't show a different VWAP on a 5m
  chart vs. a 15m chart of the same session). Computing it separately per
  timeframe from that timeframe's own coarser bars would produce slightly
  different numbers for the "same" value depending on timeframe — exactly
  the kind of divergence this whole migration exists to eliminate.
  Computed once, from 1m bars only (the finest granularity this engine
  ever directly receives), and the SAME current value is attached to
  every FeatureSet published on that 1m close — the 1m one and any
  aggregated ones that happen to complete on it.
- A monotonically GROWING accumulator (cumulative price*volume,
  cumulative volume) reset at the regular session's start, not a bounded
  sliding window — there's no "oldest value to drop" the way SMA/EMA have.
  Only ever added to within a session, never subtracted from, so there's
  no equivalent of SMA's subtract-oldest/add-newest drift concern to
  design around.
- Only accumulates during REGULAR session (MarketClock.is_regular_session())
  — matching frontend/src/indicators/vwap.ts's own convention exactly.
  Pre-market and after-hours volume never contributes, unlike aggregated
  SMA/EMA (decision #51), which happily buckets pre-market/after-hours
  candles too, just kept in their own separate buckets.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import get_settings
from app.core.market_clock import get_market_clock
from app.event_bus.bus import EventBus, get_event_bus
from app.event_bus.events import make_envelope
from app.feature_engine.indicators import ema, sma, typical_price, vwap_from_accumulator
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.features import FeatureSet
from app.services import candle_aggregator, candle_store

logger = logging.getLogger(__name__)

# CandleClosed only ever fires for "1m" today (see module docstring) — the
# one timeframe this engine ever RECEIVES an event for.
SUPPORTED_TIMEFRAME = "1m"

# Widths checked for live boundary completion on every 1m close (confirmed
# decision #51). Sourced from candle_aggregator.WIDTH_TO_LABEL rather than
# a hardcoded {5, 15, 60} list, so the two modules can't drift apart on
# what "5m" means.
_AGGREGATED_WIDTHS: list[int] = sorted(candle_aggregator.WIDTH_TO_LABEL)
_ONE_MINUTE = timedelta(minutes=1)


class FeatureEngine:
    def __init__(
        self,
        bus: EventBus,
        sma_periods: list[int] | None = None,
        ema_periods: list[int] | None = None,
        ema_seed_multiplier: int | None = None,
        aggregated_lookback_days: int | None = None,
    ) -> None:
        self._bus = bus
        self._sma_periods = sma_periods if sma_periods is not None else get_settings().feature_engine_sma_periods
        self._ema_periods = ema_periods if ema_periods is not None else get_settings().feature_engine_ema_periods
        self._ema_seed_multiplier = (
            ema_seed_multiplier
            if ema_seed_multiplier is not None
            else get_settings().feature_engine_ema_seed_multiplier
        )
        # How many raw closes the rolling window needs to hold — the
        # LARGER of SMA's own max period and EMA's max period times its
        # seed multiplier (confirmed decision #52). One shared window per
        # (symbol, timeframe) backs both indicator families; each reads
        # only the trailing slice it actually needs (sma()/ema() both
        # already do this internally).
        sma_max = max(self._sma_periods) if self._sma_periods else 0
        ema_max = max(self._ema_periods) * self._ema_seed_multiplier if self._ema_periods else 0
        self._window_capacity = max(sma_max, ema_max)
        self._lookback_days = (
            aggregated_lookback_days
            if aggregated_lookback_days is not None
            else get_settings().feature_engine_aggregated_lookback_days
        )

        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

        # Per-(symbol, timeframe) rolling state — see module docstring.
        self._windows: dict[tuple[str, str], deque[float]] = {}
        self._last_applied_ts: dict[tuple[str, str], datetime] = {}
        # Most recent successfully-computed FeatureSet per key — confirmed
        # decision #47. Separate from `_windows` (raw closes, always needed
        # for the next SMA computation) because this is purely a read-side
        # cache for get_snapshot(): nothing in the compute path itself
        # reads it back. A plain dict, not bounded/evicted — one entry per
        # (symbol, timeframe) actually seen, capped by the real symbol
        # universe (~100), not by event volume.
        self._latest: dict[tuple[str, str], FeatureSet] = {}

        # Per-SYMBOL (not per-timeframe — see module docstring) VWAP
        # accumulator: {"session_start": datetime, "cumulative_pv": float,
        # "cumulative_volume": int}. Confirmed decision #53.
        self._vwap_state: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        self._bus.subscribe(EventType.CANDLE_CLOSED, self._on_candle_closed)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="feature-engine-sma")
        logger.info(
            "FeatureEngine started — computing SMA%s and EMA%s on 1m CandleClosed, VWAP during regular session, "
            "plus %s on completed bucket boundaries",
            self._sma_periods, self._ema_periods, list(candle_aggregator.WIDTH_TO_LABEL.values()),
        )

    async def stop(self) -> None:
        """Awaits actual task completion, not just schedules cancellation
        — see CandleRecorder.stop()'s docstring for the real bug this
        fixes (confirmed decision #47)."""
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None

    # --- read-side snapshot (confirmed decision #47) ---------------------------

    def get_snapshot(self, symbol: str | None = None) -> dict[str, dict[str, dict[str, Any]]]:
        """
        Current computed state, for the Feature Engine panel (and anything
        else that wants a point-in-time read rather than subscribing to
        the event stream). Pure in-memory dict read — no I/O, safe to call
        directly from an async route handler without asyncio.to_thread.

        Shape: {symbol: {timeframe: {"candle_ts": iso str, "close": float,
        "features": {level_key: value}}}}. Only includes symbols/timeframes
        this process has actually computed at least once since startup —
        deliberately not pre-populated for the whole configured universe,
        so an empty/missing entry means "nothing computed yet," not "zero."
        """
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for (sym, timeframe), feature_set in self._latest.items():
            if symbol is not None and sym != symbol:
                continue
            result.setdefault(sym, {})[timeframe] = {
                "candle_ts": feature_set.candle_ts.isoformat(),
                "close": feature_set.close,
                "features": dict(feature_set.features),
            }
        return result

    # --- Event Bus subscriber (must stay fast — same reasoning as CandleRecorder) ---

    def _on_candle_closed(self, envelope: EventEnvelope) -> None:
        if envelope.symbol is None:
            return  # shouldn't happen — TickIngestBridge always sets symbol — never worth crashing the bus over
        if envelope.payload.get("timeframe") != SUPPORTED_TIMEFRAME:
            return  # only 1m is event-driven today — see module docstring
        self._queue.put_nowait({"symbol": envelope.symbol, **envelope.payload})

    # --- background worker ---------------------------------------------------

    async def _worker_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                try:
                    results = await asyncio.to_thread(self._compute_one, item)
                    for symbol, payload in results:
                        await self._bus.publish(make_envelope(EventType.FEATURES_UPDATED, payload, symbol=symbol))
                except Exception:  # noqa: BLE001 — one bad symbol/candle must not stall the other ~100
                    logger.exception("FeatureEngine failed to compute features for %s", item.get("symbol"))
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    # --- computation (runs off-loop via asyncio.to_thread — see module docstring) ---

    def _compute_one(self, item: dict[str, Any]) -> list[tuple[str, FeatureSet]]:
        """
        Returns a LIST — not an Optional single result — because one 1m
        CandleClosed can now produce up to four FeaturesUpdated publishes
        in the same pass: the 1m one (almost always, once warmed up), plus
        any of 5m/15m/1h whose bucket this candle happens to complete
        (confirmed decision #51). Usually just the 1m entry; occasionally
        two; at an hour boundary, potentially all four at once (a 1h close
        is also always a 15m and 5m close).
        """
        results: list[tuple[str, FeatureSet]] = []

        symbol = item["symbol"]
        timeframe = item["timeframe"]
        high = float(item["high"])
        low = float(item["low"])
        close = float(item["close"])
        volume = item["volume"]
        candle_ts = item["candle_ts"]
        if isinstance(candle_ts, str):
            candle_ts = datetime.fromisoformat(candle_ts)
        if candle_ts.tzinfo is None:
            candle_ts = candle_ts.replace(tzinfo=timezone.utc)

        key = (symbol, timeframe)

        # Duplicate / out-of-order guard — see module docstring.
        last_ts = self._last_applied_ts.get(key)
        if last_ts is not None and candle_ts <= last_ts:
            logger.warning(
                "FeatureEngine dropped a duplicate/out-of-order CandleClosed for %s %s at %s (last applied: %s)",
                symbol, timeframe, candle_ts, last_ts,
            )
            return results

        # VWAP (confirmed decision #53) is computed ONCE per 1m close, not
        # per timeframe — see module docstring for why — and attached to
        # every FeatureSet this same close produces below, 1m and any
        # aggregated ones alike.
        vwap_value = self._update_vwap(symbol, candle_ts, high, low, close, volume)
        extra = {"vwap": round(vwap_value, 6)} if vwap_value is not None else {}

        one_min = self._apply_close(key, candle_ts, close, extra)
        if one_min is not None:
            results.append((symbol, one_min))

        # Aggregated timeframes are a derived side effect of THIS 1m close
        # only — never processed for a candle this engine didn't itself
        # just accept above (an early return above means none of this
        # runs, same as the un-migrated behavior).
        bounds = get_market_clock().session_bounds(candle_ts)
        if bounds is not None:
            session_start, _session_end = bounds
            for width in _AGGREGATED_WIDTHS:
                if not candle_aggregator.completes_bucket(candle_ts, session_start, width):
                    continue
                aggregated = self._compute_aggregated(symbol, width, session_start, candle_ts, close, extra)
                if aggregated is not None:
                    results.append((symbol, aggregated))

        return results

    def _apply_close(
        self, key: tuple[str, str], candle_ts: datetime, close: float, extra_features: dict[str, float]
    ) -> FeatureSet | None:
        """The original 1m computation, unchanged in behavior aside from
        `extra_features` — pulled out of _compute_one() so
        _compute_aggregated() below can reuse the exact same "seed window,
        append, compute SMA(s)/EMA(s), publish only if warmed up" shape
        for 5m/15m/1h instead of a parallel copy of it. `extra_features`
        (VWAP, confirmed decision #53) is merged in AFTER the SMA/EMA
        computation but BEFORE the "nothing ready yet" warm-up check —
        VWAP warms up far faster than a 9-period SMA (one bar of nonzero
        volume vs. nine bars of history), so a symbol whose SMA/EMA are
        still warming up should still publish once VWAP alone is ready,
        not wait for the slower indicator."""
        symbol, timeframe = key
        window = self._windows.get(key)
        if window is None:
            # First time this process has seen this symbol+timeframe —
            # backfill prior closes from persisted history (strictly
            # BEFORE this candle_ts, which by definition already finished
            # closing earlier and had time to be recorded — no race with
            # CandleRecorder's write of THIS candle).
            window = deque(maxlen=self._window_capacity)
            if self._window_capacity > 1:
                prior = candle_store.get_recent_closes(
                    symbol, timeframe, before=candle_ts, limit=self._window_capacity - 1, strict_before=True
                )
                window.extend(prior)
            self._windows[key] = window

        window.append(close)
        self._last_applied_ts[key] = candle_ts

        features: dict[str, float] = {}
        closes = list(window)
        for period in self._sma_periods:
            value = sma(closes, period)
            if value is not None:
                features[f"sma_{period}"] = round(value, 6)
        for period in self._ema_periods:
            value = ema(closes, period, self._ema_seed_multiplier)
            if value is not None:
                features[f"ema_{period}"] = round(value, 6)
        features.update(extra_features)

        if not features:
            return None  # warm-up: not enough history yet for ANY configured period

        payload = FeatureSet(timeframe=timeframe, candle_ts=candle_ts, close=close, features=features)
        self._latest[key] = payload
        return payload

    def _compute_aggregated(
        self,
        symbol: str,
        width: int,
        session_start: datetime,
        candle_ts: datetime,
        close: float,
        extra_features: dict[str, float],
    ) -> FeatureSet | None:
        """
        The just-completed 5m/15m/1h bucket's close is this same 1m
        candle's own close (see module docstring — no DB read needed to
        know it). What differs from _apply_close()'s 1m path is only HOW
        the window gets cold-start-seeded: candle_store never holds a
        "5m"-labeled row (CandleRecorder only ever writes "1m" —
        candle_aggregator.py's own docstring), so
        candle_store.get_recent_closes() would silently return [] for any
        aggregated timeframe. candle_aggregator.aggregate_from_recorded()
        is used instead, deliberately, for that one piece.
        """
        label = candle_aggregator.WIDTH_TO_LABEL[width]
        bucket_start = candle_aggregator.bucket_start_for(candle_ts, session_start, width)
        key = (symbol, label)

        last_ts = self._last_applied_ts.get(key)
        if last_ts is not None and bucket_start <= last_ts:
            # Defense in depth — the top-level 1m dedup guard already
            # prevents the only known way to reach this twice, but a
            # bucket must never be double-applied to this window either.
            logger.warning(
                "FeatureEngine dropped a duplicate/out-of-order aggregated bucket for %s %s at %s (last applied: %s)",
                symbol, label, bucket_start, last_ts,
            )
            return None

        if key not in self._windows:
            window: deque[float] = deque(maxlen=self._window_capacity)
            if self._window_capacity > 1:
                lookback_start = bucket_start - timedelta(days=self._lookback_days)
                try:
                    prior_bars = candle_aggregator.aggregate_from_recorded(
                        symbol, label, lookback_start, bucket_start - _ONE_MINUTE
                    )
                except ValueError:
                    prior_bars = []
                # Belt-and-suspenders on the upper bound: aggregate_from_recorded
                # always returns strictly-prior bars given the `end` passed
                # above, but the explicit filter keeps this correct even if
                # that upstream contract ever changes without this call site
                # being revisited.
                closes = [b.close for b in prior_bars if b.candle_ts < bucket_start]
                window.extend(closes[-(self._window_capacity - 1):])
            self._windows[key] = window

        return self._apply_close(key, bucket_start, close, extra_features)

    def _update_vwap(
        self, symbol: str, candle_ts: datetime, high: float, low: float, close: float, volume: int
    ) -> float | None:
        """
        See module docstring for why this is symbol-keyed, monotonically
        accumulating, and regular-session-only. Returns None outside
        regular hours (nothing to publish — matching
        frontend/src/indicators/vwap.ts, which simply doesn't emit a point
        for pre-market/after-hours candles either) or on the pathological
        zero-cumulative-volume case (see vwap_from_accumulator()).
        """
        if not get_market_clock().is_regular_session(candle_ts):
            return None
        bounds = get_market_clock().session_bounds(candle_ts)
        if bounds is None:  # pragma: no cover — is_regular_session() true implies bounds exist
            return None
        session_start, _session_end = bounds

        state = self._vwap_state.get(symbol)
        if state is None or state["session_start"] != session_start:
            # First time this process has seen this symbol, or a new
            # regular session has started since the last bar — (re)seed by
            # summing whatever's ALREADY persisted between session_start
            # and this candle (strictly before it — same "already had time
            # to be recorded" reasoning as SMA/EMA's own cold-start
            # backfill). If candle_ts == session_start (the very first bar
            # of the session), this range is naturally empty and the query
            # below just returns [] — no special-casing needed for that.
            #
            # A known-narrower accumulator, not a bug, if CandleRecorder
            # had a gap earlier in the session — same trade-off
            # candle_aggregator.py's own docstring already accepts for its
            # read-side aggregation.
            prior_rows = candle_store.get_recorded_candles(symbol, "1m", session_start, candle_ts - _ONE_MINUTE)
            cumulative_pv = sum(typical_price(r.high, r.low, r.close) * r.volume for r in prior_rows)
            cumulative_volume = sum(r.volume for r in prior_rows)
            state = {"session_start": session_start, "cumulative_pv": cumulative_pv, "cumulative_volume": cumulative_volume}

        state["cumulative_pv"] += typical_price(high, low, close) * volume
        state["cumulative_volume"] += volume
        self._vwap_state[symbol] = state

        return vwap_from_accumulator(state["cumulative_pv"], state["cumulative_volume"])


_feature_engine: FeatureEngine | None = None


def get_feature_engine(bus: EventBus | None = None, sma_periods: list[int] | None = None) -> FeatureEngine:
    """
    Lazy singleton, matching get_event_bus()/get_gateway()'s existing
    pattern (confirmed decision #47) — lets a route handler reach the SAME
    instance main.py's lifespan started, without threading it through
    FastAPI dependency injection. Tests should keep constructing
    FeatureEngine(...) directly, the way they already do, rather than
    going through this — this singleton exists for main.py and routes,
    not to replace direct construction where a test wants its own isolated
    instance.
    """
    global _feature_engine
    if _feature_engine is None:
        _feature_engine = FeatureEngine(bus or get_event_bus(), sma_periods=sma_periods)
    return _feature_engine
