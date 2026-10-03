"""
`PositionMonitor` — decision slug `position-monitor-lite`, extended by
`simulated-eod-monitor-handoff` (decision #185's approved simulated EOD
contract; design doc §6.6). See the package docstring
(`position_monitor/__init__.py`) for the EX-5/EX-11 scoping call.

Same subscribe -> own queue -> worker shape as every other engine in this
codebase (decision #84's pattern). Valid ticks enter a bounded arrival-order
journal before any position read. Only replay wake-ups are coalesced; tick
observations themselves are never reduced to extrema.

Two observation kinds, two independent slots per position
---------------------------------------------------------
* **protective** (`stop` / `target`): ticks replay through the worker in
  arrival order; candles are queued for held symbols. Stop is checked before
  target, so one bar touching both resolves to `"stop"`.
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

Tick journal and EOD eligibility
--------------------------------
Every valid `PriceUpdated` (aware `exchange_ts`, finite positive price, not
later than wall time at ingest) is journaled with its arrival sequence before
any snapshot read. The single worker replays only through the sequence bound
of the item it handles. Per-position progress advances after evaluation or
safe observation registration. A separate recovery timer wakes the worker
without requiring a second tick or an EOD pulse.

For EOD, the latest exchange timestamp among ticks within a pulse's arrival
boundary wins (first received wins an equal-timestamp tie). The old diagnostic
tick cache is retained, but pulse eligibility reads the ordered journal.

An EOD label needs a tick for the position's symbol with
`position.opened_at <= exchange_ts <= wall now` on the position's entry ET
trading day. There is no additional maximum age. With no such tick no slot is
created; the miss is logged at a bounded rate and retried each pulse. Nothing
is substituted (no entry price, wall time or candle).

Candles retain the held-symbol subscriber filter. Ticks do not: a ready-flat or
unavailable snapshot cannot discard a first protective touch.
"""
from __future__ import annotations

import asyncio
from collections import deque
import logging
import math
import time
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


@dataclass(frozen=True)
class _Replay:
    seq: int


