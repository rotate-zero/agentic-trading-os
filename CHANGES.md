# CHANGES — pending delivery `market-state-websocket-upgrade`

Base commit: fresh `main` re-pulled immediately before packaging (after
`world-view-v1` landed mid-task; confirmed file-disjoint by `diff -rq`
both ways).

## What changed

- Upgraded `frontend/src/hooks/useMarketState.ts` from fetch + 5-second
  poll to WebSocket-primary: subscribes to the `intelligence.market-state`
  channel via `workspaceSocket`, mirroring `useContextSnapshot.ts`'s
  decision #126 pattern. Poll (`POLL_INTERVAL_MS`/`setInterval`) removed
  entirely rather than kept as a fallback — reasoning in the decision-log
  entry below.
- Branches on the envelope's `symbol`: the real sentinel `"__MARKET__"`
  reloads the cross-symbol composite, a matching ticker reloads that
  symbol's per-symbol scores, anything else is ignored — confirmed
  directly against `channels.py`/`market_state_engine/engine.py` rather
  than assumed from `ContextChanged`'s null-based convention.
- Public return shape (`UseMarketStateResult`) unchanged; zero consumer
  files (`AIAnalysisPanel.tsx`, `InfoTab.tsx`) edited.
- Updated `docs/architecture/system-design.md` §10.3's `MarketStateChanged`
  row (no longer says the hook doesn't consume the channel) and added a
  new as-built note with two diagrams (cross-component flow; the hook's
  internal subscribe/branch/unsubscribe flow).
- Updated `docs/architecture/trading-intelligence-architecture.md` §4's
  prose and both of its existing diagrams to reflect the current
  WS-primary design, superseding the now-stale fetch/poll-only versions.
- Appended the unnumbered `market-state-websocket-upgrade` entry to
  `docs/decisions/confirmed-decisions.md`. No real number or `INDEX.md`
  row assigned (nine-way collision on next-available-143 — see that
  entry for the full list).
- Replaced repo-root `TESTING.md` and this file with this delivery's own
  content.

## No backend changes

The `intelligence.market-state` WebSocket channel and its
`EVENT_TO_CHANNEL` routing entry already existed (temp id
`market-state-changed-websocket-channel`, already merged) — confirmed by
direct read of `channels.py` before writing any code. Nothing under
`backend/` was touched by this delivery.

## Validation summary

- `npx tsc -b`: clean — only the four known, pre-existing decision #35
  `GridPresetPicker` errors, confirmed identical against a fresh
  untouched clone's own baseline run.
- `npx vite build`: clean, 96 modules transformed, no errors.
- No frontend hook test file exists in this codebase (confirmed by
  search) — none added, matching existing practice.

## Related, not changed

`world-view-v1` (landed on `main` mid-task) already flagged
`confirmed-decisions.md` as well past its ~100KB rollover trigger with
multiple unnumbered PENDING entries blocking a safe rollover. This
delivery adds one more such entry and does not attempt the rollover,
for the same reason already on record.
