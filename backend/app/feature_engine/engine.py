"""
FeatureEngine — Phase 4 kickoff. Computes SMA from persisted/live 1m candles
and publishes it as FeaturesUpdated, extending Feature Engine from the
frontend-only, view-only concept it's been through Phase 3 (confirmed
decisions #40/#41; frontend/src/indicators/sma.ts stays exactly what it
was — a chart overlay nothing downstream reads) into the one canonical
backend feature source Strategy/Decision Engine, and first up the planned
Level Interaction Engine, are meant to read (system-design.md §1's
"single canonical Feature Engine path" non-goal boundary; principle 8,
"compute once, consume everywhere").

Scoped to 1m only in this pass: CandleClosed only ever fires for 1m today
(TickIngestBridge's fixed bucket size — see candle_recorder.py's own
docstring); 5m/15m/1h are derived read-side only (candle_aggregator.py),
with no equivalent "aggregated candle closed" event yet. Extending SMA to
those timeframes is real, deliberate follow-up work for whenever the Level
Interaction Engine needs it — not silently assumed to already work. A
CandleClosed for any other timeframe is ignored, not an error.

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
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.feature_engine.indicators import sma
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.features import FeatureSet
from app.services import candle_store

logger = logging.getLogger(__name__)

# CandleClosed only ever fires for "1m" today (see module docstring) — the
# one timeframe this engine computes against in this pass.
SUPPORTED_TIMEFRAME = "1m"


class FeatureEngine:
    def __init__(
        self,
        bus: EventBus,
        sma_periods: list[int] | None = None,
    ) -> None:
        self._bus = bus
        self._sma_periods = sma_periods if sma_periods is not None else get_settings().feature_engine_sma_periods
        self._max_period = max(self._sma_periods) if self._sma_periods else 0

        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

        # Per-(symbol, timeframe) rolling state — see module docstring.
        self._windows: dict[tuple[str, str], deque[float]] = {}
        self._last_applied_ts: dict[tuple[str, str], datetime] = {}

    def start(self) -> None:
        self._bus.subscribe(EventType.CANDLE_CLOSED, self._on_candle_closed)
        self._worker_task = asyncio.create_task(self._worker_loop(), name="feature-engine-sma")
        logger.info("FeatureEngine started — computing SMA%s on 1m CandleClosed", self._sma_periods)

    def stop(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            self._worker_task.cancel()
        self._worker_task = None

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
                    result = await asyncio.to_thread(self._compute_one, item)
                    if result is not None:
                        symbol, payload = result
                        await self._bus.publish(make_envelope(EventType.FEATURES_UPDATED, payload, symbol=symbol))
                except Exception:  # noqa: BLE001 — one bad symbol/candle must not stall the other ~100
                    logger.exception("FeatureEngine failed to compute features for %s", item.get("symbol"))
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    # --- computation (runs off-loop via asyncio.to_thread — see module docstring) ---

    def _compute_one(self, item: dict[str, Any]) -> tuple[str, FeatureSet] | None:
        symbol = item["symbol"]
        timeframe = item["timeframe"]
        close = float(item["close"])
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
            return None

        window = self._windows.get(key)
        if window is None:
            # First time this process has seen this symbol+timeframe —
            # backfill prior closes from persisted history (strictly
            # BEFORE this candle_ts, which by definition already finished
            # closing earlier and had time to be recorded — no race with
            # CandleRecorder's write of THIS candle).
            window = deque(maxlen=self._max_period)
            if self._max_period > 1:
                prior = candle_store.get_recent_closes(
                    symbol, timeframe, before=candle_ts, limit=self._max_period - 1, strict_before=True
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

        if not features:
            return None  # warm-up: not enough history yet for ANY configured period

        payload = FeatureSet(timeframe=timeframe, candle_ts=candle_ts, close=close, features=features)
        return symbol, payload
