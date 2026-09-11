# TESTING.md — Context Engine frontend surfacing

## What changed

Frontend-only. `ContextEngine` (`CalendarProvider`/`FundamentalsProvider`/
`NewsFlagProvider`, decisions #90/#96) was fully built and already reachable
via `GET /intelligence/context` (decision #98), but nothing in the UI showed
it. This delivery surfaces it, following the same "built-but-invisible
capability" pattern decision #123 already closed for `strategy_outcomes`/
opportunity conflicts.

**No backend file was touched.** `intelligence.py`'s `/context` route,
`context_engine/engine.py`, and all three provider files are read-only
references for this task — confirmed unchanged by `diff -rq` against a
freshly-pulled untouched clone (see below).

### Files touched

- `frontend/src/services/api-client.ts` (extended) — new
  `fetchContextSnapshot(symbol?)` + wire types
  (`CalendarProviderWireShape`, `FundamentalsProviderWireShape`,
  `NewsProviderWireShape`, `ContextProvidersWireShape`,
  `ContextGlobalWireShape`, `ContextSymbolWireShape`,
  `ContextSnapshotWireShape`), copied field-for-field from the real
  backend provider code, not guessed.
- `frontend/src/hooks/useContextSnapshot.ts` (**new**) — fetch on
  mount/symbol-change + a light 60-second poll (no WebSocket push
  available — see reasoning below and in decision #125).
- `frontend/src/components/workspace/InfoTab.tsx` (extended) — new
  `MarketSessionSummary` section (Calendar, market-wide) in
  `GeneralContent`.
- `frontend/src/components/ai-panel/AIAnalysisPanel.tsx` (extended) —
  new `SymbolContextSummary` section (Fundamentals/News, per-symbol),
  alongside decision #123's own `ConflictStatus`.
- `docs/decisions/confirmed-decisions.md` / `docs/decisions/INDEX.md` —
  new decision #125.
- This file.

**Not touched:** any backend file; `useOpportunities.ts`;
`useOpportunityConflicts.ts`; `useStrategyOutcomes.ts`; any new
page/route/panel type; any chart/visualization.

## Why a poll, and why 60 seconds

There is no `EventType.CONTEXT_CHANGED` entry in
`app/api/websocket/channels.py`'s `EVENT_TO_CHANNEL` — confirmed by reading
the live file — so there's no WebSocket channel to subscribe to for
Context updates. Unlike `useStrategyOutcomes.ts` (which stays pure
fetch-on-mount because `strategy_outcomes` has no live writer at all yet),
Context genuinely changes on its own while a panel is open: session
boundaries several times a day, plus a 15-minute Fundamentals/News timer
per symbol. `ContextEngine.get_snapshot()` is a synchronous, in-memory,
zero-I/O read by its own docstring, so a light poll is cheap. 60 seconds
catches a session-boundary transition within a minute without hammering
anything or trying to compute the client's own guess at exactly when the
next boundary lands. Full reasoning is in the hook's own comments and in
decision #125.

## Aggregate/global context score

Checked directly against `ContextEngine.get_snapshot()`'s real
implementation: the "global" section is exactly
`{"providers": {...}, "evaluated_at": ...}` — no aggregate score,
composite assessment, or other derived/summary field exists anywhere in
the real snapshot today. Per the explicit instruction not to recompute,
reinterpret, or invent one if absent: **none was added.** Each Calendar
field is shown as-is.

## Placement — Calendar vs. Fundamentals/News

- **Calendar** (`session`, `is_market_open`, `fed_day`, ...) is
  market-wide, not symbol-specific — it surfaces in `InfoTab.tsx`'s
  `GeneralContent` (the same market-wide view decision #123's "Recent
  Closed Trades" already lives in), as a new `MarketSessionSummary`
  section, always rendered including the not-yet-evaluated case.
- **Fundamentals/News** are genuinely per-symbol (decision #96) — they
  surface in `AIAnalysisPanel.tsx` as a new `SymbolContextSummary`
  section, fed by `ConnectorContent`'s own new `useContextSnapshot(symbol)`
  call (same "parent fetches, child renders" split `useOpportunities`
  already establishes).
- **One deliberate deviation from decision #123's own layout:**
  `ConflictStatus` renders `null` when there's nothing to show (0/1
  cached opportunities is a legitimate absence). `SymbolContextSummary`
  never renders `null` — it's hoisted into a shared `header` block
  rendered from **all three** of `AIAnalysisPanel`'s return branches
  (loading/empty/populated), not gated behind Opportunities being
  non-empty, since Context Engine data has nothing to do with whether
  Strategy Engine has fired an opportunity yet. Gating it the same way
  `ConflictStatus` is gated would have hidden real, available Context
  data in the common case of a symbol with no opportunities yet.

## Empty/absent states

- **Fundamentals:** a `null` entry (Context Engine never evaluated this
  symbol) and a non-null entry with every field `null` (evaluated, but no
  `symbol_fundamentals` refresh has landed yet) both render "No
  fundamentals data yet." — the UI can't act on the distinction, so one
  honest message covers both. `sector` is never displayed (not even as
  "—"): it's permanently `null` by construction in this build (Finnhub
  has no separate sector field), not a value that's ever "not yet
  fetched."
- **News:** `null` (never evaluated) renders "No news data yet.";
  `present: false` (evaluated, nothing found) renders "No recent
  headlines." — deliberately the same neutral copy whether that's a
  genuine no-headlines symbol or SPY/QQQ/IWM's unconditional decision-#94
  exclusion, since the wire shape is identical either way.

## Verification

```
cd frontend
npx tsc -b
npx vite build
```

Both clean. `tsc -b` shows exactly the four known pre-existing
`GridPresetPicker.tsx` errors (decision #35) — confirmed identical (same
four errors, same lines) against a freshly-pulled untouched second clone
before filtering:

```
src/components/workspace/GridPresetPicker.tsx(2,10): error TS2305: Module '"../../types/workspace"' has no exported member 'GRID_PRESETS'.
src/components/workspace/GridPresetPicker.tsx(6,11): error TS2339: Property 'preset' does not exist on type 'WorkspaceContextValue'.
src/components/workspace/GridPresetPicker.tsx(6,19): error TS2339: Property 'setPreset' does not exist on type 'WorkspaceContextValue'.
src/components/workspace/GridPresetPicker.tsx(19,30): error TS7006: Parameter 'p' implicitly has an 'any' type.
```

`vite build` succeeds cleanly (85 modules transformed, up from 84 on an
untouched clone — the one new hook file).

**Footprint**, confirmed via `diff -rq` against a freshly-pulled untouched
second clone: only `frontend/src/services/api-client.ts`,
`frontend/src/hooks/useContextSnapshot.ts` (new),
`frontend/src/components/workspace/InfoTab.tsx`,
`frontend/src/components/ai-panel/AIAnalysisPanel.tsx`,
`docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md`, and
this file differ. Zero backend files, zero unrelated frontend files.

## Not done / intentionally out of scope

- No backend change of any kind, including adding a `ContextChanged`
  WebSocket channel — flagged in the task prompt as a real gap but
  explicitly out of bounds for this delivery.
- No frontend test file for `useContextSnapshot.ts` — matches this
  codebase's existing (test-free) hook practice, re-confirmed by search
  immediately before writing, not assumed from decision #123's
  description of it.
- No new dashboard, panel type, page, or chart/visualization of Context
  data.

## Decision log

New entry: **#125** in `docs/decisions/confirmed-decisions.md` +
matching `INDEX.md` row. Re-checked the tail twice against a fresh pull
(once before reading, once immediately before writing) — #124 stayed
latest both times, no collision. `confirmed-decisions.md` is ~33KB after
this entry, well under the ~100KB archive-rollover trigger (`README.md`)
— no rollover needed this round. Note for Saqib: the "~97KB, rollover
imminent" figure from before this session's context is stale — decision
#121's own rollover already reset `confirmed-decisions.md` to start
fresh at #122, before this task began.
