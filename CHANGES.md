# Backtest Runner v1 — Units 1-4 complete: the core proof works end-to-end

Copy this into your repo root, overwriting the existing path. Units 1-4
of the 4-unit plan are done and verified — one fixture-driven run
produces a real `BacktestRunRecord` and a real
`StrategyOutcomeRecord(is_backtest=True)` resolving to it, through the
real unmodified engine pipeline and a real (unmodified) `Strategy`
interface. **Still not a full task close-out**: no decision log entry
yet, no `TESTING.md`, no full-suite before/after regression run (only
the adjacent suites re-run as a sanity check) — those are Unit 5/6, next
drop, pending your review of this one. Same convention as the last
drop: one entry per coherent piece of *finished* work, not a partial one.

## What's new since the Units 1-3 drop

- `backend/app/backtest_runner/runner.py` — `BacktestRunner`, the
  orchestrator. Replays one symbol's candles through the real pipeline,
  calls one real `Strategy.evaluate()` on EVERY candle (matching live —
  see below), simulates fills for actionable `Opportunity`s, and
  persists real outcomes.
- `backend/tests/test_backtest_runner.py` — 3 tests, real Postgres. The
  main one is the whole task's central proof: fixture run → real
  `BacktestRunRecord` + `StrategyOutcomeRecord(is_backtest=True)`,
  `backtest_run_id` resolving correctly, `market_state_at_entry`/
  `context_at_entry`/`_at_exit` all real dicts, `realized_r` matching
  hand-computed expected value.

## The design finding that reshaped Unit 4 mid-planning

`schemas/performance.py` states directly that `market_state_at_entry`
is "captured at `entry_filled_at`, not `setup_detected_at`" — and
`state_snapshot.py` has an existing (previously unnoticed by this
session) `capture_strategy_outcome_snapshots()` convenience function
documented as the call a real fill handler makes "once, at
`entry_filled_at`, and again, separately, at `exit_filled_at`." That
moved D17's real check off the signal candle and onto the fill/exit
candles specifically — `runner.py` captures snapshots at exactly those
two instants, not when `evaluate()` first returns an Opportunity. This
was surfaced and discussed with you before any of `runner.py` was
written, not discovered mid-implementation.

## Also surfaced and confirmed before coding

`Strategy.evaluate()` is called on **every** replayed candle, even while
a simulated position is open — traced from `scheduler.py`: live, acting
on an Opportunity is a downstream Decision Engine concern that doesn't
exist yet (blocked on D4), so skipping `evaluate()` mid-trade would
silently diverge from live and corrupt a strategy's own internal state
(ORB's `candles_seen`, concretely). Confirmed empirically in this drop's
own test: `strategy.calls == 4` for a 4-candle replay with one open
position spanning most of it.

## First-use conventions established here (none existed anywhere in this
codebase — checked by grep before choosing, not guessed)

- `opportunity_id`: `Opportunity` carries no identity field, `OpportunityCache`
  doesn't assign one either — `runner.py` mints a fresh `uuid4()` at the
  moment a signal is accepted for simulated entry.
- `config_hash`: sha256 hex of a stable JSON serialization of
  `(gate_conditions, params)`.
- `feature_version`: caller-supplied, no silent default (Feature Engine
  has no versioning scheme yet — a hidden default risked a future
  real-data run forgetting to override it).
- `final_stop`/`final_target` (required floats): set equal to
  `structural_invalidation`/`structural_target` — no Trade Planning
  refinement stage exists in v1.
- `origin="auto"`, `feature_snapshot_id=None` (the `feature_snapshots`
  table doesn't exist anywhere in this codebase — confirmed by grep,
  same finding the schema's own docstring already states independently).

## Verified

Real local Postgres. 44 tests total (12 from Unit 1, 4 from Unit 2, 16
from Unit 3, 3 new from Unit 4), all passing. Diffed this entire working
tree against a fresh untouched clone: **zero modification anywhere**
outside the new `backtest_runner/` module, the four new test files, and
this file — confirmed directly, not asserted (`diff -rq`, clean). Every
one of the 7 real strategy files byte-identical to the untouched clone.
`test_performance_queries.py` (decision #124's own suite, 9 tests)
re-run alongside this drop as a sanity check — still passes, this drop
doesn't regress it.

**Not yet done:** the full backend suite before/after (not just adjacent
files); the decision log entry; `TESTING.md`; the optional
`backend/app/api/routes/backtest.py` route (deferred last drop, still
deferred). Next drop, once you've had a look at this one.
