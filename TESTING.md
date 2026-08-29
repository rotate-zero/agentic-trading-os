# TESTING — weekend-date test fragility fix

Unzip at project root. Overwrites `backend/tests/test_daily_levels.py`
and `backend/tests/test_intelligence_routes.py` only — no application
code changed.

## What broke

7 tests used `datetime.now(timezone.utc).replace(hour=15, ...)` — hour
fixed, but the DATE still real. This worked fine on weekdays, but broke
the instant real wall-clock time crossed into a weekend: `MarketClock`
correctly treats Saturday/Sunday as `Session.CLOSED` regardless of hour,
so "hour=15 UTC" stopped meaning "regular session" the moment "today"
became a Saturday. Same root cause as the 4 tests fixed in the previous
delivery (`datetime.now()` without a full fixed anchor) — just a
different manifestation that only surfaces on weekends, so it stayed
dormant until real time actually reached one.

Fixed by anchoring all 7 to a fully fixed date: Wednesday, 2026-08-12,
15:00 UTC — a real, ordinary regular-session weekday that can't drift.

## Verify

```bash
cd backend
pytest tests/test_daily_levels.py tests/test_intelligence_routes.py -v
```

## A separate, confirmed-pre-existing, non-deterministic issue — NOT fixed here

While re-verifying, the FK-violation-on-teardown race flagged in the
previous delivery turned out to be more general than first described:
it can hit **any** test in `test_intelligence_routes.py` that spins up
the real app lifespan and creates `level_interaction_state` rows for a
fresh ticker — confirmed by watching it land on three different tests
across three consecutive full-suite runs
(`test_intelligence_series_reflects_real_persisted_candles`,
`test_new_level_types_get_level_interaction_tracking_automatically`,
`test_previous_day_levels_skip_a_weekend_gap`), each passing cleanly in
isolation. One root cause (a race between the app's shutdown sequence
and `LevelInteractionEngine`'s background worker), not three separate
bugs — genuinely pre-existing, confirmed to fail identically on the code
as it stood before any of this session's work.

Worth a dedicated thread to investigate properly (needs a careful look
at whether `LevelInteractionEngine.stop()` is fully awaited before the
lifespan context exits) rather than a rushed fix here. Low urgency:
test-only, doesn't affect production behavior, and the full suite
passes reliably once whichever single test it happens to land on that
run is deselected.
