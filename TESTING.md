# TESTING — First Pullback & Reversal design note (decision #107)

**No application code in this change — nothing to run.** Per your "short design note first, then code" instruction, this delivers only:

- `docs/architecture/strategy-engine-design.md` — new §16 (First Pullback/Reversal GATE/MATCH/SCORE/PROPOSE walkthrough + diagrams), a new D9 row in §10, and updated §12/§13.
- `docs/decisions/confirmed-decisions.md` — decision #107 appended.
- `docs/decisions/INDEX.md` — #107 summary row appended.

## How to verify this landed correctly

1. Unzip this folder's contents over your project root (paths already match: `docs/architecture/...`, `docs/decisions/...`).
2. `git diff` should show only additions to those three files — no other files touched, no code files added or changed.
3. Read `docs/architecture/strategy-engine-design.md` §16 top to bottom — that's the actual design; §10's new D9 row and §12/§13's small updates are cross-references into it.
4. Decision numbering: `confirmed-decisions.md` should now contain exactly one entry (#107); `INDEX.md`'s last row should read #107 and link to `confirmed-decisions.md` (not an archive file, since it hasn't rolled over).

## What I'd like your call on before I write code

Both flagged inline in §16/decision #107, repeated here since they're the two things most worth a quick yes/no from you rather than me just proceeding:

1. **`level_key` default.** I picked `"vwap"` as the v1 default reference level for First Pullback (params-driven, easy to override to `"sma_9"`/`"sma_20"` later). Fine as a starting default, or would you rather start with an SMA?
2. **Reversal's scope.** I designed Reversal to fire on ANY touch count (not just the first), on the theory that real reversals often happen on the 2nd/3rd test. If you'd rather start narrower (e.g. only fire on a level's 2nd+ touch specifically, to avoid overlapping with First Pullback's own first-touch territory on the same level), that's a one-line GATE change, easy to adjust before code is written.

Everything else in §16 (the private per-symbol touch-tracking mechanism, the `get_level_interaction_engine().get_snapshot()` access pattern, `allows_waiting=False` for v1) I'm treating as settled unless you say otherwise — happy to start on `first_pullback_strategy.py`/`reversal_strategy.py` next.
