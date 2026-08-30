"""
LevelInteractionEngine — confirmed decision #46. Turns Feature Engine's raw
level values (currently SMA-9/20/50 — feature_engine/engine.py) into a
stateful read of how price behaves around them: Touch / Holding / Rejected
/ Conquered, with a per-touch counter, dwell time, and distance, per the
concept discussed and pinned down across several rounds before any of this
was written.

Generic by construction, not SMA-specific: subscribes to FeaturesUpdated
and runs the identical state machine against EVERY key in
`FeatureSet.features` (currently sma_9/sma_20/sma_50). When Feature Engine
starts publishing ema_9, vwap, or pivot levels under their own keys later,
this engine picks them up with zero code changes — the "reusable unit"
requirement from the original discussion, delivered by not hardcoding a
level type at all rather than by building a plugin system for one.
Matches trading-intelligence-architecture.md §7's "design it around a
question it answers" — the question here is "how is price behaving around
a key level," not "how is price behaving around the SMA."

Daily Levels (confirmed decision #64, Stage 3 of daily-levels-design.md)
is the one deliberate exception to "zero code changes for a new level
type," flagged as such back in decisions #59/#61 before it was ever
built: `daily_levels` is a separate LIST field on `FeatureSet`, not
`features` dict entries, so `_process_one` below walks it with its own
small loop. `_process_level` itself needed no changes at all — `level_id`
slots in as `level_key`, `price` as `level_value`, the exact same generic
contract every dict-based level type already satisfies. Everything below
this point (aura, touch/holding/rejected/conquered, gap-through,
cold-start-unknown-origin, daily reset, get_snapshot()'s shape) applies to
Daily Levels identically and required no further changes — only the
iteration surface in `_process_one` grew.

Trigger and precision: candle-close only (confirmed choice — no tick
stream in this setup). This means a "touch" is only ever observed at
whatever granularity CandleClosed actually fires at (currently 1m —
Feature Engine's own current scope), and duration/counters are inherently
quantized to that timeframe's step size, not sub-candle-precise. Confirmed
acceptable — noise is treated as real information here (repeated
entry/re-entry IS a signal, per that discussion), so no debounce/cooldown
logic sits between raw zone transitions and a counted touch.

State model — same in-memory + lazy-DB-backfill shape as FeatureEngine
(feature_engine/engine.py's own module docstring), one level deeper:
per (symbol, timeframe, level_key), an in-memory live state is the fast
path; a DB row (`level_interaction_state`) is only read once, lazily, the
first time this process sees that key — restart survival, same "rebuilt
from persisted history on startup" shape trading-intelligence-
architecture.md §4 specifies for Market State Engine. Unlike FeatureEngine,
this engine's DB WRITES also only happen on an actual zone transition
(touch start / touch resolution / an active touch surviving a day
rollover) — a steady "still below the level, nothing happening" candle
costs nothing beyond an in-memory comparison, keeping DB writes at
touch-rate rather than candle-rate.

Definitions, as discussed and confirmed:
- Aura: a symmetric %-band around the level value (`aura_pct`, default
  0.2%), configurable, applied uniformly to every level_key in this pass —
  a per-level-type override is real, deliberate follow-up work if some
  level types eventually need a different width, not built ahead of that
  need.
- Touch: a `below`/`above` -> `inside_aura` transition. Increments
  `touch_count_today`.
- Holding: `zone == "inside_aura"`, unresolved.
- Rejected: exits back out the SAME side it entered from.
- Conquered: exits out the OPPOSITE side.
- Distance: anchored at `touch_anchor_price` = the level's value at the
  candle where the touch began (confirmed choice — NOT re-anchored to the
  live level each candle, so a drifting SMA during a multi-candle hold
  doesn't get conflated with price actually moving).
- Time: seconds between touch start and resolution, derived from candle
  count x timeframe step — not sub-candle precise, per the trigger note
  above.
- Daily reset: `touch_count_today` resets on an ET trading-day rollover
  (`MarketClock.trading_day()`) — intraday-scoped per the stated focus,
  but the field is explicit rather than hardcoded so a future
  longer-horizon mode can key on a wider window without touching this
  engine.

Two edge cases resolved by explicit judgment call, flagged rather than
silently decided (raised back to the person after building, same as any
other confirmed-decisions.md entry):
- Gap-through: a candle closes `below`, the very next closes `above` (or
  vice versa), with NO candle ever closing `inside_aura` in between. By
  definition this can only be `conquered` (a rejection requires exiting
  the side you entered from, which is impossible in a single jump).
  `entered_ts`/dwell-based fields are set to their empty/zero equivalents
  (`entered_ts=None`, `seconds_in_zone=0`), the touch is still counted,
  and `anchor_price` uses the CURRENT (arrival) candle's level value —
  there's no discrete "touch began" candle to anchor to, since the Aura
  was never actually observed. Tagged `observed_via="gap"` so a consumer
  can tell "ground through the level over several candles" (`"dwell"`)
  apart from "clean break with no test at all" (`"gap"`).
- Cold-start-unknown-origin: this process's very FIRST observation of a
  given (symbol, timeframe, level_key) is already `inside_aura` — with no
  prior zone recorded, there's no known entry side to classify a
  resolution against. That touch is still counted and tracked, but when
  it resolves, `outcome` is left unclassified (`status="unclassified"`,
  `observed_via="cold_start_unknown_origin"`) rather than guessing.

Per-item isolation: same posture as FeatureEngine — EventBus._safe_call
already isolates this engine's subscriber from every other subscriber; on
top of that, each level_key within one FeaturesUpdated is processed inside
its own try/except, so one bad level (or a transient DB hiccup) can't
block the other configured periods for the same symbol, or the next
event behind it in this engine's own queue.

SMA/EMA slope-family keys (confirmed decision #85) are the SECOND
deliberate exception to "zero code changes, tracks every key" above, and
were flagged as a known gap back in decision #83's own write-up before
being fixed here: `sma_{period}_slope`/`_r2`/`_slope_pct`/`_slope_angle`
and the `ema_` equivalents (indicators/sma.py::sma_slope(),
indicators/ema.py::ema_slope(), confirmed decision #83) are a $/bar rate,
a 0-1 fit-quality score, a %/bar rate, and a degree angle — none of them
a price, so running touch/holding/rejected/conquered tracking against
`close` for any of them (which `_process_one` did unconditionally before
this fix) produced meaningless classifications and wrote real, persisted
`level_interaction_state`/`level_interaction_events` rows for numbers
that were never a level to begin with. Excluded via a small, explicit
suffix check (`_is_sma_ema_slope_key`, untouched by decision #86 below,
still checked first) — deliberately NOT, at the time, a general-purpose
allowlist covering every current/future Feature Engine key: `atr_14`,
`gap_pct`, `rvol`, `regression_*`, `kama_*_slope`, and friends had the
identical problem (flagged explicitly in decision #83's own write-up),
but fixing all of them at once was a broader architectural decision
still being worked out elsewhere at the time, out of scope for that
narrow, SMA/EMA-slope-specific fix.

Confirmed decision #86 closes that broader gap — every remaining
non-price-coordinate key ATR, Gap, Session Change, RVOL, Premarket
Volume Ratio, session volume totals, Regression, and KAMA publish is now
excluded too (`_is_excluded_from_level_tracking`, which checks
`_is_sma_ema_slope_key` first, then the newer families — the #85
mechanism EXTENDED, not replaced or duplicated). `sma_{period}`/
`ema_{period}` themselves, and every other genuine price-scale reference
this engine tracks (`vwap`/`vwap_ext`, `pdc`/`pdh`/`pdl`/`pmh`/`pml`,
`vpoc`, Camarilla's nine pivots, `kama_{period}` itself, and — the one
deliberately-argued call in #86 — `regression_{period}_value`, on the
grounds that it's structurally the same kind of key as `kama_{period}`
(a fitted price-scale reference line with its own separate `deviation`/
`dist` delta feature sitting right next to it, not a delta or ratio
itself) — remain unchanged, still tracked exactly as before. See decision
#86's own write-up for the full per-family reasoning, including why ATR
is the one family excluded WHOLESALE (its base value is a magnitude —
typical daily RANGE — never a price coordinate at all, unlike KAMA/
Regression's own base values).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.market_clock import get_market_clock
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus, get_event_bus
from app.event_bus.events import make_envelope
from app.models.market_data import Symbol
from app.models.trading_intelligence import LevelInteractionEvent, LevelInteractionState
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.level_interaction import LevelInteractionChanged

logger = logging.getLogger(__name__)

_EPSILON = 1e-9  # tiny relative tolerance at the Aura's exact edge — see classify_zone()

# Poison-pill used by stop() to drive a graceful worker-loop exit instead
# of cancelling the task outright — see stop()'s own docstring (confirmed
# decision #84) for the real, reproduced shutdown race this replaces. A
# private object identity, never a plain value, so it can never collide
# with a real FeaturesUpdated-derived queue item (always a dict).
_STOP_SENTINEL = object()


@dataclass
class _LiveState:
    zone: str  # below | inside_aura | above
    zone_entered_ts: datetime
    trading_day: date
    touch_count_today: int
    touch_anchor_price: float | None = None
    touch_entered_ts: datetime | None = None
    touch_entered_from: str | None = None  # below | above | None (cold-start)


# Confirmed decision #85 — see the module docstring's own paragraph on
# SMA/EMA slope-family exclusion for the full reasoning. Endswith-checked
# (an exact suffix match), same as `_parse_slope_key`'s identical check in
# `api/routes/intelligence.py` — the two are deliberately NOT the same
# shared constant/function despite checking the same four suffixes: this
# one only needs a yes/no exclusion decision, that one needs to extract
# which of the four fields a key is, and importing across an
# API-routes-module <-> trading-intelligence-module boundary for four
# short strings would be a worse coupling than the small duplication.
_SMA_EMA_SLOPE_KEY_SUFFIXES = ("_slope", "_r2", "_slope_pct", "_slope_angle")


def _is_sma_ema_slope_key(level_key: str) -> bool:
    """
    True for "sma_9_slope", "ema_20_r2", "sma_50_slope_pct",
    "ema_9_slope_angle", etc. False for "sma_9"/"ema_20" themselves
    (real levels, still tracked), and false for every OTHER family that
    happens to share a "_slope"/"_r2" suffix (`regression_9_slope`,
    `kama_9_slope`, ...) — scoped to `sma_`/`ema_` only, on direct
    instruction (decision #85), even though those other families share
    the identical underlying problem (flagged, not fixed, in decision
    #83's own write-up).
    """
    if not (level_key.startswith("sma_") or level_key.startswith("ema_")):
        return False
    return level_key.endswith(_SMA_EMA_SLOPE_KEY_SUFFIXES)


# Confirmed decision #86 — closes the broader gap decisions #83 and #85
# both flagged and deferred ("atr_14, gap_pct, rvol, regression_*,
# kama_*_slope, and friends have the identical problem... a broader
# architectural decision still being worked out elsewhere"). Every key
# below is a ratio, percentage, magnitude, efficiency score, or price
# DELTA — never a coordinate on the same scale as `close` — audited
# directly against indicators/atr.py, gap.py, session_change.py,
# rvol.py, regression.py, kama.py, and engine.py's own
# premarket_volume_ratio/session_volume/session_volume_ext, not assumed
# from the report that requested this.
#
# Two no-period, single-fixed-name families (Gap, Session Change) plus
# three no-period single keys Feature Engine publishes directly from
# engine.py (RVOL, Premarket Volume Ratio, and the two raw session
# volume totals — share COUNTS, not prices, same reasoning as the
# dollar-delta keys below) — a flat set, no period/suffix parsing needed
# since none of these varies by period.
_EXACT_EXCLUDED_KEYS = frozenset({
    "gap_pct", "gap_dollars",
    "session_pct_change", "session_dollar_change",
    "rvol", "premarket_volume_ratio",
    "session_volume", "session_volume_ext",
})

# prefix -> the excluded suffixes for period-keyed families. An empty
# string "" in a family's tuple means the BARE `{prefix}{period}` key —
# the family's own base value, with no suffix at all — is excluded too,
# not just its derived siblings.
#
# ATR is the one family excluded WHOLESALE (both `""` and `"_pct"`):
# `atr_{period}` itself is a MAGNITUDE — how big a typical daily range
# is — never a price coordinate the way `kama_{period}`/`sma_{period}`/
# `regression_{period}_value` are, so unlike those, there's no "base
# value stays, derived siblings go" split to make here; the whole family
# is off-scale for `close` comparison from the start.
#
# Regression and KAMA both keep their own bare `{prefix}{period}`-shaped
# base value tracked (`kama_{period}` was already explicit about this in
# decision #85's own write-up; `regression_{period}_value` is decision
# #86's own deliberately-argued call — see the module docstring's own
# paragraph on it) — so neither family's tuple includes `""`, only their
# genuinely derived slope/deviation/r2/dist/efficiency byproducts.
_PERIOD_KEYED_EXCLUDED_SUFFIXES: dict[str, tuple[str, ...]] = {
    "atr_": ("", "_pct"),
    "regression_": ("_slope", "_deviation", "_r2", "_slope_norm"),
    "kama_": ("_slope", "_dist", "_dist_pct", "_slope_norm", "_er"),
}


def _is_excluded_from_level_tracking(level_key: str) -> bool:
    """
    True for every non-price-coordinate key Feature Engine currently
    publishes — ATR/ATR%, Gap, Session Change, RVOL, Premarket Volume
    Ratio, the two session volume totals, and Regression/KAMA's own
    derived slope/deviation/r2/dist/efficiency byproducts. False for
    every genuine price-scale reference this engine tracks: `sma_*`,
    `ema_*`, `vwap`/`vwap_ext`, `pdc`/`pdh`/`pdl`/`pmh`/`pml`, `vpoc`,
    Camarilla's nine pivots, `kama_{period}` itself, and
    `regression_{period}_value`.

    Checks `_is_sma_ema_slope_key` (decision #85) FIRST, unmodified —
    this function EXTENDS that decision's exclusion to the remaining
    families, it doesn't replace, duplicate, or re-derive what #85
    already built and tested.

    The period-suffix check mirrors `_is_sma_ema_slope_key`'s own
    endswith-based exact-suffix matching (no ordering dependency between
    a family's suffixes — see that function's own docstring for why),
    generalized to data (`_PERIOD_KEYED_EXCLUDED_SUFFIXES`) since three
    families need the identical shape of check rather than three more
    copies of the same loop.
    """
    if _is_sma_ema_slope_key(level_key):
        return True
    if level_key in _EXACT_EXCLUDED_KEYS:
        return True
    for prefix, suffixes in _PERIOD_KEYED_EXCLUDED_SUFFIXES.items():
        if not level_key.startswith(prefix):
            continue
        for suffix in suffixes:
            if suffix:
                if not level_key.endswith(suffix):
                    continue
                candidate_period = level_key[len(prefix):-len(suffix)]
            else:
                candidate_period = level_key[len(prefix):]
            if candidate_period.isdigit():
                return True
    return False


def classify_zone(close: float, level_value: float, aura_pct: float) -> str:
    """Pure, testable in isolation. `level_value` must be nonzero — a
    level computed from real prices never legitimately is."""
    distance_frac = (close - level_value) / level_value
    if abs(distance_frac) <= aura_pct + _EPSILON:
        return "inside_aura"
    return "above" if distance_frac > 0 else "below"


class LevelInteractionEngine:
    def __init__(self, bus: EventBus, aura_pct: float | None = None) -> None:
        self._bus = bus
        self._aura_pct = aura_pct if aura_pct is not None else get_settings().trading_intelligence_aura_pct

        self._queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()  # object half is _STOP_SENTINEL only
        self._worker_task: asyncio.Task | None = None

        self._state: dict[tuple[str, str, str], _LiveState] = {}
        self._last_applied_ts: dict[tuple[str, str], datetime] = {}  # (symbol, timeframe) -> candle_ts
        # Most recent close and level value seen per key — confirmed
        # decision #47. Needed for get_snapshot()'s "live" reading while a
        # touch is still holding: the persisted/live state (_state) only
        # ever stores anchor_price (fixed at touch START), never a running
        # current price, because nothing in the STATE MACHINE itself needs
        # one — resolution uses the resolving candle's own close directly.
        # A snapshot consumer wants "how far away is it *right now*," which
        # does need a live number, hence this separate cache.
        self._latest_close: dict[tuple[str, str], float] = {}
        self._latest_level_value: dict[tuple[str, str, str], float] = {}

    def start(self) -> None:
        self._bus.subscribe(EventType.FEATURES_UPDATED, self._on_features_updated)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="level-interaction-engine")
        logger.info("LevelInteractionEngine started — aura=%.3f%% on FeaturesUpdated", self._aura_pct * 100)

    async def stop(self) -> None:
        """
        Confirmed decision #84 — replaces a `task.cancel()` + `await task`
        pattern (the same shape decision #47 introduced for CandleRecorder,
        copied here) that LOOKS like it waits for real completion but
        doesn't, for any item still mid-`_process_one` at the moment
        `stop()` is called.

        Root cause, reproduced with a standalone script before touching
        this file (not assumed from reading asyncio's docs): the Task
        here is suspended awaiting the plain `asyncio.Future` that
        `loop.run_in_executor` (what `asyncio.to_thread` calls internally)
        hands back. `Future.cancel()` on that object transitions it to
        CANCELLED synchronously, regardless of whether the real OS thread
        underneath it — the one actually running `_process_one`, including
        its own blocking `_persist_state` writes — has finished. Cancelling
        the wrapped `concurrent.futures.Future` is attempted too, but
        best-effort only: a `concurrent.futures.Future` that's already
        RUNNING can't be cancelled and silently keeps executing. Net
        effect: `task.cancel()` + `await task` can return while a
        `_persist_state` write for this test's own symbol is still
        in-flight, fully detached from anything `stop()`'s caller can see
        or wait on. Measured directly: a `to_thread(slow_fn)` awaiting
        task, cancelled while `slow_fn` had 500ms left to run, had its
        `await task` return in ~0ms — `slow_fn` kept running regardless.

        For an engine whose thread-pool work writes rows keyed on
        `symbol_id`, that orphaned write is exactly what raced a test's
        post-`stop()` `DELETE FROM symbols` into a real, intermittent
        ForeignKeyViolation on teardown — reproduced against this file's
        own `_process_one`/`_persist_state` path (see the new
        `test_stop_waits_for_an_in_flight_persist_before_returning`
        below), not hypothetical. `CandleRecorder.stop()` and
        `FeatureEngine.stop()` share this exact shape and very likely
        have the identical latent bug — flagged, not fixed here (see
        decision #84's own write-up for why this stayed scoped to the
        engine actually implicated by the reported race).

        Fixed with a poison-pill drain, not cancellation: enqueue
        `_STOP_SENTINEL` and await the worker task with nothing cancelled
        at all. Because the queue is FIFO and main.py's shutdown already
        stops the Event Bus before this engine — cutting off any new
        arrivals here — the sentinel is only ever dequeued after every
        item genuinely ahead of it, including one already mid-flight
        inside `to_thread`, has fully finished (`_persist_state` and all).
        This is what main.py's own shutdown-order comment already
        claimed ("await engine.stop() actually means what it says") —
        true for the queued backlog even before this fix, not for an
        item already running inside `to_thread` at the moment `stop()`
        was called. Now it's true for that case too.

        Trade-off, made deliberately rather than left implicit: if the DB
        is genuinely wedged (not just erroring — actually hanging) during
        shutdown, this can make `stop()` take longer than the old
        cancel-based version did, since there's no timeout-then-cancel
        fallback. Not adding one on purpose — a timeout that falls back
        to cancellation would just reintroduce this exact race at lower
        probability instead of removing it, which defeats the point.
        Correctness over shutdown latency is the right call for a
        DB-writing background worker; a stuck-forever shutdown is a
        starkly worse failure mode than a slightly slower one, and is
        already a symptom of a wedged DB regardless of this engine.
        """
        if self._worker_task is not None and not self._worker_task.done():
            await self._queue.put(_STOP_SENTINEL)
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None

    # --- read-side snapshot (confirmed decision #47) ---------------------------

    def get_snapshot(self, symbol: str | None = None) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        """
        Current live state, for the Feature Engine panel. Pure in-memory
        dict read — no I/O, safe to call directly from an async route
        handler.

        Shape: {symbol: {timeframe: {level_key: {...}}}}. Every entry has
        "zone", "touch_count_today", "trading_day", "seconds_in_zone",
        and "distance_pct" — confirmed decision #49: these last two used
        to only appear while actively holding (inside_aura); a real gap,
        not by design — "how long has price been above the level, and by
        how much" is exactly as meaningful in the steady "above"/"below"
        state as mid-touch, and there was no reason to withhold it there.

        `distance_pct`'s semantics genuinely differ by zone, though, and
        that's deliberate, not an inconsistency:
        - While `inside_aura` (an active touch): anchored at
          `touch_anchor_price` — the level's value when THIS touch began
          — unchanged from before. Answers "how far has price moved
          since the test started," not re-anchored to the live level
          each candle (confirmed decision #46 — a drifting SMA during a
          hold shouldn't be conflated with price actually moving).
        - While `below`/`above` (steady state, not testing the level):
          relative to the CURRENT live level value, not a historical
          anchor. Answers "how far away is it right now" — there's no
          test in progress to anchor to, and the level itself may have
          drifted a long way since this zone was entered.

        `seconds_in_zone` is uniform either way — now - zone_entered_ts,
        computed fresh at call time, so it keeps counting up between
        candle closes rather than jumping in whole-timeframe steps.

        `holding` is still present only while `inside_aura`, now
        carrying just the touch-specific extras that don't apply to a
        steady zone: `anchor_price`, `entered_from`, `entered_ts`.
        """
        result: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        now = datetime.now(timezone.utc)
        for (sym, timeframe, level_key), state in self._state.items():
            if symbol is not None and sym != symbol:
                continue

            latest_close = self._latest_close.get((sym, timeframe))
            entry: dict[str, Any] = {
                "zone": state.zone,
                "touch_count_today": state.touch_count_today,
                "trading_day": state.trading_day.isoformat(),
                "seconds_in_zone": int((now - state.zone_entered_ts).total_seconds()),
                "distance_pct": None,
            }

            if state.zone == "inside_aura" and state.touch_anchor_price is not None and state.touch_entered_ts is not None:
                if latest_close is not None:
                    entry["distance_pct"] = round(
                        (latest_close - state.touch_anchor_price) / state.touch_anchor_price * 100, 4
                    )
                entry["holding"] = {
                    "anchor_price": state.touch_anchor_price,
                    "entered_from": state.touch_entered_from,
                    "entered_ts": state.touch_entered_ts.isoformat(),
                }
            else:
                latest_level_value = self._latest_level_value.get((sym, timeframe, level_key))
                if latest_close is not None and latest_level_value:
                    entry["distance_pct"] = round((latest_close - latest_level_value) / latest_level_value * 100, 4)

            result.setdefault(sym, {}).setdefault(timeframe, {})[level_key] = entry
        return result

    # --- Event Bus subscriber (must stay fast) --------------------------------

    def _on_features_updated(self, envelope: EventEnvelope) -> None:
        if envelope.symbol is None:
            return
        self._queue.put_nowait({"symbol": envelope.symbol, **envelope.payload})

    # --- background worker -----------------------------------------------------

    async def _worker_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is _STOP_SENTINEL:
                    # Graceful stop() request (confirmed decision #84) —
                    # not a real FeaturesUpdated payload, nothing to
                    # process. Everything queued AHEAD of this has
                    # already been fully processed by the time we see
                    # it, since this is a plain FIFO queue.
                    self._queue.task_done()
                    break
                try:
                    events = await asyncio.to_thread(self._process_one, item)
                    for symbol, payload in events:
                        await self._bus.publish(
                            make_envelope(EventType.LEVEL_INTERACTION_CHANGED, payload, symbol=symbol)
                        )
                except Exception:  # noqa: BLE001 — one bad symbol/event must not stall the rest
                    logger.exception("LevelInteractionEngine failed to process features for %s", item.get("symbol"))
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    # --- computation (runs off-loop via asyncio.to_thread) ----------------------

    def _process_one(self, item: dict[str, Any]) -> list[tuple[str, LevelInteractionChanged]]:
        symbol = item["symbol"]
        timeframe = item["timeframe"]
        close = float(item["close"])
        features: dict[str, float] = item["features"]

        candle_ts = item["candle_ts"]
        if isinstance(candle_ts, str):
            candle_ts = datetime.fromisoformat(candle_ts)
        if candle_ts.tzinfo is None:
            candle_ts = candle_ts.replace(tzinfo=timezone.utc)

        tf_key = (symbol, timeframe)
        last_ts = self._last_applied_ts.get(tf_key)
        if last_ts is not None and candle_ts <= last_ts:
            logger.warning(
                "LevelInteractionEngine dropped a duplicate/out-of-order FeaturesUpdated for %s %s at %s (last: %s)",
                symbol, timeframe, candle_ts, last_ts,
            )
            return []
        self._last_applied_ts[tf_key] = candle_ts

        results: list[tuple[str, LevelInteractionChanged]] = []
        for level_key, level_value in features.items():
            if _is_excluded_from_level_tracking(level_key):
                # Confirmed decisions #85/#86 — not a price coordinate,
                # never tracked as a level. See module docstring /
                # _is_excluded_from_level_tracking's own docstring for
                # the full reasoning.
                continue
            try:
                event = self._process_level(symbol, timeframe, level_key, float(level_value), close, candle_ts)
                if event is not None:
                    results.append((symbol, event))
            except Exception:  # noqa: BLE001 — one bad level must not block the others in this same candle
                logger.exception(
                    "LevelInteractionEngine failed on %s %s %s at %s", symbol, timeframe, level_key, candle_ts
                )

        # Daily Levels (Stage 3, confirmed decision #64) — a separate
        # `daily_levels` LIST field on FeatureSet, not `features` dict
        # entries (decision #59's own schema choice), so it needs its own
        # loop here rather than fitting into features.items() above.
        # Reuses _process_level completely unmodified: `level_id` is the
        # level_key, `price` is the level_value — the exact same generic
        # contract every other level type already satisfies through the
        # dict above. This is the first (and, per decision #59/#61's own
        # framing, deliberately the ONLY) place this engine's "zero code
        # changes for a new level type" property doesn't hold — recorded
        # as an acknowledged exception, not something that happened
        # quietly in a diff.
        for daily_level in item.get("daily_levels", []):
            level_key = daily_level.get("level_id")
            try:
                event = self._process_level(symbol, timeframe, level_key, float(daily_level["price"]), close, candle_ts)
                if event is not None:
                    results.append((symbol, event))
            except Exception:  # noqa: BLE001 — same per-level isolation as the features.items() loop above
                logger.exception(
                    "LevelInteractionEngine failed on Daily Level %s for %s %s at %s", level_key, symbol, timeframe, candle_ts
                )
        return results

    def _process_level(
        self, symbol: str, timeframe: str, level_key: str, level_value: float, close: float, candle_ts: datetime
    ) -> LevelInteractionChanged | None:
        key = (symbol, timeframe, level_key)
        trading_day = get_market_clock().trading_day(candle_ts)
        new_zone = classify_zone(close, level_value, self._aura_pct)

        # Updated on EVERY candle regardless of whether a transition
        # happens — see the cache fields' own docstring in __init__.
        self._latest_close[(symbol, timeframe)] = close
        self._latest_level_value[key] = level_value

        state = self._state.get(key)
        if state is None:
            state = self._load_state_from_db(symbol, timeframe, level_key)
            if state is None:
                # Never seen anywhere, ever — brand new.
                state = _LiveState(zone=new_zone, zone_entered_ts=candle_ts, trading_day=trading_day, touch_count_today=0)
                if new_zone == "inside_aura":
                    # Cold-start-unknown-origin — see module docstring.
                    state.touch_count_today = 1
                    state.touch_anchor_price = level_value
                    state.touch_entered_ts = candle_ts
                    state.touch_entered_from = None
                self._state[key] = state
                self._persist_state(symbol, timeframe, level_key, state)
                return None  # first-ever observation — nothing has resolved yet
            self._state[key] = state

        if trading_day != state.trading_day:
            state.trading_day = trading_day
            state.touch_count_today = 0  # active touch fields (if any) carry forward unchanged across the rollover

        if new_zone == state.zone:
            return None  # no transition — steady state, nothing to persist or emit (see module docstring)

        if new_zone == "inside_aura":
            # New touch begins, from below or above.
            state.touch_count_today += 1
            state.touch_anchor_price = level_value
            state.touch_entered_ts = candle_ts
            state.touch_entered_from = state.zone
            state.zone = "inside_aura"
            state.zone_entered_ts = candle_ts
            self._persist_state(symbol, timeframe, level_key, state)
            return LevelInteractionChanged(
                timeframe=timeframe, level_key=level_key, trading_day=trading_day, status="holding",
                zone="inside_aura", touch_count_today=state.touch_count_today, seconds_in_zone=0,
                distance_pct=0.0, anchor_price=level_value, observed_via=None, candle_ts=candle_ts,
            )

        # new_zone is "below" or "above" — either a normal resolution (was holding) or a gap-through.
        if state.zone == "inside_aura":
            entered_from = state.touch_entered_from
            anchor = state.touch_anchor_price
            entered_ts = state.touch_entered_ts
            seconds_in_zone = int((candle_ts - entered_ts).total_seconds()) if entered_ts is not None else 0
            observed_via = "dwell"
            if entered_from is None:
                status = "unclassified"
                observed_via = "cold_start_unknown_origin"
            elif new_zone == entered_from:
                status = "rejected"
            else:
                status = "conquered"
        else:
            # Gap-through — see module docstring.
            entered_from = state.zone
            anchor = level_value
            entered_ts = None
            seconds_in_zone = 0
            status = "conquered"
            observed_via = "gap"
            state.touch_count_today += 1

        distance_pct = ((close - anchor) / anchor * 100) if anchor else 0.0

        state.zone = new_zone
        state.zone_entered_ts = candle_ts
        state.touch_anchor_price = None
        state.touch_entered_ts = None
        state.touch_entered_from = None
        self._persist_state(symbol, timeframe, level_key, state)
        self._persist_event(
            symbol, timeframe, level_key, trading_day,
            outcome=status if status != "unclassified" else None,
            entered_from=entered_from, exited_to=new_zone, entered_ts=entered_ts, exited_ts=candle_ts,
            seconds_in_zone=seconds_in_zone, anchor_price=anchor, distance_pct=distance_pct,
            observed_via=observed_via,
        )
        return LevelInteractionChanged(
            timeframe=timeframe, level_key=level_key, trading_day=trading_day, status=status,
            zone=new_zone, touch_count_today=state.touch_count_today, seconds_in_zone=seconds_in_zone,
            distance_pct=round(distance_pct, 4), anchor_price=anchor, observed_via=observed_via, candle_ts=candle_ts,
        )

    # --- persistence (sync — runs inside asyncio.to_thread via _process_one) ----

    def _load_state_from_db(self, symbol: str, timeframe: str, level_key: str) -> _LiveState | None:
        session = SessionLocal()
        try:
            row = session.execute(
                select(LevelInteractionState)
                .join(Symbol, Symbol.id == LevelInteractionState.symbol_id)
                .where(
                    Symbol.ticker == symbol,
                    LevelInteractionState.timeframe == timeframe,
                    LevelInteractionState.level_key == level_key,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return _LiveState(
                zone=row.zone,
                zone_entered_ts=row.zone_entered_ts,
                trading_day=row.trading_day,
                touch_count_today=row.touch_count_today,
                touch_anchor_price=float(row.touch_anchor_price) if row.touch_anchor_price is not None else None,
                touch_entered_ts=row.touch_entered_ts,
                touch_entered_from=row.touch_entered_from,
            )
        except Exception:  # noqa: BLE001 — a DB hiccup on cold-start read must not crash startup; treat as brand-new
            logger.exception("LevelInteractionEngine failed to load persisted state for %s %s %s", symbol, timeframe, level_key)
            return None
        finally:
            session.close()

    def _persist_state(self, symbol: str, timeframe: str, level_key: str, state: _LiveState) -> None:
        session = SessionLocal()
        try:
            symbol_id = self._get_or_create_symbol_id(session, symbol)
            existing = session.execute(
                select(LevelInteractionState).where(
                    LevelInteractionState.symbol_id == symbol_id,
                    LevelInteractionState.timeframe == timeframe,
                    LevelInteractionState.level_key == level_key,
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = LevelInteractionState(symbol_id=symbol_id, timeframe=timeframe, level_key=level_key)
                session.add(existing)
            existing.trading_day = state.trading_day
            existing.touch_count_today = state.touch_count_today
            existing.zone = state.zone
            existing.zone_entered_ts = state.zone_entered_ts
            existing.touch_anchor_price = state.touch_anchor_price
            existing.touch_entered_ts = state.touch_entered_ts
            existing.touch_entered_from = state.touch_entered_from
            session.commit()
        except Exception:  # noqa: BLE001 — same soft-fail posture as CandleRecorder: an unreachable DB must not crash this engine
            logger.exception("LevelInteractionEngine failed to persist state for %s %s %s", symbol, timeframe, level_key)
            session.rollback()
        finally:
            session.close()

    def _persist_event(
        self, symbol: str, timeframe: str, level_key: str, trading_day: date, *,
        outcome: str | None, entered_from: str | None, exited_to: str, entered_ts: datetime | None,
        exited_ts: datetime, seconds_in_zone: int, anchor_price: float, distance_pct: float, observed_via: str,
    ) -> None:
        session = SessionLocal()
        try:
            symbol_id = self._get_or_create_symbol_id(session, symbol)
            session.add(
                LevelInteractionEvent(
                    symbol_id=symbol_id, timeframe=timeframe, level_key=level_key, trading_day=trading_day,
                    outcome=outcome, entered_from=entered_from, exited_to=exited_to, entered_ts=entered_ts,
                    exited_ts=exited_ts, seconds_in_zone=seconds_in_zone, anchor_price=anchor_price,
                    distance_pct=distance_pct, observed_via=observed_via,
                )
            )
            session.commit()
        except Exception:  # noqa: BLE001 — same soft-fail posture as CandleRecorder
            logger.exception("LevelInteractionEngine failed to persist event for %s %s %s", symbol, timeframe, level_key)
            session.rollback()
        finally:
            session.close()

    def _get_or_create_symbol_id(self, session, ticker: str) -> int:
        existing = session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one_or_none()
        if existing is not None:
            return existing
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        session.execute(pg_insert(Symbol).values(ticker=ticker).on_conflict_do_nothing(index_elements=["ticker"]))
        session.commit()
        return session.execute(select(Symbol.id).where(Symbol.ticker == ticker)).scalar_one()


_level_interaction_engine: LevelInteractionEngine | None = None


def get_level_interaction_engine(bus: EventBus | None = None, aura_pct: float | None = None) -> LevelInteractionEngine:
    """Lazy singleton, same pattern and same reasoning as
    feature_engine.engine.get_feature_engine() (confirmed decision #47)."""
    global _level_interaction_engine
    if _level_interaction_engine is None:
        _level_interaction_engine = LevelInteractionEngine(bus or get_event_bus(), aura_pct=aura_pct)
    return _level_interaction_engine
