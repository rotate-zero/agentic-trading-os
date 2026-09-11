"""
ReplayStateProducer — the seam that owns turning "here's the next
historical candle" into "here's real Feature/MarketState/Context state
for that instant," so the (not-yet-built) Backtest Runner never has to
know HOW that happens.

**The contract, stated the way Unit 2's brief states it:**

    historical candle timestamp
            |
      deterministic replay
            |
    state/features/context for that timestamp

`BacktestRunner` will only ever call `await producer.advance_to(symbol,
candle)` and read the returned `ReplayState` — it has no visibility into,
and must never depend on, whatever `advance_to()` does internally to get
there. That's the whole point of this seam: `EngineBackedReplayStateProducer`
below currently satisfies the contract by running the REAL async engines
and waiting for them to settle (which, for `MarketStateEngine`
specifically, really does mean a bounded real-world wait — see that
class's own docstring) — but a future producer could satisfy the exact
same `ReplayStateProducer` ABC with a synchronous, non-async-engine
implementation (e.g. a pure-function forward walk, the same pattern
`feature_engine/historical.py` already established for SMA/EMA) without
`BacktestRunner` changing at all. This task does not build that
alternative — it only makes sure today's implementation doesn't foreclose
it.

**candle_ts is the only source of historical truth.** Every timestamp
this module produces or checks — `FeatureSet.candle_ts`,
`MarketState.candle_ts`, the replay clock handed to
`BacktestContextProvider.advance_to()` — is derived from the fixture
candle being replayed, never `datetime.now()`/`time.time()`. The
wall-clock waits inside `_settle()` below are about HOW LONG this process
waits for a real background task to catch up, not WHAT INSTANT is being
computed for; those are two different clocks and this module is careful
never to let the first one leak into the second.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.backtest_runner.context_provider import BacktestContextProvider
from app.broker_adapters.base import Candle
from app.context_engine.engine import ContextEngine
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.feature_engine.engine import FeatureEngine
from app.market_state_engine.engine import MarketStateEngine
from app.schemas.events.context import ContextChanged
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_data import CandleClosed
from app.schemas.events.market_state import MarketState
from app.trading_intelligence.level_interaction_engine import LevelInteractionEngine

logger = logging.getLogger(__name__)

__all__ = ["ReplayState", "ReplayStateProducer", "EngineBackedReplayStateProducer", "ReplaySettleTimeout"]


class ReplaySettleTimeout(RuntimeError):
    """Raised when the downstream pipeline hasn't produced state for a
    replayed candle within the bounded wait `_settle()` allows. A
    timeout here is a real signal something is wrong (a subscriber
    silently failing, a debounce that never fires) — never suppressed
    into a fabricated/partial ReplayState."""


@dataclass(frozen=True)
class ReplayState:
    """Exactly what `Strategy.evaluate(symbol, market_state, features,
    context)` needs, for one symbol at one replayed candle_ts. All three
    `.candle_ts`-bearing fields are asserted equal to the input candle's
    `candle_ts` before this is ever constructed — see `_settle()`."""

    symbol: str
    candle_ts: datetime
    features: FeatureSet
    market_state: MarketState
    context: ContextChanged


class ReplayStateProducer(ABC):
    """See module docstring. One instance per backtest run."""

    @abstractmethod
    async def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def advance_to(self, symbol: str, candle: Candle) -> ReplayState:
        """Feed one closed candle into the replay and return real state
        for it, settled and ready to hand to `Strategy.evaluate()`. Must
        not return until `.market_state.candle_ts`/`.features.candle_ts`
        both equal `candle.candle_ts` — no partial/stale state is ever
        handed back silently."""
        raise NotImplementedError


class EngineBackedReplayStateProducer(ReplayStateProducer):
    """v1's only implementation. Wires a fresh, run-scoped `EventBus` +
    real `FeatureEngine` + real `LevelInteractionEngine` + real
    `MarketStateEngine` + a real `ContextEngine` (built from the supplied
    `BacktestContextProvider`) — the SAME engine classes production uses,
    zero modification, zero subclassing. Nothing here reimplements
    feature/state/context computation; this class only orchestrates
    candle delivery and waits for the real pipeline to reflect it.

    **Where the wall-clock waiting actually lives (read this before
    changing anything about candle pacing).** Three different settling
    mechanisms are needed, for three different reasons — tracing exactly
    why required reading each engine's own internals, not assuming:

    1. `EventBus` dispatch: publishing `CandleClosed` only enqueues it;
       the bus's own `_consume()` loop calls `queue.task_done()` only
       after every subscriber handler for that envelope has returned.
       `Queue.join()` on the bus's own queues is therefore an exact,
       non-probabilistic signal that every subscriber's SYNCHRONOUS
       handler has returned — not a sleep, not a poll, a real
       synchronization primitive `asyncio.Queue` already provides.

    2. `FeatureEngine`/`LevelInteractionEngine`: each subscriber handler
       above only does `self._queue.put_nowait(...)` — the actual compute
       + publish happens on a SEPARATE background worker task reading
       that engine's own internal queue (confirmed by reading both
       classes directly). `Queue.join()` on the bus only proves hand-off,
       not completion — so this producer also joins each engine's own
       internal `_queue` after the bus round, which is exact for the same
       reason: `task_done()` there is called only after that engine's
       worker has fully processed the item (`_worker_loop`, both
       classes). Neither engine debounces (confirmed: no
       `DebounceScheduler` in either), so once their own queue is joined,
       their state is genuinely current — no sleep needed for either.

    3. `MarketStateEngine`: DOES debounce (`DebounceScheduler`,
       `_MIN_INTERVAL_SECONDS = 1.0` — confirmed by reading
       `market_state_engine/engine.py`/`core/debounce_scheduler.py`
       directly). A symbol's first-ever trigger in a process runs its
       callback immediately (an uninitialized `_last_run = 0.0` makes the
       very first `elapsed` reading enormous), but every trigger after
       that, arriving within 1 real second of the previous one — which a
       fast, non-real-time replay loop will do for every candle after the
       first — gets deferred onto a REAL `asyncio.sleep(delay)` inside
       `DebounceScheduler._run_after_delay()`. There is no flush/force
       path on `DebounceScheduler` and this class deliberately does not
       add one (Unit 2 brief: "Do not redesign the existing engines
       merely to make v1 replay easier"). So `_settle()` polls
       `MarketState` at a short interval, bounded by a generous timeout,
       until its own subscriber cache shows `candle_ts` caught up — this
       IS a real wall-clock wait, contained entirely in this method,
       genuinely up to ~1 real second per candle after the first. This is
       the ONLY step in `advance_to()` that imposes a real delay; the
       other two are exact synchronization, not timed waits.

    This means a v1 fixture run of N candles for one symbol costs
    roughly N real seconds, dominated entirely by step 3 — acceptable for
    the "smallest useful" validation run this task builds, explicitly NOT
    acceptable for a future real multi-year historical backtest. That's a
    real, separate scaling problem for whoever builds the outer sweep
    later (see this class's own module docstring on the
    `ReplayStateProducer` seam existing so that future work doesn't
    require touching `BacktestRunner` at all) — not something this task
    solves by redesigning `DebounceScheduler`.
    """

    def __init__(
        self,
        *,
        context_provider: BacktestContextProvider,
        settle_timeout_seconds: float = 5.0,
        settle_poll_interval_seconds: float = 0.05,
    ) -> None:
        self._context_provider = context_provider
        self._settle_timeout = settle_timeout_seconds
        self._settle_poll_interval = settle_poll_interval_seconds

        self.bus = EventBus()
        self.feature_engine = FeatureEngine(self.bus)
        self.level_interaction_engine = LevelInteractionEngine(self.bus)
        self.market_state_engine = MarketStateEngine(self.bus)
        providers, symbol_providers = context_provider.build_engine_providers()
        self.context_engine = ContextEngine(self.bus, providers=providers, symbol_providers=symbol_providers)

        # This producer's OWN read cache — populated by subscribing to the
        # real FeaturesUpdated/MarketStateChanged events, the exact same
        # pattern `strategy_engine/scheduler.py`'s `_on_features_updated`/
        # `_on_market_state_changed` already use for the live path (see
        # that module for the precedent). Reading through this cache
        # rather than reaching into engine internals (e.g.
        # `feature_engine._latest`) means this producer only ever
        # observes state the same way a real live subscriber would.
        self._latest_features: dict[tuple[str, str], FeatureSet] = {}
        self._latest_market_state: dict[str, MarketState] = {}

        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self.bus.subscribe(EventType.FEATURES_UPDATED, self._on_features_updated)
        self.bus.subscribe(EventType.MARKET_STATE_CHANGED, self._on_market_state_changed)
        await self.bus.start()
        self.feature_engine.start()
        self.level_interaction_engine.start()
        self.market_state_engine.start()
        # ContextEngine's own start() spawns REAL wall-clock-timer-driven
        # background loops (_symbol_loop/_global_loop, real
        # asyncio.sleep(900)) — deliberately never started here. This
        # producer drives ContextEngine entirely via direct, explicit
        # evaluate_for_symbol() calls in advance_to(), never via its own
        # timers — see context_provider.py's module docstring for why a
        # replay must control its own notion of "now."
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        await self.feature_engine_stop_if_supported()
        await self.level_interaction_engine.stop()
        await self.market_state_engine.stop()
        await self.bus.stop()
        self._started = False

    async def feature_engine_stop_if_supported(self) -> None:
        """FeatureEngine has no `async def stop()` of its own (confirmed
        by reading `feature_engine/engine.py` directly — unlike
        `LevelInteractionEngine`/`MarketStateEngine`, it was never given
        the decision #84 poison-pill teardown, presumably because live
        production never stops it mid-process). That's a real,
        pre-existing characteristic this task doesn't fix by touching
        `feature_engine/engine.py` (out of scope, same "don't redesign
        existing engines" boundary as everywhere else in this module) —
        but a backtest run genuinely needs clean multi-run teardown, so
        THIS producer, which owns the instance it created, cancels the
        worker task directly rather than leaking it. Found empirically:
        an early version of this method was a no-op, and pytest's
        cross-test event-loop teardown surfaced the leaked task as a
        'RuntimeError: Event loop is closed' warning from inside
        FeatureEngine's own `_worker_loop` — a real resource leak, not a
        cosmetic warning, since a leaked task from one backtest run could
        in principle still be holding a reference into a torn-down run's
        state."""
        task = self.feature_engine._worker_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # --- subscriber cache (mirrors strategy_engine/scheduler.py) -----------

    async def _on_features_updated(self, envelope: EventEnvelope) -> None:
        symbol = envelope.symbol
        if symbol is None:
            return
        timeframe = envelope.payload.get("timeframe")
        self._latest_features[(symbol, timeframe)] = FeatureSet.model_validate(envelope.payload)

    async def _on_market_state_changed(self, envelope: EventEnvelope) -> None:
        symbol = envelope.symbol
        if symbol is None:
            return
        self._latest_market_state[symbol] = MarketState.model_validate(envelope.payload)

    # --- replay ---------------------------------------------------------

    async def advance_to(self, symbol: str, candle: Candle) -> ReplayState:
        if not self._started:
            raise RuntimeError("EngineBackedReplayStateProducer.advance_to() called before start()")

        envelope = make_envelope(
            EventType.CANDLE_CLOSED,
            CandleClosed(
                timeframe=candle.timeframe,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                candle_ts=candle.candle_ts,
            ),
            symbol=symbol,
        )
        await self.bus.publish(envelope)

        # Step 1 — exact: bus dispatch to every subscriber's synchronous
        # handler (FeatureEngine._on_candle_closed, and, once
        # FeaturesUpdated is published below, LevelInteractionEngine/
        # MarketStateEngine/this producer's own _on_features_updated).
        await self._drain_bus()
        # Step 2 — exact: each engine's own internal worker queue.
        await self.feature_engine._queue.join()
        await self._drain_bus()  # the FeaturesUpdated publish just triggered needs its own dispatch round
        await self.level_interaction_engine._queue.join()

        # Step 3 — the one real wait: MarketStateEngine's debounce floor.
        # See class docstring's numbered breakdown above.
        await self._settle_market_state(symbol, candle.candle_ts)
        await self._drain_bus()  # dispatch this producer's own MarketStateChanged subscriber

        features = self._latest_features.get((symbol, "1m"))
        market_state = self._latest_market_state.get(symbol)
        if features is None or market_state is None:
            # Should be unreachable given _settle_market_state() already
            # confirmed market_state — a real bug, not an expected
            # "waiting" condition, so this is a hard failure, not a
            # ReplaySettleTimeout.
            raise RuntimeError(
                f"advance_to({symbol!r}, candle_ts={candle.candle_ts!r}): pipeline settled but "
                f"features={features!r} market_state={market_state!r} — this is a bug in this "
                "producer, not an honest absence."
            )
        if features.candle_ts != candle.candle_ts or market_state.candle_ts != candle.candle_ts:
            raise RuntimeError(
                f"advance_to({symbol!r}): settled state's candle_ts doesn't match the replayed "
                f"candle — features.candle_ts={features.candle_ts!r}, "
                f"market_state.candle_ts={market_state.candle_ts!r}, expected {candle.candle_ts!r}."
            )

        # Context: driven directly, not via subscription — ContextEngine
        # isn't candle-triggered at all (see context_provider.py). Advance
        # the replay-clock-aware provider's cursor to THIS candle's
        # instant, then evaluate synchronously and await it directly —
        # no settling needed, this producer is the one calling it.
        #
        # BOTH aggregation paths, in this order — a real bug caught by
        # actually running Unit 2's proof rather than reasoning about it:
        # evaluate_for_symbol() alone only calls `_symbol_providers`
        # (deliberately empty here — see context_provider.py), so the
        # calendar provider (registered on the GLOBAL `providers` list)
        # never ran and `context.providers` came back {} every candle.
        # evaluate_all() is the path that actually calls it. Same two-call
        # order `test_strategy_scheduler.py`'s own real-engine integration
        # tests already establish for exactly this reason.
        self._context_provider.advance_to(candle.candle_ts)
        await self.context_engine.evaluate_all()
        await self.context_engine.evaluate_for_symbol(symbol)
        context = self._read_context(self.context_engine, symbol)
        if context is None:
            # Should be unreachable: evaluate_for_symbol() was just
            # awaited for this exact symbol, synchronously, above.
            raise RuntimeError(
                f"advance_to({symbol!r}): context missing immediately after evaluate_for_symbol() — "
                "a real bug, not an honest cold-start (that case can't occur here)."
            )

        return ReplayState(
            symbol=symbol, candle_ts=candle.candle_ts, features=features, market_state=market_state, context=context
        )

    # --- settling internals ----------------------------------------------

    async def _drain_bus(self) -> None:
        """Exact — see class docstring step 1. Reaches into the bus's own
        private queues rather than adding a new public `drain()` method to
        `event_bus/bus.py` itself: this producer owns a fresh, run-scoped
        `EventBus` instance it constructed (never the live process
        singleton), so this is inspecting an object this class already
        fully owns, not reaching across a module boundary into shared
        live infrastructure — deliberately NOT changing `bus.py` itself,
        per the Unit 2 brief's 'do not redesign existing engines merely
        to make v1 replay easier' applied to the bus as well."""
        await self.bus._critical_queue.join()
        await self.bus._normal_queue.join()

    async def _settle_market_state(self, symbol: str, candle_ts: datetime) -> None:
        """The one real wait — see class docstring step 3. Polls this
        producer's OWN subscriber cache (never engine internals) until it
        reflects `candle_ts`, bounded by `_settle_timeout`. A timeout
        raises `ReplaySettleTimeout` rather than returning stale state —
        this producer never hands back a `ReplayState` whose
        `market_state.candle_ts` doesn't match the candle being
        replayed."""
        deadline = time.monotonic() + self._settle_timeout
        while True:
            current = self._latest_market_state.get(symbol)
            if current is not None and current.candle_ts == candle_ts:
                return
            if time.monotonic() >= deadline:
                raise ReplaySettleTimeout(
                    f"MarketStateEngine did not settle to candle_ts={candle_ts!r} for {symbol!r} "
                    f"within {self._settle_timeout}s (last seen: "
                    f"{current.candle_ts if current else None!r})"
                )
            await asyncio.sleep(self._settle_poll_interval)

    @staticmethod
    def _read_context(context_engine: ContextEngine, symbol: str) -> ContextChanged | None:
        """Identical reconstruction to `strategy_engine/scheduler.py`'s
        own `_read_context` static method — same shape, same "absent
        means ContextEngine hasn't evaluated this symbol yet" contract.
        Deliberately reads the `context_engine` INSTANCE this producer
        owns and was just passed, not the `get_context_engine()`
        process-wide singleton getter `scheduler.py`'s version uses:
        `advance_to()`'s correctness must never depend on whether some
        external caller has (yet) installed this producer's engines as
        the process singleton via `engine_singleton_guard` — that
        installation exists for `state_snapshot.py`'s capture functions
        and for strategies that call `get_level_interaction_engine()`
        directly (First Pullback/Reversal, D9), a separate concern from
        this producer correctly reading its own state.
        """
        snapshot = context_engine.get_snapshot(symbol)
        raw = snapshot["symbols"].get(symbol)
        if raw is None:
            return None
        return ContextChanged.model_validate({"providers": raw["providers"]})
