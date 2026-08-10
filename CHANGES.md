# Self-recorded intraday history (closes the "no history before the first live bar" gap)

Copy these into your repo root, overwriting the existing paths. Full
reasoning is in `docs/decisions/confirmed-decisions.md` #43; the
architecture note is in `docs/architecture/system-design.md` §4.2.

**Docs note:** same as last drop — `docs-decision-43.patch` (a unified
diff of just the two doc files) is included as a safer alternative to
copying the full files, in case either has moved since this zip was
built. Apply with `git apply docs-decision-43.patch` from the repo root
instead, if you'd rather.

## ⚠️ Setup required — this does nothing until you do this once

The `candles`/`symbols` tables have existed since Phase 2 but nothing
ever wrote to them. If you don't already have a local Postgres running
matching `backend/app/core/config.py`'s defaults (`localhost:5432`,
db `trading_workspace`, user/password `trading`/`trading`) — WSL Ubuntu,
so this is the same `apt-get` you'd expect:

```bash
sudo apt-get update
sudo apt-get install -y postgresql
sudo service postgresql start
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"
sudo -u postgres psql -c "CREATE USER trading WITH PASSWORD 'trading' SUPERUSER;"
sudo -u postgres psql -c "CREATE DATABASE trading_workspace OWNER trading;"
```

Then, from `backend/` (with your venv activated):

```bash
alembic upgrade head
```

If you already have Postgres running with different credentials, either
match the defaults above or override via env vars
(`POSTGRES_HOST`/`PORT`/`DB`/`USER`/`PASSWORD` — see `core/config.py`).

**If you skip this:** the app still boots and works exactly as before —
every DB call is wrapped and soft-fails to "nothing recorded yet," logged
not raised (verified by literally stopping Postgres and re-running
everything, see #43's verification note). You just won't get the
history-persists-across-reconnects behavor until it's set up.

## What changed and why

**The actual bug:** not a bug — a documented, unbuilt piece of the
architecture. `system-design.md` §4.2 has said "persist candles via a
write-behind recorder" since it was written; the `candles` table's own
migration TODO said outright this needed doing "before Market Data
Engine is actually writing candles." Nothing had ever subscribed to
`CandleClosed` to do it. Every sub-window's history only ever existed
for as long as that specific tab had been open and listening live.

- `backend/app/services/candle_recorder.py` — **new.** Subscribes to
  `CandleClosed`, writes every closed `1m` candle to Postgres. Doesn't do
  the write inside the Event Bus callback itself (would block live price
  fan-out for every other subscriber — see the file's own docstring for
  why) — pushes to an in-memory queue instead, a separate background task
  drains it via `asyncio.to_thread`.
- `backend/app/db/partitions.py` — **new.** Auto-creates the current +
  next month's partition before every write. The original migration only
  seeded July/August 2026 — without this, writes would silently start
  failing the moment September arrives, which is 3 weeks away.
- `backend/app/services/candle_store.py` — **new.** Read side —
  `get_recorded_candles()`, called by the route below.
- `backend/app/api/routes/market.py` — `GET /market/candles` now checks
  self-recorded data FIRST, before ever reaching Polygon. For `1m`
  specifically this is now the primary path, not a fallback — Polygon's
  free tier structurally can't serve minute-level data at all (see below).
- `backend/app/main.py` — `CandleRecorder` starts/stops in `lifespan()`,
  same pattern as last drop's `TickIngestBridge.stop()`.
- `backend/app/models/market_data.py` — unrelated bug found by this being
  the first code to ever actually INSERT into `candles`: `Candle.id`
  didn't declare `Identity()`, even though its own migration does. Fixed
  to match — SQLAlchemy was warning about it, not silently corrupting
  anything, but worth fixing now that it's finally exercised.
- `backend/tests/test_candle_recorder.py` — **new.** Integration tests
  against a real local Postgres (write → read back, duplicate-close
  doesn't double-write). Auto-skips (not fails) if Postgres isn't
  reachable — verified both ways.
- `backend/tests/test_market_routes.py` — one new test proving the route
  itself serves self-recorded data with zero provider connected; a couple
  of existing tests' fixture ticker names shortened to fit
  `symbols.ticker VARCHAR(16)` (found because my own first draft of the
  new test tripped over it).

  **Verified:** real local PostgreSQL 16, not mocked — installed,
  migrated, used for every claim above, then torn down. Full suite
  passes both with Postgres up (69/69) and down (65 pass, 4 skip, nothing
  fails or crashes). A real `uvicorn` process with the DB down still
  boots and answers with a clean 400, not a crash. **Not verified:** the
  live-market-hours version of your original report — today's Sunday,
  markets closed — so this is real-DB-test-level verification, not a
  click-through with the market actually open.

## Your two questions, answered

**"Is running 2 APIs at once causing this?"** No — Finnhub/Polygon role
separation isn't a coordination bug and isn't what this fixes. The gap
was a capability gap in whichever provider held the historical role:
Polygon's free tier can't serve `1m` data at any window, full stop
(confirmed against Polygon's now-Massive.com current docs — they
rebranded sometime in 2026, after my training cutoff). The fix wasn't
"run them differently," it's "stop depending on either one for data
neither can provide, and self-supply it from what's already flowing
through the pipeline."

**The "2 years vs 1 Day bars" mismatch:** expected, not a bug. Massive's
free ("Stocks Basic") plan is end-of-day/daily-only. The "2 years" figure
refers to how far back that DAILY data goes, not that minute bars are
included.

## Known, deliberately deferred

`polygon_provider.py`'s `if not aggs: raise SymbolNotFoundError` still
conflates "invalid symbol" with "authorized but zero trades in this
window" (e.g. a weekend query) — real bug, lower priority now that
self-recorded data is the primary `1m` path and Polygon's only ever a
last-resort fallback. Flagged in confirmed-decisions #43, not fixed here
to keep this drop focused.
