# TESTING.md — LevelInteractionEngine.stop() shutdown-race fix (decision #84)

## What changed

- `backend/app/trading_intelligence/level_interaction_engine.py` — `stop()`
  now enqueues a `_STOP_SENTINEL` poison-pill instead of calling
  `task.cancel()`, and `_worker_loop` recognizes it and exits cleanly.
  This is the actual bug fix.
- `backend/tests/test_level_interaction_engine.py` — one new regression
  test, `test_stop_waits_for_an_in_flight_persist_before_returning`.
- `docs/decisions/confirmed-decisions.md` — decision #84, full write-up.
- `docs/decisions/INDEX.md` — indexed entry for #84.

No frontend files touched. No new migration, no schema change.

## Unzip

From your project root:

```
unzip decision-84-level-interaction-stop-fix.zip -d .
```

This overwrites the 4 files above in place; nothing else in the repo is
touched.

## How to verify locally

Needs real Postgres, same as the rest of this suite (skipped, not failed,
if unreachable).

```
cd backend
pytest -q tests/test_level_interaction_engine.py tests/test_intelligence_routes.py
```

Expect all tests green, including the new
`test_stop_waits_for_an_in_flight_persist_before_returning`.

For extra confidence, run the full backend suite a few times back to
back — the race this fixes is timing-dependent, so a single green run
proves less than several:

```
pytest -q
```

## What was actually verified in this environment

- Real local Postgres 16 installed and migrated fresh (`alembic upgrade
  head`) specifically to verify this — not skipped, not mocked.
- Root cause reproduced empirically with a standalone script (isolated
  from this codebase) proving `task.cancel()` + `await task` returns in
  ~0ms while a `to_thread`-wrapped blocking call keeps running orphaned
  in the background — this is *why* the FK violation happens, not just a
  guess from reading asyncio's docs.
- The new regression test verified in **both directions**: confirmed it
  fails deterministically (3/3) against the old cancel-based `stop()`,
  and passes deterministically (5/5) against the fix.
- Full backend suite run **5x** after the fix: **270 passed, 0 failed,
  every run**. A second, previously-unexplained flake in
  `test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched`
  (present in a pre-fix baseline run, absent in all 5 post-fix runs) is
  treated as corroborating evidence, not independently proven — see
  decision #84's own note on this.

## What was NOT verified

- **Production shutdown under real load.** This fix intentionally makes
  `stop()` take as long as any in-flight DB write, rather than returning
  near-instantly — correct by design (see decision #84's trade-off
  section), but not observed against a live running instance under real
  traffic, only against this test suite's synthetic races.
- **`CandleRecorder.stop()` and `FeatureEngine.stop()`** — both share the
  identical `task.cancel()`-around-a-`to_thread`-worker-loop shape and
  very likely carry the same latent bug. Confirmed by direct code
  inspection while root-causing this issue, but no reproduction was
  attempted against either, and neither was touched in this change — see
  decision #84's "Scope" section for why. Flagged here again so it isn't
  missed: worth a dedicated follow-up decision before assuming either is
  safe.
- No browser/frontend involved in this change, so the usual "not
  verified in an actual browser" caveat from other entries in this log
  doesn't apply here.
