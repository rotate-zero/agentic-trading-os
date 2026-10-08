"""Protected-feed reconciliation status (task `protected-feed-reconciliation-status`).

Real ProtectedFeedReconciler cycles run against controlled providers and symbol
readers; GET /market/protected-feed-status is exercised through the real app.
The snapshot is request evidence only, is immutable to consumers, and reading
it never starts a cycle, a database read, a subscription or a provider call.
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

import app.services.protected_feed_reconciliation as module
from app.db.session import SessionLocal
from app.main import app as fastapi_app
from app.services import broker_registry
from app.services.protected_feed_reconciliation import ProtectedFeedReconciler

SECRET = "postgresql://trader:s3cr3t-pw@db.internal:5432/trading"
BASE = datetime(2026, 10, 8, 6, 0, tzinfo=timezone.utc)


class Clock:
    """Strictly increasing pinned clock, one second per call."""

    def __init__(self) -> None:
        self.now = BASE

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(module, "_utcnow", Clock())
    fastapi_app.state.protected_feed_status_reader = None
    yield
    fastapi_app.state.protected_feed_status_reader = None
    broker_registry.clear_all()


class Provider:
    provider_id = "fake-stream"

    def __init__(self, *, inventory: bool = True) -> None:
        self.connected = True
        self.symbols: set[str] = set()
        self.calls: list[str] = []
        self.fail: dict[str, Exception] = {}
        self.inventory = inventory
        self.connected_checks = 0
        self.snapshot_reads = 0

    def is_connected(self):
        self.connected_checks += 1
        return self.connected

    def get_subscription_snapshot(self):
        self.snapshot_reads += 1
        if not self.inventory:
            raise RuntimeError(SECRET)
        return tuple(self.symbols)

    async def subscribe(self, symbols):
        for symbol in symbols:
            self.calls.append(symbol)
            if symbol in self.fail:
                raise self.fail[symbol]
            self.symbols.add(symbol)

    async def disconnect(self):
        self.connected = False

    def on_tick(self, callback):
        pass


def patch_read(monkeypatch, source):
    """`source` is a callable returning a set or raising."""
    reads: list[int] = []

    def read(_factory):
        reads.append(1)
        return frozenset(source())

    monkeypatch.setattr(module, "read_protected_symbols", read)
    return reads


async def get_status() -> dict:
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/market/protected-feed-status")
    assert response.status_code == 200
    return response.json()


# --- availability states ---------------------------------------------------


async def test_no_reconciler_installed_is_distinct_from_installed_but_never_attempted():
    body = await get_status()
    assert body["status"] == "unavailable"
    assert body["reason"] == "reconciler_not_installed"
    assert body["reconciler"] is None and body["protected_set"] is None and body["requests"] is None

    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner
    body = await get_status()
    assert body["status"] == "available" and body["reason"] is None
    assert body["reconciler"]["state"] == "never_attempted"
    assert body["reconciler"]["running"] is False
    assert body["reconciler"]["attempts_started"] == 0
    assert body["reconciler"]["last_attempt_at"] is None
    assert body["protected_set"]["availability"] == "never_read"
    assert body["protected_set"]["symbols"] is None  # never an empty list before any read
    assert body["requests"] is None and body["provider"] is None


async def test_successful_empty_set_is_not_a_failure_or_a_missing_read(monkeypatch):
    patch_read(monkeypatch, lambda: set())
    provider = Provider()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner
    await owner.reconcile_once()
    body = await get_status()
    assert body["reconciler"]["state"] == "completed"
    assert body["protected_set"]["availability"] == "read"
    assert body["protected_set"]["symbols"] == [] and body["protected_set"]["count"] == 0
    assert body["protected_set"]["latest_read"] == "succeeded"
    assert body["protected_set"]["latest_read_error"] is None
    assert body["requests"]["entries"] == [] and body["requests"]["inventory_available"] is True
    assert provider.calls == []


async def test_no_streaming_provider_and_disconnected_provider_are_distinct(monkeypatch):
    patch_read(monkeypatch, lambda: {"ZZA"})
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner

    await owner.reconcile_once()
    body = await get_status()
    assert body["reconciler"]["state"] == "no_streaming_provider"
    assert body["provider"] is None
    assert body["protected_set"]["symbols"] == ["ZZA"]  # the read itself succeeded
    assert body["requests"] is None  # nothing was requested

    provider = Provider()
    provider.connected = False
    await broker_registry.take_over_streaming(provider)
    await owner.reconcile_once()
    body = await get_status()
    assert body["reconciler"]["state"] == "provider_disconnected"
    assert body["provider"] == {"id": "fake-stream", "class_name": "Provider", "connected": False}
    assert body["requests"] is None
    assert provider.calls == [] and provider.snapshot_reads == 0


async def test_provider_connection_check_failure_is_a_safe_code(monkeypatch):
    patch_read(monkeypatch, lambda: {"ZZA"})

    class Broken(Provider):
        def is_connected(self):
            raise RuntimeError(SECRET)

    await broker_registry.take_over_streaming(Broken())
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner
    await owner.reconcile_once()
    body = await get_status()
    assert body["reconciler"]["state"] == "provider_check_failed"
    assert body["provider"]["connected"] is None
    assert "s3cr3t" not in str(body) and "RuntimeError" not in str(body)


# --- request outcomes -------------------------------------------------------


async def test_local_inventory_hits_returned_requests_and_partial_failures(monkeypatch):
    patch_read(monkeypatch, lambda: {"ZZA", "ZZB", "ZZC", "ZZD"})
    provider = Provider()
    provider.symbols.add("ZZA")  # locally present
    provider.fail["ZZB"] = RuntimeError(SECRET)
    provider.fail["ZZC"] = asyncio.TimeoutError(SECRET)
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner
    await owner.reconcile_once()

    body = await get_status()
    assert body["reconciler"]["state"] == "completed_with_failures"
    assert provider.calls == ["ZZB", "ZZC", "ZZD"]  # ZZA not re-requested
    entries = {e["symbol"]: (e["outcome"], e["error_class"]) for e in body["requests"]["entries"]}
    assert entries == {
        "ZZA": ("locally_present", None),
        "ZZB": ("request_failed", "other"),
        "ZZC": ("request_failed", "timeout"),
        "ZZD": ("request_returned", None),
    }
    assert [e["symbol"] for e in body["requests"]["entries"]] == ["ZZA", "ZZB", "ZZC", "ZZD"]
    assert body["requests"]["counts"] == {
        "locally_present": 1, "request_returned": 1, "request_failed": 2, "no_outcome": 0,
    }
    assert body["requests"]["basis"] == "request_evidence_only"
    # Raw exception text, DSNs and credentials never reach the response.
    assert "s3cr3t" not in str(body) and "trader" not in str(body) and "postgresql" not in str(body)

    # A later cycle retries only what is not locally recorded.
    provider.fail.clear()
    await owner.reconcile_once()
    body = await get_status()
    assert body["reconciler"]["state"] == "completed"
    assert {e["symbol"]: e["outcome"] for e in body["requests"]["entries"]} == {
        "ZZA": "locally_present", "ZZB": "request_returned",
        "ZZC": "request_returned", "ZZD": "locally_present",
    }


async def test_inventory_unavailable_requests_everything_and_says_so(monkeypatch):
    patch_read(monkeypatch, lambda: {"ZZA"})
    provider = Provider(inventory=False)
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner
    await owner.reconcile_once()
    body = await get_status()
    assert body["requests"]["inventory_available"] is False
    assert [e["outcome"] for e in body["requests"]["entries"]] == ["request_returned"]
    assert "s3cr3t" not in str(body)


async def test_read_failure_after_success_keeps_last_read_and_never_looks_empty(monkeypatch):
    state = {"fail": False}

    def source():
        if state["fail"]:
            raise RuntimeError(SECRET)
        return {"ZZA", "ZZB"}

    patch_read(monkeypatch, source)
    provider = Provider()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner

    await owner.reconcile_once()
    good = await get_status()
    state["fail"] = True
    await owner.reconcile_once()
    bad = await get_status()

    assert bad["reconciler"]["state"] == "protected_set_read_failed"
    assert bad["protected_set"]["latest_read"] == "failed"
    assert bad["protected_set"]["latest_read_error"] == "protected_set_read_failed"
    assert bad["protected_set"]["latest_read_attempt_at"] > good["protected_set"]["latest_read_attempt_at"]
    # Retained, labelled with the time of the successful read; not empty.
    assert bad["protected_set"]["symbols"] == ["ZZA", "ZZB"]
    assert bad["protected_set"]["read_at"] == good["protected_set"]["read_at"]
    assert bad["requests"] == good["requests"]
    assert provider.calls == ["ZZA", "ZZB"]  # the failed cycle requested nothing
    assert "s3cr3t" not in str(bad)


async def test_first_read_failure_is_never_read_not_empty(monkeypatch):
    def source():
        raise RuntimeError(SECRET)

    patch_read(monkeypatch, source)
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner
    await owner.reconcile_once()
    body = await get_status()
    assert body["reconciler"]["state"] == "protected_set_read_failed"
    assert body["protected_set"]["availability"] == "never_read"
    assert body["protected_set"]["symbols"] is None and body["protected_set"]["count"] is None
    assert body["protected_set"]["latest_read"] == "failed"
    assert "s3cr3t" not in str(body)


async def test_takeover_records_the_new_provider_with_its_own_timestamp(monkeypatch):
    patch_read(monkeypatch, lambda: {"ZZA"})
    first = Provider()
    await broker_registry.take_over_streaming(first)
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner
    await owner.reconcile_once()
    before = await get_status()

    class Replacement(Provider):
        provider_id = "replacement-stream"

    replacement = Replacement()
    await broker_registry.take_over_streaming(replacement)
    await owner.reconcile_once()
    after = await get_status()

    assert before["requests"]["provider"]["id"] == "fake-stream"
    assert after["requests"]["provider"] == {"id": "replacement-stream", "class_name": "Replacement"}
    assert after["requests"]["recorded_at"] > before["requests"]["recorded_at"]
    assert replacement.calls == ["ZZA"]


async def test_provider_replaced_mid_cycle_is_interrupted_with_no_outcome_for_the_rest(monkeypatch):
    patch_read(monkeypatch, lambda: {"ZZA", "ZZB", "ZZC"})

    class Swapping(Provider):
        async def subscribe(self, symbols):
            await super().subscribe(symbols)
            if len(self.calls) == 1:  # replaced after the first request returns
                await broker_registry.take_over_streaming(Provider())

    first = Swapping()
    await broker_registry.take_over_streaming(first)
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner
    await owner.reconcile_once()
    body = await get_status()
    assert body["reconciler"]["state"] == "interrupted"
    assert [(e["symbol"], e["outcome"]) for e in body["requests"]["entries"]] == [
        ("ZZA", "request_returned"), ("ZZB", "no_outcome"), ("ZZC", "no_outcome"),
    ]


async def test_provider_disconnect_mid_cycle_is_reported_as_disconnected(monkeypatch):
    patch_read(monkeypatch, lambda: {"ZZA", "ZZB"})

    class Dropping(Provider):
        async def subscribe(self, symbols):
            await super().subscribe(symbols)
            self.connected = False

    provider = Dropping()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner
    await owner.reconcile_once()
    body = await get_status()
    assert body["reconciler"]["state"] == "provider_disconnected"
    assert body["provider"]["connected"] is False
    assert [e["outcome"] for e in body["requests"]["entries"]] == ["request_returned", "no_outcome"]


# --- lifecycle: running, in progress, shutdown -----------------------------


async def test_running_and_in_progress_flags_and_shutdown_records_interrupted(monkeypatch):
    patch_read(monkeypatch, lambda: {"ZZA", "ZZB"})
    entered = asyncio.Event()
    blocker = asyncio.Event()

    class Blocking(Provider):
        async def subscribe(self, symbols):
            entered.set()
            await blocker.wait()
            await super().subscribe(symbols)

    provider = Blocking()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal, interval_seconds=60.0)
    fastapi_app.state.protected_feed_status_reader = owner
    owner.start()
    try:
        await asyncio.wait_for(entered.wait(), 2)
        body = await get_status()
        assert body["reconciler"]["running"] is True
        assert body["reconciler"]["cycle_in_progress"] is True
        assert body["reconciler"]["state"] == "first_attempt_in_progress"
        assert body["reconciler"]["last_attempt_at"] is not None
        assert body["reconciler"]["last_completed_at"] is None

        await asyncio.wait_for(owner.stop(), 2)
        blocker.set()
        await asyncio.sleep(0)
    finally:
        blocker.set()
        await owner.stop()

    body = await get_status()
    assert body["reconciler"]["running"] is False
    assert body["reconciler"]["cycle_in_progress"] is False
    assert body["reconciler"]["state"] == "interrupted"
    assert body["reconciler"]["interval_seconds"] == 60.0
    assert [(e["symbol"], e["outcome"]) for e in body["requests"]["entries"]] == [
        ("ZZA", "no_outcome"), ("ZZB", "no_outcome"),
    ]
    assert provider.calls == []  # nothing was admitted after shutdown


async def test_periodic_cycles_advance_attempt_counters(monkeypatch):
    patch_read(monkeypatch, lambda: set())
    owner = ProtectedFeedReconciler(SessionLocal, interval_seconds=0.01)
    await broker_registry.take_over_streaming(Provider())
    owner.start()
    try:
        deadline = time.monotonic() + 2
        while owner.get_snapshot().attempts_completed < 3 and time.monotonic() < deadline:
            await asyncio.sleep(0.005)
        snapshot = owner.get_snapshot()
        assert snapshot.attempts_completed >= 3
        assert snapshot.attempts_started >= snapshot.attempts_completed
        assert snapshot.last_completed_at > snapshot.last_attempt_at - timedelta(seconds=1)
    finally:
        await owner.stop()


# --- immutability and side-effect freedom ----------------------------------


async def test_snapshot_cannot_be_mutated_by_consumers(monkeypatch):
    patch_read(monkeypatch, lambda: {"ZZA", "ZZB"})
    provider = Provider()
    provider.fail["ZZB"] = RuntimeError("x")
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal)
    await owner.reconcile_once()
    snapshot = owner.get_snapshot()

    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.attempts_started = 99  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.last_set_read.symbols = ()  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.request_record.requests[0].outcome = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        snapshot.last_set_read.symbols.append("ZZZ")  # type: ignore[attr-defined]
    assert isinstance(snapshot.last_set_read.symbols, tuple)
    assert isinstance(snapshot.request_record.requests, tuple)

    # Nothing in the snapshot is a mutable container the owner also holds.
    def containers(value):
        if dataclasses.is_dataclass(value):
            for field in dataclasses.fields(value):
                yield from containers(getattr(value, field.name))
        elif isinstance(value, tuple):
            for item in value:
                yield from containers(item)
        else:
            yield value

    assert not [v for v in containers(snapshot) if isinstance(v, (list, dict, set))]

    # Two reads are equal in content and independent of later cycles.
    again = owner.get_snapshot()
    assert again == snapshot
    provider.fail.clear()
    await owner.reconcile_once()
    assert snapshot.request_record.requests[1].outcome == "request_failed"
    assert owner.get_snapshot() != snapshot


async def test_response_is_a_fresh_projection_not_shared_state(monkeypatch):
    patch_read(monkeypatch, lambda: {"ZZA"})
    await broker_registry.take_over_streaming(Provider())
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner
    await owner.reconcile_once()
    first = await get_status()
    first["protected_set"]["symbols"].append("MUTATED")
    first["requests"]["entries"].clear()
    second = await get_status()
    assert second["protected_set"]["symbols"] == ["ZZA"]
    assert len(second["requests"]["entries"]) == 1


async def test_status_reads_have_no_side_effects(monkeypatch):
    reads = patch_read(monkeypatch, lambda: {"ZZA", "ZZB"})
    provider = Provider()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner

    # Never attempted: reading must not start a cycle.
    for _ in range(3):
        body = await get_status()
    assert body["reconciler"]["state"] == "never_attempted"
    assert reads == [] and provider.calls == []
    assert provider.connected_checks == 0 and provider.snapshot_reads == 0

    await owner.reconcile_once()
    baseline = (len(reads), list(provider.calls), provider.connected_checks, provider.snapshot_reads)
    task_before = owner._task
    for _ in range(5):
        await get_status()
        owner.get_snapshot()
    assert (len(reads), list(provider.calls), provider.connected_checks, provider.snapshot_reads) == baseline
    assert owner._task is task_before and not owner._lock.locked()
    assert owner.get_snapshot().attempts_started == 1


async def test_reading_status_while_a_cycle_holds_the_lock_does_not_wait_or_start_work(monkeypatch):
    patch_read(monkeypatch, lambda: {"ZZA"})
    entered = asyncio.Event()
    blocker = asyncio.Event()

    class Blocking(Provider):
        async def subscribe(self, symbols):
            entered.set()
            await blocker.wait()
            await super().subscribe(symbols)

    provider = Blocking()
    await broker_registry.take_over_streaming(provider)
    owner = ProtectedFeedReconciler(SessionLocal)
    fastapi_app.state.protected_feed_status_reader = owner
    cycle = asyncio.create_task(owner.reconcile_once())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        body = await asyncio.wait_for(get_status(), 1)  # would hang if it queued on the lock
        assert body["reconciler"]["cycle_in_progress"] is True
        assert provider.calls == []  # the one in-flight request has not returned yet
    finally:
        blocker.set()
        await cycle
        await owner.stop()
    assert provider.calls == ["ZZA"]  # the status read added no request of its own
    assert owner.get_snapshot().attempts_started == 1


async def test_misbehaving_reader_returns_a_safe_code():
    class Broken:
        def get_snapshot(self):
            raise RuntimeError(SECRET)

    fastapi_app.state.protected_feed_status_reader = Broken()
    body = await get_status()
    assert body["status"] == "unavailable" and body["reason"] == "snapshot_read_failed"
    assert "s3cr3t" not in str(body)


# --- lifespan ownership -----------------------------------------------------


def test_lifespan_installs_the_reader_for_the_running_owner_and_clears_it_on_shutdown(monkeypatch):
    with TestClient(fastapi_app) as client:
        owner = fastapi_app.state.protected_feed_status_reader
        assert isinstance(owner, ProtectedFeedReconciler)
        deadline = time.monotonic() + 5
        body = client.get("/market/protected-feed-status").json()
        while body["reconciler"]["attempts_completed"] < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
            body = client.get("/market/protected-feed-status").json()
        assert body["status"] == "available"
        assert body["reconciler"]["running"] is True
        assert body["reconciler"]["attempts_completed"] >= 1
    assert fastapi_app.state.protected_feed_status_reader is None
    assert owner.get_snapshot().running is False  # stopped, not abandoned


def test_reader_is_not_installed_when_startup_is_blocked(monkeypatch):
    from app.portfolio_state.reconciliation import ReconciliationReport

    async def discrepant(*args):
        return ReconciliationReport(discrepancies=["mismatch A"])

    monkeypatch.setattr("app.portfolio_state.reconciliation.reconcile_with_venue", discrepant)
    with TestClient(fastapi_app) as client:
        body = client.get("/market/protected-feed-status").json()
    assert body["status"] == "unavailable" and body["reason"] == "reconciler_not_installed"
    assert fastapi_app.state.protected_feed_status_reader is None


def test_rollback_when_the_owner_fails_to_start_never_exposes_or_leaves_a_reader(monkeypatch):
    started = []
    original_start = ProtectedFeedReconciler.start

    def start_then_fail(self):
        original_start(self)
        started.append(self)
        raise RuntimeError(SECRET)

    monkeypatch.setattr(ProtectedFeedReconciler, "start", start_then_fail)
    with TestClient(fastapi_app) as client:
        body = client.get("/market/protected-feed-status").json()
    assert body["status"] == "unavailable" and body["reason"] == "reconciler_not_installed"
    assert "s3cr3t" not in str(body)
    assert len(started) == 1 and started[0].get_snapshot().running is False  # rollback stopped it
    assert fastapi_app.state.protected_feed_status_reader is None
