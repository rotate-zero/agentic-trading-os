# TESTING.md — decision-113-momentum-vwap-built

Application code + docs. Closes out the full v1 strategy set (all 7 from
`trading-intelligence-architecture.md` §8).

## Before unzipping

**Delete the existing root `TESTING.md` first** — this replaces it
entirely, same convention as every prior delivery.

## What changed

- `backend/app/strategy_engine/momentum_strategy.py` — **new.** Sixth
  v1 strategy, first consumer of `MarketState.acceleration_score`.
- `backend/app/strategy_engine/vwap_strategy.py` — **new.** Seventh and
  last v1 strategy, disjoint from `reversal_strategy.py` by construction.
- `backend/app/strategy_engine/scoring_utils.py` — `ESTABLISHED_TREND_SCORE_THRESHOLD`
  and `trend_established_side()` added.
- `backend/app/strategy_engine/reversal_strategy.py` — refactored onto
  the shared constant/helper above. **Behavior unchanged** — all 11
  existing tests re-run unmodified and still pass.
- `backend/app/strategy_engine/orb_strategy.py` — one stale comment
  corrected (it referenced the discarded draft's momentum threshold
  value as precedent; this rebuild deliberately doesn't reuse it).
- `backend/tests/test_momentum_strategy.py` — **new, 18 tests, no DB dependency.**
- `backend/tests/test_vwap_strategy.py` — **new, 19 tests, DB-gated** (skips
  cleanly as a whole if Postgres isn't reachable, same convention as
  `test_reversal_strategy.py`/`test_first_pullback_strategy.py`).
- `backend/tests/test_scoring_utils.py` — +6 tests (now 17) for the new
  shared constant/helper.
- `docs/architecture/strategy-engine-design.md` — new §18 (full design +
  build account, with diagrams); §10 gets new item **D11**; §12/§13
  updated to reflect all 7 strategies built and **Stage 2 (D10) now
  unblocked**.
- `docs/architecture/trading-intelligence-architecture.md` — §8's summary
  paragraph updated to reflect all 7 strategies built.
- `docs/decisions/confirmed-decisions.md` — new entry **#113**.
- `docs/decisions/INDEX.md` — row added for #113.

## How to verify

**The DB-gated tests need a real Postgres**, same standing requirement
as every `LevelInteractionEngine`-dependent strategy test in this repo
(`test_reversal_strategy.py`, `test_first_pullback_strategy.py`). This
delivery was verified against a real, locally-provisioned Postgres 16 —
not just the DB-free subset — including the two most logically intricate
new VWAP scenarios (a same-zone-repeat suppression across an
established-trend window, and a day-rollover reset), which would
otherwise only be trace-verified by hand:

```bash
# 1. Confirm your existing Postgres matches app/core/config.py's defaults
#    (postgres_user=trading, postgres_password=trading, postgres_db=trading_workspace,
#    localhost:5432) or set POSTGRES_* env vars / .env to match your own instance.

# 2. Migrations (only needed once, or after a schema change — none in this delivery)
cd backend && python3 -m alembic upgrade head

# 3. Run the new suites directly
python3 -m pytest tests/test_momentum_strategy.py tests/test_vwap_strategy.py \
    tests/test_scoring_utils.py tests/test_reversal_strategy.py -v

# 4. Full regression
python3 -m pytest tests/ -q
```

Expect **37 new tests, all passing** (18 Momentum + 19 VWAP), **6 new**
`scoring_utils.py` tests, and **all 11 existing Reversal tests unchanged**.

**Two pre-existing failures you'll likely still see, unrelated to this
delivery — confirmed against a completely untouched clone of `main`
before this work started:**
- `test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up`
- `test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched`

Both fail identically with zero changes applied — not introduced by this
delivery, not investigated further here (out of scope). Worth a look in
a future session if they're still bothering you.

**Quick doc-consistency check:**
```bash
grep -n "^113\." docs/decisions/confirmed-decisions.md
grep -n "| 113 |" docs/decisions/INDEX.md
grep -n "^## 18\." docs/architecture/strategy-engine-design.md
```

## Unzip instructions

Unzip directly at the project root — overwrites/adds the files listed
above. Confirmed against your current `main` (the tree with real
decision #112 in it) before this zip was built.
