"""Real-PostgreSQL tests for ``POST /backtest/run/stored`` (task
``stored-candle-backtest``) and ``backtest_runner/stored_history.py``.

Everything here uses the real database, the real route, the real
``BacktestRunner`` and the real engine pipeline. Only the connected-provider
guard and the database-failure case use doubles. Data is synthetic: these
tests prove plumbing, isolation and look-ahead safety, never strategy
profitability.
"""
from __future__ import annotations

import asyncio
import threading
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from starlette.testclient import TestClient

import app.api.routes.backtest as backtest_route
import app.backtest_runner.stored_history as stored_history
import app.backtest_runner.stored_coverage as stored_coverage
from app.api.routes import broker, finnhub_data, market_data
from app.backtest_runner.scenarios import load_scenario_candles
from app.backtest_runner.stored_history import (
    STORED_DATA_VERSION,
    StoredHistoryMalformedError,
    StoredHistoryNoDataError,
    StoredHistoryUnavailableError,
    read_stored_replay_data,
)
from app.core.config import get_settings
from app.db.partitions import ensure_month_partition
from app.db.session import SessionLocal
from app.main import app
from app.services import broker_registry

SYMBOL = "ZSTORE1"
FIXTURE_LABEL = "ZSTORE1F"
_TICKERS = (SYMBOL, FIXTURE_LABEL)

T0 = datetime(2026, 2, 2, 14, 30, tzinfo=timezone.utc)  # Monday 09:30 ET


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


def _clean() -> None:
    session = SessionLocal()
    try:
        for ticker in _TICKERS:
            session.execute(text("DELETE FROM strategy_outcomes WHERE symbol = :t"), {"t": ticker})
            session.execute(text("DELETE FROM backtests WHERE :t = ANY(symbol_universe)"), {"t": ticker})
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
                    {"t": ticker},
                )
            session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _isolated():
    _clean()
    yield
    _clean()
    get_settings.cache_clear()


# --- seeding helpers --------------------------------------------------------


def _bar(ts: datetime, price: float = 100.0, volume: int = 1000):
    return (ts, price, price + 0.5, price - 0.5, price + 0.1, volume)


