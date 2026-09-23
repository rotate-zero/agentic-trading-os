"""
Portfolio State Session compatibility — decisions #172/#173, design §6.5.
Real PostgreSQL only. Tests persist trade/order/fill rows directly; the
Execution Engine has no fill publisher yet. Event-worker tests live in
test_portfolio_worker.py and use the explicitly scoped fake ledger.
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.models.execution_ledger import Fill, Order, Trade, Position, PortfolioStateCursor
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
        execution_venue="simulated", symbol=trade.symbol, side="BUY" if position_effect == "open" else "SELL", position_effect=position_effect,
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
    """The compatibility API does not publish; this verifies committed-fill
    recovery only. The fake-port worker tests cover event publication."""
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
        ps._state = replace(ps._state, positions=(replace(ps._state.positions[0], qty=999),))

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
        assert position is None  # anomaly blocks read-side decisions
        stored = session.execute(select(Position).where(Position.trade_id == trade.trade_id)).scalar_one()
        assert stored.qty == 10 and stored.status == "open"
        assert stored.realized_pnl == 0  # no fabricated gain on the five excess shares
    finally:
        session.close()


def test_fill_exceeding_a_terminal_order_is_persisted_and_flagged_overfill() -> None:
    """AC #13: a fill for an order the ledger already considers
    terminal AND already fully accounted is never dropped — flagged
    as an overfill and not applied to position accounting."""
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
        assert stray.anomaly == "overfill"  # persisted AND flagged, not dropped
        assert ps.get_snapshot() is None
        stored = session.execute(select(Position).where(Position.trade_id == trade.trade_id)).scalar_one()
        assert stored.qty == 10 and stored.realized_pnl == 0
    finally:
        session.close()


def test_flush_does_not_expose_uncommitted_position_and_commit_failure_installs_nothing():
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        fill = _make_fill(session, order, seq_hint=1, qty=10, price=100, ts=datetime.now(timezone.utc))
        session.commit()  # the venue fill is durable before portfolio accounting
        ps = PortfolioState("simulated")
        ps.apply_fill(session, fill)
        assert ps.get_snapshot() is None  # flush is NOT commit
        def fail_commit(s):
            raise RuntimeError("injected pre-commit failure")
        event.listen(session, "before_commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected"):
            session.commit()
        session.rollback()
        event.remove(session, "before_commit", fail_commit)
        assert ps.get_snapshot() is None
        assert session.execute(select(Position).where(Position.trade_id == trade.trade_id)).scalar_one_or_none() is None
        assert session.get(PortfolioStateCursor, "simulated") is None
        assert session.get(Fill, fill.ledger_seq) is not None  # original report survives
        recovered = ps.rebuild_from_ledger(session)
        assert recovered.positions["AAPL"].qty == 10
    finally:
        session.close()


def test_restart_restores_partial_realizations_on_their_fill_days_and_separate_fees():
    from datetime import date
    from decimal import Decimal
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        entry = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        first = _make_fill(session, entry, seq_hint=1, qty=10, price=100,
                           ts=datetime(2026, 1, 5, 15, tzinfo=timezone.utc))
        first.commission = Decimal("1")
        exit_order = _make_order(session, trade, suffix="exit", position_effect="close", qty=10)
        partial = _make_fill(session, exit_order, seq_hint=1, qty=4, price=110,
                             ts=datetime(2026, 4, 7, 1, tzinfo=timezone.utc))  # Apr 6 ET
        partial.commission = Decimal("0.4")
        session.commit()
        ps = PortfolioState("simulated")
        ps.rebuild_from_ledger(session)
        identity = ps.get_snapshot().positions["AAPL"].position_id
        assert ps.get_snapshot(trading_day=date(2026, 4, 6)).realized_profit_today == 40
        assert ps.get_snapshot().in_flight[exit_order.client_order_id].remaining_qty == 6
        last = _make_fill(session, exit_order, seq_hint=2, qty=6, price=95,
                          ts=datetime(2026, 9, 22, 15, tzinfo=timezone.utc))
        last.commission = Decimal("0.6")
        closed = ps.apply_fill(session, last)
        assert ps.get_snapshot() is None
        session.commit()
        assert closed.position_id == identity and closed.realized_pnl == 10 and closed.fees == 2
        fresh = PortfolioState("simulated")
        fresh.rebuild_from_ledger(session)
        january = fresh.get_snapshot(trading_day=date(2026, 1, 5))
        april = fresh.get_snapshot(trading_day=date(2026, 4, 6))
        september = fresh.get_snapshot(trading_day=date(2026, 9, 22))
        assert january.realized_pnl_today == 0 and january.fees_today == 1
        assert april.realized_profit_today == 40 and april.fees_today == Decimal("0.4")
        assert september.realized_loss_today == 30 and september.fees_today == Decimal("0.6")
        assert fresh.get_snapshot().open_position_count == 0
        assert fresh.apply_fill(session, last) is None  # duplicate closure must not signal publish
        session.rollback()
    finally:
        session.close()


def test_all_partial_fills_committed_before_replay_are_applied_in_order():
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10, status="filled")
        _make_fill(session, order, seq_hint=1, qty=4, price=100, ts=datetime.now(timezone.utc))
        _make_fill(session, order, seq_hint=2, qty=6, price=110, ts=datetime.now(timezone.utc))
        session.commit()  # upstream status may already be filled
        ps = PortfolioState("simulated")
        snap = ps.rebuild_from_ledger(session)
        assert snap.positions["AAPL"].qty == 10 and snap.positions["AAPL"].avg_price == 106
        identity = snap.positions["AAPL"].position_id
        assert PortfolioState("simulated").rebuild_from_ledger(session).positions["AAPL"].position_id == identity
    finally:
        session.close()


def test_cursor_cannot_skip_an_earlier_fill():
    from app.portfolio_state.ports import PositionLedgerError
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        _make_fill(session, order, seq_hint=1, qty=4, price=100, ts=datetime.now(timezone.utc))
        second = _make_fill(session, order, seq_hint=2, qty=6, price=110, ts=datetime.now(timezone.utc))
        session.commit()
        ps = PortfolioState("simulated")
        with pytest.raises(PositionLedgerError, match="unapplied fill"):
            ps.apply_fill(session, second)
        session.rollback()
        assert ps.get_snapshot() is None
        assert ps.rebuild_from_ledger(session).positions["AAPL"].qty == 10
    finally:
        session.close()


def test_new_position_after_closure_keeps_distinct_persisted_identity():
    from datetime import timedelta
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        entry = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        _make_fill(session, entry, seq_hint=1, qty=10, price=100, ts=datetime.now(timezone.utc))
        ps = PortfolioState("simulated")
        first_id = ps.rebuild_from_ledger(session).positions["AAPL"].position_id
        close = _make_order(session, trade, suffix="exit", position_effect="close", qty=10)
        _make_fill(session, close, seq_hint=1, qty=10, price=101, ts=datetime.now(timezone.utc))
        ps.rebuild_from_ledger(session)
        reopen = _make_order(session, trade, suffix="entry2", position_effect="open", qty=10)
        _make_fill(session, reopen, seq_hint=1, qty=10, price=102,
                   ts=datetime.now(timezone.utc) + timedelta(days=30))
        second_id = ps.rebuild_from_ledger(session).positions["AAPL"].position_id
        assert first_id != second_id
        assert PortfolioState("simulated").rebuild_from_ledger(session).positions["AAPL"].position_id == second_id
    finally:
        session.close()


def test_committing_only_first_partial_fill_leaves_snapshot_unavailable_until_catchup():
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10)
        first = _make_fill(session, order, seq_hint=1, qty=4, price=100, ts=datetime.now(timezone.utc))
        _make_fill(session, order, seq_hint=2, qty=6, price=110, ts=datetime.now(timezone.utc))
        session.commit()
        ps = PortfolioState("simulated")
        ps.apply_fill(session, first)
        session.commit()
        assert ps.get_snapshot() is None  # known backlog, not a current flat/partial account
        assert ps.rebuild_from_ledger(session).positions["AAPL"].qty == 10
    finally:
        session.close()
