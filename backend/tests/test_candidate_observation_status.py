"""
GET /intelligence/candidate-observation (task `candidate-observation-status-ui`).

Direct ASGI transport against the real, unstarted `app` (same technique as
test_scanner_observation_status.py): no lifespan runs, so the only reader is
the one a test installs on `app.state.candidate_observation_reader` -- the slot
the lifespan owns. Nothing here needs PostgreSQL: the route must not touch the
database, a strategy or the Event Bus, and the side-effect tests make any
attempt fail loudly.

Snapshots come from a REAL `CandidateObservationReader` fed REAL
`StrategyEvaluationCompleted` envelopes (C1 reducer underneath), delivered by
direct handler call with an injected clock; only the "reader raises" and
"counts its reads" cases use a thin wrapper around such a reader.
"""
from __future__ import annotations

import ast
import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy.engine import Engine

from app.event_bus.bus import EventBus
from app.main import app as fastapi_app
from app.services import broker_registry
from app.trading_intelligence import candidate_observation_status as status_module
from app.trading_intelligence.candidate_contract import StrategyDisposition, UnavailablePrerequisite
from app.trading_intelligence.candidate_eligibility import CandidateFreshnessPolicy
from app.trading_intelligence.candidate_observation import CandidateObservationReader
from app.trading_intelligence.candidate_observation_status import (
    MAX_CANDIDATE_ROWS,
    MAX_UNAVAILABLE_FRAMES,
    project_candidate_observation,
)
from tests.test_candidate_observation import (
    POLICY,
    Clock,
    deliver,
    make_reader,
    minute,
)
from tests.test_opportunity_candidate_contract import (
    ONE_MIN,
    T0,
    make_batch,
    make_opportunity,
    opportunity_disposition,
)

URL = "/intelligence/candidate-observation"


@pytest.fixture(autouse=True)
def _clean_state():
    broker_registry.clear_all()
    had = hasattr(fastapi_app.state, "candidate_observation_reader")
    previous = getattr(fastapi_app.state, "candidate_observation_reader", None)
    yield
    broker_registry.clear_all()
    if had:
        fastapi_app.state.candidate_observation_reader = previous
    elif hasattr(fastapi_app.state, "candidate_observation_reader"):
        del fastapi_app.state.candidate_observation_reader


def install(reader) -> None:
    fastapi_app.state.candidate_observation_reader = reader


