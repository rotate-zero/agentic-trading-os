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

# start Postgres (from repo root)
cd .. && docker compose up -d postgres

# apply migrations
cd backend && alembic upgrade head

# run the API
uvicorn app.main:app --reload
```

## Verifying the Phase 2 round-trip

1. Open a WebSocket client (e.g. `wscat -c ws://localhost:8000/ws`) and subscribe:
   ```json
   {"action": "subscribe", "channel": "dev.ping"}
   ```
2. In another terminal: `curl -X POST http://localhost:8000/dev/dummy-event -H 'Content-Type: application/json' -d '{"message":"hello"}'`
3. The WebSocket client should receive the event on the `dev.ping` channel — proving
   HTTP route → Event Bus (normal lane) → WebSocket Gateway → client.
4. Same idea for the critical lane: `curl -X POST http://localhost:8000/dev/critical-event`,
   subscribed to channel `orders.status`.
5. `GET /health` reports current market session and both lanes' queue depths.

## Running tests

```bash
cd backend
pytest
```

`tests/test_event_bus.py` and `tests/test_debounce_scheduler.py` don't need a database —
they test the in-process bus and scheduler logic directly, including a test that
specifically proves a slow normal-lane subscriber can't delay a critical-lane event
(confirmed decision #9).
