"""
MarketStateEngine — turns Feature Engine's per-symbol output into scored
Trend/Volatility regime/Volume regime/VWAP relationship/Acceleration
state (trading-intelligence-architecture.md §4, decision #91's shape,
decision #93 for this build). Subscribes to FeaturesUpdated, one
DebounceScheduler per symbol (§4's cadence: ~1s floor/~10s ceiling per
symbol — SPY/QQQ/IWM's tighter ~3-5s ceiling is M3, not built here, since
this engine is generic over any tracked symbol and doesn't yet know which
three are "always-on").

Two deliberate deviations from decision #91's six-dimension list, both
answered directly rather than guessed at, logged in decision #93:
- `session_type_score` is dropped from this build entirely — it doesn't
  fit the directional/magnitude shape the other four dimensions use, and
  rather than force a definition on it, it's left out and flagged to
  revisit (scoring.py has no session-type function at all; this isn't a
  stub, it's an absence).
- `acceleration_score` tracks Trend's own rate of change specifically,
  not one value per dimension and not an adaptive pick of whichever
  dimension moved most.

Persistence architecture, and why it's shaped this way (decision #84,
reused deliberately rather than reinvented): DebounceScheduler's own
`stop()` is a plain `task.cancel()` + `await task` (core/debounce_
scheduler.py) — exactly the shape decision #84 diagnosed as unsafe for
any callback that does a blocking `asyncio.to_thread` DB write, because
cancelling the awaiting task does NOT stop the underlying thread-pool
write; it just detaches from awaiting it, leaving an orphaned write that
can outlive `stop()` returning. #84's own write-up flags this exact
failure mode as "very likely" present in any engine sharing that shape.
Rather than let a brand-new engine inherit a bug this project already
found and fixed once: each per-symbol DebounceScheduler's callback here
does ONLY a fast, synchronous, non-blocking queue put — no I/O — so
DebounceScheduler's plain cancel-based `stop()` stays safe to call. All
the actual work (score computation, the DB write, the publish) happens
in a single background worker consuming that queue, using the identical
poison-pill drain LevelInteractionEngine's `stop()` already established
(decision #84) — enqueue `_STOP_SENTINEL`, await the worker task with
nothing cancelled, so a write already in flight is guaranteed to finish
before `stop()` returns.

Rolling window (§4's "Implementation note"): the only history this v1
needs is enough to compute Acceleration — one prior (trend_score,
timestamp) pair per symbol, kept in `_prev_trend`. Nothing longer; §4 is
explicit that a cold start on restart is an accepted v1 simplification,
not a gap this table needs to help close. Mutated only from inside the
single worker task, so no lock is needed the way LevelInteractionEngine's
own state needs one — one consumer, one queue, strictly serial.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.debounce_scheduler import DebounceScheduler
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus, get_event_bus
from app.event_bus.events import make_envelope
from app.market_state_engine.scoring import (
    acceleration_score,
    trend_score,
    volatility_regime_score,
    volume_regime_score,
    vwap_relationship_score,
)
from app.models.market_data import Symbol
from app.models.market_state import MarketStateHistory
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.market_state import MarketState

logger = logging.getLogger(__name__)

_MIN_INTERVAL_SECONDS = 1.0
_MAX_INTERVAL_SECONDS = 10.0

_STOP_SENTINEL = object()


class MarketStateEngine:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

        self._schedulers: dict[str, DebounceScheduler] = {}
        self._latest_features: dict[str, dict[str, Any]] = {}  # symbol -> raw FeaturesUpdated payload
        self._prev_trend: dict[str, tuple[float, float]] = {}  # symbol -> (trend_score, time.monotonic() at that score)

    def start(self) -> None:
        self._bus.subscribe(EventType.FEATURES_UPDATED, self._on_features_updated)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="market-state-engine")
        logger.info("MarketStateEngine started — on FeaturesUpdated, %.0fs/%.0fs debounce per symbol",
                    _MIN_INTERVAL_SECONDS, _MAX_INTERVAL_SECONDS)

    async def stop(self) -> None:
        """Poison-pill drain (decision #84) — see module docstring for
        why this engine needs the identical fix LevelInteractionEngine
        already established, not a fresh `task.cancel()`."""
        for scheduler in self._schedulers.values():
            await scheduler.stop()
        if self._worker_task is not None and not self._worker_task.done():
            await self._queue.put(_STOP_SENTINEL)
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None
        logger.info("MarketStateEngine stopped")

    # --- Event Bus subscriber (async — EventBus._safe_call awaits a coroutine
    # handler, bus/bus.py — but must still stay fast: this lane serializes on
    # it, per _consume's `await asyncio.gather(...)` before dequeuing the next
    # envelope. Safe here because DebounceScheduler.trigger()'s common path is
    # a single non-blocking queue put, never I/O; see class docstring for why
    # the actual work is kept out of this path entirely.) -----------------------

    async def _on_features_updated(self, envelope: EventEnvelope) -> None:
        symbol = envelope.symbol
        if symbol is None:
            return
        self._latest_features[symbol] = envelope.payload
        scheduler = self._schedulers.get(symbol)
        if scheduler is None:
            scheduler = DebounceScheduler(
                callback=lambda s=symbol: self._queue.put_nowait(s),
                min_interval=_MIN_INTERVAL_SECONDS,
                max_interval=_MAX_INTERVAL_SECONDS,
                name=f"market-state-{symbol}",
            )
            self._schedulers[symbol] = scheduler
            await scheduler.start()
        await scheduler.trigger()

    # --- background worker (owns all compute + I/O + publish) -------------------

    async def _worker_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is _STOP_SENTINEL:
                    self._queue.task_done()
                    break
                symbol = item
                try:
                    state = self._compute(symbol)
                    if state is not None:
                        await asyncio.to_thread(self._persist, symbol, state)
                        await self._bus.publish(
                            make_envelope(EventType.MARKET_STATE_CHANGED, state, symbol=symbol)
                        )
                except Exception:  # noqa: BLE001 — one bad symbol must not stall the rest
                    logger.exception("MarketStateEngine failed to process %s", symbol)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    def _compute(self, symbol: str) -> MarketState | None:
        """Pure, in-memory — reads the latest cached FeaturesUpdated
        payload for `symbol` and scores.py's functions; no I/O. Returns
        None if a recompute was triggered before any FeaturesUpdated for
        this symbol ever arrived (shouldn't happen in practice — the
        scheduler is only created inside _on_features_updated — kept as
        a defensive guard, not load-bearing)."""
        payload = self._latest_features.get(symbol)
        if payload is None:
            return None

        features: dict[str, float] = payload["features"]
        close = payload["close"]

        t_score = trend_score(features.get("sma_20_slope_angle", 0.0))

        now = time.monotonic()
        prev = self._prev_trend.get(symbol)
        accel = None
        if prev is not None:
            prev_score, prev_time = prev
            accel = acceleration_score(t_score, prev_score, now - prev_time)
        self._prev_trend[symbol] = (t_score, now)

        return MarketState(
            timeframe=payload["timeframe"],
            candle_ts=payload["candle_ts"],
            trend_score=t_score,
            volatility_regime_score=volatility_regime_score(features.get("atr_14_pct", 0.0)),
            volume_regime_score=volume_regime_score(features.get("rvol", 0.0)),
            vwap_relationship_score=vwap_relationship_score(close, features.get("vwap", close)),
            acceleration_score=accel,
        )

    # --- persistence (runs off-loop via asyncio.to_thread) ----------------------

    def _persist(self, symbol: str, state: MarketState) -> None:
        session = SessionLocal()
        try:
            symbol_id = self._get_or_create_symbol_id(session, symbol)
            session.add(
                MarketStateHistory(
                    symbol_id=symbol_id,
                    timeframe=state.timeframe,
                    candle_ts=state.candle_ts,
                    trend_score=state.trend_score,
                    volatility_regime_score=state.volatility_regime_score,
                    volume_regime_score=state.volume_regime_score,
                    vwap_relationship_score=state.vwap_relationship_score,
                    acceleration_score=state.acceleration_score,
                )
            )
            session.commit()
        except Exception:  # noqa: BLE001 — same soft-fail posture as LevelInteractionEngine/CandleRecorder
            logger.exception("MarketStateEngine failed to persist state for %s", symbol)
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


_market_state_engine: MarketStateEngine | None = None


def get_market_state_engine(bus: EventBus | None = None) -> MarketStateEngine:
    """Lazy singleton, same pattern as get_level_interaction_engine()/get_context_engine()."""
    global _market_state_engine
    if _market_state_engine is None:
        _market_state_engine = MarketStateEngine(bus or get_event_bus())
    return _market_state_engine