@dataclass(frozen=True)
class _JournalTick:
    price: float
    ts: datetime
    seq: int
    arrived: float


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
        max_journal_symbols: int = 100,
        max_ticks_per_symbol: int = 2000,
        unowed_retention_seconds: float = 60.0,
        recovery_interval_seconds: float = 0.25,
        monotonic_clock: Callable[[], float] = time.monotonic,
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
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
               for value in (max_journal_symbols, max_ticks_per_symbol)):
            raise ValueError("journal capacities must be positive integers")
        if not math.isfinite(unowed_retention_seconds) or unowed_retention_seconds <= 0:
            raise ValueError("unowed_retention_seconds must be finite and positive")
        if not math.isfinite(recovery_interval_seconds) or recovery_interval_seconds <= 0:
            raise ValueError("recovery_interval_seconds must be finite and positive")

        self._bus = bus
        self._position_reader = position_reader
        self._clock = clock or get_market_clock()
        self._on_exit_intent = on_exit_intent
        self._on_observation = on_observation
        self._wall_clock = wall_clock or _utc_now
        self._eod_lead_seconds = eod_lead_seconds
        self._pulse_interval = pulse_interval_seconds
        self._queue: asyncio.Queue[_QueuedEvent | _Pulse | _Replay | object] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._timer_task: asyncio.Task | None = None
        self._recovery_task: asyncio.Task | None = None
        self._accepting = False
        self._seq = 0
        self._pulse_queued = False
        self._pulse_pending_seq: int | None = None
        self._replay_queued = False
        self._max_symbols = max_journal_symbols
        self._max_ticks = max_ticks_per_symbol
        self._retention = unowed_retention_seconds
        self._recovery_interval = recovery_interval_seconds
        self._monotonic = monotonic_clock
        self._journal: dict[str, deque[_JournalTick]] = {}
        self._progress: dict[UUID, int] = {}
        self._visible: dict[UUID, PositionView] = {}
        self._snapshot_unavailable = False
        self._fill_pending: dict[str, float] = {}
        self._reported_invisible: set[str] = set()
        self._incidents: deque[dict[str, object]] = deque(maxlen=100)
        self._incident_counts: dict[str, int] = {}
        self._lost_window = False
        self._last_loss_log: dict[str, float] = {}
        self._last_unavailable_log: float | None = None

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
        self._bus.subscribe(EventType.ORDER_FILLED, self._on_market_event)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="position-monitor")
        self._recovery_task = asyncio.create_task(self._recovery_loop(), name="position-monitor-recovery")
        if self._pulse_interval is not None:
            self._timer_task = asyncio.create_task(self._timer_loop(), name="position-monitor-timer")

    async def stop(self) -> None:
        self._accepting = False
        self._bus.unsubscribe(EventType.PRICE_UPDATED, self._on_market_event)
        self._bus.unsubscribe(EventType.CANDLE_CLOSED, self._on_market_event)
        self._bus.unsubscribe(EventType.ORDER_FILLED, self._on_market_event)
        if self._timer_task is not None:
            self._timer_task.cancel()
            try:
                await self._timer_task
            except asyncio.CancelledError:
                pass
            self._timer_task = None
        if self._recovery_task is not None:
            self._recovery_task.cancel()
            try:
                await self._recovery_task
            except asyncio.CancelledError:
                pass
            self._recovery_task = None
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
        self._pulse_pending_seq = None
        self._replay_queued = False

    def enqueue_pulse(self) -> bool:
        """Put one timer pulse on the same queue as market events. Coalesced:
        returns False (and enqueues nothing) while one is already queued or the
        monitor is not accepting work."""
        if not self._accepting or self._pulse_queued:
            return False
        self._pulse_queued = True
        self._pulse_pending_seq = self._next_seq()
        self._queue.put_nowait(_Pulse(seq=self._pulse_pending_seq))
        return True

    async def _timer_loop(self) -> None:
        assert self._pulse_interval is not None
        while True:
            await asyncio.sleep(self._pulse_interval)
            self.enqueue_pulse()

    async def _recovery_loop(self) -> None:
        while True:
            await asyncio.sleep(self._recovery_interval)
            if self._journal or self._fill_pending or self._snapshot_unavailable:
                self._enqueue_replay(self._seq)

    def _enqueue_replay(self, seq: int) -> None:
        if self._accepting and not self._replay_queued:
            self._replay_queued = True
            self._queue.put_nowait(_Replay(seq))

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

    def protection_diagnostics(self) -> dict[str, object]:
        """Bounded, additive visibility; counters retain loss evidence after recovery."""
        return {
            "status": "degraded" if self._snapshot_unavailable or self._fill_pending or self._lost_window else "healthy",
            "snapshot_unavailable": self._snapshot_unavailable,
            "lost_window": self._lost_window,
            "journaled_symbols": len(self._journal),
            "journaled_ticks": sum(map(len, self._journal.values())),
            "fill_pending_symbols": sorted(self._fill_pending),
            "incident_counts": dict(self._incident_counts),
            "recent_incidents": list(self._incidents),
            "limits": {"symbols": self._max_symbols, "ticks_per_symbol": self._max_ticks,
                       "unowed_retention_seconds": self._retention},
        }

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
        if envelope.event_type == EventType.PRICE_UPDATED:
            self._journal_tick(envelope, seq)
            return
        if envelope.event_type == EventType.ORDER_FILLED:
            if envelope.symbol not in self._fill_pending and len(self._fill_pending) >= self._max_symbols:
                oldest = min(self._fill_pending, key=self._fill_pending.get)
                del self._fill_pending[oldest]
                self._reported_invisible.discard(oldest)
                self._incident("fill_marker_overflow", oldest, lost=True)
            self._fill_pending.setdefault(envelope.symbol, self._monotonic())
            self._enqueue_replay(seq)
            return
        try:
            held_symbols = {p.symbol for p in self._position_reader.get_open_positions()}
        except Exception:  # noqa: BLE001
            self._record_unavailable()
            return
        if envelope.symbol not in held_symbols:
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
                        try:
                            self._process_pulse(item)
                        finally:
                            self._pulse_queued = False
                            self._pulse_pending_seq = None
                    elif isinstance(item, _Replay):
                        self._replay_queued = False
                        self._replay_through(item.seq)
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
        self._replay_through(queued.seq)
        bar = _bar_from_envelope(envelope)
        if bar is None:
            return
        try:
            positions = [p for p in self._position_reader.get_open_positions() if p.symbol == envelope.symbol]
        except Exception:  # noqa: BLE001
            self._record_unavailable()
            return
        for position in positions:
            if not self._protective_open(position.position_id):
                continue
            intent = _evaluate(position, bar)
            if intent is not None:
                self._register("protective", intent)

    def _process_pulse(self, pulse: _Pulse) -> None:
        self._replay_through(pulse.seq)
        now = self._wall_clock()
        if now.tzinfo is None or now.utcoffset() is None:
            logger.error("PositionMonitor: wall clock returned a naive datetime; pulse skipped")
            return
        try:
            positions = self._position_reader.get_open_positions()
        except Exception:  # noqa: BLE001
            self._record_unavailable()
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

    def _incident(self, cause: str, symbol: str | None = None, *, lost: bool = False) -> None:
        self._incident_counts[cause] = self._incident_counts.get(cause, 0) + 1
        self._incidents.append({"cause": cause, "symbol": symbol, "at": self._wall_clock().isoformat()})
        self._lost_window |= lost
        if lost:
            now = self._monotonic()
            last = self._last_loss_log.get(cause)
            if last is None or now - last >= 60:
                self._last_loss_log[cause] = now
                logger.error("PositionMonitor: retained price window lost cause=%s symbol=%s", cause, symbol)

    def _record_unavailable(self) -> None:
        if not self._snapshot_unavailable:
            self._incident("snapshot_unavailable")
            now = self._monotonic()
            if self._last_unavailable_log is None or now - self._last_unavailable_log >= 60:
                self._last_unavailable_log = now
                logger.error("PositionMonitor: position snapshot unavailable; retained ticks await recovery")
        self._snapshot_unavailable = True

    def _journal_tick(self, envelope: EventEnvelope, seq: int) -> None:
        assert envelope.symbol is not None
        try:
            tick = PriceUpdated.model_validate(envelope.payload)
        except ValidationError:
            return
        ts = tick.exchange_ts
        now = self._wall_clock()
        if (ts.tzinfo is None or ts.utcoffset() is None or now.tzinfo is None
                or ts > now or not math.isfinite(tick.price) or tick.price <= 0):
            return
        symbol = envelope.symbol
        if symbol not in self._journal and len(self._journal) >= self._max_symbols:
            # Discard an unowed symbol first. Either choice records loss of
            # first-touch certainty for a position that may appear later.
            victim = None
            for candidate, ticks in self._journal.items():
                eod_candidates = self._eod_candidate_seqs(candidate)
                if not any(self._owed(candidate, item, eod_candidates) for item in ticks):
                    victim = candidate
                    break
            if victim is None:
                victim = next(iter(self._journal))
            del self._journal[victim]
            self._tick_cache.pop(victim, None)
            self._incident("symbol_capacity_loss", victim, lost=True)
        entries = self._journal.setdefault(symbol, deque())
        if len(entries) >= self._max_ticks:
            entries.popleft()
            self._incident("tick_overflow", symbol, lost=True)
        entries.append(_JournalTick(tick.price, ts, seq, self._monotonic()))
        self._cache_tick(envelope, seq)
        self._enqueue_replay(seq)

    def _eod_candidate_seqs(self, symbol: str) -> set[int]:
        ticks = self._journal.get(symbol, ())
        candidates: set[int] = set()
        for position in self._visible.values():
            if (position.symbol != symbol or not self._protective_open(position.position_id)
                    or (position.position_id, "eod") in self._slots):
                continue
            eligible = [item for item in ticks if item.ts >= position.opened_at]
            if eligible:
                candidates.add(max(eligible, key=lambda item: (item.ts, -item.seq)).seq)
            if self._pulse_pending_seq is not None:
                prior = [item for item in eligible if item.seq <= self._pulse_pending_seq]
                if prior:
                    candidates.add(max(prior, key=lambda item: (item.ts, -item.seq)).seq)
        return candidates

    def _owed(self, symbol: str, tick: _JournalTick, eod_candidates: set[int] | None = None) -> bool:
        if self._snapshot_unavailable or symbol in self._fill_pending:
            return True
        if tick.seq in (self._eod_candidate_seqs(symbol) if eod_candidates is None else eod_candidates):
            return True
        for position in self._visible.values():
            if position.symbol != symbol or tick.ts < position.opened_at or not self._protective_open(position.position_id):
                continue
            if tick.seq > self._progress.get(position.position_id, 0):
                return True
        return False

    def _prune_journal(self) -> None:
        now = self._monotonic()
        for symbol, ticks in tuple(self._journal.items()):
            eod_candidates = self._eod_candidate_seqs(symbol)
            kept = deque()
            expired = 0
            for tick in ticks:
                if now - tick.arrived >= self._retention and not self._owed(symbol, tick, eod_candidates):
                    expired += 1
                else:
                    kept.append(tick)
            if expired:
                # Expiry after an extant position safely evaluated the ticks
                # does not erase its first-touch evidence. A flat symbol may
                # still acquire a delayed fill with an earlier opened_at.
                self._incident("unowed_expiry", symbol,
                               lost=not any(p.symbol == symbol for p in self._visible.values()))
            if kept:
                self._journal[symbol] = kept
            else:
                del self._journal[symbol]
                self._tick_cache.pop(symbol, None)

    def _replay_through(self, boundary: int) -> None:
        try:
            positions = self._position_reader.get_open_positions()
        except Exception:  # noqa: BLE001
            self._record_unavailable()
            return
        self._snapshot_unavailable = False
        current = {p.position_id: p for p in positions}
        for pid in tuple(self._visible):
            if pid not in current:
                self._progress.pop(pid, None)
                self._eod_tick_floor.pop(pid, None)
                self._closed_positions.discard(pid)
                self._last_logged = {key: value for key, value in self._last_logged.items() if key[0] != pid}
                for kind in ("protective", "eod"):
                    self._slots.pop((pid, kind), None)
                self._exit_intents.pop(pid, None)
        self._visible = current
        for position in positions:
            self._fill_pending.pop(position.symbol, None)
            self._reported_invisible.discard(position.symbol)
            if not self._protective_open(position.position_id):
                continue
            for tick in self._journal.get(position.symbol, ()):
                if tick.seq > boundary:
                    break
                if tick.seq <= self._progress.get(position.position_id, 0):
                    continue
                if tick.ts >= position.opened_at:
                    bar = _Bar(tick.price, tick.price, tick.price, tick.ts)
                    intent = _evaluate(position, bar)
                    if intent is not None:
                        self._register("protective", intent)
                # Progress follows safe registration, never precedes it.
                self._progress[position.position_id] = tick.seq
                if not self._protective_open(position.position_id):
                    break
        for symbol, started in tuple(self._fill_pending.items()):
            if self._monotonic() - started >= self._retention and symbol not in self._reported_invisible:
                self._reported_invisible.add(symbol)
                self._incident("fill_invisible", symbol)
        self._prune_journal()
        if any(ticks and ticks[-1].seq > boundary for ticks in self._journal.values()):
            self._enqueue_replay(self._seq)

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
        eligible = (tick for tick in self._journal.get(position.symbol, ()) if tick.seq <= pulse_seq)
        latest = max(eligible, key=lambda tick: (tick.ts, -tick.seq), default=None)
        if latest is None:
            return None
        tick = _CachedTick(latest.price, latest.ts, latest.seq)
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