async def _get(params: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(URL, params=params)


def strict_json(response: httpx.Response):
    """Decode refusing NaN/Infinity (Starlette would already refuse to encode them)."""

    def refuse(token):
        raise AssertionError(f"non-finite JSON constant {token}")

    return json.loads(response.text, parse_constant=refuse)


def snapshot_of(body: dict) -> dict:
    assert body["status"] == "available", body
    return body["snapshot"]


async def full_reader(policy=POLICY):
    """A reader holding one eligible candidate plus every non-eligible shape."""
    reader, clock, bus = make_reader(policy=policy)
    batch = make_batch(
        minute(0),
        [
            opportunity_disposition("orb"),
            StrategyDisposition("gap", "1", "no_opportunity"),
            StrategyDisposition("vwap", "1", "gated", reason="gate_conditions_not_satisfied"),
            StrategyDisposition("momentum", "1", "error", reason="evaluate_failed"),
            opportunity_disposition("reversal", direction="short", status="waiting"),
        ],
    )
    await deliver(reader, clock, batch, at=minute(1) + timedelta(seconds=2))
    clock.now = minute(1) + timedelta(seconds=30)
    return reader, clock, bus


# ------------------------------------------------------------ unavailable


async def test_no_reader_installed_is_explicitly_unavailable_not_an_empty_universe():
    if hasattr(fastapi_app.state, "candidate_observation_reader"):
        del fastapi_app.state.candidate_observation_reader
    response = await _get()
    assert response.status_code == 200
    body = strict_json(response)
    assert body["status"] == "unavailable"
    assert body["reason"] == "reader_not_installed"
    assert body["snapshot"] is None and body["observation_only"] is True
    assert "candidates" not in json.dumps(body["reader"])


async def test_lifespan_shutdown_sentinel_none_is_unavailable_too():
    install(None)  # main.py's shutdown sets the slot to None
    body = strict_json(await _get())
    assert (body["status"], body["reason"], body["snapshot"]) == ("unavailable", "reader_not_installed", None)


async def test_not_started_reader_is_unavailable_with_its_status():
    reader = CandidateObservationReader(EventBus(), clock=Clock(T0))
    install(reader)
    body = strict_json(await _get())
    assert body["status"] == "unavailable" and body["reason"] == "reader_not_started"
    assert body["reader"] == {"status": "not_started", "execution_mode": "simulated"}
    assert body["snapshot"] is None


async def test_stopped_reader_is_unavailable_and_does_not_report_retained_candidates():
    reader, clock, _ = await full_reader()
    await reader.stop()
    install(reader)
    body = strict_json(await _get())
    assert body["status"] == "unavailable" and body["reason"] == "reader_stopped"
    assert body["reader"] == {"status": "stopped", "execution_mode": "simulated"}
    assert body["snapshot"] is None  # stale retained rows are not presented as current


# ------------------------------------------------------------ empty state


async def test_running_reader_with_no_evaluations_is_available_and_honestly_empty():
    reader, clock, _ = make_reader()
    install(reader)
    body = strict_json(await _get())
    snap = snapshot_of(body)
    assert body["reader"] == {"status": "running", "execution_mode": "simulated"}
    assert snap["counts"] == {
        "evaluations": 0, "eligible": 0, "ineligible": 0, "by_disposition": {}, "unavailable_inputs": 0,
    }
    assert snap["candidates"] == [] and snap["unavailable_inputs"] == []
    assert snap["candidates_truncation"] == {"limit": MAX_CANDIDATE_ROWS, "returned": 0, "total": 0, "truncated": False}
    assert snap["arrival_sequence"] == 0
    assert snap["reset"]["count"] == 1  # the start() restart reset
    assert snap["reset"]["boundary"] == "2026-10-09T13:55:00Z"


# ------------------------------------------------------------ populated


async def test_every_disposition_is_reported_with_counts_ids_reasons_and_times():
    reader, clock, _ = await full_reader()
    install(reader)
    snap = snapshot_of(strict_json(await _get()))

    assert snap["counts"]["evaluations"] == 5
    assert snap["counts"]["eligible"] == 1 and snap["counts"]["ineligible"] == 4
    assert snap["counts"]["by_disposition"] == {"error": 1, "gated": 1, "no_opportunity": 1, "opportunity": 2}
    assert snap["freshness"] == {"status": "freshness_policy_configured", "configured": True}

    rows = {row["strategy"]: row for row in snap["candidates"]}
    orb = rows["orb"]
    assert orb["eligible"] is True and orb["reasons"] == [] and orb["disposition"] == "opportunity"
    assert orb["candidate_id"].startswith("cnd1:") and orb["evaluation_id"].startswith("evl1:")
    assert (orb["symbol"], orb["strategy_version"], orb["timeframe"], orb["direction"]) == ("AAPL", "1", "1m", "long")
    assert orb["opportunity"] == {"status": "actionable", "confidence": 0.7, "expected_horizon_minutes": None}
    assert orb["source_candle_ts"] == "2026-10-09T14:00:00Z"
    assert orb["source_interval_start"] == "2026-10-09T14:00:00Z"
    assert orb["source_interval_close"] == "2026-10-09T14:01:00Z"
    assert orb["completed_at"] == "2026-10-09T14:01:01Z"
    assert orb["received_at"] == "2026-10-09T14:01:02Z"
    assert orb["age_seconds"] == 30.0 and orb["max_age_seconds"] == 120.0

    assert rows["gap"]["disposition"] == "no_opportunity" and rows["gap"]["reasons"] == ["no_opportunity"]
    assert rows["gap"]["candidate_id"] is None and rows["gap"]["opportunity"] is None and rows["gap"]["direction"] is None
    assert rows["vwap"]["disposition"] == "gated" and rows["vwap"]["disposition_reason"] == "gate_conditions_not_satisfied"
    assert rows["momentum"]["disposition"] == "error" and rows["momentum"]["disposition_reason"] == "evaluate_failed"
    assert rows["reversal"]["eligible"] is False and "not_actionable" in rows["reversal"]["reasons"]
    assert rows["reversal"]["opportunity"]["status"] == "waiting"


async def test_eligible_candidates_are_ordered_first_then_by_symbol_strategy_version():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0), [opportunity_disposition("zeta"), opportunity_disposition("alpha")], symbol="MSFT"))
    await deliver(reader, clock, make_batch(minute(0), [StrategyDisposition("beta", "1", "no_opportunity")], symbol="AAPL"))
    await deliver(reader, clock, make_batch(minute(0), [opportunity_disposition("orb")], symbol="NVDA"))
    clock.now = minute(1) + timedelta(seconds=10)
    install(reader)
    rows = snapshot_of(strict_json(await _get()))["candidates"]
    assert [(r["eligible"], r["symbol"], r["strategy"]) for r in rows] == [
        (True, "MSFT", "alpha"), (True, "MSFT", "zeta"), (True, "NVDA", "orb"), (False, "AAPL", "beta"),
    ]


