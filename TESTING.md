# TESTING.md — Decision #135: historical-provider gap in Backtest Runner replay

## What this delivery is

Closes a real, structural gap `scenarios.py`'s own module docstring
named directly: `volume_regime_score`/`volatility_regime_score` were
`0.0` for every candle in every `BacktestRunner` replay, for any
symbol, regardless of how a scenario's candles were built — because
`FeatureEngine`'s Daily Levels/ATR/RVOL refresh never had a real
`broker_registry.get_historical_provider()` to ask during a replay.
Four of the seven v1 strategies (ORB, Gap, Volume Spike, Momentum)
hard-gate their MATCH stage on `volume_regime_score >= 45.0`, so all
four could never fire in a backtest — not because of their own candle
shape, but because of this one shared, structural ceiling.

Full reasoning, including the fork presented to Saqib and how it was
resolved, the second (write-side-effect) finding also presented and
resolved, and a genuine decision-number collision with a parallel
session reconciled per Saqib's own new standing instruction, lives in
`docs/decisions/confirmed-decisions.md` — decision #135. This file
covers what to run to verify it and what a reader should know before
touching this seam again.

**Zero frontend changes.** This delivery is backend-only.

## Files changed

**New:**
- `backend/app/backtest_runner/historical_provider_guard.py` — new.
  `install_replay_historical_provider()`, an async context manager
  mirroring `engine_singleton_guard.py`'s own save/install/restore
  shape exactly: saves whatever `broker_registry.get_historical_
  provider()` currently returns, installs the run's own
  `MarketDataProvider` in its place for the duration of the `async
  with` block, restores the original in `finally` — even on exception.
  Own `asyncio.Lock`, deliberately not reusing `engine_singleton_
  guard.py`'s lock, even though the real call path always nests this
  inside that guard's own lock (see the module's own docstring for
  why). Never calls `.connect()`/`.disconnect()` on what it installs —
  see "Why this is safe" below.
