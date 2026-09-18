# TESTING — pending decision (temp id: `real-market-data-backtest-attempt`) — first attempted real-market-data BacktestRunner execution, blocked by environment

Docs-only delivery — no `backend/` or `frontend/` files touched, so no `pytest` run or `npx tsc -b`/`npx vite build` applies. What follows is the verification trail for the empirical claims this delivery's docs make, since the entire value of this delivery is that those claims are real, not asserted.

## Pre-work

- Fresh `git clone --depth 1` at task start: `4f5f32b8444fbd5e384a604f8ea8add5740b60a9`.
- Read, in order: this task's own prompt (hard boundary, decision-number rule); `docs/decisions/INDEX.md`'s tail and `docs/decisions/confirmed-decisions.md`'s tail in full (found a **six-way** collision on next-available-143, not the two-way the task's own framing described — corrected explicitly, not silently); D4's row in `docs/architecture/strategy-engine-open-decisions.md`; `docs/api/routes/backtest.py`, `backend/app/backtest_runner/runner.py`, `docs/architecture/backtest-runner-design.md` in full.
- `backend/.env.example` read directly to confirm what real-data configuration exists and what's actually required (`IBKR_HOST`/`IBKR_PORT`/`IBKR_BACKTEST_CLIENT_ID`, `FINNHUB_API_KEY`, `POLYGON_API_KEY` — none set).

## Environment build (real, not simulated)

- `apt-get install postgresql postgresql-contrib` — PostgreSQL 16 installed natively; the repo's own `docker-compose.yml` Postgres image was not used since no container registry is reachable from this sandbox's network allowlist.
- `pg_ctlcluster 16 main start`; `CREATE USER trading WITH PASSWORD 'trading' SUPERUSER`; `CREATE DATABASE trading_workspace OWNER trading` — exact convention from `ways-of-working.md`.
- Python venv, `pip install -r backend/requirements.txt` — clean install, no errors, no version conflicts.
- `backend/.env` copied from `.env.example`, `IBKR_BACKTEST_CLIENT_ID=2` set (required, no default per that file's own comment).
- `alembic upgrade head` — clean run, `0001` through `0010`, zero errors. Confirms this session's schema matches decisions #140/#141's real current head, not an older or divergent one.
- Direct query confirmed pre-attempt state: `strategy_outcomes` 0, `backtests` 0, `candles` 0 (`symbols` had 6 rows, from the scanner-universe seed migration only — unrelated to backtesting).
- `uvicorn app.main:app` started as a real detached process (`setsid`, to survive across tool calls) against this real database. Startup log confirmed: `FINNHUB_API_KEY not set`, `POLYGON_API_KEY not set` — no live auto-connect attempted.

## The attempt itself

- `GET /finnhub/status`, `GET /market-data/status`, `GET /broker/status` — all three confirmed `connected: false` immediately before the real call, so decision #132's live-data guard would not block it for the wrong reason.
- `POST /backtest/run/ibkr?strategy_name=ORB&symbol=AAPL&start=2026-09-15T13:30:00+00:00&end=2026-09-15T15:30:00+00:00` — a real 2-hour window inside the 24-hour cap, a real recent trading day.
- Response: `HTTP 503`, `{"detail":{"code":"ibkr_connection_unavailable","message":"Could not connect to IB Gateway/TWS at 127.0.0.1:4002: [Errno 111] Connection refused"}}` — captured verbatim, not paraphrased, in `backtest-runner-design.md`'s new as-built note.
- Direct TCP check independent of the app: `/dev/tcp/127.0.0.1/4002` — `Connection refused`, confirming the failure is a genuinely unreachable Gateway, not an application-level misconfiguration.

## Ruling out alternatives, not assuming them unavailable

- `curl -D - https://api.polygon.io/...` and `https://finnhub.io` — both `403`, `x-deny-reason: host_not_allowed`, confirming this sandbox's network egress allowlist has no market-data-provider domain in it (checked against the actual configured allowlist, not inferred from a prior belief).
- `SELECT count(*) FROM candles WHERE ...` — confirmed 0 real live-recorded candles exist anywhere in this database to substitute as a "real data" source instead.
- Grepped `backend/app/api/routes/` for any route other than `POST /backtest/run/ibkr` that constructs a `BacktestRunner` from a non-fixture provider — none exists.

## Post-attempt verification

- Table counts re-checked after the failed attempt: `strategy_outcomes` 0, `backtests` 0 — unchanged, confirming the failed call left no partial or misleading row (matches `backtest.py`'s own documented behavior: acquisition failure precedes `BacktestRunRecord` creation).
- `diff -rq` against a freshly re-pulled, untouched second clone (immediately before writing any doc) confirmed zero drift on `main` since task start — same commit both times, ruling out a parallel-session collision risk.
- Backend process stopped (`pkill`) before packaging; the local Postgres instance and its data are local to this sandboxed session only and are not part of this delivery's footprint.
- `diff -rq` against a third freshly re-pulled clone, immediately before packaging: confirms the only files touched anywhere in the repo are `docs/architecture/backtest-runner-design.md`, `docs/architecture/strategy-engine-open-decisions.md`, `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md`, plus this file and `CHANGES.md` — explicitly including zero changes under `backend/app/feature_engine/`, `backend/app/trading_intelligence/level_interaction_engine.py`, `backend/app/market_state_engine/`, and their five named test files (this task's own hard boundary), and zero changes anywhere else under `backend/` or `frontend/`.
