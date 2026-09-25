"""
`PositionMonitor` — decision slug `position-monitor-lite`. See the
package docstring (`position_monitor/__init__.py`) for the full EX-5/
EX-11 scoping call, and `docs/architecture/execution-engine-design.md`
§6.6 for the design spec this implements.

Same subscribe -> own queue -> worker shape as every other engine in
this codebase (decision #84's pattern, mirrored from
`execution_engine/engine.py` and `trading_intelligence/
level_interaction_engine.py` — this task's own §1.7). Deliberately no
`DebounceScheduler`: LevelInteractionEngine's own docstring records the
project's confirmed choice that noise between raw zone transitions is
real information, not something to coalesce, and this module needs the
same precision for the same reason — coalescing ticks would risk
missing the exact tick/candle that actually crossed a stop or target.

Held-symbol filtering, stated precisely (mirrors decision #173's own
Portfolio State worker: "EventBus has no symbol-filtered subscriptions,
so unheld price events are dropped before queueing and checked again at
processing"): the subscriber callback below does one cheap
`get_open_positions()` read to decide whether to even enqueue an
incoming event, and `_process_event()` reads the Protocol again, fresh,
before evaluating — so a position that closed (or a symbol that started
being held) between enqueue and processing is never acted on with stale
membership.

EOD-flatten session-close derivation deliberately duplicates
`backtest_runner/fill_simulator.py`'s `regular_session_close_utc()`
rather than importing it (this task's own §1.8 reading, and EX-8's own
recommendation (a): "reuse conventions only... write a new incremental
model" — extracting a shared helper is EX-8's option (b), explicitly
named as expanding the build's footprint into `backtest_runner/`, not
this task's call to make). Same reasoning `fill_simulator.py` itself
gives for not adding a method to `MarketClock` instead: `MarketClock`
exposes no public "close instant for trading_day X" accessor, only
session-membership checks, and this task's own scope discipline (no
redesigning existing engines to make new work easier) applies to
`MarketClock` exactly as it did there.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.core.market_clock import MarketClock, get_market_clock
from app.event_bus.bus import EventBus
from app.position_monitor.ports import PositionReader, PositionView
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.market_data import CandleClosed, PriceUpdated

logger = logging.getLogger(__name__)

_STOP_SENTINEL = object()

# Same ET zone / close-time constants core/market_clock.py itself uses —
# see module docstring above re: why this is a local copy, not an import
# from fill_simulator.py or a new market_clock.py method.
_ET = ZoneInfo("America/New_York")
_REGULAR_CLOSE = time(16, 0)
_HALF_DAY_CLOSE = time(13, 0)


def _regular_session_close_utc(clock: MarketClock, trading_day: date) -> datetime:
    """Field-for-field mirror of `fill_simulator.regular_session_close_utc`
    (§1.8), so a future parity test between the backtest and live exit
    paths (Slice A's AC #2, not this task's own scope) has a chance of
    ever passing."""
    close_time = _HALF_DAY_CLOSE if clock.is_half_day(trading_day) else _REGULAR_CLOSE
    return datetime.combine(trading_day, close_time, tzinfo=_ET).astimezone(timezone.utc)


@dataclass(frozen=True)
class ExitIntent:
    """The typed, in-process output this module's whole job is to
    produce (§3) — nothing more. No `client_order_id`: minting one is
    explicitly the order-placement half of §6.6's "the position moves to
    `closing` first" line, out of scope here per §3/§4 item 4 — that is
    the later task's job, once EX-5 is confirmed."""

    position_id: UUID
    symbol: str
    side: Literal["BUY", "SELL"]
    qty: int
    exit_reason: Literal["stop", "target", "eod_flatten"]
    trigger_price: float
    trigger_ts: datetime  # the exchange/candle timestamp that produced this intent — never wall-clock


@dataclass(frozen=True)
class _Bar:
    """One evaluatable price observation — either a single tick
    (`high == low == close == price`) or a closed candle. Unifying the
    two into one shape lets `_evaluate()` below run the identical
    precedence check `fill_simulator.simulate_exit()` uses against
    either kind of input."""

    high: float
    low: float
    close: float
    ts: datetime


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


