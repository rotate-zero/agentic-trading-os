"""Manual scanner feed requests against an isolated PostgreSQL schema and controlled provider."""
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.api.routes import scanner
from app.core.config import get_settings

pytestmark = pytest.mark.asyncio
SCHEMA = "zz_scanner_feed_request_test"


@pytest.fixture
def universe_db(monkeypatch):
    """Only two scratch tables; no public universe/candle/ledger writes."""
    engine = create_engine(get_settings().database_url)
    with engine.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        connection.execute(text(f"CREATE TABLE {SCHEMA}.symbols (LIKE public.symbols INCLUDING ALL)"))
        connection.execute(text(f"CREATE TABLE {SCHEMA}.scanner_universe_symbols (LIKE public.scanner_universe_symbols INCLUDING ALL)"))
    engine.dispose()
    scoped_engine = create_engine(get_settings().database_url, connect_args={"options": f"-c search_path={SCHEMA}"})
    session_factory = sessionmaker(bind=scoped_engine)
    thread_ids = []
    statements = []

    @event.listens_for(scoped_engine, "before_cursor_execute")
    def record_statement(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    def owned_session():
        thread_ids.append(threading.get_ident())
        return session_factory()

    monkeypatch.setattr(scanner, "SessionLocal", owned_session)

    def insert(*symbols):
        with session_factory.begin() as session:
            for symbol in symbols:
                symbol_id = session.execute(
                    text("INSERT INTO symbols (ticker, is_backtest) VALUES (:ticker, false) RETURNING id"),
                    {"ticker": symbol},
                ).scalar_one()
                session.execute(
                    text("INSERT INTO scanner_universe_symbols (symbol_id) VALUES (:symbol_id)"),
                    {"symbol_id": symbol_id},
                )

    insert.statements = statements

    yield insert, thread_ids
    scoped_engine.dispose()
    cleanup = create_engine(get_settings().database_url)
    with cleanup.begin() as connection:
        connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    cleanup.dispose()


class Provider:
    provider_id = "controlled"

    def __init__(self, *, present=(), fail=()):
        self.connected = True
        self.present = set(present)
        self.fail = set(fail)
        self.calls = []
        self.unsubscribes = []
        self.on_subscribe = None

    def is_connected(self):
        return self.connected

    def get_subscription_snapshot(self):
        return tuple(sorted(self.present))

    async def subscribe(self, symbols):
        self.calls.append(tuple(symbols))
        if self.on_subscribe is not None:
            await self.on_subscribe(symbols[0])
        if symbols[0] in self.fail:
            raise ConnectionError("token=must-never-leak")
        self.present.update(symbols)

    async def unsubscribe(self, symbols):
        self.unsubscribes.append(tuple(symbols))


def current(monkeypatch, provider):
    owner = [provider]
    monkeypatch.setattr(scanner.broker_registry, "get_streaming_provider", lambda: owner[0])
    return owner


async def test_empty_universe_is_success_and_database_read_is_offloaded(universe_db, monkeypatch):
    _, threads = universe_db
    provider = Provider()
    current(monkeypatch, provider)
    result = await scanner.request_universe_feeds()
    assert result == {"status": "completed", "reason": None, "universe": [], "provider": {"provider_id": "controlled", "class_name": "Provider"}, "results": []}
    assert threads and all(thread != threading.get_ident() for thread in threads)
    assert provider.calls == provider.unsubscribes == []


async def test_captured_persisted_universe_local_hits_failures_and_no_writes(universe_db, monkeypatch):
    insert, _ = universe_db
    insert("AAPL", "MSFT", "NVDA")
    provider = Provider(present={"AAPL"}, fail={"MSFT"})
    current(monkeypatch, provider)
    insert.statements.clear()
    result = await scanner.request_universe_feeds()
    assert result["universe"] == ["AAPL", "MSFT", "NVDA"]
    assert result["status"] == "partial_failure"
    assert [(r["symbol"], r["outcome"], r["error_class"]) for r in result["results"]] == [
        ("AAPL", "locally_present", None), ("MSFT", "request_failed", "connection"),
        ("NVDA", "request_returned", None),
    ]
    assert provider.calls == [("MSFT",), ("NVDA",)]
    assert provider.unsubscribes == []
    assert insert.statements and all(statement.lstrip().upper().startswith("SELECT") for statement in insert.statements)
    assert "token=" not in str(result)
    # A request only reads the persisted set; no row is inserted or removed.
    with scanner.SessionLocal() as session:
        assert session.scalar(text("SELECT count(*) FROM scanner_universe_symbols")) == 3
        assert session.scalar(text("SELECT count(*) FROM symbols")) == 3


async def test_failed_local_inventory_read_does_not_claim_a_hit(universe_db, monkeypatch):
    insert, _ = universe_db
    insert("AAPL")
    provider = Provider()
    provider.get_subscription_snapshot = lambda: (_ for _ in ()).throw(RuntimeError("token=secret"))
    current(monkeypatch, provider)
    result = await scanner.request_universe_feeds()
    assert result["results"] == [{"symbol": "AAPL", "outcome": "request_returned", "error_class": None}]
    assert provider.calls == [("AAPL",)] and "token=" not in str(result)


async def test_missing_disconnected_and_read_failure_are_not_empty_success(universe_db, monkeypatch):
    current(monkeypatch, None)
    with pytest.raises(HTTPException, match="No streaming provider") as missing:
        await scanner.request_universe_feeds()
    assert missing.value.status_code == 409
    provider = Provider()
    provider.connected = False
    current(monkeypatch, provider)
    with pytest.raises(HTTPException, match="disconnected"):
        await scanner.request_universe_feeds()
    provider.is_connected = lambda: (_ for _ in ()).throw(RuntimeError("wss://feed?token=secret"))
    with pytest.raises(HTTPException) as availability:
        await scanner.request_universe_feeds()
    assert availability.value.status_code == 409 and "token=" not in availability.value.detail
    monkeypatch.setattr(scanner, "DbUniverseProvider", lambda _factory: type("Broken", (), {"get_core_universe": lambda self: (_ for _ in ()).throw(RuntimeError("secret"))})())
    with pytest.raises(HTTPException) as failed:
        await scanner.request_universe_feeds()
    assert failed.value.status_code == 503 and "secret" not in str(failed.value.detail)


async def test_takeover_stops_remaining_requests_and_keeps_partial_result(universe_db, monkeypatch):
    insert, _ = universe_db
    insert("AAPL", "MSFT", "NVDA")
    old, new = Provider(), Provider()
    owner = current(monkeypatch, old)

    async def takeover(_symbol):
        owner[0] = new

    old.on_subscribe = takeover
    result = await scanner.request_universe_feeds()
    assert result["status"] == "interrupted" and result["reason"] == "provider_changed"
    assert [r["outcome"] for r in result["results"]] == ["request_returned", "not_attempted", "not_attempted"]
    assert old.calls == [("AAPL",)] and new.calls == []


async def test_takeover_inside_last_subscribe_does_not_claim_current_owner_completion(universe_db, monkeypatch):
    insert, _ = universe_db
    insert("AAPL")
    old, new = Provider(), Provider()
    owner = current(monkeypatch, old)

    async def takeover(_symbol):
        owner[0] = new

    old.on_subscribe = takeover
    result = await scanner.request_universe_feeds()
    assert result["status"] == "interrupted" and result["reason"] == "provider_changed"
    assert result["results"][0]["outcome"] == "request_returned"
    assert new.calls == []


async def test_duplicate_batch_rejected_and_edit_after_capture_does_not_change_result(universe_db, monkeypatch):
    insert, _ = universe_db
    insert("AAPL", "MSFT")
    provider = Provider()
    current(monkeypatch, provider)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(_symbol):
        entered.set()
        await release.wait()

    provider.on_subscribe = blocked
    first = asyncio.create_task(scanner.request_universe_feeds())
    await entered.wait()
    with pytest.raises(HTTPException) as duplicate:
        await scanner.request_universe_feeds()
    assert duplicate.value.status_code == 409
    insert("NVDA")  # committed after the first request captured its set
    release.set()
    result = await first
    assert result["universe"] == ["AAPL", "MSFT"]
    assert provider.calls == [("AAPL",), ("MSFT",)]


async def test_replay_slot_rejects_request_without_subscription(universe_db, monkeypatch):
    insert, _ = universe_db
    insert("AAPL")
    provider = Provider()
    current(monkeypatch, provider)
    async with scanner.finnhub_connection_slot():
        with pytest.raises(HTTPException) as conflict:
            await scanner.request_universe_feeds()
    assert conflict.value.status_code == 409
    assert provider.calls == []


async def test_actual_post_route_and_disconnect_during_batch(universe_db, monkeypatch):
    from app.main import app

    insert, _ = universe_db
    insert("AAPL", "MSFT")
    provider = Provider()
    current(monkeypatch, provider)

    async def disconnect(_symbol):
        provider.connected = False

    provider.on_subscribe = disconnect
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/scanner/request-universe-feeds")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "interrupted" and body["reason"] == "provider_disconnected"
    assert [item["outcome"] for item in body["results"]] == ["request_returned", "not_attempted"]
    assert provider.calls == [("AAPL",)] and provider.unsubscribes == []


async def test_replay_beginning_during_offloaded_read_rejects_batch(universe_db, monkeypatch):
    insert, _ = universe_db
    insert("AAPL")
    provider = Provider()
    current(monkeypatch, provider)
    entered, release = threading.Event(), threading.Event()
    original = scanner.DbUniverseProvider

    class BlockedRead(original):
        def get_core_universe(self):
            entered.set()
            release.wait(5)
            return super().get_core_universe()

    monkeypatch.setattr(scanner, "DbUniverseProvider", BlockedRead)
    task = asyncio.create_task(scanner.request_universe_feeds())
    assert await asyncio.to_thread(entered.wait, 5)
    async with scanner.finnhub_connection_slot():
        release.set()
        with pytest.raises(HTTPException) as conflict:
            await task
    assert conflict.value.status_code == 409 and provider.calls == []
