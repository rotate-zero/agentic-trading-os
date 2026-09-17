from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

import app.api.routes.backtest as backtest_route
from app.api.routes import broker
from app.backtest_runner.ibkr_historical import (
    IBKRHistoricalPermissionError,
    IBKRReplayDataset,
    PreloadedHistoricalCandleProvider,
)
from app.broker_adapters.base import Candle
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app

SYMBOL = "ZIBKR1"


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


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="Postgres not reachable at the configured DATABASE settings",
)


def _clean_symbol() -> None:
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM strategy_outcomes WHERE symbol = :t"), {"t": SYMBOL})
        session.execute(text("DELETE FROM backtests WHERE :t = ANY(symbol_universe)"), {"t": SYMBOL})
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
                text(f"DELETE FROM {table} WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"),
                {"t": SYMBOL},
            )
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": SYMBOL})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_and_configure(monkeypatch):
    _clean_symbol()
    monkeypatch.setenv("IBKR_BACKTEST_CLIENT_ID", "77")
    get_settings.cache_clear()
    yield
    _clean_symbol()
    get_settings.cache_clear()


def _params(start: str = "2026-01-05T14:30:00Z", end: str = "2026-01-05T14:33:00Z"):
    return {
        "strategy_name": "ORB",
        "symbol": SYMBOL,
        "start": start,
        "end": end,
    }


def _run_count() -> int:
    session = SessionLocal()
    try:
        return session.execute(
            text("SELECT count(*) FROM backtests WHERE :t = ANY(symbol_universe)"),
            {"t": SYMBOL},
        ).scalar_one()
    finally:
        session.close()


@pytest.mark.parametrize("value", ["", "not-an-int", "-1", "1"])
def test_route_rejects_missing_invalid_or_colliding_historical_client_id(monkeypatch, value):
    monkeypatch.setenv("IBKR_BACKTEST_CLIENT_ID", value)
    get_settings.cache_clear()
    with TestClient(app) as client:
        response = client.post("/backtest/run/ibkr", params=_params())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ibkr_backtest_not_configured"


@pytest.mark.parametrize(
    "params",
    [
        _params(start="2026-01-05T14:30:00", end="2026-01-05T15:30:00Z"),
        _params(start="2026-01-05T15:30:00Z", end="2026-01-05T14:30:00Z"),
        _params(start="2026-01-05T14:30:00Z", end="2026-01-06T14:30:01Z"),
        {**_params(), "symbol": "   "},
    ],
)
def test_route_rejects_invalid_time_and_symbol_inputs(params):
    with TestClient(app) as client:
        response = client.post("/backtest/run/ibkr", params=params)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_backtest_request"


def test_exactly_24_hours_is_allowed_without_clamping(monkeypatch):
    seen = {}

    async def fail_after_capture(**kwargs):
        seen.update(kwargs)
        raise IBKRHistoricalPermissionError("stop after validation")

    monkeypatch.setattr(backtest_route, "acquire_ibkr_replay_data", fail_after_capture)
    params = _params(
        start="2026-01-05T14:30:00Z",
        end="2026-01-06T14:30:00Z",
    )
    with TestClient(app) as client:
        response = client.post("/backtest/run/ibkr", params=params)

    assert response.status_code == 503
    assert seen["start"] == datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    assert seen["end"] == datetime(2026, 1, 6, 14, 30, tzinfo=timezone.utc)


def test_acquisition_failure_maps_stably_and_writes_no_backtest_row(monkeypatch):
    async def fail_acquisition(**_kwargs):
        raise IBKRHistoricalPermissionError("subscription required")

    monkeypatch.setattr(backtest_route, "acquire_ibkr_replay_data", fail_acquisition)
    assert _run_count() == 0
    with TestClient(app) as client:
        response = client.post("/backtest/run/ibkr", params=_params())

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "ibkr_historical_permission_denied",
        "message": "subscription required",
    }
    assert _run_count() == 0


def test_successful_route_uses_preloaded_real_provider_and_persists_run(monkeypatch):
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    minute_candles = [
        Candle(
            timeframe="1m",
            open=100 + i,
            high=100.5 + i,
            low=99.5 + i,
            close=100.25 + i,
            volume=1000 + i,
            candle_ts=start + timedelta(minutes=i),
        )
        for i in range(2)
    ]
    daily_candles = [
        Candle(
            timeframe="1d",
            open=95,
            high=101,
            low=94,
            close=100,
            volume=1_000_000,
            candle_ts=datetime.combine(
                date(2025, 12, 1) + timedelta(days=i),
                datetime.min.time(),
                tzinfo=timezone.utc,
            ),
        )
        for i in range(20)
    ]
    provider = PreloadedHistoricalCandleProvider(
        {(SYMBOL, "1m"): minute_candles, (SYMBOL, "1d"): daily_candles}
    )

    async def acquire(**kwargs):
        assert kwargs["client_id"] == 77
        assert kwargs["daily_lookback_days"] == get_settings().daily_levels_lookback_days
        assert kwargs["premarket_lookback_days"] == get_settings().feature_engine_premarket_lookback_days
        return IBKRReplayDataset(
            provider=provider,
            data_version="ibkr:TRADES:1m-ext:1d-rth",
            acquired_at=datetime.now(timezone.utc),
            contract_con_id=12345,
        )

    monkeypatch.setattr(backtest_route, "acquire_ibkr_replay_data", acquire)
    with TestClient(app) as client:
        response = client.post("/backtest/run/ibkr", params=_params())

    assert response.status_code == 200
    assert response.json()["outcomes_recorded"] == 0
    assert _run_count() == 1
    session = SessionLocal()
    try:
        data_version = session.execute(
            text("SELECT data_version FROM backtests WHERE :t = ANY(symbol_universe)"),
            {"t": SYMBOL},
        ).scalar_one()
    finally:
        session.close()
    assert data_version == "ibkr:TRADES:1m-ext:1d-rth"


def test_existing_fixture_route_rejects_connected_ibkr(monkeypatch):
    monkeypatch.setattr(broker, "is_connected", lambda: True)
    with TestClient(app) as client:
        response = client.post(
            "/backtest/run",
            params={
                "strategy_name": "ORB",
                "symbol": SYMBOL,
                "scenario": "volume_gated_baseline",
            },
        )
    assert response.status_code == 409
    assert "IBKR" in response.json()["detail"]
    assert _run_count() == 0
