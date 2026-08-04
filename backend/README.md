# Backend — Phase 2/3

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
  interface (which now extends `MarketDataProvider` — see below) using `ib_async`. Streams live ticks (`on_tick`) and fetches historical
  bars (`get_historical`). `place_order`/`cancel_order` raise `NotImplementedError`
  on purpose — see the docstring on `BrokerAdapter` for why. Symbol qualification
  failures raise a clean `SymbolNotFoundError` instead of silently proceeding with an
  unresolved contract (found and fixed by reading `qualifyContractsAsync`'s actual
  source — it never raises on a bad symbol itself). Unexpected disconnects are logged
  loudly; auto-reconnect is explicitly left to Phase 4's Market Data Engine, not built
  here — see the docstring on `_on_disconnected`.
- **`TickIngestBridge`** (`app/services/tick_ingest.py`, renamed from `IBKRIngestBridge` —
  see `../docs/decisions/confirmed-decisions.md` #31) — Phase-3-minimal bridge:
  publishes every tick as `PriceUpdated`, buckets ticks into 1-minute `CandleClosed`
  events. Works for any `MarketDataProvider`, not just IBKR — shared by all three
  adapters. Explicitly not the real Market Data Engine (Phase 4) — gets replaced
  wholesale, not extended.
- `POST /broker/connect`, `/subscribe`, `/unsubscribe`, `/disconnect`, `GET /broker/status`
  for manual connection control (no auto-connect on app startup — see below)
- `GET /market/candles?symbol=&count=&timeframe=` — candle backfill via whichever
  provider currently holds the **historical role** (`app/services/broker_registry.py`
  — see below). **This is what the frontend actually calls now** — the mock-swap
  (`frontend/src/hooks/useLiveCandles.ts`) is done, not just planned.
- `POST /market/subscribe?symbol=` — tells whichever provider holds the **streaming
  role** to start streaming a symbol. Provider-agnostic on purpose: the frontend calls
  this one route regardless of whether Finnhub, Polygon, or IBKR is actually
  connected. Provider-specific subscribe routes (`/finnhub/subscribe`,
  `/market-data/subscribe`, `/broker/subscribe`) still exist for manual/debug use.
- **`MarketDataProvider` / `BrokerAdapter` split** (`app/broker_adapters/base.py`) —
  `BrokerAdapter` now extends `MarketDataProvider`, so a pure data-only vendor can
  implement just the data-streaming subset without pretending to support order
  placement. `IBKRAdapter` is unaffected behaviorally — same interface, now assembled
  via inheritance. See `../docs/decisions/confirmed-decisions.md` #28.
- **`PolygonAdapter`** (`app/broker_adapters/polygon_provider.py`) — implements
  `MarketDataProvider` only (no execution, correctly). Built around the free/Basic
  tier's real constraints (15-min delayed, 5 REST calls/min, **no WebSocket at this
  tier at all**) rather than assuming a real-time feed — see
  `../docs/decisions/confirmed-decisions.md` #30 for the full design reasoning.
  `on_tick()` is backed by rate-limited REST polling
  (`app/core/rate_limiter.py`), not a push stream. **Auto-connects on app startup**
  if `POLYGON_API_KEY` is set (soft-fail if not — see "Polygon.io connection setup"
  below). Serves the **historical** role always; also serves **streaming** as a
  fallback if Finnhub isn't connected.
- **`FinnhubAdapter`** (`app/broker_adapters/finnhub_provider.py`) — implements
  `MarketDataProvider` only. Genuine real-time WebSocket streaming (built directly on
  `websockets`, since `finnhub-python` ships no WS client at all — confirmed by
  inspecting the package), but `get_historical()` raises the new
  `HistoricalDataUnavailableError` — Finnhub's free tier paywalls historical stock
  candles (confirmed 403, not assumed) — see
  `../docs/decisions/confirmed-decisions.md` #32. Auto-connects on startup if
  `FINNHUB_API_KEY` is set. Always takes the **streaming** role when connected —
  never historical.
