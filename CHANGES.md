# Backtest Runner v1 — Unit 5: four regression gaps closed, test-only

Copy this into your repo root, overwriting the existing path. Test-only
delivery, as scoped: **zero changes to any production file** — confirmed
directly (`diff -rq` of `backend/app/` against a fresh pull of current
`main`, clean; `backend/app/backtest_runner/` byte-for-byte identical to
what's already committed there). One new file:
`backend/tests/test_backtest_runner_regression.py` (20 tests).

## What each of the four sections proves

**1. Runner-level D17 enforcement (3 tests, real Postgres).** Mocks
`capture_strategy_outcome_snapshots` at `app.backtest_runner.runner`'s
own namespace — the name as the Runner actually calls it, not just
`gate_and_warmup.check_entry_allowed()` in isolation (which Unit 4's
D17 finding showed isn't the Runner's real gate anymore). Proves: (a)
`None` at the fill candle → signal discarded, `record_strategy_outcome()`
never called, no exit-time capture even attempted (position was voided
first); (b) `None` at the exit candle → outcome not persisted, no
fabricated `{}` substituted; (c) real dicts at both instants →
`record_strategy_outcome()` called exactly once, with those exact dicts
flowing through to `market_state_at_entry`/`context_at_entry`/`_at_exit`
unchanged — confirming the mock target genuinely matches what the Runner
calls, not a coincidence.

```text
Opportunity
    |
capture snapshots (at fill candle, then again at exit candle)
    |
 present?
  no -> discard signal, DiscardedSignal recorded, never call record_strategy_outcome()
  yes -> persist outcome
```

**2. `engine_singleton_guard` serialization (2 tests, DB-free).** The
new one proves actual mutual exclusion, not merely an observed call
order: two coroutines race for the guard via `asyncio.gather`, one holds
it for 0.2s, the other for 0.05s, both timestamp their own enter/exit
instants with `time.monotonic()`. The assertion checks the two
`[enter, exit]` intervals don't overlap **in either possible
acquisition order** — a coincidental pass isn't possible the way a bare
"B's log line came after A's" check could be. Kept the existing
restore-on-exception test alongside it, same file now.

```text
Run A: enter -----0.2s hold----- exit
Run B:                            enter --0.05s-- exit
                    ^-- B cannot enter here; the lock blocks it
```

**3. Polygon `NOT_AUTHORIZED` propagation (1 test, DB-free — the
exception fires before `BacktestRunner` ever touches the DB).** Reuses
`test_polygon_provider.py`'s own established seam
(`adapter._client.get_aggs` monkeypatched to raise the real captured
`NOT_AUTHORIZED` body) against a real `PolygonAdapter` fed into a real
`BacktestRunner`. Confirms `HistoricalDataUnavailableError` propagates
uncaught — same exception type, same `.provider == "Polygon"` — and
`strategy.calls == 0`: the Runner never reaches candle replay, so
there's no risk of a silent partial or empty "successful" run.

**4. No backtest-specific branches in the 7 real strategy files (14
tests, DB-free, AST-based).** Two checks per file: no import with
"backtest" anywhere in the module path or imported names; no `if`
condition (walking the whole expression, not just top-level) referencing
an identifier containing "backtest". AST-based specifically so the
several legitimate docstring/comment mentions of "backtest" already in
these files (e.g. "backtest-safe by construction") never trigger a false
positive — only real Python identifiers in import statements and
if-conditions are inspected, never string/comment content.

## Confirmed per the acceptance criteria

- New tests: 20/20 pass.
- Existing Backtest Runner suite (Units 1-4, 44 tests) + adjacent
  `test_performance_queries.py` (9 tests) re-run alongside: 64/64 pass.
- **No production behavior changed.** Verified two ways: `diff -rq`
  against a fresh pull of current `main` (clean), and running the three
  DB-free sections with Postgres deliberately stopped — they pass
  regardless (17/20 pass, exactly the 3 D17 tests skip, confirming the
  DB-gating is scoped to only the tests that genuinely need it, not a
  blanket module-wide skip that would have falsely gated the other 17).

## Not done, on purpose, per Unit 5's own scope boundary

Full-suite before/after regression run, decision log entry, `TESTING.md`,
the optional API route — Unit 6, next drop, pending review of this one.
