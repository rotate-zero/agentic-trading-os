"""Outcome snapshot JSON safety (task: outcome-snapshot-json-serialization).

Regression for the real Finnhub trial failure: FundamentalsProvider hands the
Context Engine raw ``datetime``/``date`` values, and the snapshot captured for a
simulated MSFT entry could not be persisted as JSON. These tests drive the real
provider, ContextEngine, capture functions, OutcomeRecorder persistence and the
shared backtest writer against PostgreSQL. Only the MarketStateEngine (which
needs candles) and the symbol the context is read for are stand-ins.
"""
from __future__ import annotations

import copy
import json
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.context_engine.engine import ContextEngine
from app.context_engine.provider import SymbolContextProvider
from app.context_engine.providers.calendar import CalendarProvider
from app.context_engine.providers.fundamentals import FundamentalsProvider
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.models.execution_ledger import Fill, Position, Trade
from app.models.market_data import Symbol
from app.models.symbol_fundamentals import SymbolFundamentals
from app.models.trading_intelligence import BacktestRunRecord, StrategyOutcomeRecord
from app.schemas.performance import StrategyOutcome
from app.trading_intelligence import state_snapshot
from app.trading_intelligence.outcome_recorder import OutcomeRecorder
from app.trading_intelligence.performance import record_strategy_outcome
from app.trading_intelligence.state_snapshot import (
    capture_context_snapshot,
    capture_market_state_snapshot,
    capture_strategy_outcome_snapshots,
)
from tests.test_outcome_recorder import (  # noqa: F401  (_cleanup is an autouse fixture)
    NAME,
    _cleanup,
    _result,
    _seed,
    _seed_first_entry,
)

FUND_TICKER = "TESTSNAPFUND"
BT_NAME = "TEST_SNAPSHOT_BACKTEST"
DHAKA = timezone(timedelta(hours=6))
PROFILE_AT = datetime(2026, 10, 7, 21, 30, 15, 250000, tzinfo=DHAKA)  # 15:30:15.25 UTC
PROFILE_AT_Z = "2026-10-07T15:30:15.250000Z"
EARNINGS_DAY = date(2026, 10, 28)


def _db_available() -> bool:
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(not _db_available(), reason="real PostgreSQL unavailable")


# --- fixtures -------------------------------------------------------------------


def _clean_fundamentals():
    with SessionLocal.begin() as session:
        session.execute(text("DELETE FROM symbol_fundamentals WHERE symbol_id IN "
                             "(SELECT id FROM symbols WHERE ticker = :t)"), {"t": FUND_TICKER})
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": FUND_TICKER})


def _clean_backtest():
    with SessionLocal.begin() as session:
        session.execute(text("DELETE FROM strategy_outcomes WHERE strategy_name = :n"), {"n": BT_NAME})
        session.execute(text("DELETE FROM backtests WHERE strategy_name = :n"), {"n": BT_NAME})


@pytest.fixture
def fundamentals_row():
    """A populated, partly-null symbol_fundamentals row with a non-UTC aware timestamp."""
    _clean_fundamentals()
    with SessionLocal.begin() as session:
        session.execute(pg_insert(Symbol).values(ticker=FUND_TICKER, is_backtest=False)
                        .on_conflict_do_nothing(index_elements=["ticker", "is_backtest"]))
        symbol_id = session.execute(text("SELECT id FROM symbols WHERE ticker = :t"),
                                    {"t": FUND_TICKER}).scalar_one()
        session.add(SymbolFundamentals(
            symbol_id=symbol_id, sector=None, industry="Technology",
            profile_updated_at=PROFILE_AT, market_cap=3100000.5, market_cap_updated_at=PROFILE_AT,
            revenue_ttm=None, net_income_ttm=None, operating_cash_flow_ttm=None,
            financials_period=None, financials_updated_at=None,
            next_earnings_date=EARNINGS_DAY, earnings_updated_at=PROFILE_AT))
    yield
    _clean_fundamentals()


@pytest.fixture
def backtest_cleanup():
    _clean_backtest()
    yield
    _clean_backtest()


class _StubMarketEngine:
    """MarketStateEngine needs live candles; its get_snapshot() shape is all capture reads."""

    def __init__(self, symbol_state):
        self.symbol_state = symbol_state
        self.market = {"spy": {"trend": "up", "score": 1}, "qqq": None}

    def get_snapshot(self, symbol):
        symbols = {} if self.symbol_state is None else {symbol: self.symbol_state}
        return {"symbols": symbols, "market": self.market}


