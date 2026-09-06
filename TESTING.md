# TESTING — First Pullback & Reversal built (decisions #108, #109, #110)

## What's in this delivery

**New application code:**
- `backend/app/strategy_engine/level_touch_tracking.py` — shared, isolated touch-resolution reconstruction (gap-through and cold-start handling, both fixed vs. the original design per the review).
- `backend/app/strategy_engine/first_pullback_strategy.py`
- `backend/app/strategy_engine/reversal_strategy.py`

**Modified application code:**
- `backend/app/trading_intelligence/level_interaction_engine.py` — one additive field (`last_applied_candle_ts`) on `get_snapshot()`'s per-entry dict. Nothing removed, nothing renamed; existing consumers (the UI panel) are unaffected.

**New tests:**
- `backend/tests/test_level_touch_tracking.py` (13 tests, pure — no DB)
- `backend/tests/test_first_pullback_strategy.py` (16 tests)
- `backend/tests/test_reversal_strategy.py` (12 tests)

**Docs:**
- `docs/architecture/strategy-engine-design.md` — §16 rewritten to cover the design review outcome and the actual build; §10's D9 row updated; §12's checklist updated.
- `docs/decisions/confirmed-decisions.md` / `INDEX.md` — decisions #108 (review + `get_snapshot()` fix), #109 (First Pullback), #110 (Reversal).

## How to verify

1. Unzip over your project root — paths already match.
2. `cd backend && pytest -q` — I ran the full suite against a real, freshly-migrated Postgres 16 in my own sandbox (not mocked, per your verification standard): **486 passed, 0 failed** (445 pre-change baseline + 41 new tests, zero regressions). You should see the same shape locally; if Postgres isn't reachable, the DB-backed tests in the three new files skip as a whole (same posture `test_level_interaction_engine.py` already has) rather than fail, and you'd see the same 445+13 pure-test count passing with the rest skipped.
3. No frontend files touched — `tsc`/`vite build` not applicable to this change.
4. No live browser click-through available in my environment, as always — flagged per standing practice, though there's no UI surface in this change to click through anyway (`get_snapshot()`'s new field is additive and the Feature Engine panel that already reads this endpoint doesn't break on an extra key).

## Worth double-checking on your end

Nothing is blocking, but two things worth a look since they involve judgment calls made during the build, not just mechanical implementation:

1. **The `get_level_interaction_engine()` singleton pattern in tests.** The three new DB-backed test files construct their own `LevelInteractionEngine(bus, aura_pct=0.002)` and assign it directly to the module-level `_level_interaction_engine` global (matching the existing `conftest.py` autouse fixture, which already resets this same global before/after every test — I didn't add new reset logic, just used what's already there). Worth a skim if you want to confirm this doesn't feel like it's fighting the existing fixture.
2. **Reversal's gap-through invalidation fallback** (`features.features.get(level_key)`, module docstring in `reversal_strategy.py`) is a real, if rare, precision trade-off — flagged explicitly rather than silently accepted. If gap-throughs turn out more common than expected for a given `level_key` once this runs against real data, that fallback's quality is worth revisiting.

Both strategies still need actual live wiring (a Strategy Scheduler) before they run in the real pipeline — same pre-existing gap ORB/Gap/Volume Spike already have, not something this delivery was expected to close.
