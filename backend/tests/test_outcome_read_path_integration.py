"""Read-path integration: an ``OutcomeRecorder`` row is visible to every reader.

Decision #186's ``OutcomeRecorder`` is the only writer of non-backtest
``strategy_outcomes``.  This module proves, on a real PostgreSQL database, that
a row the recorder actually wrote is readable — unchanged and without any
fabricated value — through all four read paths:

* ``GET /intelligence/strategy-outcomes``
* ``GET /intelligence/win-rate-by-hour``
* ``GET /intelligence/expectancy-by-session-type``
* ``GET /intelligence/world-view`` (its ``performance`` envelope)

The simulated rows are produced by the real ``OutcomeRecorder.record_trade()``
from the ledger rows seeded by ``tests.test_outcome_recorder._seed`` (reused, not
rebuilt — this file builds no execution pipeline).  Only
``capture_strategy_outcome_snapshots`` is stubbed, exactly as the recorder's own
tests do.  Two backtest rows are then written through the Backtest Runner's real
``record_strategy_outcome()`` wrapper so population isolation, Eastern-time hour
grouping across a DST change, and session grouping can be asserted with exact
values.

Shared-database note: the World View performance envelope is system-wide and
has no strategy filter, so its assertions are before/after *deltas* against a
baseline read taken before seeding.  Nothing here deletes rows it did not create.
"""
from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select, text

import app.context_engine.engine as context_engine_module
import app.market_state_engine.engine as market_state_engine_module
from app.context_engine.engine import ContextEngine
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.main import app
from app.market_state_engine.engine import MarketStateEngine
from app.models.execution_ledger import Trade
from app.models.trading_intelligence import StrategyOutcomeRecord
from app.schemas.performance import StrategyOutcome
from app.trading_intelligence import outcome_recorder as recorder_module
from app.trading_intelligence.outcome_recorder import OutcomeRecorder
from app.trading_intelligence.performance import record_strategy_outcome
from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots

# Reused ledger seeding + cleanup from the recorder's own test module.  ``_cleanup``
# is an autouse fixture there; importing it registers it here too, and it deletes
# every ``strategy_outcomes`` row with strategy_name == NAME (live and backtest).
from tests.test_outcome_recorder import NAME, _cleanup, _seed  # noqa: F401

_ET = ZoneInfo("America/New_York")
_ENTRY_CONTEXT = {"calendar": {"session": "power_hour", "is_market_open": True}}


def _db_available() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="real PostgreSQL unavailable")


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------

def _backtest_outcome(*, entered: datetime, realized_r: float, session: str) -> StrategyOutcome:
    """A backtest-population row, in the same shape the Backtest Runner writes."""
    exited = entered + timedelta(minutes=10)
    return StrategyOutcome(
        outcome_id=uuid.uuid4(), opportunity_id=uuid.uuid4(), schema_version=2,
        strategy_name=NAME, strategy_version="v1", symbol="AAPL", origin="auto",
        is_backtest=True, backtest_run_id=None,
        trading_day=entered.astimezone(_ET).date(),
        setup_detected_at=entered, signal_confirmed_at=entered, decided_at=entered,
        entry_filled_at=entered, exit_filled_at=exited, holding_seconds=600,
        direction="BUY", entry_price=100.0, entry_qty=1,
        exit_price=100.0 + realized_r, exit_qty=1, commission_total=0.0, slippage_entry=0.0,
        realized_pnl=realized_r, realized_r=realized_r, exit_reason="target",
        structural_invalidation=99.0, structural_target=102.0, final_stop=99.0, final_target=102.0,
        confidence_at_signal=0.5, evidence={},
        market_state_at_entry={"m": "bt"}, context_at_entry={"calendar": {"session": session}},
        market_state_at_exit={"m": "bt"}, context_at_exit={"c": "bt"},
        feature_snapshot_id=None,
    )


async def _record_simulated_trades(monkeypatch) -> tuple[uuid.UUID, uuid.UUID]:
    """Let the real recorder write two simulated rows.

    Trade A carries an entry snapshot the entry hook would have persisted: a
    context with a session, but *no* market-state snapshot (engine was cold) and
    its reason.  Trade B has no entry snapshot at all, so the recorder itself
    writes NULL + ``recorder_unavailable`` for both entry snapshots.  Exit
    snapshots are available for both (stubbed capture, as in the recorder tests).
    """
    trade_a, _ = _seed()
    trade_b, _ = _seed()
    with SessionLocal.begin() as session:
        trade = session.get(Trade, trade_a)
        trade.entry_market_state = None
        trade.entry_context = _ENTRY_CONTEXT
        trade.entry_snapshot_missing_reasons = {"market_state_at_entry": "engine_cold_start"}
        trade.entry_snapshot_captured_at = datetime.now(timezone.utc)
    monkeypatch.setattr(
        recorder_module, "capture_strategy_outcome_snapshots",
        lambda symbol: StrategyOutcomeSnapshots({"m": "exit"}, {"c": "exit"}),
    )
    recorder = OutcomeRecorder(EventBus(), SessionLocal)
    assert await recorder.record_trade(trade_a) == "recorded"
    assert await recorder.record_trade(trade_b) == "recorded"
    return trade_a, trade_b


