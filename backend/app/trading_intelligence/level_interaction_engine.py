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


@dataclass
class _LiveState:
    zone: str  # below | inside_aura | above
    zone_entered_ts: datetime
    trading_day: date
    touch_count_today: int
    touch_anchor_price: float | None = None
    touch_entered_ts: datetime | None = None
    touch_entered_from: str | None = None  # below | above | None (cold-start)


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

        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
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
