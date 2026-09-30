"""governor-approval-evidence: pure validator + AuthorizerStub carry tests.

No database here (the real-PostgreSQL half is test_governor_evidence_postgres.py).
The engine tests use test_governor_engine.py's fakes; they prove only what the
engine hands to the ledger port. Refusal of a bad payload is the ledger's job
and is proven against real PostgreSQL.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.event_bus.bus import EventBus
from app.governor.evidence import EvidenceError, detach_evidence, evidence_equal
from app.schemas.events.envelope import EventEnvelope, EventType
from tests.test_governor_engine import (
    _build_and_start,
    _opportunity_payload,
)

GOOD = {
    "conditions": {"or_minutes": 5, "close": 101.25, "breakout": True, "note": None, "bars": [1, 2.5, "x"]},
    "reason": "ORB buy \u2014 unicode ok",
    "basis": "closed",
}


# --- detach_evidence ---------------------------------------------------------


def test_detach_returns_equal_but_fully_detached_copy():
    copy = detach_evidence(GOOD)
    assert copy == GOOD
    assert copy is not GOOD
    assert copy["conditions"] is not GOOD["conditions"]
    assert copy["conditions"]["bars"] is not GOOD["conditions"]["bars"]
    copy["conditions"]["bars"].append(99)
    assert GOOD["conditions"]["bars"] == [1, 2.5, "x"]


def test_detach_preserves_empty_dict_and_scalar_kinds():
    assert detach_evidence({}) == {}
    out = detach_evidence({"t": True, "i": 1, "f": 1.5, "n": None})
    assert out["t"] is True and type(out["i"]) is int and type(out["f"]) is float and out["n"] is None


def _cycle():
    d: dict = {}
    d["self"] = d
    return d


def _too_deep():
    d: dict = {}
    cur = d
    for _ in range(64):
        cur["k"] = {}
        cur = cur["k"]
    return d


@pytest.mark.parametrize(
    "bad",
    [
        {"x": float("nan")},
        {"x": float("inf")},
        {"x": float("-inf")},
        {"nested": {"deep": [1, float("nan")]}},
        {"x": datetime(2026, 9, 30, tzinfo=timezone.utc)},
        {"x": {1, 2}},
        {"x": (1, 2)},
        {"x": Decimal("1.5")},
        {"x": b"bytes"},
        {"x": object()},
        {1: "int key"},
        {None: "none key"},
        {"nested": {2: "int key"}},
        _cycle(),
        _too_deep(),
        [],
        "not a dict",
        None,
    ],
    ids=lambda v: repr(v)[:40],
)
def test_detach_refuses_non_json_and_never_repairs(bad):
    with pytest.raises(EvidenceError):
        detach_evidence(bad)


def test_error_names_the_offending_path():
    with pytest.raises(EvidenceError, match=r"evidence\.conditions\.close"):
        detach_evidence({"conditions": {"close": float("nan")}})


# --- evidence_equal ----------------------------------------------------------


def test_equality_is_structural_and_does_not_conflate_bool_with_int():
    assert evidence_equal({"a": [1, {"b": None}]}, {"a": [1, {"b": None}]})
    assert not evidence_equal({"a": True}, {"a": 1})
    assert not evidence_equal({"a": 1}, {"a": True})
    assert not evidence_equal({"a": 1}, {"a": 2})
    assert not evidence_equal({"a": 1}, {"a": 1, "b": 2})
    assert not evidence_equal({"a": [1, 2]}, {"a": [2, 1]})
    assert not evidence_equal({"a": "1"}, {"a": 1})
    # JSONB may hand back a large float as an integer; that is the same record.
    assert evidence_equal({"v": 10**22}, {"v": 1e22})


# --- AuthorizerStub carries evidence to the ledger port ----------------------


@pytest.mark.asyncio
async def test_approved_record_carries_the_opportunitys_evidence(monkeypatch):
    bus, authorizer, published, ledger = await _build_and_start(monkeypatch)
    try:
        await _publish(bus, evidence=GOOD)
        await asyncio.sleep(0.15)
        assert len(ledger.committed) == 1
        assert ledger.committed[0].decision == "approved"
        assert ledger.committed[0].evidence == GOOD
        assert EventType.ORDER_APPROVED in [e.event_type for e in published]
    finally:
        await authorizer.stop()
        await bus.stop()


@pytest.mark.asyncio
async def test_rejected_record_never_carries_evidence_even_when_it_is_invalid(monkeypatch):
    """Authorization rules are unchanged: a rule rejection is still audited and
    published, and its (possibly bad) evidence is simply not carried."""
    bus, authorizer, published, ledger = await _build_and_start(monkeypatch, is_regular_session=False)
    try:
        await _publish(bus, evidence={"conditions": {"close": float("nan")}})
        await asyncio.sleep(0.15)
        assert len(ledger.committed) == 1
        assert ledger.committed[0].decision == "rejected"
        assert ledger.committed[0].evidence is None
        assert EventType.PLAN_REJECTED in [e.event_type for e in published]
    finally:
        await authorizer.stop()
        await bus.stop()


async def _publish(bus: EventBus, symbol: str = "AAPL", **overrides) -> None:
    """Like test_governor_engine._publish_opportunity, but the override is applied
    AFTER model_dump so invalid evidence reaches the engine untouched."""
    evidence = overrides.pop("evidence", None)
    payload = _opportunity_payload(**overrides)
    if evidence is not None:
        payload["evidence"] = evidence
    await bus.publish(EventEnvelope(event_type=EventType.OPPORTUNITY_CREATED, symbol=symbol, payload=payload))

