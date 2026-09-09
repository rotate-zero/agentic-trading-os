# TESTING.md — decision-118-ai-panel-real-opportunities

Frontend only. Zero backend files touched. Wires the AI Analysis Panel
to the real Opportunity contract (decision #114/Stage 2) in place of
`mocks/opportunities.ts`'s Phase-5 placeholder.

## What changed

- `frontend/src/services/api-client.ts` — **additive.** New
  `OpportunityEvidenceWireShape`/`OpportunityWireShape`/
  `OpportunitiesSnapshotWireShape` interfaces (matching
  `backend/app/strategy_engine/base_strategy.py`'s real `Opportunity`
  model + `OpportunityCache.get_snapshot()`'s wrapping shape) and
  `fetchOpportunities(symbol?)` hitting `GET /intelligence/
  opportunities`. Nothing existing in this file touched.
- `frontend/src/hooks/useOpportunities.ts` — **new file.** Same
  fetch/normalize/WS-refresh pattern `useIntelligenceState.ts` already
  establishes: initial `fetchOpportunities()` on mount/symbol change,
  `normalize()` flattens `{"symbols": {ticker: {strategy: {...}}}}`
  into a flat `Opportunity[]` (injecting `symbol`/`strategy` from the
  keys), `subscribeSymbol()` on mount, re-fetch (not merge) on any
  `"opportunity.new"` WS push matching the current symbol.
- `frontend/src/components/ai-panel/AIAnalysisPanel.tsx` —
  **rewritten.** Now takes `{ symbol, opportunities, loading }` (was
  `{ symbol, opportunities }` — `loading` is new, owns its own
  loading/empty states, same convention `FeatureEnginePanel.tsx`
  already uses). Entry column dropped; Stop/Target relabeled
  Invalidation/Structural target; `evidence.reason` shown as primary
  text, `evidence.conditions` as a compact supporting strip; a status
  badge + card dimming for anything not `status === "actionable"`;
  `wait_reason` shown when waiting; `setup_detected_at` shown as the
  timestamp. Imports `Opportunity` from `hooks/useOpportunities.ts`
  now, not `types/intelligence.ts` (deleted — see below).
- `frontend/src/components/workspace/InfoTab.tsx` — **modified.**
  `ConnectorContent` now calls `useOpportunities(symbol)` instead of
  `useLiveCandles(symbol)` + `generateMockOpportunities(...)`. No other
  line changed — `GeneralContent`, layout, resize logic all untouched.
- `frontend/src/mocks/opportunities.ts` — **deleted.** Its own header
  called itself a Phase-5 stand-in; Phase 5 is built.
- `frontend/src/types/intelligence.ts` — **deleted, beyond what was
  explicitly asked, flagged rather than done quietly.** Its guessed
  `Opportunity` shape (flat `reason`/`suggested_entry`/`suggested_stop`/
  `suggested_target`) was equally stale, and had exactly one remaining
  importer (`AIAnalysisPanel.tsx`, repointed to the real type in
  `useOpportunities.ts`).
- `docs/architecture/system-design.md` — §10.3's `OpportunityCreated`
  row corrected (was still the same stale flat shape the deleted
  `types/intelligence.ts` appears to have been guessed from).
- `docs/architecture/strategy-engine-design.md` — new §19 (full
  walkthrough, including the `evidence.reason` correction to this
  task's own brief), new row **D16** in §10, §12's checklist updated.
- `docs/decisions/confirmed-decisions.md` — new entry **#118**.
- `docs/decisions/INDEX.md` — row added for #118.

**Not touched, deliberately:** anything in `backend/` (route + channel
already existed, decision #114); `GeneralContent` (this task is
`ConnectorContent`'s per-symbol panel only); Trade Planning Engine;
`gate_conditions`/`active_from`/`active_to` (D10/D14/D15 — a concurrent
session's work, confirmed non-conflicting by touching zero shared
files).

## ⚠️ Manual step required — a zip can't delete files

Unzip adds/overwrites; it cannot remove paths. After unzipping this
drop at the project root, delete these two files by hand:

```
rm frontend/src/mocks/opportunities.ts
rm frontend/src/types/intelligence.ts
```

If you skip this: the app still builds and runs correctly (nothing
imports either file anymore — confirmed by `tsc -b`/`vite build` and
a repo-wide grep), they'd just be dead, stale code left sitting in the
tree, which is exactly the "future session could mistake it for still-
relevant" risk this decision was trying to close.

## Verification performed

- Checked `docs/decisions/INDEX.md`/`confirmed-decisions.md`'s tail
  before starting, and again immediately before writing this entry
  (standard practice) — a concurrent session pushed the
  `gate_conditions` work (decision #117) mid-session; synced those
  files in, confirmed zero overlap with this task's own files, used
  **#118** as the next free number.
- `npx tsc -b` — clean (only the pre-existing, unrelated
  `GridPresetPicker.tsx` baseline errors, confirmed by grep that no
  new error touches any file this delivery changed).
- `npx vite build` — clean, 82 modules, no warnings.
- **Exercised against the real running stack, not just compiled.**
  Provisioned PostgreSQL 16 directly in this sandbox (`apt-get install
  postgresql`, same as decision #117), ran `alembic upgrade head`, ran
  the real `app.main` lifespan in-process (`app.router.
  lifespan_context`), and:
  - Confirmed `GET /intelligence/opportunities` genuinely returns
    `{"symbols": {}}` before any publish (not assumed).
  - Published a real `Opportunity` (ORB, `status="actionable"`,
    `expected_horizon_minutes=45`) onto the real `EventBus` via
    `make_envelope(EventType.OPPORTUNITY_CREATED, ...)`, read it back
    via `GET /intelligence/opportunities?symbol=NVDA` through an
    in-process ASGI client — response matched `OpportunityWireShape`
    field-for-field.
  - Published a second `Opportunity` (Gap, `status="waiting"`, a real
    `wait_reason`, no `expected_horizon_minutes`) and read it back over
    a live `/ws` connection subscribed to `"opportunity.new"`
    (`starlette.testclient.TestClient`) — the push matched
    `WireMessage`'s shape exactly (`channel`, `symbol`, `event_type`,
    `payload`, `timestamp`), confirming `useOpportunities.ts`'s
    `msg.symbol` filter and re-fetch trigger line up with what the
    Gateway actually sends.
- Full backend suite re-run against the same real Postgres for a clean
  baseline check (this delivery touches zero backend files): 596
  passed, 2 failed — `test_vwap_publishes_even_while_sma_is_still_
  warming_up` and `test_daily_levels_carry_level_interaction_once_
  touched`, both already documented as pre-existing/order-sensitive in
  decisions #114/#116/#117.
- **Not verified:** in an actual browser (no browser available in this
  sandbox — standing gap noted in every delivery). The empty-state,
  loading-state, and populated-row rendering were checked by reading
  the component logic and the real JSON shapes above line up exactly
  with what it destructures, but nobody has visually seen it render.

## Unzip instructions

Unzip directly at the project root. Adds one new file
(`frontend/src/hooks/useOpportunities.ts`) and updates six existing
ones (`frontend/src/services/api-client.ts`,
`frontend/src/components/ai-panel/AIAnalysisPanel.tsx`,
`frontend/src/components/workspace/InfoTab.tsx`,
`docs/architecture/system-design.md`,
`docs/architecture/strategy-engine-design.md`,
`docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md`) —
then run the two `rm` commands above; the zip cannot perform deletions
itself.
