"""PostgreSQL-backed tests for GET /intelligence/execution-trades/{trade_id}."""
from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text

import app.api.routes.intelligence as route
import app.execution_engine.trade_detail as detail
from app.db.session import SessionLocal
from app.main import app
from app.models.execution_ledger import ExitRequest, Fill, Order, Position, Trade
from app.models.trading_intelligence import StrategyOutcomeRecord

MARKER = "__TRADE_DETAIL_TEST__"
STAMP = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
IST = timezone(timedelta(hours=5, minutes=30))


def _clean() -> None:
    with SessionLocal.begin() as session:
        ids = "SELECT trade_id FROM trades WHERE strategy_name = :n"
        params = {"n": MARKER}
        session.execute(text(f"DELETE FROM exit_requests WHERE position_id IN (SELECT position_id FROM positions WHERE trade_id IN ({ids}))"), params)
        session.execute(text(f"DELETE FROM fills WHERE client_order_id IN (SELECT client_order_id FROM orders WHERE trade_id IN ({ids}))"), params)
        session.execute(text(f"DELETE FROM orders WHERE trade_id IN ({ids})"), params)
        session.execute(text(f"DELETE FROM positions WHERE trade_id IN ({ids})"), params)
        session.execute(text("DELETE FROM trades WHERE strategy_name = :n"), params)
        session.execute(text("DELETE FROM strategy_outcomes WHERE strategy_name = :n"), params)


@pytest.fixture(autouse=True)
def clean_rows():
    _clean()
    yield
    _clean()


def tid(number: int) -> uuid.UUID:
    return uuid.UUID(int=0x7DE7A110000 + number)


def add_all(*rows) -> None:
    with SessionLocal.begin() as session:
        session.add_all(rows)
        session.flush()
        for row in rows:
            session.expunge(row)  # keep explicitly-set attributes readable after commit


def trade(number: int, **changes) -> Trade:
    values = dict(
        trade_id=tid(number), symbol="ZZTD1", strategy_name=MARKER, strategy_version="gap_v1",
        direction="BUY", execution_mode="simulated", execution_venue="simulated",
        decision="approved", reasons=[], status="open", thesis={}, created_at=STAMP,
        limits_snapshot={"max_concurrent_positions": 1, "fixed_notional_usd": 1000.0, "daily_loss_cap_usd": 100.0},
    )
    values.update(changes)
    row = Trade(**values)
    add_all(row)
    return row


def rejected(number: int, **changes) -> Trade:
    base = dict(decision="rejected", execution_mode=None, execution_venue=None, status=None,
                reasons=["no_reference_price"], limits_snapshot={})
    base.update(changes)
    return trade(number, **base)


def order(trade_number: int, suffix: str, **changes) -> Order:
    values = dict(
        client_order_id=f"{tid(trade_number)}:{suffix}", trade_id=tid(trade_number), execution_mode="simulated",
        execution_venue="simulated", symbol="ZZTD1", side="BUY", position_effect="open", qty=100,
        order_type="market", status="approved", created_at=STAMP, updated_at=STAMP,
    )
    values.update(changes)
    row = Order(**values)
    add_all(row)
    return row


def fill(order_id: str, venue_fill_id: str, qty: int, price: str, **changes) -> Fill:
    values = dict(client_order_id=order_id, execution_venue="simulated", venue_fill_id=venue_fill_id,
                  qty=qty, price=Decimal(price), venue_ts=STAMP, commission=None)
    values.update(changes)
    row = Fill(**values)
    add_all(row)
    return row


def position(trade_number: int, position_id: uuid.UUID | None = None, **changes) -> Position:
    values = dict(
        position_id=position_id or uuid.uuid4(), trade_id=tid(trade_number), execution_mode="simulated",
        execution_venue="simulated", symbol="ZZTD1", side="BUY", qty=100, avg_price=Decimal("10.123457"),
        opened_at=STAMP, status="open", exit_attempt=0,
    )
    values.update(changes)
    row = Position(**values)
    add_all(row)
    return row


