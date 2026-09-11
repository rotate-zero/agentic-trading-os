# Backtest Runner v1 — Units 1-3 (replay plumbing + fill simulation), NOT the full task

Copy this into your repo root, overwriting the existing path. This is a
**mid-task checkpoint**, not a finished delivery — per your "smaller
units" direction, this covers Units 1-3 of a 4-unit plan; Unit 4 (the
actual `BacktestRunner` orchestrator that ties this into
`record_strategy_outcome()` and writes a real `BacktestRunRecord`) is not
built yet and is not in this zip. No decision log entry yet, on purpose —
that lands with the completed task, not a partial one, same convention
this log always uses for "one entry per coherent piece of work."

## What this is

New module `backend/app/backtest_runner/` (plus tests) — the historical
data landmine, the D17 context landmine (and a third landmine found
during investigation: Context Engine has no historical replay mode at
all, deeper than the Polygon gap), the replay-state-settling design, and
the fill model were all discussed and confirmed with you in-session
before any of this was written; not re-litigated here, just built.

## Files added (all new, nothing existing touched)

- `backend/app/backtest_runner/__init__.py`
- `backend/app/backtest_runner/fixture_provider.py` — `FixtureCandleProvider(MarketDataProvider)`, explicitly labeled synthetic, never claims to validate real historical performance.
- `backend/app/backtest_runner/context_provider.py` — `BacktestContextProvider` ABC, `FixtureBacktestContextProvider` (real `MarketClock` logic, calendar-only, `symbol_providers=[]` — Fundamentals/News honestly absent, never faked), `HistoricalContextProvider` as a documented, deliberately-unbuilt extension point.
- `backend/app/backtest_runner/engine_singleton_guard.py` — installs a run's own engines as the process-wide singleton `state_snapshot.py` resolves through; serializes runs in-process; restores prior state even on exception. Documents (doesn't fix) that concurrent backtests aren't supported in one process.
- `backend/app/backtest_runner/replay_state_producer.py` — `ReplayStateProducer` ABC + `EngineBackedReplayStateProducer`, the real seam: wires the real `EventBus`/`FeatureEngine`/`LevelInteractionEngine`/`MarketStateEngine`/`ContextEngine` (zero modification to any of them), settles each replayed candle to real state, raises `ReplaySettleTimeout` rather than ever returning stale state. All wall-clock waiting is contained here — nothing above this seam knows a sleep exists anywhere.
- `backend/app/backtest_runner/fill_simulator.py` — pure, `MarketClock`-derived fill model: next-open entry, forward-walk exit against `structural_target`/`structural_invalidation`, stop-wins same-candle tie-break, real-session-close `eod_flatten`. Distinguishes `eod_flatten` from a fixture simply running out of data before close (`InsufficientReplayDataError`) rather than collapsing the two.
- `backend/app/backtest_runner/gate_and_warmup.py` — `check_entry_allowed()`, D17 option (a): gate_conditions then both real capture functions non-`None`, never a fabricated placeholder.
- `backend/tests/test_backtest_runner_fixtures.py` (12 tests)
- `backend/tests/test_replay_state_producer.py` (4 tests, real-Postgres DB-gated)
- `backend/tests/test_fill_simulator_and_gate.py` (16 tests)

## A real bug found and fixed during Unit 2's proof run, not caught by reasoning alone

First draft of `advance_to()` only called `ContextEngine.evaluate_for_symbol()`.
The calendar provider is registered on the market-wide `providers` list,
which only `evaluate_all()` populates — `evaluate_for_symbol()` alone
never called it, so `context.providers` came back `{}` every candle.
Caught by actually running the fixture replay and inspecting output, not
by code review; fixed by calling both, same two-call order
`test_strategy_scheduler.py`'s own real-engine integration test already
established for this exact reason.

## A second bug found in the same proof run

`FeatureEngine` has no `async def stop()` (confirmed — unlike
`LevelInteractionEngine`/`MarketStateEngine`, it was never given the
decision #84 poison-pill teardown). A backtest run genuinely needs clean
multi-run teardown, so `EngineBackedReplayStateProducer.stop()` now
cancels `feature_engine._worker_task` directly rather than leaking it —
found because pytest's cross-test event-loop teardown surfaced a real
"Event loop is closed" warning from the leaked task, not a cosmetic one.
`feature_engine/engine.py` itself is untouched — this is handled entirely
by the producer that owns the instance.

## An independent corroboration, not something I had to guess at

Decision #124 (landed upstream mid-session, synced in before this drop)
confirms the real shape `capture_context_snapshot()` produces is
provider-keyed — `context_at_entry["calendar"]["session"]` — exactly
matching what `FixtureBacktestContextProvider`/`_ReplayClockCalendarProvider`
already produce here. Built independently, same shape, before that
decision's fix was even visible to this session.

## Verified

Real local Postgres (PG16, provisioned fresh this session — not the
sandbox's default state). 32 new tests, all passing. Diffed this drop's
files against an untouched clone: confirmed additive-only, nothing
existing modified. `test_performance_queries.py` (9 tests, decision #124's
own suite) re-run alongside this drop's tests as a sanity check — all
pass, confirming this checkpoint doesn't regress anything decision
#124/#125 just landed. Full backend suite before/after (not just the
adjacent files) not yet run — that's part of Unit 4's delivery, once the
whole task is done, not this checkpoint's.

**Not yet done, honestly:** the actual `BacktestRunner` orchestrator
(Unit 4) that calls all of the above plus `record_strategy_outcome()`
and writes a real `BacktestRunRecord`; the full-suite before/after
regression run; `TESTING.md`; the decision log entry; the optional
`backend/app/api/routes/backtest.py` route. Next drop.

## Left open, on purpose

Same two judgment calls flagged and confirmed with you before this was
written — recorded here for anyone reading this file cold:
- `FixtureBacktestContextProvider` as v1's only real implementation of
  `BacktestContextProvider` — calendar-only, `symbol_providers=[]`,
  confirmed rather than defaulted.
- Stop-wins on a same-candle target/stop tie in `fill_simulator.py` —
  confirmed conservative v1 convention, not the only defensible choice.
