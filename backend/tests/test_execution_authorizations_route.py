"""PostgreSQL-backed tests for the persisted Trade authorization projection."""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

import app.api.routes.intelligence as route
import app.execution_engine.authorization_history as history
from app.db.session import SessionLocal
from app.main import app
from app.models.execution_ledger import Trade


MARKER = "__AUTHORIZATION_HISTORY_TEST__"
STAMP = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clean_rows():
    with SessionLocal.begin() as session:
        session.execute(text("DELETE FROM trades WHERE strategy_name = :name"), {"name": MARKER})
    yield
    with SessionLocal.begin() as session:
        session.execute(text("DELETE FROM trades WHERE strategy_name = :name"), {"name": MARKER})


def insert_trade(number: int, **changes) -> Trade:
    values = dict(
        trade_id=uuid.UUID(int=number), symbol="ZZAH1", strategy_name=MARKER,
        strategy_version="gap_v1", direction="BUY", execution_mode="simulated",
        execution_venue="simulated", decision="approved", reasons=[],
        limits_snapshot={"max_concurrent_positions": 1, "fixed_notional_usd": 1000.0,
                         "daily_loss_cap_usd": 100.0},
        thesis={}, status="open", created_at=STAMP,
    )
    values.update(changes)
    with SessionLocal.begin() as session:
        row = Trade(**values)
        session.add(row)
        session.flush()
        session.expunge(row)
    return row


async def get(params=None):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/intelligence/execution-authorizations", params=params)


@pytest.mark.asyncio
async def test_approved_and_rejected_rows_keep_modes_reasons_and_missing_evidence():
    approved = insert_trade(1)
    rejected_unknown = insert_trade(
        2, symbol="ZZAH2", decision="rejected", execution_mode="future_mode",
        execution_venue=None, reasons=["execution_mode_not_permitted"],
        limits_snapshot={}, status=None,
    )
    rejected_null = insert_trade(
        3, symbol="ZZAH3", decision="rejected", execution_mode=None,
        execution_venue=None, reasons=None, limits_snapshot={}, status=None,
    )
    response = await get()
    assert response.status_code == 200
    rows = response.json()["authorizations"]
    assert [item["trade_id"] for item in rows] == [str(rejected_null.trade_id), str(rejected_unknown.trade_id), str(approved.trade_id)]
    assert rows[0]["opportunity_id"] is None
    assert rows[0]["execution_mode"] is None and rows[0]["execution_venue"] is None
    assert rows[0]["reasons"] is None
    assert rows[0]["limits_snapshot"] == {
        "max_concurrent_positions": None, "fixed_notional_usd": None, "daily_loss_cap_usd": None,
    }
    assert rows[1]["execution_mode"] == "future_mode"
    assert rows[1]["reasons"] == ["execution_mode_not_permitted"]
    assert rows[2]["opportunity_id"] == str(approved.trade_id)
    assert rows[2]["execution_venue"] == "simulated"
    assert rows[2]["strategy_name"] == MARKER and rows[2]["strategy_version"] == "gap_v1"
    assert set(rows[2]) == {
        "trade_id", "opportunity_id", "symbol", "strategy_name", "strategy_version",
        "execution_mode", "execution_venue", "decision", "reasons", "created_at", "limits_snapshot",
    }


@pytest.mark.asyncio
async def test_filters_are_exact_and_composable():
    match = insert_trade(11, symbol="ZZAH4", decision="rejected", execution_mode=None,
                         execution_venue=None, reasons=["no_reference_price"], status=None)
    insert_trade(12, symbol="ZZAH4")
    insert_trade(13, symbol="ZZAH5", decision="rejected", execution_mode=None,
                 execution_venue=None, status=None)
    response = await get({"symbol": "ZZAH4", "decision": "rejected"})
    assert [row["trade_id"] for row in response.json()["authorizations"]] == [str(match.trade_id)]
    approved = await get({"symbol": "ZZAH4", "decision": "approved"})
    assert len(approved.json()["authorizations"]) == 1
    assert approved.json()["authorizations"][0]["decision"] == "approved"
    assert (await get({"symbol": "zzah4"})).json() == {"authorizations": []}
    assert (await get({"symbol": "ZZAH"})).json() == {"authorizations": []}
    assert (await get({"symbol": "ZZAH_NONE"})).json() == {"authorizations": []}


