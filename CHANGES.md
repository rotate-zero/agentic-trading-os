# CHANGES — Backtest replay timing investigation (decision #155)

Base: `main`, re-pulled immediately before packaging; the decision number was assigned by the three-source re-check (`INDEX.md` last row, `confirmed-decisions.md` tail, archive file list) run immediately before writing the entry. Slug during parallel work: `backtest-replay-timing-investigation`. **Docs only — no `backend/` or `frontend/` file is touched, no test is touched.**

## What changed

- `docs/decisions/confirmed-decisions.md` — new entry #155 appended: measured findings, two diagrams, options table (a)–(e), a recommendation flagged for Saqib's decision, two design forks, and what was not measured. Status in the entry: **awaiting Saqib's direction — nothing decided or changed.**
- `docs/decisions/INDEX.md` — one new row (#155), appended.
- `CHANGES.md` / `TESTING.md` — replaced (this file and its sibling).

## What the investigation found (details in the entry and `TESTING.md`)

- The 787 s full suite is dominated by four replay tests (719 s, 91.7%); every replayed candle after the first waits on `MarketStateEngine`'s real 1.0 s debounce floor — confirmed by a per-candle instrumented run, not just by reading the docstring.
- The floor also feeds `acceleration_score` (it divides by real elapsed time), so making replay faster would change persisted replay values — this is why the options table separates "faster tests" from "faster replay for users".
- Nothing was implemented, patched or renumbered. Existing decisions (including #153) are untouched.

## Parallel-work note

`test-wall-clock-audit` appends to the same four files. If it lands first, renumber this entry to the next free number, update the entry header, the `INDEX.md` row and the "decision #N" mentions in these two root files, and note the collision inline. See `TESTING.md` for the manual-merge notes.
