# TESTING.md — decision-116-d14-activation-window-resolved

Docs-only change. No application code touched — `scheduler.py`,
`opportunity_cache.py`, and `intelligence.py` are all untouched and
remain exactly as verified in decisions #114/#115.

## What changed

- `docs/architecture/strategy-engine-design.md`
  - §10: **D14 changed from Open to Resolved.** Canonical text added
    explaining why the two instructions that looked contradictory
    ("out of scope for now" vs. "use event timestamps") are actually
    reconciled rather than in conflict, and why a future session
    shouldn't reopen this just because it encounters `active_from`,
    `active_to`, or `candle_ts` in the code.
  - §12: Stage 2's staged-plan entry updated to reflect both tracks fully
    built/verified and D14 closed (previously said "open").
- `docs/decisions/confirmed-decisions.md` — new entry **#116**, Stage 0,
  no code. Records the canonical resolution plus a restated summary of
  Stage 2's fully-verified end-to-end state (real Postgres, 577
  passed/1 pre-existing failure; decision #115's "40 failed" figure
  confirmed as a Postgres-availability artifact, not a regression).
- `docs/decisions/INDEX.md` — row added for #116.

## Verification performed

- Re-fetched the repo fresh before editing (session startup protocol).
- Confirmed `scheduler.py` is untouched (this delivery makes no code
  changes at all).
- Re-ran the full backend suite against a real local Postgres after
  applying only the doc edits: 576 passed / 2 failed this particular
  run (the two already-known pre-existing/order-sensitive failures —
  `test_vwap_publishes_even_while_sma_is_still_warming_up` and
  `test_daily_levels_carry_level_interaction_once_touched`). Consistent
  with the established baseline; nothing regressed, as expected for a
  docs-only change.

## Unzip instructions

Unzip directly at the project root — only the three doc files listed
above are touched.
