"""
Portfolio State Engine — decision #172, design
doc §6.5. Real Postgres only. Every test builds its own trade/order/fill
rows directly (no Execution Engine exists yet to produce them — same
"prove the contract, don't fabricate the caller" posture as
test_performance_intelligence.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models.execution_ledger import Fill, Order, Trade
from app.portfolio_state.engine import PortfolioState

_STRATEGY_NAME = "TEST_PORTFOLIO_STATE"


def _db_available() -> bool:
    try:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")


def _clean_test_rows() -> None:
    session = SessionLocal()
    try:
        session.execute(text(
            "DELETE FROM fills WHERE client_order_id IN "
            "(SELECT client_order_id FROM orders WHERE trade_id IN "
            "(SELECT trade_id FROM trades WHERE strategy_name = :n))"
        ), {"n": _STRATEGY_NAME})
        session.execute(text(
            "DELETE FROM positions WHERE trade_id IN (SELECT trade_id FROM trades WHERE strategy_name = :n)"
        ), {"n": _STRATEGY_NAME})
        session.execute(text(
            "DELETE FROM orders WHERE trade_id IN (SELECT trade_id FROM trades WHERE strategy_name = :n)"
        ), {"n": _STRATEGY_NAME})
        session.execute(text("DELETE FROM trades WHERE strategy_name = :n"), {"n": _STRATEGY_NAME})
        session.execute(text("DELETE FROM portfolio_state_cursor WHERE execution_mode = 'simulated'"))
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _cleanup():
    _clean_test_rows()
    yield
    _clean_test_rows()


def _make_trade(session, thesis: dict | None = None) -> Trade:
    trade = Trade(
        execution_mode="simulated", execution_venue="simulated", strategy_name=_STRATEGY_NAME,
        strategy_version="v1", direction="BUY", symbol="AAPL", thesis=thesis or {}, decision="approved",
        limits_snapshot={}, status="open",
    )
    session.add(trade)
    session.flush()
    return trade


def _make_order(session, trade: Trade, *, suffix: str, position_effect: str, qty: int, status: str = "submitted") -> Order:
    order = Order(
        client_order_id=f"{trade.trade_id}:{suffix}", trade_id=trade.trade_id, execution_mode="simulated",
        execution_venue="simulated", symbol=trade.symbol, side="BUY", position_effect=position_effect,
        qty=qty, status=status,
    )
    session.add(order)
    session.flush()
    return order


def _make_fill(session, order: Order, *, seq_hint: int, qty: int, price: float, ts: datetime) -> Fill:
    fill = Fill(
        client_order_id=order.client_order_id, execution_venue="simulated",
        venue_fill_id=f"{order.client_order_id}:f{seq_hint}", qty=qty, price=price, venue_ts=ts,
    )
    session.add(fill)
    session.flush()
    return fill


def test_apply_fill_opens_a_position() -> None:
    session = SessionLocal()
    try:
        trade = _make_trade(session, thesis={"final_stop": 95.0, "final_target": 110.0})
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        fill = _make_fill(session, order, seq_hint=1, qty=10, price=100.0, ts=datetime.now(timezone.utc))

        ps = PortfolioState("simulated")
        state = ps.apply_fill(session, fill)
        session.commit()

        assert state is not None
        assert state.symbol == "AAPL"
        assert state.qty == 10
        assert state.avg_price == 100.0
        assert state.stop == 95.0
        assert state.target == 110.0
        assert state.status == "open"

        snap = ps.get_snapshot()
        assert "AAPL" in snap.positions
        assert snap.open_position_count == 1
    finally:
        session.close()


def test_apply_fill_closes_a_position_and_computes_realized_pnl() -> None:
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        entry_order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        entry_fill = _make_fill(session, entry_order, seq_hint=1, qty=10, price=100.0, ts=datetime.now(timezone.utc))
        ps = PortfolioState("simulated")
        ps.apply_fill(session, entry_fill)
        session.commit()

        exit_order = _make_order(session, trade, suffix="exit", position_effect="close", qty=10)
        exit_fill = _make_fill(session, exit_order, seq_hint=1, qty=10, price=105.0, ts=datetime.now(timezone.utc))
        state = ps.apply_fill(session, exit_fill)
        session.commit()

        assert state.status == "closed"
        assert state.realized_pnl == pytest.approx(50.0)  # (105-100) * 10
        assert "AAPL" not in ps.get_snapshot().positions
        assert ps.get_snapshot().realized_pnl_today == pytest.approx(50.0)
    finally:
        session.close()


def test_apply_fill_is_idempotent_on_ledger_seq() -> None:
    """AC #8's Portfolio-State-side half: applying the same fill row
    twice must not double-count qty/pnl."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        fill = _make_fill(session, order, seq_hint=1, qty=10, price=100.0, ts=datetime.now(timezone.utc))

        ps = PortfolioState("simulated")
        ps.apply_fill(session, fill)
        session.commit()
        ps.apply_fill(session, fill)  # replay of the exact same fill row
        session.commit()

        state = ps.get_snapshot().positions["AAPL"]
        assert state.qty == 10  # not 20
    finally:
        session.close()


def test_rebuild_from_ledger_recovers_open_position_from_scratch() -> None:
    """AC #9 (persist-before-publish): fills are committed directly to
    the ledger — no PortfolioState instance ever touched them in
    memory (simulating "fault injection between the commit and the
    publish") — and a FRESH PortfolioState still recovers full state
    purely by reading the ledger."""
    session = SessionLocal()
    try:
        trade = _make_trade(session, thesis={"final_stop": 95.0})
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        _make_fill(session, order, seq_hint=1, qty=10, price=100.0, ts=datetime.now(timezone.utc))
        session.commit()  # committed; nothing has ever been "published" and nothing applied it

        fresh = PortfolioState("simulated")  # simulates a brand-new process
        snap = fresh.rebuild_from_ledger(session)

        assert "AAPL" in snap.positions
        assert snap.positions["AAPL"].qty == 10
        assert snap.positions["AAPL"].avg_price == 100.0
    finally:
        session.close()


