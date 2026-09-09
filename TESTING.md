# TESTING.md — decision-117-gate-conditions-enforcement

Application code + tests + docs. Closes the item D10 (decision #112)
deferred alongside `active_from`/`active_to` (D14/decision #116) —
`active_from`/`active_to` itself is untouched, still canonically closed.

## What changed

- `backend/app/strategy_engine/gate_conditions.py` — **new file.** The
  §2b registry/validation/check for declarative `StrategyConfig.
  gate_conditions`. v1 recognizes exactly one key, `"session"`, with
  exactly one value, `"regular"` — confirmed by grep to be the only
  condition any of the 7 real strategies' `default_config()` declares,
  and confirmed by a second grep that §3's own illustrative `vix_min`
  example corresponds to no real field anywhere in this codebase.
  `validate_gate_conditions()` raises `ValueError` for anything else;
  `gate_conditions_satisfied(gate_conditions, candle_ts)` is the actual
  per-candle check, always against `candle_ts`, never wall-clock.
- `backend/app/strategy_engine/scheduler.py` — **modified.**
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
- `backend/tests/test_gate_conditions.py` — **new file**, 10 pure unit
  tests for the new module directly (empty-dict-always-passes,
  `"regular"` satisfied/not against real pre-market/regular/after-
  hours/weekend timestamps via a real `MarketClock`, and
  `validate_gate_conditions()`'s accept/reject cases).
- `backend/tests/test_strategy_scheduler.py` — **modified.** Module
  docstring updated to describe the new third test tier. 10 new tests:
  - 4 registration-time: raises for an unrecognized key, the error
    names the offending strategy, accepts the one real condition, and
    a standing regression guard that the real 7-strategy registry
    stays valid against this module (`test_construction_succeeds_
    for_the_real_seven_strategy_registry` — if a future strategy adds
    a `gate_conditions` key `gate_conditions.py` doesn't recognize,
    THIS test fails at construction time).
  - 5 per-candle pure: gated-and-blocked outside session,
    gated-and-fires inside session, an ungated strategy never blocked
    by this mechanism even outside session, the check is per-strategy
    (not a blanket skip for the whole dispatch), and a gate-check
    exception doesn't block the strategy after it in the loop.
  - 1 DB-gated real-engine end-to-end test — real `EventBus`/
    `MarketStateEngine`/`ContextEngine`/`MarketClock`, a real
    pre-market `FeaturesUpdated` candle — proving the exact failure
    mode this task set out to close: a strategy whose config declares
    `{"session": "regular"}` does not get `evaluate()` called when the
    triggering candle falls outside regular session.
- `docs/architecture/strategy-engine-design.md`
  - §10: new row **D15** (gate_conditions resolution), separate from
    D10/D14 since it's a distinct item closed on its own terms.
  - §12: Stage 2's entry updated to point at D15/decision #117 instead
    of listing `gate_conditions` as still out of scope; new bullet
    `Stage 2 (continued) — gate_conditions enforcement built`.
- `docs/decisions/confirmed-decisions.md` — new entry **#117**. Full
  reasoning: scope confirmed by grep (not assumed), the 3-of-7
  live-gap finding, the `market_state.candle_ts` vs. `features.
  candle_ts` timestamp-source call, the fail-loud-at-registration call
  and why it differs from decision #114's own adjacent precedent, the
  extensibility mechanism, and full verification numbers.
- `docs/decisions/INDEX.md` — row added for #117.

**Not touched, deliberately:** none of the 7 strategy files
(`orb_strategy.py`, `gap_strategy.py`, `momentum_strategy.py`,
`volume_spike_strategy.py`, `first_pullback_strategy.py`,
`reversal_strategy.py`, `vwap_strategy.py`) — including the 4 that now
have a redundant inline session check alongside the new central one.
Removing an inline check is separate, later work, not bundled here.
`active_from`/`active_to` handling — still out of scope, still closed
by D14/decision #116, not reopened.

## Verification performed

- Re-fetched the repo fresh at session start (session startup
  protocol) and confirmed directly against the code — not assumed from
  the task prompt — that decisions #114/#115/#116 are actually
  reflected in `main.py`, `scheduler.py`, and `opportunity_cache.py`
  before writing anything.
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
- Implemented the change, then ran the full backend suite against the
  real Postgres, twice (to characterize the known flakiness rather than
  chase it): 598 total (578 baseline + 20 new) both times; the same 2
  pre-existing failures flicker between 1 and 2 failing per run, never
  more, never a different test. Zero regressions, and the 20 new tests
  pass consistently across both runs.
- `diff -rq` of `backend/app` and `backend/tests` between this
  delivery and the untouched second clone: exactly 4 files differ —
  `gate_conditions.py` (new), `scheduler.py` (modified),
  `test_gate_conditions.py` (new), `test_strategy_scheduler.py`
  (modified). Nothing else in the tree changed.

## Unzip instructions

Unzip directly at the project root. Adds two new files
(`backend/app/strategy_engine/gate_conditions.py`,
`backend/tests/test_gate_conditions.py`) and updates four existing
ones (`backend/app/strategy_engine/scheduler.py`,
`backend/tests/test_strategy_scheduler.py`,
`docs/architecture/strategy-engine-design.md`,
`docs/decisions/confirmed-decisions.md`,
`docs/decisions/INDEX.md`) — no deletions, no renames.