class _ReplayProvider(SymbolContextProvider):
    """Serves a payload the REAL FundamentalsProvider read from PostgreSQL for another ticker."""

    name = "fundamentals"

    def __init__(self, payload):
        self.payload = payload

    async def evaluate(self, symbol):
        return self.payload


async def _context_engine(payload, symbol="AAPL"):
    engine = ContextEngine(EventBus(), providers=[CalendarProvider()],
                           symbol_providers=[_ReplayProvider(payload)])
    await engine.evaluate_all()
    await engine.evaluate_for_symbol(symbol)
    return engine


async def _real_fundamentals_payload():
    payload = await FundamentalsProvider().evaluate(FUND_TICKER)
    # Guard the premise: these are the raw, non-JSON types the provider really returns.
    assert isinstance(payload["profile_updated_at"], datetime)
    assert isinstance(payload["next_earnings_date"], date) and not isinstance(payload["next_earnings_date"], datetime)
    with pytest.raises(TypeError):
        json.dumps(payload)
    return payload


def _market_state():
    return {"candle_ts": "2026-10-07T15:30:00Z", "regime": "trend", "score": 0.5, "flag": True, "gap": None}


def _install(monkeypatch, context_engine, market_state=None):
    market = _StubMarketEngine(_market_state() if market_state is None else market_state)
    monkeypatch.setattr(state_snapshot, "get_context_engine", lambda: context_engine)
    monkeypatch.setattr(state_snapshot, "get_market_state_engine", lambda: market)
    return market


# --- serializer contract (no database needed) ------------------------------------


def test_serializer_converts_supported_datetimes_and_preserves_everything_else():
    from app.trading_intelligence.state_snapshot import to_json_safe_snapshot

    source = {
        "stamp": PROFILE_AT, "utc": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc), "day": EARNINGS_DAY,
        "nested": {"when": [PROFILE_AT, None, {"deep": (1, 2.5, "x", True, False)}]},
        "none": None, "int": 7, "float": 0.1, "str": "s", "bool": True, "empty": {}, "empty_list": [],
    }
    out = to_json_safe_snapshot(source)
    assert out == {
        "stamp": PROFILE_AT_Z, "utc": "2026-01-02T03:04:05Z", "day": "2026-10-28",
        "nested": {"when": [PROFILE_AT_Z, None, {"deep": [1, 2.5, "x", True, False]}]},
        "none": None, "int": 7, "float": 0.1, "str": "s", "bool": True, "empty": {}, "empty_list": [],
    }
    assert out["bool"] is True and type(out["int"]) is int
    json.dumps(out, allow_nan=False)


def test_serializer_returns_a_detached_copy_in_both_directions():
    from app.trading_intelligence.state_snapshot import to_json_safe_snapshot

    source = {"a": {"b": [1, {"c": 2}]}}
    before = copy.deepcopy(source)
    out = to_json_safe_snapshot(source)
    source["a"]["b"][1]["c"] = 99
    source["a"]["b"].append(3)
    assert out == before
    out["a"]["b"][0] = -1
    assert source["a"]["b"][0] == 1


@pytest.mark.parametrize("bad, fragment", [
    (datetime(2026, 1, 1, 12), "timezone-naive"),
    (Decimal("1.5"), "Decimal"),
    ({1, 2}, "set"),
    (b"raw", "bytes"),
    (object(), "object"),
    (float("nan"), "non-finite"),
    (math.inf, "non-finite"),
    ({"k": 1}.keys(), "dict_keys"),
])
def test_serializer_rejects_unsupported_values_with_the_path_and_type_only(bad, fragment):
    from app.trading_intelligence.state_snapshot import SnapshotSerializationError, to_json_safe_snapshot

    with pytest.raises(SnapshotSerializationError) as caught:
        to_json_safe_snapshot({"fundamentals": {"field": [bad]}})
    assert "snapshot.fundamentals.field[0]" in str(caught.value)
    assert fragment in str(caught.value)


def test_serializer_rejects_non_string_keys_and_cycles():
    from app.trading_intelligence.state_snapshot import SnapshotSerializationError, to_json_safe_snapshot

    with pytest.raises(SnapshotSerializationError, match="non-string key"):
        to_json_safe_snapshot({1: "a"})
    loop = {}
    loop["self"] = loop
    with pytest.raises(SnapshotSerializationError, match="circular"):
        to_json_safe_snapshot(loop)


