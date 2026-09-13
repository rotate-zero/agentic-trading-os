# TESTING.md — Decisions #131 & #132: Backtest Runner trigger route reconciliation + live-data safety guard

## What this delivery is

Two things, landed together:

1. **Decision #131 — retroactive documentation.** The Backtest Runner
   trigger route (`POST /backtest/run`) was already built, working, and
   on `main` before this delivery started. It had never gotten its own
   decision-log entry — every file it touched incorrectly self-cited
   "(decision #130)," a number decision #130 (the unrelated
   `/strategy-outcomes` `is_backtest` filter) had already legitimately
   taken. #131 is that missing entry, written after the fact — it
   documents and corrects attribution for code that already shipped,
   it does not introduce new behavior.
2. **Decision #132 — a real new fix.** While verifying #131, the route's
   own docstring turned out to only *warn* that calling it against a
   live-trading process is unsafe, without actually enforcing that.
   Raised directly by Saqib; closed by making it a real `409` instead
   of a warning nobody is forced to read.

Full reasoning for both lives in `docs/decisions/confirmed-decisions.md`
(#131, #132); this file covers what to run to verify them and what a
reader should know before touching this route again.

## Files changed

**Decision #131 (misattribution fix only — no logic changed):**
- `backend/app/api/routes/backtest.py` — docstring's decision-number
  citation corrected.
- `backend/app/backtest_runner/scenarios.py` — same.
- `backend/app/strategy_engine/scheduler.py` — same (the
  `_default_registry()` → `default_registry()` rename itself was
  already-shipped, pre-existing work; only its docstring's citation
  changed here).
- `backend/tests/test_backtest_routes.py` — same (module docstring's
  citation).

**Decision #132 (real code change):**
- `backend/app/api/routes/backtest.py` — new
  `_reject_if_live_data_connected()`, called at the top of
  `run_backtest()` before any engine or `BacktestRunner` construction;
  raises `409` if either Finnhub or Polygon is currently connected.
  Route docstring updated from a warning to a statement of enforcement.
- `backend/app/api/routes/finnhub_data.py` — new `is_connected()` public
  accessor (no behavior change; mirrors the existing `/finnhub/status`
  computation).
- `backend/app/api/routes/market_data.py` — new `is_connected()` public
  accessor (same, mirrors `/market-data/status`).
- `backend/tests/test_backtest_routes.py` — two new tests:
  `test_run_backtest_rejects_when_finnhub_connected`,
  `test_run_backtest_rejects_when_polygon_connected`.

**Both decisions:**
- `docs/decisions/confirmed-decisions.md` / `docs/decisions/INDEX.md` —
  new #131 and #132 entries.

## What was deliberately NOT built

- **No auth system.** Grepped the entire `backend/app/api/routes/` tree
  before deciding this — no `Depends`/`HTTPBearer`/`APIKeyHeader`
  pattern exists anywhere in this codebase today. Adding one for a
  single route would be a larger, inconsistent change; the live-data
  connection check already solves the actual risk precisely.
- **No new settings flag.** A `settings.allow_backtest_trigger`-style
  bool was considered and explicitly rejected in favor of reusing
  `is_connected()` — a flag is one more thing to remember to leave off
  in production; the connection check can only ever fire when live data
  is genuinely flowing, in dev or prod alike.
- **No fix for the intermittent `ForeignKeyViolation` investigated
  during #131's own verification** — because there was nothing in this
  delivery's code to fix. See "A note on test flakiness" below.
- **`backend/app/backtest_runner/runner.py`, `engine_singleton_guard.py`,
  `main.py`'s router wiring, `broker.py`, and every file already
  correctly attributed to decision #130** — untouched, confirmed by
  `diff -rq` against a freshly re-pulled clone.

## A note on test flakiness encountered while verifying this delivery

During #131's own initial full-suite verification,
`test_run_backtest_first_pullback_scenario_fires_and_persists` failed
twice with a genuine Postgres-server-side `ForeignKeyViolation`. This
was investigated directly rather than patched around or ignored:

- Decision #128's own pre-existing test of the identical
  write-then-insert sequence (`test_backtest_runner.py`) never failed,
  in this session or historically.
- Multiple standalone reproductions of the exact same sequence — both
  via direct `BacktestRunner` construction and via `TestClient`,
  entirely outside pytest — succeeded every time.
- Postgres's own server log showed an unambiguous hard crash during
  this session (`database system was not properly shut down; automatic
  recovery in progress`, following simultaneous `Connection reset by
  peer` messages on every open connection) — consistent with an
  out-of-memory kill in this sandbox's constrained (3.9GB) container.
- After restarting Postgres, the identical suite ran clean multiple
  times in direct succession, including one full run of 715 passed / 0
  failed.
- Postgres was separately observed to die a second time with zero query
  activity in the intervening window — ruling out this delivery's own
  test load as the sole trigger.

**Conclusion: this is a sandbox-environment artifact, not a defect in
`BacktestRunner`, the trigger route, or anything else in this
delivery.** It's recorded here and in decision #131 for visibility, not
as an open bug to track. If it resurfaces on a properly provisioned
machine (real CI, a dev box not memory-constrained to under 4GB), that
would change this conclusion and should be re-investigated from
scratch rather than assumed to be the same cause.

## How to verify

```bash
# Full suite, real Postgres 16 (matches this project's standing convention)
cd backend
alembic upgrade head
python3 -m pytest -q
```

Expect **715 collected**. One pre-existing, documented flake may appear:
`test_daily_levels_carry_level_interaction_once_touched` (the #119
cluster) — order/timing-sensitive, unrelated to this delivery,
intentionally left unfixed per standing project convention. Everything
else should pass.

```bash
# Just this delivery's own tests (7 total; 2 are genuinely slow, ~130s each —
# EngineBackedReplayStateProducer's real ~1s/candle engine-settle cost,
# not a bug — see backtest.py's own docstring)
python3 -m pytest -q tests/test_backtest_routes.py
```

The two new #132 guard tests
(`test_run_backtest_rejects_when_finnhub_connected`/`..._polygon_connected`)
are fast — they monkeypatch `is_connected()` rather than standing up a
real connection, since the guard fires before any candle is replayed.

No frontend changes in this delivery — `npx tsc -b`/`npx vite build`
were not re-run, since neither `frontend/` file was touched.
