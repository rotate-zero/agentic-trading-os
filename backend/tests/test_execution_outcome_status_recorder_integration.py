"""``GET /intelligence/execution-outcome-status`` reflects the *real* ``OutcomeRecorder``.

``test_execution_outcome_status_route.py`` proves the route's read shape over
hand-inserted ``trades.outcome_status`` values; ``test_outcome_read_path_integration.py``
proves recorder-written outcomes are readable through the outcome readers but never
calls this route.  This module closes the gap on a real PostgreSQL database: the
ledger rows come from ``tests.test_outcome_recorder._seed`` (reused, not rebuilt),
the status transitions are made by the actual ``OutcomeRecorder.record_trade()``,
and every observation is made through the actual route.  No execution pipeline is
run and no app lifespan is booted (``httpx.ASGITransport`` does not run it).

Transitions covered, each observed through the route:

* closed eligible trade, recorder not yet run -> SQL NULL status, counted ``pending``
* recorder success -> ``recorded`` with the real ``strategy_outcomes.outcome_id`` link
* permanent block (``evidence_unavailable``) -> ``blocked``, no outcome link, and the
  reason code appears nowhere in the response (it lives in logs only, #186)
* transient ``SQLAlchemyError`` from the outcome writer -> durable ``pending_retry``,
  then the real recorder recovers it to ``recorded``

Only the one failure needed for the retry case is injected
(``record_strategy_outcome_in_session`` raising ``SQLAlchemyError``); the writer is
restored afterwards so the real recorder completes the retry.

Isolation. The shared database may hold unrelated qualifying trades, so every count
assertion is a *delta* against a baseline read through the route before seeding.
Cleanup removes only the rows this test created, identified by the trade ids it
seeded (never by strategy name), and runs before and after each test.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.main import app
from app.models.execution_ledger import Trade
from app.models.trading_intelligence import StrategyOutcomeRecord
from app.trading_intelligence import outcome_recorder as recorder_module
from app.trading_intelligence.outcome_recorder import OutcomeRecorder
from tests.test_outcome_recorder import _seed

_COUNT_KEYS = ["pending", "pending_retry", "blocked", "recorded", "other"]
_TRADE_FIELDS = {"trade_id", "symbol", "strategy_name", "outcome_status", "outcome_id", "updated_at"}


def _db_available() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="real PostgreSQL unavailable")


# ---------------------------------------------------------------------------
# Seeding + exact cleanup
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded():
    """Seeds closed trades via ``_seed`` and removes exactly those trades' rows."""
    created: list[uuid.UUID] = []

    def seed(**kwargs) -> uuid.UUID:
        trade_id, _ = _seed(**kwargs)
        created.append(trade_id)
        return trade_id

    def clean() -> None:
        if not created:
            return
        params = {"ids": created}
        with SessionLocal.begin() as session:
            # trades.outcome_id FKs strategy_outcomes: unlink, then delete children first.
            session.execute(text("UPDATE trades SET outcome_id = NULL WHERE trade_id = ANY(:ids)"), params)
            session.execute(text("DELETE FROM strategy_outcomes WHERE opportunity_id = ANY(:ids)"), params)
            session.execute(text(
                "DELETE FROM position_fill_receipts WHERE position_id IN "
                "(SELECT position_id FROM positions WHERE trade_id = ANY(:ids))"), params)
            session.execute(text(
                "DELETE FROM fills WHERE client_order_id IN "
                "(SELECT client_order_id FROM orders WHERE trade_id = ANY(:ids))"), params)
            session.execute(text("DELETE FROM orders WHERE trade_id = ANY(:ids)"), params)
            session.execute(text("DELETE FROM trade_reservations WHERE trade_id = ANY(:ids)"), params)
            session.execute(text("DELETE FROM positions WHERE trade_id = ANY(:ids)"), params)
            session.execute(text("DELETE FROM trades WHERE trade_id = ANY(:ids)"), params)

    yield seed
    clean()


# ---------------------------------------------------------------------------
# Route + DB helpers
# ---------------------------------------------------------------------------

async def _get(**params) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/intelligence/execution-outcome-status", params={"limit": 100, **params})


async def _read() -> httpx.Response:
    resp = await _get()
    assert resp.status_code == 200
    return resp


def _delta(resp: httpx.Response, baseline: dict[str, int]) -> dict[str, int]:
    counts = resp.json()["counts"]
    return {key: counts[key] - baseline[key] for key in _COUNT_KEYS}


