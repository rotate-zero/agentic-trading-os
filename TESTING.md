# TESTING — LiveTickRelay / "Tick" fluidity (decision #72)

Unzip this directly at the project root (`agentic-trading-os/`) — every
path inside mirrors the real repo, so it overwrites the modified files and
adds the new ones in place.

## Files touched

**New:**
- `backend/app/services/live_tick_relay.py` — the relay itself
- `backend/tests/test_live_tick_relay.py` — 6 new tests

**Modified:**
- `backend/app/schemas/events/envelope.py` — new `EventType.PRICE_SNAPSHOT`
- `backend/app/schemas/events/market_data.py` — new `PriceSnapshot` model
- `backend/app/api/websocket/channels.py` — new `market.tick.snapshot` channel
- `backend/app/api/routes/market.py` — new `POST`/`GET /market/active-symbols`
- `backend/app/main.py` — wires `LiveTickRelay` into app startup/shutdown
- `backend/tests/conftest.py` — resets the new singleton between tests
- `frontend/src/types/workspace.ts` — new `SubWindowConfig.liveTick` field
- `frontend/src/state/WorkspaceContext.tsx` — `liveTick: false` added to all 9 existing config literals
- `frontend/src/hooks/useLiveCandles.ts` — new `liveTick` param + `_upsertLast` fix (see decision #72 for why the existing `CandleClosed` handler needed a fix too, not just a new branch)
- `frontend/src/components/sub-window/SubWindow.tsx` — passes `config.liveTick` through
- `frontend/src/components/sub-window/SubWindowMenu.tsx` — new "Tick" option in the Timeframe menu
- `docs/decisions/confirmed-decisions.md` — decision #72, full writeup
- `docs/architecture/system-design.md` — `PriceSnapshot` added to §10.3's contract table

## Backend

```bash
cd backend
pip install -r requirements.txt --break-system-packages   # if not already installed
pytest tests/test_live_tick_relay.py -v                    # the 6 new tests, isolated
pytest tests/ -q                                            # full suite against real local Postgres 16
```

Expect all of `test_live_tick_relay.py` to pass. I ran the full suite in a
sandbox with **no local Postgres available** — 161 passed, 21 failed (all
21 are the pre-existing DB-dependent tests, e.g. `test_daily_levels.py`,
`test_feature_engine.py`, failing on `connection to server ... Connection
refused` — nothing to do with this change). Against your real local
Postgres those should all pass as before; if anything outside
`test_live_tick_relay.py` fails there, that's worth flagging back to me.

## Frontend

```bash
cd frontend
npm install       # if not already installed
npx tsc -b 2>&1 | grep -v "GridPresetPicker"    # should print nothing
npx vite build                                    # should complete clean
```

Both ran clean in the sandbox (zero non-`GridPresetPicker` TypeScript
errors, `vite build` succeeded — 74 modules, no warnings beyond the usual
chunk-size notice).

## What I could NOT verify (flagging honestly, not glossing over it)

- **No real browser session** — same standing limitation as every other
  frontend change here. The "Tick" toggle's actual visual behavior against
  real Finnhub ticks hasn't been seen, only reasoned through against the
  exact code read from the live repo and proven at the unit-test level.
- **`POST /market/active-symbols` untested against a real running
  server** — reasoned correct, not yet exercised live. Try something like:
  ```bash
  curl -X POST http://localhost:8000/market/active-symbols \
    -H "Content-Type: application/json" \
    -d '{"symbols": ["NVDA", "AAPL"]}'
  ```
  then open a chart on one of those symbols, switch it to "Tick" via the
  Timeframe menu, and watch the last bar during live market hours.
- **No scanning process exists yet** to call `set_active_symbols`
  automatically — until Market Scanner is built, you set the active set
  manually via the endpoint above. A symbol's chart can be switched to
  "Tick" mode even if it's NOT in the active set; it just won't visibly
  do anything beyond normal 1m-close updates until it is.
