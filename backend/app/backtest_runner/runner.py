"""
BacktestRunner — Unit 4. Ties `fixture_provider.py`/`context_provider.py`
(Unit 1), `replay_state_producer.py`/`engine_singleton_guard.py` (Unit 2),
and `gate_and_warmup.py`/`fill_simulator.py` (Unit 3) into one full run:
replay one symbol's candles for one date range through the real engine
pipeline, evaluate ONE real `Strategy` every candle, simulate fills for
actionable `Opportunity`s, and persist real `StrategyOutcomeRecord` rows
via the real `record_strategy_outcome()` once both halves of D17's
snapshot requirement are satisfied AT THE RIGHT INSTANT.

**The one finding that reshaped this module, worth restating here since
it isn't obvious from the individual pieces.** `schemas/performance.py`
states outright that `market_state_at_entry`/`context_at_entry` are
"captured at `entry_filled_at`, not `setup_detected_at`" — and
`state_snapshot.py`'s own `capture_strategy_outcome_snapshots()` is
documented as the call a real fill handler makes "once, at
`entry_filled_at`, and again, separately, at `exit_filled_at`." That
means the D17 check isn't a pre-condition on the SIGNAL candle (Unit 3's
`gate_and_warmup.check_entry_allowed()` still checks `gate_conditions` at
signal time, unchanged and still correct for that) — it's a pre-condition
on whether the FILL and EXIT candles, once the replay loop actually
reaches them, can produce real snapshots. This module captures at both
of those specific instants, not at the moment `Strategy.evaluate()`
returns an Opportunity.

**Why `Strategy.evaluate()` is called on every replayed candle, even
while a simulated position is open.** Traced from `strategy_engine/
scheduler.py` directly: live, `evaluate()` runs every candle regardless
of open positions — deciding whether to ACT on an Opportunity is a
downstream Decision Engine concern that doesn't exist yet (explicitly
out of scope, blocked on D4). Skipping `evaluate()` while a simulated
trade is open would silently diverge from live behavior and corrupt a
strategy's own per-symbol internal state (ORB's `candles_seen` counter,
concretely) in a way live never does. So: this runner always evaluates;
only the "should this open a new simulated position" decision is gated
by whether one is already open for this symbol — a real, v1-only policy
this module imposes (no portfolio/position-sizing layer exists to allow
more than one), not a live invariant being reproduced.

**Position/exit are both precomputed, not discovered candle-by-candle.**
`fill_simulator.simulate_exit()` is a pure function over an ALREADY
fully-fetched candle list — for a historical replay, that's fine: the
entire outcome of a simulated trade is knowable the instant it's
entered, exactly as any backtest engine works (this does not leak future
information into `Strategy.evaluate()`'s own decision — that already
happened using only contemporaneous, correctly-replayed state). So the
moment an actionable Opportunity is accepted, this module computes the
ENTIRE trade (entry index, exit index, exit reason) up front via
`_PendingTrade`, then waits for the replay loop's own candle-by-candle
advance to naturally reach those two indices before capturing real state
at each and persisting.

**First-use conventions established here (no prior precedent in this
codebase — confirmed by grep before choosing, not guessed):**
  - `opportunity_id`: `Opportunity` itself carries no identity field, and
    `OpportunityCache` doesn't assign one either — this module is the
    first thing to mint one (`uuid4()`, at the moment a signal is
    accepted for simulated entry).
  - `config_hash`: sha256 hex digest of a stable JSON serialization of
    `(gate_conditions, params)` — distinguishes pre-promotion tuning
    variants of the same `strategy_version`, per `BacktestRun.config_hash`'s
    own docstring.
  - `feature_version`: caller-supplied, no default — Feature Engine has
    no versioning scheme of its own yet, so this module doesn't invent
    a silent default a future real-data run could forget to override.
  - `final_stop`/`final_target` (required floats): set equal to
    `structural_invalidation`/`structural_target` — no Trade Planning
    refinement stage exists in v1, so "final" trivially equals
    "structural."
  - `origin="auto"`, `feature_snapshot_id=None` (the `feature_snapshots`
    table doesn't exist anywhere in this codebase — confirmed by grep,
    same finding the schema's own docstring already states),
    `signal_confirmed_at=None`, `decided_at=None` (no confirmation/
    decision stage exists).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4

from app.backtest_runner.context_provider import BacktestContextProvider
from app.backtest_runner.engine_singleton_guard import install_replay_engines
from app.backtest_runner.fill_simulator import (
    EntryFill,
    ExitFill,
    InsufficientReplayDataError,
    compute_realized_pnl,
    compute_realized_r,
    simulate_entry,
    simulate_exit,
)
from app.backtest_runner.replay_state_producer import EngineBackedReplayStateProducer
from app.broker_adapters.base import MarketDataProvider
from app.core.market_clock import MarketClock, get_market_clock
from app.db.session import SessionLocal
from app.models.trading_intelligence import BacktestRunRecord
from app.schemas.performance import StrategyOutcome
from app.strategy_engine.base_strategy import Opportunity, Strategy
from app.strategy_engine.gate_conditions import gate_conditions_satisfied
from app.trading_intelligence.performance import record_strategy_outcome
from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots, capture_strategy_outcome_snapshots

logger = logging.getLogger(__name__)

__all__ = ["BacktestRunner", "BacktestRunResult", "DiscardedSignal"]


@dataclass(frozen=True)
class DiscardedSignal:
    """A signal that was accepted for simulated entry but never became a
    recorded `StrategyOutcome` — honest bookkeeping (§11's "no fabricated
    state" extends to the run's own summary: a discarded signal is
    reported, never silently dropped)."""

    signal_candle_ts: datetime
    reason: str
    detail: str


@dataclass(frozen=True)
class BacktestRunResult:
    run_id: UUID
    sweep_id: UUID
    outcomes_recorded: int
    discarded_signals: list[DiscardedSignal] = field(default_factory=list)


@dataclass
class _PendingTrade:
    """One simulated position, fully precomputed at entry-acceptance
    time (see module docstring). Mutable only to attach the two
    snapshot pairs once the replay loop actually reaches each instant —
    everything else is fixed the moment this is constructed."""

    opportunity: Opportunity
    opportunity_id: UUID
    entry_fill: EntryFill
    exit_fill: ExitFill
    entry_snapshots: StrategyOutcomeSnapshots | None = None


def _compute_config_hash(gate_conditions: dict, params: dict) -> str:
    payload = json.dumps({"gate_conditions": gate_conditions, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _write_backtest_run_record(row: BacktestRunRecord) -> None:
    """Synchronous — same self-contained-session pattern
    `record_strategy_outcome()` uses (own `SessionLocal()`, commit or
    rollback-and-raise, always close). No existing helper to call
    instead: `BacktestRunRecord`'s own docstring says "No Backtest Runner
    writes to this table yet" — this function is genuinely that first
    writer. Lives here, not in `trading_intelligence/performance.py`
    (out of scope to touch), since nothing requires it to live there."""
    session = SessionLocal()
    try:
        session.add(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _build_strategy_outcome(
    *,
    symbol: str,
    strategy_name: str,
    strategy_version: str,
    run_id: UUID,
    pending: _PendingTrade,
    exit_snapshots: StrategyOutcomeSnapshots,
    clock: MarketClock,
) -> StrategyOutcome:
    opportunity = pending.opportunity
    entry_fill = pending.entry_fill
    exit_fill = pending.exit_fill
    entry_snapshots = pending.entry_snapshots
    assert entry_snapshots is not None and entry_snapshots.market_state is not None and entry_snapshots.context is not None
    assert exit_snapshots.market_state is not None and exit_snapshots.context is not None

    entry_qty = exit_qty = 1.0  # v1: no position sizing — see fill_simulator.py's own module docstring
    return StrategyOutcome(
        outcome_id=uuid4(),
        opportunity_id=pending.opportunity_id,
        schema_version=1,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        symbol=symbol,
        origin="auto",
        is_backtest=True,
        backtest_run_id=run_id,
        trading_day=clock.trading_day(entry_fill.entry_ts),
        setup_detected_at=opportunity.setup_detected_at,
        signal_confirmed_at=None,
        decided_at=None,
        entry_filled_at=entry_fill.entry_ts,
        exit_filled_at=exit_fill.exit_ts,
        holding_seconds=int((exit_fill.exit_ts - entry_fill.entry_ts).total_seconds()),
        direction=opportunity.direction,
        entry_price=entry_fill.entry_price,
        entry_qty=entry_qty,
        exit_price=exit_fill.exit_price,
        exit_qty=exit_qty,
        commission_total=None,
        slippage_entry=None,
        realized_pnl=compute_realized_pnl(opportunity, entry_fill, exit_fill, qty=entry_qty),
        realized_r=compute_realized_r(opportunity, entry_fill, exit_fill),
        exit_reason=exit_fill.exit_reason,
        structural_invalidation=opportunity.structural_invalidation,
        structural_target=opportunity.structural_target,
        final_stop=opportunity.structural_invalidation,
        final_target=opportunity.structural_target,
        confidence_at_signal=opportunity.confidence,
        evidence=opportunity.evidence,
        market_state_at_entry=entry_snapshots.market_state,
        context_at_entry=entry_snapshots.context,
        market_state_at_exit=exit_snapshots.market_state,
        context_at_exit=exit_snapshots.context,
        feature_snapshot_id=None,
    )


class BacktestRunner:
    """One `StrategyConfig` (via `strategy`), one `symbol`, one date
    range, one `MarketDataProvider` — the v1 scope this whole task was
    built against. `symbol_universe` on the written `BacktestRunRecord`
    is a single-element list, not a bare string, so a future multi-symbol
    runner extends this row shape rather than needing a new one — this
    class itself still only drives one symbol (Unit 4 brief: don't build
    multi-symbol now, don't foreclose it either)."""

    def __init__(
        self,
        *,
        strategy: Strategy,
        symbol: str,
        market_data_provider: MarketDataProvider,
        start: datetime,
        end: datetime,
        context_provider: BacktestContextProvider,
        data_version: str,
        feature_version: str,
        timeframe: str = "1m",
        walk_forward_fold: int | None = None,
        is_holdout: bool = False,
        clock: MarketClock | None = None,
    ) -> None:
        self._strategy = strategy
        self._symbol = symbol
        self._market_data_provider = market_data_provider
        self._start = start
        self._end = end
        self._timeframe = timeframe
        self._context_provider = context_provider
        self._data_version = data_version
        self._feature_version = feature_version
        self._walk_forward_fold = walk_forward_fold
        self._is_holdout = is_holdout
        self._clock = clock or get_market_clock()

    async def run(self) -> BacktestRunResult:
        candles = await self._market_data_provider.get_historical(self._symbol, self._timeframe, self._start, self._end)
        if not candles:
            raise ValueError(
                f"BacktestRunner.run(): {self._market_data_provider!r} returned zero candles for "
                f"{self._symbol!r} in [{self._start!r}, {self._end!r}) — nothing to replay."
            )

        run_id = uuid4()
        sweep_id = uuid4()  # "a sweep of one" — see this task's earlier design note, confirmed
        config_hash = _compute_config_hash(self._strategy.config.gate_conditions, self._strategy.config.params)

        producer = EngineBackedReplayStateProducer(context_provider=self._context_provider)
        discarded: list[DiscardedSignal] = []
        recorded = 0
        pending: _PendingTrade | None = None

        await producer.start()
        try:
            async with install_replay_engines(
                feature_engine=producer.feature_engine,
                level_interaction_engine=producer.level_interaction_engine,
                market_state_engine=producer.market_state_engine,
                context_engine=producer.context_engine,
            ):
                # Written BEFORE any outcome — record_strategy_outcome()
                # enforces backtest_run_id as a real FK into this row, so
                # this isn't just "nice to do first," it's a hard
                # ordering requirement.
                await asyncio.to_thread(
                    _write_backtest_run_record,
                    BacktestRunRecord(
                        run_id=run_id,
                        sweep_id=sweep_id,
                        strategy_name=self._strategy.name,
                        strategy_version=self._strategy.config.version,
                        config_hash=config_hash,
                        symbol_universe=[self._symbol],
                        date_range_start=self._clock.trading_day(candles[0].candle_ts),
                        date_range_end=self._clock.trading_day(candles[-1].candle_ts),
                        data_version=self._data_version,
                        feature_version=self._feature_version,
                        walk_forward_fold=self._walk_forward_fold,
                        is_holdout=self._is_holdout,
                    ),
                )

                for i, candle in enumerate(candles):
                    state = await producer.advance_to(self._symbol, candle)

                    # --- entry side of a pending trade reached? ---------
                    if pending is not None and i == pending.entry_fill.entry_candle_index:
                        snapshots = capture_strategy_outcome_snapshots(self._symbol)
                        if snapshots.market_state is None or snapshots.context is None:
                            discarded.append(
                                DiscardedSignal(
                                    signal_candle_ts=candle.candle_ts,
                                    reason="D17: entry snapshot unavailable at entry_filled_at",
                                    detail=f"market_state={snapshots.market_state!r} context={snapshots.context!r}",
                                )
                            )
                            pending = None  # void — never simulated as a real trade, per D17 option (a)
                        else:
                            pending.entry_snapshots = snapshots

                    # --- exit side of a pending trade reached? ----------
                    if pending is not None and i == pending.exit_fill.exit_candle_index:
                        snapshots = capture_strategy_outcome_snapshots(self._symbol)
                        if snapshots.market_state is None or snapshots.context is None:
                            discarded.append(
                                DiscardedSignal(
                                    signal_candle_ts=pending.entry_fill.entry_ts,
                                    reason="D17: exit snapshot unavailable at exit_filled_at",
                                    detail=f"market_state={snapshots.market_state!r} context={snapshots.context!r}",
                                )
                            )
                        else:
                            outcome = _build_strategy_outcome(
                                symbol=self._symbol,
                                strategy_name=self._strategy.name,
                                strategy_version=self._strategy.config.version,
                                run_id=run_id,
                                pending=pending,
                                exit_snapshots=snapshots,
                                clock=self._clock,
                            )
                            await asyncio.to_thread(record_strategy_outcome, outcome)
                            recorded += 1
                        pending = None  # position closed either way — recorded or honestly discarded

                    # --- evaluate every candle, always — see module docstring ---
                    gate_ok = gate_conditions_satisfied(self._strategy.config.gate_conditions, state.market_state.candle_ts)
                    opportunity: Opportunity | None = None
                    if gate_ok:
                        opportunity = await self._strategy.evaluate(self._symbol, state.market_state, state.features, state.context)

                    # --- accept a new signal only if no position is open ---
                    if pending is None and opportunity is not None and opportunity.status == "actionable":
                        entry_fill = simulate_entry(opportunity, candles, i)
                        if entry_fill is None:
                            discarded.append(
                                DiscardedSignal(
                                    signal_candle_ts=candle.candle_ts,
                                    reason="no next candle to fill against",
                                    detail="signal fired on the last available candle in this replay range",
                                )
                            )
                        else:
                            try:
                                exit_fill = simulate_exit(opportunity, entry_fill, candles, self._clock)
                            except InsufficientReplayDataError as exc:
                                discarded.append(
                                    DiscardedSignal(
                                        signal_candle_ts=candle.candle_ts,
                                        reason="InsufficientReplayDataError",
                                        detail=str(exc),
                                    )
                                )
                            else:
                                pending = _PendingTrade(
                                    opportunity=opportunity,
                                    opportunity_id=uuid4(),
                                    entry_fill=entry_fill,
                                    exit_fill=exit_fill,
                                )
        finally:
            await producer.stop()

        if pending is not None:
            # Unreachable by construction (simulate_exit() only ever
            # returns an index within `candles`, so the loop always
            # reaches it) — a hard failure, not an honest edge case, if
            # it ever fires.
            raise RuntimeError(
                f"BacktestRunner.run(): replay loop ended with an unresolved pending trade "
                f"(entry_candle_index={pending.entry_fill.entry_candle_index}, "
                f"exit_candle_index={pending.exit_fill.exit_candle_index}, candles={len(candles)}) — this is a bug."
            )

        return BacktestRunResult(run_id=run_id, sweep_id=sweep_id, outcomes_recorded=recorded, discarded_signals=discarded)
