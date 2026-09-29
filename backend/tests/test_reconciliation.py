"""
Restart recovery/reconciliation — AC #10: killing the process with
orders in each non-terminal status and restarting reconciles each
with the venue as §6.9 specifies. Real Postgres + a fresh
`SimulatedVenue` per test standing in for "the venue after a restart
lost its old process's book" (SimulatedVenue is genuinely not durable
— see its own module docstring — so a fresh instance IS what a real
restart looks like from the ledger's point of view).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text

from app.broker_adapters.order_venue import OrderInstruction
from app.broker_adapters.simulated_venue import SimulatedVenue
from app.core.market_clock import MarketClock
from app.db.session import SessionLocal
from app.models.execution_ledger import ExitRequest, Fill, Order, Position, Trade
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.reconciliation import reconcile_with_venue

_STRATEGY_NAME = "TEST_RECONCILIATION"


class _AlwaysOpenClock(MarketClock):
    def is_regular_session(self, ts: datetime | None = None) -> bool:  # type: ignore[override]
        return True


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
            "DELETE FROM exit_requests WHERE position_id IN "
            "(SELECT position_id FROM positions WHERE trade_id IN "
            "(SELECT trade_id FROM trades WHERE strategy_name = :n))"
        ), {"n": _STRATEGY_NAME})
        session.execute(text(
            "DELETE FROM orders WHERE trade_id IN (SELECT trade_id FROM trades WHERE strategy_name = :n)"
        ), {"n": _STRATEGY_NAME})
        session.execute(text(
            "DELETE FROM positions WHERE trade_id IN (SELECT trade_id FROM trades WHERE strategy_name = :n)"
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


def _make_trade(session, symbol: str = "AAPL") -> Trade:
    trade = Trade(
        execution_mode="simulated", execution_venue="simulated", strategy_name=_STRATEGY_NAME,
        strategy_version="v1", direction="BUY", symbol=symbol, thesis={}, decision="approved",
        limits_snapshot={}, status="open",
    )
    session.add(trade)
    session.flush()
    return trade


def _make_order(session, trade: Trade, *, suffix: str, position_effect: str, qty: int, status: str) -> Order:
    order = Order(
        client_order_id=f"{trade.trade_id}:{suffix}", trade_id=trade.trade_id, execution_mode="simulated",
        execution_venue="simulated", symbol=trade.symbol, side="BUY", position_effect=position_effect,
        qty=qty, status=status,
    )
    session.add(order)
    session.flush()
    return order


@pytest.mark.asyncio
async def test_approved_entry_never_sent_is_cancelled_as_stale() -> None:
    """§6.9: approved, never sent, entry -> cancelled (not resubmitted)."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10, status="approved")
        session.commit()

        venue = SimulatedVenue(clock=_AlwaysOpenClock())  # fresh — never heard of this order
        await venue.connect()
        ps = PortfolioState("simulated")
        ps.rebuild_from_ledger(session)

        report = await reconcile_with_venue(session, venue, ps)

        session.refresh(order)
        assert order.status == "cancelled"
        assert order.reject_reason == "stale_opportunity_not_resubmitted"
        assert order.client_order_id in report.cancelled_stale_entries
        assert not report.has_discrepancy
    finally:
        session.close()


