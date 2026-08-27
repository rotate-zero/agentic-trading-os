# TESTING — Scanner: RVOL-only scoring, editable universe, top-8, collapsible panel

Unzip at project root. **Requires a new migration** (`alembic upgrade
head`) — this delivery adds `scanner_universe_symbols` (migration 0004).

This overwrites: `backend/app/core/config.py` (gap/session-change
weights now 0.0 — RVOL-only scoring), `backend/app/db/base.py` (registers
the new model), `backend/app/scanner/universe.py` (adds `DbUniverseProvider`
+ add/remove/list functions — `StaticUniverseProvider`/`TEST_UNIVERSE`
unchanged), `backend/app/api/routes/scanner.py` (adds universe endpoints
+ `top_n`), `frontend/src/types/workspace.ts` + `WorkspaceContext.tsx`
(adds `scannerCollapsed`/`scannerWidthPx`), `frontend/src/services/api-client.ts`,
`frontend/src/components/scanner/ScannerPanel.tsx`, `frontend/src/App.tsx`.
Adds: `backend/app/models/scanner.py`, `backend/alembic/versions/0004_scanner_universe.py`,
`backend/tests/test_scanner_universe.py`, `frontend/src/hooks/useScannerUniverse.ts`.

## 1. Run the migration first

```bash
cd backend
alembic upgrade head
```

Seeds `scanner_universe_symbols` with the same 6 placeholder symbols
(`AAPL MSFT NVDA AMD TSLA SPY`) as before — now a real, editable table
instead of a hardcoded Python list.

## 2. Backend tests

```bash
pytest tests/test_scanner.py tests/test_scanner_runner.py tests/test_scanner_universe.py -v
```

14 tests. The new `test_scanner_universe.py` (5 tests) runs against your
**real** Postgres — same convention `test_feature_engine.py`'s DB tests
already use (a distinctively-named test symbol, explicit cleanup, no
mocking). All 14 already run and passing against a freshly-installed
Postgres 16 instance before this was sent — including one bug caught and
fixed in the process: my first draft test ticker didn't satisfy the new
format-validation rule it was supposed to be testing around. Fixed by
picking a format-valid placeholder, not by loosening validation.

## 3. Backend routes, manually

```bash
uvicorn app.main:app --reload
```

```bash
curl http://127.0.0.1:8000/scanner/universe
curl -X POST http://127.0.0.1:8000/scanner/universe -H "Content-Type: application/json" -d '{"symbol": "nflx"}'
curl -X POST http://127.0.0.1:8000/scanner/universe -H "Content-Type: application/json" -d '{"symbol": "toolongname"}'   # expect 400
curl -X DELETE http://127.0.0.1:8000/scanner/universe/NFLX
curl "http://127.0.0.1:8000/scanner/state?top_n=3"
```

All of the above already run against a real server during verification — exact
same commands, all behaved as shown.

## 4. Frontend, visually

```bash
cd frontend && npm run dev
```

- The Scanner panel now has a **«** / **»** collapse toggle and a
  drag-resize handle on its left edge — same behavior as the Feature
  Engine panel next to it.
- Two tabs inside: **Results** (top 8, ranked — RVOL is the only bolded
  chip now, since that's the only thing currently driving score; gap/day/ATR
  still show when available but are muted) and **Universe** (view the
  current list, remove a symbol with the **×**, add one via the text
  input at the bottom).
- Adding an invalid symbol (numbers, >5 letters, etc.) shows the
  backend's exact rejection reason inline, not a generic error.

`tsc -b` and `vite build` both run clean against the full tree with
these changes (only the pre-existing `GridPresetPicker` errors, decision
#35 — nothing new).

## On the RVOL calculation question

Not a testable item, but worth restating here since it came up in the
same conversation as this delivery: RVOL = `session_volume /
(avg_daily_volume × elapsed_minutes/total_session_minutes)`, computed
**only during the regular session** (9:30am–4:00pm ET) — premarket
volume is not part of `session_volume` at all in the current
implementation, and RVOL is honestly absent (not zero, not estimated)
before the 9:30am open. See `app/feature_engine/indicators/rvol.py` and
`_update_rvol` in `engine.py` for the exact logic.