- **Two-role `broker_registry`** (`streaming` / `historical`, not one shared slot —
  see `../docs/decisions/confirmed-decisions.md` #33) — Finnhub and Polygon need to be
  connected at once now, each doing the job it's actually good at.
  `take_over_streaming()` safely hands off the streaming role without disconnecting a
  provider still needed for historical. `IBKRAdapter`, once connected, takes over
  both roles (it's capable of both) — that's always a deliberate manual action, so
  it's allowed to override whatever auto-connected at startup.
- `POST /market-data/connect`, `/subscribe`, `/unsubscribe`, `/disconnect`,
  `GET /market-data/status` (Polygon) and `POST /finnhub/connect`, `/subscribe`,
  `/unsubscribe`, `/disconnect`, `GET /finnhub/status` (Finnhub) — same pattern as
  `/broker/*`, one route file per provider. `main.py`'s auto-connect calls these same
  modules' shared `connect_polygon()`/`connect_finnhub()` functions rather than
  constructing adapters inline — an earlier version didn't, and auto-connect silently
  desynced from these routes' own state (see `../docs/decisions/confirmed-decisions.md`
  #34, found via an actual startup test, not caught by unit tests).

**Deliberately not implemented yet** (belongs to a later phase, per
[`phase-roadmap.md`](../docs/roadmap/phase-roadmap.md)):
- Market Data Engine, Feature Engine, multi-symbol StateCache, persistence (Phase 4)
- Any table beyond `symbols`/`candles`
- Order placement — no route exists for it; only the Governor (Phase 5/6) should ever
  be able to trigger a real order

**What's verified vs. not, for the IBKR pieces specifically:**
- ✅ `ib_async` API signatures — verified by installing the library and introspecting
  real method signatures, not trusting memory
- ✅ Tick→candle bucketing logic — unit tested (`tests/test_tick_ingest.py`)
- ✅ Symbol-qualification failure path — unit tested by simulating
  `qualifyContractsAsync`'s real "return None" failure signal, both in the adapter
  directly and through the `/broker/subscribe` and `/market/candles` routes
- ✅ Disconnect handler — unit tested by firing the same `eventkit` event ib_async
  fires internally on a real drop
- ✅ The connect path genuinely attempts a real socket connection and fails cleanly
  (verified: got a real `ConnectionRefusedError` against `127.0.0.1:4002` with no
  Gateway running, surfaced as a clean HTTP 502, not a crash)
- ❌ An actual live connection to a running Gateway with a real IBKR account — this
  sandbox has no path to that. Has to happen on your machine.
- ❌ Auto-reconnect after a disconnect — not built. Deliberately deferred to Phase 4's
  Market Data Engine (`ConnectionManager`), not pulled forward into this adapter.

**What's verified vs. not, for Finnhub specifically:**
- ✅ `finnhub-python` ships no WebSocket client — confirmed by inspecting the
  installed package, not assumed
- ✅ Free tier's historical-candle paywall — confirmed via a real GitHub issue from
  someone hitting the actual 403, not from a tutorial that might predate the change
- ✅ Full connect → subscribe → receive-trade → ignore-ping → unsubscribe → disconnect
  flow — verified against a **real local WebSocket server** mimicking Finnhub's exact
  protocol (this sandbox can't reach `wss://ws.finnhub.io` — not in its network
  allowlist — so this is the strongest verification available short of a real key)
- ✅ Message parsing edge cases — multiple trades in one message, malformed entries,
  ping/unknown message types — all unit tested
- ❌ Behavior against the real Finnhub servers with a real key — has to happen on your
  machine

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

## Polygon.io connection setup

Much simpler than IBKR's — Polygon is just an API-key-authenticated cloud service, no
desktop app, no 2FA handshake per session.

1. Set `POLYGON_API_KEY` in `.env`. That's it — no other setup step.
2. Start the backend. It auto-connects on startup (check the logs for
   `Polygon auto-connected on startup`) — no manual `POST /connect` needed, unlike IBKR.
3. **Read this before expecting live data:** the free/Basic tier gives **15-minute
   delayed** data, 5 REST calls/minute, and **no WebSocket access at all**. This
   adapter polls (`app/core/rate_limiter.py`-throttled) rather than streams — see
   `../docs/decisions/confirmed-decisions.md` #30 for the full reasoning. Every tick's
   timestamp reflects Polygon's actual (delayed) bar time, not "now," so what you see
   on a chart fed by this will honestly lag real time by ~15 minutes if Polygon is your
   only connected source. This is expected behavior on this tier, not a bug.
4. Polygon always serves the **historical** role (`GET /market/candles`) regardless of
   what else is connected. It also serves **streaming** as a fallback if Finnhub isn't
   configured — check `GET /market-data/status`'s `role` field to see which.
5. Subscribe to a symbol:
   ```bash
   curl -X POST "http://localhost:8000/market-data/subscribe?symbol=NVDA"
   curl http://localhost:8000/market-data/status
   ```
6. No `POLYGON_API_KEY` set → the app boots fine anyway and just skips Polygon
   entirely (check logs for `POLYGON_API_KEY not set — skipping Polygon auto-connect`).
   An optional data source failing to configure must never block the whole app.

## Finnhub connection setup

Same auto-connect pattern as Polygon — an API key, no desktop app.

1. Set `FINNHUB_API_KEY` in `.env`.
2. Start the backend. It auto-connects on startup (check logs for
   `Finnhub auto-connected on startup`) and **takes the streaming role over Polygon**
   if both are configured (Finnhub is genuinely real-time; Polygon's streaming is only
   ever a delayed fallback). Polygon keeps serving historical either way — connecting
   Finnhub never disconnects Polygon unless Polygon has no other job to do.
3. **This one actually is real-time** — free-tier WebSocket streaming, not polling,
   not delayed. What it can't do: historical candles for stocks — `GET /market/candles`
   will 501 if Finnhub is somehow the only thing registered as historical (it never
   registers as historical itself, so this shouldn't happen in practice; see
   `../docs/decisions/confirmed-decisions.md` #32 for why).
4. Subscribe to a symbol:
   ```bash
   curl -X POST "http://localhost:8000/finnhub/subscribe?symbol=NVDA"
   curl http://localhost:8000/finnhub/status
   ```
   Then watch `market.tick`/`market.candle` on `/ws` — same pipeline as everything
   else, genuinely live this time.
5. No `FINNHUB_API_KEY` set → skips cleanly, same as Polygon. If both keys are set,
   Polygon's auto-connect logs will say "historical only — Finnhub already streaming"
   instead of claiming the streaming-fallback role.

## Running locally


**Python 3.10–3.13 recommended.** Python 3.14 works too, but only with `ib_async>=2.1`
(already what `requirements.txt` pins) — earlier `ib_async` versions depend on a library
that crashes on import under 3.14 (`RuntimeError: There is no current event loop`, a
Python 3.14 behavior change, not a bug in this codebase — see confirmed decision #29
if you hit this with an older lockfile or cached install).

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

No extra setup needed — every test file below runs with just `pip install -r requirements.txt`, no database and no live IBKR connection required. `pytest` alone picks up all of them.

| File | What it verifies |
|---|---|
| `test_event_bus.py` | Event Bus pub/sub, including a test that specifically proves a slow normal-lane subscriber can't delay a critical-lane event (confirmed decision #9) |
| `test_debounce_scheduler.py` | The shared min/max-interval update-policy utility (confirmed decision #10) |
| `test_ibkr_adapter.py` | `IBKRAdapter`'s pure logic: `_duration_str`/`_bar_size_for` helpers, ABC compliance against both `MarketDataProvider` and `BrokerAdapter`, a minimal fake proving `MarketDataProvider` is satisfiable with zero execution methods (confirmed decision #28), the symbol-qualification-failure path (simulates `qualifyContractsAsync`'s real `None`-on-failure behavior), and the disconnect handler (fires the same `eventkit` event `ib_async` fires internally on a real drop) |
| `test_tick_ingest.py` (renamed from `test_ibkr_ingest.py`, confirmed decision #31) | Tick→candle bucketing: same-minute ticks aggregate into one bucket, a minute rollover finalizes and publishes it, multiple symbols bucket independently — same tests, now proven provider-agnostic rather than IBKR-specific |
| `test_market_routes.py` | `GET /market/candles`, `POST /market/subscribe` (the generic, provider-agnostic route the frontend actually uses), and `POST /broker/subscribe`'s error paths — not-connected → 400, unresolvable symbol → 400, unsupported timeframe → 400, plus a successful-subscribe happy path. Uses a hand-built fake adapter, not a real `IBKRAdapter`, so no network access happens |
| `test_rate_limiter.py` | The shared token-bucket rate limiter (confirmed decision #30): calls within budget don't wait, a call beyond budget genuinely waits for the window to clear, concurrent acquires don't race past the limit |
| `test_polygon_provider.py` | `PolygonAdapter`'s pure logic, all via monkeypatched `get_aggs` (no real API key or network access): timeframe mapping, ABC compliance, `get_historical`'s bad-symbol handling (empty-list-not-exception, same trap as `qualifyContractsAsync`), and the polling dedup logic — a new bar fires `on_tick`, the same bar polled again doesn't, a client exception doesn't kill the loop |
| `test_finnhub_provider.py` | `FinnhubAdapter`'s pure logic: missing-key handling, ABC compliance, `get_historical` correctly raising `HistoricalDataUnavailableError` (confirmed decision #32), and WS message parsing — ping/unknown types ignored, single and multi-trade messages parsed, malformed entries skipped without crashing the batch |
| `test_broker_registry.py` | The two-role registry's coordination logic (confirmed decision #33) directly, not just incidentally through route tests: `take_over_streaming()` disconnects the previous streaming provider by default, but does NOT disconnect one still needed for the historical role; a no-op takeover (same provider) doesn't self-disconnect; `get_all_active_providers()` deduplicates by identity |
| `conftest.py` | Not a test file — an autouse fixture resetting the app's module-level singletons (Event Bus, connection manager, Gateway, broker registry) between tests. Needed because those singletons are cached for the process lifetime, but `pytest-asyncio` gives each test its own event loop; without this reset, a second `TestClient(app)` in a later test reuses queues bound to an already-dead loop |

**What these tests deliberately don't cover:** an actual live IBKR connection. `test_ibkr_adapter.py`'s interface-compliance test constructs a real `IBKRAdapter` but never calls `connect()` — that requires a running IB Gateway and a real account, which only exists on your machine, not here. See "IBKR connection setup" above for that verification path, and the smoke checks below.

### Manually exercising `/market/candles`

Once connected (`POST /broker/connect` then `POST /broker/subscribe?symbol=NVDA` from the section above):

```bash
curl "http://localhost:8000/market/candles?symbol=NVDA&count=50&timeframe=1m"
```

Returns `{"symbol": "NVDA", "candles": [...]}` — the same shape the frontend's
`useLiveCandles` hook actually consumes now, not just a planned one. Before
connecting, this same call should return a clean `400` ("Not connected"), not a
crash or a 500 — that's the behavior `test_market_routes.py` checks automatically;
this is just how to see it happen for real.

## Frontend

`frontend/` reads live data through this backend now (`useLiveCandles`,
`useLatestPrices`, `services/websocket-client.ts`, `services/api-client.ts`) — no
config needed if the backend's on `localhost:8000` and the frontend dev server's on
`localhost:5173` (both are the defaults on each side, including CORS). Override via
`frontend/.env`'s `VITE_API_BASE_URL`/`VITE_WS_BASE_URL` if not.

```bash
cd frontend
npm install
npm run dev
```

Verified before shipping: a full `tsc -b && vite build` succeeds, and the actual
CORS headers + error-response JSON shape were checked against a real running
backend (not assumed to match what the frontend's error handling expects).

**One pre-existing, unrelated bug worth knowing about:** `GridPresetPicker.tsx`
references a `GRID_PRESETS` export and `preset`/`setPreset` context fields that don't
exist anywhere in the codebase — confirmed via `git diff` that this wasn't touched by
any of the backend/data-provider work, and it's not imported by anything else either,
so it's dead code rather than something actively broken at runtime. Only `tsc -b`'s
full-project type-check catches it (`vite build` alone, and `npm run dev`, won't).
Worth deciding whether to finish it or delete it — not touched here since it's out of
scope for a data-source swap.