def test_serializer_does_not_stringify_or_leak_the_offending_value():
    from app.trading_intelligence.state_snapshot import SnapshotSerializationError, to_json_safe_snapshot

    class Secret:
        def __repr__(self):
            return "SECRET-VALUE"

    with pytest.raises(SnapshotSerializationError) as caught:
        to_json_safe_snapshot({"x": Secret()})
    assert "SECRET-VALUE" not in str(caught.value)


# --- capture from the real provider ------------------------------------------------


@requires_db
async def test_real_provider_payload_is_captured_json_safe_with_utc_nulls_and_detachment(fundamentals_row, monkeypatch):
    payload = await _real_fundamentals_payload()
    engine = await _context_engine(payload)
    _install(monkeypatch, engine)

    captured = capture_context_snapshot("AAPL")

    json.dumps(captured, allow_nan=False)
    fundamentals = captured["fundamentals"]
    assert fundamentals["profile_updated_at"] == PROFILE_AT_Z
    assert fundamentals["market_cap_updated_at"] == PROFILE_AT_Z
    assert fundamentals["earnings_updated_at"] == PROFILE_AT_Z
    assert fundamentals["next_earnings_date"] == "2026-10-28"
    assert fundamentals["market_cap"] == 3100000.5
    assert fundamentals["industry"] == "Technology"
    # Unavailable stays unavailable: never fabricated or dropped.
    for key in ("sector", "revenue_ttm", "net_income_ttm", "operating_cash_flow_ttm",
                "financials_period", "financials_updated_at"):
        assert key in fundamentals and fundamentals[key] is None
    assert captured["calendar"]["trading_day"]  # sibling provider state is intact

    # Detached: the engine keeps its own raw values and later mutation cannot reach the capture.
    cached = engine._latest_by_symbol["AAPL"]["fundamentals"]
    assert isinstance(cached["profile_updated_at"], datetime)
    snapshot_before = copy.deepcopy(captured)
    cached["industry"] = "MUTATED"
    engine._latest_global["calendar"]["session"] = "MUTATED"
    assert captured == snapshot_before
    captured["fundamentals"]["industry"] = "CAPTURE-MUTATED"
    assert engine._latest_by_symbol["AAPL"]["fundamentals"]["industry"] == "MUTATED"  # engine untouched by capture edits


@requires_db
async def test_market_state_capture_is_detached_and_keeps_nested_null(monkeypatch):
    market = _install(monkeypatch, ContextEngine(EventBus(), providers=[], symbol_providers=[]))
    captured = capture_market_state_snapshot("AAPL")
    assert captured == {**_market_state(), "market": {"spy": {"trend": "up", "score": 1}, "qqq": None}}
    market.market["spy"]["trend"] = "down"
    market.symbol_state["score"] = 9
    assert captured["market"]["spy"]["trend"] == "up" and captured["score"] == 0.5


@requires_db
def test_unavailable_state_still_returns_none_not_a_placeholder(monkeypatch):
    _install(monkeypatch, ContextEngine(EventBus(), providers=[], symbol_providers=[]), market_state=None)
    market = state_snapshot.get_market_state_engine()
    market.symbol_state = None
    snapshots = capture_strategy_outcome_snapshots("AAPL")
    assert snapshots.market_state is None and snapshots.context is None


@requires_db
async def test_unsupported_value_in_context_raises_explicitly_at_capture(monkeypatch):
    from app.trading_intelligence.state_snapshot import SnapshotSerializationError

    _install(monkeypatch, await _context_engine({"market_cap": Decimal("1.5")}))
    with pytest.raises(SnapshotSerializationError, match=r"context\.fundamentals\.market_cap"):
        capture_context_snapshot("AAPL")
    with pytest.raises(SnapshotSerializationError):
        capture_strategy_outcome_snapshots("AAPL")


# --- OutcomeRecorder: entry, exit and the linked outcome ---------------------------------


@requires_db
async def test_entry_snapshot_with_real_provider_payload_persists(fundamentals_row, monkeypatch):
    engine = await _context_engine(await _real_fundamentals_payload())
    _install(monkeypatch, engine)
    trade_id, order_id = _seed_first_entry()

    await OutcomeRecorder(EventBus(), SessionLocal).capture_entry(order_id)

    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        assert trade.entry_snapshot_captured_at is not None
        assert trade.entry_snapshot_missing_reasons is None
        assert trade.entry_market_state["regime"] == "trend"
        assert trade.entry_context["fundamentals"]["profile_updated_at"] == PROFILE_AT_Z
        assert trade.entry_context["fundamentals"]["next_earnings_date"] == "2026-10-28"
        assert trade.entry_context["fundamentals"]["revenue_ttm"] is None
        assert session.scalar(select(Fill).where(Fill.client_order_id == order_id)) is not None


