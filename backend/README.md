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
  round-trip through the bus and out over WebSocket
- **`IBKRAdapter`** (`app/broker_adapters/`) — implements the full `BrokerAdapter`
  interface using `ib_async`. Streams live ticks (`on_tick`) and fetches historical
  bars (`get_historical`). `place_order`/`cancel_order` raise `NotImplementedError`
  on purpose — see the docstring on `BrokerAdapter` for why.
- **`IBKRIngestBridge`** (`app/services/ibkr_ingest.py`) — Phase-3-minimal bridge:
  publishes every tick as `PriceUpdated`, buckets ticks into 1-minute `CandleClosed`
  events. Explicitly not the real Market Data Engine (Phase 4) — gets replaced
  wholesale, not extended.
- `POST /broker/connect`, `/subscribe`, `/unsubscribe`, `/disconnect`, `GET /broker/status`
  for manual connection control (no auto-connect on app startup — see below)

**Deliberately not implemented yet** (belongs to a later phase, per
[`phase-roadmap.md`](../docs/roadmap/phase-roadmap.md)):
- Market Data Engine, Feature Engine, multi-symbol StateCache, persistence (Phase 4)
- Any table beyond `symbols`/`candles`
- Order placement — no route exists for it; only the Governor (Phase 5/6) should ever
  be able to trigger a real order
- Frontend mock-data swap — paused mid-implementation to prioritize the IBKR adapter;
  the plan (candles.ts only, via a `useLiveCandles` hook) is unchanged and now has a
  real data source to point at instead of the dev mock publisher

**What's verified vs. not, for the IBKR pieces specifically:**
- ✅ `ib_async` API signatures — verified by installing the library and introspecting
  real method signatures, not trusting memory
- ✅ Tick→candle bucketing logic — unit tested (`tests/test_ibkr_ingest.py`)
- ✅ The connect path genuinely attempts a real socket connection and fails cleanly
  (verified: got a real `ConnectionRefusedError` against `127.0.0.1:4002` with no
  Gateway running, surfaced as a clean HTTP 502, not a crash)
- ❌ An actual live connection to a running Gateway with a real IBKR account — this
  sandbox has no path to that. Has to happen on your machine.

## IBKR connection setup

You'll need **IB Gateway** (lighter than full TWS, standard for API-only use) running
and logged in before this backend can connect to it.

1. **Install IB Gateway** from IBKR's site, and log in once manually to set it up.
2. **Switch 2FA to IB Key, not SMS.** In IBKR Account Management → Settings → Secure
   Login System: set your 2FA method to IB Key (IBKR Mobile app push + biometric/passcode),
   not SMS. This isn't just a preference — it's what makes the setup below tolerable,
   since IB Key is a tap-to-approve, not a code you have to type into a script.
3. **Set Gateway to auto-restart, not auto-logoff**, in Gateway's Configure → Settings.
   Combined with IB Key, this means you'll need to approve a push notification roughly
   **once a week**, not once a day. This is a real reduction in friction — it is
   *not* a way to eliminate the 2FA step entirely. Nothing can do that; IBKR requires
   2FA for all accounts, no exceptions, and their own automation tooling (IBC) says so
   explicitly. Don't build toward or expect a fully unattended setup.
4. **Enable the API and set the port** in Gateway's Configure → Settings → API:
   check "Enable ActiveX and Socket Clients," add `127.0.0.1` to Trusted IPs, and note
   the port — default is `4002` for paper trading, `4001` for live. This backend
   defaults to `4002` (`.env`'s `IBKR_PORT`) — **paper, on purpose.** Switching to
   live trading is a deliberate `.env` change, not an accident of a default.
5. Optional but recommended for less manual clicking: [IBC](https://github.com/IbcAlpha/IBC)
   automates the username/password portion of Gateway's login (you still handle the
   IB Key tap yourself).

Once Gateway is running and logged in:

```bash
curl -X POST http://localhost:8000/broker/connect
curl -X POST "http://localhost:8000/broker/subscribe?symbol=NVDA"
curl http://localhost:8000/broker/status
```

Then subscribe to `market.tick` and `market.candle` on the `/ws` endpoint (same as the
Phase 2 dev routes) — real IBKR ticks now flow through the exact same Event Bus →
WebSocket Gateway pipeline the dummy events proved out in Phase 2. Nothing about that
pipeline changed; only the source feeding it did.

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
