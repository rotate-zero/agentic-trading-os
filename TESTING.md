# TESTING.md — d16-session-gate-cleanup (decision #119)

Standalone cleanup pass closing D16, per decision #118's own trigger
condition ("the next time any of these files is touched for other work,
or a standalone cleanup pass") — Saqib's direct call, not a deferral.
Removes the v1 strategies' now-redundant inline session GATE checks, so
`gate_conditions.py`/`StrategyScheduler` are the sole place session is
ever enforced, with no remaining exception.

**Correction to D16's own framing, made explicit rather than silently
absorbed:** only **3** strategies had a removable inline check, not 4.
`gap_strategy.py`, `momentum_strategy.py`, `volume_spike_strategy.py`
each called `MarketClock.is_regular_session()` inline in their own GATE
step. `orb_strategy.py` has no `is_regular_session()` call anywhere —
grepped directly, confirmed absent. Its own `minutes_since_open()` call
is load-bearing MATCH logic (opening-range formation timing: still
forming vs. closed), not a duplicate session gate under a different
name. `orb_strategy.py` is **completely untouched, code-wise**, by this
delivery. Decision #117's/#118's own docstring text ("4 of the 7
today") repeated this imprecise count and is corrected in the same
change (see below).

## What changed

- `backend/app/strategy_engine/gap_strategy.py` — **modified.**
  - `evaluate()`'s GATE block: removed the two-line
    `if not clock.is_regular_session(features.candle_ts): return None`
    check. `clock = get_market_clock()` and its import stay live — reused
    later in the same call for `clock.trading_day(...)` and, via
    `match_direction(...)`, `clock.minutes_since_open(...)`.
  - `default_config()`'s `gate_conditions={"session": "regular"}` line:
    stale trailing comment (claiming `evaluate()` self-enforces this,
    "not yet consumed by any Scheduler") replaced — states plainly that
    enforcement is solely `StrategyScheduler`'s (#117/#118).
  - Module docstring's `--- Session scope ---` section: the substantive
    "why session matters for Gap" reasoning kept verbatim; only the
    specific claim that this file enforces it via `is_regular_session()`
    reworded to point at central enforcement, with a pointer to this
    decision.
  - **Not touched:** the separate `max_minutes_since_open` bounded-window
    check inside the pure `match_direction()` function (decision #111) —
    a different mechanism (a plain int parameter, not `MarketClock`), not
    the thing D16 targets.
- `backend/app/strategy_engine/momentum_strategy.py` — **modified.**
  Same GATE-block removal and Session-scope docstring reword as above.
  **No `default_config()` comment update** — verified directly that,
  unlike Gap/Volume Spike, this file's `gate_conditions={"session":
  "regular"}` line carried no trailing comment to begin with, so there
  was nothing stale to fix there. (The task's own pre-verified findings
  assumed this comment existed identically in all three files; it
  didn't — re-verified per file rather than propagated.)
- `backend/app/strategy_engine/volume_spike_strategy.py` — **modified.**
  Same GATE-block removal, `default_config()` comment fix, and
  Session-scope docstring reword as `gap_strategy.py`.
- `backend/app/strategy_engine/orb_strategy.py` — **not touched.** No
  code change of any kind.
- `backend/app/strategy_engine/gate_conditions.py` — **modified,
  docstring only.** ARCHITECTURAL RULE section corrected from "4 of the
  7 today" to name the actual 3 files and state D16 as resolved by this
  decision, with the ORB distinction spelled out. No enforcement logic
  touched.
- `backend/app/strategy_engine/scheduler.py` — **modified, docstring
  only.** Module docstring items 2 and 3 (the `gate_conditions` section)
  corrected for the same 3-vs-4 imprecision and updated to state D16 is
  resolved. No enforcement logic touched — `StrategyScheduler.__init__`
  and `_on_market_state_changed` are byte-for-byte unchanged.
- `backend/tests/test_gap_strategy.py` — **modified.** Removed
  `test_outside_regular_session_never_fires` (the only test asserting on
  this strategy's own inline GATE behavior directly). Replaced with a
  one-line comment pointing to `test_gate_conditions.py`/
  `test_strategy_scheduler.py` as where this contract now lives. Every
  other test in the file inspected directly for incidental reliance on
  the old gate (session-related fixtures, out-of-session `candle_ts`
  values) — none found; all other `evaluate()`-driving tests already use
  in-session timestamps.
- `backend/tests/test_momentum_strategy.py` — **modified.** Same removal
  and pointer comment for its own `test_outside_regular_session_never_
  fires`. Same incidental-reliance check performed — none found.
- `backend/tests/test_volume_spike_strategy.py` — **modified.** Same
  removal and pointer comment. Same incidental-reliance check performed
  — none found.
- `docs/architecture/strategy-engine-design.md` — **modified.**
  - §2b: the "sole enforcement authority" paragraph's closing sentence
    updated — D16 now marked resolved (decision #119), "4 of the 7"
    corrected to 3, ORB distinction stated.
  - §10: the D16 row changed from "Open, deferred" to "Resolved,"
    pointing to decision #119, with the 3-vs-4 correction recorded in
    the row itself rather than only in the decision log.
- `docs/decisions/INDEX.md` — new row for decision #119.
- `docs/decisions/confirmed-decisions.md` — new entry **#119** (checked
  the tail immediately before writing — #118 was still the last entry,
  no collision).
- `TESTING.md` — this file, replaced (delete-first, per standard
  practice for this repo).

**Not touched, deliberately:** `orb_strategy.py` (code-wise, entirely);
`gap_strategy.py`'s `max_minutes_since_open` check (decision #111, a
different mechanism); `active_from`/`active_to` (D14/#116); Opportunity
Engine ranking (§9); any new `gate_conditions` key or value;
`gate_conditions.py`'s/`scheduler.py`'s actual enforcement logic
(unchanged since #117 — only their module docstrings changed here); any
strategy's MATCH/SCORE/PROPOSE logic.

## Manual merge notes

No parallel-track conflicts expected — this delivery only touches files
no other in-flight track is known to be working in
(`gap_strategy.py`/`momentum_strategy.py`/`volume_spike_strategy.py`/
their test files, `gate_conditions.py`/`scheduler.py` docstrings, and the
docs listed above). If a manual merge is needed on
`confirmed-decisions.md` or `docs/decisions/INDEX.md` because another
track has landed first, this delivery's new content is a pure append —
copy the `119.` entry / new INDEX.md row in after whatever the latest
existing entry is, renumbering if `#119` was claimed by another track in
the interim (same renumber-and-note-the-collision pattern as
`#98`/`#99`, `#111`/`#112`, `#114`/`#115`).

## Verification performed

- Pulled a fresh repo tarball at session start; did not assume anything
  from the task prompt's "pre-verified findings," including the 3-vs-4
  count itself — re-grepped every one of the 7 strategy files for
  `is_regular_session`/`minutes_since_open`/`get_market_clock` directly
  before writing any code.
- Confirmed decision #118 is reflected in `gate_conditions.py`'s and
  `scheduler.py`'s module docstrings, per the task's required pre-check
  — and in doing so found both already repeated the "4 of the 7"
  imprecision this delivery corrects.
- Checked `confirmed-decisions.md`'s tail immediately before writing —
  #118 was still the last entry, #119 confirmed free.
- Provisioned a real local PostgreSQL 16 directly in this sandbox
  (`apt-get install postgresql`, `alembic upgrade head`) — no DB-gated
  test was skipped or mocked.
- Diffed all three modified strategy files against a freshly-pulled,
  untouched second clone — confirmed the only hunks are the removed
  GATE check and the two docstring/comment updates per file (one per
  file for Momentum, which had no stale `default_config()` comment to
  begin with). MATCH/SCORE/PROPOSE confirmed byte-for-byte unchanged.
- Ran all strategy-engine test modules (`test_gap_strategy.py`,
  `test_momentum_strategy.py`, `test_volume_spike_strategy.py`,
  `test_orb_strategy.py`, `test_first_pullback_strategy.py`,
  `test_reversal_strategy.py`, `test_vwap_strategy.py`,
  `test_gate_conditions.py`, `test_strategy_scheduler.py`,
  `test_scoring_utils.py`, `test_base_strategy.py`) 3 times
  independently: 187/187 passed every run, zero flakiness in the area
  this task actually touched.
- Full backend suite: 595 collected post-change (598 baseline − 3
  removed tests, exactly as expected). Ran repeatedly against both the
  untouched second clone and the edited copy, with the DB wiped and
  recreated before each run (this project's own established practice
  for avoiding order-dependent failures). A representative paired
  comparison, both sides freshly migrated: untouched clone — 598 total,
  596 passed, 2 failed; edited copy — 595 total, 592 passed, 3 failed.
  Zero regressions in either comparison.
- **Test-suite flakiness turned out broader than the previously-named
  pair, run down rather than glossed over.** Across repeated runs on
  both copies, four distinct tests were observed failing intermittently:
  the documented pair (`test_vwap_publishes_even_while_sma_is_still_
  warming_up`, consistent; `test_daily_levels_carry_level_interaction_
  once_touched`, intermittent) plus two more —
  `test_sma_ema_slope_family_groups_under_the_owning_period_and_is_
  excluded_from_level_interaction` and
  `test_feature_engine_backfills_from_persisted_history_on_cold_start`.
  Both of the latter two were confirmed, via repeated isolated reruns,
  to also flicker on the completely **untouched clone**
  (`test_feature_engine_backfills_from_persisted_history_on_cold_start`
  failed 1 of 3 isolated reruns there — an async-timing race in Feature
  Engine cold-start backfill, no code path anywhere near
  `strategy_engine/`) — confirming both are pre-existing and unrelated
  to this change, not new regressions. The #117/#118 session's own
  (now-superseded) `TESTING.md` had already independently observed this
  same broader cluster, including these exact two tests, without
  promoting them into `confirmed-decisions.md`'s own named pair — this
  delivery is the first to name all four in the decision log itself.
- `diff -rq` of `backend/app`, `backend/tests`, and `docs` between this
  delivery and the untouched second clone: exactly the files listed
  under "What changed" above differ — nothing else in the tree changed.

## Unzip instructions

Unzip directly at the project root. Modifies eight existing files
(`backend/app/strategy_engine/gap_strategy.py`,
`backend/app/strategy_engine/momentum_strategy.py`,
`backend/app/strategy_engine/volume_spike_strategy.py`,
`backend/app/strategy_engine/gate_conditions.py`,
`backend/app/strategy_engine/scheduler.py`,
`backend/tests/test_gap_strategy.py`,
`backend/tests/test_momentum_strategy.py`,
`backend/tests/test_volume_spike_strategy.py`) and three docs
(`docs/architecture/strategy-engine-design.md`,
`docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md`) —
plus this `TESTING.md` itself. No new files, no deletions, no renames.
`orb_strategy.py` is not part of this delivery.
