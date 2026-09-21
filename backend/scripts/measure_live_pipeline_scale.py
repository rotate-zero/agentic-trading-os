"""
Phase 4 scale/load measurement harness (temp id: phase4-scale-load-measurement).

Investigation only — see docs/decisions/confirmed-decisions.md's
phase4-scale-load-measurement entry for the full writeup, and
docs/roadmap/phase-roadmap.md's Phase 4 row for how this changed its
exit-criterion wording. This script implements NO fix and picks NO
option; it measures.

WHAT THIS MEASURES
-------------------
Phase 4's exit criterion is "100-symbol universe streaming with a
FeatureSet published per symbol, no dropped ticks." Nothing in the repo
had ever measured that before this. Tracing the live wiring (see the
decision entry's diagrams) shows every stage from CandleClosed onward —
FeatureEngine, LevelInteractionEngine, MarketStateEngine — is ONE
asyncio worker draining an unbounded asyncio.Queue with per-item
asyncio.to_thread DB/CPU work; MarketStateEngine additionally runs one
DebounceScheduler (1.0s floor / 10s-4s ceiling, decision #10/#155) per
symbol. So "dropped ticks" cannot come from queue overflow (nothing here
has a maxsize) — the real question is BACKLOG/LAG: whether the pipeline
drains a same-second burst of N symbols before the next one arrives, 60
real seconds later in production.

This script exercises the REAL stage objects (not mocks), wired in the
same classes/order main.py's lifespan uses, against a real scratch
Postgres 16 database, with synthetic ticks/candles standing in for a
real feed. It ramps N = 1, 10, 25, 50, 100 synthetic symbols and reports
drain time, per-stage queue depth over time, and event coverage —
without implementing any fix.

SAFETY
------
Refuses to run unless POSTGRES_DB (per app.core.config.Settings) has
"scratch" in its name — this script TRUNCATEs application tables between
ramp steps and must never be pointed at a dev/prod database.

USAGE
-----
    # one-time setup (see TESTING.md for full commands):
    createdb -O trading trading_scale_scratch
    POSTGRES_DB=trading_scale_scratch alembic upgrade head

    POSTGRES_DB=trading_scale_scratch python scripts/measure_live_pipeline_scale.py
    POSTGRES_DB=trading_scale_scratch python scripts/measure_live_pipeline_scale.py --ramp 1,10,25,50,100 --bursts 16
    POSTGRES_DB=trading_scale_scratch python scripts/measure_live_pipeline_scale.py --steady-state-n 100 --steady-state-bursts 3

Writes a JSON results file (default: scale_measurement_results.json in
the current directory) and prints a human-readable summary table. Not
collected by pytest — no suite-time impact (confirmed: this file matches
no test_*.py/ *_test.py glob pytest.ini uses).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Same sys.path fix test_scanner_pipeline.py's own docstring documents
# finding necessary — `python scripts/foo.py` puts scripts/ on sys.path,
# not backend/, so `from app...` fails without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.broker_adapters.base import MarketDataProvider, Tick  # noqa: E402
from app.context_engine.engine import ContextEngine  # noqa: E402
from app.context_engine.fundamentals_refresh import FundamentalsRefreshJobs  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.event_bus.bus import EventBus  # noqa: E402
from app.event_bus.events import make_envelope  # noqa: E402
from app.feature_engine.engine import FeatureEngine  # noqa: E402
from app.market_state_engine.engine import MarketStateEngine  # noqa: E402
from app.schemas.events.envelope import EventEnvelope, EventType  # noqa: E402
from app.schemas.events.market_data import CandleClosed  # noqa: E402
from app.services import broker_registry  # noqa: E402
from app.services.candle_recorder import CandleRecorder  # noqa: E402
from app.services.live_tick_relay import LiveTickRelay  # noqa: E402
from app.services.tick_ingest import TickIngestBridge  # noqa: E402
from app.strategy_engine.scheduler import StrategyScheduler  # noqa: E402
from app.trading_intelligence.level_interaction_engine import LevelInteractionEngine  # noqa: E402
from app.trading_intelligence.opportunity_cache import OpportunityCache  # noqa: E402

logger = logging.getLogger("measure_live_pipeline_scale")

ET = ZoneInfo("America/New_York")
# Monday 2026-01-05, 09:30 ET — regular NYSE session open, not a holiday
# or half-day per app/core/market_clock.py's own _HOLIDAYS_2026/
# _HALF_DAYS_2026 (checked directly against that file, not assumed).
# Session-local 5m/15m/1h buckets anchor off session open (candle_
# aggregator.py), so starting exactly there gives clean, predictable
# boundary math: 5m completes at :34/:44/..., 15m at :44.
SESSION_OPEN = datetime(2026, 1, 5, 9, 30, tzinfo=ET)

# Mirrors MarketStateEngine's own _MIN_INTERVAL_SECONDS
# (app/market_state_engine/engine.py) — the per-symbol DebounceScheduler
# floor (decision #10/#155). Used only to bound this harness's own
# post-drain settle wait; not imported from that module (a private
# name) since a local, documented constant makes the harness's own
# timeout assumption explicit rather than silently inheriting whatever
# that module currently sets it to.
_MIN_DEBOUNCE_INTERVAL_S = 1.0


# ---------------------------------------------------------------------------
# Safety guard — never a dev/prod database (file boundary / SCOPE requirement)
# ---------------------------------------------------------------------------

def _require_scratch_db() -> None:
    settings = get_settings()
    if "scratch" not in settings.postgres_db.lower():
        print(
            f"REFUSING TO RUN: POSTGRES_DB={settings.postgres_db!r} does not contain "
            "'scratch'. This script TRUNCATEs application tables between ramp steps "
            "and must only ever point at a disposable scratch database. Set "
            "POSTGRES_DB to something like trading_scale_scratch and re-run.",
            file=sys.stderr,
        )
        sys.exit(2)
    logger.info("Scratch DB guard passed: POSTGRES_DB=%s", settings.postgres_db)


def _reset_scratch_db() -> None:
    """Truncates every application table this harness's live path can
    write to, between ramp steps — cheaper than dropping/recreating the
    whole database and migrating again, and gives each N its own clean
    slate (no symbol_id collisions, no stale daily_levels_state/
    market_state_history rows from a previous N). backtests/
    strategy_outcomes are untouched — nothing in this harness's live
    path writes them.
    """
    session = SessionLocal()
    try:
        session.execute(
            text(
                """
                TRUNCATE TABLE
                    level_interaction_events,
                    level_interaction_state,
                    market_state_history,
                    daily_levels_state,
                    symbol_fundamentals,
                    scanner_universe_symbols,
                    candles,
                    symbols
                RESTART IDENTITY CASCADE
                """
            )
        )
        session.commit()
    finally:
        session.close()


def _seed_scanner_universe(symbols: list[str]) -> None:
    """Inserts each synthetic symbol into `symbols` + `scanner_universe_
    symbols` — the SAME table ContextEngine._bootstrap_symbol_loops()
    reads on start() to decide which per-symbol context loops to spin up
    (app/context_engine/engine.py). Without this, ContextEngine never
    calls evaluate_for_symbol() for a symbol this harness invents, so
    StrategyScheduler._read_context() would see `context is None` for
    every synthetic symbol and skip strategy evaluation entirely —
    silently exercising a shorter path than main.py's real wiring would
    for a genuinely scanner-tracked symbol. Seeding this table is the
    one piece of DB setup this harness does beyond what publishing
    events itself requires, and it uses the real ORM models, not a
    parallel schema assumption.
    """
    from app.models.market_data import Symbol
    from app.models.scanner import ScannerUniverseSymbol

    session = SessionLocal()
    try:
        for ticker in symbols:
            symbol_row = Symbol(ticker=ticker, is_backtest=False)
            session.add(symbol_row)
            session.flush()
            session.add(ScannerUniverseSymbol(symbol_id=symbol_row.id))
        session.commit()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Synthetic tick provider — satisfies MarketDataProvider, used only by
# TickIngestBridge's constructor (`provider.on_tick(self._on_tick)`).
# Every method besides on_tick()/push() is a harness-only stub; nothing
# under test calls them.
# ---------------------------------------------------------------------------

class SyntheticProvider(MarketDataProvider):
    def __init__(self) -> None:
        self._callbacks: list = []

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    def is_connected(self) -> bool:
        return True

    async def subscribe(self, symbols: list[str]) -> None:
        pass

    async def unsubscribe(self, symbols: list[str]) -> None:
        pass

    async def get_historical(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list:
        return []

    def on_tick(self, callback) -> None:
        self._callbacks.append(callback)

    def push(self, tick: Tick) -> None:
        for cb in self._callbacks:
            cb(tick)


# ---------------------------------------------------------------------------
# Observation: a wildcard Event Bus subscriber (EventBus.subscribe_all(),
# a real, already-existing public API — the WebSocket Gateway is its
# production caller) recording every event's wall-clock arrival. This is
# the harness's ONLY hook into the pipeline; nothing under
# backend/app/** is modified to make this measurement possible.
# ---------------------------------------------------------------------------

@dataclass
class RecordedEvent:
    t_wall: float
    event_type: str
    symbol: str | None
    candle_ts: str | None
    timeframe: str | None


class EventRecorder:
    def __init__(self) -> None:
        self.events: list[RecordedEvent] = []

    def handler(self, envelope: EventEnvelope) -> None:
        self.events.append(
            RecordedEvent(
                t_wall=time.monotonic(),
                event_type=str(envelope.event_type),
                symbol=envelope.symbol,
                candle_ts=envelope.payload.get("candle_ts"),
                timeframe=envelope.payload.get("timeframe"),
            )
        )

    def count(self, event_type: str) -> int:
        return sum(1 for e in self.events if e.event_type == event_type)

    def by_type(self, event_type: str) -> list[RecordedEvent]:
        return [e for e in self.events if e.event_type == event_type]

    def by_type_symbol(self, event_type: str, symbol: str) -> list[RecordedEvent]:
        return [e for e in self.events if e.event_type == event_type and e.symbol == symbol]


async def _join_with_timeout(queue: asyncio.Queue, *, timeout: float = 120.0) -> tuple[bool, float]:
    """await queue.join() — returns only once every item ever put() has
    had task_done() called on it, INCLUDING whatever's currently mid-
    to_thread inside the worker loop (not just qsize()==0, which only
    reflects items still WAITING, not the one already dequeued and being
    processed). The same primitive MarketStateEngine.settle_replay()
    itself already uses internally (market_state_engine/engine.py) for
    exactly this "has this engine truly finished" question — reused
    here, not reinvented. Returns (finished_before_timeout, wall_seconds).
    """
    start = time.monotonic()
    try:
        await asyncio.wait_for(queue.join(), timeout=timeout)
        return True, time.monotonic() - start
    except asyncio.TimeoutError:
        logger.warning("queue.join() timed out after %.1fs (qsize=%d)", timeout, queue.qsize())
        return False, time.monotonic() - start


async def _wait_until_quiet(
    get_count, *, quiet_polls: int = 6, poll_interval: float = 0.05, timeout: float = 120.0
) -> tuple[int, float]:
    """Polls get_count() until it stops changing for `quiet_polls`
    consecutive polls (pipeline has drained), or `timeout` real seconds
    elapse. Returns (final_count, wall_seconds_elapsed). Deliberately
    polling, not a queue.join() — this harness needs to observe MULTIPLE
    independent queues/stages converge, not just one.
    """
    start = time.monotonic()
    last = -1
    stable = 0
    while True:
        cur = get_count()
        if cur == last:
            stable += 1
            if stable >= quiet_polls:
                return cur, time.monotonic() - start
        else:
            stable = 0
        last = cur
        if time.monotonic() - start > timeout:
            logger.warning("wait_until_quiet timed out after %.1fs at count=%d", timeout, cur)
            return cur, time.monotonic() - start
        await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Stage A — tick ingestion (SCOPE 2a): synthetic provider callback ->
# TickIngestBridge -> PriceUpdated (-> LiveTickRelay, respecting its
# active-symbol gating).
# ---------------------------------------------------------------------------

async def _run_tick_stage(
    provider: SyntheticProvider,
    bus: EventBus,
    recorder: EventRecorder,
    symbols: list[str],
    tick_relay: LiveTickRelay,
    *,
    ticks_per_minute: int,
    minutes: int,
) -> dict[str, Any]:
    n = len(symbols)
    price_updated_before = recorder.count("PriceUpdated")
    candle_closed_before = recorder.count("CandleClosed")

    t0 = time.monotonic()
    for m in range(minutes):
        for sym in symbols:
            for t in range(ticks_per_minute):
                exchange_ts = SESSION_OPEN + timedelta(minutes=m, seconds=t * (60 // max(ticks_per_minute, 1)))
                price = 100.0 + 0.01 * (hash((sym, m, t)) % 50)
                provider.push(Tick(symbol=sym, price=price, size=100, exchange_ts=exchange_ts))
        await asyncio.sleep(0)  # let the create_task'd _handle_tick coroutines actually run
    # One closing tick per symbol, one synthetic minute later, to force
    # TickIngestBridge's rollover check to publish the final minute's
    # CandleClosed via the tick's own exchange_ts — not real wall-clock
    # time (see tick_ingest.py's _handle_tick: the rollover compares
    # tick.exchange_ts, not datetime.now()). The safety-net _flush_loop
    # is real-wall-clock-driven and deliberately NOT relied on here.
    for sym in symbols:
        exchange_ts = SESSION_OPEN + timedelta(minutes=minutes)
        provider.push(Tick(symbol=sym, price=100.0, size=100, exchange_ts=exchange_ts))

    push_wall_s = time.monotonic() - t0

    expected_price_updated = n * (minutes * ticks_per_minute + 1)
    price_updated_final, price_updated_drain_s = await _wait_until_quiet(
        lambda: recorder.count("PriceUpdated") - price_updated_before
    )
    expected_candle_closed = n * minutes
    candle_closed_final, candle_closed_drain_s = await _wait_until_quiet(
        lambda: recorder.count("CandleClosed") - candle_closed_before
    )

    # LiveTickRelay active-symbol gating: flush_interval defaults to 5s
    # of REAL wall clock (its _flush_loop uses asyncio.sleep(5.0), not
    # synthetic time) — wait one real cycle so any PriceSnapshot for the
    # active subset actually gets published, then check no inactive
    # symbol ever got one.
    price_snapshot_cutoff = time.monotonic()
    await asyncio.sleep(tick_relay._flush_interval_seconds + 0.3)
    snapshots = [e for e in recorder.by_type("PriceSnapshot") if e.t_wall >= price_snapshot_cutoff]
    snapshot_symbols = {e.symbol for e in snapshots}
    active_set = set(tick_relay.get_active_symbols())
    gating_violation = snapshot_symbols - active_set

    return {
        "n_symbols": n,
        "ticks_per_minute": ticks_per_minute,
        "minutes": minutes,
        "ticks_pushed": n * (minutes * ticks_per_minute + 1),
        "push_wall_s": push_wall_s,
        "price_updated_expected": expected_price_updated,
        "price_updated_observed": price_updated_final,
        "price_updated_drain_wall_s": price_updated_drain_s,
        "candle_closed_expected": expected_candle_closed,
        "candle_closed_observed": candle_closed_final,
        "candle_closed_drain_wall_s": candle_closed_drain_s,
        "active_symbols": sorted(active_set),
        "price_snapshot_symbols_observed": sorted(snapshot_symbols),
        "price_snapshot_gating_violation": sorted(gating_violation),
    }


# ---------------------------------------------------------------------------
# Stage B — candle burst (SCOPE 2b): K >= 10 consecutive synthetic
# minutes, all N CandleClosed published at once per minute, candle_ts
# advancing one minute per burst.
# ---------------------------------------------------------------------------

async def _run_candle_burst_stage(
    bus: EventBus,
    recorder: EventRecorder,
    feature_engine: FeatureEngine,
    level_engine: LevelInteractionEngine,
    market_state_engine: MarketStateEngine,
    symbols: list[str],
    *,
    burst_count: int,
    start_ts: datetime,
) -> dict[str, Any]:
    n = len(symbols)
    fe_before = recorder.count("FeaturesUpdated")
    li_before = recorder.count("LevelInteractionChanged")
    ms_before = recorder.count("MarketStateChanged")

    bursts: list[dict[str, Any]] = []
    t_start = time.monotonic()
    for k in range(burst_count):
        candle_ts = start_ts + timedelta(minutes=k)
        # Depths BEFORE publishing this burst show whatever backlog the
        # PREVIOUS burst left behind in each stage's own queue — this is
        # "whether backlog remains when burst k+1 starts" (SCOPE 2b).
        # Meaningful only because of the asyncio.sleep(0) at the end of
        # this loop body: an unbounded asyncio.Queue's put() never
        # actually suspends the coroutine (CPython's Queue.put_nowait()
        # short-circuit — confirmed by reading asyncio's own queues.py,
        # not assumed), so a tight loop of bare `await bus.publish(...)`
        # calls with no yield point never lets the Event Bus's own
        # consumer task, or any engine's worker task, run AT ALL between
        # bursts — an earlier version of this harness measured all-zero
        # backlog at every burst for exactly this reason, which was the
        # harness never actually giving the pipeline a chance to run
        # between bursts, not the pipeline draining instantly. Fixed by
        # yielding once per burst below.
        depths_before = {
            "feature_engine_qsize": feature_engine._queue.qsize(),
            "level_engine_qsize": level_engine._queue.qsize(),
            "market_state_qsize": market_state_engine._queue.qsize(),
        }
        burst_t0 = time.monotonic()
        for sym in symbols:
            base = 100.0 + 0.01 * k + 0.001 * (hash(sym) % 23)
            payload = CandleClosed(
                timeframe="1m",
                open=base,
                high=base + 0.05,
                low=base - 0.05,
                close=base + 0.02,
                volume=1000 + k,
                candle_ts=candle_ts,
            )
            await bus.publish(make_envelope(EventType.CANDLE_CLOSED, payload, symbol=sym))
        publish_wall_s = time.monotonic() - burst_t0
        # One real scheduler turn — long enough for the bus's normal-lane
        # consumer task and each engine's worker task to run as far as
        # they can before yielding themselves (their own `await
        # queue.get()` / `await asyncio.to_thread(...)` calls), short
        # enough that K bursts still complete in a small fraction of a
        # real second, not K real minutes.
        await asyncio.sleep(0)
        bursts.append(
            {
                "k": k,
                "candle_ts": candle_ts.isoformat(),
                "publish_wall_s": publish_wall_s,
                **{f"{key}_before_this_burst": val for key, val in depths_before.items()},
            }
        )

    total_publish_wall_s = time.monotonic() - t_start

    # Authoritative drain detection: queue.join() (see _join_with_timeout)
    # rather than polling published-event counts. Necessary in particular
    # for LevelInteractionEngine, which does NOT publish one event per
    # item processed (LevelInteractionChanged only fires on an actual
    # zone TRANSITION — verified directly against level_interaction_
    # state's own updated_at timestamps during this harness's own smoke
    # test, see the decision entry) — an event-count-based "quiet" check
    # for that engine can declare victory while real backlog remains.
    fe_joined, features_drain_s = await _join_with_timeout(feature_engine._queue)
    li_joined, level_drain_s = await _join_with_timeout(level_engine._queue)
    ms_joined, market_drain_s = await _join_with_timeout(market_state_engine._queue)
    # A small settle margin after join() so the FINAL published event(s)
    # for the last-processed item are guaranteed to have already landed
    # in `recorder` (join() returns the instant task_done() is called,
    # which is in the same worker-loop iteration as, but not provably
    # after, the bus.publish() for that item's output).
    await asyncio.sleep(0.05)
    features_final = recorder.count("FeaturesUpdated") - fe_before
    level_final = recorder.count("LevelInteractionChanged") - li_before

    # MarketStateEngine needs one more wait, specifically: its own
    # `_queue` only ever holds items a DebounceScheduler already decided
    # to run — queue.join() says nothing about a symbol's PENDING
    # catch-up task (scheduled via bare asyncio.create_task, outside
    # this queue, up to `min_interval` REAL seconds after that symbol's
    # last trigger — debounce_scheduler.py's `_run_after_delay`) that
    # hasn't fired yet. Bounded event-count settle (not a queue this
    # engine owns) — MarketStateChanged, unlike LevelInteractionChanged,
    # DOES publish exactly once per actual recompute, so this proxy is
    # sound here even though it wasn't for LevelInteractionEngine above.
    market_settle_count, _ = await _wait_until_quiet(
        lambda: recorder.count("MarketStateChanged") - ms_before,
        quiet_polls=8,
        poll_interval=0.1,
        timeout=_MIN_DEBOUNCE_INTERVAL_S + 1.0,
    )
    market_final = max(market_settle_count, recorder.count("MarketStateChanged") - ms_before)

    # Coverage: per-symbol count of 1m FeaturesUpdated actually produced
    # for THIS burst run (expect exactly `burst_count` per symbol — one
    # per candle, no debounce on FeatureEngine).
    fe_events_this_run = [e for e in recorder.by_type("FeaturesUpdated") if e.t_wall >= t_start]
    per_symbol_1m_counts = defaultdict(int)
    per_symbol_all_tf_counts = defaultdict(int)
    for e in fe_events_this_run:
        per_symbol_all_tf_counts[e.symbol] += 1
        if e.timeframe == "1m":
            per_symbol_1m_counts[e.symbol] += 1
    coverage_1m = [per_symbol_1m_counts.get(sym, 0) for sym in symbols]

    ms_events_this_run = [e for e in recorder.by_type("MarketStateChanged") if e.t_wall >= t_start]
    per_symbol_ms_counts = defaultdict(int)
    for e in ms_events_this_run:
        per_symbol_ms_counts[e.symbol] += 1
    coverage_ms = [per_symbol_ms_counts.get(sym, 0) for sym in symbols]

    # Last-symbol timing: from the LAST burst's publish-loop completion
    # to the LAST FeaturesUpdated(1m)/MarketStateChanged/
    # LevelInteractionChanged carrying that same final candle_ts,
    # across ANY symbol — i.e. how long until every symbol's output for
    # the final synthetic minute has actually landed.
    last_burst_done_wall = t_start + sum(b["publish_wall_s"] for b in bursts)
    last_candle_ts_iso = (start_ts + timedelta(minutes=burst_count - 1)).isoformat()

    def _last_event_offset(event_type: str, timeframe: str | None = None) -> float | None:
        matches = [
            e
            for e in recorder.by_type(event_type)
            if e.t_wall >= t_start
            and e.candle_ts is not None
            and e.candle_ts.startswith(last_candle_ts_iso[:16])  # minute-resolution match
            and (timeframe is None or e.timeframe == timeframe)
        ]
        if not matches:
            return None
        return max(e.t_wall for e in matches) - last_burst_done_wall

    return {
        "n_symbols": n,
        "burst_count": burst_count,
        "start_candle_ts": start_ts.isoformat(),
        "end_candle_ts": last_candle_ts_iso,
        "total_publish_wall_s": total_publish_wall_s,
        "bursts": bursts,
        "features_updated_count": features_final,
        "features_updated_expected_min_1m_only": n * burst_count,
        "features_drain_wall_s": features_drain_s,
        "level_interaction_count": level_final,
        "level_interaction_drain_wall_s": level_drain_s,
        "market_state_changed_count": market_final,
        "market_state_changed_expected_upper_bound": n * burst_count,
        "market_state_drain_wall_s": market_drain_s,
        "coverage_1m_features_per_symbol": {
            "min": min(coverage_1m) if coverage_1m else None,
            "max": max(coverage_1m) if coverage_1m else None,
            "mean": statistics.fmean(coverage_1m) if coverage_1m else None,
            "symbols_at_full_coverage": sum(1 for c in coverage_1m if c == burst_count),
            "symbols_below_full_coverage": sum(1 for c in coverage_1m if c < burst_count),
        },
        "coverage_market_state_per_symbol": {
            "min": min(coverage_ms) if coverage_ms else None,
            "max": max(coverage_ms) if coverage_ms else None,
            "mean": statistics.fmean(coverage_ms) if coverage_ms else None,
            "note": (
                "MarketStateEngine debounces (1.0s floor/symbol, decision #10/#155) — "
                "a value below burst_count here is EXPECTED under a fast synthetic burst "
                "and reflects intentional coalescing, not a drop. See decision entry."
            ),
        },
        "last_symbol_offset_from_last_burst_publish_s": {
            "features_updated_1m": _last_event_offset("FeaturesUpdated", "1m"),
            "level_interaction_changed": _last_event_offset("LevelInteractionChanged"),
            "market_state_changed": _last_event_offset("MarketStateChanged"),
        },
        "drain_vs_60s_deadline": {
            "features_drain_within_60s": fe_joined and features_drain_s <= 60.0,
            "level_interaction_drain_within_60s": li_joined and level_drain_s <= 60.0,
            "market_state_drain_within_60s": ms_joined and market_drain_s <= 60.0,
        },
        "queue_join_completed_before_timeout": {
            "feature_engine": fe_joined,
            "level_engine": li_joined,
            "market_state_engine": ms_joined,
        },
    }


# ---------------------------------------------------------------------------
# Per-N orchestration — constructs the real stage objects in the same
# classes/order main.py's lifespan() uses (see that function; the
# FastAPI app, WebSocket Gateway, and provider auto-connect are not part
# of the pipeline under test and are omitted). Fresh instances per N,
# not the get_xxx() singletons main.py itself uses — main.py's
# singleton caching exists so route handlers reach the SAME
# cross-request instance; it is not part of the wiring itself, and
# reusing it across ramp steps would leak in-memory state (rolling
# windows, per-symbol debounce schedulers, daily-levels cache) between
# different N values, biasing the timing this script exists to measure.
# ---------------------------------------------------------------------------

async def run_pipeline_for(
    n_symbols: int,
    *,
    burst_count: int,
    tick_minutes: int,
    ticks_per_minute: int,
) -> dict[str, Any]:
    logger.info("=== N=%d: resetting scratch DB and seeding scanner_universe ===", n_symbols)
    _reset_scratch_db()
    symbols = [f"SYN{idx:04d}" for idx in range(n_symbols)]
    _seed_scanner_universe(symbols)

    bus = EventBus()
    await bus.start()
    recorder = EventRecorder()
    bus.subscribe_all(recorder.handler)

    candle_recorder = CandleRecorder(bus)
    candle_recorder.start()

    tick_relay = LiveTickRelay(bus)
    tick_relay.start()
    active = symbols[: min(8, n_symbols)]
    tick_relay.set_active_symbols(active)

    feature_engine = FeatureEngine(bus)
    feature_engine.start()

    level_engine = LevelInteractionEngine(bus)
    level_engine.start()

    market_state_engine = MarketStateEngine(bus)  # is_backtest=False default — the live namespace
    market_state_engine.start()

    context_engine = ContextEngine(bus)
    context_engine.start()
    # ContextEngine._bootstrap_symbol_loops() is itself asyncio.to_thread
    # + asyncio.create_task per symbol — give it a moment to read
    # scanner_universe_symbols and fire each symbol's first
    # evaluate_for_symbol() before Stage B needs context() populated.
    await asyncio.sleep(0.5)

    strategy_scheduler = StrategyScheduler(bus)
    strategy_scheduler.start()

    opportunity_cache = OpportunityCache(bus)
    opportunity_cache.start()

    fundamentals_jobs = FundamentalsRefreshJobs()  # no Finnhub key in this harness — soft no-op start(), matches main.py's own posture
    fundamentals_jobs.start()

    provider = SyntheticProvider()
    bridge = TickIngestBridge(provider, bus)

    assert broker_registry.get_historical_provider() is None, (
        "harness invariant violated: no historical provider should be registered — "
        "Daily Levels must take the honest no-provider no-op path, not a real fetch"
    )

    try:
        stage_a = await _run_tick_stage(
            provider,
            bus,
            recorder,
            symbols,
            tick_relay,
            ticks_per_minute=ticks_per_minute,
            minutes=tick_minutes,
        )
        # Stop the bridge's real-wall-clock flush loop right after Stage A,
        # before Stage B starts — Stage B publishes CandleClosed directly
        # and never needs the bridge again. Found empirically, not assumed:
        # TickIngestBridge._flush_loop's stale-bucket check compares
        # bucket.minute_ts against REAL datetime.now(timezone.utc)
        # (tick_ingest.py's own module docstring). Stage A's closing tick
        # deliberately leaves one bucket open per symbol at a SYNTHETIC
        # 2026-01-05 timestamp; when this harness's real wall-clock crosses
        # a real minute boundary mid-run, that safety net (correctly, by
        # its own design) sees a "stale" bucket — Jan 2026 is always less
        # than the real current minute — and force-publishes it, producing
        # a spurious CandleClosed that FeatureEngine's real duplicate/
        # out-of-order guard then correctly rejects (harmless, but noisy,
        # and irrelevant to what this harness measures). Never observed
        # in production, where candle_ts always tracks real time.
        bridge.stop()

        # Stage B's candle_ts starts strictly after Stage A's own ticks
        # (tick_minutes + 1 synthetic minutes consumed, including the
        # closing tick) — keeps every CandleClosed candle_ts strictly
        # increasing per symbol, so FeatureEngine's duplicate/out-of-
        # order guard (engine.py, module docstring) never fires as a
        # false positive against this harness's own earlier stage.
        stage_b_start = SESSION_OPEN + timedelta(minutes=tick_minutes + 1)
        stage_b = await _run_candle_burst_stage(
            bus,
            recorder,
            feature_engine,
            level_engine,
            market_state_engine,
            symbols,
            burst_count=burst_count,
            start_ts=stage_b_start,
        )
    finally:
        # Same shutdown order as main.py's lifespan finally block.
        for provider_obj in broker_registry.get_all_active_providers():
            await provider_obj.disconnect()
        await context_engine.stop()
        await fundamentals_jobs.stop()
        await bus.stop()
        await candle_recorder.stop()
        await tick_relay.stop()
        await feature_engine.stop()
        await level_engine.stop()
        await market_state_engine.stop()
        await strategy_scheduler.stop()
        await opportunity_cache.stop()
        bridge.stop()

    return {
        "n_symbols": n_symbols,
        "stage_a_tick_ingestion": stage_a,
        "stage_b_candle_burst": stage_b,
        "total_events_recorded": len(recorder.events),
    }


# ---------------------------------------------------------------------------
# Optional real-wall-clock steady-state sanity check (SCOPE 2c) — NOT
# accelerated: publishes `bursts` candles spaced by real time.sleep-free
# asyncio.sleep(interval_s), default one real minute apart, at a single
# N. Off by default — explicitly opt-in via --steady-state-n because it
# costs real wall-clock minutes to run.
# ---------------------------------------------------------------------------

async def run_steady_state_check(n_symbols: int, bursts: int, interval_s: float) -> dict[str, Any]:
    logger.info("=== steady-state check: N=%d, %d bursts, %.0fs apart (real time) ===", n_symbols, bursts, interval_s)
    _reset_scratch_db()
    symbols = [f"SYN{idx:04d}" for idx in range(n_symbols)]
    _seed_scanner_universe(symbols)

    bus = EventBus()
    await bus.start()
    recorder = EventRecorder()
    bus.subscribe_all(recorder.handler)

    candle_recorder = CandleRecorder(bus)
    candle_recorder.start()
    feature_engine = FeatureEngine(bus)
    feature_engine.start()
    level_engine = LevelInteractionEngine(bus)
    level_engine.start()
    market_state_engine = MarketStateEngine(bus)
    market_state_engine.start()
    context_engine = ContextEngine(bus)
    context_engine.start()
    await asyncio.sleep(0.5)
    strategy_scheduler = StrategyScheduler(bus)
    strategy_scheduler.start()
    opportunity_cache = OpportunityCache(bus)
    opportunity_cache.start()

    start_ts = SESSION_OPEN
    per_burst_drain: list[dict[str, Any]] = []
    try:
        for k in range(bursts):
            candle_ts = start_ts + timedelta(minutes=k)
            fe_before = recorder.count("FeaturesUpdated")
            t0 = time.monotonic()
            for sym in symbols:
                base = 100.0 + 0.01 * k
                payload = CandleClosed(
                    timeframe="1m", open=base, high=base + 0.05, low=base - 0.05,
                    close=base + 0.02, volume=1000, candle_ts=candle_ts,
                )
                await bus.publish(make_envelope(EventType.CANDLE_CLOSED, payload, symbol=sym))
            _, drain_s = await _wait_until_quiet(lambda: recorder.count("FeaturesUpdated") - fe_before, timeout=interval_s)
            per_burst_drain.append({"k": k, "publish_to_drain_s": time.monotonic() - t0, "drain_s": drain_s})
            if k < bursts - 1:
                await asyncio.sleep(max(0.0, interval_s - (time.monotonic() - t0)))
    finally:
        await context_engine.stop()
        await bus.stop()
        await candle_recorder.stop()
        await feature_engine.stop()
        await level_engine.stop()
        await market_state_engine.stop()
        await strategy_scheduler.stop()
        await opportunity_cache.stop()

    return {"n_symbols": n_symbols, "bursts": bursts, "interval_s": interval_s, "per_burst": per_burst_drain}


# ---------------------------------------------------------------------------
# CLI / reporting
# ---------------------------------------------------------------------------

def _print_summary(results: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 100)
    print("PHASE 4 SCALE/LOAD MEASUREMENT — SUMMARY (synthetic input, accelerated candle_ts)")
    print("=" * 100)
    header = (
        f"{'N':>5} | {'FeatUpd':>8} | {'FeatDrain(s)':>13} | {'LvlInt':>7} | {'LvlDrain(s)':>12} | "
        f"{'MktSt':>6} | {'MktDrain(s)':>12} | {'1m full-cov':>11} | {'<=60s all?':>10}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        b = r["stage_b_candle_burst"]
        cov = b["coverage_1m_features_per_symbol"]
        deadline = b["drain_vs_60s_deadline"]
        all_within = all(deadline.values())
        print(
            f"{r['n_symbols']:>5} | {b['features_updated_count']:>8} | {b['features_drain_wall_s']:>13.3f} | "
            f"{b['level_interaction_count']:>7} | {b['level_interaction_drain_wall_s']:>12.3f} | "
            f"{b['market_state_changed_count']:>6} | {b['market_state_drain_wall_s']:>12.3f} | "
            f"{cov['symbols_at_full_coverage']:>4}/{r['n_symbols']:<6} | {'yes' if all_within else 'NO':>10}"
        )
    print("=" * 100 + "\n")


async def _amain(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("measure_live_pipeline_scale").setLevel(logging.INFO)

    _require_scratch_db()

    ramp = [int(x) for x in args.ramp.split(",") if x.strip()]
    results = []
    wall_start = time.monotonic()
    for n in ramp:
        result = await run_pipeline_for(
            n,
            burst_count=args.bursts,
            tick_minutes=args.tick_minutes,
            ticks_per_minute=args.ticks_per_minute,
        )
        results.append(result)
        logger.info("N=%d complete (%.1fs elapsed total)", n, time.monotonic() - wall_start)

    steady_state = None
    if args.steady_state_n:
        steady_state = await run_steady_state_check(
            args.steady_state_n, args.steady_state_bursts, args.steady_state_interval_s
        )

    output = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ramp": ramp,
            "burst_count": args.bursts,
            "postgres_db": get_settings().postgres_db,
        },
        "ramp_results": results,
        "steady_state_check": steady_state,
    }
    out_path = Path(args.output)
    out_path.write_text(json.dumps(output, indent=2, default=str))
    logger.info("Wrote full results to %s", out_path)

    _print_summary(results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ramp", default="1,10,25,50,100", help="comma-separated symbol counts to ramp through")
    parser.add_argument("--bursts", type=int, default=16, help="K consecutive synthetic 1m candles per N (spans a 5m and 15m boundary at K=16 from session open)")
    parser.add_argument("--tick-minutes", type=int, default=3, help="synthetic minutes for the tick-ingestion stage (2a)")
    parser.add_argument("--ticks-per-minute", type=int, default=3, help="ticks per symbol per synthetic minute in stage 2a")
    parser.add_argument("--steady-state-n", type=int, default=0, help="if >0, also run a REAL-wall-clock steady-state check at this N (2c, optional)")
    parser.add_argument("--steady-state-bursts", type=int, default=3, help="number of real-time-spaced bursts for the steady-state check")
    parser.add_argument("--steady-state-interval-s", type=float, default=65.0, help="real seconds between steady-state bursts")
    parser.add_argument("--output", default="scale_measurement_results.json", help="path to write full JSON results")
    args = parser.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