async def test_response_is_deterministic_regardless_of_delivery_order():
    async def build(order):
        reader, clock, _ = make_reader()
        batches = {s: make_batch(minute(0), [opportunity_disposition("orb")], symbol=s) for s in ("AAPL", "MSFT", "NVDA")}
        for symbol in order:
            await deliver(reader, clock, batches[symbol], at=minute(1) + timedelta(seconds=2))
        clock.now = minute(1) + timedelta(seconds=10)
        install(reader)
        return snapshot_of(strict_json(await _get()))["candidates"]

    first = await build(["AAPL", "MSFT", "NVDA"])
    second = await build(["NVDA", "AAPL", "MSFT"])
    for rows in (first, second):
        for row in rows:  # receive times differ by arrival order; ordering must not
            row.pop("received_at")
    assert first == second


async def test_two_reads_with_nothing_new_are_identical():
    reader, clock, _ = await full_reader()
    install(reader)
    assert strict_json(await _get()) == strict_json(await _get())


# ------------------------------------------------------------ freshness


async def test_unconfigured_freshness_policy_is_explicit_and_nothing_is_eligible():
    reader, clock, _ = await full_reader(policy=None)
    install(reader)
    snap = snapshot_of(strict_json(await _get()))
    assert snap["freshness"] == {"status": "freshness_policy_unconfigured", "configured": False}
    assert snap["counts"]["eligible"] == 0
    orb = next(r for r in snap["candidates"] if r["strategy"] == "orb")
    assert orb["eligible"] is False and "freshness_policy_unconfigured" in orb["reasons"]
    assert orb["max_age_seconds"] is None


async def test_expired_candidate_reports_age_beyond_the_limit():
    reader, clock, _ = await full_reader()
    clock.now = minute(1) + timedelta(seconds=400)
    install(reader)
    snap = snapshot_of(strict_json(await _get()))
    orb = next(r for r in snap["candidates"] if r["strategy"] == "orb")
    assert orb["eligible"] is False and "expired" in orb["reasons"] and orb["age_seconds"] == 400.0
    assert snap["as_of"] == "2026-10-09T14:07:40Z"


async def test_policy_for_another_timeframe_does_not_configure_this_one():
    reader, clock, _ = await full_reader(policy=CandidateFreshnessPolicy.create({"5m": 600.0}))
    install(reader)
    snap = snapshot_of(strict_json(await _get()))
    orb = next(r for r in snap["candidates"] if r["strategy"] == "orb")
    assert snap["freshness"]["configured"] is True  # a policy exists...
    assert "freshness_policy_unconfigured" in orb["reasons"]  # ...but not for 1m


# ------------------------------------------------------------ prerequisites, diagnostics, resets


async def test_missing_prerequisites_are_listed_and_invalidate_the_earlier_candidate():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)))
    unavailable = make_batch(
        minute(1), None,
        unavailable=[UnavailablePrerequisite("features", "candle_ts_mismatch"), UnavailablePrerequisite("context", "context_unavailable")],
    )
    await deliver(reader, clock, unavailable)
    clock.now = minute(2) + timedelta(seconds=5)
    install(reader)
    snap = snapshot_of(strict_json(await _get()))
    assert snap["counts"]["unavailable_inputs"] == 1 and snap["counts"]["eligible"] == 0
    frame = snap["unavailable_inputs"][0]
    assert (frame["symbol"], frame["timeframe"]) == ("AAPL", "1m")
    assert frame["prerequisites"] == [
        {"name": "context", "reason": "context_unavailable"},
        {"name": "features", "reason": "candle_ts_mismatch"},
    ]
    assert frame["source_candle_ts"] == "2026-10-09T14:01:00Z"
    assert snap["candidates"][0]["reasons"][0] == "invalidated:prerequisite_unavailable"
    assert snap["candidates"][0]["invalidation_reason"] == "prerequisite_unavailable"
    assert snap["candidates_truncation"]["truncated"] is False
    assert snap["unavailable_inputs_truncation"]["total"] == 1