def _expect(**nonzero: int) -> dict[str, int]:
    return {key: nonzero.get(key, 0) for key in _COUNT_KEYS}


def _row(resp: httpx.Response, trade_id: uuid.UUID) -> dict:
    rows = [r for r in resp.json()["trades"] if r["trade_id"] == str(trade_id)]
    assert len(rows) == 1, f"trade {trade_id} not listed exactly once (limit=100)"
    assert set(rows[0]) == _TRADE_FIELDS
    return rows[0]


def _db_state(trade_id: uuid.UUID):
    """(trades.outcome_status, trades.outcome_id, strategy_outcomes rows for the trade)."""
    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        rows = session.scalars(select(StrategyOutcomeRecord).where(
            StrategyOutcomeRecord.opportunity_id == trade_id)).all()
        return trade.outcome_status, trade.outcome_id, rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_null_status_is_pending_then_recorded_with_real_outcome_link(seeded):
    baseline = (await _read()).json()["counts"]
    trade_id = seeded()

    before = await _read()  # closed + eligible, recorder has not run: NULL status
    assert _delta(before, baseline) == _expect(pending=1)
    row = _row(before, trade_id)
    assert row["outcome_status"] is None  # stored NULL is returned as JSON null
    assert row["outcome_id"] is None
    assert _db_state(trade_id)[:2] == (None, None)

    assert await OutcomeRecorder(EventBus(), SessionLocal).record_trade(trade_id) == "recorded"

    after = await _read()
    assert _delta(after, baseline) == _expect(recorded=1)  # left pending, joined recorded
    row = _row(after, trade_id)
    status, outcome_id, outcomes = _db_state(trade_id)
    assert (status, len(outcomes)) == ("recorded", 1)
    assert outcomes[0].outcome_id == outcome_id  # a real strategy_outcomes row, not a stub
    assert row["outcome_status"] == "recorded"
    assert row["outcome_id"] == str(outcome_id)


async def test_permanently_blocked_trade_is_counted_blocked_without_link_or_reason(seeded, caplog):
    baseline = (await _read()).json()["counts"]
    trade_id = seeded(evidence=False)  # recorder cannot truthfully attribute this trade
    assert _delta(await _read(), baseline) == _expect(pending=1)

    assert await OutcomeRecorder(EventBus(), SessionLocal).record_trade(trade_id) == "blocked"
    assert "evidence_unavailable" in caplog.text  # the reason exists, but only in logs

    resp = await _read()
    assert _delta(resp, baseline) == _expect(blocked=1)
    row = _row(resp, trade_id)
    assert row["outcome_status"] == "blocked"
    assert row["outcome_id"] is None
    assert _db_state(trade_id) == ("blocked", None, [])  # no outcome row was written
    # The contract exposes no blocked reason anywhere in the payload.
    assert set(resp.json()) == {"counts", "trades"}
    assert "evidence_unavailable" not in resp.text
    assert "reason" not in resp.text


async def test_transient_recorder_failure_is_durably_pending_retry_then_recovers(seeded, monkeypatch):
    baseline = (await _read()).json()["counts"]
    trade_id = seeded()
    real_writer = recorder_module.record_strategy_outcome_in_session

    def transient_failure(session, outcome):
        real_writer(session, outcome)  # insert really happens, then the unit of work fails
        session.flush()
        raise SQLAlchemyError("injected transient fault before trade link")

    monkeypatch.setattr(recorder_module, "record_strategy_outcome_in_session", transient_failure)
    recorder = OutcomeRecorder(EventBus(), SessionLocal)
    assert await recorder.record_trade(trade_id) == "pending_retry"

    resp = await _read()  # fresh request/session: the status is durable, not in-process state
    assert _delta(resp, baseline) == _expect(pending_retry=1)
    row = _row(resp, trade_id)
    assert row["outcome_status"] == "pending_retry"
    assert row["outcome_id"] is None
    assert _db_state(trade_id) == ("pending_retry", None, [])  # failed insert rolled back

    monkeypatch.setattr(recorder_module, "record_strategy_outcome_in_session", real_writer)
    assert await recorder.record_trade(trade_id) == "recorded"

    resp = await _read()
    assert _delta(resp, baseline) == _expect(recorded=1)  # retry bucket drained, no residue
    row = _row(resp, trade_id)
    status, outcome_id, outcomes = _db_state(trade_id)
    assert (status, len(outcomes)) == ("recorded", 1)
    assert row["outcome_status"] == "recorded" and row["outcome_id"] == str(outcome_id)
