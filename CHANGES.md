# AI Analysis Panel wired to the real Opportunity contract (decision #118)

Copy this into your repo root, overwriting the existing path — replaces
the previous drop note (`gate_conditions` enforcement, decision #117).
Frontend only; that #117 work is untouched by this drop and this drop
doesn't touch anything #117 touched — confirmed by diffing before
starting.

## What this is

`AIAnalysisPanel.tsx` was fully built but fed entirely by
`mocks/opportunities.ts` — a Phase-5 stand-in, per its own header
comment. Phase 5 (Stage 2, decisions #114-#117) is built and running.
This drop wires the panel to the real `GET /intelligence/opportunities`
+ `opportunity.new` WebSocket contract. No backend changes — both
already existed.

## A correction to the task brief, not just a build note

The brief that started this task claimed `evidence` never carries a
narrative `reason`/`basis`, only `conditions` — and warned that an
earlier `{"reason", "basis"}` shape was fabricated test scaffolding not
to be trusted. That's wrong as the repo stands: all 5 real strategies
(`orb_strategy.py`, `gap_strategy.py`, `volume_spike_strategy.py`,
`first_pullback_strategy.py`, `reversal_strategy.py`) populate
`evidence.reason`/`evidence.basis` alongside `conditions`, and
`strategy-engine-design.md` §4 confirms this is intentional (`reason`
is meant "for display"). Built against the real shape, not the brief's
claim — full account in `strategy-engine-design.md` §19 and
`confirmed-decisions.md` #118.

## Files changed

- `frontend/src/services/api-client.ts` — additive: `OpportunityWireShape`/`fetchOpportunities()`.
- `frontend/src/hooks/useOpportunities.ts` — new.
- `frontend/src/components/ai-panel/AIAnalysisPanel.tsx` — rewritten against the real shape.
- `frontend/src/components/workspace/InfoTab.tsx` — `ConnectorContent` updated.
- `frontend/src/mocks/opportunities.ts`, `frontend/src/types/intelligence.ts` — deleted (**zip can't delete — see TESTING.md, manual `rm` required**).
- `docs/architecture/system-design.md` — §10.3's stale `OpportunityCreated` row fixed.
- `docs/architecture/strategy-engine-design.md` — new §19, new D16, §12 updated.
- `docs/decisions/confirmed-decisions.md`/`INDEX.md` — decision #118.

## Verified

`tsc -b`/`vite build` clean. Beyond that: real PostgreSQL provisioned,
real app lifespan run in-process, real `Opportunity` objects published
onto the real `EventBus`, read back and confirmed field-for-field
matching over both `GET /intelligence/opportunities` and the
`opportunity.new` WebSocket push (including a `status="waiting"` case
with a real `wait_reason`). Full details, exact numbers: TESTING.md.
Full backend suite re-run for a clean baseline: 596 passed, 2 failed,
both pre-existing/unrelated (same two flaky tests decisions
#114/#116/#117 already document) — confirms zero backend regressions
from a change that touches zero backend files.

**Not verified:** in an actual browser — no browser available in this
sandbox, standing gap every delivery in this log notes.

## Left open, on purpose

- **D16** (`strategy-engine-design.md` §10) — whether to surface
  `wait_expires_at`/a countdown for waiting Opportunities. Deferred:
  no v1 strategy sets `allows_waiting=True` yet, so the field is
  always `null` in real data today (same deferral trigger as D5).
- `evidence.conditions`' generic `key=value` rendering doesn't do any
  strategy-aware formatting (e.g. knowing `gap_pct` is a percentage,
  `trend_score` is 0-100) — every value is just `.toFixed(2)` if
  numeric. Minor, not promoted to a numbered open item; would need a
  per-key formatting table that doesn't exist anywhere in this
  codebase yet and wasn't worth inventing for this drop.
- A real visual/browser check of the panel — see "Not verified" above.

## Next

- Your own visual check of the panel in a browser, ideally with a real
  strategy actually firing (or the same in-process publish technique
  this drop's own verification used, adapted to hit the frontend dev
  server instead of just the API).
- Whether `GeneralContent` (the market-wide Info tab view) should ever
  get its own opportunities summary — explicitly out of this drop's
  scope, not decided here either way.
