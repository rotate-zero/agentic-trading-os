# TESTING.md — Backtest Runner trigger route (decision #130)

## What this documents

`POST /backtest/run` (`backend/app/api/routes/backtest.py`) — the first
real, directly HTTP-callable entry point to `BacktestRunner`, closing
the "optional API route" decision #128 deferred. Full reasoning:
decision #130, `docs/decisions/confirmed-decisions.md`.

### Files touched by this delivery

- `backend/app/api/routes/backtest.py` — **new.** The route itself.
- `backend/app/backtest_runner/scenarios.py` — **new.** Named fixture
  scenario registry.
- `backend/app/backtest_runner/fixtures/*.csv` — **new**, 4 files.
  `first_pullback_vwap_dip.csv`, `reversal_vwap_break.csv`,
  `vwap_neutral_conquest.csv`, `volume_gated_baseline.csv`.
- `backend/app/main.py` — 2-line router registration.
- `backend/app/strategy_engine/scheduler.py` — `_default_registry()`
  promoted to public `default_registry()` (Saqib's direct call).
  Behavior unchanged; name and one docstring note only.
- `backend/tests/test_strategy_scheduler.py` — import/usage updated for
  the rename above (4 call sites). No behavior/coverage change.
- `backend/tests/test_backtest_routes.py` — **new.** Real HTTP-level
  tests for the route.
- `docs/architecture/strategy-engine-design.md` — §7 extended with a
  small new diagram + two findings (below); new open item D18 in §10.
- `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md` —
  new decision #130 entry + index row.
- This file.

**Zero touch to any of this task's explicit boundary files** —
`intelligence.py`, `performance_queries.py`, any of the 7 real strategy
files, `performance.py`, `models/trading_intelligence.py`,
`schemas/performance.py`, or any frontend file. Confirmed by `diff -rq`
against a freshly re-pulled untouched clone — see "Fresh-clone diff
verification" below.

## Two findings that shaped this delivery, not just documentation notes

### Finding 1 — `volume_regime_score`/`volatility_regime_score` are structurally always `0.0` in any BacktestRunner replay

Found while trying to build a guaranteed-fire ORB scenario: a clean,
steep synthetic breakout still produced `volume_regime_score == 0.00`
for the entire replay. Traced to source, then confirmed by direct
execution rather than assumed from the trace: `FeatureEngine`'s
`rvol`/`atr_14_pct` are populated exclusively from
`self._daily_candle_cache`, itself populated only by
`_maybe_refresh_daily_levels()` calling
`broker_registry.get_historical_provider().get_historical(symbol, "1d",
...)`. `EngineBackedReplayStateProducer` constructs a brand-new
`FeatureEngine` with no historical provider wired in at all, and
`BacktestRunner` never touches `broker_registry` — so both scores are
structurally `0.0` for any replay, for any symbol, regardless of how the
fixture candles are built.

Checked against every real strategy's actual MATCH-stage code (not
docstrings):

| Strategy | MATCH-stage volume gate? | Can fire via BacktestRunner today? |
|---|---|---|
| ORB | `volume_regime_score < 45 → None` | **No** |
| Gap | `volume_regime_score < 45 → None` | **No** |
| Volume Spike | `volume_regime_score < 45 → None` | **No** |
| Momentum | `volume_regime_score < 45 → None` | **No** |
| First Pullback | none — volume only affects SCORE | Yes |
| Reversal | none — volume only affects SCORE | Yes |
| VWAP | none — volume only affects SCORE | Yes |

Raised directly to Saqib with three options before proceeding (guaranteed-fire
for the 3 reachable strategies + honest best-effort for the other 4, log
the gap; build a temporary fixture daily-history seam into
`broker_registry` so all 7 can fire; fall back to generic scenarios) —
the first was chosen. Tracked as new open item **D18**
(`strategy-engine-design.md` §10) rather than worked around silently.

### Finding 2 — replay costs a real, measured ~1 second per candle

`EngineBackedReplayStateProducer` costs a genuine ~1 second of
engine-settle time per replayed candle (confirmed by direct per-candle
timing, not estimated) — so this route's response time is proportional
to scenario length, not request-processing overhead. Raised directly by
Saqib as a real API-design concern mid-task, not something to leave as a
docstring footnote:

- Checked this deployment's actual `Dockerfile`/`docker-compose.yml`:
  single uvicorn worker, no reverse proxy, no `--timeout-keep-alive`
  override — nothing in this stack today would truncate a multi-minute
  synchronous request.
- Confirmed directly that `TestClient`'s in-process ASGI transport does
  **not** enforce httpx's normal 5-second default client timeout (a
  throwaway `asyncio.sleep(7)` endpoint returns successfully through it)
  — so a passing HTTP-level test alone would not prove real latency
  either way. The new tests measure their own wall-clock elapsed time
  and assert a floor for exactly this reason (see below).
- Deliberately did **not** add background-job, polling, or webhook
  infrastructure — no such pattern exists anywhere else in this
  codebase, and this task's scope is "a route + strategy lookup," not
  new async infrastructure. Stated explicitly in the route's own
  docstring as a deliberate v1 trade-off, not an oversight.

## Scenarios, each verified by direct execution against the real `BacktestRunner`

Not reasoned about from reading strategy source, and re-verified a
second time from their actual checked-in CSV form (not just the
in-memory candle objects used to build them):

