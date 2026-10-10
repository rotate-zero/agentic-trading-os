"""Observation-only candidate reader (C2, trading-intelligence-architecture.md §19.2).

Subscribes to ``StrategyEvaluationCompleted`` (the Scheduler's complete,
atomic batches), reduces each through the C1 pure reducer and serves detached
eligibility snapshots. It is a SHADOW READ MODEL and nothing else:

* it never calls the Governor, reserves capital, creates trades, ranks,
  selects, plans, or publishes any event (it holds a bus only to subscribe);
* it never reads ``OpportunityCreated`` — eligibility is built solely from the
  complete contents the batch carries;
* it does not touch ``OpportunityCache``/``opportunity_view`` or
  ``AuthorizerStub``, whose behavior is unchanged.

Atomicity and the arrival-sequence cutoff
-----------------------------------------
The batch handler contains no ``await``: parse, session roll, reduce and the
single assignment of the new immutable state happen without yielding to the
event loop. A reader therefore sees either all of a batch or none of it — there
is no mid-batch state to observe. Every received delivery is numbered in
arrival order; a snapshot records ``arrival_sequence`` (the highest arrival
fully processed when the snapshot was cut), so a later consumer can state
exactly which deliveries a captured snapshot includes.

Resets (C1 reset-boundary rule, applied by ``apply_reset``)
-----------------------------------------------------------
Every reset clears eligibility and sets an admission boundary; a batch whose
source interval STARTS before the boundary is rejected (``pre_reset_boundary``),
so a delayed or straddling pre-reset batch cannot repopulate the state and the
first admitted candle is a fully post-boundary one.

* ``start()`` — ``restart`` reset, boundary = start time.
* session change — detected from ``MarketClock.session_bounds`` (regular
  open/lunch/power-hour is ONE session; pre-market and after-hours are their
  own) on each delivery and each snapshot; boundary = the new session's start
  (or "now" when the market is closed).
* streaming ownership change — ``broker_registry`` listener; boundary = the
  moment the registry replaced/cleared the source.
* ``stop()`` — unsubscribes and unregisters; later deliveries are ignored.

Mode isolation: a reader is bound to ONE execution mode and the reducer rejects
other modes (``mode_mismatch``). Backtest replay uses its own private
``EventBus`` and never reaches this reader; there is deliberately no module
singleton for a replay to share.

Freshness: maximum ages are policy INPUTS (``CandidateFreshnessPolicy``). With
none configured nothing is eligible and every opportunity reports
``freshness_policy_unconfigured`` — no age threshold is invented here.
"""
from __future__ import annotations

import logging
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import ValidationError

from app.core.market_clock import MarketClock, get_market_clock
from app.event_bus.bus import EventBus
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.strategy_evaluation import StrategyEvaluationCompleted
from app.services import broker_registry
from app.trading_intelligence.candidate_batch_wire import payload_to_batch
from app.trading_intelligence.candidate_contract import CandidateContractError, ExecutionMode
from app.trading_intelligence.candidate_eligibility import (
    CandidateFreshnessPolicy,
    EligibilitySnapshot,
    assess_candidates,
)
from app.trading_intelligence.candidate_state import (
    CandidateState,
    UnavailableRecord,
    apply_batch,
    apply_reset,
    initial_state,
)

logger = logging.getLogger(__name__)

_CROSS_SYMBOL_SENTINEL = "__MARKET__"  # see strategy_engine/scheduler.py for why this is a literal
_UNSET = object()

REASON_INVALID_ENVELOPE = "invalid_envelope"
REASON_INVALID_PAYLOAD = "invalid_payload"
REASON_NOT_RUNNING = "reader_not_running"

FRESHNESS_POLICY_UNCONFIGURED = "freshness_policy_unconfigured"
FRESHNESS_POLICY_CONFIGURED = "freshness_policy_configured"

ReaderStatus = Literal["not_started", "running", "stopped"]


@dataclass(frozen=True)
class ObservedProblem:
    """A delivery that changed nothing and why (conflicts and rejections)."""

    arrival_sequence: int
    status: str
    reason: str | None
    symbol: str | None
    timeframe: str | None
    source_candle_ts: datetime | None
    received_at: datetime


@dataclass(frozen=True)
class ObservationDiagnostics:
    deliveries: int
    by_status: tuple[tuple[str, int], ...]  # applied/duplicate/stale/ignored/rejected/conflict/invalid/not_running
    by_reason: tuple[tuple[str, int], ...]
    resets: tuple[tuple[str, int], ...]  # by reset kind
    recent_problems: tuple[ObservedProblem, ...]  # newest last, bounded


@dataclass(frozen=True)
class ObservationSnapshot:
    """Detached point-in-time view. Holds only immutable values."""

    status: ReaderStatus
    mode: ExecutionMode
    as_of: datetime
    arrival_sequence: int  # highest delivery fully processed when this was cut
    freshness_status: str  # freshness_policy_unconfigured | freshness_policy_configured
    eligibility: EligibilitySnapshot
    unavailable_frames: tuple[UnavailableRecord, ...]
    diagnostics: ObservationDiagnostics


