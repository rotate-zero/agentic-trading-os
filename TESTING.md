# TESTING — pending decision (temp id: `broker-connection-panel`) — IBKR broker connection management surfaced as a new panel

Frontend-only delivery. No `backend/` files touched, so no backend `pytest` run applies — but all five routes this delivery surfaces were exercised live against a real, locally-run instance of this exact backend (see "Live route verification" below), not just read from source or reasoned about.

## Pre-work verification

- Fresh tarball pull (`codeload.github.com/.../refs/heads/main`) at task start.
- Read, in order: `docs/decisions/README.md`, `INDEX.md`'s tail; `backend/app/api/routes/broker.py` in full including its module docstring; `backend/app/broker_adapters/base.py`'s `BrokerAdapter`/`SymbolNotFoundError` docstrings; `backend/app/broker_adapters/ibkr_adapter.py`'s module docstring plus `connect()`/`disconnect()`/`subscribe()`/`unsubscribe()`/`is_connected()`/`_qualify()`/`_on_disconnected()`; `frontend/src/App.tsx` in full; `frontend/src/components/scanner/ScannerPanel.tsx` in full as structural template; `frontend/src/services/api-client.ts`'s existing wrapper conventions (all exports enumerated via grep); `frontend/src/hooks/useBacktestRun.ts` and `useContextSnapshot.ts` for the trigger/poll-reasoning references named in this task's own prompt.
- Mid-task re-pull (the user's own "git is updated" signal) surfaced that the parallel Finnhub/Polygon `data-feed-status-indicator` delivery had already landed on `main`. Re-read the new/changed files it introduced (`App.tsx`'s diff, `api-client.ts`'s diff, its own `CHANGES.md`/`TESTING.md`/decision-log entry, `system-design.md` §4.2's new note) before writing any code of my own, to build on top of the current state rather than a stale one.

## Immediately before writing/packaging

- Three-source decision-number check (`INDEX.md` tail, `confirmed-decisions.md` tail, `docs/decisions/archive/` file list): all three agree the latest real number is #142; the `data-feed-status-indicator` entry immediately above mine already identified 143 as next-available at its own packaging time, and nothing has moved since — this delivery's own PENDING entry states the same number for the same reason, not assumed to be a re-derivation.
- Re-checked `App.tsx` and `api-client.ts` for overlap with the already-landed parallel delivery: confirmed disjoint by inspection (that delivery edits `<header>` only in `App.tsx`; mine edits `<main>` only) and by diff (that delivery's `api-client.ts` block ends at line 1030; mine is appended after it, lines 1031+, no shared lines).

## Live route verification

The task's own material was explicit that a live IBKR connection can't be verified in this sandbox — but the five routes themselves (their status codes, error shapes, and parameter conventions) don't need a real Gateway to test, only a running instance of this backend. Installed `backend/requirements.txt` and ran `uvicorn app.main:app` locally with **no Postgres, no IBKR Gateway, and no API keys configured** — confirmed this boots cleanly (per `app/main.py`'s own documented soft-fail startup posture) — then hit all five routes directly:

```
$ curl -s http://127.0.0.1:8123/broker/status
{"connected":false}

$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST http://127.0.0.1:8123/broker/connect
{"detail":"IBKR connect failed: [Errno 111] Connection refused. Is IB Gateway running
and logged in? See backend/README.md's IBKR connection setup section."}
HTTP_STATUS:502

$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://127.0.0.1:8123/broker/subscribe?symbol=NVDA"
{"detail":"Not connected — call POST /broker/connect first"}
HTTP_STATUS:400

$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://127.0.0.1:8123/broker/unsubscribe?symbol=NVDA"
{"detail":"Not connected"}
HTTP_STATUS:400

$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST http://127.0.0.1:8123/broker/disconnect
{"status":"disconnected"}
HTTP_STATUS:200

$ curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X POST "http://127.0.0.1:8123/broker/subscribe" \
    -H "Content-Type: application/json" -d '{"symbol":"NVDA"}'
{"detail":[{"type":"missing","loc":["query","symbol"],"msg":"Field required","input":null}]}
HTTP_STATUS:422
```