@pytest.mark.asyncio
async def test_created_at_then_uuid_desc_and_utc_serialization():
    older = insert_trade(21, symbol="ZZAH6", created_at=STAMP - timedelta(seconds=1))
    tied_low = insert_trade(22, symbol="ZZAH6", created_at=STAMP)
    tied_high = insert_trade(23, symbol="ZZAH6", created_at=STAMP)
    offset = insert_trade(24, symbol="ZZAH6", created_at=(STAMP + timedelta(seconds=1)).astimezone(
        timezone(timedelta(hours=5, minutes=30))))
    response = await get({"symbol": "ZZAH6"})
    rows = response.json()["authorizations"]
    assert [row["trade_id"] for row in rows] == [
        str(offset.trade_id), str(tied_high.trade_id), str(tied_low.trade_id), str(older.trade_id),
    ]
    assert rows[0]["created_at"] == "2026-09-18T14:00:01Z"
    assert all(row["created_at"].endswith("Z") for row in rows)


@pytest.mark.asyncio
async def test_limits_are_curated_exact_strings_with_missing_keys_null():
    row = insert_trade(31, symbol="ZZAH7", limits_snapshot={"max_concurrent_positions": 7,
        "fixed_notional_usd": 1, "unrelated_secret": "do not expose"})
    with SessionLocal.begin() as session:
        session.execute(text("UPDATE trades SET limits_snapshot = CAST(:limits AS jsonb) WHERE trade_id = :trade_id"), {
            "limits": '{"max_concurrent_positions":7,"fixed_notional_usd":1234567890.123456789012345678,"unrelated_secret":"do not expose"}',
            "trade_id": row.trade_id,
        })
    response = await get({"symbol": "ZZAH7"})
    limits = response.json()["authorizations"][0]["limits_snapshot"]
    assert limits == {
        "max_concurrent_positions": 7,
        "fixed_notional_usd": "1234567890.123456789012345678",
        "daily_loss_cap_usd": None,
    }


@pytest.mark.asyncio
async def test_default_limit_and_bounds():
    for number in range(100, 152):
        insert_trade(number, symbol="ZZAH8")
    default = await get({"symbol": "ZZAH8"})
    one = await get({"symbol": "ZZAH8", "limit": 1})
    maximum = await get({"symbol": "ZZAH8", "limit": 500})
    assert len(default.json()["authorizations"]) == 50
    assert len(one.json()["authorizations"]) == 1
    assert len(maximum.json()["authorizations"]) == 52
    assert default.json()["authorizations"][0]["trade_id"] == str(uuid.UUID(int=151))


@pytest.mark.asyncio
async def test_invalid_parameters_never_call_helper(monkeypatch):
    def forbidden(**kwargs):
        raise AssertionError(f"database helper called for invalid request: {kwargs}")

    monkeypatch.setattr(route, "read_execution_authorizations", forbidden)
    for params in ({"limit": 0}, {"limit": 501}, {"limit": "not-a-number"},
                   {"decision": "unknown"}, {"decision": "APPROVED"}):
        assert (await get(params)).status_code == 422


@pytest.mark.asyncio
async def test_worker_query_is_read_only_and_leaves_rows_unchanged(monkeypatch):
    row = insert_trade(201, symbol="ZZAH9")
    observed: list[tuple[int, str]] = []
    event_loop_thread = threading.get_ident()

    def session_factory():
        session = SessionLocal()
        execute = session.execute

        def checked_execute(statement, *args, **kwargs):
            observed.append((threading.get_ident(), execute(text("SHOW transaction_read_only")).scalar_one()))
            return execute(statement, *args, **kwargs)

        session.execute = checked_execute
        return session

    monkeypatch.setattr(history, "SessionLocal", session_factory)
    response = await get({"symbol": "ZZAH9"})
    assert response.status_code == 200
    assert [item["trade_id"] for item in response.json()["authorizations"]] == [str(row.trade_id)]
    assert observed and all(thread != event_loop_thread and readonly == "on" for thread, readonly in observed)
    with SessionLocal() as session:
        assert session.get(Trade, row.trade_id).decision == "approved"


@pytest.mark.asyncio
async def test_blocked_query_does_not_block_health(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def blocked(**kwargs):
        started.set()
        if not release.wait(5):
            raise TimeoutError("blocked authorization read was never released")
        return []

    monkeypatch.setattr(route, "read_execution_authorizations", blocked)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        task = asyncio.create_task(client.get("/intelligence/execution-authorizations"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            health = await asyncio.wait_for(client.get("/health"), 2)
            assert health.status_code == 200 and health.json()["status"] == "ok"
        finally:
            release.set()
        response = await asyncio.wait_for(task, 5)
    assert response.status_code == 200 and response.json() == {"authorizations": []}