@requires_db
async def test_entry_snapshot_is_not_changed_by_later_engine_mutation(fundamentals_row, monkeypatch):
    engine = await _context_engine(await _real_fundamentals_payload())
    market = _install(monkeypatch, engine)
    trade_id, order_id = _seed_first_entry()
    await OutcomeRecorder(EventBus(), SessionLocal).capture_entry(order_id)
    engine._latest_by_symbol["AAPL"]["fundamentals"]["industry"] = "LATER"
    market.symbol_state["regime"] = "LATER"
    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        assert trade.entry_context["fundamentals"]["industry"] == "Technology"
        assert trade.entry_market_state["regime"] == "trend"


@requires_db
async def test_closed_simulated_trade_persists_linked_outcome_with_entry_and_exit_snapshots(fundamentals_row, monkeypatch):
    engine = await _context_engine(await _real_fundamentals_payload())
    _install(monkeypatch, engine)
    trade_id, _ = _seed()
    recorder = OutcomeRecorder(EventBus(), SessionLocal)
    # Entry is stored while the trade is open (the real order of events); the seed is already closed.
    _set_status(trade_id, "open")
    market, context, reasons = module_capture("entry")
    await _store_entry(recorder, trade_id, market, context, reasons)
    _set_status(trade_id, "closed")

    assert await recorder.record_trade(trade_id) == "recorded"

    status, outcome_id, rows = _result(trade_id)
    assert status == "recorded" and len(rows) == 1 and rows[0].outcome_id == outcome_id
    row = rows[0]
    for snapshot in (row.context_at_entry, row.context_at_exit):
        assert snapshot["fundamentals"]["profile_updated_at"] == PROFILE_AT_Z
        assert snapshot["fundamentals"]["next_earnings_date"] == "2026-10-28"
        assert snapshot["fundamentals"]["sector"] is None
    assert row.market_state_at_entry["regime"] == row.market_state_at_exit["regime"] == "trend"
    assert row.snapshot_missing_reasons is None
    # Accounting is the ledger's, unchanged by the snapshot work.
    assert tuple(float(v) for v in (row.entry_price, row.exit_price, row.entry_qty, row.exit_qty)) == (101.2, 105.4, 10.0, 10.0)
    assert float(row.realized_pnl) == pytest.approx(42.0)


@requires_db
async def test_unsupported_context_value_keeps_market_state_and_records_reason_at_entry(monkeypatch):
    _install(monkeypatch, await _context_engine({"market_cap": Decimal("1.5")}))
    trade_id, order_id = _seed_first_entry()

    await OutcomeRecorder(EventBus(), SessionLocal).capture_entry(order_id)

    with SessionLocal() as session:
        trade = session.get(Trade, trade_id)
        assert trade.entry_market_state["regime"] == "trend"  # valid unrelated state is kept
        assert trade.entry_context is None
        assert trade.entry_snapshot_missing_reasons == {"context_at_entry": "snapshot_capture_error"}
        assert trade.entry_snapshot_captured_at is not None
        # durable fill accounting is untouched
        fill = session.scalar(select(Fill).where(Fill.client_order_id == order_id))
        assert fill is not None and fill.qty == 4


@requires_db
async def test_unsupported_context_value_at_exit_records_outcome_with_reason_not_retry_loop(monkeypatch):
    _install(monkeypatch, await _context_engine({"market_cap": Decimal("1.5")}))
    trade_id, _ = _seed()
    with SessionLocal() as session:
        position_before = session.scalar(select(Position).where(Position.trade_id == trade_id))
        pnl_before = position_before.realized_pnl

    assert await OutcomeRecorder(EventBus(), SessionLocal).record_trade(trade_id) == "recorded"

    status, _, rows = _result(trade_id)
    assert status == "recorded" and len(rows) == 1
    row = rows[0]
    assert row.context_at_exit is None and row.market_state_at_exit["regime"] == "trend"
    assert row.snapshot_missing_reasons["context_at_exit"] == "snapshot_capture_error"
    with SessionLocal() as session:
        assert session.scalar(select(Position).where(Position.trade_id == trade_id)).realized_pnl == pnl_before


@requires_db
async def test_unsupported_market_state_value_keeps_context_at_exit(fundamentals_row, monkeypatch):
    _install(monkeypatch, await _context_engine(await _real_fundamentals_payload()),
             market_state={"regime": "trend", "naive": datetime(2026, 10, 7, 12, 0)})
    trade_id, _ = _seed()
    assert await OutcomeRecorder(EventBus(), SessionLocal).record_trade(trade_id) == "recorded"
    row = _result(trade_id)[2][0]
    assert row.market_state_at_exit is None
    assert row.snapshot_missing_reasons["market_state_at_exit"] == "snapshot_capture_error"
    assert row.context_at_exit["fundamentals"]["profile_updated_at"] == PROFILE_AT_Z