async def test_duplicate_conflict_and_invalid_deliveries_surface_as_diagnostics():
    reader, clock, _ = make_reader()
    batch = make_batch(minute(0))
    await deliver(reader, clock, batch)
    await deliver(reader, clock, batch)  # identical redelivery -> duplicate
    await deliver(reader, clock, make_batch(minute(0), [opportunity_disposition(confidence=0.1)]))  # conflict
    from app.schemas.events.envelope import EventEnvelope, EventType

    await reader._on_batch(EventEnvelope(event_type=EventType.STRATEGY_EVALUATION_COMPLETED, symbol="AAPL", payload={"junk": 1}))
    install(reader)
    snap = snapshot_of(strict_json(await _get()))
    diag = snap["diagnostics"]
    assert diag["deliveries"] == 4 == snap["arrival_sequence"]
    assert diag["by_status"] == {"applied": 1, "conflict": 1, "duplicate": 1, "invalid": 1}
    assert diag["by_reason"]["evaluation_content_conflict"] == 1 and diag["by_reason"]["invalid_payload"] == 1
    problems = diag["recent_problems"]
    assert [p["status"] for p in problems] == ["conflict", "invalid"]  # oldest first, duplicates are not problems
    assert problems[0]["symbol"] == "AAPL" and problems[0]["source_candle_ts"] == "2026-10-09T14:00:00Z"
    assert problems[0]["arrival_sequence"] == 3
    # the first content is kept, never last-write-wins
    assert snap["candidates"][0]["opportunity"]["confidence"] == 0.7


async def test_recent_problems_stay_bounded_by_the_reader():
    reader, clock, _ = make_reader()
    for index in range(30):
        await deliver(reader, clock, make_batch(minute(0)), symbol="__MARKET__")  # invalid envelope each time
    install(reader)
    diag = snapshot_of(strict_json(await _get()))["diagnostics"]
    assert diag["deliveries"] == 30 and diag["by_status"] == {"invalid": 30}
    assert len(diag["recent_problems"]) == 20


async def test_reset_boundary_and_count_are_reported_for_restart_session_and_provider_changes():
    reader, clock, _ = make_reader()
    await deliver(reader, clock, make_batch(minute(0)))
    reader._on_streaming_ownership_changed("takeover")  # what the broker_registry hook invokes
    after_hours = datetime(2026, 10, 9, 20, 0, 30, tzinfo=timezone.utc)
    clock.now = after_hours
    install(reader)
    snap = snapshot_of(strict_json(await _get()))
    assert snap["reset"]["count"] == 3
    assert snap["reset"]["boundary"] == "2026-10-09T20:00:00Z"
    assert snap["diagnostics"]["resets"] == {"provider_change": 1, "restart": 1, "session_change": 1}
    assert snap["counts"]["evaluations"] == 0  # eligibility was cleared by the resets


async def test_pre_reset_batch_is_reported_as_rejected_not_silently_dropped():
    reader, clock, _ = make_reader(start_at=T0 + timedelta(seconds=30))
    await deliver(reader, clock, make_batch(minute(0)), at=minute(1) + timedelta(seconds=2))
    install(reader)
    snap = snapshot_of(strict_json(await _get()))
    assert snap["diagnostics"]["by_status"] == {"rejected": 1}
    assert snap["diagnostics"]["by_reason"] == {"pre_reset_boundary": 1}
    assert snap["diagnostics"]["recent_problems"][0]["reason"] == "pre_reset_boundary"
    assert snap["counts"]["evaluations"] == 0


async def test_reader_mode_is_reported():
    reader, clock, _ = make_reader(mode="paper")
    install(reader)
    assert strict_json(await _get())["reader"] == {"status": "running", "execution_mode": "paper"}


# ------------------------------------------------------------ serialization