def _seed_backtest_rows() -> tuple[uuid.UUID, uuid.UUID]:
    """Two backtest rows, one in EDT and one in EST, both at 10:05 Eastern.

    14:05Z (July) and 15:05Z (January) are different UTC hours but the same
    Eastern hour; a UTC-bucketed or blended query would split or merge them
    differently from the assertions below.
    """
    summer = _backtest_outcome(
        entered=datetime(2099, 7, 1, 14, 5, tzinfo=timezone.utc), realized_r=-1.0, session="power_hour")
    winter = _backtest_outcome(
        entered=datetime(2099, 1, 15, 15, 5, tzinfo=timezone.utc), realized_r=2.0, session="open")
    record_strategy_outcome(summer)
    record_strategy_outcome(winter)
    return summer.outcome_id, winter.outcome_id


def _live_rows() -> list[StrategyOutcomeRecord]:
    with SessionLocal() as session:
        return list(session.scalars(select(StrategyOutcomeRecord).where(
            StrategyOutcomeRecord.strategy_name == NAME, StrategyOutcomeRecord.is_backtest.is_(False))))


def _install_world_view_sources():
    """Install empty Market State / Context singletons; return a restore callable."""
    bus = EventBus()
    prior = (market_state_engine_module._market_state_engine, context_engine_module._context_engine)
    market_state_engine_module._market_state_engine = MarketStateEngine(bus)
    context_engine_module._context_engine = ContextEngine(bus)

    def restore() -> None:
        market_state_engine_module._market_state_engine, context_engine_module._context_engine = prior

    return restore


def _by(rows: list[dict], key: str) -> dict:
    return {row[key]: row for row in rows}


