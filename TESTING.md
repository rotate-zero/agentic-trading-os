# TESTING.md — decision-117/118-gate-conditions-enforcement-and-sole-authority-rule

Two related deliveries in this drop:

- **#117** — application code: `StrategyConfig.gate_conditions` enforced
  centrally in `StrategyScheduler`, before `evaluate()` (§2b). Closes
  the item D10 deferred alongside `active_from`/`active_to` (D14/#116,
  itself untouched).
- **#118** — docs/comments-only follow-up, raised directly by Saqib
  before calling #117's area finished: makes explicit that
  `gate_conditions` is declarative configuration and `StrategyScheduler`
  (via `gate_conditions.py`) is its SOLE enforcement authority — no
  strategy may independently interpret it or enforce a gate itself.
  Opens a new tracked, deferred item (§10 D16) for removing the 4 v1
  strategies' pre-existing inline session checks, rather than doing
  that cleanup now.

## What changed

- `backend/app/strategy_engine/gate_conditions.py` — **new file** (#117).
  The §2b registry/validation/check for declarative `StrategyConfig.
  gate_conditions`. v1 recognizes exactly one key, `"session"`, with
  exactly one value, `"regular"` — confirmed by grep to be the only
  condition any of the 7 real strategies' `default_config()` declares,
  and confirmed by a second grep that §3's own illustrative `vix_min`
  example corresponds to no real field anywhere in this codebase.
  `validate_gate_conditions()` raises `ValueError` for anything else;
  `gate_conditions_satisfied(gate_conditions, candle_ts)` is the actual
  per-candle check, always against `candle_ts`, never wall-clock.
  **(#118, docstring only)** New "ARCHITECTURAL RULE" section at the
  top of the module docstring, stating the sole-enforcement-authority
  rule and naming the 4 existing inline checks as a tolerated,
  pre-existing exception — not a template for new strategies.
- `backend/app/strategy_engine/scheduler.py` — **modified** (#117).
  - Module docstring: `gate_conditions` moved out of the "explicitly
    out of scope" list into its own new section describing what's now
    enforced, why registration-time validation fails loudly (a real,
    deliberate call, differing from the `after_time`/`on_event`
    "registered but unreachable" precedent), and the finding that 3 of
    the 7 strategies had zero enforcement of this precondition
    anywhere before this change.
  - `StrategyScheduler.__init__`: calls `validate_gate_conditions()`
    once per registered strategy, before trigger-grouping.
  - `_on_market_state_changed`: the per-strategy loop now checks
    `gate_conditions_satisfied(strategy.config.gate_conditions,
    market_state.candle_ts)` in its own try/except, immediately before
    `evaluate()`'s own try/except — a failing gate check skips only
    that strategy (`logger.debug`, "honest gate skip, not an error");
    a gate-check exception is isolated the same way an `evaluate()`
    exception already was.
  - No other line changed. `active_from`/`active_to` handling
    (already correctly absent, per D14) is untouched.
  - **(#118, docstring only)** New item 3 in the `gate_conditions`
    section stating the sole-authority rule and cross-referencing D16.
- `backend/tests/test_gate_conditions.py` — **new file** (#117), 10 pure
  unit tests for the new module directly (empty-dict-always-passes,
  `"regular"` satisfied/not against real pre-market/regular/after-
  hours/weekend timestamps via a real `MarketClock`, and
  `validate_gate_conditions()`'s accept/reject cases).
- `backend/tests/test_strategy_scheduler.py` — **modified** (#117).
  Module docstring updated to describe the new third test tier. 10 new
  tests: 4 registration-time (raises for an unrecognized key, the error
  names the offending strategy, accepts the one real condition, and a
  standing regression guard that the real 7-strategy registry stays
  valid against this module); 5 per-candle pure (gated-and-blocked
  outside session, gated-and-fires inside session, an ungated strategy
  never blocked by this mechanism even outside session, the check is
  per-strategy not a blanket skip, a gate-check exception doesn't block
  the strategy after it); 1 DB-gated real-engine end-to-end test — real
  `EventBus`/`MarketStateEngine`/`ContextEngine`/`MarketClock`, a real
  pre-market `FeaturesUpdated` candle — proving the exact failure mode
  this closes. **Not touched by #118** — nothing behavioral changed, so
  no new tests were needed for it; the existing suite already fully
  covers the mechanism the new rule governs.
- `docs/architecture/strategy-engine-design.md`
  - §2b: **(#118)** new paragraph immediately after the existing
    "Gate ≠ ranking" principle, same callout style, stating the
    sole-enforcement-authority rule.
  - §10: new row **D15** (#117, gate_conditions resolution) and new row
    **D16** (#118, open/deferred — remove the 4 inline checks).
  - §12: Stage 2's entry updated to point at D15/#117 instead of
    listing `gate_conditions` as still out of scope; new bullet
    `Stage 2 (continued) — gate_conditions enforcement built`.
- `docs/decisions/confirmed-decisions.md` — new entries **#117** and
  **#118**.
- `docs/decisions/INDEX.md` — rows added for #117 and #118.

**Not touched, deliberately:** none of the 7 strategy files
(`orb_strategy.py`, `gap_strategy.py`, `momentum_strategy.py`,
`volume_spike_strategy.py`, `first_pullback_strategy.py`,
`reversal_strategy.py`, `vwap_strategy.py`) — including the 4 that now
have a redundant inline session check alongside the new central one and
are named in decision #118/D16 as a tolerated, tracked-for-removal
exception. Removing them is real per-strategy work (each one's own test
file asserts on that strategy's inline GATE behavior directly), kept
deliberately separate — trigger condition is in D16, not "next
session." `active_from`/`active_to` handling — still out of scope,
still closed by D14/decision #116, not reopened.

## Verification performed

- Re-fetched the repo fresh at session start and confirmed directly
  against the code — not assumed from the task prompt — that decisions
  #114/#115/#116 are actually reflected in `main.py`, `scheduler.py`,
  and `opportunity_cache.py` before writing anything.
- Grepped every one of the 7 strategies' `default_config()` for
  `gate_conditions=` — confirmed all 7 declare exactly
  `{"session": "regular"}`. Grepped `schemas/events/market_state.py`/
  `context.py` for `vix` — confirmed no such field exists anywhere.
  Grepped every strategy file for `is_regular_session`/
  `minutes_since_open` — confirmed 4 of 7 (ORB, Gap, Momentum, Volume
  Spike) already self-gate on session inline, and confirmed the other
  3 (First Pullback, Reversal, VWAP) have no session check anywhere in
  their own `evaluate()`.
- Provisioned a real local PostgreSQL 16 directly in this sandbox
  (`apt-get install postgresql`, real user/db matching `config.py`'s
  defaults, `alembic upgrade head`) — no DB-gated test was skipped or
  mocked.
- Established a real baseline: a second, completely untouched clone of
  the repo, its own venv, run against the same real Postgres —
  576 passed / 2 failed (`test_vwap_publishes_even_while_sma_is_still_
  warming_up`, `test_daily_levels_carry_level_interaction_once_
  touched`), both already documented as pre-existing/order-sensitive
  in decisions #114/#116.
- Implemented #117, then ran the full backend suite against real
  Postgres repeatedly: 598 total (578 baseline + 20 new) consistently;
  the same cluster of pre-existing, order/timing-sensitive tests
  flickers between 1-3 failing per run depending on execution order
  (`test_vwap_publishes_even_while_sma_is_still_warming_up` — most
  consistent; `test_daily_levels_carry_level_interaction_once_touched`,
  `test_sma_ema_slope_family_groups_under_the_owning_period_and_is_
  excluded_from_level_interaction`, and — seen once —
  `test_feature_engine_backfills_from_persisted_history_on_cold_start`
  — all intermittent), never a new failure signature outside this known
  cluster, never fewer than 595 passing. Zero regressions; the 20 new
  tests pass consistently across every run.
- Implemented #118 (docstrings only) on top — re-ran the full suite
  again to confirm no accidental syntax/import breakage: same 598
  total, same known-flaky cluster, zero new failures attributable to
  the docstring changes (a docstring cannot change runtime behavior;
  confirmed by direct import of both modified modules succeeding
  before the suite run).
- `diff -rq` of `backend/app`, `backend/tests`, `docs`, and
  `TESTING.md` between this delivery and the untouched second clone:
  exactly the files listed under "What changed" above differ — nothing
  else in the tree changed.

## Unzip instructions

Unzip directly at the project root. Adds two new files
(`backend/app/strategy_engine/gate_conditions.py`,
`backend/tests/test_gate_conditions.py`) and updates five existing ones
(`backend/app/strategy_engine/scheduler.py`,
`backend/tests/test_strategy_scheduler.py`,
`docs/architecture/strategy-engine-design.md`,
`docs/decisions/confirmed-decisions.md`,
`docs/decisions/INDEX.md`) — no deletions, no renames.
