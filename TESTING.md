# TESTING — IBKR historical Backtest Runner provider

## Base and environment

- Fresh GitHub `main` base: `38cf0f68ff435b7cf5fca979d1977d0e5f8b8e84`.
- Worktree: fresh codeload archive extracted into `/tmp/tmp.Pu0aohQ2X0`; implementation was not overlaid onto the existing repository checkout.
- Python: repository virtual environment, Python 3.14, `ib_async==2.1.0`.
- Database: real PostgreSQL 18.6, isolated unprivileged cluster under `/tmp`, listening on `127.0.0.1:55432`; real Alembic migrations `0001` through `0010` applied.
- PostgreSQL timezone: UTC. The cluster initially inherited `Asia/Dhaka`; that first setup run produced two deterministic timestamp-rendering failures, so it was discarded, the server default was changed to UTC, and the untouched baseline was rerun before source edits.
- No live IB Gateway/TWS/account connection was available or claimed.

Database setup used:

```bash
/usr/lib/postgresql/18/bin/initdb -D /tmp/atos-pg.r5q55D/data --auth=trust --username=trading
/usr/lib/postgresql/18/bin/pg_ctl -D /tmp/atos-pg.r5q55D/data \
  -l /tmp/atos-pg.r5q55D/postgres.log \
  -o '-p 55432 -k /tmp/atos-pg.r5q55D' start
/usr/lib/postgresql/18/bin/createdb -h 127.0.0.1 -p 55432 -U trading trading_workspace
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 \
POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/alembic upgrade head
```

## Untouched baseline

Command, before the first source edit:

```bash
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 \
POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/pytest -q --tb=short
```

Result: **741 collected; 740 passed; 1 failed; 0 skipped** in 788.94 seconds.

The sole failure was the repository's timing-sensitive
`test_feature_engine_backfills_from_persisted_history_on_cold_start`: it received two
events before its fixed assertion instead of one. Immediate isolated rerun:

```bash
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 \
POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/pytest \
  tests/test_feature_engine.py::test_feature_engine_backfills_from_persisted_history_on_cold_start \
  -q --tb=short
```

Result: **1 passed** in 1.30 seconds. No source was changed to hide or weaken it.

For transparency, the discarded non-UTC setup run collected the same 741 tests and
reported 739 passed / 2 failed. Both failures compared UTC expectations to timestamps
rendered with the PostgreSQL server's inherited `+06:00` zone; neither represented a
repository defect.

## Focused validation

Pure adapter/acquisition tests:

```bash
.venv/bin/pytest tests/test_ibkr_adapter.py tests/test_ibkr_historical.py \
  -q --tb=short --disable-warnings
```

Result: **19 passed**.

New route tests against real PostgreSQL:

```bash
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 \
POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/pytest tests/test_ibkr_backtest_route.py \
  -q --tb=short --disable-warnings
```

Result: **12 passed**.

Complete focused set after final adapter assertions:

```bash
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 \
POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/pytest tests/test_ibkr_adapter.py tests/test_ibkr_historical.py \
  tests/test_ibkr_backtest_route.py -q --tb=short --disable-warnings
```

Result: **33 passed**.

Additional checks:

```bash
.venv/bin/python -m compileall -q app tests/test_ibkr_historical.py \
  tests/test_ibkr_backtest_route.py

POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 \
POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/pytest --collect-only -q
```

Collection result: **766 tests**, exactly 25 more than baseline.

## Final complete suite

```bash
POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 \
POSTGRES_DB=trading_workspace POSTGRES_USER=trading POSTGRES_PASSWORD=trading \
  .venv/bin/pytest -q --tb=short
```

Result: **766 passed, 0 failed, 0 skipped** in 789.80 seconds.

Comparison: 741 → 766 collected (**+25**); 740 → 766 passing; the baseline timing
failure did not recur; zero new regressions.

## Mocked IBKR behavior covered

- A successful IBKR response becomes canonical `Candle` objects.
- Intraday timestamps are timezone-aware UTC; IBKR daily dates are interpreted in the configured market timezone before conversion to UTC.
- Multiple serial one-day one-minute chunks merge in ascending order.
- Identical overlap-boundary bars deduplicate; conflicting duplicates fail as incomplete data.
- Final provider reads honor exact `[start,end)` bounds and filter bars on both sides.
- Acquisition includes the primary `1m` replay range, the configured prior-session `1m` premarket span, and the configured `1d` Daily Levels/ATR/RVOL span.
- One-minute requests use `TRADES`, `useRTH=False`; daily requests use `TRADES`, `useRTH=True`.
- Success, connect failure, unresolved contract, missing permission, pacing rejection, timeout, disconnect, zero primary bars, and malformed timestamp paths all disconnect the isolated adapter.
- The application watches IBKR error/disconnect events in addition to exceptions; tests cover permission and pacing events that accompany or replace ordinary exceptions.
- Route validation covers empty symbols, naïve timestamps, reversed ranges, over-24-hour ranges, exactly 24 hours, and missing/malformed/negative/colliding `IBKR_BACKTEST_CLIENT_ID` values.
- Acquisition failure produces a stable HTTP error and leaves no `BacktestRunRecord` in real PostgreSQL.
- Successful route integration replays through the real runner and persists honest IBKR `data_version` metadata.
- A registered IBKR connection produces the same pre-replay `409` guard as Finnhub/Polygon, including on the unchanged fixture route.
- No subscription, tick callback, order placement, or order cancellation method is invoked by acquisition.
- Existing `POST /backtest/run` tests remain unchanged and passed in the complete suite.