- `backend/app/backtest_runner/fixture_daily_history.py` — new.
  `build_daily_history_candles(before, num_days=20)` — a deterministic,
  honestly-synthetic sequence of prior 1-DAY candles, dated on real NYSE
  trading days (via `MarketClock`'s own weekday/holiday logic) strictly
  before `before`, timestamped at real NYSE close through a genuine
  `ZoneInfo("America/New_York")` conversion (DST-safe). 20 days: 15
  genuinely needed (`feature_engine_atr_period + 1` = Wilder ATR's real
  requirement), 5 needed for `feature_engine_rvol_lookback_days` — both
  confirmed by reading `atr.py`/`rvol.py` and their real callers
  directly. One shared dataset regardless of symbol or named scenario,
  anchored to the scenario's own start date — extends
  `FixtureBacktestContextProvider`'s own "same synthetic facts
  regardless of which symbol" precedent one step further.

**Modified:**
- `backend/app/backtest_runner/fixture_provider.py` —
  `FixtureCandleProvider.get_historical()`'s timeframe guard widened
  from `"1m"`-only to `{"1m", "1d"}`. Confirmed by grep across `app/`
  that these are the only two timeframes any real code in this
  codebase ever requests from a historical-role provider — not opened
  further than that.
- `backend/app/backtest_runner/runner.py` — `BacktestRunner.run()` now
  nests `install_replay_historical_provider(self._market_data_provider)`
  inside its existing `install_replay_engines(...)` block. Whatever
  `MarketDataProvider` a run was constructed with now also temporarily
  becomes the process's historical-role provider for the run's
  duration — generic, not fixture-specific; a future real-data provider
  would get the same behavior automatically.
- `backend/app/api/routes/backtest.py` — now builds a synthetic daily
  history (`fixture_daily_history.py`) anchored to the scenario's first
  replayed trading day, and constructs one `FixtureCandleProvider` with
  both `(symbol, "1m")` (the existing replay feed) and `(symbol, "1d")`
  (new) entries. Route and module docstrings corrected: the old
  "structurally always 0.0" claim is gone, replaced with the real,
  checked-not-assumed current state (see "What changed for the four
  volume-gated strategies" below) — plus a new, prominent disclosure
  about this route now writing into shared `symbols`/`daily_levels_
  state` tables (see "A genuinely separate finding" below).
- `backend/app/backtest_runner/scenarios.py` — module docstring's "hard
  ceiling" section rewritten to describe the gap as resolved, with the
  real, checked findings per strategy (not a re-assertion of the old
  ceiling). `volume_gated_baseline`'s own catalog description updated
  to match.
- `backend/tests/test_backtest_runner_regression.py` — new section 5,
  four tests (see "Tests" below).
- `docs/architecture/strategy-engine-design.md` — §7 gained a new
  as-built note + diagram for this seam, inserted after the parallel
  session's own decision #133 note (not replacing it — see "A genuine
  numbering collision" below). New **D18** row in the D-items table
  (the write-side-effect finding, left open).
- `docs/decisions/future-ideas.md` — new entry #25 (a pre-existing,
  unrelated gap found while checking this seam's own safety: decision
  #132's live-data guard doesn't check IBKR).

**Confirmed untouched** (checked via `diff -rq` against a freshly
re-pulled clone, both before writing any code and again immediately
before packaging): everything under `frontend/`,
`feature_engine/engine.py`, `replay_state_producer.py`,
`broker_registry.py`, `engine_singleton_guard.py`,
`api/routes/intelligence.py`, `trading_intelligence/performance_queries.py`.

## Why this is safe — read before assuming a broker_registry-wide swap is risky

`broker_registry.get_historical_provider()` has exactly three real call
sites outside `feature_engine/engine.py` itself (confirmed by grep, not
assumed): two identity-comparison-only reads (`main.py`, `market_data.py`
— never call `get_historical()`), and one real live-facing read,
`GET /market/candles` (`market.py`), which gates on
`adapter.is_connected()` before ever calling `get_historical()`.
`FeatureEngine` itself never checks `is_connected()`, only `is None` —
so as long as the seam's installed provider is never `.connect()`ed
(it never is), `GET /market/candles` keeps returning its existing
honest 400 throughout a backtest run. No silent fixture-data leak into
a live-facing route, by construction. Decision #132's Finnhub/Polygon
409 already means `broker_registry`'s historical role is unclaimed
going into every valid backtest run today — of the two,
only Polygon (`market_data.py`) actually claims the historical role on
connect; Finnhub only ever claims streaming (confirmed by reading
`finnhub_data.py` directly). See `historical_provider_guard.py`'s own
module docstring for the full trace, including the one gap this
doesn't cover (IBKR — pre-existing, `future-ideas.md` #25).

## What changed for the four volume-gated strategies — checked directly, not assumed

`volume_regime_score`/`volatility_regime_score` can now genuinely be
non-zero. That does **not** mean every scenario fires for every
strategy. Ran `volume_gated_baseline` against all four with this seam
active:

- **Momentum now genuinely fires** (`outcomes_recorded=1`) — an
  unintended side effect of `fixture_daily_history.py`'s specific
  numbers, not engineered to happen, and not something to rely on.
- **ORB, Gap, Volume Spike still return `outcomes_recorded=0`** — not
  blocked by the volume gate anymore, but by their own other MATCH-stage
  shape requirements (no real opening-range breakout shape, no
  overnight gap, no spike-shaped volume burst). Building new,
  deliberately-shaped scenarios for these three is explicitly out of
  scope for this delivery, per the task's own boundary.

## A genuinely separate finding — read before choosing a `symbol` for `POST /backtest/run`

Closing this gap means `FeatureEngine`'s real, unmodified Daily Levels
reconciliation (`_reconcile_and_persist_daily_levels()`) now actually
runs during a backtest for the first time — and it writes real rows
into the SAME shared `symbols`/`daily_levels_state` tables live trading
reads, under whatever `symbol` label the caller supplies, with no
`is_backtest` flag on either table. Confirmed by direct execution
against a real Postgres:

1. A `symbol` label colliding with a real, live-tracked ticker lands
   synthetic backtest-derived daily levels in the same rows live
   trading reads.
2. Re-running the identical `(symbol, scenario)` pair a second time
   silently reverts `volume_regime_score`/`volatility_regime_score` to
   `0.0` for that run — `_maybe_refresh_daily_levels()`'s own
   pre-existing "restart-survival" short-circuit finds the first run's
   persisted row and skips the raw-candle-cache population entirely,
   before ever asking the provider again.

Neither is fixed here — this is orthogonal to which fork option got
picked (inherent to any historical-provider seam existing at all).
Presented to Saqib as three options (accept + document; have
`BacktestRunner` clean up after itself; add real `is_backtest`
namespacing to the two tables); confirmed: accept + document loudly +
flag as a new open item (**D18**, `strategy-engine-design.md`), not
fixed this round. **Practical guidance: pick a `symbol` label that
doesn't collide with anything live-tracked, and don't rely on
re-running the same scenario twice to double-check a result.**

## Two genuine numbering collisions in a row, reconciled per Saqib's new process rule

This delivery's own work was carried out citing #133 throughout,
confirmed correct via the standard three-source check at session start.
A parallel frontend session (the "Backtest Results" panel) also claimed
#133 and merged first, mid-session — not discovered until the same
three-source re-check this log's protocol already requires immediately
before writing a new entry. Per Saqib's own new standing instruction to
distinguish "current observed next decision number" from "decision
number assigned to this delivery": at that first re-check, **observed:
#134, assigned: #134** — every citation renumbered, `strategy-engine-
design.md` §7 rebased onto that session's own real content. Before
packaging finished, a **second** parallel frontend session (linking
`BacktestPanel.tsx`/`BacktestResultsPanel.tsx` via a shared `run_id`)
independently also claimed **#134** — caught by a further re-check
immediately before the actual zip was written, the same discipline
applied a second time rather than assumed sufficient after the first
catch. **Final observed: #135. Final assigned: #135** — not #133, not
#134. Every internal citation across this delivery's code, tests, and
docs was renumbered accordingly before packaging (`grep -c "#134"`
returns 0 across every file this delivery touches; the two remaining
real `"#133"`/`"#134"` references are the two parallel sessions' own,
legitimate entries, confirmed untouched). `strategy-engine-design.md`
§7 required two successive rebases, not a copy-over: this session's
working copy had originally been edited against a base pulled before
either parallel session's work landed, so it was missing both sessions'
entire real as-built notes — re-based twice onto freshly re-pulled
`main`, with this delivery's own note inserted after both of theirs
each time, never in place of either. Confirmed file-disjoint both ways,
both times (zero backend files touched by either parallel session,
zero `frontend/` files touched by this delivery).

## Tests

Four new tests in `backend/tests/test_backtest_runner_regression.py`'s
new section 5:

- `test_historical_provider_guard_serializes_concurrent_installs` /
  `test_historical_provider_guard_restores_prior_value_even_on_exception`
  — the same two shapes `test_engine_singleton_guard_*` already covers,
  applied to this new seam. DB-free (plain sentinel objects — this seam
  only touches an in-memory global).
- `test_historical_provider_guard_restores_none_when_nothing_installed_before`
  — this module's own second restore branch (`prev is None` →
  `clear_historical_provider()`), which `engine_singleton_guard.py`'s
  unconditional-reassignment restore never needed.
- `test_backtest_runner_volume_gated_baseline_produces_nonzero_regime_scores`
  — real end-to-end regression against real Postgres. Proves every
  replayed candle's persisted `market_state_history` row now has
  non-zero `volume_regime_score`/`volatility_regime_score`, where every
  one was exactly `0.0` before this decision. Uses `len(rows) >=
  len(candles) - 1`, not strict equality — checked directly against the
  unmodified baseline before assuming this was a bug in the new seam:
  the real, pre-existing `MarketStateEngine`/`ReplayStateProducer`
  pipeline already drops the final candle's persistence before
  `producer.stop()` completes, true on `main` today, unrelated to this
  decision.

## How to verify

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # or your usual env
pip install -r requirements.txt --break-system-packages

# Fresh DB, per this project's standing convention
psql -c "CREATE USER trading WITH PASSWORD 'trading' SUPERUSER;"
psql -c "CREATE DATABASE trading_workspace OWNER trading;"
cp .env.example .env
alembic upgrade head

python -m pytest tests/ -q
```

Expected: 719 collected. Failures will range from 0 to 3 depending on
run — every failure you might see belongs to the long-documented,
4-test #119 flaky cluster (`test_vwap_publishes_even_while_sma_is_still_warming_up`,
`test_daily_levels_carry_level_interaction_once_touched`,
`test_sma_ema_slope_family_groups_under_the_owning_period_and_is_excluded_from_level_interaction`,
`test_feature_engine_backfills_from_persisted_history_on_cold_start`) —
named across decisions #93/#113/#114/#116-119/#122-133, each
independently reconfirmed pre-existing and sandbox-timing-sensitive by
multiple prior sessions, unrelated to `strategy_engine/`/
`feature_engine/engine.py`. Two full runs during this delivery's own
verification: one showed 718 passed/1 failed (just the first test
above), another showed 716 passed/3 failed (three of the four at
once) — consistent with decision #119's own documented "flickers
between 1-3 failing per run" signature, not a new or worsened pattern.
Every individual failure reconfirmed passing in isolation immediately
after. None of this delivery's own 4 new tests were ever among the
flickering set, in any run.

**Manually confirming the fix directly** (optional, the automated test
above already proves this):

```bash
# with the backend running, Finnhub/Polygon both disconnected
curl -s -X POST "http://localhost:8000/backtest/run?strategy=Momentum&scenario=volume_gated_baseline&symbol=ZDEMO01" | python -m json.tool
```

`outcomes_recorded` should be `1` (Momentum's own unintended fire — see
above). Pick a `symbol` that isn't a real ticker you're tracking live,
and don't re-run this exact command a second time expecting the same
non-zero regime scores underneath (see "A genuinely separate finding"
above).

## What was deliberately NOT built

- **No new guaranteed-fire scenarios for ORB/Gap/Volume Spike.**
  Engineering scenario candles to guarantee a fire for each is real,
  separate, later work with its own judgment calls — flagged, not
  built, per this task's own explicit scope boundary.
- **No fix for the write-side-effect finding.** See "A genuinely
  separate finding" above — documented and flagged as D18, not
  resolved this round, per Saqib's own confirmed direction.
- **No IBKR fix for decision #132's live-data guard.** A pre-existing,
  unrelated gap, flagged as `future-ideas.md` #25, not fixed here.
- **No changes to `HistoricalContextProvider`/`context_provider.py`,**
  or to live-provider vendor selection (`future-ideas.md` #17) — a
  different, already-documented axis of "historical data," untouched.
- **No changes under `frontend/`.** This delivery is backend-only.
