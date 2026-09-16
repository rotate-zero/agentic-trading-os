"""D18 namespace regressions against real PostgreSQL.

Every production module that resolves or joins ``symbols.ticker`` gets a
same-ticker live/backtest collision here.  The assertion is deliberately
about which row is returned, not merely whether the old live-only case still
works.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select, text

from app.context_engine.engine import ContextEngine
from app.context_engine.fundamentals_refresh import FundamentalsRefreshJobs
from app.context_engine.providers.fundamentals import FundamentalsProvider
from app.db.session import SessionLocal
from app.event_bus.bus import EventBus
from app.feature_engine.engine import FeatureEngine
from app.market_state_engine.engine import MarketStateEngine
from app.models.daily_levels import DailyLevelState
from app.models.market_data import Candle, Symbol
from app.models.scanner import ScannerUniverseSymbol
from app.models.symbol_fundamentals import SymbolFundamentals
from app.models.trading_intelligence import LevelInteractionState
from app.scanner.universe import DbUniverseProvider, list_universe_symbols, remove_symbol_from_universe
from app.services.candle_recorder import CandleRecorder
from app.services.candle_store import get_latest_recorded_candle, get_recent_closes, get_recorded_candles
from app.trading_intelligence.level_interaction_engine import LevelInteractionEngine


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


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at configured DATABASE settings")


def _clean(ticker: str) -> None:
    session = SessionLocal()
    try:
        for table in (
            "level_interaction_events",
            "level_interaction_state",
            "daily_levels_state",
            "market_state_history",
            "symbol_fundamentals",
            "scanner_universe_symbols",
            "candles",
        ):
            session.execute(
                text(f"DELETE FROM {table} WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :ticker)"),
                {"ticker": ticker},
            )
        session.execute(text("DELETE FROM symbols WHERE ticker = :ticker"), {"ticker": ticker})
        session.commit()
    finally:
        session.close()


def _pair(session, ticker: str) -> tuple[Symbol, Symbol]:
    live = Symbol(ticker=ticker, is_backtest=False)
    backtest = Symbol(ticker=ticker, is_backtest=True)
    session.add_all([live, backtest])
    session.flush()
    return live, backtest


def test_feature_engine_reads_and_writes_its_selected_namespace_only():
    ticker = "ZZNSFE"
    _clean(ticker)
    session = SessionLocal()
    try:
        live, backtest = _pair(session, ticker)
        for symbol, is_backtest, price in ((live, False, 101.0), (backtest, True, 202.0)):
            session.add(
                DailyLevelState(
                    symbol_id=symbol.id,
                    is_backtest=is_backtest,
                    level_id=f"{ticker}-DL-{symbol.id}",
                    price=price,
                    strength=2,
                    distinct_candle_count=2,
                    status="active",
                    first_seen_day=date(2026, 8, 12),
                    last_confirmed_day=date(2026, 8, 12),
                )
            )
        session.commit()

        live_engine = FeatureEngine(EventBus())
        backtest_engine = FeatureEngine(EventBus(), is_backtest=True)
        assert live_engine._get_or_create_symbol_id(session, ticker) == live.id
        assert backtest_engine._get_or_create_symbol_id(session, ticker) == backtest.id
        assert [level.price for level in live_engine._load_confirmed_daily_levels_for_today(ticker, date(2026, 8, 12))] == [101.0]
        assert [level.price for level in backtest_engine._load_confirmed_daily_levels_for_today(ticker, date(2026, 8, 12))] == [202.0]
    finally:
        session.close()
        _clean(ticker)


def test_market_state_engine_resolves_live_and_backtest_symbol_rows_separately():
    ticker = "ZZNSMS"
    _clean(ticker)
    session = SessionLocal()
    try:
        live, backtest = _pair(session, ticker)
        session.commit()
        assert MarketStateEngine(EventBus())._get_or_create_symbol_id(session, ticker) == live.id
        assert MarketStateEngine(EventBus(), is_backtest=True)._get_or_create_symbol_id(session, ticker) == backtest.id
    finally:
        session.close()
        _clean(ticker)


def test_level_interaction_engine_loads_only_its_selected_namespace():
    ticker = "ZZNSLI"
    ts = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
    _clean(ticker)
    session = SessionLocal()
    try:
        live, backtest = _pair(session, ticker)
        session.add_all(
            [
                LevelInteractionState(
                    symbol_id=live.id,
                    timeframe="1m",
                    level_key="vwap",
                    trading_day=date(2026, 8, 12),
                    touch_count_today=1,
                    zone="below",
                    zone_entered_ts=ts,
                ),
                LevelInteractionState(
                    symbol_id=backtest.id,
                    timeframe="1m",
                    level_key="vwap",
                    trading_day=date(2026, 8, 12),
                    touch_count_today=2,
                    zone="above",
                    zone_entered_ts=ts,
                ),
            ]
        )
        session.commit()
        live_engine = LevelInteractionEngine(EventBus())
        backtest_engine = LevelInteractionEngine(EventBus(), is_backtest=True)
        assert live_engine._get_or_create_symbol_id(session, ticker) == live.id
        assert backtest_engine._get_or_create_symbol_id(session, ticker) == backtest.id
        assert live_engine._load_state_from_db(ticker, "1m", "vwap").zone == "below"
        assert backtest_engine._load_state_from_db(ticker, "1m", "vwap").zone == "above"
    finally:
        session.close()
        _clean(ticker)


def test_scanner_universe_lists_and_removes_only_the_live_namespace():
    ticker = "ZZNSU"
    _clean(ticker)
    session = SessionLocal()
    try:
        live, backtest = _pair(session, ticker)
        session.add_all([ScannerUniverseSymbol(symbol_id=live.id), ScannerUniverseSymbol(symbol_id=backtest.id)])
        session.commit()
        backtest_id = backtest.id
    finally:
        session.close()
    try:
        assert DbUniverseProvider(SessionLocal).get_core_universe().count(ticker) == 1
        assert [row["symbol"] for row in list_universe_symbols(SessionLocal)].count(ticker) == 1
        assert remove_symbol_from_universe(SessionLocal, ticker) is True
        session = SessionLocal()
        try:
            remaining_ids = set(
                session.execute(
                    select(ScannerUniverseSymbol.symbol_id).join(Symbol, Symbol.id == ScannerUniverseSymbol.symbol_id).where(Symbol.ticker == ticker)
                ).scalars()
            )
            assert remaining_ids == {backtest_id}
        finally:
            session.close()
    finally:
        _clean(ticker)


def test_candle_recorder_get_or_create_uses_live_namespace():
    ticker = "ZZNSCR"
    _clean(ticker)
    session = SessionLocal()
    try:
        backtest = Symbol(ticker=ticker, is_backtest=True)
        session.add(backtest)
        session.commit()
        symbol_id = CandleRecorder(EventBus())._get_or_create_symbol_id(session, ticker)
        selected = session.get(Symbol, symbol_id)
        assert selected is not None and selected.is_backtest is False
        assert {row.is_backtest for row in session.execute(select(Symbol).where(Symbol.ticker == ticker)).scalars()} == {
            False,
            True,
        }
    finally:
        session.close()
        _clean(ticker)


def test_candle_store_reads_only_live_candles_for_colliding_ticker():
    ticker = "ZZNSCS"
    ts = datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc)
    _clean(ticker)
    session = SessionLocal()
    try:
        live, backtest = _pair(session, ticker)
        session.add_all(
            [
                Candle(symbol_id=live.id, timeframe="1m", candle_ts=ts, open=10, high=11, low=9, close=10, volume=100),
                Candle(symbol_id=backtest.id, timeframe="1m", candle_ts=ts, open=20, high=21, low=19, close=20, volume=200),
            ]
        )
        session.commit()
        assert [c.close for c in get_recorded_candles(ticker, "1m", ts, ts)] == [10.0]
        assert get_latest_recorded_candle(ticker, "1m").close == 10.0
        assert get_recent_closes(ticker, "1m", ts, 5) == [10.0]
    finally:
        session.close()
        _clean(ticker)


def test_context_engine_universe_bootstrap_reads_only_live_symbols():
    ticker = "ZZNSCE"
    _clean(ticker)
    session = SessionLocal()
    try:
        live, backtest = _pair(session, ticker)
        session.add_all([ScannerUniverseSymbol(symbol_id=live.id), ScannerUniverseSymbol(symbol_id=backtest.id)])
        session.commit()
        engine = ContextEngine(EventBus(), providers=[], symbol_providers=[])
        assert engine._load_scanner_universe_symbols().count(ticker) == 1
    finally:
        session.close()
        _clean(ticker)


def test_fundamentals_refresh_universe_and_upsert_resolve_live_namespace():
    ticker = "ZZNSFR"
    _clean(ticker)
    session = SessionLocal()
    try:
        live, backtest = _pair(session, ticker)
        session.add_all([ScannerUniverseSymbol(symbol_id=live.id), ScannerUniverseSymbol(symbol_id=backtest.id)])
        session.commit()
        jobs = FundamentalsRefreshJobs(api_key="unused")
        assert jobs._load_universe_symbols().count(ticker) == 1
        assert jobs._get_or_create_symbol_id(session, ticker) == live.id
    finally:
        session.close()
        _clean(ticker)


def test_fundamentals_provider_reads_only_live_row():
    ticker = "ZZNSFP"
    _clean(ticker)
    session = SessionLocal()
    try:
        live, backtest = _pair(session, ticker)
        session.add_all(
            [
                SymbolFundamentals(symbol_id=live.id, industry="Live industry", data_source="test"),
                SymbolFundamentals(symbol_id=backtest.id, industry="Backtest industry", data_source="test"),
            ]
        )
        session.commit()
        assert FundamentalsProvider()._read(ticker)["industry"] == "Live industry"
    finally:
        session.close()
        _clean(ticker)