def exit_request(position_id: uuid.UUID, **changes) -> ExitRequest:
    values = dict(position_id=position_id, exit_reason="stop", trigger_price=Decimal("9.500000"), trigger_ts=STAMP)
    values.update(changes)
    row = ExitRequest(**values)
    add_all(row)
    return row


def outcome(**changes) -> StrategyOutcomeRecord:
    when = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
    values = dict(
        outcome_id=uuid.uuid4(), opportunity_id=uuid.uuid4(), schema_version=2, strategy_name=MARKER,
        strategy_version="gap_v1", symbol="ZZTD1", origin="auto", is_backtest=False, backtest_run_id=None,
        execution_mode="simulated", execution_venue="simulated", trading_day=when.date(),
        setup_detected_at=when, signal_confirmed_at=None, decided_at=when, entry_filled_at=when,
        exit_filled_at=when + timedelta(minutes=5), holding_seconds=300, direction="BUY",
        entry_price=Decimal("10.123457"), entry_qty=Decimal("100"), exit_price=Decimal("10.500000"),
        exit_qty=Decimal("100"), commission_total=None, slippage_entry=Decimal("0"),
        realized_pnl=Decimal("37.654300"), realized_r=Decimal("1.2346"), exit_reason="target",
        structural_invalidation=Decimal("9"), structural_target=Decimal("11"), final_stop=Decimal("9"),
        final_target=Decimal("11"), confidence_at_signal=Decimal("0.5"), evidence={"basis": "live"},
        market_state_at_entry=None, context_at_entry=None, market_state_at_exit=None, context_at_exit=None,
        snapshot_missing_reasons={k: "recorder_unavailable" for k in (
            "market_state_at_entry", "context_at_entry", "market_state_at_exit", "context_at_exit")},
    )
    values.update(changes)
    row = StrategyOutcomeRecord(**values)
    add_all(row)
    return row


async def get(trade_id) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(f"/intelligence/execution-trades/{trade_id}")


EMPTY_OUTCOME = {"outcome_status": None, "outcome_id": None, "summary": None}


@pytest.mark.asyncio
async def test_rejected_attempts_are_valid_details_with_empty_downstream_and_raw_modes():
    trade(1)  # unrelated approved trade must not leak in
    unknown_mode = rejected(2, execution_mode="future_mode", reasons=["execution_mode_not_permitted"])
    null_mode = rejected(3, reasons=None)
    body = (await get(unknown_mode.trade_id)).json()
    assert body["trade"]["decision"] == "rejected" and body["trade"]["opportunity_id"] is None
    assert body["trade"]["execution_mode"] == "future_mode" and body["trade"]["execution_venue"] is None
    assert body["trade"]["reasons"] == ["execution_mode_not_permitted"] and body["trade"]["status"] is None
    assert [body[k] for k in ("orders", "fills", "positions", "exit_requests")] == [[], [], [], []]
    assert body["outcome"] == EMPTY_OUTCOME
    nulls = (await get(null_mode.trade_id)).json()["trade"]
    assert nulls["execution_mode"] is None and nulls["reasons"] is None
    assert nulls["limits_snapshot"] == {"max_concurrent_positions": None, "fixed_notional_usd": None, "daily_loss_cap_usd": None}


@pytest.mark.asyncio
async def test_approved_without_order_is_not_an_order_or_fill():
    approved = trade(10)
    response = await get(approved.trade_id)
    assert response.status_code == 200
    body = response.json()
    assert body["trade"]["decision"] == "approved" and body["trade"]["opportunity_id"] == str(approved.trade_id)
    assert body["trade"]["direction"] == "BUY" and body["trade"]["origin"] == "auto"
    assert body["orders"] == body["fills"] == body["positions"] == body["exit_requests"] == []
    assert body["outcome"] == EMPTY_OUTCOME
    assert set(body) == {"trade", "orders", "fills", "positions", "exit_requests", "outcome"}


