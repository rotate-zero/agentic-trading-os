"""C2: observation-only candidate reader and its lifecycle.

Real ``EventBus`` objects (un-started: subscribe/unsubscribe/dispatch-by-hand),
real ``MarketClock``, real ``broker_registry`` for the ownership hook, and the
C1 reducer underneath. Delivery is by direct handler call unless a test says it
exercises bus dispatch.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import subprocess
import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventEnvelope, EventType
from app.services import broker_registry
from app.trading_intelligence import candidate_observation
from app.trading_intelligence.candidate_batch_wire import batch_to_payload
from app.trading_intelligence.candidate_contract import StrategyDisposition, UnavailablePrerequisite
from app.trading_intelligence.candidate_eligibility import CandidateFreshnessPolicy
from app.trading_intelligence.candidate_observation import CandidateObservationReader
from tests.test_opportunity_candidate_contract import (
    ONE_MIN,
    T0,  # 2026-10-09 14:00 UTC = 10:00 ET, regular session (session start 13:30 UTC)
    make_batch,
    make_opportunity,
    opportunity_disposition,
)

POLICY = CandidateFreshnessPolicy.create({"1m": 120.0})


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def minute(n: int) -> datetime:
    return T0 + n * ONE_MIN


def envelope(batch, symbol: str | None = None) -> EventEnvelope:
    return make_envelope(EventType.STRATEGY_EVALUATION_COMPLETED, batch_to_payload(batch), symbol=symbol or batch.symbol)


def make_reader(*, start_at: datetime = T0 - 5 * ONE_MIN, policy=POLICY, mode="simulated", bus=None):
    clock = Clock(start_at)
    bus = bus if bus is not None else EventBus()
    reader = CandidateObservationReader(bus, mode=mode, freshness_policy=policy, clock=clock)
    reader.start()
    return reader, clock, bus


async def deliver(reader, clock, batch, *, at: datetime | None = None, symbol: str | None = None) -> None:
    clock.now = at if at is not None else batch.source_interval_close + timedelta(seconds=2)
    await reader._on_batch(envelope(batch, symbol))


def eligible(reader, clock, as_of=None):
    return [a.candidate_id for a in reader.snapshot(as_of or clock.now).eligibility.eligible]


@pytest.fixture(autouse=True)
def _clean_listeners():
    broker_registry.clear_all()
    yield
    broker_registry.clear_all()


# --- lifecycle basics ------------------------------------------------------------------


async def test_not_started_stopped_and_running_statuses():
    clock = Clock(T0)
    reader = CandidateObservationReader(EventBus(), clock=clock)
    assert reader.snapshot().status == "not_started"
    reader.start()
    assert reader.snapshot().status == "running"
    await reader.stop()
    assert reader.snapshot().status == "stopped"


async def test_start_subscribes_and_stop_unsubscribes_and_unregisters_the_ownership_listener():
    bus = EventBus()
    reader, clock, _ = make_reader(bus=bus)
    assert reader._on_batch in bus._subscribers[EventType.STRATEGY_EVALUATION_COMPLETED]
    assert len(broker_registry._streaming_ownership_listeners) == 1
    await reader.stop()
    assert reader._on_batch not in bus._subscribers[EventType.STRATEGY_EVALUATION_COMPLETED]
    assert broker_registry._streaming_ownership_listeners == []


async def test_deliveries_after_stop_are_ignored_and_counted():
    reader, clock, _ = make_reader()
    await reader.stop()
    await deliver(reader, clock, make_batch(minute(0)))
    snap = reader.snapshot(clock.now)
    assert snap.eligibility.assessments == ()
    assert dict(snap.diagnostics.by_status) == {"not_running": 1}


# --- complete batches become candidates ---------------------------------------------------


async def test_complete_batch_is_visible_with_distinct_source_completion_and_receive_times():
    reader, clock, _ = make_reader()
    batch = make_batch(minute(0), [opportunity_disposition("orb"), opportunity_disposition("vwap", direction="short")])
    await deliver(reader, clock, batch, at=minute(1) + timedelta(seconds=2))
    snap = reader.snapshot(minute(1) + timedelta(seconds=30))
    assert {(a.strategy, a.direction) for a in snap.eligibility.eligible} == {("orb", "long"), ("vwap", "short")}
    one = snap.eligibility.assessments[0]
    assert one.source_candle_ts == minute(0)
    assert one.source_interval_close == minute(1)
    assert one.completed_at == minute(1) + timedelta(seconds=1)
    assert one.received_at == minute(1) + timedelta(seconds=2)
    assert len({one.source_interval_close, one.completed_at, one.received_at}) == 3
    assert snap.mode == "simulated" and snap.arrival_sequence == 1


async def test_without_a_configured_policy_nothing_is_eligible_and_it_says_so():
    reader, clock, _ = make_reader(policy=None)
    await deliver(reader, clock, make_batch(minute(0)))
    snap = reader.snapshot(minute(1) + timedelta(seconds=10))
    assert snap.freshness_status == "freshness_policy_unconfigured"
    assert snap.eligibility.eligible == ()
    assert "freshness_policy_unconfigured" in snap.eligibility.assessments[0].reasons


async def test_policy_for_another_timeframe_is_still_unconfigured_for_this_one():
    reader, clock, _ = make_reader(policy=CandidateFreshnessPolicy.create({"5m": 600.0}))
    await deliver(reader, clock, make_batch(minute(0)))
    assert "freshness_policy_unconfigured" in reader.snapshot(clock.now).eligibility.assessments[0].reasons


async def test_candidate_expires_by_source_interval_close_not_delivery_time():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)), at=minute(1) + timedelta(seconds=100))  # delivered very late
    assert eligible(reader, clock, minute(1) + timedelta(seconds=120))  # age 120 == max: still fresh
    assert not eligible(reader, clock, minute(1) + timedelta(seconds=121))


# --- batches are atomic ------------------------------------------------------------------------


def test_batch_handler_has_no_await_so_no_reader_can_observe_a_partial_batch():
    source = inspect.getsource(CandidateObservationReader._on_batch)
    tree = ast.parse("\n".join(line[4:] if line.startswith("    ") else line for line in source.splitlines()))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Await)]


async def test_a_conflicting_member_withholds_the_whole_batch_from_readers():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0), [opportunity_disposition("orb")]))
    before = reader.snapshot(clock.now)
    changed = make_batch(
        minute(0), [opportunity_disposition("orb", confidence=0.2), opportunity_disposition("vwap")]
    )
    await deliver(reader, clock, changed)
    after = reader.snapshot(clock.now)
    assert after.eligibility.assessments == before.eligibility.assessments  # vwap from the same batch is NOT visible
    assert dict(after.diagnostics.by_status) == {"applied": 1, "conflict": 1}


async def test_all_members_of_an_applied_batch_appear_together_for_the_next_snapshot():
    names = ["a", "b", "c", "d"]
    taken: list[int] = []
    clock = Clock(T0 - 5 * ONE_MIN)
    bus = EventBus()
    reader2 = CandidateObservationReader(bus, freshness_policy=POLICY, clock=clock)
    reader2.start()
    # A second subscriber on the same dispatch takes snapshots between/around the reader's.
    bus.subscribe(EventType.STRATEGY_EVALUATION_COMPLETED, lambda e: taken.append(len(reader2.snapshot(clock.now).eligibility.assessments)))
    await bus.start()
    try:
        clock.now = minute(1) + timedelta(seconds=2)
        await bus.publish(envelope(make_batch(minute(0), [opportunity_disposition(n) for n in names])))
        await bus._normal_queue.join()
    finally:
        await bus.stop()
        await reader2.stop()
    assert taken and set(taken) <= {0, len(names)}  # never 1, 2 or 3
    assert len(reader2.snapshot(clock.now).eligibility.assessments) == len(names)


# --- reads only the batch --------------------------------------------------------------------------


async def test_opportunity_created_events_are_not_read_at_all():
    bus = EventBus()
    reader, clock, _ = make_reader(bus=bus)
    assert EventType.OPPORTUNITY_CREATED not in bus._subscribers or not bus._subscribers[EventType.OPPORTUNITY_CREATED]
    await bus.start()
    try:
        await bus.publish(make_envelope(EventType.OPPORTUNITY_CREATED, make_opportunity_model(), symbol="AAPL"))
        await bus._normal_queue.join()
    finally:
        await bus.stop()
    snap = reader.snapshot(clock.now)
    assert snap.eligibility.assessments == () and snap.arrival_sequence == 0


def make_opportunity_model():
    from app.strategy_engine.base_strategy import Opportunity

    return Opportunity(
        strategy="orb", version="1", direction="BUY", confidence=0.7, structural_invalidation=99.0,
        structural_target=102.0, evidence={}, setup_detected_at=T0,
    )


# --- duplicate / conflict / stale / out of order ---------------------------------------------------------


async def test_identical_redelivery_is_a_duplicate_no_op_that_keeps_the_first_receive_time():
    reader, clock, _ = make_reader()
    batch = make_batch(minute(0))
    await deliver(reader, clock, batch, at=minute(1) + timedelta(seconds=2))
    cut = minute(1) + timedelta(seconds=60)
    first = reader.snapshot(cut).eligibility.assessments
    await deliver(reader, clock, batch, at=minute(1) + timedelta(seconds=40))
    again = reader.snapshot(cut)
    assert again.eligibility.assessments == first
    assert again.eligibility.assessments[0].received_at == minute(1) + timedelta(seconds=2)
    assert dict(again.diagnostics.by_status) == {"applied": 1, "duplicate": 1}


async def test_same_candle_with_changed_contents_is_a_reported_conflict_not_a_duplicate(caplog):
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)))
    with caplog.at_level(logging.WARNING):
        await deliver(reader, clock, make_batch(minute(0), [opportunity_disposition(confidence=0.1)]))
    snap = reader.snapshot(clock.now)
    assert dict(snap.diagnostics.by_status) == {"applied": 1, "conflict": 1}
    assert dict(snap.diagnostics.by_reason)["evaluation_content_conflict"] == 1
    assert snap.eligibility.assessments[0].opportunity.confidence == 0.7  # first content kept, not last-write-wins
    problem = snap.diagnostics.recent_problems[-1]
    assert (problem.status, problem.symbol, problem.source_candle_ts) == ("conflict", "AAPL", minute(0))
    assert "conflicting evaluation contents" in caplog.text


async def test_a_changed_completion_time_alone_is_a_conflict_never_silently_a_duplicate():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0), completed_delay=timedelta(seconds=1)))
    await deliver(reader, clock, make_batch(minute(0), completed_delay=timedelta(seconds=9)))
    assert dict(reader.snapshot(clock.now).diagnostics.by_status) == {"applied": 1, "conflict": 1}


async def test_out_of_order_older_batch_is_stale_and_cannot_resurrect_a_removed_candidate():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(1), [StrategyDisposition("orb", "1", "no_opportunity")]))
    await deliver(reader, clock, make_batch(minute(0), [opportunity_disposition("orb")]))
    snap = reader.snapshot(minute(2))
    assert dict(snap.diagnostics.by_status) == {"applied": 1, "stale": 1}
    assert snap.eligibility.eligible == () and snap.eligibility.assessments[0].kind == "no_opportunity"


@pytest.mark.parametrize(
    "newer",
    [
        StrategyDisposition("orb", "1", "no_opportunity"),
        StrategyDisposition("orb", "1", "gated", reason="gate_conditions_not_satisfied"),
        StrategyDisposition("orb", "1", "error", reason="evaluate_failed"),
    ],
    ids=["none", "gated", "error"],
)
async def test_a_newer_none_gate_or_error_removes_the_earlier_candidate(newer):
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)))
    assert eligible(reader, clock, minute(1) + timedelta(seconds=10))
    await deliver(reader, clock, make_batch(minute(1), [newer]))
    assert eligible(reader, clock, minute(2) + timedelta(seconds=10)) == []


async def test_an_unavailable_batch_invalidates_the_earlier_candidate_and_is_reported():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)))
    unavailable = make_batch(minute(1), None, unavailable=[UnavailablePrerequisite("features", "candle_ts_mismatch")])
    await deliver(reader, clock, unavailable)
    snap = reader.snapshot(minute(2))
    assert snap.eligibility.eligible == ()
    assert snap.eligibility.assessments[0].reasons[0] == "invalidated:prerequisite_unavailable"
    assert [(f.symbol, f.prerequisites[0].reason) for f in snap.unavailable_frames] == [("AAPL", "candle_ts_mismatch")]


async def test_invalid_envelopes_and_payloads_are_counted_and_do_not_stop_the_reader():
    reader, clock, _ = make_reader()
    await reader._on_batch(EventEnvelope(event_type=EventType.STRATEGY_EVALUATION_COMPLETED, symbol="AAPL", payload={"junk": 1}))
    await reader._on_batch(envelope(make_batch(minute(0)), symbol="__MARKET__"))
    await reader._on_batch(EventEnvelope(event_type=EventType.STRATEGY_EVALUATION_COMPLETED, symbol=None, payload={}))
    await deliver(reader, clock, make_batch(minute(0)))
    snap = reader.snapshot(clock.now)
    assert dict(snap.diagnostics.by_status) == {"invalid": 3, "applied": 1}
    assert dict(snap.diagnostics.by_reason) == {"invalid_payload": 1, "invalid_envelope": 2}
    assert len(snap.eligibility.assessments) == 1


# --- mode isolation ------------------------------------------------------------------------------------------


async def test_a_backtest_batch_never_enters_the_live_reader():
    reader, clock, _ = make_reader(mode="simulated")
    await deliver(reader, clock, make_batch(minute(0), mode="backtest"))
    snap = reader.snapshot(clock.now)
    assert snap.eligibility.assessments == ()
    assert dict(snap.diagnostics.by_reason) == {"mode_mismatch": 1}


async def test_replay_state_lives_in_its_own_reader_on_its_own_bus():
    live_bus, replay_bus = EventBus(), EventBus()
    live, live_clock, _ = make_reader(bus=live_bus, mode="simulated")
    replay, replay_clock, _ = make_reader(bus=replay_bus, mode="backtest")
    await deliver(replay, replay_clock, make_batch(minute(0), mode="backtest"))
    assert len(replay.snapshot(replay_clock.now).eligibility.assessments) == 1
    assert live.snapshot(live_clock.now).eligibility.assessments == ()  # separate bus, separate state
    assert candidate_observation.__dict__.get("_candidate_observation_reader") is None  # no shared singleton


# --- reset boundaries -------------------------------------------------------------------------------------------


async def test_restart_boundary_rejects_a_delayed_or_straddling_pre_start_batch():
    reader, clock, _ = make_reader(start_at=T0 + timedelta(seconds=30))  # started mid-candle
    await deliver(reader, clock, make_batch(minute(0)), at=minute(1) + timedelta(seconds=2))  # straddles the start
    assert dict(reader.snapshot(clock.now).diagnostics.by_reason) == {"pre_reset_boundary": 1}
    await deliver(reader, clock, make_batch(minute(1)))  # first fully post-start candle
    assert eligible(reader, clock, minute(2) + timedelta(seconds=5))


async def test_session_change_clears_eligibility_even_with_no_new_delivery():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)))
    assert eligible(reader, clock, minute(1) + timedelta(seconds=5))
    after_hours = datetime(2026, 10, 9, 20, 0, 30, tzinfo=timezone.utc)  # 16:00:30 ET
    clock.now = after_hours
    snap = reader.snapshot(after_hours)
    assert snap.eligibility.assessments == () and dict(snap.diagnostics.resets)["session_change"] == 1


async def test_session_change_boundary_rejects_the_late_last_candle_and_admits_the_first_new_one():
    reader, clock, _ = make_reader()
    last_regular = datetime(2026, 10, 9, 19, 59, tzinfo=timezone.utc)  # 15:59 ET
    first_after_hours = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)  # 16:00 ET
    await deliver(reader, clock, make_batch(last_regular), at=datetime(2026, 10, 9, 20, 0, 3, tzinfo=timezone.utc))
    assert dict(reader.snapshot(clock.now).diagnostics.by_reason) == {"pre_reset_boundary": 1}
    await deliver(reader, clock, make_batch(first_after_hours))
    assert len(reader.snapshot(clock.now).eligibility.assessments) == 1


async def test_lunch_and_power_hour_are_not_session_changes():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)))
    clock.now = datetime(2026, 10, 9, 18, 45, tzinfo=timezone.utc)  # 14:45 ET: power hour, same regular session
    assert dict(reader.snapshot(clock.now).diagnostics.resets) == {"restart": 1}
    assert len(reader.snapshot(clock.now).eligibility.assessments) == 1


async def test_provider_takeover_clears_eligibility_and_enforces_the_boundary():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)))
    assert reader.snapshot(clock.now).eligibility.assessments

    class Provider:
        async def disconnect(self):
            pass

    clock.now = minute(2) + timedelta(seconds=30)
    await broker_registry.take_over_streaming(Provider())
    snap = reader.snapshot(clock.now)
    assert snap.eligibility.assessments == () and dict(snap.diagnostics.resets)["provider_change"] == 1
    # delayed old-source batch for a candle that began before the change, then the straddling candle
    await deliver(reader, clock, make_batch(minute(1)), at=minute(2) + timedelta(seconds=40))
    await deliver(reader, clock, make_batch(minute(2)), at=minute(3) + timedelta(seconds=2))
    assert dict(reader.snapshot(clock.now).diagnostics.by_reason) == {"pre_reset_boundary": 2}
    await deliver(reader, clock, make_batch(minute(3)))
    assert len(reader.snapshot(clock.now).eligibility.assessments) == 1


async def test_clearing_the_streaming_provider_resets_but_an_unchanged_takeover_does_not():
    reader, clock, _ = make_reader()

    class Provider:
        async def disconnect(self):
            pass

    provider = Provider()
    await broker_registry.take_over_streaming(provider)
    await broker_registry.take_over_streaming(provider)  # same provider, same (absent) bridge: no change
    assert dict(reader.snapshot(clock.now).diagnostics.resets)["provider_change"] == 1
    broker_registry.clear_streaming_provider()
    assert dict(reader.snapshot(clock.now).diagnostics.resets)["provider_change"] == 2
    broker_registry.clear_streaming_provider()  # nothing registered: no further reset
    assert dict(reader.snapshot(clock.now).diagnostics.resets)["provider_change"] == 2


async def test_a_failing_ownership_listener_cannot_break_provider_switching(caplog):
    def bad(_kind):
        raise RuntimeError("observer bug")

    broker_registry.register_streaming_ownership_listener(bad)

    class Provider:
        async def disconnect(self):
            pass

    with caplog.at_level(logging.ERROR):
        await broker_registry.take_over_streaming(Provider())
    assert broker_registry.get_streaming_provider() is not None
    assert "streaming ownership listener failed" in caplog.text


async def test_after_stop_a_provider_change_is_not_observed():
    reader, clock, _ = make_reader()
    await reader.stop()

    class Provider:
        async def disconnect(self):
            pass

    await broker_registry.take_over_streaming(Provider())
    assert "provider_change" not in dict(reader.snapshot(clock.now).diagnostics.resets)


# --- detached snapshots and the arrival-sequence cutoff -----------------------------------------------------------------


async def test_snapshots_are_detached_immutable_and_unaffected_by_later_arrivals():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)))
    early = reader.snapshot(minute(1) + timedelta(seconds=5))
    assert early.arrival_sequence == 1
    await deliver(reader, clock, make_batch(minute(1), [StrategyDisposition("orb", "1", "no_opportunity")]))
    late = reader.snapshot(minute(2) + timedelta(seconds=5))
    assert late.arrival_sequence == 2
    assert [a.eligible for a in early.eligibility.assessments] == [True]   # captured cut unchanged
    assert [a.eligible for a in late.eligibility.assessments] == [False]
    with pytest.raises(FrozenInstanceError):
        early.arrival_sequence = 9  # type: ignore[misc]
    evidence = early.eligibility.assessments[0].opportunity.evidence_copy()
    evidence["mutated"] = True
    assert "mutated" not in early.eligibility.assessments[0].opportunity.evidence_copy()


async def test_every_delivery_increments_the_arrival_sequence_in_order():
    reader, clock, _ = make_reader()
    for n in range(3):
        await deliver(reader, clock, make_batch(minute(n)))
    snap = reader.snapshot(clock.now)
    assert snap.arrival_sequence == 3 == snap.diagnostics.deliveries


# --- observation-only: no authorization, order or selection side effects ---------------------------------------------------


def test_reader_module_has_no_publish_and_imports_no_authorization_or_order_modules():
    path = Path(candidate_observation.__file__)
    tree = ast.parse(path.read_text())
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "publish" not in calls
    imported = {
        (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in getattr(n, "names", [None])
    }
    forbidden = ("app.governor", "app.execution_engine", "app.portfolio_state", "app.trade_planning", "app.position_monitor")
    assert not [m for m in imported if m.startswith(forbidden)]


def test_importing_the_reader_in_a_clean_interpreter_loads_no_authorization_or_order_modules():
    root = str(Path(__file__).resolve().parents[1])
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "import app.trading_intelligence.candidate_observation;"
        "print([n for n in sys.modules if n.startswith(('app.governor','app.execution_engine',"
        "'app.portfolio_state','app.trade_planning','app.position_monitor'))])"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code, root], capture_output=True, text=True, cwd=root, check=True)
    assert out.stdout.strip() == "[]", out.stdout