async def test_every_timestamp_is_utc_z_text_that_round_trips():
    reader, clock, _ = await full_reader()
    await deliver(
        reader, clock,
        make_batch(minute(1), None, unavailable=[UnavailablePrerequisite("features", "features_unavailable")]),
        at=minute(2) + timedelta(seconds=1),
    )
    clock.now = minute(2) + timedelta(seconds=30)
    install(reader)
    body = strict_json(await _get())
    stamps = []

    def walk(value, key=""):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, str) and (key.endswith("_at") or key.endswith("_ts") or key in ("boundary", "as_of") or key.startswith("source_interval")):
            stamps.append(value)

    walk(body)
    assert len(stamps) >= 10
    for stamp in stamps:
        assert stamp.endswith("Z") and "+" not in stamp
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def test_non_finite_floats_become_null_never_nan():
    assert status_module._finite_or_none(float("nan")) is None
    assert status_module._finite_or_none(float("inf")) is None
    assert status_module._finite_or_none(None) is None
    assert status_module._finite_or_none(1.5) == 1.5


async def test_raw_evidence_and_structural_levels_are_not_exposed():
    reader, clock, _ = make_reader()
    secret = {"conditions": {"x": 1}, "reason": "private-evidence-marker", "nested": {"deep": [1, 2, 3]}}
    disposition = StrategyDisposition("orb", "1", "opportunity", make_opportunity(evidence=secret, structural_invalidation=98.76, structural_target=104.32))
    await deliver(reader, clock, make_batch(minute(0), [disposition]))
    install(reader)
    response = await _get()
    for needle in ("private-evidence-marker", "evidence", "98.76", "104.32", "structural"):
        assert needle not in response.text
    row = snapshot_of(strict_json(response))["candidates"][0]
    assert set(row["opportunity"]) == {"status", "confidence", "expected_horizon_minutes"}


async def test_projection_is_detached_from_reader_state_and_from_other_responses():
    reader, clock, _ = await full_reader()
    snapshot = reader.snapshot(clock.now)
    first = project_candidate_observation(snapshot)
    expected = json.loads(json.dumps(first))
    first["snapshot"]["candidates"].clear()
    first["snapshot"]["counts"]["eligible"] = 999
    first["snapshot"]["diagnostics"]["by_status"]["applied"] = 999
    first["snapshot"]["freshness"]["configured"] = False
    again = project_candidate_observation(snapshot)
    assert again == expected  # the captured snapshot was not reachable from the mutated output
    assert again is not first and again["snapshot"]["candidates"] is not first["snapshot"]["candidates"]
    live = reader.snapshot(clock.now)
    assert project_candidate_observation(live) == expected  # nor was the reader's state


# ------------------------------------------------------------ bounds


async def test_candidate_rows_are_bounded_with_full_population_counts_and_explicit_truncation():
    reader, clock, _ = make_reader()
    total = MAX_CANDIDATE_ROWS + 5
    for index in range(total):
        await deliver(reader, clock, make_batch(minute(0), [opportunity_disposition("orb")], symbol=f"S{index:04d}"), at=minute(1) + timedelta(seconds=2))
    clock.now = minute(1) + timedelta(seconds=10)
    install(reader)
    snap = snapshot_of(strict_json(await _get()))
    assert len(snap["candidates"]) == MAX_CANDIDATE_ROWS
    assert snap["counts"]["evaluations"] == total and snap["counts"]["eligible"] == total  # full population
    assert snap["candidates_truncation"] == {"limit": MAX_CANDIDATE_ROWS, "returned": MAX_CANDIDATE_ROWS, "total": total, "truncated": True}
    assert [r["symbol"] for r in snap["candidates"]] == [f"S{i:04d}" for i in range(MAX_CANDIDATE_ROWS)]  # deterministic prefix


async def test_eligible_rows_survive_truncation_ahead_of_ineligible_ones():
    reader, clock, _ = make_reader()
    for index in range(MAX_CANDIDATE_ROWS):
        await deliver(reader, clock, make_batch(minute(0), [StrategyDisposition("gap", "1", "no_opportunity")], symbol=f"A{index:04d}"))
    await deliver(reader, clock, make_batch(minute(0), [opportunity_disposition("orb")], symbol="ZZZZ"), at=minute(1) + timedelta(seconds=2))
    clock.now = minute(1) + timedelta(seconds=10)
    install(reader)
    snap = snapshot_of(strict_json(await _get()))
    assert snap["candidates"][0]["symbol"] == "ZZZZ" and snap["candidates"][0]["eligible"] is True
    assert snap["candidates_truncation"]["truncated"] is True and snap["counts"]["eligible"] == 1