def test_rebuild_from_ledger_is_idempotent_no_duplicate_positions() -> None:
    """AC #9's "no duplicate order, fill, or outcome" — calling
    rebuild_from_ledger() twice must not double-apply anything."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        _make_fill(session, order, seq_hint=1, qty=10, price=100.0, ts=datetime.now(timezone.utc))
        session.commit()

        ps = PortfolioState("simulated")
        ps.rebuild_from_ledger(session)
        ps.rebuild_from_ledger(session)  # a second "restart" — cursor already caught up

        assert ps.get_snapshot().positions["AAPL"].qty == 10
    finally:
        session.close()


def test_rebuild_from_ledger_recovers_a_closed_position_documenting_critical_lane_boundary(caplog) -> None:
    """AC #12: this delivery never publishes PositionClosed at all (the
    publish seam documented on PortfolioState.apply_fill) — so EVERY
    closure in this test suite is, by construction, "published but
    unhandled" in the sense AC #12 means: nothing downstream of the
    ledger ever heard about it. This test documents that the closure
    is still fully recovered from the ledger regardless."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        entry_order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        _make_fill(session, entry_order, seq_hint=1, qty=10, price=100.0, ts=datetime.now(timezone.utc))
        exit_order = _make_order(session, trade, suffix="exit", position_effect="close", qty=10)
        _make_fill(session, exit_order, seq_hint=1, qty=10, price=105.0, ts=datetime.now(timezone.utc))
        session.commit()  # both fills committed; no PortfolioState instance has ever run; nothing published

        fresh = PortfolioState("simulated")
        snap = fresh.rebuild_from_ledger(session)

        assert "AAPL" not in snap.positions  # closed — correctly not in the open-positions view
        from sqlalchemy import select
        from app.models.execution_ledger import Position
        position = session.execute(select(Position).where(Position.trade_id == trade.trade_id)).scalar_one()
        assert position.status == "closed"
        assert float(position.realized_pnl) == pytest.approx(50.0)
    finally:
        session.close()


def test_rebuild_from_ledger_ledger_wins_over_corrupted_in_memory_state(caplog) -> None:
    """AC #11: corrupt in-memory Portfolio State, then rebuild_from_ledger()
    restores the ledger-derived truth; the disagreement is logged."""
    import logging

    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        fill = _make_fill(session, order, seq_hint=1, qty=10, price=100.0, ts=datetime.now(timezone.utc))
        ps = PortfolioState("simulated")
        ps.apply_fill(session, fill)
        session.commit()

        # Corrupt in-memory state directly (simulating a bug/drift) — the ledger itself is untouched.
        ps.get_snapshot().positions["AAPL"].qty = 999

        with caplog.at_level(logging.WARNING):
            ps.rebuild_from_ledger(session, full_rebuild=True)

        assert ps.get_snapshot().positions["AAPL"].qty == 10  # ledger wins, not 999
        assert any("ledger wins" in rec.message for rec in caplog.records)
    finally:
        session.close()


def test_overfill_is_persisted_and_flagged_not_dropped() -> None:
    """AC #13: an overfill is still persisted, flagged anomaly='overfill', and
    handled without crashing — never dropped (I14)."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        entry_order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        _make_fill(session, entry_order, seq_hint=1, qty=10, price=100.0, ts=datetime.now(timezone.utc))
        exit_order = _make_order(session, trade, suffix="exit", position_effect="close", qty=10)
        overfill = _make_fill(session, exit_order, seq_hint=1, qty=15, price=105.0, ts=datetime.now(timezone.utc))  # 15 > 10 open

        ps = PortfolioState("simulated")
        ps.rebuild_from_ledger(session, full_rebuild=True)
        session.commit()

        session.refresh(overfill)
        assert overfill.anomaly == "overfill"  # persisted AND flagged, not dropped
        position = ps.get_snapshot()
        assert "AAPL" not in position.positions  # clamped to fully closed, not left dangling negative
    finally:
        session.close()


def test_fill_for_terminal_order_is_persisted_and_flagged_unmatched_order() -> None:
    """AC #13: a fill for an order the ledger already considers
    terminal (filled/cancelled/rejected) is never dropped — flagged
    anomaly='unmatched_order' and skipped for position accounting."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10, status="submitted")
        fill1 = _make_fill(session, order, seq_hint=1, qty=10, price=100.0, ts=datetime.now(timezone.utc))
        ps = PortfolioState("simulated")
        ps.apply_fill(session, fill1)
        session.commit()

        # order.status is now "filled" (apply_fill's own bookkeeping, since total_filled ==
        # order.qty) — send it ANOTHER fill, simulating a stray duplicate from the venue.
        assert order.status == "filled"
        stray = _make_fill(session, order, seq_hint=2, qty=5, price=101.0, ts=datetime.now(timezone.utc))

        result = ps.apply_fill(session, stray)
        session.commit()

        assert result is None  # not applied to position accounting
        session.refresh(stray)
        assert stray.anomaly == "unmatched_order"  # persisted AND flagged, not dropped
        assert ps.get_snapshot().positions["AAPL"].qty == 10  # unaffected by the stray fill
    finally:
        session.close()