## Not live-verified

The sandbox did not connect to a real Gateway/TWS instance or IBKR account. Therefore it
did not empirically verify account-specific historical subscriptions, the exact error
text returned by that account, farm availability, real pacing behavior, or real bar
coverage. Those boundaries are handled from installed `ib_async` behavior and official
IBKR error/documentation semantics, then exercised with mocks.

## Saqib's live Gateway/TWS verification

1. Start paper IB Gateway (`4002`) or paper TWS (`7497`), log in, and enable API socket clients plus trusted `127.0.0.1`.
2. Confirm the account has the US-equity market-data subscriptions/permissions needed for historical `TRADES` bars.
3. Set `IBKR_HOST`/`IBKR_PORT`, keep the normal live-path `IBKR_CLIENT_ID`, and set an explicit different value such as `IBKR_BACKTEST_CLIENT_ID=2`.
4. Start the backend and confirm normal startup succeeds even when `IBKR_BACKTEST_CLIENT_ID` is temporarily blank; calling only the IBKR backtest route should then return the documented configuration `503`.
5. Restore the distinct historical client ID. Confirm `GET /broker/status`, `GET /finnhub/status`, and `GET /market-data/status` report disconnected before replay.
6. Use a liquid US stock and a short, completed historical interval first:

   ```bash
   curl -sS -X POST \
     "http://localhost:8000/backtest/run/ibkr?strategy_name=ORB&symbol=AAPL&start=2026-09-15T13%3A30%3A00Z&end=2026-09-15T14%3A00%3A00Z"
   ```

7. While acquisition runs, confirm Gateway/TWS shows a separate client ID and read-only API session. Confirm no market-data subscription or order is created.
8. After acquisition completes and replay begins, confirm that historical client disappears before the synchronous replay finishes.
9. Query `GET /intelligence/backtest-runs?run_id=<returned-run_id>` and verify `data_version` is `ibkr:TRADES:1m-ext:1d-rth`; inspect `/intelligence/strategy-outcomes?is_backtest=true&backtest_run_id=<run_id>` separately.
10. Repeat with a premarket-inclusive interval and inspect whether premarket-volume features populate when five prior sessions genuinely exist.
11. Connect the normal broker with `POST /broker/connect`; confirm either backtest route now returns `409`. Disconnect it afterward.
12. Test an account/symbol lacking historical permission and an invalid symbol; confirm neither request creates a `backtests` row.
13. Do not interpret a successful run as live-account verification of profitability or historical Context fidelity. Only OHLCV is IBKR-backed; point-in-time fundamentals/news remain absent.

## Expected HTTP examples

Success shape:

```http
HTTP/1.1 200 OK
{
  "run_id": "<uuid>",
  "sweep_id": "<uuid>",
  "outcomes_recorded": 0,
  "discarded_signals": []
}
```

Missing configuration:

```http
HTTP/1.1 503 Service Unavailable
{
  "detail": {
    "code": "ibkr_backtest_not_configured",
    "message": "IBKR_BACKTEST_CLIENT_ID is required for POST /backtest/run/ibkr. Set an explicit client ID that differs from IBKR_CLIENT_ID."
  }
}
```

Range over 24 elapsed hours:

```http
HTTP/1.1 422 Unprocessable Entity
{
  "detail": {
    "code": "invalid_backtest_request",
    "message": "The requested replay window exceeds the v1 maximum of 24 elapsed hours; the range is rejected and will not be clamped."
  }
}
```

Missing historical permission:

```http
HTTP/1.1 503 Service Unavailable
{
  "detail": {
    "code": "ibkr_historical_permission_denied",
    "message": "IBKR error <code>: <account-specific permission message>"
  }
}
```

Timeout:

```http
HTTP/1.1 504 Gateway Timeout
{
  "detail": {
    "code": "ibkr_historical_timeout",
    "message": "IBKR historical request timed out after 60s (<request label>)."
  }
}
```

Live-provider guard:

```http
HTTP/1.1 409 Conflict
{"detail":"Refusing to run a backtest: IBKR is currently connected in the live broker registry. ..."}
```

Zero usable primary bars:

```http
HTTP/1.1 400 Bad Request
{
  "detail": {
    "code": "ibkr_no_data",
    "message": "IBKR returned zero usable 1m TRADES bars for <symbol> in the exact interval [<start>, <end>)."
  }
}
```

## Clean-diff and archive validation

The changed-file footprint is compared with a fresh untouched archive of the exact base
commit, excluding only generated caches and the local virtual-environment symlink.
`ibkr-historical-backtest-provider.zip` is built from that explicit file list, not from
the whole worktree. Archive entries are rooted directly at the project root; `.git`,
virtual environments, caches, test output, databases, and untouched files are excluded.
