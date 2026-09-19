"""World View v1 integration and route tests (decision #150).

The source-backed tests use Market State's real ``FeaturesUpdated`` event
path, Context's public evaluation methods, and Performance Intelligence's
real ``record_strategy_outcome()`` write path against PostgreSQL.  No private
snapshot dictionary is mutated and no outcome is inserted with raw SQL.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import inspect, text

import app.context_engine.engine as context_engine_module
import app.market_state_engine.engine as market_state_engine_module
from app.context_engine.engine import ContextEngine
from app.context_engine.provider import ContextProvider, SymbolContextProvider
from app.db.session import SessionLocal, engine as db_engine
from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.main import app
from app.market_state_engine.engine import MarketStateEngine
from app.schemas.events.envelope import EventType
from app.schemas.events.features import FeatureSet
from app.schemas.performance import StrategyOutcome
from app.trading_intelligence.performance import record_strategy_outcome
from app.world_view import WorldView

_STRATEGY_NAME = "__TEST_WORLD_VIEW_V1__"
_SYMBOL = "TESTWORLDVIEW"
_CANDLE_TS = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)


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
        # The existing literal-empty performance tests use the same whole-table
        # cleanup: there is still no live Execution/Position writer, and proving
        # a genuinely empty system-wide aggregate requires a genuinely empty
        # table.  World View itself never writes here.
        session.execute(text("DELETE FROM strategy_outcomes"))
        session.execute(
            text(
                "DELETE FROM market_state_history WHERE symbol_id IN "
                "(SELECT id FROM symbols WHERE ticker = :ticker)"
            ),
            {"ticker": _SYMBOL},
        )
        session.execute(text("DELETE FROM symbols WHERE ticker = :ticker"), {"ticker": _SYMBOL})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _cleanup_world_view_rows():
    _clean_test_rows()
    yield
    _clean_test_rows()


class _GlobalProvider(ContextProvider):
    name = "calendar"

    async def evaluate(self) -> dict:
        return {"session": "open", "is_market_open": True}


class _SymbolProvider(SymbolContextProvider):
    name = "news"

    async def evaluate(self, symbol: str) -> dict:
        return {"present": symbol == _SYMBOL, "count_15m": 1 if symbol == _SYMBOL else 0}


def _install_source_singletons(market: MarketStateEngine, context: ContextEngine) -> None:
    # Same tested singleton seam used by test_strategy_integration_contract.py;
    # source state is still produced only through each engine's public path.
    market_state_engine_module._market_state_engine = market
    context_engine_module._context_engine = context


async def _publish_market_features(bus: EventBus, symbol: str = _SYMBOL) -> None:
    features = FeatureSet(
        timeframe="1m",
        candle_ts=_CANDLE_TS,
        close=100.0,
        features={"sma_20_slope_angle": 12.0},
    )
    await bus.publish(make_envelope(EventType.FEATURES_UPDATED, features, symbol=symbol))


async def _wait_for_market_state(engine: MarketStateEngine, symbol: str, timeout: float = 8.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if symbol in engine.get_snapshot(symbol)["symbols"]:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"Market State did not compute {symbol} within {timeout}s")


def _outcome(*, is_backtest: bool, entry_hour_utc: int, realized_r: float, session_type: str) -> StrategyOutcome:
    entered = datetime(2026, 9, 10, entry_hour_utc, 0, tzinfo=timezone.utc)
    return StrategyOutcome(
        outcome_id=uuid.uuid4(),
        opportunity_id=uuid.uuid4(),
        schema_version=1,
        strategy_name=_STRATEGY_NAME,
        strategy_version="v1",
        symbol=_SYMBOL,
        origin="auto",
        is_backtest=is_backtest,
        backtest_run_id=None,
        trading_day=entered.date(),
        setup_detected_at=entered,
        signal_confirmed_at=entered,
        decided_at=entered,
        entry_filled_at=entered,
        exit_filled_at=entered,
        holding_seconds=60,
        direction="BUY",
        entry_price=100.0,
        entry_qty=1,
        exit_price=100.0 + realized_r,
        exit_qty=1,
        commission_total=0.0,
        slippage_entry=0.0,
        realized_pnl=realized_r,
        realized_r=realized_r,
        exit_reason="target",
        structural_invalidation=99.0,
        structural_target=102.0,
        final_stop=99.0,
        final_target=102.0,
        confidence_at_signal=0.5,
        evidence={},
        market_state_at_entry={},
        context_at_entry={"calendar": {"session": session_type}},
        market_state_at_exit={},
        context_at_exit={},
        feature_snapshot_id=None,
    )


def _all_table_counts() -> dict[str, int]:
    """Exact before/after row counts for every public application table."""
    table_names = inspect(db_engine).get_table_names(schema="public")
    quote = db_engine.dialect.identifier_preparer.quote
    session = SessionLocal()
    try:
        return {
            table_name: int(session.execute(text(f"SELECT COUNT(*) FROM {quote(table_name)}")).scalar_one())
            for table_name in table_names
        }
    finally:
        session.close()


@pytest.mark.asyncio
async def test_snapshot_preserves_real_source_envelopes_separates_populations_and_writes_nothing():
    bus = EventBus()
    await bus.start()
    market = MarketStateEngine(bus)
    context = ContextEngine(bus, providers=[_GlobalProvider()], symbol_providers=[_SymbolProvider()])
    _install_source_singletons(market, context)
    market.start()
    market_stopped = False
    try:
        await _publish_market_features(bus)
        await context.evaluate_all()
        await context.evaluate_for_symbol(_SYMBOL)
        await _wait_for_market_state(market, _SYMBOL)
        await market.stop()  # no source-owned persistence remains in flight
        market_stopped = True

        record_strategy_outcome(
            _outcome(is_backtest=False, entry_hour_utc=14, realized_r=2.0, session_type="open")
        )
        record_strategy_outcome(
            _outcome(is_backtest=True, entry_hour_utc=19, realized_r=-1.0, session_type="power_hour")
        )

        expected_market = market.get_snapshot(_SYMBOL)
        expected_context = context.get_snapshot(_SYMBOL)
        before = _all_table_counts()
        result = await WorldView().snapshot(_SYMBOL)
        after = _all_table_counts()

        assert result.symbol == _SYMBOL
        assert result.market_state == expected_market
        assert result.context == expected_context
        assert result.portfolio is None  # unavailable source, never an empty-portfolio fabrication
        assert result.performance == {
            "live": {
                "hourly_win_rates": [{"hour_et": 10, "total_trades": 1, "win_count": 1, "win_rate": 1.0}],
                "session_expectancy": [{"session_type": "open", "trade_count": 1, "expectancy_r": 2.0}],
            },
            "backtest": {
                "hourly_win_rates": [{"hour_et": 15, "total_trades": 1, "win_count": 0, "win_rate": 0.0}],
                "session_expectancy": [
                    {"session_type": "power_hour", "trade_count": 1, "expectancy_r": -1.0}
                ],
            },
        }
        assert after == before  # World View creates no persistent rows of its own
    finally:
        if not market_stopped:
            await market.stop()
        await bus.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("present_population", ["live", "backtest"])
async def test_snapshot_keeps_the_other_performance_population_honestly_empty(present_population: str):
    bus = EventBus()
    market = MarketStateEngine(bus)
    context = ContextEngine(bus, providers=[], symbol_providers=[])
    _install_source_singletons(market, context)
    record_strategy_outcome(
        _outcome(
            is_backtest=present_population == "backtest",
            entry_hour_utc=14,
            realized_r=1.0,
            session_type="open",
        )
    )

    result = await WorldView().snapshot("NEVER_COMPUTED")
    empty_population = "backtest" if present_population == "live" else "live"

    assert result.market_state == {"symbols": {}, "market": None}
    assert result.context == {"global": {"providers": {}, "evaluated_at": None}, "symbols": {}}
    assert result.performance[empty_population] == {"hourly_win_rates": [], "session_expectancy": []}
    assert result.portfolio is None


@pytest.mark.asyncio
async def test_world_view_route_with_and_without_symbol_serializes_the_contract():
    bus = EventBus()
    await bus.start()
    market = MarketStateEngine(bus)
    context = ContextEngine(bus, providers=[_GlobalProvider()], symbol_providers=[_SymbolProvider()])
    _install_source_singletons(market, context)
    try:
        await context.evaluate_all()
        await context.evaluate_for_symbol(_SYMBOL)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            all_response = await client.get("/intelligence/world-view")
            missing_response = await client.get(
                "/intelligence/world-view", params={"symbol": "NEVER_COMPUTED"}
            )

        assert all_response.status_code == 200
        all_body = all_response.json()
        assert all_body["symbol"] is None
        assert _SYMBOL in all_body["context"]["symbols"]
        assert all_body["portfolio"] is None
        assert all_body["performance"] == {
            "live": {"hourly_win_rates": [], "session_expectancy": []},
            "backtest": {"hourly_win_rates": [], "session_expectancy": []},
        }

        assert missing_response.status_code == 200
        missing_body = missing_response.json()
        assert missing_body["symbol"] == "NEVER_COMPUTED"
        assert missing_body["market_state"] == {"symbols": {}, "market": None}
        assert missing_body["context"]["symbols"] == {}
        assert missing_body["context"]["global"] == all_body["context"]["global"]
        assert missing_body["portfolio"] is None
        # FastAPI's normal encoder serialized the frozen dataclass directly.
        assert set(missing_body) == {"symbol", "market_state", "context", "performance", "portfolio"}
    finally:
        await bus.stop()
