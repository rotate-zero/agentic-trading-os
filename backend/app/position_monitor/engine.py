"""
`PositionMonitor` — decision slug `position-monitor-lite`, extended by
`simulated-eod-monitor-handoff` (decision #185's approved simulated EOD
contract; design doc §6.6). See the package docstring
(`position_monitor/__init__.py`) for the EX-5/EX-11 scoping call.

Same subscribe -> own queue -> worker shape as every other engine in this
codebase (decision #84's pattern). Deliberately no `DebounceScheduler`:
coalescing ticks would risk missing the exact tick/candle that crossed a stop
or target.

Two observation kinds, two independent slots per position
---------------------------------------------------------
* **protective** (`stop` / `target`): event-driven, exactly as before. Ticks
  and candles for held symbols are queued; stop is checked before target, so a
  bar touching both resolves to `"stop"`.
* **eod** (`eod_flatten`): timer-driven only. Candles never label it.

Each slot is `PENDING` when created and `ACKNOWLEDGED` only after the consumer
calls `acknowledge_observation()` following its own durable commit (see
`handoff.py`). An EOD slot never suppresses protective evaluation. A protective
slot suppresses duplicate protective observations and, if it was created first,
EOD creation. Closing the EOD window without a commit is reported with
`release_observation(..., WINDOW_CLOSED)`, which ends only the EOD slot.

Timer pulses
------------
A background task enqueues a `_Pulse` on the SAME queue as market events (a
pulse is coalesced while one is already queued). The single worker processes
every event queued before a pulse before it handles that pulse, so this is
arrival ordering, not global exchange-time ordering. On a pulse, for each open
position whose EOD window (`core.session_window.eod_session_window`, lead
passed explicitly) contains the injected wall clock's `now`, and that has no
slot yet: take the eligible cached tick, check stop/target against it first,
otherwise emit EOD. A position on a covered holiday/weekend has no window; an
unsupported entry year is skipped with a bounded-rate log. Neither stops
event-driven stop/target evaluation.

Tick cache and EOD eligibility
------------------------------
Every valid `PriceUpdated` (aware `exchange_ts`, finite positive price, not
later than wall time at ingest) is offered to a per-symbol cache that keeps
the maximum exchange timestamp; on an equal timestamp the first received tick
wins, and an older tick can never move it backwards. Ticks for symbols that
are not held at arrival are cached in the subscriber callback (before the
held-symbol filter); ticks for held symbols are cached when the worker reaches
them in queue order, so a tick that arrives after a pulse is queued is later
work. Each tick carries an arrival sequence; a pulse ignores a cached tick
whose sequence is newer than its own and retries at the next pulse.

An EOD label needs a tick for the position's symbol with
`position.opened_at <= exchange_ts <= wall now` on the position's entry ET
trading day. There is no additional maximum age. With no such tick no slot is
created; the miss is logged at a bounded rate and retried each pulse. Nothing
is substituted (no entry price, wall time or candle).

Held-symbol filtering mirrors decision #173's Portfolio State worker: one
cheap `get_open_positions()` read in the subscriber callback decides whether to
enqueue, and processing reads it again, fresh.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal
from uuid import UUID

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.market_clock import MarketClock, get_market_clock
from app.core.session_window import UnsupportedEodCalendarError, eod_session_window
from app.event_bus.bus import EventBus
from app.position_monitor.handoff import (
    Observation,
    ObservationKind,
    ObservationState,
    ReleaseReason,
)
from app.position_monitor.ports import PositionReader, PositionView
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.market_data import CandleClosed, PriceUpdated

logger = logging.getLogger(__name__)

_STOP_SENTINEL = object()
_LOG_INTERVAL = timedelta(seconds=60)
_DEFAULT_PULSE_INTERVAL_SECONDS = 1.0  # a scheduling target, not a latency promise


@dataclass(frozen=True)
class ExitIntent:
    """A market observation; Execution owns durable order identity and placement.

    `eod_flatten_at` / `eod_close_at` are UTC and set for `eod_flatten` only
    (both, ordered); they are the placement window the monitor evaluated, not a
    fill deadline. Stop/target intents leave both `None`.
    """

    position_id: UUID
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: int
    exit_reason: Literal["stop", "target", "eod_flatten"]
    trigger_price: float
    trigger_ts: datetime  # the exchange/candle timestamp that produced this intent — never wall-clock
    eod_flatten_at: datetime | None = None
    eod_close_at: datetime | None = None

    def __post_init__(self) -> None:
        bounds = (self.eod_flatten_at, self.eod_close_at)
        if self.exit_reason != "eod_flatten":
            if any(b is not None for b in bounds):
                raise ValueError("eod_flatten_at/eod_close_at are for eod_flatten intents only")
            return
        if any(b is None for b in bounds):
            raise ValueError("eod_flatten intents require eod_flatten_at and eod_close_at")
        for bound in bounds:
            if bound.tzinfo is None or bound.utcoffset() != timedelta(0):  # type: ignore[union-attr]
                raise ValueError("EOD bounds must be timezone-aware UTC")
        if not self.eod_flatten_at < self.eod_close_at:  # type: ignore[operator]
            raise ValueError("eod_flatten_at must be before eod_close_at")


@dataclass(frozen=True)
class _Bar:
    """One evaluatable price observation — either a single tick
    (`high == low == close == price`) or a closed candle."""

    high: float
    low: float
    close: float
    ts: datetime


@dataclass(frozen=True)
class _CachedTick:
    price: float
    ts: datetime
    seq: int  # arrival order; lower wins an equal-timestamp tie


@dataclass(frozen=True)
class _QueuedEvent:
    seq: int
    envelope: EventEnvelope


@dataclass(frozen=True)
class _Pulse:
    seq: int


@dataclass
class _Slot:
    sequence: int
    intent: ExitIntent
    state: ObservationState


def _bar_from_envelope(envelope: EventEnvelope) -> _Bar | None:
    if envelope.event_type == EventType.PRICE_UPDATED:
        tick = PriceUpdated.model_validate(envelope.payload)
        return _Bar(high=tick.price, low=tick.price, close=tick.price, ts=tick.exchange_ts)
    if envelope.event_type == EventType.CANDLE_CLOSED:
        candle = CandleClosed.model_validate(envelope.payload)
        return _Bar(high=candle.high, low=candle.low, close=candle.close, ts=candle.candle_ts)
    return None  # not a market-data event this module cares about


def _stop_touched(position: PositionView, bar: _Bar) -> bool:
    if position.stop is None:
        return False
    if position.side == "BUY":
        return bar.low <= position.stop
    return bar.high >= position.stop


def _target_touched(position: PositionView, bar: _Bar) -> bool:
    if position.target is None:
        return False
    if position.side == "BUY":
        return bar.high >= position.target
    return bar.low <= position.target


def _evaluate(position: PositionView, bar: _Bar) -> ExitIntent | None:
    """Stop/target only. Stop is checked first, so a single bar crossing both
    resolves to `"stop"` (stop-wins-tie, `fill_simulator`'s convention, EX-8).
    EOD is timer-driven and lives in `PositionMonitor._process_pulse`."""
    if _stop_touched(position, bar):
        return ExitIntent(
            position_id=position.position_id,
            symbol=position.symbol,
            side=position.side,
            qty=position.qty,
            exit_reason="stop",
            trigger_price=position.stop,  # type: ignore[arg-type]  # not None — _stop_touched guarantees it
            trigger_ts=bar.ts,
        )
    if _target_touched(position, bar):
        return ExitIntent(
            position_id=position.position_id,
            symbol=position.symbol,
            side=position.side,
            qty=position.qty,
            exit_reason="target",
            trigger_price=position.target,  # type: ignore[arg-type]  # not None — _target_touched guarantees it
            trigger_ts=bar.ts,
        )
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PositionMonitor:
    def __init__(
        self,
        bus: EventBus,
        position_reader: PositionReader,
        *,
        clock: MarketClock | None = None,
        on_exit_intent: Callable[[ExitIntent], None] | None = None,
        on_observation: Callable[[Observation], None] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
        eod_lead_seconds: int | None = None,
        pulse_interval_seconds: float | None = _DEFAULT_PULSE_INTERVAL_SECONDS,
    ) -> None:
        """
        `on_exit_intent`: legacy, fire-and-forget, stop/target ONLY, unchanged
        (today's `main.py` wiring). It is never called for EOD.
        `on_observation`: optional wake-up for BOTH kinds, called once per new
        slot with a `PENDING` `Observation`. It is a hint, not a commit
        signal; the authoritative retry source is `pending_observations()`.
        `wall_clock`: injectable aware-UTC "now" (default real time).
        `eod_lead_seconds`: the configured lead, passed explicitly to
        `eod_session_window`; `None` reads
        `settings.execution_eod_flatten_lead_seconds` once, here.
        `pulse_interval_seconds`: timer period; `None` disables the timer
        (tests and manual `enqueue_pulse()` only).
        """
        if eod_lead_seconds is None:
            eod_lead_seconds = get_settings().execution_eod_flatten_lead_seconds
        if isinstance(eod_lead_seconds, bool) or not isinstance(eod_lead_seconds, int) or not 1 <= eod_lead_seconds <= 900:
            raise ValueError("eod_lead_seconds must be an integer from 1 through 900")
        if pulse_interval_seconds is not None and pulse_interval_seconds <= 0:
            raise ValueError("pulse_interval_seconds must be positive or None")

        self._bus = bus
        self._position_reader = position_reader
        self._clock = clock or get_market_clock()
        self._on_exit_intent = on_exit_intent
        self._on_observation = on_observation
        self._wall_clock = wall_clock or _utc_now
        self._eod_lead_seconds = eod_lead_seconds
        self._pulse_interval = pulse_interval_seconds
        self._queue: asyncio.Queue[_QueuedEvent | _Pulse | object] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._timer_task: asyncio.Task | None = None
        self._accepting = False
        self._seq = 0
        self._pulse_queued = False

        self._tick_cache: dict[str, _CachedTick] = {}
        # Control slots, one per (position, kind). A slot's existence is the
        # dedupe latch; only ACKNOWLEDGED proves a consumer commit.
        self._slots: dict[tuple[UUID, ObservationKind], _Slot] = {}
        self._slot_sequence = 0
        self._closed_positions: set[UUID] = set()
        # After an INVALID EOD release, only a strictly newer tick may retry.
        self._eod_tick_floor: dict[UUID, datetime] = {}
        # Diagnostic record (GET /intelligence/exit-intents): the FIRST intent
        # per position of any kind, as before. Not a control latch.
        self._exit_intents: dict[UUID, ExitIntent] = {}
        self._last_logged: dict[tuple[UUID, str], datetime] = {}

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        self._accepting = True
        self._bus.subscribe(EventType.PRICE_UPDATED, self._on_market_event)
        self._bus.subscribe(EventType.CANDLE_CLOSED, self._on_market_event)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="position-monitor")
        if self._pulse_interval is not None:
            self._timer_task = asyncio.create_task(self._timer_loop(), name="position-monitor-timer")

    async def stop(self) -> None:
        self._accepting = False
        self._bus.unsubscribe(EventType.PRICE_UPDATED, self._on_market_event)
        self._bus.unsubscribe(EventType.CANDLE_CLOSED, self._on_market_event)
        if self._timer_task is not None:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
            self._timer_task = None
        if self._worker_task is not None and not self._worker_task.done():
            await self._queue.put(_STOP_SENTINEL)
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None
        # Nothing is decided after stop(); drop anything still queued.
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
        self._pulse_queued = False

    def enqueue_pulse(self) -> bool:
        """Put one timer pulse on the same queue as market events. Coalesced:
        returns False (and enqueues nothing) while one is already queued or the
        monitor is not accepting work."""
        if not self._accepting or self._pulse_queued:
            return False
        self._pulse_queued = True
        self._queue.put_nowait(_Pulse(seq=self._next_seq()))
        return True

    async def _timer_loop(self) -> None:
        assert self._pulse_interval is not None
        while True:
            await asyncio.sleep(self._pulse_interval)
            self.enqueue_pulse()

    # --- read surfaces ---------------------------------------------------------

    def get_exit_intents(self, symbol: str | None = None) -> tuple[ExitIntent, ...]:
        """Diagnostic, point-in-time read (§4 item 5): the first intent per
        position, any kind. Same shape/semantics as before EOD timing existed,
        so the exit-intents route and its one-row-per-position UI keys are
        unaffected. Use `get_observations()` for both slots."""
        values = self._exit_intents.values()
        if symbol is not None:
            values = (intent for intent in values if intent.symbol == symbol)
        return tuple(values)

    def get_observations(self, symbol: str | None = None) -> tuple[Observation, ...]:
        """Every slot (pending, acknowledged, expired) in creation order."""
        slots = sorted(self._slots.items(), key=lambda item: item[1].sequence)
        return tuple(
            Observation(sequence=slot.sequence, kind=key[1], state=slot.state, intent=slot.intent)
            for key, slot in slots
            if symbol is None or slot.intent.symbol == symbol
        )

    def pending_observations(self) -> tuple[Observation, ...]:
        """Slots not yet proven committed, oldest first. This — not a
        callback — is the retry source after a failed commit or lost ack."""
        return tuple(o for o in self.get_observations() if o.state is ObservationState.PENDING)

    def restore_observation(self, kind: ObservationKind, intent: ExitIntent, state: ObservationState) -> None:
        """Re-arm a committed request before start() subscribes to market events.

        The caller reads the ledger after reconciliation. A restored slot is
        never pending because its durable observation has already committed.
        """
        if self._accepting or state is ObservationState.PENDING:
            raise ValueError("only committed observations may be restored before start")
        key = (intent.position_id, kind)
        if key in self._slots:
            raise ValueError("duplicate restored observation")
        self._slot_sequence += 1
        self._slots[key] = _Slot(self._slot_sequence, intent, state)
        self._exit_intents.setdefault(intent.position_id, intent)

    # --- handoff: consumer -> monitor --------------------------------------------

    def acknowledge_observation(self, position_id: UUID, kind: ObservationKind) -> bool:
        """Call ONLY after the observation's durable commit (or a readback
        proving it). Idempotent: True if the slot is now acknowledged, False
        if there is no such live slot (never created, released, or expired)."""
        slot = self._slots.get((position_id, kind))
        if slot is None or slot.state is ObservationState.EXPIRED:
            return False
        slot.state = ObservationState.ACKNOWLEDGED
        return True

    def release_observation(self, position_id: UUID, kind: ObservationKind, reason: ReleaseReason) -> bool:
        """Hand a slot back without a commit. Returns whether anything changed.

        WINDOW_CLOSED (EOD only): the slot becomes terminal EXPIRED; no new
        EOD for this position; protective slot untouched. A protective slot is
        never window-restricted, so WINDOW_CLOSED on it is refused (False).
        POSITION_CLOSED: the position's slots are discarded and none is
        created again. INVALID: the slot is discarded; a new EOD needs a
        strictly newer tick than the rejected one; a protective slot can
        re-form on the next stop/target event.
        A DB failure or lost ack needs NO release — leave the slot pending."""
        key = (position_id, kind)
        slot = self._slots.get(key)
        if reason is ReleaseReason.POSITION_CLOSED:
            changed = position_id not in self._closed_positions
            self._closed_positions.add(position_id)
            for slot_kind in ("eod", "protective"):
                if self._slots.pop((position_id, slot_kind), None) is not None:
                    changed = True
            return changed
        if slot is None:
            return False
        if reason is ReleaseReason.WINDOW_CLOSED:
            if kind != "eod":
                logger.warning("PositionMonitor: WINDOW_CLOSED refused for protective slot position_id=%s", position_id)
                return False
            if slot.state is ObservationState.ACKNOWLEDGED:
                return False  # committed work is not un-committed by a late expiry report
            slot.state = ObservationState.EXPIRED
            return True
        # INVALID
        if kind == "eod":
            self._eod_tick_floor[position_id] = slot.intent.trigger_ts
        del self._slots[key]
        return True

    # --- EventBus subscriber (must stay cheap — no awaiting here) ---------------

    def _on_market_event(self, envelope: EventEnvelope) -> None:
        if not self._accepting or envelope.symbol is None:
            return
        seq = self._next_seq()
        is_tick = envelope.event_type == EventType.PRICE_UPDATED
        try:
            held_symbols = {p.symbol for p in self._position_reader.get_open_positions()}
        except Exception:  # noqa: BLE001 — e.g. snapshot unavailable; never lose a tick's cache offer
            logger.exception("PositionMonitor: positions unavailable in subscriber; event dropped")
            if is_tick:
                self._cache_tick(envelope, seq)
            return
        if envelope.symbol not in held_symbols:
            if is_tick:
                # Cached before the held-symbol filter drops it, so a position
                # opened just after can still be labelled from this tick.
                self._cache_tick(envelope, seq)
            return  # cheap early drop, rechecked at processing time
        self._queue.put_nowait(_QueuedEvent(seq=seq, envelope=envelope.model_copy(deep=True)))

    # --- worker -------------------------------------------------------------

    async def _worker_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is _STOP_SENTINEL:
                    self._queue.task_done()
                    break
                try:
                    if isinstance(item, _Pulse):
                        self._pulse_queued = False
                        self._process_pulse(item)
                    else:
                        self._process_event(item)  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001 — one bad event must not kill the worker
                    logger.exception("PositionMonitor: unhandled error processing queued item")
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    def _process_event(self, queued: _QueuedEvent) -> None:
        envelope = queued.envelope
        if envelope.event_type == EventType.PRICE_UPDATED:
            self._cache_tick(envelope, queued.seq)  # in queue order for held symbols
        bar = _bar_from_envelope(envelope)
        if bar is None:
            return
        positions = [p for p in self._position_reader.get_open_positions() if p.symbol == envelope.symbol]
        for position in positions:
            if not self._protective_open(position.position_id):
                continue
            intent = _evaluate(position, bar)
            if intent is not None:
                self._register("protective", intent)

    def _process_pulse(self, pulse: _Pulse) -> None:
        now = self._wall_clock()
        if now.tzinfo is None or now.utcoffset() is None:
            logger.error("PositionMonitor: wall clock returned a naive datetime; pulse skipped")
            return
        try:
            positions = self._position_reader.get_open_positions()
        except Exception:  # noqa: BLE001 — e.g. snapshot unavailable; retry next pulse
            logger.exception("PositionMonitor: positions unavailable for pulse")
            return
        for position in positions:
            pid = position.position_id
            if pid in self._closed_positions:
                continue
            if (pid, "eod") in self._slots or (pid, "protective") in self._slots:
                continue  # EOD once per position; a protective slot created first wins
            try:
                window = eod_session_window(self._clock, position.opened_at, self._eod_lead_seconds)
            except UnsupportedEodCalendarError:
                self._log_throttled(pid, "calendar", now, logging.WARNING,
                                    "PositionMonitor: no EOD calendar coverage for position_id=%s; no EOD observation", pid)
                continue
            except ValueError:
                self._log_throttled(pid, "opened_at", now, logging.ERROR,
                                    "PositionMonitor: position_id=%s has an unusable opened_at; no EOD observation", pid)
                continue
            if window is None or not window.contains(now):
                continue
            tick = self._eligible_tick(position, now, pulse.seq)
            if tick is None:
                self._log_throttled(pid, "no_tick", now, logging.WARNING,
                                    "PositionMonitor: no eligible post-opening tick for position_id=%s symbol=%s inside EOD window",
                                    pid, position.symbol)
                continue
            bar = _Bar(high=tick.price, low=tick.price, close=tick.price, ts=tick.ts)
            protective = _evaluate(position, bar)  # stop/target first, then EOD
            if protective is not None:
                self._register("protective", protective)
                continue
            self._register(
                "eod",
                ExitIntent(
                    position_id=pid,
                    symbol=position.symbol,
                    side=position.side,
                    qty=position.qty,
                    exit_reason="eod_flatten",
                    trigger_price=tick.price,
                    trigger_ts=tick.ts,
                    eod_flatten_at=window.flatten_at,
                    eod_close_at=window.close_at,
                ),
            )

    # --- helpers ---------------------------------------------------------------

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _protective_open(self, position_id: UUID) -> bool:
        return position_id not in self._closed_positions and (position_id, "protective") not in self._slots

    def _cache_tick(self, envelope: EventEnvelope, seq: int) -> None:
        assert envelope.symbol is not None
        try:
            tick = PriceUpdated.model_validate(envelope.payload)
        except ValidationError:
            return
        ts = tick.exchange_ts
        if ts.tzinfo is None or ts.utcoffset() is None:
            return
        if not math.isfinite(tick.price) or tick.price <= 0:
            return
        now = self._wall_clock()
        if now.tzinfo is None or ts > now:
            return  # future-stamped ticks can never displace a valid tick
        current = self._tick_cache.get(envelope.symbol)
        if current is None or ts > current.ts or (ts == current.ts and seq < current.seq):
            self._tick_cache[envelope.symbol] = _CachedTick(price=tick.price, ts=ts, seq=seq)

    def _eligible_tick(self, position: PositionView, now: datetime, pulse_seq: int) -> _CachedTick | None:
        tick = self._tick_cache.get(position.symbol)
        if tick is None or tick.seq > pulse_seq:
            return None  # arrived after this pulse was queued: later work
        if not position.opened_at <= tick.ts <= now:
            return None
        if self._clock.trading_day(tick.ts) != self._clock.trading_day(position.opened_at):
            return None
        floor = self._eod_tick_floor.get(position.position_id)
        if floor is not None and tick.ts <= floor:
            return None
        return tick

    def _register(self, kind: ObservationKind, intent: ExitIntent) -> None:
        """Create the pending slot, then notify. The slot is retained whether
        or not any callback succeeds; only acknowledgement advances it."""
        self._slot_sequence += 1
        slot = _Slot(sequence=self._slot_sequence, intent=intent, state=ObservationState.PENDING)
        self._slots[(intent.position_id, kind)] = slot
        self._exit_intents.setdefault(intent.position_id, intent)
        logger.info(
            "PositionMonitor: ExitIntent produced position_id=%s symbol=%s reason=%s trigger_price=%s",
            intent.position_id, intent.symbol, intent.exit_reason, intent.trigger_price,
        )
        if kind == "protective" and self._on_exit_intent is not None:
            try:
                self._on_exit_intent(intent)  # legacy; Execution owns commit/retry. Never EOD.
            except Exception:  # noqa: BLE001
                logger.exception("PositionMonitor: on_exit_intent failed; slot stays pending")
        if self._on_observation is not None:
            try:
                self._on_observation(
                    Observation(sequence=slot.sequence, kind=kind, state=slot.state, intent=intent)
                )
            except Exception:  # noqa: BLE001
                logger.exception("PositionMonitor: on_observation failed; slot stays pending")

    def _log_throttled(self, position_id: UUID, cause: str, now: datetime, level: int, msg: str, *args: object) -> None:
        key = (position_id, cause)
        last = self._last_logged.get(key)
        if last is not None and timedelta(0) <= now - last < _LOG_INTERVAL:
            return
        self._last_logged[key] = now
        logger.log(level, msg, *args)
