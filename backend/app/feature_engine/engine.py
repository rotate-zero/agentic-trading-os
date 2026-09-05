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

    One window, four indicator families (decisions #52, #67/#68): the deque
    holds raw closes only, never a derived value — SMA, EMA, Regression, and
    KAMA all read from the SAME window, each just slicing the trailing
    portion it actually needs (sma() needs `period`; ema() needs `period *
    seed_multiplier`; regression() needs `period`; kama() needs `er_period +
    slow_period * seed_multiplier`). The window's maxlen is sized to the
    LARGEST of all four families' needs (`self._window_capacity`), not any
    one alone. Regression and KAMA are also the first two families where
    "applies to every timeframe that fires" isn't true — see
    `_apply_close`'s own per-config timeframe check.

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

Previous-day PDH/PDL/PDC + Camarilla, and today's pre-market H/L
(confirmed decision #56) — same symbol-keyed shape as VWAP above, for the
same reason: neither varies by chart timeframe. Genuinely different from
VWAP in one respect, though: PDH/PDL/PDC is a FIXED value for the whole of
today (it describes YESTERDAY), recomputed once per calendar-day rollover,
not accumulated candle-by-candle; pre-market H/L accumulates like VWAP
does, but gated to PRE_MARKET specifically rather than regular session,
and — unlike VWAP, which has no meaning outside the session it's scoped
to — keeps being published (frozen) through the rest of the day once
pre-market ends, since "where did pre-market top out" stays a meaningful
question well after 9:30. All four of these are exactly the kind of
"level" LevelInteractionEngine already tracks generically (confirmed
decision #46) — publishing them under their own feature keys is the
entire integration; that engine needed zero code changes to start
tracking touch/reject/conquer against pdh/pdl/pdc/pmh/pml/cam_* the moment
they appear in FeatureSet.features, the same way it needed none for
ema_9/ema_20/vwap either.

Daily Levels (confirmed decision #59; docs/architecture/daily-levels-
design.md) breaks two of the patterns above, on purpose, not by
accident: it's the first indicator here with a genuine EXTERNAL-PROVIDER
dependency (fetched via broker_registry.get_historical_provider(), the
same provider-agnostic seam GET /market/candles uses — never Polygon by
name), and the first published as a variable-length `daily_levels` list
on FeatureSet rather than more dict[str, float] entries on `features`
(a collection-valued feature is a genuinely different shape from a
scalar one — see schemas/events/features.py::DailyLevel's own
docstring). Computed once per (symbol, ET calendar day), same cadence as
previous-day/premarket above, but via a SEPARATE async refresh
(_maybe_refresh_daily_levels, called from _worker_loop before the
thread-offloaded _compute_one) rather than folded into
_update_previous_day/_update_premarket — those two do their own
synchronous DB reads directly inside the already-thread-offloaded
_compute_one; MarketDataProvider.get_historical() is itself async and
needs a real event loop to await it with. STAGE 1 ONLY: this build
computes and publishes daily_levels but mints a fresh level_id every
day rather than reconciling identity against yesterday's survivors
(design doc §4) — that reconciliation, and the persistence table it
needs, is Stage 2, not built yet.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.broker_adapters.base import HistoricalDataUnavailableError, SymbolNotFoundError
from app.core.config import get_settings
from app.core.market_clock import Session, get_market_clock
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus, get_event_bus
from app.event_bus.events import make_envelope
from app.feature_engine.indicators import (
    ClusteredLevel,
    DailyCandlePoint,
    aggregate_day,
    atr,
    camarilla_pivots,
    cluster_daily_levels,
    ema,
    ema_slope,
    fold_range,
    gap,
    kama,
    regression,
    rvol,
    session_change,
    sma,
    sma_slope,
    typical_price,
    volume_point_of_control,
    vwap_from_accumulator,
)
from app.models.daily_levels import DailyLevelState
from app.models.market_data import Symbol
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.features import DailyLevel, FeatureSet
from app.services import broker_registry, candle_aggregator, candle_store

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

# Pre-market VWAP/Extended VWAP (docs/architecture/premarket-accumulator-design.md
# §1) — "in play" for the extended accumulator means pre-market through
# the regular-session close, deliberately excluding AFTER_HOURS (§4.2 of
# that doc: thin, illiquid volume that would distort the reference more
# than help it — easy to widen later if that default turns out wrong).
_EXTENDED_HOURS_LABELS = {Session.PRE_MARKET, Session.OPEN, Session.LUNCH, Session.POWER_HOUR}


class FeatureEngine:
    def __init__(
        self,
        bus: EventBus,
        sma_periods: list[int] | None = None,
        ema_periods: list[int] | None = None,
        ema_seed_multiplier: int | None = None,
        aggregated_lookback_days: int | None = None,
        regression_configs: list[dict] | None = None,
        kama_configs: list[dict] | None = None,
        kama_seed_multiplier: int | None = None,
    ) -> None:
        self._bus = bus
        self._sma_periods = sma_periods if sma_periods is not None else get_settings().feature_engine_sma_periods
        self._ema_periods = ema_periods if ema_periods is not None else get_settings().feature_engine_ema_periods
        self._ema_seed_multiplier = (
            ema_seed_multiplier
            if ema_seed_multiplier is not None
            else get_settings().feature_engine_ema_seed_multiplier
        )
        # Regression / KAMA (confirmed decisions #67/#68,
        # docs/architecture/feature-engine-indicator-expansion.md §4) —
        # UNLIKE Daily Levels/ATR's settings-only precedent, these DO take
        # constructor overrides (same reasoning as sma_periods/ema_periods
        # above): tests need small custom periods, not the real 9/30-bar
        # defaults, to stay fast and hand-verifiable. `_regression_configs`
        # is a list of {"timeframe": str, "period": int}; `_kama_configs`
        # is a list of {"timeframe": str, "er_period": int, "fast_period":
        # int, "slow_period": int} — validated here (fail fast on a
        # malformed setting) rather than by config.py itself, matching
        # that file's own "keep config.py minimal, validate at the point
        # of use" precedent (see its own comment on these two settings).
        self._regression_configs = (
            regression_configs if regression_configs is not None else get_settings().feature_engine_regression_configs
        )
        for cfg in self._regression_configs:
            if not cfg.get("timeframe") or int(cfg.get("period", 0)) < 2:
                raise ValueError(f"invalid feature_engine_regression_configs entry: {cfg!r}")
        self._kama_configs = kama_configs if kama_configs is not None else get_settings().feature_engine_kama_configs
        for cfg in self._kama_configs:
            if (
                not cfg.get("timeframe")
                or int(cfg.get("er_period", 0)) < 1
                or int(cfg.get("fast_period", 0)) < 1
                or int(cfg.get("slow_period", 0)) < 1
            ):
                raise ValueError(f"invalid feature_engine_kama_configs entry: {cfg!r}")
        self._kama_seed_multiplier = (
            kama_seed_multiplier if kama_seed_multiplier is not None else get_settings().feature_engine_kama_seed_multiplier
        )
        # How many raw closes the rolling window needs to hold — the
        # LARGEST of all six indicator families' needs (confirmed
        # decisions #52, #67/#68, #83): SMA's own max period; EMA's max
        # period times its seed multiplier; SMA-slope's own
        # `2*period-1` (indicators/sma.py::sma_slope's docstring); EMA-
        # slope's own `period*seed_multiplier + period - 1`
        # (indicators/ema.py::ema_slope's docstring); Regression's max
        # configured period; KAMA's max `er_period + slow_period *
        # seed_multiplier` (its own docstring has the full "why the
        # +er_period" reasoning). One shared window per (symbol,
        # timeframe) backs ALL SIX families — each reads only the
        # trailing slice it actually needs
        # (sma()/ema()/sma_slope()/ema_slope()/regression()/kama() all do
        # this internally). A real, accepted cost: 15m/1h windows now
        # also carry this much history even though Regression/KAMA are
        # never evaluated for those timeframes (see _apply_close's own
        # per-timeframe applicability check below) — the design doc's own
        # §7 already anticipated and accepted this tradeoff rather than
        # it being an oversight. At this system's shipped defaults,
        # sma_slope_max/ema_slope_max both stay under kama_max (99 and
        # 119 vs. 159), so decision #83 did not actually grow
        # `_window_capacity` in practice — both terms are still computed
        # explicitly below rather than assumed dominated, so a future
        # change to sma_periods/ema_periods/kama_configs can't silently
        # under-size the window.
        sma_max = max(self._sma_periods) if self._sma_periods else 0
        ema_max = max(self._ema_periods) * self._ema_seed_multiplier if self._ema_periods else 0
        sma_slope_max = max((2 * p - 1 for p in self._sma_periods), default=0)
        ema_slope_max = max(
            (p * self._ema_seed_multiplier + p - 1 for p in self._ema_periods), default=0
        )
        regression_max = max((cfg["period"] for cfg in self._regression_configs), default=0)
        kama_max = max(
            (cfg["er_period"] + cfg["slow_period"] * self._kama_seed_multiplier for cfg in self._kama_configs),
            default=0,
        )
        self._window_capacity = max(sma_max, ema_max, sma_slope_max, ema_slope_max, regression_max, kama_max)
        self._lookback_days = (
            aggregated_lookback_days
            if aggregated_lookback_days is not None
            else get_settings().feature_engine_aggregated_lookback_days
        )
        self._previous_day_lookback_days = get_settings().feature_engine_previous_day_lookback_days
        self._daily_levels_lookback_days = get_settings().daily_levels_lookback_days
        self._daily_levels_cluster_pct = get_settings().daily_levels_cluster_pct
        self._daily_levels_min_distinct_candles = get_settings().daily_levels_min_distinct_candles
        # Unused until Stage 2 (decision #63) — decision #60 added this
        # setting ahead of time specifically so this moment wouldn't need
        # a second config round-trip. Now actually read.
        self._daily_levels_identity_match_pct = get_settings().daily_levels_identity_match_pct
        # ATR(1D, 14) (confirmed decisions #67/#68) — settings-only, no
        # constructor override, same precedent as the Daily Levels
        # settings just above (tests that need a different period
        # monkeypatch get_settings(), not a constructor kwarg).
        self._atr_period = get_settings().feature_engine_atr_period
        # Relative Volume (confirmed decision #71) — settings-only, no
        # constructor override, same precedent as _atr_period above.
        self._rvol_lookback_days = get_settings().feature_engine_rvol_lookback_days
        self._premarket_lookback_days = get_settings().feature_engine_premarket_lookback_days

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

        # Extended VWAP accumulator (docs/architecture/premarket-accumulator-design.md)
        # — a SEPARATE, day-spanning sibling of _vwap_state above, not a
        # modification of it: {"for_day": date, "cumulative_pv": float,
        # "cumulative_volume": int}. Resets once per trading day (like
        # _premarket_state below, NOT like _vwap_state's per-regular-session
        # reset) and accumulates across _EXTENDED_HOURS_LABELS without
        # resetting again at the 9:30am regular-session boundary — that
        # continuity through the open is the entire point (§1 of that doc).
        self._vwap_ext_state: dict[str, dict[str, Any]] = {}

        # Pre-market volume ratio's own once-per-(symbol, ET day) baseline
        # cache — {"for_day": date, "avg_premarket_volume": float | None}.
        # NOT the same cache _maybe_refresh_daily_levels populates
        # (self._daily_candle_cache): that one holds 1-DAY bars, useless
        # here since a single daily bar doesn't isolate how much of it
        # was pre-market. This one is built from 1-MINUTE bars filtered
        # to Session.PRE_MARKET specifically — see
        # _maybe_refresh_premarket_baseline below.
        self._premarket_baseline_cache: dict[str, dict[str, Any]] = {}

        # Per-SYMBOL previous-day levels (PDH/PDL/PDC + Camarilla) and
        # today's premarket range — both confirmed decision #56, both
        # symbol-keyed for the same reason VWAP is (see module docstring):
        # neither varies by chart timeframe, so there's exactly one value
        # per symbol, attached to every timeframe's FeatureSet alike.
        # {"for_day": date, "values": dict[str, float] | None} —
        # `values` is None when there's no previous trading day in the
        # configured lookback window yet (a fresh symbol/deployment).
        self._previous_day_state: dict[str, dict[str, Any]] = {}
        # {"for_day": date, "high": float | None, "low": float | None} —
        # high/low are None until the first premarket bar of `for_day`
        # has actually been seen (backfilled or live).
        self._premarket_state: dict[str, dict[str, Any]] = {}

        # Per-SYMBOL Daily Levels (confirmed decision #59) —
        # {"for_day": date, "levels": list[DailyLevel]}. Same symbol-keyed
        # shape as VWAP/previous-day/premarket above, and the same
        # once-per-(symbol, ET day) cache/gate shape as previous-day/
        # premarket, but refreshed in _maybe_refresh_daily_levels() (async
        # worker loop, not _compute_one) since it needs an actual
        # provider network call — see that method's docstring. STAGE 1
        # LIMITATION, flagged rather than hidden: `level_id` values here
        # are freshly minted every day, not yet reconciled against
        # yesterday's survivors (daily-levels-design.md §4 / Stage 2, not
        # built yet) — do not wire LevelInteractionEngine against these
        # ids expecting cross-day stability until Stage 2 lands.
        self._daily_levels_state: dict[str, dict[str, Any]] = {}

        # Per-SYMBOL shared daily-candle cache (confirmed decision #68,
        # D1) — the raw, complete-days-strictly-before-today `1d` candles
        # `_maybe_refresh_daily_levels` already fetches for Daily Levels
        # (decision #59/#60), extracted out of `_daily_levels_state` into
        # its own dict so ATR (below) can read the SAME fetch rather than
        # issuing a second provider call per symbol per day. Populated in
        # the exact same place `_daily_levels_state[symbol]["candles"]`
        # used to be written (see `_maybe_refresh_daily_levels`); read by
        # `get_daily_levels()`'s custom-lookback preview (unchanged
        # behavior, just a different dict) AND by `_update_atr` below. A
        # real, accepted coupling, not an oversight — see this engine's
        # design doc §1 for the reasoning and the one-line reversal to
        # full independence if that ever needs revisiting.
        self._daily_candle_cache: dict[str, list[Any]] = {}

        # Per-SYMBOL ATR (confirmed decisions #67/#68,
        # docs/architecture/feature-engine-indicator-expansion.md §1) —
        # {"for_day": date, "features": dict[str, float]}. Recomputed
        # once per (symbol, ET day) from `self._daily_candle_cache`
        # above, then frozen — no live-capture branch needed the way Gap
        # has one, since this is a pure derived value from an
        # already-fetched cache, not something that waits for a specific
        # live candle to arrive.
        self._atr_state: dict[str, dict[str, Any]] = {}

        # Per-SYMBOL Gap (confirmed decisions #67/#68,
        # docs/architecture/feature-engine-indicator-expansion.md §3) —
        # {"for_day": date, "regular_open": float | None}. `regular_open`
        # is None until today's regular session has actually started
        # (backfilled or live), then frozen for the rest of `for_day` —
        # deliberately NOT reset/refolded on every candle the way
        # premarket high/low is, since Gap is a single captured value,
        # not a running range. Session % Change (session_change.py) needs
        # no state of its own — it's a pure function of `close` (already
        # in hand) and `pdc` (already computed below) — so only Gap gets
        # a state dict here.
        self._gap_state: dict[str, dict[str, Any]] = {}

    def start(self) -> None:
        self._bus.subscribe(EventType.CANDLE_CLOSED, self._on_candle_closed)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="feature-engine-sma")
        logger.info(
            "FeatureEngine started — computing SMA%s and EMA%s on 1m CandleClosed, VWAP during regular session, "
            "Extended VWAP across pre-market + regular session, previous-day PDH/PDL/PDC + Camarilla, pre-market H/L, "
            "plus %s on completed bucket boundaries; Daily Levels clustered once per (symbol, ET day) from up to %s "
            "days of 1D history when a historical provider is connected (decision #59)",
            self._sma_periods, self._ema_periods, list(candle_aggregator.WIDTH_TO_LABEL.values()),
            self._daily_levels_lookback_days,
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
        "features": {level_key: value}, "daily_levels": [{"level_id":
        ..., "price": ..., "strength": ..., "distinct_candle_count":
        ...}, ...]}}} — the last key added by decision #59; empty list
        when nothing's been clustered yet for that symbol (no historical
        provider connected, or genuinely fewer than 2 candles confirming
        any zone), same "empty means not-yet, not zero" meaning the rest
        of this docstring already describes. Only includes symbols/
        timeframes this process has actually computed at least once since
        startup — deliberately not pre-populated for the whole configured
        universe, so an empty/missing entry means "nothing computed yet,"
        not "zero."
        """
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for (sym, timeframe), feature_set in self._latest.items():
            if symbol is not None and sym != symbol:
                continue
            result.setdefault(sym, {})[timeframe] = {
                "candle_ts": feature_set.candle_ts.isoformat(),
                "close": feature_set.close,
                "features": dict(feature_set.features),
                "daily_levels": [level.model_dump() for level in feature_set.daily_levels],
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
                    await self._maybe_refresh_daily_levels(item)
                    await self._maybe_refresh_premarket_baseline(item)
                    results = await asyncio.to_thread(self._compute_one, item)
                    for symbol, payload in results:
                        await self._bus.publish(make_envelope(EventType.FEATURES_UPDATED, payload, symbol=symbol))
                except Exception:  # noqa: BLE001 — one bad symbol/candle must not stall the other ~100
                    logger.exception("FeatureEngine failed to compute features for %s", item.get("symbol"))
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    # --- Daily Levels refresh (confirmed decision #59) -----------------------

    async def _maybe_refresh_daily_levels(self, item: dict[str, Any]) -> None:
        """
        Daily Levels needs up to `daily_levels_lookback_days` of 1D candle
        history — the first Feature Engine indicator with a genuine
        external-provider dependency (market.py's own "1d" routing has
        always gone straight to whichever provider holds the historical
        role, never self-recorded — decision #44). Fetched through
        broker_registry.get_historical_provider() — the SAME
        provider-agnostic seam GET /market/candles already uses — so a
        future IBKR historical-role change needs zero changes here; this
        method never imports or knows about Polygon specifically.

        Lives in the ASYNC worker loop, called before the thread-offloaded
        _compute_one(), not inside it: MarketDataProvider.get_historical()
        is itself `async def` (it already thread-offloads its own
        blocking REST call internally, same as PolygonAdapter.get_historical
        does) — a plain sync function running via asyncio.to_thread has no
        event loop of its own to await it with. _compute_one() only ever
        does a synchronous, already-cached read of the result this method
        stores in `self._daily_levels_state`.

        Gated the same once-per-(symbol, ET day) way as
        _update_previous_day/_update_premarket: a fast, synchronous cache
        check runs on every candle; an actual network fetch only happens
        when today's clustering hasn't run yet for this symbol. A slow
        fetch here delays this worker loop picking up its NEXT queued
        item (this loop is already strictly serial — see module
        docstring) — an accepted, rare, once-per-symbol-per-day cost, not
        a new class of problem.
        """
        symbol = item["symbol"]
        candle_ts = item["candle_ts"]
        if isinstance(candle_ts, str):
            candle_ts = datetime.fromisoformat(candle_ts)
        if candle_ts.tzinfo is None:
            candle_ts = candle_ts.replace(tzinfo=timezone.utc)

        today = get_market_clock().trading_day(candle_ts)
        state = self._daily_levels_state.get(symbol)
        if state is not None and state["for_day"] == today:
            return  # already fresh for today — no I/O, the common case on every candle

        # Restart-survival (Stage 2, confirmed decision #63): a fresh
        # process starts with an EMPTY in-memory cache regardless of
        # whether today's reconciliation already ran in a prior process
        # instance before a restart. Check the persisted table first —
        # one indexed query, no network call — before assuming a
        # provider fetch is needed. A symbol whose reconciliation
        # genuinely hasn't run yet today returns None here (falls
        # through to the normal fetch path below); one that HAS gets its
        # already-persisted, already-reconciled levels back immediately.
        # Known, accepted limitation: this short-circuit doesn't restore
        # the shared RAW candle cache (`self._daily_candle_cache` —
        # confirmed decision #68; only the provider fetch path below
        # populates that), so get_daily_levels()'s custom-lookback
        # preview AND ATR (`_update_atr` below) won't have anything to
        # read for this symbol until the next natural per-day refresh —
        # a minor gap, not a correctness issue for either, and not worth
        # persisting ~360 raw candles per symbol just to close it.
        persisted_today = await asyncio.to_thread(self._load_confirmed_daily_levels_for_today, symbol, today)
        if persisted_today is not None:
            self._daily_levels_state[symbol] = {"for_day": today, "levels": persisted_today}
            return

        provider = broker_registry.get_historical_provider()
        if provider is None:
            # No historical provider connected — an honest gap, same
            # treatment as _update_previous_day finding no prior trading
            # day: leave whatever was last computed in place rather than
            # wiping it to empty on what may be a transient disconnect,
            # but still stamp today's date so this check stays cheap
            # (no-op) for the rest of the day instead of retrying on
            # every single candle.
            if state is None:
                logger.info(
                    "Daily Levels: no historical provider connected for %s yet — levels unavailable until one is",
                    symbol,
                )
                self._daily_levels_state[symbol] = {"for_day": today, "levels": []}
            return

        lookback_start = candle_ts - timedelta(days=self._daily_levels_lookback_days)
        try:
            candles = await provider.get_historical(symbol, "1d", lookback_start, candle_ts)
        except SymbolNotFoundError:
            logger.warning("Daily Levels: %s not resolvable by the historical provider — levels unavailable", symbol)
            self._daily_levels_state[symbol] = {"for_day": today, "levels": []}
            return
        except HistoricalDataUnavailableError as exc:
            logger.warning("Daily Levels: historical provider can't serve 1d data for %s (%s) — levels unavailable", symbol, exc)
            self._daily_levels_state[symbol] = {"for_day": today, "levels": []}
            return
        except Exception:  # noqa: BLE001 — one bad fetch must not stall the worker loop or crash on an unexpected provider error
            logger.exception("Daily Levels: fetching 1d history failed for %s — leaving prior levels in place", symbol)
            return  # deliberately does NOT overwrite state on an unexpected error — stale is better than silently empty

        # Strictly-prior days only — today's 1D bar, if a provider even
        # returns one mid-session, hasn't finished forming and shouldn't
        # contribute a point (same "already fully elapsed" requirement
        # _update_previous_day applies to its own single previous day —
        # same clock.trading_day() comparison that method uses, not a
        # raw timestamp cutoff, so this handles weekends/holidays the
        # same free way that method's own docstring describes).
        clock = get_market_clock()
        prior_candles = sorted(
            (c for c in candles if clock.trading_day(c.candle_ts) < today), key=lambda c: c.candle_ts
        )

        clustered = self._cluster_raw(prior_candles)
        # Reconciliation is DB-backed (Stage 2, decision #63) — genuine
        # I/O, so thread-offloaded from this async method the same
        # discipline as everywhere else in this file that talks to
        # Postgres from FeatureEngine (there wasn't any before Stage 2;
        # LevelInteractionEngine's own persistence — sync SessionLocal
        # calls — already runs safely off-loop because ITS caller is
        # thread-offloaded at a higher level. This one has to do it
        # itself, since it's called directly from the async worker loop.)
        levels = await asyncio.to_thread(self._reconcile_and_persist_daily_levels, symbol, today, clustered)
        # Cache the raw candles in the SHARED cache (confirmed decision
        # #68, D1) — not just the levels themselves, and not nested
        # inside `_daily_levels_state` anymore. Two consumers now:
        # get_daily_levels() below re-clusters from a SLICE of these on
        # demand (e.g. "last 30 days" instead of the configured default)
        # without a second provider fetch, and `_update_atr` reads the
        # trailing `period + 1` entries for Wilder ATR — both without a
        # second network call, since the expensive part (the fetch above)
        # only happens once per (symbol, ET day) regardless of how many
        # features end up reading its result.
        self._daily_candle_cache[symbol] = prior_candles
        self._daily_levels_state[symbol] = {"for_day": today, "levels": levels}

    async def _maybe_refresh_premarket_baseline(self, item: dict[str, Any]) -> None:
        """
        Pre-market volume ratio's baseline (docs/architecture/premarket-accumulator-design.md
        §3, empirically confirmed available on Polygon's free tier by
        Saqib directly before this was written) — average pre-market
        volume over `feature_engine_premarket_lookback_days` prior
        trading days.

        Deliberately NOT reusing `self._daily_candle_cache`
        (`_maybe_refresh_daily_levels` above) even though the fetch
        pattern is nearly identical: that cache holds 1-DAY bars, and a
        single daily bar doesn't tell you how much of that day's volume
        happened specifically between 4:00 and 9:30am. This fetches 1m
        bars over a wide-enough calendar window, then filters to
        `Session.PRE_MARKET` rows on strictly prior trading days and sums
        volume PER DAY — genuinely different granularity, so a genuinely
        separate cache and fetch, same "don't force two different shapes
        of data through one cache" instinct that kept `_vwap_ext_state`
        separate from `_vwap_state` rather than folding one into the
        other.

        Same once-per-(symbol, ET day) gate, same
        broker_registry.get_historical_provider() seam, same
        SymbolNotFoundError/HistoricalDataUnavailableError/generic-Exception
        handling shape as `_maybe_refresh_daily_levels` above — copied
        deliberately for consistency, not because the two could easily
        share a helper (the post-fetch filtering/grouping logic differs
        too much to make that worthwhile).
        """
        symbol = item["symbol"]
        candle_ts = item["candle_ts"]
        if isinstance(candle_ts, str):
            candle_ts = datetime.fromisoformat(candle_ts)
        if candle_ts.tzinfo is None:
            candle_ts = candle_ts.replace(tzinfo=timezone.utc)

        clock = get_market_clock()
        today = clock.trading_day(candle_ts)
        state = self._premarket_baseline_cache.get(symbol)
        if state is not None and state["for_day"] == today:
            return  # already fresh for today — no I/O, the common case on every candle

        provider = broker_registry.get_historical_provider()
        if provider is None:
            if state is None:
                logger.info(
                    "Pre-market volume ratio: no historical provider connected for %s yet — unavailable until one is",
                    symbol,
                )
                self._premarket_baseline_cache[symbol] = {"for_day": today, "avg_premarket_volume": None}
            return

        # Generous calendar-day window (3x the trading-day lookback) so
        # weekends/holidays don't starve this of enough actual TRADING
        # days — same reasoning _maybe_refresh_daily_levels's own
        # lookback_start uses, wider here since a whole day's worth of
        # pre-market bars needs to survive the filter below, not just
        # one bar per day.
        lookback_start = candle_ts - timedelta(days=self._premarket_lookback_days * 3)
        try:
            bars = await provider.get_historical(symbol, "1m", lookback_start, candle_ts)
        except SymbolNotFoundError:
            logger.warning("Pre-market volume ratio: %s not resolvable by the historical provider — unavailable", symbol)
            self._premarket_baseline_cache[symbol] = {"for_day": today, "avg_premarket_volume": None}
            return
        except HistoricalDataUnavailableError as exc:
            logger.warning("Pre-market volume ratio: historical provider can't serve 1m data for %s (%s) — unavailable", symbol, exc)
            self._premarket_baseline_cache[symbol] = {"for_day": today, "avg_premarket_volume": None}
            return
        except Exception:  # noqa: BLE001 — one bad fetch must not stall the worker loop or crash on an unexpected provider error
            logger.exception("Pre-market volume ratio: fetching 1m history failed for %s — leaving prior baseline in place", symbol)
            return  # deliberately does NOT overwrite state on an unexpected error — stale is better than silently empty

        # Strictly-prior trading days only, pre-market rows only — same
        # "already fully elapsed" requirement _maybe_refresh_daily_levels
        # applies to its own prior_candles filter.
        by_day: dict[date, int] = {}
        for bar in bars:
            if clock.trading_day(bar.candle_ts) >= today:
                continue
            if clock.current_session(bar.candle_ts) != Session.PRE_MARKET:
                continue
            day = clock.trading_day(bar.candle_ts)
            by_day[day] = by_day.get(day, 0) + bar.volume

        recent_days = sorted(by_day)[-self._premarket_lookback_days :]
        if len(recent_days) < self._premarket_lookback_days:
            # Honest gap — not enough COMPLETE prior pre-market sessions
            # cached yet, same convention every other feature reading a
            # lookback-based cache already follows (RVOL's own
            # `len(prior_candles) < lookback_days` check, one level up).
            avg_premarket_volume = None
        else:
            avg_premarket_volume = sum(by_day[d] for d in recent_days) / self._premarket_lookback_days

        self._premarket_baseline_cache[symbol] = {"for_day": today, "avg_premarket_volume": avg_premarket_volume}

    def _cluster_raw(self, prior_candles: list[Any]) -> list[ClusteredLevel]:
        """Shared by every caller that needs raw clustered zones before
        any identity gets assigned — _maybe_refresh_daily_levels (the
        cached default, reconciled+persisted below) and get_daily_levels
        (an on-demand custom lookback, minted ephemeral — decision #62's
        own design choice to keep that path ad-hoc, not tracked)."""
        points: list[DailyCandlePoint] = []
        for idx, candle in enumerate(prior_candles):
            points.append(DailyCandlePoint(candle_index=idx, price=candle.open))
            points.append(DailyCandlePoint(candle_index=idx, price=candle.close))

        return cluster_daily_levels(
            points, cluster_pct=self._daily_levels_cluster_pct, min_distinct_candles=self._daily_levels_min_distinct_candles
        )

    def _mint_adhoc_daily_levels(self, symbol: str, clustered: list[ClusteredLevel]) -> list[DailyLevel]:
        """Ephemeral, rank-based ids — NOT persisted, NOT reconciled
        against yesterday's DB state. Two callers, on purpose: (a)
        get_daily_levels()'s custom-lookback preview (decision #62 — kept
        deliberately ad-hoc, not tracked, since it's a "what if" display
        control, not the tracked default), and (b) a soft-fail fallback
        if DB-backed reconciliation itself throws (Daily Levels should
        still show SOMETHING on a transient DB hiccup rather than nothing
        — same posture as every other soft-fail persistence path in this
        codebase). The "-preview-" marker makes it impossible to mistake
        one of these for a genuinely tracked, Stage-3-ready level_id."""
        clustered_sorted = sorted(clustered, key=lambda lvl: lvl.price)
        return [
            DailyLevel(
                level_id=f"{symbol}-DL-preview-{idx + 1}",
                price=lvl.price,
                strength=lvl.strength,
                distinct_candle_count=lvl.distinct_candle_count,
            )
            for idx, lvl in enumerate(clustered_sorted)
        ]

    # --- Daily Levels persistence (sync — always called via asyncio.to_thread) ---

    def _load_confirmed_daily_levels_for_today(self, symbol: str, today: date) -> list[DailyLevel] | None:
        """Restart-survival's DB read (Stage 2, decision #63). Returns
        None when today's reconciliation genuinely hasn't run yet for
        this symbol (the normal per-day case — falls through to a
        provider fetch) — deliberately distinct from an empty list, which
        IS a real, valid "already confirmed today, zero levels currently
        qualify" outcome, not a sentinel for "not checked."

        Known, accepted imprecision: a symbol whose active levels ALL got
        archived today (reconciliation ran, matched nothing, zero rows
        remain active) is indistinguishable here from "hasn't run yet" —
        both read back zero active rows. Falls through to a redundant
        provider re-fetch that reproduces the same (correct) empty
        result — a bounded, one-time-per-restart inefficiency for a rare
        edge case, not a correctness bug, and not worth a second query
        against archived_day to close.
        """
        session = SessionLocal()
        try:
            symbol_id = session.execute(select(Symbol.id).where(Symbol.ticker == symbol)).scalar_one_or_none()
            if symbol_id is None:
                return None  # never persisted anything for this symbol — nothing to restore
            rows = (
                session.execute(
                    select(DailyLevelState)
                    .where(
                        DailyLevelState.symbol_id == symbol_id,
                        DailyLevelState.status == "active",
                        DailyLevelState.last_confirmed_day == today,
                    )
                    .order_by(DailyLevelState.price)
                )
                .scalars()
                .all()
            )
            if not rows:
                return None
            return [
                DailyLevel(level_id=r.level_id, price=float(r.price), strength=r.strength, distinct_candle_count=r.distinct_candle_count)
                for r in rows
            ]
        except Exception:  # noqa: BLE001 — a DB hiccup on this read must not crash startup; treat as brand-new (falls through to a normal fetch)
            logger.exception("Daily Levels: failed to check persisted state for %s — falling back to a fresh fetch", symbol)
            return None
        finally:
            session.close()

    def _reconcile_and_persist_daily_levels(self, symbol: str, today: date, clustered: list[ClusteredLevel]) -> list[DailyLevel]:
        """
        The day-over-day identity reconciliation itself (design doc §4,
        Stage 2, confirmed decision #63) — greedy nearest-price matching
        between today's fresh clusters and yesterday's still-active
        persisted levels, NOT rank-based (design doc §4 explicitly
        rejected rank: a level's rank among others can change day to day
        even when the physical zone hasn't moved).

        Algorithm: build every (today-cluster, active-row) pair whose
        price distance is within daily_levels_identity_match_pct of the
        larger of the two prices, sort ALL such candidate pairs by
        distance ascending, then greedily claim matches — each cluster
        and each row used at most once, closest pairs claimed first. A
        matched cluster carries its row's existing level_id forward (with
        updated price/strength/distinct_candle_count/last_confirmed_day);
        an unmatched cluster mints a brand-new level_id from its own
        fresh row's DB identity. Any active row that matched nothing gets
        archived (status/archived_day set), not deleted — design doc §4's
        own "unmatched survivor is archived, not deleted" language.
        """
        session = SessionLocal()
        try:
            symbol_id = self._get_or_create_symbol_id(session, symbol)
            active_rows = (
                session.execute(
                    select(DailyLevelState).where(DailyLevelState.symbol_id == symbol_id, DailyLevelState.status == "active")
                )
                .scalars()
                .all()
            )

            tolerance_pct = self._daily_levels_identity_match_pct
            candidate_pairs: list[tuple[float, int, int]] = []
            for c_idx, cluster in enumerate(clustered):
                for r_idx, row in enumerate(active_rows):
                    row_price = float(row.price)
                    distance = abs(cluster.price - row_price)
                    tolerance = max(cluster.price, row_price) * tolerance_pct
                    if distance <= tolerance:
                        candidate_pairs.append((distance, c_idx, r_idx))
            candidate_pairs.sort(key=lambda pair: pair[0])

            matched_row_for_cluster: dict[int, Any] = {}
            used_row_indices: set[int] = set()
            for _distance, c_idx, r_idx in candidate_pairs:
                if c_idx in matched_row_for_cluster or r_idx in used_row_indices:
                    continue  # closest pairs already claimed one side of this pair — skip, don't double-assign
                matched_row_for_cluster[c_idx] = active_rows[r_idx]
                used_row_indices.add(r_idx)

            result: list[DailyLevel] = []
            for c_idx, cluster in enumerate(clustered):
                row = matched_row_for_cluster.get(c_idx)
                if row is not None:
                    row.price = cluster.price
                    row.strength = cluster.strength
                    row.distinct_candle_count = cluster.distinct_candle_count
                    row.last_confirmed_day = today
                    level_id = row.level_id
                else:
                    new_row = DailyLevelState(
                        symbol_id=symbol_id,
                        level_id="",  # placeholder — real id derived from this row's own PK right after flush, below
                        price=cluster.price,
                        strength=cluster.strength,
                        distinct_candle_count=cluster.distinct_candle_count,
                        status="active",
                        first_seen_day=today,
                        last_confirmed_day=today,
                    )
                    session.add(new_row)
                    session.flush()  # populate new_row.id (Identity column) without committing yet
                    new_row.level_id = f"{symbol}-DL-{new_row.id}"
                    level_id = new_row.level_id
                result.append(
                    DailyLevel(level_id=level_id, price=cluster.price, strength=cluster.strength, distinct_candle_count=cluster.distinct_candle_count)
                )

            for r_idx, row in enumerate(active_rows):
                if r_idx not in used_row_indices:
                    row.status = "archived"
                    row.archived_day = today

            session.commit()
            result.sort(key=lambda lvl: lvl.price)
            return result
        except Exception:  # noqa: BLE001 — same soft-fail posture as CandleRecorder/LevelInteractionEngine: an unreachable DB must not crash this engine
            logger.exception("Daily Levels: reconciliation/persist failed for %s — falling back to ephemeral ids for this refresh", symbol)
            session.rollback()
            return self._mint_adhoc_daily_levels(symbol, clustered)
        finally:
            session.close()

    def _get_or_create_symbol_id(self, session: Any, ticker: str) -> int:
        """Same pattern as LevelInteractionEngine._get_or_create_symbol_id
        and CandleRecorder._get_or_create_symbol_id — duplicated rather
        than shared, matching how this codebase already has this exact
        helper twice; a third small copy here is consistent with that
        existing choice, not a new one."""
        existing = session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one_or_none()
        if existing is not None:
            return existing
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        session.execute(pg_insert(Symbol).values(ticker=ticker).on_conflict_do_nothing(index_elements=["ticker"]))
        session.commit()
        return session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one()

    def get_daily_levels(self, symbol: str, lookback_days: int | None = None) -> list[DailyLevel]:
        """
        Confirmed decision #62 — backs GET /intelligence/state's optional
        `daily_levels_lookback_days` query param (the frontend's lookback
        selector: 30/60/90/... days instead of the server's configured
        default). Re-clusters from the RAW candles _maybe_refresh_daily_levels
        already cached for this symbol — no new provider call, since the
        expensive part already happened once for today; slicing a cached
        ~360-point list and re-running cluster_daily_levels is cheap
        enough to do on every request that asks for a non-default lookback.

        `lookback_days=None` (or omitted): returns exactly what's already
        cached for today — the pre-computed default, zero extra work,
        same values GET /intelligence/state has always returned.

        A `lookback_days` LARGER than what's actually cached (fewer
        trading days exist than requested — a recent IPO, or a value
        bigger than the server's own configured
        `daily_levels_lookback_days`) is silently clamped to whatever IS
        cached, not an error — same "return what's available" honesty
        the rest of this feature already practices (§2's provider-gap
        handling), not a reason to fail the whole request.
        """
        state = self._daily_levels_state.get(symbol)
        if state is None:
            return []
        if lookback_days is None:
            return state["levels"]

        candles: list[Any] = self._daily_candle_cache.get(symbol, [])
        if not candles:
            # Either a pre-Stage-4.1 cached entry (no provider was
            # connected when it was written) or a restart-survival
            # short-circuit (_maybe_refresh_daily_levels's own docstring —
            # that path restores levels but not the shared raw-candle
            # cache) — nothing to slice from either way; falls back to
            # the tracked default.
            return state["levels"]

        sliced = candles[-lookback_days:] if lookback_days < len(candles) else candles
        return self._mint_adhoc_daily_levels(symbol, self._cluster_raw(sliced))

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
        open_price = float(item["open"])
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

        # VWAP (confirmed decision #53), Extended VWAP
        # (docs/architecture/premarket-accumulator-design.md), previous-day
        # levels + Camarilla, and premarket H/L (both confirmed decision
        # #56) are each computed ONCE per 1m close, not per timeframe —
        # see module docstring for why — and attached to every FeatureSet
        # this same close produces below, 1m and any aggregated ones alike.
        vwap_features = self._update_vwap(symbol, candle_ts, high, low, close, volume)
        extra = dict(vwap_features)
        extra.update(self._update_vwap_ext(symbol, candle_ts, high, low, close, volume))
        extra.update(self._update_previous_day(symbol, candle_ts))
        extra.update(self._update_premarket(symbol, candle_ts, high, low))
        # Session % Change + Gap (confirmed decisions #67/#68,
        # docs/architecture/feature-engine-indicator-expansion.md §2/§3) —
        # both reference `pdc` from _update_previous_day just above, read
        # here off `extra` rather than re-deriving it, same "receive
        # already-known values, don't re-fetch" shape camarilla_pivots
        # uses for high/low/close. Session % Change needs no state of its
        # own (pure function of close + pdc); Gap needs _update_gap for
        # the one-time "today's regular open" capture/freeze.
        pdc_value = extra.get("pdc")
        extra.update(session_change(close, pdc_value))
        extra.update(self._update_gap(symbol, candle_ts, open_price, pdc_value))
        # ATR(1D, 14) + ATR% (confirmed decisions #67/#68,
        # docs/architecture/feature-engine-indicator-expansion.md §1) —
        # reads the SHARED daily-candle cache _maybe_refresh_daily_levels
        # populated earlier in the async worker loop (decision #68, D1),
        # same "pure, already-cached read here" shape the Daily Levels
        # comment just below already uses for its own state.
        extra.update(self._update_atr(symbol, candle_ts))
        # Relative Volume (confirmed decision #71) — reads
        # `session_volume` off `extra` (published by _update_vwap just
        # above, same "receive already-known values" shape session_change/
        # gap already use for `pdc`) and the SAME shared daily-candle
        # cache ATR just above reads, for its own average-daily-volume
        # denominator — zero new provider calls, zero new accumulator.
        extra.update(self._update_rvol(symbol, candle_ts, extra.get("session_volume")))
        # Pre-market volume ratio (docs/architecture/premarket-accumulator-design.md)
        # — reads `session_volume_ext` off `extra` (published by
        # _update_vwap_ext above, same "receive already-known values"
        # shape RVOL just used for `session_volume`) and the SEPARATE
        # pre-market-specific baseline cache (_maybe_refresh_premarket_baseline,
        # called earlier in the async worker loop) — zero new provider
        # calls from THIS thread-offloaded method, same as RVOL.
        extra.update(self._update_premarket_volume_ratio(symbol, candle_ts, extra.get("session_volume_ext")))
        # Daily Levels (confirmed decision #59) — a pure, already-cached
        # read here (see _maybe_refresh_daily_levels, called earlier in
        # the async worker loop, not this thread-offloaded method).
        # Symbol-keyed, attached to every FeatureSet this close produces
        # below, same as vwap/extra above — a list, not a features dict
        # entry, so threaded through as its own parameter.
        daily_levels = self._daily_levels_state.get(symbol, {}).get("levels", [])

        one_min = self._apply_close(
            key, candle_ts, close, extra, daily_levels,
            open_price=open_price, high=high, low=low, volume=volume,
        )
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
                aggregated = self._compute_aggregated(symbol, width, session_start, candle_ts, close, extra, daily_levels)
                if aggregated is not None:
                    results.append((symbol, aggregated))

        return results

    def _apply_close(
        self,
        key: tuple[str, str],
        candle_ts: datetime,
        close: float,
        extra_features: dict[str, float],
        daily_levels: list[DailyLevel] | None = None,
        open_price: float | None = None,
        high: float | None = None,
        low: float | None = None,
        volume: int | None = None,
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
        not wait for the slower indicator.

        `open_price`/`high`/`low`/`volume` (decision #99) are optional —
        the 1m call site in `_compute_one()` passes this candle's real
        OHLCV; `_compute_aggregated()` below deliberately passes none of
        them (see FeatureSet's own docstring for why an aggregated bar's
        OHLC can't just be the last constituent 1m candle's)."""
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
            # Slope/angle (confirmed decision #83) warms up strictly
            # slower than the SMA itself (2*period-1 closes vs. period)
            # — computed unconditionally here regardless of whether
            # `value` was ready; sma_slope() does its own honest-gap
            # check and returns {} until it has enough history.
            features.update(sma_slope(closes, period))
        for period in self._ema_periods:
            value = ema(closes, period, self._ema_seed_multiplier)
            if value is not None:
                features[f"ema_{period}"] = round(value, 6)
            features.update(ema_slope(closes, period, self._ema_seed_multiplier))
        # Regression / KAMA (confirmed decisions #67/#68) — the FIRST
        # indicator families in this engine where "applies to every
        # timeframe that fires" (SMA/EMA's own assumption above) isn't
        # true: both are configured for specific timeframes only (1m+5m
        # by default, not 15m/1h), so each config is checked against
        # THIS call's own `timeframe` before computing anything, rather
        # than computed unconditionally the way SMA/EMA loops are.
        for cfg in self._regression_configs:
            if cfg["timeframe"] != timeframe:
                continue
            features.update(regression(closes, cfg["period"]))
        for cfg in self._kama_configs:
            if cfg["timeframe"] != timeframe:
                continue
            features.update(
                kama(closes, cfg["er_period"], cfg["fast_period"], cfg["slow_period"], self._kama_seed_multiplier)
            )
        features.update(extra_features)

        if not features:
            return None  # warm-up: not enough history yet for ANY configured period

        payload = FeatureSet(
            timeframe=timeframe,
            candle_ts=candle_ts,
            close=close,
            open=open_price,
            high=high,
            low=low,
            volume=volume,
            features=features,
            daily_levels=daily_levels or [],
        )
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
        daily_levels: list[DailyLevel] | None = None,
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

        return self._apply_close(key, bucket_start, close, extra_features, daily_levels)

    def _update_vwap(
        self, symbol: str, candle_ts: datetime, high: float, low: float, close: float, volume: int
    ) -> dict[str, float]:
        """
        See module docstring for why this is symbol-keyed, monotonically
        accumulating, and regular-session-only. Returns {} outside
        regular hours (nothing to publish — matching
        frontend/src/indicators/vwap.ts, which simply doesn't emit a point
        for pre-market/after-hours candles either) or on the pathological
        zero-cumulative-volume case (see vwap_from_accumulator()).

        Also returns `session_volume` (confirmed decision #71) — the SAME
        `state["cumulative_volume"]` this method already tracks for its
        own VWAP calculation, published as its own feature key rather
        than kept purely internal, specifically so `_update_rvol` below
        can read it off the shared `extra` dict (same "receive
        already-known values" shape session_change/gap already use for
        `pdc`) instead of either reaching into this method's private
        `self._vwap_state` directly or standing up a second, redundant
        restart-safe accumulator+backfill-query pair that would just
        duplicate everything below. `session_volume` is a genuinely
        useful standalone value too (today's regular-session cumulative
        volume), not purely a means to RVOL.

        Return type changed from `float | None` to `dict[str, float]`
        (decision #71) to match every other `_update_*` helper in this
        engine (`_update_previous_day`/`_update_premarket`/`_update_gap`/
        `_update_atr` all already return dicts) — this was the one
        holdout, not a new convention invented for RVOL's sake.
        """
        if not get_market_clock().is_regular_session(candle_ts):
            return {}
        bounds = get_market_clock().session_bounds(candle_ts)
        if bounds is None:  # pragma: no cover — is_regular_session() true implies bounds exist
            return {}
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

        result: dict[str, float] = {"session_volume": float(state["cumulative_volume"])}
        vwap_value = vwap_from_accumulator(state["cumulative_pv"], state["cumulative_volume"])
        if vwap_value is not None:
            result["vwap"] = round(vwap_value, 6)
        return result

    def _update_vwap_ext(
        self, symbol: str, candle_ts: datetime, high: float, low: float, close: float, volume: int
    ) -> dict[str, float]:
        """
        Extended VWAP (docs/architecture/premarket-accumulator-design.md
        §1) — same math as `_update_vwap` (`typical_price`,
        `vwap_from_accumulator`, the restart-safe backfill-from-`candle_store`
        shape), deliberately a SEPARATE accumulator and method rather than
        a modification of `_update_vwap` itself: `vwap`/`session_volume`
        stay exactly as they are for every existing consumer (Level
        Interaction Engine, the chart), and this is purely additive.

        The one real structural difference from `_update_vwap`: this
        accumulates across `_EXTENDED_HOURS_LABELS` (pre-market through
        regular-session close) rather than regular session alone, and
        resets once per TRADING DAY (`_update_premarket`'s reset trigger,
        decision #56 — reused, not reinvented) rather than once per
        REGULAR SESSION (`_update_vwap`'s own trigger) — because the
        whole point is NOT resetting again at the 9:30am boundary the way
        `_update_vwap` does.

        Returns {} outside `_EXTENDED_HOURS_LABELS` (after-hours/closed)
        — same "simply isn't published outside its window" behavior
        `_update_vwap` already has, not `_update_premarket`'s
        freeze-and-keep-returning-the-last-value behavior. `vwap_ext` is
        meant to be read the same way `vwap` already is (a live line
        during trading hours), not a frozen end-of-window reference the
        way `pmh`/`pml` are.

        `session_volume_ext` — this accumulator's cumulative volume,
        published the same "alongside its VWAP, not purely internal" way
        `_update_vwap` already publishes `session_volume` — is what a
        pre-market-volume-ratio feature would read from WHILE
        `current_session(candle_ts) == Session.PRE_MARKET` specifically
        (not built in this pass — see that doc's §2/§3 for why: it needs
        a historical pre-market-volume baseline this method has no
        opinion about, gated on a still-open empirical check).
        """
        clock = get_market_clock()
        if clock.current_session(candle_ts) not in _EXTENDED_HOURS_LABELS:
            return {}

        today = clock.trading_day(candle_ts)
        state = self._vwap_ext_state.get(symbol)

        if state is None or state["for_day"] != today:
            # Same 24h-lookback + per-row session classification
            # `_update_premarket` already uses, not `_update_vwap`'s
            # single session_bounds() range — pre-market and regular
            # session have DIFFERENT session_bounds() windows, so there's
            # no single (start, end) tuple spanning both to query against
            # directly the way _update_vwap can for regular session alone.
            lookback_start = candle_ts - timedelta(hours=24)
            rows = candle_store.get_recorded_candles(symbol, "1m", lookback_start, candle_ts - _ONE_MINUTE)
            todays_extended_rows = [
                r for r in rows if clock.trading_day(r.candle_ts) == today and clock.current_session(r.candle_ts) in _EXTENDED_HOURS_LABELS
            ]
            cumulative_pv = sum(typical_price(r.high, r.low, r.close) * r.volume for r in todays_extended_rows)
            cumulative_volume = sum(r.volume for r in todays_extended_rows)
            state = {"for_day": today, "cumulative_pv": cumulative_pv, "cumulative_volume": cumulative_volume}

        state["cumulative_pv"] += typical_price(high, low, close) * volume
        state["cumulative_volume"] += volume
        self._vwap_ext_state[symbol] = state

        result: dict[str, float] = {"session_volume_ext": float(state["cumulative_volume"])}
        vwap_ext_value = vwap_from_accumulator(state["cumulative_pv"], state["cumulative_volume"])
        if vwap_ext_value is not None:
            result["vwap_ext"] = round(vwap_ext_value, 6)
        return result

    def _update_previous_day(self, symbol: str, candle_ts: datetime) -> dict[str, float]:
        """
        PDH/PDL/PDC, the nine Camarilla pivots derived from them, and VPOC
        (confirmed decisions #56, #57) — all computed together from the
        SAME already-fetched rows for the previous trading day. Camarilla
        is a pure function of PDH/PDL/PDC specifically; VPOC needs the
        full row list (a volume-at-price histogram, not just high/low/
        close), but it's the SAME bounded, already-fully-elapsed day's
        worth of rows already sitting in `rows` below — no second DB
        query, no live-growing accumulator. This is what actually resolves
        D5 (feature-engine-chart-migration.md): D5 worried VPOC needed a
        continuously-growing histogram that wouldn't fit this flat
        dict[str, float] shape; the frontend's OWN VPOC is scoped to the
        previous day only ("VPOC (Prev Day)" — types/workspace.ts), the
        exact same bounded dataset everything else here already uses. See
        indicators/vpoc.py's own docstring for the fuller version of this.

        Recomputed once per (symbol, ET calendar day) — cheap to check
        (`state["for_day"] != today`), expensive to actually do (a
        multi-day DB scan), so gated the same way VWAP's own session-reset
        check is. "Previous day" here means the most recent ET calendar
        date STRICTLY BEFORE today that has any persisted 1m rows at all
        within `feature_engine_previous_day_lookback_days` — the same
        definition frontend/src/indicators/sessions.ts's
        getPreviousTradingDayCandles() already uses (the second-most-recent
        DISTINCT date actually present in the data, not "yesterday's
        calendar date" — which would incorrectly assume Monday's previous
        day is Sunday rather than Friday). Weekends and holidays are
        skipped automatically this way, for free: no candles were ever
        recorded on a day the market didn't open, so that date simply
        never appears as a candidate.

        High/low span the WHOLE calendar day — pre-market and after-hours
        included, not just regular session — matching
        previousDayLevels.ts's own choice exactly, not reconsidered here.
        """
        today = get_market_clock().trading_day(candle_ts)
        state = self._previous_day_state.get(symbol)

        if state is None or state["for_day"] != today:
            lookback_start = candle_ts - timedelta(days=self._previous_day_lookback_days)
            rows = candle_store.get_recorded_candles(symbol, "1m", lookback_start, candle_ts - _ONE_MINUTE)
            clock = get_market_clock()
            by_day: dict[Any, list] = {}
            for row in rows:
                by_day.setdefault(clock.trading_day(row.candle_ts), []).append(row)
            distinct_prior_days = sorted(d for d in by_day if d < today)

            values: dict[str, float] | None = None
            if distinct_prior_days:
                previous_day_rows = by_day[distinct_prior_days[-1]]
                aggregated = aggregate_day(previous_day_rows)
                if aggregated is not None:
                    high, low, close = aggregated
                    values = {"pdc": close, "pdh": high, "pdl": low}
                    values.update({f"cam_{k}": v for k, v in camarilla_pivots(high, low, close).items()})
                    vpoc = volume_point_of_control(previous_day_rows)
                    if vpoc is not None:
                        values["vpoc"] = vpoc
            state = {"for_day": today, "values": values}
            self._previous_day_state[symbol] = state

        if state["values"] is None:
            return {}  # no previous trading day within the lookback window yet — an honest gap, not an error
        return {k: round(v, 6) for k, v in state["values"].items()}

    def _update_premarket(self, symbol: str, candle_ts: datetime, high: float, low: float) -> dict[str, float]:
        """
        Today's pre-market High/Low (confirmed decision #56) — grows while
        pre-market is actually forming, then naturally stops changing once
        regular session starts (there are simply no more pre-market bars
        for today to fold in), matching
        frontend/src/indicators/premarketLevels.ts's own docstring: "not a
        fixed level... only meaningful before/during today's regular
        session." The STORED value stays available (frozen) through the
        rest of the day rather than disappearing the moment pre-market
        ends — a person checking this an hour into regular session still
        wants to see where pre-market topped out.

        Reset once per (symbol, ET calendar day), same trigger shape as
        `_update_previous_day` above. On that reset, backfills from
        whatever of TODAY's pre-market rows are ALREADY persisted (a real
        process restart mid-morning shouldn't show "no data" just because
        this process wasn't running for pre-market itself) — classified
        row-by-row via `current_session()` rather than constructing a
        separate "today's pre-market window" query, since a 24h lookback
        plus a per-row session check is simpler and reuses logic
        MarketClock already owns.
        """
        clock = get_market_clock()
        today = clock.trading_day(candle_ts)
        state = self._premarket_state.get(symbol)

        if state is None or state["for_day"] != today:
            lookback_start = candle_ts - timedelta(hours=24)  # generous enough to safely span back to 4:00 ET regardless of DST
            rows = candle_store.get_recorded_candles(symbol, "1m", lookback_start, candle_ts - _ONE_MINUTE)
            pm_rows = [r for r in rows if clock.trading_day(r.candle_ts) == today and clock.current_session(r.candle_ts) == Session.PRE_MARKET]
            if pm_rows:
                state = {"for_day": today, "high": max(r.high for r in pm_rows), "low": min(r.low for r in pm_rows)}
            else:
                state = {"for_day": today, "high": None, "low": None}
            self._premarket_state[symbol] = state

        if clock.current_session(candle_ts) == Session.PRE_MARKET:
            # Fold in the CURRENT candle too — deliberately not included in
            # the backfill query above (which stops one minute before it,
            # same race-avoidance shape as VWAP's own backfill), so this
            # is the only place this candle's own high/low get applied.
            state["high"], state["low"] = fold_range(state["high"], state["low"], high, low)

        if state["high"] is None:
            return {}  # pre-market hasn't started yet today, or nothing was recorded for it — an honest gap
        return {"pmh": round(state["high"], 6), "pml": round(state["low"], 6)}

    def _update_gap(self, symbol: str, candle_ts: datetime, open_price: float, pdc: float | None) -> dict[str, float]:
        """
        Gap % / $ (confirmed decisions #67/#68,
        docs/architecture/feature-engine-indicator-expansion.md §3) — the
        traditional opening gap, captured ONCE at today's regular-session
        open and frozen for the rest of the day, deliberately distinct
        from Session % Change's continuous drift (session_change.py, no
        state of its own). Same "established once, frozen" shape as
        pre-market H/L's freeze in _update_premarket just above, but
        triggered at the opposite session boundary: pmh/pml accumulate
        through PRE_MARKET and freeze once regular session starts; Gap
        captures a single value on the FIRST candle where today
        transitions INTO regular session, and never updates again that
        day regardless of how many more regular-session candles follow.

        Reset once per (symbol, ET calendar day), same trigger shape as
        every other daily-reset state in this engine. Restart-safe the
        same way _update_premarket is: on reset, backfills from whatever
        of today's regular-session rows are already persisted (a real
        process restart mid-morning shouldn't lose today's gap just
        because this process wasn't running at 9:30) by taking the
        EARLIEST such row's open — `candle_store.get_recorded_candles`
        already returns chronological order, same assumption
        previous_day.py's aggregate_day makes — rather than only ever
        capturing it live.

        `is_regular_session()` rather than `current_session() ==
        Session.OPEN` specifically: OPEN/LUNCH/POWER_HOUR are one
        continuous domain for anything that cares about session
        BOUNDARIES (MarketClock's own docstring) — this is exactly that
        kind of concern ("has today's regular session begun"), not a
        sub-label concern, even though in practice the first regular-
        session candle of any day will always land within the OPEN
        sub-window specifically.

        The actual gap math (this function receives `pdc` already
        computed by _update_previous_day above rather than re-deriving
        it) lives in indicators/gap.py, same split every other file in
        this package keeps.
        """
        clock = get_market_clock()
        today = clock.trading_day(candle_ts)
        state = self._gap_state.get(symbol)

        if state is None or state["for_day"] != today:
            lookback_start = candle_ts - timedelta(hours=24)
            rows = candle_store.get_recorded_candles(symbol, "1m", lookback_start, candle_ts - _ONE_MINUTE)
            regular_rows = [r for r in rows if clock.trading_day(r.candle_ts) == today and clock.is_regular_session(r.candle_ts)]
            regular_open = regular_rows[0].open if regular_rows else None
            state = {"for_day": today, "regular_open": regular_open}
            self._gap_state[symbol] = state

        if state["regular_open"] is None and clock.is_regular_session(candle_ts):
            # This candle IS the first regular-session candle of today —
            # not included in the backfill query above (stops one minute
            # before it, same race-avoidance shape as VWAP/premarket's own
            # backfill), so this is the only place this candle's own open
            # gets applied. Guarded by `state["regular_open"] is None` so
            # it only ever fires once per day — every later regular-
            # session candle this same run processes leaves it frozen.
            state["regular_open"] = open_price

        return gap(state["regular_open"], pdc)

    def _update_atr(self, symbol: str, candle_ts: datetime) -> dict[str, float]:
        """
        ATR(1D, `self._atr_period`) + ATR% (confirmed decisions #67/#68,
        docs/architecture/feature-engine-indicator-expansion.md §1) —
        Wilder's classic Average True Range over the last
        `self._atr_period` COMPLETE daily bars, strictly before today,
        recomputed once per `(symbol, ET day)` and frozen for the rest of
        the day. Same "no accidental look-ahead" rule
        `_update_previous_day` already established for PDH/PDL/PDC: only
        a trading day strictly before today ever contributes, and today's
        still-forming daily bar never does — inherited for free here
        rather than re-implemented, since `_maybe_refresh_daily_levels`'s
        own "Strictly-prior days only" filtering already guarantees the
        candles this method reads satisfy it.

        Reads from `self._daily_candle_cache`, NOT its own fetch — that
        cache is populated once per (symbol, ET day) by
        `_maybe_refresh_daily_levels`, the async, I/O-doing method that
        already runs BEFORE `_compute_one` in the worker loop (see
        `_worker_loop`: `await self._maybe_refresh_daily_levels(item)`
        always precedes `await asyncio.to_thread(self._compute_one,
        item)` for the same queued item, strictly serially) — the SAME
        daily-candle fetch Daily Levels itself uses (decision #68, D1),
        avoiding a second provider call per symbol per day. Purely
        synchronous here, no I/O of its own, same "async fetches, sync
        computes" split `_update_previous_day`/`_update_premarket`/
        `_update_gap` all already keep.

        A real, accepted coupling, not an oversight: if
        `_maybe_refresh_daily_levels` hasn't populated the shared cache
        yet today (no historical provider connected, a restart-survival
        short-circuit that skips the raw-candle cache, a fetch error —
        see that method's own docstring for each), ATR is honestly absent
        too — `atr()` itself returns {} on too few candles, same "empty
        means not-yet, not zero" convention this engine uses throughout —
        in exactly the same situations Daily Levels itself would also be
        affected, rather than a new, independent failure mode.
        """
        today = get_market_clock().trading_day(candle_ts)
        state = self._atr_state.get(symbol)
        if state is not None and state["for_day"] == today:
            return state["features"]

        prior_candles = self._daily_candle_cache.get(symbol, [])
        features = atr(prior_candles, self._atr_period)
        self._atr_state[symbol] = {"for_day": today, "features": features}
        return features

    def _update_rvol(self, symbol: str, candle_ts: datetime, session_volume: float | None) -> dict[str, float]:
        """
        Relative Volume (confirmed decision #71) — how busy today's
        regular session has been so far, relative to a NORMAL day by this
        same point in time. Not part of the original five-family design
        brief; added separately per direct request. No state of its own:
        every input is either already computed elsewhere this same close
        (`session_volume`, from `_update_vwap`) or read fresh from
        already-cached data (`self._daily_candle_cache`, the SAME shared
        cache ATR reads — decision #68's D1 precedent extended to a
        second consumer) — nothing here needs its own once-per-day
        freeze/reset gate the way Gap or ATR do.

        `session_volume` being None means _update_vwap returned {} for
        this candle (outside regular session — see its own docstring) —
        RVOL is honestly absent there too, same scoping VWAP itself
        already uses, not a new restriction invented for RVOL.

        Average daily volume: the mean of the last `self._rvol_lookback_days`
        COMPLETE prior daily volumes in the shared cache — same "strictly
        before today" rule ATR/PDC already established, inherited for
        free rather than re-implemented (the cache is already filtered
        that way by `_maybe_refresh_daily_levels`). Absent (too few prior
        days cached yet) → RVOL is honestly absent, same convention as
        every other feature reading this cache.

        Elapsed time: `get_market_clock().minutes_since_open(candle_ts)`,
        floored at 1 rather than left at its natural 0 for the very FIRST
        regular-session candle of the day — a deliberate judgment call,
        not an oversight: `minutes_since_open` measures against the
        bar's OPEN timestamp, so the literal first candle reports 0
        elapsed minutes even though real market time has been passing
        while it formed. Flooring at 1 avoids a division by zero AND
        avoids omitting RVOL specifically for what's often the most
        information-dense candle of the day (unusually heavy volume in
        the first minute against a normal day's pace is a genuinely
        meaningful signal, not noise worth suppressing) — see
        indicators/rvol.py's own docstring for why this is a proxy
        formula, not full time-of-day-profile normalization.
        """
        if session_volume is None:
            return {}

        prior_candles = self._daily_candle_cache.get(symbol, [])
        if len(prior_candles) < self._rvol_lookback_days:
            return {}
        recent = prior_candles[-self._rvol_lookback_days :]
        avg_daily_volume = sum(c.volume for c in recent) / self._rvol_lookback_days

        clock = get_market_clock()
        bounds = clock.session_bounds(candle_ts)
        if bounds is None:  # pragma: no cover — session_volume not None implies regular session, implies bounds exist
            return {}
        session_start, session_end = bounds
        total_session_minutes = int((session_end - session_start).total_seconds() // 60)
        elapsed_minutes = max(1, clock.minutes_since_open(candle_ts))

        return rvol(session_volume, avg_daily_volume, elapsed_minutes, total_session_minutes)

    def _update_premarket_volume_ratio(
        self, symbol: str, candle_ts: datetime, session_volume_ext: float | None
    ) -> dict[str, float]:
        """
        Pre-market volume ratio (docs/architecture/premarket-accumulator-design.md
        §2) — reuses `indicators/rvol.py`'s `rvol()` function VERBATIM,
        just against a pre-market-specific baseline instead of
        `avg_daily_volume` — the same "is right now unusually busy
        relative to a normal day by this same point" question RVOL
        already answers, asked about the pre-market window instead of
        the regular session. The output key is renamed from `rvol`'s own
        `"rvol"` to `"premarket_volume_ratio"` before merging into
        `extra` — the two are DIFFERENT numbers (different baseline,
        different window) and must never collide on one key.

        Only ever published `while candle_ts is still inside
        Session.PRE_MARKET` — this is deliberately an ORB-screening
        signal, not something meant to keep updating (or stay frozen)
        once regular session starts; unlike `vwap_ext`, there's no
        continuation story here, so this simply doesn't exist outside
        its one window.

        `session_volume_ext` being None means `_update_vwap_ext`
        returned {} for this candle (same reasoning `_update_rvol`
        already applies to `session_volume`/`_update_vwap`) — honestly
        absent, not the same thing as "not currently in the pre-market
        gate below" even though both produce {} here.

        §4's open question about linear time-of-day normalization being
        a weaker fit for pre-market than for regular session applies
        here unchanged — this reuses `rvol()`'s exact linear assumption
        as the deliberate starting point, not a claim that it's the
        right long-term model.
        """
        clock = get_market_clock()
        if clock.current_session(candle_ts) != Session.PRE_MARKET:
            return {}
        if session_volume_ext is None:
            return {}

        baseline = self._premarket_baseline_cache.get(symbol)
        if baseline is None or baseline["avg_premarket_volume"] is None:
            return {}
        avg_premarket_volume = baseline["avg_premarket_volume"]

        bounds = clock.session_bounds(candle_ts)
        if bounds is None:  # pragma: no cover — Session.PRE_MARKET check above implies bounds exist
            return {}
        session_start, session_end = bounds
        total_premarket_minutes = int((session_end - session_start).total_seconds() // 60)
        elapsed_minutes = max(1, int((candle_ts - session_start).total_seconds() // 60))

        result = rvol(session_volume_ext, avg_premarket_volume, elapsed_minutes, total_premarket_minutes)
        if not result:
            return {}
        return {"premarket_volume_ratio": result["rvol"]}


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
