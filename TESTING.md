# TESTING.md — Decision #89 delivery

## Scope

Documentation only. `strategy_engine/` still has no application code (Stage 0),
so there's nothing to run against real Postgres, no `pytest`, no `tsc -b`,
no `vite build` — the verification chain in the project's usual delivery
format doesn't apply to this delivery.

## What changed

- `docs/architecture/strategy-engine-design.md` — §4 (evidence boundary note),
  §5 (`StrategyOutcome` schema rewritten), §7 (`backtests` table shape added),
  §10 (new open item D7), §11 (new principles), §12 (Stage 0 checkbox updated)
- `docs/architecture/system-design.md` — `strategy_performance` →
  `strategy_outcomes` (2 sites), `backtests` reservation note updated
- `docs/architecture/trading-intelligence-architecture.md` — §14 and the
  component→file table, same rename plus entry/exit wording
- `docs/decisions/confirmed-decisions.md` — decision #89 appended
- `docs/decisions/INDEX.md` — row for #89 appended
- `docs/decisions/future-ideas.md` — entry #21 (`exit_trigger`) appended

## Verification performed

- `grep -rn "strategy_performance" docs/` — the only remaining hits are
  inside decision #87's own historical text and the new #89/#INDEX entries
  that *describe* the rename. The decision log is append-only; past entries
  aren't rewritten to match later state, so those are correct as-is, not
  stale.
- `grep -rn "market_state_at_signal|context_at_signal" docs/architecture/*.md`
  — the only remaining hit is the intentional one, inside the new D7 open-item
  row, which exists specifically to name the old field pair as a possible
  future reintroduction.
- `grep -rn "closed_at" docs/architecture/*.md` — zero hits. Confirms the old
  `StrategyOutcome.closed_at` field (replaced by `exit_filled_at` in the B.
  Timing group) has no orphaned references left anywhere.
- File size check on `confirmed-decisions.md` before appending #89 (~60KB)
  confirmed no archive-chunking (~100KB threshold) was triggered by this
  delivery.
- Cross-reference check: every `§` reference added in one doc (e.g.
  `strategy-engine-design.md §5`, `system-design.md §4.13`) was checked
  against the live section numbering in the target file at delivery time,
  not assumed from memory.

## Not done (same as always)

Live click-through / doc rendering check — left to Saqib, per this
project's standing pattern.