- **`first_pullback_vwap_dip`** (130 candles) — established uptrend +
  a single engineered dip into VWAP's aura band that bounces back out
  the same side (REJECTED), genuinely the day's first touch. Real
  persisted row: `FirstPullback, BUY, entry=106.585, exit=107.857,
  exit_reason=target, realized_r=2.0`.
- **`reversal_vwap_break`** (140 candles) — same uptrend base, but the
  dip breaks all the way through VWAP (CONQUERED) instead of bouncing;
  the established uptrend resumes afterward (the reversal attempt
  fails), cleanly stopping the resulting SELL out. Real persisted row:
  `Reversal, SELL, entry=105.314, exit=105.949, exit_reason=stop,
  realized_r=-1.0`.
- **`vwap_neutral_conquest`** (140 candles) — flat/choppy session
  (trend_score held ~50 throughout, confirmed against
  `scoring_utils.trend_established_side`'s exact neutral band) with a
  genuine VWAP conquest partway through. Real persisted row: `VWAP,
  SELL, entry=99.978, exit=99.087, exit_reason=target, realized_r=2.0`.
- **`volume_gated_baseline`** (120 candles) — generic moderate-uptrend
  session, deliberately not shaped to attempt triggering any particular
  strategy (see Finding 1 — a shaped-but-structurally-futile scenario
  would misrepresent the situation, not honestly represent it).
  Verified clean (`outcomes_recorded=0, discarded_signals=[]`, no
  errors) against all four volume-gated strategies individually.

Any `(strategy_name, scenario)` pair is accepted by the route — the
per-strategy names describe what each scenario was built to
demonstrate, not a restriction.

## Tests

`backend/tests/test_backtest_routes.py`, real Postgres, DB-gated (same
`_db_available()`/`pytestmark.skipif` convention as the rest of this
suite). Deliberately does not re-test `BacktestRunner`'s own
orchestration — that's `test_backtest_runner.py`'s job.

```
cd backend
python -m pytest -q tests/test_backtest_routes.py
```

5 tests:

- `test_run_backtest_first_pullback_scenario_fires_and_persists` —
  **~130s, deliberately.** Asserts the real response shape, a real
  persisted `strategy_outcomes` row (queried directly, not just trusted
  from the response body), and `elapsed > 60` as a floor — specifically
  so a future change that accidentally short-circuits the replay would
  fail this test rather than silently ship a faster-but-wrong response.
- `test_run_backtest_volume_gated_strategy_returns_honest_zero` —
  **~120s, deliberately.** Confirms `outcomes_recorded=0` is a real 200,
  not an error, for the documented volume-gated case.
- `test_run_backtest_rejects_unknown_strategy_name` — fast, 400 with
  valid names listed.
- `test_run_backtest_rejects_unknown_scenario` — fast, 400 with valid
  scenarios listed.
- `test_run_backtest_requires_all_three_query_params` — fast, FastAPI's
  own 422 for a missing required param (confirms no silent default).

Run both individually (each pass) and together (5 passed in 247.52s) —
100% stable both ways. The ~248s combined runtime is a real, deliberate
cost this test file's own module docstring explains, not something to
work around.

## Verification

Real local Postgres 16 (provisioned directly in this session —
`apt-get install postgresql`, `service postgresql start`, `trading`/
`trading`/`trading_workspace`, `alembic upgrade head`, fresh
wipe-and-recreate before both the baseline and the final run).

**Baseline** (freshly re-pulled untouched clone):

```
cd backend
python -m pytest -q
```

**702 collected, 701 passed, 1 failed** —
`test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched`,
the #119 cluster's own documented intermittent flake, matching decision
#129's own reported post-fix baseline exactly.

**Working copy, all of this delivery's changes included:**

```
cd backend
python -m pytest -q
```

**707 collected, 706 passed, 1 failed** — the identical single failure,
exactly +5 collected/passed (this delivery's own new test file), zero
regressions. Confirmed both with the new test file run in isolation
per-test and as one combined file run.

`test_strategy_scheduler.py`'s own 30 tests re-run clean immediately
after the `default_registry()` rename, before building anything on top
of it.

No frontend files touched by this delivery — `npx tsc -b`/`npx vite
build` not re-run.

## Fresh-clone diff verification

`diff -rq` against a freshly-pulled untouched clone of current `main`,
taken at the start of this task and re-checked (decision-log tail only)
immediately before writing decision #130, confirms this delivery's own
change set is exactly the "Files touched" list above.

## Known limitations / deferred, not done here

- **D18** (new): ORB/Gap/Volume Spike/Momentum cannot fire through
  `BacktestRunner` today, for any fixture, because of Finding 1 above.
  Closing this means wiring a fixture daily-history provider into the
  replay stack specifically for `_maybe_refresh_daily_levels()` to
  find — real, separate work, genuinely bigger than it first looks
  (`broker_registry` is a process-wide singleton also used by live
  trading). Not attempted here.
- Replay latency (~1s/candle) is inherited, unmodified,
  `EngineBackedReplayStateProducer` behavior — not something this task
  changed or optimized. No background-job/polling infrastructure added.
- No Performance Analytics UI wiring for this route's results — a
  caller wanting the raw persisted rows can already query the existing,
  unmodified `/intelligence/strategy-outcomes` separately.
