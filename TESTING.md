# TESTING.md — decision-112-stage2-sequencing-reconciled

Docs-only change. No application code touched.

## Why this exists
The Stage 2 (Scheduler/wiring) sequencing-and-scope decision from the prior
session was drafted for slot #111 but never actually applied to git. In the
meantime Saqib committed different, unrelated work (the Gap/Volume Spike
design review + `scoring_utils.py`) as the real #111. Same category of
collision decision #99 already documents once, with #98.

## What changed
- `docs/architecture/strategy-engine-design.md`
  - §10: new row **D10** (renumbered to reference decision #112) — locks
    Stage 2 sequencing (blocked until Momentum/VWAP land) and scope
    (wiring + `OpportunityCreated` + read-side snapshot only).
  - §12: new row for the real #111 (Gap/Volume Spike review + scoring_utils),
    plus a new unchecked **Stage 2** row, marked BLOCKED, referencing #112.
  - §13: point 7 corrected (First Pullback/Reversal are built, not
    "design-locked but unbuilt"); new point 8 (Momentum/VWAP block on Stage
    2); new point 9 recording the #111 collision itself.
- `docs/decisions/confirmed-decisions.md` — new entry **#112** (the
  renumbered Stage 2 lock), explicitly noting the collision with the real
  #111 above it.
- `docs/decisions/INDEX.md` — row added for #112.

## How to verify
- `grep -n "^11[0-9]\." docs/decisions/confirmed-decisions.md` — confirms
  #111 (Gap/Volume Spike review) and #112 (Stage 2 lock) both exist, in
  order, no gaps or dupes.
- `grep -n "| 11[0-9] |" docs/decisions/INDEX.md` — same check on the index.
- Read §13 points 7-9 directly for the corrected status + collision note.

## Unzip instructions
Unzip directly at the project root — overwrites the same three files as
before. Confirmed against your current `main` (the tree that already has
real decision #111 in it) before this zip was built.