def _evaluate(position: PositionView, bar: _Bar, clock: MarketClock) -> ExitIntent | None:
    """Precedence, exactly matching `fill_simulator`'s own convention
    (§1.8/EX-8): stop checked first, so a single bar crossing both stop
    and target resolves to `"stop"` (stop-wins-tie) without a separate
    branch. EOD-flatten is keyed to the POSITION's own entry day
    (`clock.trading_day(position.opened_at)`), the same way
    `fill_simulator.simulate_exit()` keys off `entry_fill.entry_ts` —
    not "today" generically — since this system holds no positions
    overnight (§5/D8) that entry day is always the relevant one."""
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
    entry_day = clock.trading_day(position.opened_at)
    session_close = _regular_session_close_utc(clock, entry_day)
    if bar.ts >= session_close:
        return ExitIntent(
            position_id=position.position_id,
            symbol=position.symbol,
            side=position.side,
            qty=position.qty,
            exit_reason="eod_flatten",
            trigger_price=bar.close,
            trigger_ts=bar.ts,
        )
    return None


class PositionMonitor:
    def __init__(
        self,
        bus: EventBus,
        position_reader: PositionReader,
        *,
        clock: MarketClock | None = None,
    ) -> None:
        self._bus = bus
        self._position_reader = position_reader
        self._clock = clock or get_market_clock()
        self._queue: asyncio.Queue[EventEnvelope | object] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._accepting = False
        # Idempotency latch (§4 item 4): a position_id in this dict has
        # already had its one ExitIntent produced — "moved to closing"
        # in this module's own in-process sense only (see engine's own
        # docstring above re: this NOT being accounting.PositionState's
        # persisted "closing" status, a different, unrelated field of
        # the same name). No second intent is ever produced for it.
        self._exit_intents: dict[UUID, ExitIntent] = {}

    def start(self) -> None:
        self._accepting = True
        self._bus.subscribe(EventType.PRICE_UPDATED, self._on_market_event)
        self._bus.subscribe(EventType.CANDLE_CLOSED, self._on_market_event)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="position-monitor")

    async def stop(self) -> None:
        self._accepting = False
        self._bus.unsubscribe(EventType.PRICE_UPDATED, self._on_market_event)
        self._bus.unsubscribe(EventType.CANDLE_CLOSED, self._on_market_event)
        if self._worker_task is not None and not self._worker_task.done():
            await self._queue.put(_STOP_SENTINEL)
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None

    def get_exit_intents(self, symbol: str | None = None) -> tuple[ExitIntent, ...]:
        """Synchronous, point-in-time read surface — same convention as
        `get_snapshot()` everywhere else in this codebase (§4 item 5).
        `symbol=None` returns every intent produced so far, in no
        particular order."""
        values = self._exit_intents.values()
        if symbol is not None:
            values = (intent for intent in values if intent.symbol == symbol)
        return tuple(values)

    # --- EventBus subscriber (must stay cheap — no awaiting here) ---------

    def _on_market_event(self, envelope: EventEnvelope) -> None:
        if not self._accepting or envelope.symbol is None:
            return
        held_symbols = {p.symbol for p in self._position_reader.get_open_positions()}
        if envelope.symbol not in held_symbols:
            return  # cheap early drop, rechecked at processing time — see module docstring
        self._queue.put_nowait(envelope.model_copy(deep=True))

    # --- worker -------------------------------------------------------------

    async def _worker_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is _STOP_SENTINEL:
                    self._queue.task_done()
                    break
                try:
                    self._process_event(item)  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001 — one bad event must not kill the worker
                    logger.exception("PositionMonitor: unhandled error processing event")
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    def _process_event(self, envelope: EventEnvelope) -> None:
        bar = _bar_from_envelope(envelope)
        if bar is None:
            return
        positions = [p for p in self._position_reader.get_open_positions() if p.symbol == envelope.symbol]
        for position in positions:
            if position.position_id in self._exit_intents:
                continue  # already latched — no second intent, ever
            intent = _evaluate(position, bar, self._clock)
            if intent is not None:
                self._exit_intents[position.position_id] = intent
                logger.info(
                    "PositionMonitor: ExitIntent produced position_id=%s symbol=%s reason=%s trigger_price=%s",
                    position.position_id,
                    position.symbol,
                    intent.exit_reason,
                    intent.trigger_price,
                )
