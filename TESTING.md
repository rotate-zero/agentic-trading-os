# TESTING.md — decision-111-strategy-scheduler-sequencing-locked

Docs-only change. No application code touched, so no test suite to run.

## What changed
- `docs/architecture/strategy-engine-design.md`
  - §10: new row **D10** — locks Stage 2 (Strategy Scheduler/wiring) sequencing (blocked until Momentum/VWAP land) and scope (wiring + `OpportunityCreated` + read-side snapshot only; Gate enforcement and Opportunity Engine ranking explicitly out of scope).
  - §12: new unchecked **Stage 2** row in the staged plan, marked BLOCKED.
  - §13: point 7 corrected — it previously said First Pullback/Reversal were "design-locked but unbuilt," which was stale as of decisions #109/#110 (they're built). New point 8 added noting the Momentum/VWAP block on Stage 2.
- `docs/decisions/confirmed-decisions.md` — new entry **#111** recording the above, Stage 0 (no code).
- `docs/decisions/INDEX.md` — row added for #111.

## How to verify
- `grep -n "D10" docs/architecture/strategy-engine-design.md` — confirms the new row exists in §10.
- `grep -n "^111\." docs/decisions/confirmed-decisions.md` — confirms the new entry.
- `grep -n "| 111 |" docs/decisions/INDEX.md` — confirms the index row.
- Read §13 point 7 directly — should now say First Pullback/Reversal are built, not unbuilt.

## Unzip instructions
Unzip directly at the project root — the `docs/` paths inside line up with the existing tree, so this only overwrites the three files listed above.