# ---------------------------------------------------------------------------
# The integration test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recorder_row_is_readable_through_every_read_path_with_strict_isolation(monkeypatch):
    restore_sources = _install_world_view_sources()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            baseline_world = (await client.get("/intelligence/world-view")).json()["performance"]

            trade_a, trade_b = await _record_simulated_trades(monkeypatch)
            backtest_ids = set(map(str, _seed_backtest_rows()))
            live_ids = {str(trade_a), str(trade_b)}  # the recorder sets outcome.opportunity_id = trade_id

            # ---- 1. GET /strategy-outcomes: row-level fidelity and isolation ----
            default_body = (await client.get("/intelligence/strategy-outcomes", params={"limit": 500})).json()
            live_body = (await client.get(
                "/intelligence/strategy-outcomes", params={"limit": 500, "is_backtest": "false"})).json()
            bt_body = (await client.get(
                "/intelligence/strategy-outcomes", params={"limit": 500, "is_backtest": "true"})).json()

            # The default population is the live/simulated one, never a blend.
            assert default_body == live_body
            ours_live = [o for o in live_body["outcomes"] if o["strategy_name"] == NAME]
            ours_bt = [o for o in bt_body["outcomes"] if o["strategy_name"] == NAME]
            assert {o["opportunity_id"] for o in ours_live} == live_ids
            assert {o["outcome_id"] for o in ours_bt} == backtest_ids
            assert all(o["is_backtest"] is False for o in live_body["outcomes"])
            assert all(o["is_backtest"] is True for o in bt_body["outcomes"])
            assert not backtest_ids & {o["outcome_id"] for o in live_body["outcomes"]}
            assert not live_ids & {o["opportunity_id"] for o in bt_body["outcomes"]}

            by_trade = {o["opportunity_id"]: o for o in ours_live}
            a, b = by_trade[str(trade_a)], by_trade[str(trade_b)]
            for row in (a, b):
                # What the recorder computed from the ledger arrives untouched.
                assert row["execution_mode"] == row["execution_venue"] == "simulated"
                assert row["backtest_run_id"] is None and row["origin"] == "auto"
                assert row["schema_version"] == 2
                assert row["entry_qty"] == row["exit_qty"] == 10
                assert row["entry_price"] == pytest.approx(101.2)
                assert row["exit_price"] == pytest.approx(105.4)
                assert row["realized_pnl"] == pytest.approx(42.0)
                assert row["realized_r"] == pytest.approx(1.3125)
                assert row["exit_reason"] == "target"
                assert row["commission_total"] is None  # unknown fees stay unknown, not 0
                assert row["signal_confirmed_at"] is None
                assert row["market_state_at_exit"] == {"m": "exit"}
                assert row["context_at_exit"] == {"c": "exit"}
            # Honest NULL snapshots: null on the wire (never {}), each with its reason.
            assert a["market_state_at_entry"] is None
            assert a["context_at_entry"] == _ENTRY_CONTEXT
            assert a["snapshot_missing_reasons"] == {"market_state_at_entry": "engine_cold_start"}
            assert b["market_state_at_entry"] is None and b["context_at_entry"] is None
            assert b["snapshot_missing_reasons"] == {
                "market_state_at_entry": "recorder_unavailable", "context_at_entry": "recorder_unavailable"}
            # Backtest rows carry every snapshot and no missing-reason map.
            for row in ours_bt:
                assert row["execution_mode"] == "backtest" and row["execution_venue"] == "simulated"
                assert row["snapshot_missing_reasons"] is None
                assert None not in (row["market_state_at_entry"], row["context_at_entry"])

            # ---- expected aggregates, derived independently of the SQL under test ----
            live_rows = _live_rows()
            assert len(live_rows) == 2
            live_hours = Counter(r.entry_filled_at.astimezone(_ET).hour for r in live_rows)
            expected_live_hours = {h: n for h, n in live_hours.items()}

            # ---- 2. GET /win-rate-by-hour (strategy-scoped → exact populations) ----
            live_hours_body = (await client.get(
                "/intelligence/win-rate-by-hour", params={"strategy_name": NAME})).json()
            bt_hours_body = (await client.get(
                "/intelligence/win-rate-by-hour", params={"strategy_name": NAME, "is_backtest": "true"})).json()
            live_hours_rows = _by(live_hours_body["hourly_win_rates"], "hour_et")
            assert {h: r["total_trades"] for h, r in live_hours_rows.items()} == expected_live_hours
            assert all(r["win_count"] == r["total_trades"] and r["win_rate"] == 1.0
                       for r in live_hours_rows.values())
            # Backtest: EDT 14:05Z and EST 15:05Z land in the same Eastern hour; one win, one loss.
            assert bt_hours_body["hourly_win_rates"] == [
                {"hour_et": 10, "total_trades": 2, "win_count": 1, "win_rate": 0.5}]

            # ---- 3. GET /expectancy-by-session-type ----
            live_sessions = _by((await client.get(
                "/intelligence/expectancy-by-session-type", params={"strategy_name": NAME}
            )).json()["session_expectancy"], "session_type")
            bt_sessions = _by((await client.get(
                "/intelligence/expectancy-by-session-type", params={"strategy_name": NAME, "is_backtest": "true"}
            )).json()["session_expectancy"], "session_type")
            assert set(live_sessions) == {"power_hour", None}  # null session is its own honest group
            assert live_sessions["power_hour"]["trade_count"] == 1
            assert live_sessions["power_hour"]["expectancy_r"] == pytest.approx(1.3125)
            assert live_sessions[None]["trade_count"] == 1
            assert live_sessions[None]["expectancy_r"] == pytest.approx(1.3125)
            # Backtest's power_hour (-1.0) must not leak into the live power_hour group.
            assert set(bt_sessions) == {"power_hour", "open"}
            assert bt_sessions["power_hour"]["expectancy_r"] == pytest.approx(-1.0)
            assert bt_sessions["open"]["expectancy_r"] == pytest.approx(2.0)

            # ---- 4. World View performance envelope (system-wide → before/after deltas) ----
            world = (await client.get("/intelligence/world-view")).json()["performance"]
            assert set(world) == {"live", "backtest"}

            def delta(population: str, section: str, key: str, field: str) -> dict:
                after = _by(world[population][section], key)
                before = _by(baseline_world[population][section], key)
                return {k: row[field] - (before[k][field] if k in before else 0) for k, row in after.items()
                        if row[field] - (before[k][field] if k in before else 0)}

            assert delta("live", "hourly_win_rates", "hour_et", "total_trades") == expected_live_hours
            assert delta("live", "hourly_win_rates", "hour_et", "win_count") == expected_live_hours
            assert delta("backtest", "hourly_win_rates", "hour_et", "total_trades") == {10: 2}
            assert delta("backtest", "hourly_win_rates", "hour_et", "win_count") == {10: 1}
            assert delta("live", "session_expectancy", "session_type", "trade_count") == {"power_hour": 1, None: 1}
            assert delta("backtest", "session_expectancy", "session_type", "trade_count") == {
                "power_hour": 1, "open": 1}

            # The envelope serves exactly what the two aggregate functions return.
            for population, flag in (("live", "false"), ("backtest", "true")):
                hours = (await client.get("/intelligence/win-rate-by-hour", params={"is_backtest": flag})).json()
                sessions = (await client.get(
                    "/intelligence/expectancy-by-session-type", params={"is_backtest": flag})).json()
                assert world[population]["hourly_win_rates"] == hours["hourly_win_rates"]
                assert world[population]["session_expectancy"] == sessions["session_expectancy"]

        # Reading wrote nothing: the recorder's rows are exactly as it left them.
        with SessionLocal() as session:
            assert session.scalar(text("SELECT count(*) FROM strategy_outcomes WHERE strategy_name = :n"),
                                  {"n": NAME}) == 4
            assert {r.realized_pnl for r in _live_rows()} == {Decimal("42.000000")}
    finally:
        restore_sources()
