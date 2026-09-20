# CHANGES — combined: decision #155 (backtest replay timing investigation) and decision #156 (wall-clock test audit)

## Part A — Backtest replay timing investigation (decision #155)

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

## Part B — Wall-clock-dependent test audit (decision #156)

Base: `main`, re-pulled immediately before packaging (already includes decision #155, merged). Decision number assigned by the three-source re-check (`INDEX.md` last row #155, `confirmed-decisions.md` tail #155, archive file list unchanged through `122-133`) run immediately before writing the entry. Slug during parallel work: `test-wall-clock-audit`. **Docs only — no `backend/` or `frontend/` file is touched, no test is touched.**

## What changed

- `docs/decisions/confirmed-decisions.md` — new entry #156 appended after #155's entry (kept intact, not overwritten): full inventory methodology, per-test findings, the controlled-clock proof for the two genuine candidates, and what was not covered.
- `docs/decisions/INDEX.md` — one new row (#156), appended after #155's row.
- `CHANGES.md` / `TESTING.md` — combined as sections (Part A = #155's original content, unedited; Part B = this delivery), per #155's own manual-merge notes, rather than replaced outright.

## What the audit found (details in the entry and `TESTING.md`)

- **Zero proven wall-clock-dependent-test defects.** All 17 in-scope test files inventoried (36 real call sites across 16 files); every site traced to a safe code path except two tests in `test_intelligence_routes.py`, which were proven safe under `libfaketime` across 10 controlled-clock instants (weekday sessions, weekend, holiday, UTC/ET midnight, month rollover, both 2026 DST edges), 3 reps each — 30 runs, 60 individual passes, 0 failures.
- The production engine (`feature_engine/engine.py`) never reads the wall clock at all — confirmed by reading `_compute_one`/`_compute_aggregated` — so this defect class is structurally confined to test code.
- One real-looking lead (the `candles` table's monthly partitions, seeded only through August 2026 by migration `0001`, with today's real date — 2026-09-19/20 — outside that range) was chased and ruled out: `app/db/partitions.py` auto-creates each write's own month, keyed off that row's `candle_ts`, not wall-clock `now`.
- `test_backtest_runner_*.py` reviewed read-only, per the file boundary — no suspected defects to report to #155. Its own flagged overlap (`test_backtest_routes.py`'s `elapsed > 60`) confirmed to be unrelated real-duration timing (`time.monotonic()`), not this defect class.
- Full backend suite re-run at the real clock: 776 passed, 0 failed (786.25s) — matches the pre-audit baseline, no regression.
- Nothing was fixed, because nothing proven-broken was found — a valid "none found, here is the evidence" outcome per this task's own explicit allowance. Existing decisions (including #153, #155) are untouched.

## Collision resolution

Landed second against `backtest-replay-timing-investigation` (already merged as #155). Followed #155's own manual-merge notes exactly: `confirmed-decisions.md`/`INDEX.md` appended (no real conflict, append-only); `CHANGES.md`/`TESTING.md` combined as clearly labeled Part A / Part B sections in each file rather than one delivery overwriting the other. #155's content is reproduced here unedited; only this section and the matching `TESTING.md` Part B are new.
