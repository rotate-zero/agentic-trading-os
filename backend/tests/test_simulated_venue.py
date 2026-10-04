"""
SimulatedVenue behavior — the venue half of decision
decision #172 (EX-1/EX-3/EX-8). No database involved; this
is the venue's own in-memory order/fill book, exercised via direct
`ingest_tick()` calls and real PriceUpdated EventBus delivery.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from app.broker_adapters.order_venue import OrderInstruction
from app.broker_adapters.simulated_venue import SimulatedVenue
from app.core.market_clock import MarketClock
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventEnvelope, EventType
from app.schemas.events.market_data import PriceUpdated


class _FixedClock(MarketClock):
    """A MarketClock stand-in whose regular-session answer is fixed by
    the test, independent of wall-clock time (§6.4's "injectable
    clock")."""

    def __init__(self, *, regular_session: bool) -> None:
        self._regular_session = regular_session

    def is_regular_session(self, ts: datetime | None = None) -> bool:  # type: ignore[override]
        return self._regular_session


def _ts(hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 9, 22, hour, minute, tzinfo=timezone.utc)


class _BrokenTimezone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        raise ValueError("invalid offset")

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None


@pytest.fixture
def venue() -> SimulatedVenue:
    v = SimulatedVenue(clock=_FixedClock(regular_session=True))
    return v


async def _connected(v: SimulatedVenue) -> SimulatedVenue:
    await v.connect()
    return v


@pytest.mark.asyncio
async def test_market_order_fills_on_first_tick(venue: SimulatedVenue) -> None:
    await _connected(venue)
    updates = []
    venue.on_order_update(updates.append)

    ack = await venue.place_order(
        OrderInstruction(client_order_id="t1:entry", symbol="AAPL", side="BUY", qty=10)
    )
    assert ack.status == "submitted"
    assert updates == []  # no fill yet — no tick has arrived

    venue.ingest_tick("AAPL", 190.00, _ts())

    assert len(updates) == 1
    fill = updates[0]
    assert fill.status == "filled"
    assert fill.fill_qty == 10
    assert fill.fill_price == 190.00
    assert fill.venue_fill_id == "t1:entry:f1"
    assert fill.commission is None  # I3 — never fabricated

    report = await venue.get_order("t1:entry")
    assert report is not None
    assert report.status == "filled"
    assert report.filled_qty == 10
    assert report.leaves_qty == 0


@pytest.mark.asyncio
async def test_limit_order_only_fills_on_cross(venue: SimulatedVenue) -> None:
    await _connected(venue)
    await venue.place_order(
        OrderInstruction(
            client_order_id="t2:entry", symbol="MSFT", side="BUY", qty=5,
            order_type="limit", limit_price=100.0,
        )
    )
    venue.ingest_tick("MSFT", 100.50, _ts())  # above limit — no cross yet
    report = await venue.get_order("t2:entry")
    assert report.status == "submitted"
    assert report.filled_qty == 0

    venue.ingest_tick("MSFT", 99.99, _ts(10, 1))  # crosses
    report = await venue.get_order("t2:entry")
    assert report.status == "filled"
    assert report.filled_qty == 5


@pytest.mark.asyncio
async def test_place_order_idempotent_on_client_order_id(venue: SimulatedVenue) -> None:
    await _connected(venue)
    instruction = OrderInstruction(client_order_id="t3:entry", symbol="AAPL", side="BUY", qty=1)
    ack1 = await venue.place_order(instruction)
    ack2 = await venue.place_order(instruction)
    assert ack1 == ack2
    # A single tick should only ever produce ONE fill for this order,
    # not two, even though place_order was called twice (I10, I11).
    updates = []
    venue.on_order_update(updates.append)
    venue.ingest_tick("AAPL", 190.0, _ts())
    assert len(updates) == 1


@pytest.mark.asyncio
async def test_session_guard_rejects_outside_regular_session() -> None:
    v = SimulatedVenue(clock=_FixedClock(regular_session=False))
    await v.connect()
    ack = await v.place_order(
        OrderInstruction(client_order_id="t4:entry", symbol="AAPL", side="BUY", qty=1)
    )
    assert ack.status == "rejected"
    assert ack.reason == "outside_regular_session"
    # Idempotent replay of a rejected order also returns the same ack,
    # not a second attempt.
    ack2 = await v.place_order(
        OrderInstruction(client_order_id="t4:entry", symbol="AAPL", side="BUY", qty=1)
    )
    assert ack2 == ack


@pytest.mark.asyncio
async def test_unknown_order_id_reports_none_never_fabricated(venue: SimulatedVenue) -> None:
    await _connected(venue)
    assert await venue.get_order("never-placed") is None
    assert await venue.get_fills("never-placed") == []
    assert await venue.list_open_orders() == []


@pytest.mark.asyncio
async def test_fresh_instance_has_no_memory_of_prior_orders(venue: SimulatedVenue) -> None:
    """Simulates a restart: SimulatedVenue is not durable (I12) — a new
    instance genuinely has no record of an order an old instance
    accepted, which is exactly what the Execution Engine's restart
    reconciliation (§6.9) is supposed to detect and act on."""
    await _connected(venue)
    await venue.place_order(OrderInstruction(client_order_id="t5:entry", symbol="AAPL", side="BUY", qty=1))
    assert await venue.get_order("t5:entry") is not None

    fresh = SimulatedVenue(clock=_FixedClock(regular_session=True))
    await fresh.connect()
    assert await fresh.get_order("t5:entry") is None


@pytest.mark.asyncio
async def test_partial_fill_planner_injection(venue: SimulatedVenue) -> None:
    v = SimulatedVenue(
        clock=_FixedClock(regular_session=True),
        partial_fill_planner=lambda instr: [3, 7],
    )
    await v.connect()
    updates = []
    v.on_order_update(updates.append)
    await v.place_order(OrderInstruction(client_order_id="t6:entry", symbol="AAPL", side="BUY", qty=10))

    v.ingest_tick("AAPL", 100.0, _ts())
    assert len(updates) == 1
    assert updates[0].status == "partially_filled"
    assert updates[0].fill_qty == 3
    assert updates[0].venue_fill_id == "t6:entry:f1"

    v.ingest_tick("AAPL", 101.0, _ts(10, 1))
    assert len(updates) == 2
    assert updates[1].status == "filled"
    assert updates[1].fill_qty == 7
    assert updates[1].venue_fill_id == "t6:entry:f2"


@pytest.mark.asyncio
async def test_cancel_is_noop_for_unknown_or_terminal_order(venue: SimulatedVenue) -> None:
    await _connected(venue)
    await venue.cancel_order("never-placed")  # must not raise

    await venue.place_order(OrderInstruction(client_order_id="t7:entry", symbol="AAPL", side="BUY", qty=1))
    venue.ingest_tick("AAPL", 100.0, _ts())
    await venue.cancel_order("t7:entry")  # already filled — no-op
    report = await venue.get_order("t7:entry")
    assert report.status == "filled"


@pytest.mark.asyncio
async def test_cancel_open_order_dispatches_cancelled_update(venue: SimulatedVenue) -> None:
    await _connected(venue)
    updates = []
    venue.on_order_update(updates.append)
    await venue.place_order(OrderInstruction(client_order_id="t8:entry", symbol="AAPL", side="BUY", qty=1))
    await venue.cancel_order("t8:entry")
    assert len(updates) == 1
    assert updates[0].status == "cancelled"

    # A tick arriving after cancellation must not fill it.
    venue.ingest_tick("AAPL", 100.0, _ts())
    assert len(updates) == 1


@pytest.mark.asyncio
async def test_get_positions_is_derived_and_reconciliation_only(venue: SimulatedVenue) -> None:
    await _connected(venue)
    await venue.place_order(OrderInstruction(client_order_id="t9:entry", symbol="AAPL", side="BUY", qty=10))
    venue.ingest_tick("AAPL", 100.0, _ts())

    positions = await venue.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].qty == 10
    assert positions[0].side == "BUY"
    assert positions[0].avg_cost == 100.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("price", "exchange_ts"),
    [
        pytest.param(float("nan"), _ts(), id="nan"),
        pytest.param(float("inf"), _ts(), id="positive-infinity"),
        pytest.param(float("-inf"), _ts(), id="negative-infinity"),
        pytest.param(0.0, _ts(), id="zero"),
        pytest.param(-1.0, _ts(), id="negative"),
        pytest.param(100.0, datetime(2026, 9, 22, 10), id="naive-timestamp"),
        pytest.param(100.0, datetime(2026, 9, 22, 10, tzinfo=_BrokenTimezone()), id="broken-timezone"),
        pytest.param(100.0, None, id="missing-timestamp"),
    ],
)
async def test_invalid_tick_preserves_all_pending_orders_and_next_tranche(
    price: float, exchange_ts: datetime,
) -> None:
    venue = SimulatedVenue(
        clock=_FixedClock(regular_session=True),
        partial_fill_planner=lambda instruction: [2, 3],
    )
    await venue.connect()
    updates = []
    venue.on_order_update(updates.append)
    instructions = [
        OrderInstruction(client_order_id="market", symbol="AAPL", side="BUY", qty=5),
        OrderInstruction(client_order_id="buy-limit", symbol="AAPL", side="BUY", qty=5,
                         order_type="limit", limit_price=100.0),
        OrderInstruction(client_order_id="sell-limit", symbol="AAPL", side="SELL", qty=5,
                         order_type="limit", limit_price=100.0),
    ]
    for instruction in instructions:
        await venue.place_order(instruction)

    pending_before = list(venue._pending_by_symbol["AAPL"])
    venue.ingest_tick("AAPL", price, exchange_ts)
    assert updates == []
    assert venue._pending_by_symbol["AAPL"] == pending_before
    for instruction in instructions:
        report = await venue.get_order(instruction.client_order_id)
        assert report is not None
        assert (report.status, report.filled_qty, report.leaves_qty) == ("submitted", 0, 5)
        assert venue._orders[instruction.client_order_id].plan_index == 0
        assert await venue.get_fills(instruction.client_order_id) == []
    assert len(await venue.list_open_orders()) == len(instructions)

    valid_ts = datetime(2026, 9, 22, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    venue.ingest_tick("AAPL", 100.0, valid_ts)
    assert len(updates) == len(instructions)
    for update in updates:
        assert (update.status, update.fill_qty, update.cumulative_qty, update.leaves_qty) == (
            "partially_filled", 2, 2, 3,
        )
        assert update.venue_fill_id == f"{update.client_order_id}:f1"
        assert update.venue_ts.isoformat() == valid_ts.isoformat()

    venue.ingest_tick("AAPL", 100.0, _ts(10, 1))
    for instruction in instructions:
        fills = await venue.get_fills(instruction.client_order_id)
        assert [fill.venue_fill_id for fill in fills] == [
            f"{instruction.client_order_id}:f1", f"{instruction.client_order_id}:f2",
        ]
        assert [fill.fill_qty for fill in fills] == [2, 3]
        report = await venue.get_order(instruction.client_order_id)
        assert report is not None
        assert (report.status, report.filled_qty, report.leaves_qty) == ("filled", 5, 0)


@pytest.mark.asyncio
async def test_invalid_tick_between_partial_fills_preserves_next_fill_number() -> None:
    venue = SimulatedVenue(
        clock=_FixedClock(regular_session=True),
        partial_fill_planner=lambda instruction: [2, 3, 5],
    )
    await venue.connect()
    updates = []
    venue.on_order_update(updates.append)
    await venue.place_order(OrderInstruction(client_order_id="partial", symbol="AAPL", side="BUY", qty=10))
    venue.ingest_tick("AAPL", 100.0, _ts())

    venue.ingest_tick("AAPL", 0.0, _ts(10, 1))
    report = await venue.get_order("partial")
    assert report is not None
    assert (report.status, report.filled_qty, report.leaves_qty) == ("partially_filled", 2, 8)
    assert venue._orders["partial"].plan_index == 1
    assert venue._pending_by_symbol["AAPL"] == ["partial"]
    assert [fill.venue_fill_id for fill in await venue.get_fills("partial")] == ["partial:f1"]
    assert len(updates) == 1

    venue.ingest_tick("AAPL", 101.0, _ts(10, 2))
    assert [fill.venue_fill_id for fill in await venue.get_fills("partial")] == ["partial:f1", "partial:f2"]
    assert [fill.fill_qty for fill in await venue.get_fills("partial")] == [2, 3]
    assert (await venue.get_order("partial")).leaves_qty == 5


@pytest.mark.asyncio
async def test_event_bus_invalid_ticks_do_not_consume_order_or_stop_delivery() -> None:
    bus = EventBus()
    venue = SimulatedVenue(event_bus=bus, clock=_FixedClock(regular_session=True))
    await venue.connect()
    await bus.start()
    updates = []
    venue.on_order_update(updates.append)
    try:
        await venue.place_order(OrderInstruction(client_order_id="bus-market", symbol="AAPL", side="BUY", qty=1))
        for tick in (
            PriceUpdated(price=float("nan"), size=1, exchange_ts=_ts()),
            PriceUpdated(price=0.0, size=1, exchange_ts=_ts()),
            PriceUpdated(price=100.0, size=1, exchange_ts=datetime(2026, 9, 22, 10)),
        ):
            await bus.publish(make_envelope(EventType.PRICE_UPDATED, tick, symbol="AAPL"))
        await bus.publish(EventEnvelope(
            event_type=EventType.PRICE_UPDATED,
            symbol="AAPL",
            payload={"price": 100.0, "size": 1, "exchange_ts": "unparseable"},
        ))
        await asyncio.wait_for(bus._normal_queue.join(), timeout=1)
        assert updates == []
        assert (await venue.get_order("bus-market")).status == "submitted"
        assert await venue.get_fills("bus-market") == []

        offset_ts = datetime(2026, 9, 22, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        await bus.publish(make_envelope(
            EventType.PRICE_UPDATED, PriceUpdated(price=100.0, size=1, exchange_ts=offset_ts), symbol="AAPL",
        ))
        await asyncio.wait_for(bus._normal_queue.join(), timeout=1)
        assert len(updates) == 1
        assert updates[0].venue_fill_id == "bus-market:f1"
        assert updates[0].venue_ts.isoformat() == offset_ts.isoformat()
    finally:
        await venue.disconnect()
        await bus.stop()


def test_repeated_invalid_ticks_log_once_without_traceback(venue: SimulatedVenue, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING", logger="app.broker_adapters.simulated_venue"):
        venue.ingest_tick("AAPL", float("nan"), _ts())
        venue.ingest_tick("AAPL", 0.0, _ts())
        venue.ingest_tick("AAPL", 100.0, datetime(2026, 9, 22, 10))
    warnings = [record for record in caplog.records if "ignored invalid tick" in record.message]
    assert len(warnings) == 1
    assert warnings[0].exc_info is None