class CandidateObservationReader:
    def __init__(
        self,
        bus: EventBus,
        *,
        mode: ExecutionMode = "simulated",
        freshness_policy: CandidateFreshnessPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        market_clock: MarketClock | None = None,
        max_recent_problems: int = 20,
    ) -> None:
        self._bus = bus
        self._mode: ExecutionMode = mode
        self._policy = freshness_policy
        self._clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
        self._market_clock = market_clock if market_clock is not None else get_market_clock()
        self._state: CandidateState = initial_state(mode)
        self._status: ReaderStatus = "not_started"
        self._session_key: object = _UNSET
        self._arrival_sequence = 0
        self._by_status: Counter[str] = Counter()
        self._by_reason: Counter[str] = Counter()
        self._resets: Counter[str] = Counter()
        self._problems: deque[ObservedProblem] = deque(maxlen=max_recent_problems)

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Subscribe, listen for streaming ownership changes, and reset (restart)."""
        if self._status == "running":
            return
        now = self._now()
        self._state = apply_reset(initial_state(self._mode), "restart", now, "restart")
        self._resets["restart"] += 1
        self._session_key = self._current_session_key(now)
        self._status = "running"
        self._bus.subscribe(EventType.STRATEGY_EVALUATION_COMPLETED, self._on_batch)
        broker_registry.register_streaming_ownership_listener(self._on_streaming_ownership_changed)
        logger.info("CandidateObservationReader started (mode=%s, observation only)", self._mode)

    async def stop(self) -> None:
        """Nothing to drain (no queue or task). Later deliveries are ignored."""
        self._bus.unsubscribe(EventType.STRATEGY_EVALUATION_COMPLETED, self._on_batch)
        broker_registry.unregister_streaming_ownership_listener(self._on_streaming_ownership_changed)
        self._status = "stopped"
        logger.info("CandidateObservationReader stopped")

    # --- reads --------------------------------------------------------------

    def snapshot(self, as_of: datetime | None = None) -> ObservationSnapshot:
        """Detached snapshot; synchronous, no I/O, mutates nothing but a due session roll."""
        now = self._now()
        if self._status == "running":
            self._roll_session(now)
        cut = as_of if as_of is not None else now
        return ObservationSnapshot(
            status=self._status,
            mode=self._mode,
            as_of=cut,
            arrival_sequence=self._arrival_sequence,
            freshness_status=FRESHNESS_POLICY_UNCONFIGURED if self._policy is None else FRESHNESS_POLICY_CONFIGURED,
            eligibility=assess_candidates(self._state, cut, self._policy),
            unavailable_frames=tuple(
                self._state.unavailable[key] for key in sorted(self._state.unavailable)
            ),
            diagnostics=ObservationDiagnostics(
                deliveries=self._arrival_sequence,
                by_status=tuple(sorted(self._by_status.items())),
                by_reason=tuple(sorted(self._by_reason.items())),
                resets=tuple(sorted(self._resets.items())),
                recent_problems=tuple(self._problems),
            ),
        )

    # --- internals ----------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock()

    def _current_session_key(self, now: datetime) -> object:
        bounds = self._market_clock.session_bounds(now)
        return None if bounds is None else bounds[0].astimezone(timezone.utc)

    def _roll_session(self, now: datetime) -> None:
        key = self._current_session_key(now)
        if self._session_key is not _UNSET and key == self._session_key:
            return
        self._session_key = key
        boundary = key if isinstance(key, datetime) else now
        self._state = apply_reset(self._state, "session_change", boundary, "session_change")
        self._resets["session_change"] += 1
        logger.info("CandidateObservationReader: session changed — eligibility cleared (boundary=%s)", boundary)

    def _on_streaming_ownership_changed(self, kind: str) -> None:
        if self._status != "running":
            return
        now = self._now()
        self._state = apply_reset(self._state, "provider_change", now, f"provider_change:{kind}")
        self._resets["provider_change"] += 1
        logger.info("CandidateObservationReader: streaming source changed (%s) — eligibility cleared", kind)

    def _record(self, status: str, reason: str | None, *, symbol, timeframe, candle_ts, now) -> None:
        self._by_status[status] += 1
        if reason is not None:
            self._by_reason[reason] += 1
        if status in ("conflict", "rejected", "invalid", "not_running"):
            self._problems.append(
                ObservedProblem(self._arrival_sequence, status, reason, symbol, timeframe, candle_ts, now)
            )

    async def _on_batch(self, envelope: EventEnvelope) -> None:
        # No await anywhere below: the whole delivery is atomic (module docstring).
        now = self._now()
        self._arrival_sequence += 1
        symbol = envelope.symbol
        if self._status != "running":
            self._record("not_running", REASON_NOT_RUNNING, symbol=symbol, timeframe=None, candle_ts=None, now=now)
            return
        self._roll_session(now)
        if symbol is None or symbol == _CROSS_SYMBOL_SENTINEL:
            self._record("invalid", REASON_INVALID_ENVELOPE, symbol=symbol, timeframe=None, candle_ts=None, now=now)
            return
        try:
            payload = StrategyEvaluationCompleted.model_validate(envelope.payload)
            batch = payload_to_batch(symbol, payload)
        except (ValidationError, CandidateContractError):
            self._record("invalid", REASON_INVALID_PAYLOAD, symbol=symbol, timeframe=None, candle_ts=None, now=now)
            logger.warning("CandidateObservationReader: invalid StrategyEvaluationCompleted for %s dropped", symbol)
            return
        result = apply_batch(self._state, batch, now)
        self._state = result.state
        self._record(
            result.status,
            result.reason,
            symbol=symbol,
            timeframe=batch.trigger_timeframe,
            candle_ts=batch.source_candle_ts,
            now=now,
        )
        if result.status == "conflict":
            logger.warning(
                "CandidateObservationReader: conflicting evaluation contents for %s %s %s (%s) — nothing applied",
                symbol,
                batch.trigger_timeframe,
                batch.source_candle_ts,
                result.reason,
            )
