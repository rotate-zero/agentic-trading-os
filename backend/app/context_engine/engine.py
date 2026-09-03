"""
ContextEngine -- thin aggregator over independent ContextProvider
instances (trading-intelligence-architecture.md §5, decision #90). Calls
every registered provider, merges their output into one ContextChanged
event keyed by provider name, publishes it. Adding a new context
dimension later means writing one new provider and registering it here --
not touching this aggregator (§5's whole point).

**Two aggregation paths, not one (decision #96) -- see provider.py's own
docstring for the full "why":** `evaluate_all()` (unchanged since #92)
handles market-wide providers -- CalendarProvider only -- publishing
ContextChanged(symbol=None) on the session-boundary loop below.
`evaluate_for_symbol(symbol)` is new: handles per-symbol providers --
FundamentalsProvider, NewsFlagProvider -- publishing its own
ContextChanged(symbol=X), one per tracked symbol, on the 15-minute timer
loop further below (decision #96's own answer -- matches count_15m's
window, cheapest option under the calendar/news 60/min bucket).

v1 trigger, global path (decision #92): CalendarProvider's own stated
cadence is "changes on session/day boundaries" (§5) -- this loop follows
that literally, sleeping until MarketClock.next_session_boundary() and
re-evaluating there, rather than an arbitrary poll interval.

v1 trigger, per-symbol path (decision #96): one 15-minute timer per
tracked symbol, NOT tick-driven -- Fundamentals and News have nothing to
do with price ticks, and §5 says as much directly ("nothing about the
ContextProvider interface assumes tick-speed refresh"). FundamentalsProvider
is a cheap DB read with no reason to run on a different cadence than
News, so both ride the same per-symbol timer rather than each getting its
own -- one merged ContextChanged per symbol per tick of this loop, same
"merge everything registered" shape evaluate_all() already uses.

Tracked symbols come from `scanner_universe_symbols`, snapshotted ONCE at
start() -- a real v1 limitation, not a silent one: a symbol added to the
Scanner Universe after this engine starts won't get its own per-symbol
loop until a restart. Flagged here rather than building live
universe-change reactivity, which is real scope beyond what decision #96
asked for.

Cancellation note, global path: unlike LevelInteractionEngine (decision
#84), this loop's only per-iteration work is an in-memory provider call
plus one bus.publish() -- nothing here blocks on a to_thread DB WRITE
mid-iteration, so a plain task.cancel() + await is sufficient; #84's
poison-pill drain doesn't apply.

Cancellation note, per-symbol path: FundamentalsProvider/NewsFlagProvider
both do blocking I/O via asyncio.to_thread (a DB read, an API call), the
same shape #84 flagged as unsafe for WRITES. But neither of these is a
write -- nothing here is ever persisted (§5, explicitly, for News; reads
only for Fundamentals) -- so an orphaned in-flight read outliving
task.cancel() is harmless: its result is just never published, not a
corrupted shared write. #84's poison-pill drain protects against a
different failure mode than what these tasks are exposed to; a plain
cancel stays safe here.

Shutdown ordering -- one departure from decision #47's precedent, worth
stating explicitly rather than copying that pattern by default:
LevelInteractionEngine et al. are FeaturesUpdated *subscribers*, so they
stop AFTER the bus -- stopping the bus first bounds their drain to a
fixed backlog instead of an ever-refilling queue (#47). ContextEngine
subscribes to nothing; it only publishes. So it stops BEFORE the bus
instead, in main.py's shutdown -- nothing depends on draining a backlog,
and stopping first avoids publishing into a bus already mid-shutdown.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.context_engine.provider import ContextProvider, SymbolContextProvider
from app.context_engine.providers.calendar import CalendarProvider
from app.context_engine.providers.fundamentals import FundamentalsProvider
from app.context_engine.providers.news import NewsFlagProvider
from app.core.market_clock import MarketClock, get_market_clock
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus, get_event_bus
from app.event_bus.events import make_envelope
from app.models.market_data import Symbol
from app.models.scanner import ScannerUniverseSymbol
from app.schemas.events.context import ContextChanged
from app.schemas.events.envelope import EventType

logger = logging.getLogger(__name__)

_SYMBOL_LOOP_INTERVAL_SECONDS = 15 * 60  # decision #96


class ContextEngine:
    def __init__(
        self,
        bus: EventBus,
        providers: list[ContextProvider] | None = None,
        symbol_providers: list[SymbolContextProvider] | None = None,
        clock: MarketClock | None = None,
    ) -> None:
        self._bus = bus
        self._providers = providers if providers is not None else [CalendarProvider()]
        self._symbol_providers = (
            symbol_providers if symbol_providers is not None else [FundamentalsProvider(), NewsFlagProvider()]
        )
        self._clock = clock or get_market_clock()
        self._task: asyncio.Task | None = None
        self._symbol_tasks: dict[str, asyncio.Task] = {}
        self._bootstrap_task: asyncio.Task | None = None

    # --- global (market-wide) path, unchanged since decision #92 -----------------

    async def evaluate_all(self) -> ContextChanged:
        """Call every registered global provider, merge output by name,
        publish as one ContextChanged. `symbol` stays unset on the
        envelope -- these are market-wide, not symbol-specific (§10.1)."""
        results: dict[str, dict] = {}
        for provider in self._providers:
            results[provider.name] = await provider.evaluate()
        payload = ContextChanged(providers=results)
        await self._bus.publish(make_envelope(EventType.CONTEXT_CHANGED, payload))
        return payload

    # --- per-symbol path, decision #96 --------------------------------------------

    async def evaluate_for_symbol(self, symbol: str) -> ContextChanged:
        """Call every registered per-symbol provider for `symbol`, merge
        output by name, publish as ContextChanged(symbol=symbol)."""
        results: dict[str, dict] = {}
        for provider in self._symbol_providers:
            results[provider.name] = await provider.evaluate(symbol)
        payload = ContextChanged(providers=results)
        await self._bus.publish(make_envelope(EventType.CONTEXT_CHANGED, payload, symbol=symbol))
        return payload

    def _load_scanner_universe_symbols(self) -> list[str]:
        session = SessionLocal()
        try:
            rows = session.execute(
                select(Symbol.ticker).join(ScannerUniverseSymbol, ScannerUniverseSymbol.symbol_id == Symbol.id)
            ).scalars().all()
            return list(rows)
        finally:
            session.close()

    async def _symbol_loop(self, symbol: str) -> None:
        # Fire once immediately, same "don't wait for the first boundary
        # to have any state at all" reasoning as the global loop below.
        await self.evaluate_for_symbol(symbol)
        while True:
            await asyncio.sleep(_SYMBOL_LOOP_INTERVAL_SECONDS)
            await self.evaluate_for_symbol(symbol)

    # --- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="context-engine")
        self._bootstrap_task = asyncio.create_task(self._bootstrap_symbol_loops(), name="context-engine-symbol-bootstrap")

        logger.info(
            "ContextEngine started — global providers=%s, symbol providers=%s, session-boundary + 15min triggers",
            [p.name for p in self._providers], [p.name for p in self._symbol_providers],
        )

    async def _bootstrap_symbol_loops(self) -> None:
        symbols = await asyncio.to_thread(self._load_scanner_universe_symbols)
        for symbol in symbols:
            self._symbol_tasks[symbol] = asyncio.create_task(self._symbol_loop(symbol), name=f"context-engine-{symbol}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Cancel+await the bootstrap task FIRST, before touching
        # _symbol_tasks — otherwise a stop() racing an in-flight
        # bootstrap could miss tasks it creates after this method's own
        # iteration over _symbol_tasks already started (start() spawns
        # bootstrap as fire-and-forget, so there's no other guarantee
        # it's finished by the time stop() runs). Safe to cancel
        # mid-flight: bootstrap only reads the Scanner Universe and
        # creates tasks, no write, so a cancelled bootstrap simply
        # creates fewer (possibly zero) per-symbol tasks — nothing
        # orphaned either way.
        if self._bootstrap_task is not None:
            self._bootstrap_task.cancel()
            try:
                await self._bootstrap_task
            except asyncio.CancelledError:
                pass
            self._bootstrap_task = None

        for task in self._symbol_tasks.values():
            task.cancel()
        for task in self._symbol_tasks.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._symbol_tasks.clear()

        logger.info("ContextEngine stopped")

    async def _loop(self) -> None:
        # Fire once immediately so state isn't stale until the first
        # boundary, then re-evaluate at every session/day boundary
        # thereafter -- see module docstring re: why boundary-driven, not
        # a poll interval.
        await self.evaluate_all()
        while True:
            boundary = self._clock.next_session_boundary()
            now = datetime.now(boundary.tzinfo)
            sleep_seconds = max((boundary - now).total_seconds(), 0)
            await asyncio.sleep(sleep_seconds)
            await self.evaluate_all()


_context_engine: ContextEngine | None = None


def get_context_engine(bus: EventBus | None = None) -> ContextEngine:
    """Lazy singleton, same pattern and reasoning as
    level_interaction_engine.get_level_interaction_engine()."""
    global _context_engine
    if _context_engine is None:
        _context_engine = ContextEngine(bus or get_event_bus())
    return _context_engine