@pytest.mark.asyncio
async def test_partial_entry_and_partial_exit_with_multiple_exit_attempts_and_linked_outcome():
    trade(20, status="closing")
    entry = order(20, "entry", status="partially_filled", qty=100, limit_price=Decimal("10.250000"), order_type="limit")
    fill(entry.client_order_id, "f-e1", 40, "10.100000", commission=Decimal("0.400000"), venue_ts=STAMP)
    fill(entry.client_order_id, "f-e2", 30, "10.123457", commission=Decimal("0"), venue_ts=STAMP + timedelta(seconds=1))
    pos = position(20, qty=70, status="closing", exit_attempt=2, stop=Decimal("9.500000"), target=None)
    exit_request(pos.position_id, exit_reason="stop", trigger_price=Decimal("9.499999"), retry_after=STAMP + timedelta(seconds=30))
    rej = order(20, "exit:1", position_id=pos.position_id, side="SELL", position_effect="close", qty=70,
                status="rejected", exit_reason="stop", reject_reason="venue_rejected",
                created_at=STAMP + timedelta(seconds=2))
    live = order(20, "exit:2", position_id=pos.position_id, side="SELL", position_effect="close", qty=70,
                 status="partially_filled", exit_reason="stop", created_at=STAMP + timedelta(seconds=3))
    fill(live.client_order_id, "f-x1", 20, "9.480000", venue_ts=STAMP.astimezone(IST) + timedelta(minutes=1))
    done = outcome()
    with SessionLocal.begin() as session:
        row = session.get(Trade, tid(20))
        row.outcome_id, row.outcome_status = done.outcome_id, "recorded"

    body = (await get(tid(20))).json()
    assert [o["client_order_id"] for o in body["orders"]] == [entry.client_order_id, rej.client_order_id, live.client_order_id]
    assert [o["status"] for o in body["orders"]] == ["partially_filled", "rejected", "partially_filled"]
    assert body["orders"][0]["limit_price"] == "10.250000" and body["orders"][1]["limit_price"] is None
    assert body["orders"][1]["reject_reason"] == "venue_rejected" and body["orders"][1]["position_id"] == str(pos.position_id)
    assert [(f["venue_fill_id"], f["price"], f["commission"], f["symbol"]) for f in body["fills"]] == [
        ("f-e1", "10.100000", "0.400000", "ZZTD1"), ("f-e2", "10.123457", "0.000000", "ZZTD1"), ("f-x1", "9.480000", None, "ZZTD1"),
    ]
    assert body["fills"][2]["venue_ts"] == "2026-09-18T14:01:00Z" and body["fills"][0]["venue_ts"] == "2026-09-18T14:00:00Z"
    assert [p["position_id"] for p in body["positions"]] == [str(pos.position_id)]
    p = body["positions"][0]
    assert (p["status"], p["qty"], p["avg_price"], p["stop"], p["target"], p["exit_attempt"], p["closed_at"], p["realized_pnl"]) == (
        "closing", 70, "10.123457", "9.500000", None, 2, None, None)
    req = body["exit_requests"][0]
    assert req["trigger_price"] == "9.499999" and req["retry_after"] == "2026-09-18T14:00:30Z"
    assert req["position_status"] == "closing" and req["remaining_qty"] == 70 and req["fallback_reason"] is None
    assert body["outcome"]["outcome_status"] == "recorded" and body["outcome"]["outcome_id"] == str(done.outcome_id)
    assert body["outcome"]["summary"] == {
        "exit_reason": "target", "entry_price": "10.123457", "entry_qty": "100.000000", "exit_price": "10.500000",
        "exit_qty": "100.000000", "commission_total": None, "realized_pnl": "37.654300", "realized_r": "1.2346",
        "entry_filled_at": "2026-09-18T14:00:00Z", "exit_filled_at": "2026-09-18T14:05:00Z", "holding_seconds": 300,
    }


@pytest.mark.asyncio
async def test_pending_outcome_status_is_returned_as_stored_without_a_reason():
    trade(25, status="closed", outcome_status="blocked")
    body = (await get(tid(25))).json()
    assert body["outcome"] == {"outcome_status": "blocked", "outcome_id": None, "summary": None}