def _seed(ticker: str, rows, *, timeframe: str = "1m", is_backtest: bool = False) -> int:
    """Insert candles for (ticker, namespace); returns the symbol id."""
    session = SessionLocal()
    try:
        for d in {row[0].date() for row in rows}:
            ensure_month_partition(session, d)
        symbol_id = session.execute(
            text(
                "INSERT INTO symbols (ticker, is_backtest) VALUES (:t, :b) "
                "ON CONFLICT (ticker, is_backtest) DO UPDATE SET ticker = EXCLUDED.ticker RETURNING id"
            ),
            {"t": ticker, "b": is_backtest},
        ).scalar_one()
        for ts, o, h, l, c, v in rows:
            session.execute(
                text(
                    "INSERT INTO candles (symbol_id, timeframe, candle_ts, open, high, low, close, volume) "
                    "VALUES (:s, :tf, :ts, :o, :h, :l, :c, :v)"
                ),
                {"s": symbol_id, "tf": timeframe, "ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v},
            )
        session.commit()
        return symbol_id
    finally:
        session.close()


def _minutes(start: datetime, count: int, price: float = 100.0):
    return [_bar(start + timedelta(minutes=i), price + i * 0.01) for i in range(count)]


def _read(start=T0, end=T0 + timedelta(minutes=3), *, daily_days=30, premarket_days=2, symbol=SYMBOL):
    return read_stored_replay_data(
        symbol=symbol,
        start=start,
        end=end,
        daily_lookback_days=daily_days,
        premarket_lookback_days=premarket_days,
    )


def _provider_1m(dataset, start, end):
    return asyncio.run(dataset.provider.get_historical(SYMBOL, "1m", start, end))


def _params(start="2026-02-02T14:30:00Z", end="2026-02-02T14:33:00Z", **overrides):
    return {"strategy_name": "VWAP", "symbol": SYMBOL, "start": start, "end": end, **overrides}


def _coverage_params(start="2026-02-02T14:30:00Z", end="2026-02-02T14:33:00Z", **overrides):
    return {"symbol": SYMBOL, "start": start, "end": end, **overrides}


def _coverage(**params):
    # This read-only route needs no lifespan; repeated startup/shutdown in
    # one test would start unrelated singleton workers on different loops.
    return TestClient(app).get("/backtest/stored-coverage", params=_coverage_params(**params))


def test_stored_coverage_namespace_bounds_and_read_only_snapshot(monkeypatch):
    live_id = _seed(SYMBOL, [_bar(T0 - timedelta(minutes=1)), *_minutes(T0, 3), _bar(T0 + timedelta(minutes=3))])
    _seed(SYMBOL, _minutes(T0 - timedelta(minutes=2), 7, price=999), is_backtest=True)
    before = _source_snapshot(live_id)
    real_session_local = stored_coverage.SessionLocal
    observed = []

    def tracking_factory():
        session = real_session_local()
        original_execute = session.execute

        def execute(*args, **kwargs):
            observed.append((session.execute.__name__, session.connection().exec_driver_sql("SHOW transaction_read_only").scalar_one(),
                             session.connection().exec_driver_sql("SHOW transaction_isolation").scalar_one()))
            return original_execute(*args, **kwargs)

        session.execute = execute
        return session

    monkeypatch.setattr(stored_coverage, "SessionLocal", tracking_factory)
    response = _coverage(symbol=f"  {SYMBOL.lower()}  ")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == SYMBOL
    assert body["recorded_count"] == 5
    assert body["recorded_first"] == "2026-02-02T14:29:00Z"
    assert body["recorded_last"] == "2026-02-02T14:33:00Z"
    assert body["requested_count"] == 3
    assert body["requested_first"] == "2026-02-02T14:30:00Z"
    assert body["requested_last"] == "2026-02-02T14:32:00Z"
    assert body["warmup_minute_count"] == 1
    assert body["warmup_daily_count"] == 0
    assert observed and all(readonly == "on" and isolation == "repeatable read" for _, readonly, isolation in observed)
    assert _source_snapshot(live_id) == before
    assert _run_rows() == []
    _clean()
    _seed(SYMBOL, _minutes(T0, 2), is_backtest=True)
    decoy_only = _coverage()
    assert decoy_only.status_code == 200
    assert decoy_only.json()["recorded_count"] == 0
    assert decoy_only.json()["requested_first"] is None


def test_stored_coverage_empty_unknown_and_warmup_selection(monkeypatch):
    monkeypatch.setenv("FEATURE_ENGINE_PREMARKET_LOOKBACK_DAYS", "2")
    get_settings.cache_clear()
    _seed(SYMBOL, [_bar(T0 - timedelta(days=6)), _bar(T0 - timedelta(days=6) - timedelta(minutes=1)),
                   _bar(T0 - timedelta(minutes=1)), _bar(T0), _bar(T0 + timedelta(minutes=3))])
    _seed(SYMBOL, [_bar(datetime(2026, 1, 30, 5, tzinfo=timezone.utc)),
                   _bar(datetime(2026, 2, 2, 5, tzinfo=timezone.utc)),
                   _bar(datetime(2026, 2, 3, 5, tzinfo=timezone.utc))], timeframe="1d")
    response = _coverage()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requested_count"] == 1
    assert body["warmup_minute_count"] == 2  # 6-day inclusive lookback, not the earlier row
    assert body["warmup_daily_count"] == 1  # prior trading day only
    unknown = _coverage(symbol="UNKNOWN")
    assert unknown.status_code == 200
    assert unknown.json()["recorded_count"] == unknown.json()["requested_count"] == 0
    assert unknown.json()["recorded_first"] is None
    assert unknown.json()["requested_last"] is None
    empty = _coverage(start="2026-02-04T14:30:00Z", end="2026-02-04T14:31:00Z")
    assert empty.status_code == 200
    assert empty.json()["recorded_count"] == 5
    assert empty.json()["requested_count"] == 0
    assert empty.json()["requested_first"] is None
    assert empty.json()["warmup_minute_count"] == 0
    assert empty.json()["warmup_daily_count"] == 0


@pytest.mark.parametrize("overrides", [
    {"symbol": "  "}, {"start": "not-a-date"}, {"end": "not-a-date"},
    {"start": "2026-02-02T14:30:00"}, {"end": "2026-02-02T14:33:00"},
    {"end": "2026-02-02T14:30:00Z"}, {"end": "2026-02-03T14:31:00Z"},
])
def test_stored_coverage_rejects_malformed_parameters_before_read(monkeypatch, overrides):
    def must_not_read(**kwargs):
        raise AssertionError("invalid request reached the database")

    monkeypatch.setattr(backtest_route, "acquire_stored_coverage", must_not_read)
    response = _coverage(**overrides)
    assert response.status_code == 422


def _run_rows() -> list[tuple]:
    session = SessionLocal()
    try:
        return list(
            session.execute(
                text(
                    "SELECT run_id, data_version, symbol_universe FROM backtests "
                    "WHERE :t = ANY(symbol_universe)"
                ),
                {"t": SYMBOL},
            ).all()
        )
    finally:
        session.close()


def _source_snapshot(symbol_id: int) -> list[tuple]:
    session = SessionLocal()
    try:
        return list(
            session.execute(
                text(
                    "SELECT id, symbol_id, timeframe, candle_ts, open, high, low, close, volume "
                    "FROM candles WHERE symbol_id = :s ORDER BY candle_ts, timeframe, id"
                ),
                {"s": symbol_id},
            ).all()
        )
    finally:
        session.close()


def _derived_counts(symbol_id: int) -> dict[str, int]:
    session = SessionLocal()
    try:
        return {
            table: session.execute(
                text(f"SELECT count(*) FROM {table} WHERE symbol_id = :s"), {"s": symbol_id}
            ).scalar_one()
            for table in ("level_interaction_state", "level_interaction_events", "daily_levels_state", "market_state_history")
        }
    finally:
        session.close()


def _outcomes(run_id: str) -> list[tuple]:
    session = SessionLocal()
    try:
        return list(
            session.execute(
                text(
                    "SELECT strategy_name, symbol, direction, entry_price, exit_price, realized_r, "
                    "exit_reason, entry_filled_at, exit_filled_at, is_backtest "
                    "FROM strategy_outcomes WHERE backtest_run_id = :r ORDER BY entry_filled_at"
                ),
                {"r": run_id},
            ).all()
        )
    finally:
        session.close()


# --- acquisition: interval boundaries, ordering, namespace -------------------


def test_exact_half_open_interval_and_ascending_order_regardless_of_insert_order():
    rows = _minutes(T0 - timedelta(minutes=2), 7)  # 14:28 .. 14:34
    _seed(SYMBOL, list(reversed(rows)))  # inserted newest-first on purpose

    end = T0 + timedelta(minutes=3)  # [14:30, 14:33)
    dataset = _read(T0, end)

    assert dataset.primary_candle_count == 3
    served = _provider_1m(dataset, T0, end)
    assert [c.candle_ts for c in served] == [T0, T0 + timedelta(minutes=1), T0 + timedelta(minutes=2)]
    assert dataset.warmup_minute_candle_count == 2  # 14:28 and 14:29 only; never 14:33/14:34
    everything = _provider_1m(dataset, T0 - timedelta(days=1), T0 + timedelta(days=1))
    assert [c.candle_ts for c in everything] == sorted(c.candle_ts for c in everything)
    assert all(c.candle_ts < end for c in everything)
    assert dataset.data_version == STORED_DATA_VERSION
    assert len(STORED_DATA_VERSION) <= 32


def test_interval_boundaries_are_start_inclusive_and_end_exclusive():
    _seed(SYMBOL, _minutes(T0, 3))
    only_start = _read(T0, T0 + timedelta(minutes=1))
    assert only_start.primary_candle_count == 1
    assert [c.candle_ts for c in _provider_1m(only_start, T0, T0 + timedelta(minutes=1))] == [T0]
    # A candle exactly at `end` is not part of the run.
    with pytest.raises(StoredHistoryNoDataError):
        _read(T0 + timedelta(minutes=3), T0 + timedelta(minutes=4))


def test_only_non_backtest_namespace_is_read():
    live_id = _seed(SYMBOL, _minutes(T0, 3, price=100.0))
    decoy_id = _seed(SYMBOL, _minutes(T0 - timedelta(minutes=5), 12, price=999.0), is_backtest=True)
    _seed(SYMBOL, [_bar(T0 - timedelta(days=1), 999.0)], timeframe="1d", is_backtest=True)
    assert live_id != decoy_id

    dataset = _read(T0, T0 + timedelta(minutes=3))
    served = _provider_1m(dataset, T0 - timedelta(days=1), T0 + timedelta(days=1))
    assert dataset.primary_candle_count == 3
    assert all(c.close < 200 for c in served)  # no 999-priced decoy row
    assert dataset.warmup_minute_candle_count == 0
    assert dataset.daily_candle_count == 0

    # Decoys alone are "no recorded data" for the live namespace.
    _clean()
    _seed(SYMBOL, _minutes(T0, 3, price=999.0), is_backtest=True)
    with pytest.raises(StoredHistoryNoDataError):
        _read(T0, T0 + timedelta(minutes=3))


# --- acquisition: lookbacks and no look-ahead -------------------------------


def test_configured_lookbacks_bound_warmup_and_nothing_at_or_after_end_is_read():
    end = T0 + timedelta(minutes=2)
    premarket_days, daily_days = 2, 10
    inside_1m = T0 - timedelta(days=premarket_days * 3) + timedelta(minutes=1)
    outside_1m = T0 - timedelta(days=premarket_days * 3) - timedelta(minutes=1)
    rows = _minutes(T0, 2) + [_bar(inside_1m), _bar(outside_1m)]
    rows += [_bar(end), _bar(end + timedelta(minutes=1), 5000.0)]  # at/after end
    _seed(SYMBOL, rows)

    daily_ok = datetime(2026, 1, 30, tzinfo=timezone.utc)  # prior trading day
    daily_old = T0 - timedelta(days=daily_days + 1)
    daily_same_day = datetime(2026, 2, 2, 5, 0, tzinfo=timezone.utc)  # replay day's own bar (not elapsed)
    daily_future = datetime(2026, 2, 3, 5, 0, tzinfo=timezone.utc)  # after end
    _seed(
        SYMBOL,
        [_bar(daily_ok, 90.0), _bar(daily_old, 90.0), _bar(daily_same_day, 777.0), _bar(daily_future, 888.0)],
        timeframe="1d",
    )

    dataset = _read(T0, end, daily_days=daily_days, premarket_days=premarket_days)
    wide = (T0 - timedelta(days=60), T0 + timedelta(days=60))
    minute_ts = {c.candle_ts for c in _provider_1m(dataset, *wide)}
    assert inside_1m in minute_ts
    assert outside_1m not in minute_ts
    assert end not in minute_ts and end + timedelta(minutes=1) not in minute_ts

    daily = asyncio.run(dataset.provider.get_historical(SYMBOL, "1d", *wide))
    assert [c.candle_ts for c in daily] == [daily_ok]  # old: lookback; same-day/future: look-ahead


def test_a_provider_call_never_returns_rows_at_or_after_the_requested_end():
    _seed(SYMBOL, _minutes(T0, 3))
    dataset = _read(T0, T0 + timedelta(minutes=3))
    cutoff = T0 + timedelta(minutes=1)  # what FeatureEngine passes for the 14:31 candle
    served = _provider_1m(dataset, T0 - timedelta(days=1), cutoff)
    assert [c.candle_ts for c in served] == [T0]


def test_missing_daily_history_is_served_empty_and_never_synthesized():
    _seed(SYMBOL, _minutes(T0, 3))
    dataset = _read()
    assert dataset.daily_candle_count == 0
    assert asyncio.run(dataset.provider.get_historical(SYMBOL, "1d", T0 - timedelta(days=30), T0)) == []


# --- acquisition: failures ---------------------------------------------------


def test_no_recorded_candles_in_interval_is_a_structured_error_even_with_warmup():
    _seed(SYMBOL, _minutes(T0 - timedelta(hours=2), 5))  # warm-up only, nothing inside [start, end)
    with pytest.raises(StoredHistoryNoDataError) as caught:
        _read()
    assert caught.value.code == "stored_candles_no_data"
    assert caught.value.http_status == 422
    assert SYMBOL in caught.value.message


def test_non_finite_stored_values_are_refused_not_repaired():
    symbol_id = _seed(SYMBOL, _minutes(T0, 3))
    session = SessionLocal()
    try:
        session.execute(
            text("UPDATE candles SET close = 'NaN'::numeric WHERE symbol_id = :s AND candle_ts = :ts"),
            {"s": symbol_id, "ts": T0 + timedelta(minutes=1)},
        )
        session.commit()
    finally:
        session.close()
    with pytest.raises(StoredHistoryMalformedError) as caught:
        _read()
    assert caught.value.code == "stored_candles_malformed"


def test_database_failure_is_unavailable_error(monkeypatch):
    class BrokenSession:
        closed = False

        def connection(self, **_kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        def close(self):
            BrokenSession.closed = True

    monkeypatch.setattr(stored_history, "SessionLocal", BrokenSession)
    with pytest.raises(StoredHistoryUnavailableError) as caught:
        _read()
    assert caught.value.http_status == 503
    assert "connection refused" not in caught.value.message  # no driver detail leaks
    assert BrokenSession.closed


def test_read_runs_in_a_worker_thread_with_its_own_closed_session(monkeypatch):
    _seed(SYMBOL, _minutes(T0, 3))
    created: list[tuple[int, object]] = []
    real_session_local = stored_history.SessionLocal

    def tracking_factory():
        session = real_session_local()
        created.append((threading.get_ident(), session))
        return session

    monkeypatch.setattr(stored_history, "SessionLocal", tracking_factory)
    main_thread = threading.get_ident()
    dataset = asyncio.run(
        stored_history.acquire_stored_replay_data(
            symbol=SYMBOL,
            start=T0,
            end=T0 + timedelta(minutes=3),
            daily_lookback_days=30,
            premarket_lookback_days=2,
        )
    )
    assert dataset.primary_candle_count == 3
    assert len(created) == 1
    thread_id, session = created[0]
    assert thread_id != main_thread
    assert not session.in_transaction()  # closed: nothing left open


# --- route: validation -------------------------------------------------------


def test_route_rejects_unknown_strategy():
    with TestClient(app) as client:
        response = client.post("/backtest/run/stored", params=_params(strategy_name="NOPE"))
    assert response.status_code == 400


@pytest.mark.parametrize(
    "params",
    [
        _params(start="2026-02-02T14:30:00", end="2026-02-02T15:30:00Z"),  # naive start
        _params(start="2026-02-02T14:30:00Z", end="2026-02-02T15:30:00"),  # naive end
        _params(start="2026-02-02T15:30:00Z", end="2026-02-02T14:30:00Z"),  # start after end
        _params(start="2026-02-02T14:30:00Z", end="2026-02-02T14:30:00Z"),  # empty interval
        _params(start="2026-02-02T14:30:00Z", end="2026-02-03T14:30:01Z"),  # > 24 h, not clamped
        {**_params(), "symbol": "   "},
    ],
)
def test_route_rejects_invalid_time_and_symbol_inputs_before_any_read(monkeypatch, params):
    async def must_not_read(**_kwargs):
        raise AssertionError("acquisition must not run for an invalid request")

    monkeypatch.setattr(backtest_route, "acquire_stored_replay_data", must_not_read)
    with TestClient(app) as client:
        response = client.post("/backtest/run/stored", params=params)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_backtest_request"
    assert _run_rows() == []


def test_offsets_are_normalized_to_utc_and_exactly_24_hours_is_not_clamped(monkeypatch):
    seen = {}

    async def capture(**kwargs):
        seen.update(kwargs)
        raise StoredHistoryNoDataError("stop after validation")

    monkeypatch.setattr(backtest_route, "acquire_stored_replay_data", capture)
    with TestClient(app) as client:
        response = client.post(
            "/backtest/run/stored",
            params=_params(start="2026-02-02T09:30:00-05:00", end="2026-02-03T09:30:00-05:00", symbol=" zstore1 "),
        )
    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "stored_candles_no_data", "message": "stop after validation"}
    assert seen["symbol"] == SYMBOL
    assert seen["start"] == datetime(2026, 2, 2, 14, 30, tzinfo=timezone.utc)
    assert seen["end"] == datetime(2026, 2, 3, 14, 30, tzinfo=timezone.utc)
    assert seen["start"].utcoffset() == timedelta(0)
    settings = get_settings()
    assert seen["daily_lookback_days"] == settings.daily_levels_lookback_days
    assert seen["premarket_lookback_days"] == settings.feature_engine_premarket_lookback_days


def test_route_missing_data_is_structured_and_writes_no_run_row():
    with TestClient(app) as client:
        response = client.post("/backtest/run/stored", params=_params())
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "stored_candles_no_data"
    assert "recorded" in detail["message"]
    assert _run_rows() == []


# --- route: connected-provider guards ---------------------------------------


@pytest.mark.parametrize(
    ("module", "label"),
    [(finnhub_data, "Finnhub"), (market_data, "Polygon"), (broker, "IBKR")],
)
def test_connected_provider_is_rejected_before_reading_or_writing(monkeypatch, module, label):
    async def must_not_read(**_kwargs):
        raise AssertionError("acquisition must not run while a live provider is connected")

    monkeypatch.setattr(module, "is_connected", lambda: True)
    monkeypatch.setattr(backtest_route, "acquire_stored_replay_data", must_not_read)
    with TestClient(app) as client:
        response = client.post("/backtest/run/stored", params=_params())
    assert response.status_code == 409
    assert label in response.json()["detail"]
    assert _run_rows() == []


def test_guard_is_rechecked_after_the_read_and_before_any_replay_engine(monkeypatch):
    _seed(SYMBOL, _minutes(T0, 3))
    real_acquire = backtest_route.acquire_stored_replay_data

    async def acquire_then_connect(**kwargs):
        dataset = await real_acquire(**kwargs)
        monkeypatch.setattr(broker, "is_connected", lambda: True)  # connects mid-read
        return dataset

    class MustNotConstruct:
        def __init__(self, **_kwargs):
            raise AssertionError("runner must not be built once a live provider is connected")

    monkeypatch.setattr(backtest_route, "acquire_stored_replay_data", acquire_then_connect)
    monkeypatch.setattr(backtest_route, "BacktestRunner", MustNotConstruct)
    with TestClient(app) as client:
        response = client.post("/backtest/run/stored", params=_params())
    assert response.status_code == 409
    assert "IBKR" in response.json()["detail"]
    assert _run_rows() == []


def test_existing_fixture_route_still_works_alongside_the_stored_route():
    with TestClient(app) as client:
        response = client.post(
            "/backtest/run",
            params={"strategy_name": "VWAP", "symbol": FIXTURE_LABEL, "scenario": "vwap_neutral_conquest"},
        )
    assert response.status_code == 200
    assert response.json()["outcomes_recorded"] == 1


# --- real runner replay ------------------------------------------------------


def _seed_scenario_as_recorded_history(scenario: str):
    candles = load_scenario_candles(scenario)
    rows = [(c.candle_ts, c.open, c.high, c.low, c.close, c.volume) for c in candles]
    symbol_id = _seed(SYMBOL, rows)
    return candles, symbol_id


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_real_replay_over_recorded_candles_matches_the_proven_fixture_replay_and_preserves_source():
    candles, live_id = _seed_scenario_as_recorded_history("vwap_neutral_conquest")
    # Same ticker in the backtest namespace, holding decoy rows that must be neither read nor modified.
    decoy_id = _seed(SYMBOL, _minutes(candles[0].candle_ts, 30, price=999.0), is_backtest=True)
    live_before = _source_snapshot(live_id)
    decoy_before = _source_snapshot(decoy_id)
    live_derived_before = _derived_counts(live_id)
    assert live_derived_before == {k: 0 for k in live_derived_before}

    historical_before = broker_registry.get_historical_provider()
    start, end = candles[0].candle_ts, candles[-1].candle_ts  # last recorded candle is excluded (end exclusive)

    with TestClient(app) as client:
        stored = client.post("/backtest/run/stored", params=_params(start=_iso(start), end=_iso(end)))
        fixture = client.post(
            "/backtest/run",
            params={"strategy_name": "VWAP", "symbol": FIXTURE_LABEL, "scenario": "vwap_neutral_conquest"},
        )
    assert stored.status_code == 200, stored.text
    assert fixture.status_code == 200, fixture.text
    body = stored.json()
    assert set(body) == {"run_id", "sweep_id", "outcomes_recorded", "discarded_signals"}
    assert body["outcomes_recorded"] == 1

    rows = _outcomes(body["run_id"])
    assert len(rows) == 1
    strategy, symbol, direction, _entry, _exit, _r, _reason, _entered, _exited, is_backtest = rows[0]
    assert (strategy, symbol, direction, is_backtest) == ("VWAP", SYMBOL, "SELL", True)

    # Same candles, same strategy, same runner: same trade as the fixture replay.
    fixture_rows = _outcomes(fixture.json()["run_id"])
    assert [r[2:] for r in rows] == [r[2:] for r in fixture_rows]

    # Provenance on the existing run row.
    run_rows = _run_rows()
    assert len(run_rows) == 1
    assert str(run_rows[0][0]) == body["run_id"]
    assert run_rows[0][1] == STORED_DATA_VERSION
    assert run_rows[0][2] == [SYMBOL]

    # Source rows (both namespaces) untouched; live-namespace derived state untouched;
    # the runner's own state landed in the backtest namespace.
    assert _source_snapshot(live_id) == live_before
    assert _source_snapshot(decoy_id) == decoy_before
    assert _derived_counts(live_id) == live_derived_before
    session = SessionLocal()
    try:
        backtest_state = session.execute(
            text(
                "SELECT count(*) FROM market_state_history h JOIN symbols s ON s.id = h.symbol_id "
                "WHERE s.ticker = :t AND s.is_backtest IS TRUE"
            ),
            {"t": SYMBOL},
        ).scalar_one()
        live_symbols = session.execute(
            text("SELECT count(*) FROM symbols WHERE ticker = :t AND is_backtest IS FALSE"), {"t": SYMBOL}
        ).scalar_one()
    finally:
        session.close()
    assert backtest_state > 0
    assert live_symbols == 1

    # Process-wide replay seams restored.
    assert broker_registry.get_historical_provider() is historical_before


def test_rows_at_or_after_end_cannot_change_a_replay_and_recorded_daily_history_is_served():
    candles, live_id = _seed_scenario_as_recorded_history("vwap_neutral_conquest")
    start, end = candles[0].candle_ts, candles[-1].candle_ts
    # Real recorded-style daily history strictly before the replay day.
    _seed(
        SYMBOL,
        [_bar(datetime(2026, 1, 5, 5, 0, tzinfo=timezone.utc) + timedelta(days=i), 98.0 + i * 0.1) for i in range(20)],
        timeframe="1d",
    )

    with TestClient(app) as client:
        first = client.post("/backtest/run/stored", params=_params(start=_iso(start), end=_iso(end)))
        assert first.status_code == 200, first.text

        # Add look-ahead bait: 1m rows after `end`, and a daily bar for the replay day and the next day.
        _seed(SYMBOL, [_bar(end + timedelta(minutes=i), 5000.0 + i) for i in range(1, 6)])
        session = SessionLocal()
        try:
            symbol_id = session.execute(
                text("SELECT id FROM symbols WHERE ticker = :t AND is_backtest IS FALSE"), {"t": SYMBOL}
            ).scalar_one()
            for ts in (datetime(2026, 2, 2, 5, 0, tzinfo=timezone.utc), datetime(2026, 2, 3, 5, 0, tzinfo=timezone.utc)):
                session.execute(
                    text(
                        "INSERT INTO candles (symbol_id, timeframe, candle_ts, open, high, low, close, volume) "
                        "VALUES (:s, '1d', :ts, 5000, 5001, 4999, 5000, 1)"
                    ),
                    {"s": symbol_id, "ts": ts},
                )
            session.commit()
        finally:
            session.close()
        second = client.post("/backtest/run/stored", params=_params(start=_iso(start), end=_iso(end)))
        assert second.status_code == 200, second.text

    a = _outcomes(first.json()["run_id"])
    b = _outcomes(second.json()["run_id"])
    assert a and a == b
    assert first.json()["run_id"] != second.json()["run_id"]
    assert [d["reason"] for d in first.json()["discarded_signals"]] == [
        d["reason"] for d in second.json()["discarded_signals"]
    ]
