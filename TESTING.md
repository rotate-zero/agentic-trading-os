# TESTING.md — Two flagged issues fixed, plus a backfill they surfaced (decisions #100-103)

## What's in this zip

```
backend/app/market_state_engine/engine.py          (modified — the timeframe race fix)
backend/tests/test_market_state_engine.py          (modified — one line: _latest_features seed key)
backend/tests/test_market_state_engine_timeframe_race.py   (new — 4 tests)
docs/architecture/premarket-accumulator-design.md  (modified — status line corrected)
docs/architecture/system-design.md                 (modified — FeaturesUpdated table gains vwap_ext/session_volume_ext/premarket_volume_ratio)
docs/decisions/confirmed-decisions.md              (modified — new #100, #101, #102, #103)
docs/decisions/INDEX.md                            (modified — new rows #100-103)
```

Unzip directly onto project root. Nothing to delete first — every file here is either modified in place or new.

## Issue 1 — `MarketStateEngine`'s timeframe race (decision #103)

Fixed as flagged. `_latest_features` was one shared slot per symbol regardless
of timeframe — 1m/5m/15m/1h `FeaturesUpdated` all overwrote it, whichever
arrived last, so `trend_score`/`volume_regime_score` could reflect the
wrong timeframe's close. Now keyed `(symbol, timeframe)`, and `_compute()`
always reads the `(symbol, "1m")` slot specifically. As a direct
consequence, `_on_features_updated` only schedules a recompute for 1m
arrivals now — a 5m/15m/1h arrival would only have re-derived the same
state from unchanged 1m data underneath it, so triggering off it was pure
waste once the read side was fixed.

**Worth knowing:** any symbol running Market State at anything other than
1m granularity now gets `None` from `get_snapshot()` until its 1m
`FeaturesUpdated` has arrived at least once — even if 5m/15m/1h already
reported. This is intentional (honest absence, not a fabricated
cross-timeframe substitute) but means Market State is now, structurally,
a 1m-anchored read. Worth knowing before trusting it for anything that
isn't 1m — ORB already is, so this doesn't change anything for it.

`backend/tests/test_market_state_engine_timeframe_race.py` is deliberately
DB-free — it calls `_compute()`/`_on_features_updated()` directly (both
pure/in-memory), unlike `test_market_state_engine.py`'s own tests, which
stay gated behind that file's Postgres skip. Run it standalone:

```bash
cd backend
pytest tests/test_market_state_engine_timeframe_race.py -v
```

Expect 4 passed, 0 skipped, no DB needed. Then your full local suite
(with real Postgres) to also re-confirm `test_market_state_engine.py`'s
own DB-gated tests still pass through the real worker loop — not
independently re-verified in this sandbox (no local Postgres here),
same limitation every DB-gated file in this project already carries.

## Issue 2 — turned out bigger than a stale caption (decisions #100-102)

I went to fix `premarket-accumulator-design.md`'s status line and found
the doc's own §6/§7/§8 already describe THREE complete, tested builds —
`vwap_ext`/`session_volume_ext`, `premarket_volume_ratio`, and its Scanner
integration — none of which was ever logged in `confirmed-decisions.md`
or `INDEX.md`. Same systemic gap decision #99 already found once in
Strategy Engine (real work landing without a decision-log entry), this
time in Feature Engine/Scanner.

**Nothing here is new code** — I formalized what was already built and
verified (per the design doc's own recorded test counts: 245/245, then
255/255, then 268/269) into proper decisions #100/#101/#102, corrected
the design doc's status line to point to them, and filled in
`system-design.md`'s `FeaturesUpdated` table, which was missing
`vwap_ext`/`session_volume_ext`/`premarket_volume_ratio` entirely.

**Not independently re-verified in this session** — no local Postgres
available here. The test counts in #100-102 are exactly what the design
doc already recorded at build time, not re-run.

## How to verify

```bash
cd backend
pytest tests/test_market_state_engine_timeframe_race.py -v   # new, DB-free, 4 tests
pytest tests/ -q                                              # full suite
```

Verified in this sandbox (no local Postgres): identical 40 pre-existing
DB-connectivity failures and 91 skipped, both unchanged from before this
fix, plus these 4 new tests passing — 281 passed vs. 277 immediately
before. Run against your real local Postgres for the first genuine
DB-backed confirmation of the `MarketStateEngine` change specifically.