async def test_unavailable_input_frames_are_bounded_with_explicit_truncation():
    reader, clock, _ = make_reader()
    total = MAX_UNAVAILABLE_FRAMES + 3
    for index in range(total):
        await deliver(
            reader, clock,
            make_batch(minute(0), None, symbol=f"U{index:03d}", unavailable=[UnavailablePrerequisite("features", "features_unavailable")]),
        )
    install(reader)
    snap = snapshot_of(strict_json(await _get()))
    assert len(snap["unavailable_inputs"]) == MAX_UNAVAILABLE_FRAMES
    assert snap["counts"]["unavailable_inputs"] == total
    assert snap["unavailable_inputs_truncation"] == {"limit": MAX_UNAVAILABLE_FRAMES, "returned": MAX_UNAVAILABLE_FRAMES, "total": total, "truncated": True}
    assert [f["symbol"] for f in snap["unavailable_inputs"]] == [f"U{i:03d}" for i in range(MAX_UNAVAILABLE_FRAMES)]


# ------------------------------------------------------------ request contract and errors


class CountingReader:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.snapshots = 0

    def snapshot(self, as_of=None):
        self.snapshots += 1
        return self.inner.snapshot(as_of)


async def test_exactly_one_snapshot_is_captured_per_request():
    reader, clock, _ = await full_reader()
    counting = CountingReader(reader)
    install(counting)
    await _get()
    assert counting.snapshots == 1
    await _get()
    assert counting.snapshots == 2


async def test_query_parameters_cannot_configure_freshness_or_make_anything_eligible():
    reader, clock, _ = await full_reader(policy=None)
    install(reader)
    plain = strict_json(await _get())
    tampered = strict_json(
        await _get({"max_age_seconds": "999999", "freshness_policy": "1m=999999", "eligible": "true", "force": "1", "limit": "1", "as_of": "2000-01-01T00:00:00Z"})
    )
    assert tampered == plain
    assert snapshot_of(plain)["counts"]["eligible"] == 0


async def test_only_get_is_allowed():
    reader, clock, _ = await full_reader()
    install(reader)
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for method in ("post", "put", "patch", "delete"):
            assert (await getattr(client, method)(URL)).status_code == 405


async def test_failure_returns_a_generic_503_without_internal_exception_text(caplog):
    class Exploding:
        def snapshot(self, as_of=None):
            raise RuntimeError("secret /internal/path password=hunter2")

    install(Exploding())
    with caplog.at_level("ERROR"):
        response = await _get()
    assert response.status_code == 503
    assert response.json() == {"detail": "Candidate observation could not be read."}
    assert "hunter2" not in response.text and "secret" not in response.text
    assert "hunter2" in caplog.text  # still available to operators in the log


# ------------------------------------------------------------ no side effects


async def test_reads_add_no_evaluation_authorization_order_event_or_database_activity(monkeypatch):
    reader, clock, bus = await full_reader()
    install(reader)

    def boom(*args, **kwargs):
        raise AssertionError("GET /intelligence/candidate-observation must not publish, query, plan or authorize")

    monkeypatch.setattr(Engine, "connect", boom)
    monkeypatch.setattr(Engine, "begin", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket.socket, "connect_ex", boom)
    monkeypatch.setattr(EventBus, "publish", boom)
    monkeypatch.setattr(EventBus, "subscribe", boom)
    import app.governor.engine as governor_engine
    import app.execution_engine.engine as execution_engine

    for module in (governor_engine, execution_engine):
        for name in ("get_authorizer", "get_execution_engine"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, boom)

    before = reader.snapshot(clock.now)
    subscribers_before = {k: list(v) for k, v in bus._subscribers.items()}
    for _ in range(3):
        assert (await _get()).status_code == 200
    after = reader.snapshot(clock.now)

    assert after.arrival_sequence == before.arrival_sequence  # no delivery, hence no evaluation
    assert after.eligibility.assessments == before.eligibility.assessments
    assert after.diagnostics == before.diagnostics
    assert {k: list(v) for k, v in bus._subscribers.items()} == subscribers_before
    assert reader.snapshot(clock.now).status == "running"  # reading did not stop or restart it


def test_status_module_imports_no_selection_planning_governor_execution_or_database_code():
    tree = ast.parse(Path(status_module.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    forbidden = ("governor", "execution_engine", "planning", "ranking", "selection", "sqlalchemy", "event_bus", "app.models", "app.db", "broker")
    assert not [name for name in imported if any(token in name for token in forbidden)], imported
