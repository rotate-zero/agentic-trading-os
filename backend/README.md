# Backend — Phase 2 Scaffold

FastAPI skeleton, Event Bus (two dispatch lanes), Market Clock, `DebounceScheduler`,
WebSocket Gateway, PostgreSQL + Alembic (plain Postgres, native monthly
partitioning on `candles` — no TimescaleDB, per
[`../docs/decisions/confirmed-decisions.md`](../docs/decisions/confirmed-decisions.md) #2).

Architecture reference: [`../docs/architecture/system-design.md`](../docs/architecture/system-design.md).
Exit criteria for this phase: [`../docs/roadmap/phase-roadmap.md`](../docs/roadmap/phase-roadmap.md).

## What's implemented vs. deliberately not

**Implemented:**
- FastAPI app (`app/main.py`) with lifespan-managed startup/shutdown
- Event Bus with critical/normal dispatch lanes (`app/event_bus/`)
- Market Clock (`app/core/market_clock.py`)
- `DebounceScheduler` shared utility (`app/core/debounce_scheduler.py`) — not wired to a
  real consumer yet, since Market State Engine and Position Monitor don't exist until
  Phase 5. Tested standalone.
- WebSocket Gateway + connection manager (`app/api/websocket/`)
- Alembic wired to plain PostgreSQL; initial migration creates `symbols` and a
  partitioned `candles` table
- Dev routes (`POST /dev/dummy-event`, `POST /dev/critical-event`) that prove events
  round-trip through the bus and out over WebSocket — the actual Phase 2 exit criterion

**Deliberately not implemented yet** (belongs to a later phase, per
[`phase-roadmap.md`](../docs/roadmap/phase-roadmap.md)):
- Broker adapters, Market Data Engine, Feature Engine (Phase 3-4)
- Any table beyond `symbols`/`candles` — `trades`, `positions`, `ai_decisions`,
  `feature_snapshots`, etc. get added by the module that actually populates them
- Frontend mock-data swap — this backend is ready to be pointed at, but the frontend
  still reads its existing mocks until that swap happens as a separate, reviewable change

## Running locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then get PostgreSQL running — pick whichever of these two you actually have available. Both end up satisfying the same `.env.example` values (`trading`/`trading`/`trading_workspace` on `localhost:5432`), so the rest of the steps below are identical either way.

**Option A — Docker (if installed):**
```bash
cd .. && docker compose up -d postgres
```

**Option B — local PostgreSQL install, no Docker:**
```bash
sudo service postgresql start   # if not already running

sudo -u postgres psql <<'SQL'
CREATE ROLE trading WITH LOGIN PASSWORD 'trading';
CREATE DATABASE trading_workspace OWNER trading;
\c trading_workspace
GRANT ALL ON SCHEMA public TO trading;
SQL
```
The `GRANT ALL ON SCHEMA public` step matters on Postgres 15+ — by default `CREATE` on the `public` schema is locked down even for the database's nominal owner, so skipping this makes Alembic's `CREATE TABLE` fail with a permissions error.

Then, either way:

```bash
# apply migrations
cd backend && alembic upgrade head

# verify (should show `symbols` and a partitioned `candles`)
PGPASSWORD=trading psql -h localhost -U trading -d trading_workspace -c '\dt'
PGPASSWORD=trading psql -h localhost -U trading -d trading_workspace -c '\d+ candles'

# run the API
uvicorn app.main:app --reload
```

## Verifying the Phase 2 round-trip

```bash
python scripts/verify_roundtrip.py
```

Run this in a second terminal while `uvicorn app.main:app --reload` is running in the first. It checks `/health`, then proves the full round trip — HTTP → Event Bus → WebSocket Gateway → client — on **both** dispatch lanes:
- normal lane: `POST /dev/dummy-event` → received on the `dev.ping` WS channel
- critical lane: `POST /dev/critical-event` → received on the `orders.status` WS channel

Exits non-zero with a clear message if anything's wrong (most commonly: uvicorn isn't running, or something's already bound to port 8000 — see the note below). Uses Python's `websockets` + `httpx` directly, so there's no separate CLI tool (`wscat`, etc.) to install — everything it needs is already in `requirements.txt`.

**If you get `[Errno 98] Address already in use`:** that's not a bug — it means a previous `uvicorn` is still running in another terminal. Either use that one (it's fine), or stop it first (`Ctrl+C` in its terminal, or `lsof -i :8000` to find and kill it) before starting a new one.

## Running tests

```bash
cd backend
pytest
```

`tests/test_event_bus.py` and `tests/test_debounce_scheduler.py` don't need a database —
they test the in-process bus and scheduler logic directly, including a test that
specifically proves a slow normal-lane subscriber can't delay a critical-lane event
(confirmed decision #9).
