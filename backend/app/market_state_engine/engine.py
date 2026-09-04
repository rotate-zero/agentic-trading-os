"""
MarketStateEngine — turns Feature Engine's per-symbol output into scored
Trend/Volatility regime/Volume regime/VWAP relationship/Acceleration
state (trading-intelligence-architecture.md §4, decision #91's shape,
decision #93 for this build), and additionally synthesizes SPY/QQQ/IWM's
own per-symbol Trend scores into the small `CrossSymbolState` composite
(decision #91 §4, this build M3 — decision #97). Subscribes to
FeaturesUpdated, one DebounceScheduler per symbol (§4's cadence: ~1s
floor/~10s ceiling per symbol — SPY/QQQ/IWM get the same ~1s floor but a
tighter ~3-5s ceiling, `_CROSS_SYMBOL_MAX_INTERVAL_SECONDS`, since
broad-market state is what everything else gets compared against).

Cross-symbol synthesis, in one sentence: after any of SPY/QQQ/IWM
completes its own per-symbol compute, the engine checks whether all three
have now reported at least one trend_score, and if so recomputes and
republishes `CrossSymbolState` — no separate trigger, no separate
scheduler; it rides the same worker loop and the same per-symbol
DebounceScheduler cadence that got it there. Persisted as a `__MARKET__`
sentinel row in the same `market_state_history` table (decision #91: "no
separate table"), published via the same `MarketStateChanged` event type
with `envelope.symbol == "__MARKET__"` (decision #91: "no new EventType
needed — envelope.symbol distinguishes the two shapes"). No fabricated
partial state: `_compute_cross_symbol` returns None until all three have
reported at least once, per the project's honest-state-over-fabricated-
state principle.

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
`_cross_symbol_trend` (M3) is the identical shape one level up — SPY/
QQQ/IWM's own latest trend_score, no timestamp needed since cross-symbol
synthesis doesn't compute a rate of change, just a same-moment
comparison across the three — also mutated only from inside the worker
task.
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
    iwm_confirmation_score,
    qqq_leadership_score,
    risk_on_score,
    trend_alignment_score,
    trend_score,
    volatility_regime_score,
    volume_regime_score,
    vwap_relationship_score,
)
from app.models.market_data import Symbol
from app.models.market_state import MarketStateHistory
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.market_state import CrossSymbolState, MarketState

logger = logging.getLogger(__name__)

_MIN_INTERVAL_SECONDS = 1.0
_MAX_INTERVAL_SECONDS = 10.0

# M3 (decision #91 §4, this build #97) — SPY/QQQ/IWM are always-on
# cross-symbol subjects: same ~1s floor as any other symbol, but a
# tighter ceiling since broad-market state is what everything else gets
# compared against. The sentinel row's own ticker never gets a
# FeaturesUpdated subscription or scheduler — it's synthesized, not
# Feature-Engine-driven.
_CROSS_SYMBOL_TICKERS = frozenset({"SPY", "QQQ", "IWM"})
_CROSS_SYMBOL_MAX_INTERVAL_SECONDS = 4.0
_CROSS_SYMBOL_SENTINEL = "__MARKET__"

_STOP_SENTINEL = object()


class MarketStateEngine:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

        self._schedulers: dict[str, DebounceScheduler] = {}
        self._latest_features: dict[str, dict[str, Any]] = {}  # symbol -> raw FeaturesUpdated payload
        self._prev_trend: dict[str, tuple[float, float]] = {}  # symbol -> (trend_score, time.monotonic() at that score)
        self._cross_symbol_trend: dict[str, float] = {}  # "SPY"/"QQQ"/"IWM" -> latest trend_score (M3)

        # Read-side snapshot cache (decision #98, M4) — see get_snapshot()'s
        # own docstring for why this exists: this engine only ever published
        # MarketStateChanged onto the bus, with no synchronous way for a
        # consumer to ask "what do you currently believe about NVDA" without
        # replaying event history itself. Mutated only from inside the single
        # worker task (_worker_loop), same single-writer discipline
        # `_prev_trend`/`_cross_symbol_trend` already follow above — no lock
        # needed for the same reason.
        self._latest_market_state: dict[str, MarketState] = {}
        self._latest_cross_symbol_state: CrossSymbolState | None = None

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
            max_interval = (
                _CROSS_SYMBOL_MAX_INTERVAL_SECONDS if symbol in _CROSS_SYMBOL_TICKERS else _MAX_INTERVAL_SECONDS
            )
            scheduler = DebounceScheduler(
                callback=lambda s=symbol: self._queue.put_nowait(s),
                min_interval=_MIN_INTERVAL_SECONDS,
                max_interval=max_interval,
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
                        # Cache before publish (decision #98) — a subscriber
                        # reacting to the event that's about to go out can
                        # immediately call get_snapshot() and see this exact
                        # state, never a stale prior value racing the event.
                        self._latest_market_state[symbol] = state
                        await self._bus.publish(
                            make_envelope(EventType.MARKET_STATE_CHANGED, state, symbol=symbol)
                        )
                        if symbol in _CROSS_SYMBOL_TICKERS:
                            self._cross_symbol_trend[symbol] = state.trend_score
                            try:
                                cross_state = self._compute_cross_symbol()
                                if cross_state is not None:
                                    await asyncio.to_thread(self._persist_cross_symbol, cross_state)
                                    self._latest_cross_symbol_state = cross_state
                                    await self._bus.publish(
                                        make_envelope(
                                            EventType.MARKET_STATE_CHANGED,
                                            cross_state,
                                            symbol=_CROSS_SYMBOL_SENTINEL,
                                        )
                                    )
                            except Exception:  # noqa: BLE001 — a cross-symbol failure
                                # shouldn't read as this symbol's own per-symbol compute
                                # failing (it just did, successfully, above).
                                logger.exception("MarketStateEngine failed cross-symbol synthesis (triggered by %s)", symbol)
                except Exception:  # noqa: BLE001 — one bad symbol must not stall the rest
                    logger.exception("MarketStateEngine failed to process %s", symbol)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    # --- read-side snapshot (decision #98, M4) ----------------------------------

    def get_snapshot(self, symbol: str | None = None) -> dict[str, Any]:
        """
        Current computed state, synchronous, in-memory — no I/O, safe to
        call directly from an async route handler or a future Strategy's
        MATCH stage without asyncio.to_thread. Same "read-side of an
        event-only engine" pattern FeatureEngine/LevelInteractionEngine
        already established (decision #47), added here so a consumer
        doesn't have to reconstruct "current state" by replaying
        MarketStateChanged history itself.

        Shape: {"symbols": {ticker: {...MarketState fields, "candle_ts":
        iso str}}, "market": {...CrossSymbolState fields, "candle_ts":
        iso str} | None}.

        - symbol=None: every per-symbol MarketState this process has
          computed at least once, plus the cross-symbol composite once
          it's been synthesized at least once (None until SPY/QQQ/IWM
          have all reported — see _compute_cross_symbol's own docstring).
        - symbol="__MARKET__": "symbols" is always {} (the sentinel has
          no per-symbol row of its own); only "market" is populated.
        - symbol="<ticker>": "symbols" has at most one entry. "market" is
          still included whenever available regardless of which symbol
          was asked for — broad-market state isn't scoped to the request,
          the same way a strategy reading NVDA's own state would also
          want to know what SPY/QQQ/IWM are doing without a second call.

        Deliberately NOT pre-populated for the whole configured universe —
        same "empty means not-yet, not zero" convention as FeatureEngine's
        own get_snapshot(): a symbol this process hasn't computed a
        MarketState for yet is simply absent, never a fabricated default
        (honest state over fabricated state, strategy-engine-design.md §11).
        """
        symbols: dict[str, Any] = {}
        if symbol is None:
            for sym, state in self._latest_market_state.items():
                symbols[sym] = state.model_dump(mode="json")
        elif symbol != _CROSS_SYMBOL_SENTINEL:
            state = self._latest_market_state.get(symbol)
            if state is not None:
                symbols[symbol] = state.model_dump(mode="json")

        market = (
            self._latest_cross_symbol_state.model_dump(mode="json")
            if self._latest_cross_symbol_state is not None
            else None
        )
        return {"symbols": symbols, "market": market}

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

    def _compute_cross_symbol(self) -> CrossSymbolState | None:
        """Pure, in-memory, mirroring `_compute`'s shape — reads
        `_cross_symbol_trend` (updated by the worker loop right before
        this is called) and scoring.py's cross-symbol functions; no I/O.

        Returns None until all three of SPY/QQQ/IWM have reported at
        least one trend_score — no fabricated partial state (honest
        state over fabricated state, the same principle `acceleration_
        score` already applies to a symbol's first-ever recompute).
        Once available, `spy_direction_score`/`qqq_direction_score`/
        `iwm_direction_score` are a straight passthrough of each
        symbol's own trend_score (trading-intelligence-architecture.md
        §4) — no separate function needed for those three, unlike the
        four synthesized scores.

        `timeframe`/`candle_ts` are borrowed from whichever of the three
        symbols has the most recent `candle_ts` in `_latest_features` —
        SPY/QQQ/IWM share a debounce cadence but don't necessarily
        recompute in perfect lockstep, so this composite's own timestamp
        should reflect the freshest of the three inputs, not an
        arbitrary pick."""
        if not _CROSS_SYMBOL_TICKERS.issubset(self._cross_symbol_trend.keys()):
            return None

        spy = self._cross_symbol_trend["SPY"]
        qqq = self._cross_symbol_trend["QQQ"]
        iwm = self._cross_symbol_trend["IWM"]

        newest_ticker = max(
            _CROSS_SYMBOL_TICKERS,
            key=lambda t: self._latest_features[t]["candle_ts"],
        )
        newest_payload = self._latest_features[newest_ticker]

        return CrossSymbolState(
            timeframe=newest_payload["timeframe"],
            candle_ts=newest_payload["candle_ts"],
            spy_direction_score=spy,
            qqq_direction_score=qqq,
            iwm_direction_score=iwm,
            trend_alignment_score=trend_alignment_score(spy, qqq, iwm),
            risk_on_score=risk_on_score(spy, qqq, iwm),
            qqq_leadership_score=qqq_leadership_score(spy, qqq),
            iwm_confirmation_score=iwm_confirmation_score(spy, qqq, iwm),
        )

    # --- persistence (runs off-loop via asyncio.to_thread) ----------------------

    def _persist(self, symbol: str, state: MarketState) -> None:
        session = SessionLocal()
        try:
            symbol_id = self._get_or_create_symbol_id(session, symbol)
            row = MarketStateHistory(
                symbol_id=symbol_id,
                timeframe=state.timeframe,
                candle_ts=state.candle_ts,
                trend_score=state.trend_score,
                volatility_regime_score=state.volatility_regime_score,
                volume_regime_score=state.volume_regime_score,
                vwap_relationship_score=state.vwap_relationship_score,
                acceleration_score=state.acceleration_score,
            )
            # Write-time assertion (M3, decision #89's entry_qty==exit_qty
            # precedent, application-level not a DB CHECK): a per-symbol
            # row must populate the per-symbol column group and leave the
            # cross-symbol group untouched — see models/market_state.py's
            # docstring for the two mutually exclusive row shapes this
            # table now holds.
            assert row.trend_score is not None and row.spy_direction_score is None, (
                "per-symbol MarketStateHistory row must populate the per-symbol "
                "score columns and leave the cross-symbol columns NULL"
            )
            session.add(row)
            session.commit()
        except Exception:  # noqa: BLE001 — same soft-fail posture as LevelInteractionEngine/CandleRecorder
            logger.exception("MarketStateEngine failed to persist state for %s", symbol)
            session.rollback()
        finally:
            session.close()

    def _persist_cross_symbol(self, state: CrossSymbolState) -> None:
        """Mirrors `_persist`'s structure/error-handling exactly — same
        soft-fail posture, same session lifecycle — writing the
        `__MARKET__` sentinel row instead of a per-symbol one."""
        session = SessionLocal()
        try:
            symbol_id = self._get_or_create_symbol_id(session, _CROSS_SYMBOL_SENTINEL)
            row = MarketStateHistory(
                symbol_id=symbol_id,
                timeframe=state.timeframe,
                candle_ts=state.candle_ts,
                spy_direction_score=state.spy_direction_score,
                qqq_direction_score=state.qqq_direction_score,
                iwm_direction_score=state.iwm_direction_score,
                trend_alignment_score=state.trend_alignment_score,
                risk_on_score=state.risk_on_score,
                qqq_leadership_score=state.qqq_leadership_score,
                iwm_confirmation_score=state.iwm_confirmation_score,
            )
            # Same write-time assertion as _persist, opposite direction —
            # the cross-symbol row must populate the cross-symbol group
            # and leave the per-symbol group NULL.
            assert row.spy_direction_score is not None and row.trend_score is None, (
                "cross-symbol MarketStateHistory row must populate the cross-symbol "
                "score columns and leave the per-symbol columns NULL"
            )
            session.add(row)
            session.commit()
        except Exception:  # noqa: BLE001 — same soft-fail posture as LevelInteractionEngine/CandleRecorder
            logger.exception("MarketStateEngine failed to persist cross-symbol state")
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