This directly confirms every status code/error-shape assumption `api-client.ts`'s wrappers and `BrokerPanel.tsx`'s rendering make, and **decisively settles the query-param-vs-body question** this task's own prompt got wrong: the JSON-body attempt fails with a real `422` naming the missing `query` field, not a body field.

## Type-check / build

- `npx tsc -b`: clean except the four pre-existing `GridPresetPicker.tsx` errors (decision #35). Confirmed pre-existing (not introduced by this delivery, and not introduced by the already-merged parallel delivery either) by running the identical command against a separately, freshly pulled, completely untouched clone — byte-identical four-line error list.
- `npx vite build`: clean, no errors or warnings. `dist/` output produced successfully (95 modules transformed — the parallel delivery's own reported 93, plus this delivery's 2 new files: `BrokerPanel.tsx`, `useBrokerStatus.ts`).

## Footprint verification

`diff -rq` between this delivery and a freshly pulled clone (post-parallel-delivery `main`; excluding `node_modules/`, `dist/`, `tsconfig.tsbuildinfo`, and the backend's installed Python packages, none of which are source) shows exactly:

**Modified:**
- `frontend/src/App.tsx` — two additive hunks (one import line, one `<BrokerPanel />` mount per `<main>`). No existing line removed or reordered; the parallel delivery's own `<header>` hunks untouched.
- `frontend/src/services/api-client.ts` — one additive block appended after the parallel delivery's own `connectFinnhub()` (the file's last export before this change). No existing export changed.
- `docs/architecture/system-design.md` — one new as-built paragraph + two ASCII diagrams inserted into §4.1, immediately before §4.2's existing header (which now itself contains the parallel delivery's own, separate note — untouched here). No existing content in this file changed or removed.
- `docs/decisions/confirmed-decisions.md` — one new PENDING entry appended after the parallel delivery's own PENDING entry (this file's last entry before this change).
- `docs/decisions/INDEX.md` — one new PENDING row appended after the parallel delivery's own PENDING row.

**New:**
- `frontend/src/components/broker/BrokerPanel.tsx`
- `frontend/src/hooks/useBrokerStatus.ts`

**Untouched (confirmed, not just assumed):**
- Everything under `backend/`.
- `App.tsx`'s `<header>` blocks and everything under `frontend/src/components/header/` — the parallel delivery's own territory.
- `frontend/src/components/backtest/`, `frontend/src/components/backtest-results/`, and their hooks.
- `docs/decisions/archive/*.md` — decision content there is immutable, correctly left alone.
- `docs/architecture/system-design.md` §4.2 — the parallel delivery's own note there, untouched; my own note lives in §4.1.

## Manual verification of the actual behavior

- `useBrokerStatus.ts`'s `refetchStatus()` transition logic (`prevConnectedRef`) traced by hand against both the true→false case (clears `subscribedSymbols`) and every other transition (leaves it alone) — confirmed against the actual code, not just the intent.
- `connect()`'s `res.status === "connected"` vs. `"already_connected"` branch confirmed to only clear `subscribedSymbols` in the former case, matching `broker.py`'s own real branching (a genuine new `IBKRAdapter()` vs. the early-return no-op).
- `BrokerPanel.tsx`'s Connect/Disconnect button `disabled` conditions confirmed to key off `connected === true` / `connected !== true` specifically (not a truthy/falsy check), so the initial `connected === null` loading state doesn't enable Disconnect prematurely.
- `SubscribeForm`'s input/button `disabled` state confirmed to key off the `connected` prop, verified wired from `connected === true` (not `connected ?? false`, which would also disable it correctly, but the explicit form is what's actually in the code).
