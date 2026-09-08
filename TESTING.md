# TESTING.md — decision-114-strategy-scheduler-built

Stage 2, Track A: the Strategy Scheduler (`app/strategy_engine/scheduler.py`).
Track B (OpportunityCreated read-side cache + `GET /intelligence/opportunities`)
is a separate parallel session's delivery, not included here — see the
MANUAL MERGE note below for `main.py`/`conftest.py`.

## What changed

- **New:** `backend/app/strategy_engine/scheduler.py` — `StrategyScheduler`,
  `_default_registry()`, `get_strategy_scheduler()`. Read the module
  docstring first — it documents a real design correction found during
  testing (originally triggered off `FeaturesUpdated`, corrected to
  `MarketStateChanged`; see decision #114/D12) that matters for
  understanding the two-handler split.
- **New:** `backend/tests/test_strategy_scheduler.py` — 20 tests, pure
  unit tests plus two real-engine integration tests.
- **Edited, additive only:** `backend/app/main.py` — imports
  `get_strategy_scheduler`, starts it after `context_engine.start()`,
  stops it after the bus in shutdown. Nothing else in this file touched.
- **Edited, additive only:** `backend/tests/conftest.py` — added the
  `_strategy_scheduler = None` singleton reset every other engine already
  has in the autouse fixture (was missing; would have been a real
  cross-test leak).
- **Docs:** `docs/architecture/strategy-engine-design.md` (§10 new D12,
  §12 staged plan updated), `docs/decisions/confirmed-decisions.md` (new
  #114), `docs/decisions/INDEX.md` (row for #114).

## MANUAL MERGE NOTE — main.py and conftest.py

Both files are also touched independently by Track B's delivery (the
OpportunityCreated read-side), flagged when that work was scoped out.
If Track B's zip lands separately:

- **`main.py`**: Track B adds its own `OpportunityCache` start()/stop()
  block. This delivery's Scheduler block is clearly commented and
  self-contained — merge by adding Track B's block alongside it, not
  replacing either.
- **`conftest.py`**: Track B needs its own singleton reset line added to
  the same `_reset()` function this delivery's `strategy_scheduler_module`
  line was added to. Two one-line additions to the same function, trivial
  to combine by hand.

## How this was verified

Real PostgreSQL 16, provisioned directly for this session (same posture
as decision #113) — not mocked, not DB-free-only.

```
pip install -r backend/requirements.txt
# Postgres running locally, matching backend/app/core/config.py defaults
# (trading/trading@localhost:5432/trading_workspace) or your own .env
cd backend
alembic upgrade head
pytest tests/test_strategy_scheduler.py -q     # 20 passed
pytest tests/ -q                               # 566 passed, 2 failed
```

The failures are **pre-existing and unrelated** — confirmed directly
against a completely untouched clone of `main` (no scheduler.py, no
main.py/conftest.py edits at all): that clone itself fails the same way.

**Correction to decision #113's own count:** #113 recorded "2 pre-existing
failures" from its own untouched-clone check. Running the same untouched
clone's full suite in this session produced **3** failures, consistently:

- `test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up`
  — fails consistently, every run, on the untouched clone and with this
  delivery applied alike.
- `test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched`
  — order/timing-sensitive: failed most runs, passed one, on the
  untouched clone alone (no changes from this delivery involved).
- `test_intelligence_routes.py::test_sma_ema_slope_family_groups_under_the_owning_period_and_is_excluded_from_level_interaction`
  — failed consistently (3/3) in isolation, and on the untouched clone's
  full suite, in this session's runs. Not mentioned in #113's count —
  most likely #113's particular run happened not to hit it, not that it's
  newly broken; a `KeyError: 'level_interaction'` on a dict that's
  supposed to have that key populated. Notably the same general area
  (`level_interaction` zone assignment) as the daily-levels failure above
  — possibly a shared root cause in how a test sets up that computation
  and races it, not necessarily two unrelated flaky tests. Not
  investigated further here — out of scope for this delivery, flagged
  rather than fixed or silently dropped from the count.

None of the three touch anything in this diff. Baseline with this
delivery applied: 565 passed, 3 failed — the same three, zero new
failures, 20/20 new tests passing (565 + 3 = 568 = 545 baseline-passed +
3 baseline-failed + 20 new, so the arithmetic ties out exactly).

## What the two real-engine integration tests actually prove

`test_scheduler_end_to_end_real_engines_stub_strategy_publishes_opportunity`
is a **regression test**, not just a happy path — an earlier version of
`scheduler.py` (triggered off `FeaturesUpdated` directly) failed this
exact test, because `MarketStateEngine`'s debounced worker hadn't cached
anything yet by the time the handler ran. If this test ever starts
failing again, treat it as seriously as any other regression, not a flake
— it caught a real bug once already.

`test_scheduler_end_to_end_real_seven_strategies_no_crash_no_fabricated_opportunity`
runs the actual `_default_registry()` (all 7 real strategies) against a
real but MATCH-failing candle. It does not prove any strategy's own MATCH
logic fires correctly through this stack — each strategy's own test file
already covers that in isolation — only that wiring all 7 through real
engines doesn't crash and doesn't fabricate an `Opportunity`.

## Not covered by this delivery

- A real strategy's own MATCH/SCORE conditions actually firing through
  the full live stack end-to-end (engineering realistic candle sequences
  to trigger e.g. ORB's real breakout condition through real
  `MarketStateEngine` computation is real, separate effort).
- The `OpportunityCreated` read-side (Track B).
- `gate_conditions` enforcement, `active_from`/`active_to` enforcement,
  Opportunity Engine ranking — all explicitly out of Stage 2's scope (D10).

## Unzip instructions

Unzip at the project root:
- `backend/app/strategy_engine/scheduler.py` → new file
- `backend/tests/test_strategy_scheduler.py` → new file
- `backend/app/main.py` → overwrites (additive diff only — diff against
  your current copy if Track B's zip already landed, per the manual merge
  note above)
- `backend/tests/conftest.py` → overwrites (additive diff only, same
  caveat)
- `docs/architecture/strategy-engine-design.md`,
  `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md` →
  overwrite