@pytest.mark.asyncio
async def test_approved_exit_is_not_sent_into_missing_venue_position() -> None:
    """A fresh simulated venue cannot safely receive an orphaned close."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        # An open position must exist for an exit order to make sense.
        entry_order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10, status="filled")
        entry_fill = Fill(
            client_order_id=entry_order.client_order_id, execution_venue="simulated",
            venue_fill_id=f"{entry_order.client_order_id}:f1", qty=10, price=100.0,
            venue_ts=datetime.now(timezone.utc),
        )
        session.add(entry_fill)
        session.flush()
        ps = PortfolioState("simulated")
        ps.apply_fill(session, entry_fill)

        exit_order = _make_order(session, trade, suffix="exit", position_effect="close", qty=10, status="approved")
        session.commit()

        venue = SimulatedVenue(clock=_AlwaysOpenClock())
        await venue.connect()

        report = await reconcile_with_venue(session, venue, ps)

        session.refresh(exit_order)
        assert exit_order.status == "approved"
        assert report.has_discrepancy
        assert exit_order.client_order_id not in report.resubmitted_exits
        assert await venue.get_order(exit_order.client_order_id) is None
    finally:
        session.close()


@pytest.mark.asyncio
async def test_dispatch_marked_approved_exit_without_venue_report_blocks_recovery() -> None:
    """A committed dispatch claim is not proof that the venue never accepted it."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        entry = _make_order(session, trade, suffix="entry", position_effect="open", qty=10, status="filled")
        fill = Fill(client_order_id=entry.client_order_id, execution_venue="simulated",
                    venue_fill_id=f"{entry.client_order_id}:f1", qty=10, price=100,
                    venue_ts=datetime.now(timezone.utc))
        session.add(fill)
        session.flush()
        ps = PortfolioState("simulated")
        ps.apply_fill(session, fill)
        position = session.scalar(select(Position).where(Position.trade_id == trade.trade_id))
        session.add(ExitRequest(position_id=position.position_id, exit_reason="stop",
                                trigger_price=90, trigger_ts=fill.venue_ts))
        close = Order(client_order_id=f"{trade.trade_id}:exit:1", trade_id=trade.trade_id,
                      position_id=position.position_id, execution_mode="simulated",
                      execution_venue="simulated", symbol=trade.symbol, side="SELL",
                      position_effect="close", qty=10, order_type="market", status="approved",
                      exit_reason="stop", exit_dispatch_started_at=fill.venue_ts)
        session.add(close)
        session.commit()
        venue = SimulatedVenue(clock=_AlwaysOpenClock())
        await venue.connect()
        report = await reconcile_with_venue(session, venue, ps)
        assert any("uncertain dispatched exit" in item for item in report.discrepancies)
        assert close.status == "approved"
        assert await venue.get_order(close.client_order_id) is None
        second = await reconcile_with_venue(session, venue, ps)
        assert any("uncertain dispatched exit" in item for item in second.discrepancies)
        session.refresh(close)
        assert close.status == "approved"  # a second restart cannot relabel it proven unsent
    finally:
        session.close()


@pytest.mark.asyncio
async def test_dispatch_marked_approved_exit_with_retained_venue_ack_recovers_same_id() -> None:
    """A lost status commit is resolved from retained venue evidence, without resend."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        entry = _make_order(session, trade, suffix="entry", position_effect="open", qty=10, status="filled")
        ts = datetime.now(timezone.utc)
        fill = Fill(client_order_id=entry.client_order_id, execution_venue="simulated",
                    venue_fill_id=f"{entry.client_order_id}:f1", qty=10, price=100, venue_ts=ts)
        session.add(fill)
        session.flush()
        ps = PortfolioState("simulated")
        ps.apply_fill(session, fill)
        position = session.scalar(select(Position).where(Position.trade_id == trade.trade_id))
        session.add(ExitRequest(position_id=position.position_id, exit_reason="stop",
                                trigger_price=90, trigger_ts=ts))
        close = Order(client_order_id=f"{trade.trade_id}:exit:1", trade_id=trade.trade_id,
                      position_id=position.position_id, execution_mode="simulated",
                      execution_venue="simulated", symbol=trade.symbol, side="SELL",
                      position_effect="close", qty=10, order_type="market", status="approved",
                      exit_reason="stop", exit_dispatch_started_at=ts)
        session.add(close)
        session.commit()

        venue = SimulatedVenue(clock=_AlwaysOpenClock())
        await venue.connect()
        await venue.place_order(OrderInstruction(client_order_id=entry.client_order_id, symbol=trade.symbol,
            side="BUY", qty=10, order_type="market", limit_price=None, position_effect="open"))
        venue.ingest_tick(trade.symbol, 100, ts)
        await venue.place_order(OrderInstruction(client_order_id=close.client_order_id, symbol=trade.symbol,
            side="SELL", qty=10, order_type="market", limit_price=None, position_effect="close"))
        report = await reconcile_with_venue(session, venue, ps)
        session.refresh(close)
        assert not report.has_discrepancy
        assert close.status == "submitted" and close.exit_dispatch_started_at == ts
        assert close.client_order_id in report.advanced_orders
        assert len(await venue.list_open_orders()) == 1
    finally:
        session.close()


@pytest.mark.asyncio
async def test_submitted_order_venue_lost_state_expires() -> None:
    """§6.9: submitted/partially_filled, venue has no record -> expired
    (venue_lost_state_on_restart); fills already in the ledger stand."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10, status="submitted")
        session.commit()

        venue = SimulatedVenue(clock=_AlwaysOpenClock())  # fresh — genuinely never placed this order
        await venue.connect()
        ps = PortfolioState("simulated")
        ps.rebuild_from_ledger(session)

        report = await reconcile_with_venue(session, venue, ps)

        session.refresh(order)
        assert order.status == "expired"
        assert order.reject_reason == "venue_lost_state_on_restart"
        assert order.client_order_id in report.expired_orders
    finally:
        session.close()


