"""Real-PostgreSQL tests for ``POST /backtest/sweep/stored`` (task
``stored-candle-symbol-sweep``).

The route, ``acquire_stored_replay_data``, the real ``BacktestRunner``, the real
engine pipeline and the real database are used throughout. Doubles exist only
where a test needs to *observe* or *provoke* something the real components do
not expose: spies around ``BacktestRunner`` / the acquisition call, a
connected-provider flag, an injected database failure, and an injected failure
inside the runner's outcome writer. "Must not be invoked" checks record calls
instead of raising, because the sweep deliberately turns an exception raised
while handling one symbol into that symbol's result.

Datasets are synthetic and differ per symbol on purpose: the recorded
``first_pullback_vwap_dip`` session makes ``FirstPullback`` produce one trade,
while the recorded ``vwap_neutral_conquest`` and ``volume_gated_baseline``
sessions produce none for it. These tests prove plumbing, isolation and
look-ahead safety, never strategy profitability.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from starlette.testclient import TestClient

import app.api.routes.backtest as backtest_route
import app.backtest_runner.runner as runner_module
from app.api.routes import broker, finnhub_data, market_data
from app.backtest_runner import engine_singleton_guard
from app.backtest_runner.scenarios import load_scenario_candles
from app.backtest_runner.stored_history import (
    STORED_DATA_VERSION,
    StoredHistoryNoDataError,
    StoredHistoryUnavailableError,
)
from app.core.config import get_settings
from app.db.partitions import ensure_month_partition
from app.db.session import SessionLocal
from app.main import app
from app.services import broker_registry

PULL1 = "ZSSWPUL1"  # recorded first_pullback_vwap_dip session -> FirstPullback trades once
PULL2 = "ZSSWPUL2"  # identical data to PULL1 (state-independence checks)
NEUT = "ZSSWNEUT"  # recorded vwap_neutral_conquest session -> FirstPullback: 0 outcomes
BASE = "ZSSWBASE"  # recorded volume_gated_baseline session -> FirstPullback: 0 outcomes
THIN = "ZSSWTHIN"  # three recorded minutes -> 0 outcomes
THIN2 = "ZSSWTHIN2"
NONE = "ZSSWNONE"  # nothing recorded
NONE2 = "ZSSWNONE2"
BAD = "ZSSWBAD"  # a recorded candle holds NaN
DECOY = "ZSSWDECOY"  # recorded only in the backtest namespace
BATCH = [f"ZSSWB{i:02d}" for i in range(21)]
_TICKERS = [PULL1, PULL2, NEUT, BASE, THIN, THIN2, NONE, NONE2, BAD, DECOY, *BATCH]

STRATEGY = "FirstPullback"
T0 = datetime(2026, 2, 2, 14, 30, tzinfo=timezone.utc)  # Monday 09:30 ET
END = T0 + timedelta(minutes=119)  # 16:29 UTC, exclusive: inside every recorded scenario


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
        session.execute(text("DELETE FROM strategy_outcomes WHERE symbol = ANY(:t)"), {"t": _TICKERS})
        session.execute(
            text("DELETE FROM backtests WHERE symbol_universe && CAST(:t AS varchar[])"), {"t": _TICKERS}
        )
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
                text(f"DELETE FROM {table} WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = ANY(:t))"),
                {"t": _TICKERS},
            )
        session.execute(text("DELETE FROM symbols WHERE ticker = ANY(:t)"), {"t": _TICKERS})
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _isolated():
    _clean()
    yield
    _clean()
    get_settings.cache_clear()


# --- seeding and reading helpers --------------------------------------------


def _bar(ts: datetime, price: float = 100.0, volume: int = 1000):
    return (ts, price, price + 0.5, price - 0.5, price + 0.1, volume)


def _minutes(start: datetime, count: int, price: float = 100.0):
    return [_bar(start + timedelta(minutes=i), price + i * 0.01) for i in range(count)]


def _seed(ticker: str, rows, *, timeframe: str = "1m", is_backtest: bool = False) -> int:
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


def _seed_scenario(ticker: str, scenario: str, *, is_backtest: bool = False) -> int:
    rows = [
        (c.candle_ts, c.open, c.high, c.low, c.close, c.volume) for c in load_scenario_candles(scenario)
    ]
    return _seed(ticker, rows, is_backtest=is_backtest)


def _daily_history(count: int = 20, start: datetime = datetime(2026, 1, 5, 5, 0, tzinfo=timezone.utc)):
    return [_bar(start + timedelta(days=i), 98.0 + i * 0.1) for i in range(count)]


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _params(symbols, *, strategy: str = STRATEGY, start: str | None = None, end: str | None = None):
    return (
        [("strategy_name", strategy)]
        + [("symbols", s) for s in symbols]
        + [("start", start or _iso(T0)), ("end", end or _iso(END))]
    )


def _sweep(client: TestClient, symbols, **kwargs):
    return client.post("/backtest/sweep/stored", params=_params(symbols, **kwargs))


def _backtest_rows(tickers) -> list[tuple]:
    session = SessionLocal()
    try:
        return list(
            session.execute(
                text(
                    "SELECT run_id, sweep_id, symbol_universe, data_version FROM backtests "
                    "WHERE symbol_universe && CAST(:t AS varchar[]) ORDER BY symbol_universe"
                ),
                {"t": list(tickers)},
            ).all()
        )
    finally:
        session.close()


def _sweep_rows(sweep_id: str) -> list[tuple]:
    session = SessionLocal()
    try:
        return list(
            session.execute(
                text("SELECT run_id, symbol_universe, data_version FROM backtests WHERE sweep_id = :s"),
                {"s": sweep_id},
            ).all()
        )
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


def _outcome_count_for(tickers) -> int:
    session = SessionLocal()
    try:
        return session.execute(
            text("SELECT count(*) FROM strategy_outcomes WHERE symbol = ANY(:t)"), {"t": list(tickers)}
        ).scalar_one()
    finally:
        session.close()


def _candle_rows(tickers, *, is_backtest: bool) -> list[tuple]:
    session = SessionLocal()
    try:
        return list(
            session.execute(
                text(
                    "SELECT s.ticker, c.timeframe, c.candle_ts, c.open, c.high, c.low, c.close, c.volume "
                    "FROM candles c JOIN symbols s ON s.id = c.symbol_id "
                    "WHERE s.ticker = ANY(:t) AND s.is_backtest = :b "
                    "ORDER BY s.ticker, c.timeframe, c.candle_ts"
                ),
                {"t": list(tickers), "b": is_backtest},
            ).all()
        )
    finally:
        session.close()


_DERIVED = ("level_interaction_state", "level_interaction_events", "daily_levels_state", "market_state_history")


def _derived_counts(tickers, *, is_backtest: bool) -> dict[str, int]:
    session = SessionLocal()
    try:
        return {
            table: session.execute(
                text(
                    f"SELECT count(*) FROM {table} t JOIN symbols s ON s.id = t.symbol_id "
                    "WHERE s.ticker = ANY(:t) AND s.is_backtest = :b"
                ),
                {"t": list(tickers), "b": is_backtest},
            ).scalar_one()
            for table in _DERIVED
        }
    finally:
        session.close()


def _by_symbol(body: dict) -> dict[str, dict]:
    return {run["symbol"]: run for run in body["runs"]}


class _RecordingAcquire:
    """Spy around the real acquisition call (kept as the delegate)."""

    def __init__(self, delegate):
        self.delegate = delegate
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return await self.delegate(**kwargs)


def _install_runner_spy(monkeypatch):
    """Subclass the real runner to record construction, state and overlap."""
    real = backtest_route.BacktestRunner
    record = {"built": [], "order": [], "active": 0, "max_active": 0}

    class SpyRunner(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs):
            strategy = kwargs["strategy"]
            record["built"].append(
                {
                    "symbol": kwargs["symbol"],
                    "strategy": strategy,
                    "strategy_id": id(strategy),
                    "state_at_construction": dict(getattr(strategy, "_state", {"<missing>": True})),
                    "sweep_id": kwargs["sweep_id"],
                    "data_version": kwargs["data_version"],
                }
            )
            super().__init__(**kwargs)

        async def run(self):
            record["order"].append(self._symbol)
            record["active"] += 1
            record["max_active"] = max(record["max_active"], record["active"])
            try:
                return await super().run()
            finally:
                record["active"] -= 1

    monkeypatch.setattr(backtest_route, "BacktestRunner", SpyRunner)
    return record


# --- validation: the whole request is checked before anything runs -----------


@pytest.mark.parametrize(
    ("label", "params", "status", "code"),
    [
        ("unknown strategy", _params([PULL1], strategy="NOPE"), 400, None),
        ("blank symbol only", _params(["   "]), 422, "invalid_backtest_request"),
        ("empty symbol only", _params([""]), 422, "invalid_backtest_request"),
        ("blank after valid symbols", _params([PULL1, NEUT, "  "]), 422, "invalid_backtest_request"),
        ("naive start", _params([PULL1], start="2026-02-02T14:30:00"), 422, "invalid_backtest_request"),
        ("naive end", _params([PULL1], end="2026-02-02T16:29:00"), 422, "invalid_backtest_request"),
        ("start after end", _params([PULL1], start=_iso(END), end=_iso(T0)), 422, "invalid_backtest_request"),
        ("empty interval", _params([PULL1], start=_iso(T0), end=_iso(T0)), 422, "invalid_backtest_request"),
        (
            "24h plus one second",
            _params([PULL1], end=_iso(T0 + timedelta(hours=24, seconds=1))),
            422,
            "invalid_backtest_request",
        ),
        ("21 distinct symbols", _params(BATCH), 400, "backtest_sweep_too_large"),
        ("no symbols at all", [("strategy_name", STRATEGY), ("start", _iso(T0)), ("end", _iso(END))], 422, None),
    ],
)
def test_invalid_requests_are_rejected_before_any_read_or_write(monkeypatch, label, params, status, code):
    spy = _RecordingAcquire(lambda **_kw: (_ for _ in ()).throw(AssertionError("must not read")))
    monkeypatch.setattr(backtest_route, "acquire_stored_replay_data", spy)
    built = _install_runner_spy(monkeypatch)

    response = TestClient(app).post("/backtest/sweep/stored", params=params)

    assert response.status_code == status, (label, response.text)
    if code is not None:
        assert response.json()["detail"]["code"] == code
    assert spy.calls == [], f"{label}: acquisition ran for a rejected request"
    assert built["built"] == [], f"{label}: a runner was built for a rejected request"
    assert _backtest_rows(_TICKERS) == []
    assert _outcome_count_for(_TICKERS) == 0


def test_a_connected_provider_is_rejected_before_any_read_or_write_for_each_provider(monkeypatch):
    for module, label in ((finnhub_data, "Finnhub"), (market_data, "Polygon"), (broker, "IBKR")):
        with monkeypatch.context() as patch:
            spy = _RecordingAcquire(lambda **_kw: (_ for _ in ()).throw(AssertionError("must not read")))
            patch.setattr(backtest_route, "acquire_stored_replay_data", spy)
            patch.setattr(module, "is_connected", lambda: True)
            response = TestClient(app).post("/backtest/sweep/stored", params=_params([PULL1, NEUT]))
        assert response.status_code == 409, label
        assert label in response.json()["detail"]
        assert spy.calls == [], f"{label}: acquisition ran while a live provider was connected"
        assert _backtest_rows(_TICKERS) == []


def test_symbol_cap_counts_distinct_symbols_and_accepts_exactly_twenty(monkeypatch):
    async def no_data(**kwargs):
        raise StoredHistoryNoDataError(f"nothing recorded for {kwargs['symbol']}")

    spy = _RecordingAcquire(no_data)
    monkeypatch.setattr(backtest_route, "acquire_stored_replay_data", spy)

    twenty = BATCH[:20]
    # 41 listings, 20 distinct (mixed case and padding): the cap is on runs that would execute.
    listings = twenty + [s.lower() for s in twenty] + [f"  {twenty[0]} "]
    response = TestClient(app).post("/backtest/sweep/stored", params=_params(listings))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbols_requested"] == 20
    assert [r["symbol"] for r in body["runs"]] == twenty
    assert [c["symbol"] for c in spy.calls] == twenty
    assert body["symbols_failed"] == 20 and body["symbols_succeeded"] == 0


# --- normalization, duplicates, interval -------------------------------------


def test_symbols_are_normalized_and_deduplicated_in_first_occurrence_order():
    _seed_scenario(PULL1, "first_pullback_vwap_dip")
    _seed_scenario(NEUT, "vwap_neutral_conquest")

    with TestClient(app) as client:
        response = _sweep(client, [f"  {PULL1.lower()} ", NEUT, PULL1, NEUT.lower() + "  ", PULL1])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbols_requested"] == 2
    assert [r["symbol"] for r in body["runs"]] == [PULL1, NEUT]
    assert body["symbols_succeeded"] == 2 and body["symbols_failed"] == 0
    rows = _sweep_rows(body["sweep_id"])
    assert sorted(r.symbol_universe for r in rows) == sorted([[PULL1], [NEUT]])  # one run each, normalized labels
    assert len(rows) == 2


def test_shared_interval_is_exact_half_open_normalized_to_utc_and_identical_for_every_symbol(monkeypatch):
    # 14:28 .. 14:34 recorded for both symbols, with different price levels.
    _seed(THIN, _minutes(T0 - timedelta(minutes=2), 7, price=100.0))
    _seed(THIN2, _minutes(T0 - timedelta(minutes=2), 7, price=250.0))
    spy = _RecordingAcquire(backtest_route.acquire_stored_replay_data)
    monkeypatch.setattr(backtest_route, "acquire_stored_replay_data", spy)

    with TestClient(app) as client:
        response = _sweep(
            client,
            [THIN, THIN2],
            strategy="VWAP",
            start="2026-02-02T09:30:00-05:00",  # == 14:30Z
            end="2026-02-02T09:33:00-05:00",  # == 14:33Z, exclusive
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["start"] == "2026-02-02T14:30:00Z" and body["end"] == "2026-02-02T14:33:00Z"
    assert len(spy.calls) == 2
    for call in spy.calls:
        assert call["start"] == T0 and call["end"] == T0 + timedelta(minutes=3)
        assert call["start"].utcoffset() == timedelta(0) and call["end"].utcoffset() == timedelta(0)
        settings = get_settings()
        assert call["daily_lookback_days"] == settings.daily_levels_lookback_days
        assert call["premarket_lookback_days"] == settings.feature_engine_premarket_lookback_days
    for run in body["runs"]:
        assert run["primary_candle_count"] == 3  # 14:30, 14:31, 14:32 — the 14:33 and 14:34 rows are not replayed
        assert run["warmup_minute_candle_count"] == 2  # 14:28 and 14:29 only; nothing at/after end is read
        assert run["error"] is None and run["outcomes_recorded"] == 0


def test_exactly_24_hours_is_accepted_and_not_clamped(monkeypatch):
    async def no_data(**kwargs):
        raise StoredHistoryNoDataError("stop after validation")

    spy = _RecordingAcquire(no_data)
    monkeypatch.setattr(backtest_route, "acquire_stored_replay_data", spy)
    response = TestClient(app).post(
        "/backtest/sweep/stored", params=_params([PULL1], end=_iso(T0 + timedelta(hours=24)))
    )
    assert response.status_code == 200
    assert spy.calls[0]["end"] - spy.calls[0]["start"] == timedelta(hours=24)


# --- namespaces, source rows, derived state -----------------------------------


def test_live_and_backtest_namespaces_stay_separate_and_source_rows_are_never_modified():
    _seed_scenario(PULL1, "first_pullback_vwap_dip")
    _seed_scenario(NEUT, "vwap_neutral_conquest")
    _seed(PULL1, _daily_history(), timeframe="1d")
    # Same ticker in the BACKTEST namespace holding decoy rows that must be neither read nor modified.
    _seed(PULL1, _minutes(T0 - timedelta(minutes=5), 40, price=999.0), is_backtest=True)
    _seed(PULL1, _daily_history(), timeframe="1d", is_backtest=True)
    # A symbol that exists ONLY in the backtest namespace has no live data at all.
    _seed(DECOY, _minutes(T0, 30, price=999.0), is_backtest=True)

    live_before = _candle_rows([PULL1, NEUT, DECOY], is_backtest=False)
    decoy_before = _candle_rows([PULL1, DECOY], is_backtest=True)
    live_derived_before = _derived_counts([PULL1, NEUT, DECOY], is_backtest=False)
    assert live_derived_before == {k: 0 for k in _DERIVED}

    with TestClient(app) as client:
        response = _sweep(client, [PULL1, NEUT, DECOY])

    assert response.status_code == 200, response.text
    runs = _by_symbol(response.json())
    assert runs[PULL1]["outcomes_recorded"] == 1 and runs[NEUT]["outcomes_recorded"] == 0
    assert runs[DECOY]["error"]["code"] == "stored_candles_no_data"  # decoy-only symbol: nothing LIVE recorded
    assert runs[PULL1]["warmup_minute_candle_count"] == 0  # the 999-priced decoy rows are not warm-up
    assert runs[PULL1]["daily_candle_count"] == 20  # the 20 live daily bars only, not the decoy namespace's 20 more

    assert _candle_rows([PULL1, NEUT, DECOY], is_backtest=False) == live_before
    assert _candle_rows([PULL1, DECOY], is_backtest=True) == decoy_before
    assert _derived_counts([PULL1, NEUT, DECOY], is_backtest=False) == live_derived_before
    assert _derived_counts([PULL1, NEUT], is_backtest=True)["market_state_history"] > 0  # runner state went to backtest ns
    assert _derived_counts([DECOY], is_backtest=True) == {k: 0 for k in _DERIVED}  # failed symbol was never replayed
    session = SessionLocal()
    try:
        live_symbols = session.execute(
            text("SELECT count(*) FROM symbols WHERE ticker = ANY(:t) AND is_backtest IS FALSE"),
            {"t": [PULL1, NEUT, DECOY]},
        ).scalar_one()
    finally:
        session.close()
    assert live_symbols == 2  # PULL1, NEUT — DECOY has no live-namespace symbol and none was created


# --- independent per-symbol state, sequential execution, seams ----------------


def test_each_symbol_gets_a_fresh_strategy_runs_sequentially_in_order_and_matches_its_single_run(monkeypatch):
    _seed_scenario(PULL1, "first_pullback_vwap_dip")
    _seed_scenario(NEUT, "vwap_neutral_conquest")
    _seed_scenario(PULL2, "first_pullback_vwap_dip")
    order = [PULL1, NEUT, PULL2]
    record = _install_runner_spy(monkeypatch)
    historical_before = broker_registry.get_historical_provider()

    with TestClient(app) as client:
        response = _sweep(client, order)
        assert response.status_code == 200, response.text
        body = response.json()
        runs = _by_symbol(body)

        # Fresh instance per symbol: distinct objects, each untouched at construction time,
        # all carrying the one shared sweep_id and the stored provenance label.
        assert [b["symbol"] for b in record["built"]] == order
        assert len({b["strategy_id"] for b in record["built"]}) == 3
        assert all(b["state_at_construction"] == {} for b in record["built"])
        assert all(b["strategy"].name == STRATEGY for b in record["built"])
        assert {str(b["sweep_id"]) for b in record["built"]} == {body["sweep_id"]}
        assert {b["data_version"] for b in record["built"]} == {STORED_DATA_VERSION}

        # Strictly sequential, in request order, never overlapping.
        assert record["order"] == order and record["max_active"] == 1

        # Seams restored, run lock released.
        assert broker_registry.get_historical_provider() is historical_before
        assert not engine_singleton_guard._RUN_LOCK.locked()

        # The same symbols through the single-symbol stored route produce identical trades.
        monkeypatch.undo()
        for symbol in order:
            single = client.post(
                "/backtest/run/stored",
                params={"strategy_name": STRATEGY, "symbol": symbol, "start": _iso(T0), "end": _iso(END)},
            )
            assert single.status_code == 200, single.text
            assert single.json()["outcomes_recorded"] == runs[symbol]["outcomes_recorded"]
            assert _outcomes(single.json()["run_id"]) == _outcomes(runs[symbol]["run_id"])
            assert single.json()["run_id"] != runs[symbol]["run_id"]

    assert [runs[s]["outcomes_recorded"] for s in order] == [1, 0, 1]
    # PULL2 ran after PULL1 and NEUT with identical data: nothing leaked into it.
    a, b = _outcomes(runs[PULL1]["run_id"]), _outcomes(runs[PULL2]["run_id"])
    assert len(a) == len(b) == 1 and a[0][2:] == b[0][2:]
    assert (a[0][1], b[0][1]) == (PULL1, PULL2)
    assert a[0][0] == STRATEGY and a[0][9] is True


# --- results: mixed success and failure, zero outcomes, shared sweep_id -------


def test_mixed_success_failure_zero_outcomes_and_one_shared_sweep_id():
    _seed_scenario(PULL1, "first_pullback_vwap_dip")
    _seed(THIN, _minutes(T0, 3))  # replays, trades nothing
    _seed(BAD, _minutes(T0, 5))
    _seed_scenario(PULL2, "first_pullback_vwap_dip")
    session = SessionLocal()
    try:
        session.execute(
            text(
                "UPDATE candles SET close = 'NaN'::numeric WHERE candle_ts = :ts AND symbol_id IN "
                "(SELECT id FROM symbols WHERE ticker = :t AND is_backtest IS FALSE)"
            ),
            {"ts": T0 + timedelta(minutes=2), "t": BAD},
        )
        session.commit()
    finally:
        session.close()
    order = [PULL1, NONE, THIN, BAD, PULL2]

    with TestClient(app) as client:
        response = _sweep(client, order)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "sweep_id", "strategy_name", "start", "end", "data_version",
        "symbols_requested", "symbols_succeeded", "symbols_failed", "runs",
    }
    assert body["strategy_name"] == STRATEGY and body["data_version"] == STORED_DATA_VERSION
    assert (body["symbols_requested"], body["symbols_succeeded"], body["symbols_failed"]) == (5, 3, 2)
    assert [r["symbol"] for r in body["runs"]] == order  # request order, never reordered

    runs = _by_symbol(body)
    for ok in (PULL1, THIN, PULL2):
        run = runs[ok]
        assert set(run) == {
            "symbol", "run_id", "outcomes_recorded", "discarded_signals", "primary_candle_count",
            "warmup_minute_candle_count", "daily_candle_count", "error",
        }
        assert run["error"] is None and run["run_id"] is not None
        assert isinstance(run["discarded_signals"], list)
    assert (runs[PULL1]["outcomes_recorded"], runs[THIN]["outcomes_recorded"], runs[PULL2]["outcomes_recorded"]) == (1, 0, 1)
    assert runs[THIN]["primary_candle_count"] == 3  # zero outcomes is a success, with counts to show it

    no_data, malformed = runs[NONE], runs[BAD]
    assert no_data["error"]["code"] == "stored_candles_no_data" and NONE in no_data["error"]["message"]
    assert malformed["error"]["code"] == "stored_candles_malformed"
    for failed in (no_data, malformed):
        assert failed["error"]["stage"] == "before_replay"
        assert failed["run_id"] is None and failed["outcomes_recorded"] is None
        assert failed["discarded_signals"] == []
        assert failed["primary_candle_count"] is None

    # Database truth: exactly the three successful runs exist under the ONE shared sweep_id.
    rows = _sweep_rows(body["sweep_id"])
    assert {str(r.run_id) for r in rows} == {runs[s]["run_id"] for s in (PULL1, THIN, PULL2)}
    assert {r.data_version for r in rows} == {STORED_DATA_VERSION}
    assert len({runs[s]["run_id"] for s in (PULL1, THIN, PULL2)}) == 3
    # Failed-before-replay symbols left no run row and no outcome row anywhere (verified, not assumed).
    assert _backtest_rows([NONE, BAD]) == []
    assert _outcome_count_for([NONE, BAD, THIN]) == 0
    # Completed runs survived the failures between and after them.
    assert len(_outcomes(runs[PULL1]["run_id"])) == 1 and len(_outcomes(runs[PULL2]["run_id"])) == 1


def test_zero_outcomes_everywhere_is_a_successful_sweep():
    _seed(THIN, _minutes(T0, 4))
    _seed(THIN2, _minutes(T0, 6, price=250.0))

    with TestClient(app) as client:
        response = _sweep(client, [THIN, THIN2], strategy="VWAP")

    body = response.json()
    assert response.status_code == 200, response.text
    assert (body["symbols_succeeded"], body["symbols_failed"]) == (2, 0)
    assert [r["outcomes_recorded"] for r in body["runs"]] == [0, 0]
    assert all(r["error"] is None and r["run_id"] for r in body["runs"])
    assert len(_sweep_rows(body["sweep_id"])) == 2 and _outcome_count_for([THIN, THIN2]) == 0


def test_an_entirely_failed_sweep_is_a_structured_200_with_no_run_rows():
    with TestClient(app) as client:
        response = _sweep(client, [NONE, NONE2])

    body = response.json()
    assert response.status_code == 200, response.text
    assert (body["symbols_requested"], body["symbols_succeeded"], body["symbols_failed"]) == (2, 0, 2)
    assert all(r["run_id"] is None and r["error"]["code"] == "stored_candles_no_data" for r in body["runs"])
    assert body["sweep_id"]  # an ID is returned for the request, but nothing was recorded under it
    assert _sweep_rows(body["sweep_id"]) == []


def test_database_unavailable_for_one_symbol_does_not_stop_the_others(monkeypatch):
    _seed_scenario(PULL1, "first_pullback_vwap_dip")
    _seed_scenario(PULL2, "first_pullback_vwap_dip")
    real = backtest_route.acquire_stored_replay_data

    async def flaky(**kwargs):
        if kwargs["symbol"] == NEUT:
            raise StoredHistoryUnavailableError("Recorded candles could not be read from PostgreSQL (OperationalError).")
        return await real(**kwargs)

    monkeypatch.setattr(backtest_route, "acquire_stored_replay_data", flaky)
    with TestClient(app) as client:
        response = _sweep(client, [PULL1, NEUT, PULL2])

    body = response.json()
    runs = _by_symbol(body)
    assert response.status_code == 200
    assert runs[NEUT]["error"] == {
        "code": "stored_history_unavailable",
        "message": "Recorded candles could not be read from PostgreSQL (OperationalError).",
        "stage": "before_replay",
    }
    assert runs[PULL1]["outcomes_recorded"] == 1 and runs[PULL2]["outcomes_recorded"] == 1
    assert len(_sweep_rows(body["sweep_id"])) == 2


def test_runner_failure_is_reported_without_an_invented_id_and_leaves_documented_residue(monkeypatch):
    _seed_scenario(PULL1, "first_pullback_vwap_dip")
    _seed_scenario(PULL2, "first_pullback_vwap_dip")
    real_record = runner_module.record_strategy_outcome

    def flaky_record(outcome):
        if outcome.symbol == PULL1:
            raise RuntimeError("synthetic failure while persisting an outcome")
        return real_record(outcome)

    monkeypatch.setattr(runner_module, "record_strategy_outcome", flaky_record)
    historical_before = broker_registry.get_historical_provider()
    with TestClient(app) as client:
        response = _sweep(client, [PULL1, PULL2])
        assert broker_registry.get_historical_provider() is historical_before  # seams restored after a failed run
        assert not engine_singleton_guard._RUN_LOCK.locked()

    assert response.status_code == 200, response.text
    body = response.json()
    runs = _by_symbol(body)
    failed = runs[PULL1]
    assert failed["run_id"] is None and failed["outcomes_recorded"] is None  # no ID is invented
    assert failed["error"]["code"] == "backtest_run_failed"
    assert failed["error"]["stage"] == "during_replay"
    assert "RuntimeError" in failed["error"]["message"] and "synthetic failure" in failed["error"]["message"]
    assert runs[PULL2]["error"] is None and runs[PULL2]["outcomes_recorded"] == 1  # the sweep carried on
    assert (body["symbols_succeeded"], body["symbols_failed"]) == (1, 1)

    # What the response cannot say, observed directly: the failed replay HAD already written its run row
    # (the runner writes it before replaying) under the shared sweep_id, with a run_id this response lacks.
    rows = _sweep_rows(body["sweep_id"])
    assert len(rows) == 2
    reported = {runs[PULL2]["run_id"]}
    unreported = [r for r in rows if str(r.run_id) not in reported]
    assert len(unreported) == 1 and unreported[0].symbol_universe == [PULL1]
    assert _outcomes(str(unreported[0].run_id)) == []  # this particular failure persisted no outcome


# --- connected-provider guard, applied per symbol -----------------------------


def test_guard_is_rechecked_after_each_read_and_before_each_replay_without_undoing_completed_runs(monkeypatch):
    _seed_scenario(PULL1, "first_pullback_vwap_dip")
    _seed_scenario(PULL2, "first_pullback_vwap_dip")
    _seed_scenario(NEUT, "vwap_neutral_conquest")
    reads: list[str] = []
    real = backtest_route.acquire_stored_replay_data

    async def acquire_then_connect(**kwargs):
        reads.append(kwargs["symbol"])
        dataset = await real(**kwargs)
        if len(reads) == 2:
            monkeypatch.setattr(broker, "is_connected", lambda: True)  # a live provider connects mid-sweep
        return dataset

    monkeypatch.setattr(backtest_route, "acquire_stored_replay_data", acquire_then_connect)
    record = _install_runner_spy(monkeypatch)

    with TestClient(app) as client:
        response = _sweep(client, [PULL1, NEUT, PULL2])

    assert response.status_code == 200, response.text
    body = response.json()
    runs = _by_symbol(body)
    assert runs[PULL1]["error"] is None and runs[PULL1]["outcomes_recorded"] == 1  # finished before the connection
    for blocked in (NEUT, PULL2):
        assert runs[blocked]["run_id"] is None
        assert runs[blocked]["error"]["code"] == "live_data_connected"
        assert runs[blocked]["error"]["stage"] == "before_replay"
        assert "IBKR" in runs[blocked]["error"]["message"]
    assert reads == [PULL1, NEUT]  # NEUT was read but never replayed; PULL2 was stopped before even being read
    assert record["order"] == [PULL1] and [b["symbol"] for b in record["built"]] == [PULL1]  # no engine for NEUT/PULL2
    assert [str(r.run_id) for r in _sweep_rows(body["sweep_id"])] == [runs[PULL1]["run_id"]]
    assert _backtest_rows([NEUT, PULL2]) == []


# --- look-ahead exclusion and recorded history, per symbol --------------------


def test_look_ahead_rows_never_change_a_replay_and_warmup_is_reported_per_symbol():
    _seed_scenario(PULL1, "first_pullback_vwap_dip")
    _seed_scenario(PULL2, "first_pullback_vwap_dip")
    for symbol in (PULL1, PULL2):
        _seed(symbol, _daily_history(), timeframe="1d")  # recorded daily history strictly before the replay day
    # PULL2 alone also holds look-ahead bait. The recorded scenario already has 1m rows at/after END
    # (16:29..16:39), so overwrite those with extreme prices; add daily bars for the replay day and after.
    session = SessionLocal()
    try:
        baited = session.execute(
            text(
                "UPDATE candles SET open = 5000, high = 5001, low = 4999, close = 5000 "
                "WHERE timeframe = '1m' AND candle_ts >= :end AND symbol_id IN "
                "(SELECT id FROM symbols WHERE ticker = :t AND is_backtest IS FALSE)"
            ),
            {"end": END, "t": PULL2},
        ).rowcount
        session.commit()
    finally:
        session.close()
    assert baited > 0  # the bait really exists at/after END
    _seed(
        PULL2,
        [_bar(datetime(2026, 2, 2, 5, 0, tzinfo=timezone.utc), 5000.0), _bar(datetime(2026, 2, 3, 5, 0, tzinfo=timezone.utc), 5000.0)],
        timeframe="1d",
    )
    # THIN: only 1m warm-up before T0 plus three replayed minutes, no daily rows at all.
    _seed(THIN, _minutes(T0 - timedelta(minutes=5), 8))

    with TestClient(app) as client:
        response = _sweep(client, [PULL1, PULL2, THIN])

    assert response.status_code == 200, response.text
    runs = _by_symbol(response.json())
    assert runs[PULL1]["error"] is None and runs[PULL2]["error"] is None
    assert runs[PULL1]["daily_candle_count"] == runs[PULL2]["daily_candle_count"] == 20  # bait daily bars excluded
    assert runs[PULL1]["warmup_minute_candle_count"] == runs[PULL2]["warmup_minute_candle_count"] == 0
    assert runs[PULL1]["primary_candle_count"] == runs[PULL2]["primary_candle_count"] == 119
    # Identical trade with and without the bait: nothing at or after END, and no same-day/future daily bar, was used.
    a, b = _outcomes(runs[PULL1]["run_id"]), _outcomes(runs[PULL2]["run_id"])
    assert a and len(a) == len(b) and [r[2:] for r in a] == [r[2:] for r in b]
    assert [d["reason"] for d in runs[PULL1]["discarded_signals"]] == [d["reason"] for d in runs[PULL2]["discarded_signals"]]
    # Per-symbol history differs and is reported as found: nothing is synthesized for the thin symbol.
    assert runs[THIN]["warmup_minute_candle_count"] == 5 and runs[THIN]["primary_candle_count"] == 3
    assert runs[THIN]["daily_candle_count"] == 0


# --- the shared sweep_id feeds the existing sweep read-outs --------------------


def test_sweep_id_works_with_the_existing_sweep_outcome_run_and_summary_reads():
    _seed_scenario(PULL1, "first_pullback_vwap_dip")
    _seed_scenario(PULL2, "first_pullback_vwap_dip")
    _seed_scenario(NEUT, "vwap_neutral_conquest")

    with TestClient(app) as client:
        body = _sweep(client, [PULL1, NEUT, PULL2, NONE]).json()
        sweep_id = body["sweep_id"]
        assert body["symbols_succeeded"] == 3 and body["symbols_failed"] == 1

        outcomes = client.get("/intelligence/strategy-outcomes", params={"is_backtest": "true", "sweep_id": sweep_id})
        runs = client.get("/intelligence/backtest-runs", params={"sweep_id": sweep_id})
        summary = client.get("/intelligence/backtest-selection-summary", params={"sweep_id": sweep_id})

    assert outcomes.status_code == runs.status_code == summary.status_code == 200
    assert sorted(o["symbol"] for o in outcomes.json()["outcomes"]) == [PULL1, PULL2]
    listed = runs.json()["backtest_runs"]
    assert len(listed) == 3 and {r["data_version"] for r in listed} == {STORED_DATA_VERSION}
    assert {r["sweep_id"] for r in listed} == {sweep_id}
    group = summary.json()["groups"][0]
    assert summary.json()["selection_found"] is True and len(summary.json()["groups"]) == 1
    assert (group["run_count"], group["total_outcomes"]) == (3, 2)  # full population, zero-outcome run included
    assert group["data_version"] == STORED_DATA_VERSION and group["strategy_name"] == STRATEGY


# --- neighbours are unaffected -------------------------------------------------


def test_single_symbol_stored_route_and_fixture_sweep_still_work_beside_the_stored_sweep():
    _seed_scenario(PULL1, "first_pullback_vwap_dip")
    with TestClient(app) as client:
        single = client.post(
            "/backtest/run/stored",
            params={"strategy_name": STRATEGY, "symbol": PULL1, "start": _iso(T0), "end": _iso(END)},
        )
        fixture = client.post(
            "/backtest/sweep",
            params=[("strategy_name", STRATEGY), ("symbols", THIN), ("scenarios", "first_pullback_vwap_dip")],
        )
    assert single.status_code == 200 and single.json()["outcomes_recorded"] == 1
    assert set(single.json()) == {"run_id", "sweep_id", "outcomes_recorded", "discarded_signals"}
    assert fixture.status_code == 200 and fixture.json()["pairs_succeeded"] == 1