@pytest.mark.asyncio
async def test_eod_request_fields_and_utc_serialization():
    trade(30, status="closing")
    pos = position(30, status="closing", opened_at=(STAMP + timedelta(hours=1)).astimezone(IST))
    exit_request(pos.position_id, exit_reason="eod_flatten", eod_flatten_at=STAMP + timedelta(hours=2),
                 eod_close_at=STAMP + timedelta(hours=3), eod_expired_at=STAMP + timedelta(hours=3, minutes=1),
                 fallback_reason="target", fallback_trigger_price=Decimal("11.000001"),
                 fallback_trigger_ts=(STAMP + timedelta(hours=4)).astimezone(IST))
    body = (await get(tid(30))).json()
    assert body["positions"][0]["opened_at"] == "2026-09-18T15:00:00Z"
    req = body["exit_requests"][0]
    assert (req["eod_flatten_at"], req["eod_close_at"], req["eod_expired_at"]) == (
        "2026-09-18T16:00:00Z", "2026-09-18T17:00:00Z", "2026-09-18T17:01:00Z")
    assert req["fallback_reason"] == "target" and req["fallback_trigger_price"] == "11.000001"
    assert req["fallback_trigger_ts"] == "2026-09-18T18:00:00Z"


@pytest.mark.asyncio
async def test_unrelated_trades_are_excluded_from_every_collection():
    trade(40)
    mine = order(40, "entry", status="filled")
    fill(mine.client_order_id, "mine-1", 100, "10.000000")
    my_pos = position(40)
    exit_request(my_pos.position_id)
    trade(41, symbol="ZZTD1")  # same symbol, different trade
    other = order(41, "entry", status="filled")
    fill(other.client_order_id, "other-1", 100, "99.000000")
    other_pos = position(41)
    exit_request(other_pos.position_id, exit_reason="target")
    body = (await get(tid(40))).json()
    assert [o["client_order_id"] for o in body["orders"]] == [mine.client_order_id]
    assert [f["venue_fill_id"] for f in body["fills"]] == ["mine-1"]
    assert [p["position_id"] for p in body["positions"]] == [str(my_pos.position_id)]
    assert [r["position_id"] for r in body["exit_requests"]] == [str(my_pos.position_id)]
    assert body["exit_requests"][0]["exit_reason"] == "stop"


