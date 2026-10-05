"""Route tests for `GET /intelligence/portfolio-state` (`live-portfolio-details`).

A read-only detail projection of the running `PortfolioState.get_snapshot()`,
reached through the lifespan-installed `app.state.world_view_portfolio_reader`.

Fixtures are real, database-free, restored in-memory `PortfolioState`
instances (`_install_state`, `update_mark`), the same approach
`test_world_view_portfolio.py` uses. No PostgreSQL, lifespan, provider or
broker is involved: requests go through `httpx.ASGITransport`, which does not
run `main.py`'s `lifespan()`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from app.main import app
from app.portfolio_state.accounting import PositionState
from app.portfolio_state.engine import PortfolioState
from app.portfolio_state.ports import DailyAmounts, InFlightOrder, LedgerState

AS_OF = datetime(2026, 10, 5, 15, 0, 0, tzinfo=timezone.utc)
MARK_TS = AS_OF + timedelta(minutes=2)
DAY = date(2026, 10, 5)
KEYS = {
    "execution_mode", "trading_day", "snapshot_time", "open_position_count",
    "in_flight_order_count", "positions", "exposures", "marks",
    "realized_profit_today", "realized_loss_today", "realized_pnl_today",
    "reported_fees_today", "fees_today", "unknown_fee_count_today",
    "unrealized_pnl", "open_risk", "buying_power",
}


class _Clock:
    def trading_day(self, ts=None):
        return DAY


def _uuid(n: int) -> UUID:
    return UUID(int=n)


def _position(symbol="AAPL", side="BUY", qty=3, avg="101.2300", stop="98.005", target="110.5000", n=1):
    return PositionState(
        position_id=_uuid(n), trade_id=_uuid(100 + n), execution_mode="simulated",
        execution_venue="simulated", symbol=symbol, side=side, qty=qty,
        avg_price=Decimal(avg), opened_at=AS_OF,
        stop=None if stop is None else Decimal(stop),
        target=None if target is None else Decimal(target),
    )


def _portfolio(*, positions=(), orders=(), daily=(), history_complete=True, marks=None, ledger=None):
    portfolio = PortfolioState("simulated", clock=_Clock(), ledger=ledger)
    portfolio._install_state(LedgerState(
        "simulated", 1, AS_OF, positions=tuple(positions), orders=tuple(orders),
        daily=tuple(daily), history_complete=history_complete,
    ))
    for symbol, price in (marks or {}).items():
        portfolio.update_mark(symbol, Decimal(price), MARK_TS)
    return portfolio


@pytest.fixture(autouse=True)
def _reset_reader(monkeypatch):
    monkeypatch.setattr(app.state, "world_view_portfolio_reader", None, raising=False)


async def _get(path="/intelligence/portfolio-state", **params):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, params=params or None)


async def _portfolio_body(portfolio, **params):
    app.state.world_view_portfolio_reader = portfolio
    response = await _get(**params)
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"portfolio"}
    return body["portfolio"]


# --------------------------------------------------------------------- unavailable vs flat

async def test_missing_reader_is_null():
    response = await _get()
    assert response.status_code == 200
    assert response.json() == {"portfolio": None}


async def test_reader_without_snapshot_is_null():
    class _NoSnapshot:
        calls = 0

        def get_snapshot(self):
            self.calls += 1
            return None

    reader = _NoSnapshot()
    app.state.world_view_portfolio_reader = reader
    response = await _get()
    assert response.json() == {"portfolio": None}
    assert reader.calls == 1


async def test_unrestored_real_portfolio_state_is_null():
    # Never installed a ledger state: a real PortfolioState whose cache is not ready.
    app.state.world_view_portfolio_reader = PortfolioState("simulated", clock=_Clock())
    assert (await _get()).json() == {"portfolio": None}


async def test_blocked_ledger_state_is_null_not_flat():
    portfolio = PortfolioState("simulated", clock=_Clock())
    portfolio._install_state(LedgerState("simulated", 1, AS_OF, problems=("anomaly",)))
    app.state.world_view_portfolio_reader = portfolio
    assert (await _get()).json() == {"portfolio": None}


async def test_restored_flat_portfolio_is_populated_with_empty_collections():
    body = await _portfolio_body(_portfolio())
    assert set(body) == KEYS
    assert body["execution_mode"] == "simulated"
    assert body["trading_day"] == "2026-10-05"
    assert body["snapshot_time"] == AS_OF.isoformat()
    assert body["open_position_count"] == 0 and body["in_flight_order_count"] == 0
    assert body["positions"] == [] and body["exposures"] == [] and body["marks"] == []
    # Complete history with no rows today is a known zero, not an unknown.
    assert body["realized_profit_today"] == "0" and body["realized_loss_today"] == "0"
    assert body["realized_pnl_today"] == "0"
    assert body["reported_fees_today"] == "0" and body["fees_today"] == "0"
    assert body["unknown_fee_count_today"] == 0
    assert body["unrealized_pnl"] == "0" and body["open_risk"] == "0"
    assert body["buying_power"] is None


async def test_incomplete_history_keeps_daily_amounts_null():
    body = await _portfolio_body(_portfolio(history_complete=False))
    assert body["positions"] == []
    for key in ("realized_profit_today", "realized_loss_today", "realized_pnl_today",
                "reported_fees_today", "fees_today", "unknown_fee_count_today"):
        assert body[key] is None, key
    assert body["buying_power"] is None


# --------------------------------------------------------------------- marks

async def test_marked_position_exposes_mark_pnl_risk_and_timestamps():
    body = await _portfolio_body(_portfolio(positions=[_position()], marks={"AAPL": "103.5"}))
    assert body["open_position_count"] == 1
    assert body["positions"] == [{
        "position_id": str(_uuid(1)), "symbol": "AAPL", "side": "BUY", "qty": 3,
        "avg_entry_price": "101.2300", "stop": "98.005", "target": "110.5000",
        "opened_at": AS_OF.isoformat(),
    }]
    assert body["exposures"] == [{
        "symbol": "AAPL", "direction": "BUY", "qty": 3, "avg_entry_price": "101.2300",
        "stop": "98.005", "mark": "103.5", "unrealized_pnl": "6.8100", "is_in_flight": False,
    }]
    assert body["marks"] == [{"symbol": "AAPL", "price": "103.5", "as_of": MARK_TS.isoformat()}]
    assert body["unrealized_pnl"] == "6.8100"
    assert body["open_risk"] == "9.6750"  # |101.2300 - 98.005| * 3
    # The snapshot time is the newest of the ledger time and the marks.
    assert body["snapshot_time"] == MARK_TS.isoformat()


async def test_short_position_unrealized_sign_is_preserved():
    body = await _portfolio_body(_portfolio(
        positions=[_position("TSLA", "SELL", 2, "200.00", "205.00", None, n=2)], marks={"TSLA": "198.50"},
    ))
    assert body["exposures"][0]["unrealized_pnl"] == "3.00"  # (198.50 - 200.00) * 2 * -1
    assert body["unrealized_pnl"] == "3.00"


async def test_unmarked_position_keeps_unknowns_null_and_poisons_totals():
    body = await _portfolio_body(_portfolio(
        positions=[_position(n=1), _position("MSFT", "BUY", 5, "300.00", "295.00", None, n=2)],
        marks={"AAPL": "103.5"},
    ))
    by_symbol = {e["symbol"]: e for e in body["exposures"]}
    assert by_symbol["AAPL"]["unrealized_pnl"] == "6.8100"
    assert by_symbol["MSFT"]["mark"] is None and by_symbol["MSFT"]["unrealized_pnl"] is None
    # One unknown exposure makes the totals unknown; they are not partial sums.
    assert body["unrealized_pnl"] is None
    assert body["open_risk"] is None
    assert [m["symbol"] for m in body["marks"]] == ["AAPL"]
    assert body["snapshot_time"] == MARK_TS.isoformat()


async def test_position_without_stop_has_unknown_open_risk():
    body = await _portfolio_body(_portfolio(
        positions=[_position(stop=None, target=None)], marks={"AAPL": "103.5"},
    ))
    assert body["positions"][0]["stop"] is None and body["positions"][0]["target"] is None
    assert body["exposures"][0]["stop"] is None
    assert body["unrealized_pnl"] == "6.8100"
    assert body["open_risk"] is None


# --------------------------------------------------------------------- pending entries

async def test_pending_entry_is_distinct_from_held_exposure():
    entry = InFlightOrder("entry-1", "NVDA", "BUY", 10, "open", "simulated", "approved",
                          0, Decimal("450.00"), Decimal("445.00"))
    body = await _portfolio_body(_portfolio(orders=[entry]))
    assert body["open_position_count"] == 0 and body["in_flight_order_count"] == 1
    assert body["positions"] == []
    assert body["exposures"] == [{
        "symbol": "NVDA", "direction": "BUY", "qty": 10, "avg_entry_price": "450.00",
        "stop": "445.00", "mark": None, "unrealized_pnl": None, "is_in_flight": True,
    }]
    assert body["unrealized_pnl"] is None and body["open_risk"] is None


async def test_partially_filled_entry_splits_held_and_pending_remainder():
    held = _position("AAPL", "BUY", 4, "100.00", "95.00", None)
    entry = InFlightOrder("entry-2", "AAPL", "BUY", 10, "open", "simulated", "partially_filled",
                          4, Decimal("100.00"), Decimal("95.00"))
    body = await _portfolio_body(_portfolio(positions=[held], orders=[entry], marks={"AAPL": "102"}))
    assert body["open_position_count"] == 1 and body["in_flight_order_count"] == 1
    held_rows = [e for e in body["exposures"] if not e["is_in_flight"]]
    pending_rows = [e for e in body["exposures"] if e["is_in_flight"]]
    assert [r["qty"] for r in held_rows] == [4]
    assert [r["qty"] for r in pending_rows] == [6]  # remainder only, never the ordered 10
    assert held_rows[0]["unrealized_pnl"] == "8.00"
    assert pending_rows[0]["unrealized_pnl"] == "12.00"
    assert body["unrealized_pnl"] == "20.00"
    assert body["open_risk"] == "50.00"  # 5 * 4 + 5 * 6


async def test_exit_order_counts_as_in_flight_but_adds_no_exposure():
    held = _position()
    exit_order = InFlightOrder("exit-1", "AAPL", "SELL", 3, "close", "simulated")
    body = await _portfolio_body(_portfolio(positions=[held], orders=[exit_order], marks={"AAPL": "103.5"}))
    assert body["in_flight_order_count"] == 1
    assert len(body["exposures"]) == 1 and body["exposures"][0]["is_in_flight"] is False


# --------------------------------------------------------------------- daily results and fees

async def test_daily_results_use_only_the_snapshot_trading_day():
    daily = [
        DailyAmounts("AAPL", DAY, Decimal("12.5000"), Decimal("2.2500"), Decimal("1.00"), 0),
        DailyAmounts("MSFT", DAY, Decimal("0"), Decimal("4.00"), Decimal("0.50"), 0),
        DailyAmounts("AAPL", DAY - timedelta(days=1), Decimal("99"), Decimal("1"), Decimal("9"), 3),
    ]
    body = await _portfolio_body(_portfolio(daily=daily))
    assert body["realized_profit_today"] == "12.5000"
    assert body["realized_loss_today"] == "6.2500"
    assert body["realized_pnl_today"] == "6.2500"
    assert body["reported_fees_today"] == "1.50"
    assert body["fees_today"] == "1.50"  # complete: no unknown fee
    assert body["unknown_fee_count_today"] == 0


async def test_unknown_fees_make_total_fees_null_but_keep_reported_fees():
    daily = [
        DailyAmounts("AAPL", DAY, Decimal("5"), Decimal("0"), Decimal("1.25"), 2),
        DailyAmounts("MSFT", DAY, Decimal("0"), Decimal("1"), Decimal("0.75"), 1),
    ]
    body = await _portfolio_body(_portfolio(daily=daily))
    assert body["reported_fees_today"] == "2.00"
    assert body["fees_today"] is None
    assert body["unknown_fee_count_today"] == 3
    assert body["realized_pnl_today"] == "4"


# --------------------------------------------------------------------- exact decimals

async def test_decimals_serialize_exactly_without_float_or_exponent_loss():
    avg = "123456789.123456789"
    position = _position(avg=avg, stop="0.0000001", target="1E+3")
    body = await _portfolio_body(_portfolio(positions=[position], marks={"AAPL": "0.1000000001"}))
    row = body["positions"][0]
    assert row["avg_entry_price"] == avg
    assert row["stop"] == "0.0000001"  # fixed-point, not "1E-7"
    assert row["target"] == "1000"
    assert body["exposures"][0]["mark"] == "0.1000000001"
    assert body["marks"][0]["price"] == "0.1000000001"
    expected = (Decimal("0.1000000001") - Decimal(avg)) * 3
    assert body["unrealized_pnl"] == format(expected, "f")
    assert all(isinstance(body[key], str) for key in ("unrealized_pnl", "open_risk"))
    # Quantities and counts stay JSON integers.
    assert row["qty"] == 3 and body["open_position_count"] == 1


# --------------------------------------------------------------------- read-only behavior

class _ExplodingLedger:
    """Any ledger touch fails the test: the route must never read or write it."""

    def __getattr__(self, name):
        raise AssertionError(f"route touched the ledger: {name}")


async def test_one_system_wide_snapshot_per_request_and_symbol_is_ignored():
    inner = _portfolio(positions=[_position()], marks={"AAPL": "103.5"})
    calls = []

    class _Spy:
        def get_snapshot(self, *args, **kwargs):
            calls.append((args, kwargs))
            return inner.get_snapshot(*args, **kwargs)

    app.state.world_view_portfolio_reader = _Spy()
    responses = [await _get(), await _get(symbol="ZZZ")]
    assert [r.status_code for r in responses] == [200, 200]
    plain, scoped = (r.json() for r in responses)
    assert calls == [((), {}), ((), {})]  # one call per request, never scoped
    assert plain == scoped
    assert plain["portfolio"]["positions"][0]["symbol"] == "AAPL"


async def test_route_does_not_touch_ledger_refresh_or_state():
    portfolio = _portfolio(positions=[_position()], orders=[
        InFlightOrder("e", "NVDA", "BUY", 2, "open", "simulated", "approved", 0, Decimal("10"), Decimal("9")),
    ], marks={"AAPL": "103.5"}, ledger=_ExplodingLedger())
    before_state, before_marks = portfolio._state, dict(portfolio._marks)

    class _Guarded:
        """get_snapshot() works; every other PortfolioState entry point is a tripwire."""

        def get_snapshot(self):
            return portfolio.get_snapshot()

        def __getattr__(self, name):
            def _boom(*args, **kwargs):
                raise AssertionError(f"route called PortfolioState.{name}()")
            return _boom

    app.state.world_view_portfolio_reader = _Guarded()
    # The app's error middleware turns an exception into a 500 body, so a tripwire
    # firing must be seen as a status, not an exception.
    responses = [await _get(), await _get()]
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    first, second = (r.json() for r in responses)
    assert first == second
    assert first["portfolio"]["positions"][0]["symbol"] == "AAPL"
    assert portfolio._state is before_state and portfolio._marks == before_marks
    assert portfolio._queue.empty()  # nothing was enqueued for the worker


def test_route_has_no_write_methods():
    methods = {
        method for route in app.routes
        if getattr(route, "path", None) == "/intelligence/portfolio-state"
        for method in route.methods
    }
    assert methods == {"GET"}


async def test_world_view_portfolio_projection_is_unchanged():
    # The compact World View slot keeps its original shape alongside the detail route.
    app.state.world_view_portfolio_reader = _portfolio(positions=[_position()])
    import app.world_view.composite as composite

    portfolio = composite._read_portfolio(app.state.world_view_portfolio_reader)
    assert portfolio is not None
    assert set(portfolio.__dataclass_fields__) == {
        "execution_mode", "snapshot_time", "positions", "in_flight_order_count",
    }