@requires_db
async def test_missing_reason_vocabulary_is_unchanged_for_cold_and_late_captures(monkeypatch):
    # Cold engines: both halves absent -> the pre-existing cold-start reason, not a capture error.
    _install(monkeypatch, ContextEngine(EventBus(), providers=[], symbol_providers=[]), market_state=None)
    state_snapshot.get_market_state_engine().symbol_state = None
    recorder = OutcomeRecorder(EventBus(), SessionLocal)  # started before the fill, so not a restart
    trade_id, order_id = _seed_first_entry()
    await recorder.capture_entry(order_id)
    with SessionLocal() as session:
        reasons = session.get(Trade, trade_id).entry_snapshot_missing_reasons
    assert reasons == {"market_state_at_entry": "engine_cold_start", "context_at_entry": "engine_cold_start"}
    # Late exit: still recorder_unavailable, capture never attempted.
    late_trade, _ = _seed()
    assert await OutcomeRecorder(EventBus(), SessionLocal, snapshot_max_lag_seconds=1).record_trade(late_trade) == "recorded"
    late_row = _result(late_trade)[2][0]
    assert late_row.snapshot_missing_reasons["context_at_exit"] == "recorder_unavailable"


# --- shared backtest writer ----------------------------------------------------------------


@requires_db
async def test_backtest_writer_persists_captured_snapshots(fundamentals_row, backtest_cleanup, monkeypatch):
    _install(monkeypatch, await _context_engine(await _real_fundamentals_payload()))
    snapshots = capture_strategy_outcome_snapshots("AAPL")  # the call the runner makes at entry and exit
    run_id, stamp = uuid4(), datetime(2026, 9, 10, 14, 35, tzinfo=timezone.utc)
    with SessionLocal.begin() as session:
        session.add(BacktestRunRecord(
            run_id=run_id, sweep_id=uuid4(), strategy_name=BT_NAME, strategy_version="v1", config_hash="h",
            symbol_universe=["AAPL"], date_range_start=date(2026, 9, 10), date_range_end=date(2026, 9, 10),
            data_version="d", feature_version="f", walk_forward_fold=None, is_holdout=False))
    outcome = StrategyOutcome(
        outcome_id=uuid4(), opportunity_id=uuid4(), schema_version=1, strategy_name=BT_NAME,
        strategy_version="v1", symbol="AAPL", origin="auto", is_backtest=True, backtest_run_id=run_id,
        trading_day=date(2026, 9, 10), setup_detected_at=stamp, signal_confirmed_at=stamp, decided_at=stamp,
        entry_filled_at=stamp, exit_filled_at=stamp + timedelta(minutes=5), holding_seconds=300, direction="BUY",
        entry_price=100.0, entry_qty=1.0, exit_price=101.0, exit_qty=1.0, commission_total=None,
        slippage_entry=0.0, realized_pnl=1.0, realized_r=1.0, exit_reason="target", structural_invalidation=99.0,
        structural_target=101.0, final_stop=99.0, final_target=101.0, confidence_at_signal=0.7,
        evidence={"basis": "backtest"},
        market_state_at_entry=snapshots.market_state, context_at_entry=snapshots.context,
        market_state_at_exit=snapshots.market_state, context_at_exit=snapshots.context, feature_snapshot_id=None)

    record_strategy_outcome(outcome)

    with SessionLocal() as session:
        row = session.get(StrategyOutcomeRecord, outcome.outcome_id)
        assert row.context_at_entry["fundamentals"]["profile_updated_at"] == PROFILE_AT_Z
        assert row.context_at_exit["fundamentals"]["next_earnings_date"] == "2026-10-28"


# --- helpers that call the recorder's real capture/persist functions --------------------------


def _set_status(trade_id, status):
    with SessionLocal.begin() as session:
        session.execute(text("UPDATE trades SET status = :s WHERE trade_id = :t"), {"s": status, "t": trade_id})


def module_capture(suffix):
    from app.trading_intelligence import outcome_recorder

    return outcome_recorder._capture("AAPL", suffix)


async def _store_entry(recorder, trade_id, market, context, reasons):
    import asyncio

    await asyncio.to_thread(recorder._store_entry, trade_id, market, context, reasons)
