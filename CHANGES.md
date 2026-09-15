# Historical-provider gap in Backtest Runner replay — closed

Copy this into your repo root, overwriting the existing paths listed
below. Backend-only delivery: **zero changes to any file under
`frontend/`.** Confirmed by `diff -rq` against a freshly re-pulled clone
of current `main`, both before writing anything and again immediately
before packaging this delivery.

**Decision-number note, per your own standing instruction to
distinguish these explicitly:** this delivery's own work was carried
out citing **#133** throughout — confirmed as the correct next number
via the standard three-source check at session start. A parallel
frontend session (the "Backtest Results" panel) also claimed #133 and
merged first, mid-session — caught at the first mandatory re-check,
renumbered to #134. Before packaging finished, a **second** parallel
frontend session independently also claimed #134 — caught by a further
re-check immediately before the zip was written. **Final observed next
decision number: #135. Final number assigned to this delivery: #135.**
Every internal citation was renumbered twice (#133→#134→#135) before
packaging (`grep -c "#134"` returns 0 across every file this delivery
touches). `strategy-engine-design.md` §7 required two successive
rebases — this delivery's own working copy had originally been edited
against a base pulled *before either* parallel session's work landed,
so it was missing both sessions' real content to begin with; it's now
re-based onto a fresh pull, with this delivery's own note inserted
*after both* of theirs, overwriting neither. See `TESTING.md`'s own
"Two genuine numbering collisions in a row" section for the full
account, and
`docs/decisions/confirmed-decisions.md` — decision #135 — for the
complete reasoning.

## What this closes

`scenarios.py`'s own module docstring named this directly:
`volume_regime_score`/`volatility_regime_score` were `0.0` for every
candle in every `BacktestRunner` replay, for any symbol — structurally,
not just for the existing named scenarios — because `FeatureEngine`'s
Daily Levels/ATR/RVOL refresh never had a real
`broker_registry.get_historical_provider()` to ask during a replay.
Four of the seven v1 strategies (ORB, Gap, Volume Spike, Momentum)
hard-gate their MATCH stage on `volume_regime_score >= 45.0`, so none of
the four could ever fire in a backtest, for any fixture data. This
delivery closes exactly that gap, and nothing else.

## Files changed

- `backend/app/backtest_runner/historical_provider_guard.py` — **new.**
  `install_replay_historical_provider()`, an async context manager
  mirroring `engine_singleton_guard.py`'s own save/install/restore shape
  exactly. Installs a backtest run's own `MarketDataProvider` as
  `broker_registry`'s historical role for the run's duration; restores
  whatever was there before, even on exception. Never calls
  `.connect()`/`.disconnect()` on what it installs — see this file's own
  module docstring for the full safety reasoning (checked directly
  against every real caller of `broker_registry.get_historical_provider()`
  in this codebase, not assumed).
- `backend/app/backtest_runner/fixture_daily_history.py` — **new.**
  `build_daily_history_candles(before, num_days=20)` — deterministic,
  honestly-synthetic prior daily candles, dated on real NYSE trading
  days (real `MarketClock` weekday/holiday logic, DST-safe timestamps).
  20 days (15 genuinely needed for ATR's real `period + 1`, 5 for RVOL's
  real lookback — confirmed by reading the real indicator code, not
  guessed). One shared dataset regardless of symbol or scenario,
  extending `FixtureBacktestContextProvider`'s own precedent.
- `backend/app/backtest_runner/fixture_provider.py` — **modified.**
  `FixtureCandleProvider.get_historical()`'s timeframe guard widened
  from `"1m"`-only to `{"1m", "1d"}` — confirmed by grep to be the only
  two timeframes any real code in this codebase ever requests from a
  historical-role provider.
- `backend/app/backtest_runner/runner.py` — **modified.**
  `BacktestRunner.run()` now nests
  `install_replay_historical_provider(self._market_data_provider)`
  inside its existing `install_replay_engines(...)` block — generic to
  whatever provider a run was constructed with, not fixture-specific.
- `backend/app/api/routes/backtest.py` — **modified.** Builds a
  synthetic daily history anchored to the scenario's own first replayed
  trading day, and constructs one `FixtureCandleProvider` carrying both
  the existing `(symbol, "1m")` replay feed and a new `(symbol, "1d")`
  entry. Docstrings corrected — the old "structurally always 0.0" claim
  is gone; a new, prominent disclosure added about this route now
  writing into shared `symbols`/`daily_levels_state` tables (see "A
  genuinely separate finding" in `TESTING.md`).
- `backend/app/backtest_runner/scenarios.py` — **modified.** Module
  docstring's "hard ceiling" section rewritten to describe the gap as
  resolved, with real, checked-not-assumed findings per strategy (see
  `TESTING.md`'s "What changed for the four volume-gated strategies").
- `backend/tests/test_backtest_runner_regression.py` — **modified.**
  New section 5, four tests — see `TESTING.md`.
- `docs/architecture/strategy-engine-design.md` — **modified.** §7
  gained a new as-built note + diagram, inserted after the parallel
  session's own decision #133 note (not replacing it). New **D18** row
  in the D-items table (the write-side-effect finding, left open,
  Saqib's own confirmed call).
- `docs/decisions/future-ideas.md` — **modified.** New entry #25 — a
  pre-existing, unrelated gap found while checking this seam's own
  safety (decision #132's live-data guard doesn't check IBKR).
- `docs/decisions/confirmed-decisions.md` / `docs/decisions/INDEX.md` —
  **modified.** New decision #135 entry / row.
- `TESTING.md` — **replaced** (delete-first, per this project's
  standing convention — the version this replaces was decision #133's
  own).

**Confirmed untouched** (checked via `diff -rq` against a freshly
re-pulled clone, both before writing any code and again immediately
before packaging): everything under `frontend/`,
`feature_engine/engine.py`, `replay_state_producer.py`,
`broker_registry.py`, `engine_singleton_guard.py`,
`api/routes/intelligence.py`, `trading_intelligence/performance_queries.py`.

## What was deliberately NOT built

See `TESTING.md`'s own "What was deliberately NOT built" section —
no new guaranteed-fire scenarios for ORB/Gap/Volume Spike, no fix for
the write-side-effect finding (documented + flagged as D18 instead, per
Saqib's confirmed direction), no IBKR fix for decision #132's guard
(flagged as `future-ideas.md` #25 instead), no changes to
`HistoricalContextProvider`/live-provider vendor selection, no changes
under `frontend/`.

## Verification

Full reasoning and exact commands: `TESTING.md`. Summary: real local
Postgres 16, wiped and recreated before each run. Baseline (freshly
re-pulled `main`): 715 collected, 715 passed, 0 failed. This delivery:
719 collected (+4, exactly this delivery's own new tests), every time;
across multiple full runs, failures ranged 0-3, every single one a
member of the long-documented, 4-test #119 flaky cluster (never this
delivery's own new tests), each reconfirmed passing in isolation. Zero
regressions.