@pytest.mark.asyncio
async def test_complete_population_is_not_truncated_by_recent_list_caps_and_is_deterministically_ordered():
    trade(50)
    orders = [Order(client_order_id=f"{tid(50)}:exit:{n:03d}", trade_id=tid(50), execution_mode="simulated",
                    execution_venue="simulated", symbol="ZZTD1", side="BUY", position_effect="open", qty=1,
                    order_type="market", status="approved", created_at=STAMP, updated_at=STAMP) for n in range(105)]
    add_all(*orders)
    add_all(*[Fill(client_order_id=orders[n % 105].client_order_id, execution_venue="simulated",
                   venue_fill_id=f"cap-{n:03d}", qty=1, price=Decimal("1.000000"), venue_ts=STAMP) for n in range(130)])
    positions = [Position(position_id=uuid.UUID(int=0x5000 + n), trade_id=tid(50), execution_mode="simulated",
                          execution_venue="simulated", symbol="ZZTD1", side="BUY", qty=1, avg_price=Decimal("1"),
                          opened_at=STAMP, status="open", exit_attempt=0) for n in range(105)]
    add_all(*positions)
    add_all(*[ExitRequest(position_id=p.position_id, exit_reason="stop", trigger_price=Decimal("1"), trigger_ts=STAMP)
              for p in positions])
    first = (await get(tid(50))).json()
    second = (await get(tid(50))).json()
    assert (len(first["orders"]), len(first["fills"]), len(first["positions"]), len(first["exit_requests"])) == (105, 130, 105, 105)
    assert first == second
    assert [o["id"] for o in first["orders"]] == sorted(o["id"] for o in first["orders"])
    seqs = [f["ledger_seq"] for f in first["fills"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == 130
    # positions tie on opened_at, so the primary key is the deterministic tie-break
    assert [p["position_id"] for p in first["positions"]] == sorted(p["position_id"] for p in first["positions"])
    assert [r["position_id"] for r in first["exit_requests"]] == sorted(r["position_id"] for r in first["exit_requests"])


@pytest.mark.asyncio
async def test_unknown_trade_is_404_and_malformed_uuid_is_422_without_touching_the_helper(monkeypatch):
    unknown = await get(uuid.uuid4())
    assert unknown.status_code == 404 and "Unknown trade" in unknown.json()["detail"]

    def forbidden(*args, **kwargs):
        raise AssertionError("helper called for a malformed id")

    monkeypatch.setattr(route, "read_execution_trade_detail", forbidden)
    for bad in ("not-a-uuid", "123", "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz"):
        assert (await get(bad)).status_code == 422


def _table_fingerprint() -> list:
    with SessionLocal() as session:
        return [
            session.execute(text(sql), {"n": MARKER}).all() for sql in (
                "SELECT * FROM trades WHERE strategy_name = :n ORDER BY trade_id",
                "SELECT * FROM orders WHERE trade_id IN (SELECT trade_id FROM trades WHERE strategy_name = :n) ORDER BY id",
                "SELECT * FROM fills WHERE client_order_id IN (SELECT client_order_id FROM orders WHERE trade_id IN "
                "(SELECT trade_id FROM trades WHERE strategy_name = :n)) ORDER BY ledger_seq",
                "SELECT * FROM positions WHERE trade_id IN (SELECT trade_id FROM trades WHERE strategy_name = :n) ORDER BY position_id",
                "SELECT * FROM exit_requests WHERE position_id IN (SELECT position_id FROM positions WHERE trade_id IN "
                "(SELECT trade_id FROM trades WHERE strategy_name = :n)) ORDER BY position_id",
            )
        ]


@pytest.mark.asyncio
async def test_worker_snapshot_is_read_only_repeatable_read_and_alters_nothing(monkeypatch):
    trade(60, status="closing", outcome_status="pending_retry")
    entry = order(60, "entry", status="filled")
    fill(entry.client_order_id, "ro-1", 100, "10.000000")
    exit_request(position(60).position_id)
    before = _table_fingerprint()
    observed: list[tuple[int, str, str]] = []
    loop_thread = threading.get_ident()

    def factory():
        session = SessionLocal()
        execute = session.execute

        def checked(statement, *args, **kwargs):
            observed.append((
                threading.get_ident(),
                execute(text("SHOW transaction_read_only")).scalar_one(),
                execute(text("SHOW transaction_isolation")).scalar_one(),
            ))
            return execute(statement, *args, **kwargs)

        session.execute = checked
        return session

    monkeypatch.setattr(detail, "SessionLocal", factory)
    assert (await get(tid(60))).status_code == 200
    assert len(observed) >= 5  # trade, orders, fills, positions, exit requests
    assert all(t != loop_thread and ro == "on" and iso == "repeatable read" for t, ro, iso in observed)
    assert _table_fingerprint() == before


@pytest.mark.asyncio
async def test_all_collections_come_from_one_snapshot_even_if_a_writer_commits_mid_read(monkeypatch):
    trade(70)
    order(70, "entry")
    calls = {"n": 0}

    def factory():
        session = SessionLocal()
        execute = session.execute

        def racing(statement, *args, **kwargs):
            result = execute(statement, *args, **kwargs)
            calls["n"] += 1
            if calls["n"] == 1:  # right after the trade row was read, another writer commits
                order(70, "late")
            return result

        session.execute = racing
        return session

    monkeypatch.setattr(detail, "SessionLocal", factory)
    body = (await get(tid(70))).json()
    assert [o["client_order_id"] for o in body["orders"]] == [f"{tid(70)}:entry"]
    monkeypatch.undo()
    assert len((await get(tid(70))).json()["orders"]) == 2


@pytest.mark.asyncio
async def test_blocked_query_does_not_block_health(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def blocked(trade_id):
        started.set()
        if not release.wait(5):
            raise TimeoutError("blocked trade detail read was never released")
        return None

    monkeypatch.setattr(route, "read_execution_trade_detail", blocked)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        task = asyncio.create_task(client.get(f"/intelligence/execution-trades/{uuid.uuid4()}"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            health = await asyncio.wait_for(client.get("/health"), 2)
            assert health.status_code == 200 and health.json()["status"] == "ok"
        finally:
            release.set()
        assert (await asyncio.wait_for(task, 5)).status_code == 404