@pytest.mark.asyncio
async def test_submitted_order_venue_knows_it_missing_fills_are_applied() -> None:
    """§6.9: venue DOES know the order — missing fills get pulled in
    and applied via Portfolio State, status advances to match."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10, status="submitted")
        session.commit()

        venue = SimulatedVenue(clock=_AlwaysOpenClock())
        await venue.connect()
        # The venue independently has this order AND its fill (as if the process crashed
        # right after place_order returned but before the fill was ever processed).
        await venue.place_order(OrderInstruction(
            client_order_id=order.client_order_id, symbol="AAPL", side="BUY", qty=10,
        ))
        venue.ingest_tick("AAPL", 100.0, datetime.now(timezone.utc))

        ps = PortfolioState("simulated")
        ps.rebuild_from_ledger(session)
        report = await reconcile_with_venue(session, venue, ps)

        session.refresh(order)
        assert order.status == "filled"
        assert order.client_order_id in report.advanced_orders
        fills = session.execute(select(Fill).where(Fill.client_order_id == order.client_order_id)).scalars().all()
        assert len(fills) == 1  # the missing fill was pulled in exactly once
        assert ps.get_snapshot().positions["AAPL"].qty == 10
        assert not report.has_discrepancy
    finally:
        session.close()


@pytest.mark.asyncio
async def test_reconciliation_is_idempotent_no_duplicate_fills_on_a_second_pass() -> None:
    """Running reconcile_with_venue twice in a row (e.g. two restart
    attempts) must not double-insert the fills it pulled in on the first pass."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10, status="submitted")
        session.commit()

        venue = SimulatedVenue(clock=_AlwaysOpenClock())
        await venue.connect()
        await venue.place_order(OrderInstruction(
            client_order_id=order.client_order_id, symbol="AAPL", side="BUY", qty=10,
        ))
        venue.ingest_tick("AAPL", 100.0, datetime.now(timezone.utc))

        ps = PortfolioState("simulated")
        ps.rebuild_from_ledger(session)
        await reconcile_with_venue(session, venue, ps)
        await reconcile_with_venue(session, venue, ps)  # second pass, same venue state

        fills = session.execute(select(Fill).where(Fill.client_order_id == order.client_order_id)).scalars().all()
        assert len(fills) == 1
        assert ps.get_snapshot().positions["AAPL"].qty == 10
    finally:
        session.close()


@pytest.mark.asyncio
async def test_open_orders_discrepancy_is_reported() -> None:
    """§6.9: a venue-side open order the ledger doesn't know about at
    all is a discrepancy, not something reconcile_with_venue silently adopts."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        session.commit()

        venue = SimulatedVenue(clock=_AlwaysOpenClock())
        await venue.connect()
        # Place an order the LEDGER has no matching `orders` row for at all.
        await venue.place_order(OrderInstruction(
            client_order_id=f"{trade.trade_id}:phantom", symbol="AAPL", side="BUY", qty=10,
        ))

        ps = PortfolioState("simulated")
        ps.rebuild_from_ledger(session)
        report = await reconcile_with_venue(session, venue, ps)

        assert report.has_discrepancy
        assert any("phantom" in d for d in report.discrepancies)
    finally:
        session.close()


@pytest.mark.asyncio
async def test_position_qty_discrepancy_is_reported() -> None:
    """§6.9: venue and ledger disagree on a held quantity -> discrepancy, ledger untouched."""
    session = SessionLocal()
    try:
        trade = _make_trade(session)
        order = _make_order(session, trade, suffix="entry", position_effect="open", qty=10, status="filled")
        fill = Fill(
            client_order_id=order.client_order_id, execution_venue="simulated",
            venue_fill_id=f"{order.client_order_id}:f1", qty=10, price=100.0,
            venue_ts=datetime.now(timezone.utc),
        )
        session.add(fill)
        session.flush()
        ps = PortfolioState("simulated")
        ps.apply_fill(session, fill)
        session.commit()

        venue = SimulatedVenue(clock=_AlwaysOpenClock())
        await venue.connect()
        # The venue's own (reconciliation-only, per its docstring) view disagrees: it thinks
        # only 4 shares were ever filled for a DIFFERENT, unrelated order on the same symbol.
        await venue.place_order(OrderInstruction(client_order_id="unrelated:entry", symbol="AAPL", side="BUY", qty=4))
        venue.ingest_tick("AAPL", 100.0, datetime.now(timezone.utc))

        report = await reconcile_with_venue(session, venue, ps)

        assert report.has_discrepancy
        assert any("AAPL" in d for d in report.discrepancies)
    finally:
        session.close()
