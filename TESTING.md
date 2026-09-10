# TESTING.md — opportunity-engine-v1-conflict-view (decision #121)

A deliberately narrow, non-scoring read-side view over
`OpportunityCache.get_snapshot()`: per symbol, classifies 2+ currently
cached strategies as `agreement` (all one direction) or `conflict`
(BUY and SELL both present). **This is NOT the Opportunity Engine and
does NOT resolve D4** (`strategy-engine-design.md` §10) — D4 stays
explicitly OPEN. Full reasoning: `docs/decisions/confirmed-decisions.md`
#121.

## Decision-number collision — read this first

This task's own prompt re-checked the log tail immediately before
writing and found #119 latest, #120 free — same as the sibling
persistence-layer track's prompt, at the same time. **The sibling
track's push (Performance Intelligence's `strategy_outcomes`/
`backtests` persistence layer) landed on `main` first and claimed
#120.** This delivery is built against a fresh pull of that already-
merged state and renumbers its own entry to **#121**. Same category of
collision this log already documents at #98/#99, #111/#112, and
#114/#115 — noted explicitly here and in the decision entry itself,
not silently renumbered.

No functional collision: `diff -rq` against a freshly re-pulled clone
confirms this delivery's footprint (`opportunity_view.py`,
`test_opportunity_view.py`, this file, the D4 row note, the two
decision-log files) shares no file with #120's own
(`app/models/trading_intelligence.py`, `app/schemas/performance.py`,
`app/trading_intelligence/performance.py`, migration `0008`,
`app/db/base.py`).

## What changed

- `backend/app/trading_intelligence/opportunity_view.py` — **new.**
  - `compute_opportunity_conflicts(snapshot: dict) -> dict` — pure
    function. Takes exactly `OpportunityCache.get_snapshot()`'s shape
    (`{"symbols": {ticker: {strategy_name: {...Opportunity fields,
    "received_at": ...}}}}`) and returns
    `{"agreements": {...}, "conflicts": {...}}`.
  - `get_opportunity_conflicts(symbol: str | None = None) -> dict` —
    thin wrapper, calls `get_opportunity_cache().get_snapshot(symbol)`
    then delegates to the pure function above. This is the one call
    site a future route would use, unchanged.
  - No new state, no singleton — nothing added to `main.py`'s lifespan
    or `conftest.py`'s singleton-reset fixture, since this module owns
    nothing to start/stop/reset.

- `backend/tests/test_opportunity_view.py` — **new, 16 tests, all
  DB-free.**

- `docs/architecture/strategy-engine-design.md` — **modified.** §10's
  D4 row gets a note pointing to decision #121 and this capability.
  **D4 itself is NOT marked resolved** — it remains "Still deliberately
  not decided." (References decision #121, not #120 — updated to match
  the renumbering above.)

- `docs/decisions/confirmed-decisions.md` / `docs/decisions/INDEX.md`
  — **modified.** New entry **#121** (not #120 — see collision note
  above), inserted after the sibling track's own #120 entry.

- **Not touched, confirmed by diff against a freshly re-pulled clone:**
  `opportunity_cache.py`, its `get_snapshot()` contract,
  `state_snapshot.py`, `scheduler.py`, `gate_conditions.py`, any
  `strategy_engine/*_strategy.py` file, `strategy_outcomes`/`backtests`,
  `app/models/trading_intelligence.py`, `app/schemas/performance.py`,
  `app/trading_intelligence/performance.py`, migration `0008`,
  `app/db/base.py`, `app/api/routes/intelligence.py`. `StrategyOutcome`
  is not read anywhere in this module — also unrelated to #120's own
  new open item D17 (a `strategy_outcomes` nullability gap).

## Classification rule (mutually exclusive, per symbol)

- 0 or 1 strategy currently cached for a symbol → absent from **both**
  `agreements` and `conflicts` — honest absence, not a false negative.
- 2+ strategies, all one `direction` → one `agreements` entry.
- 2+ strategies, both `BUY` and `SELL` present → **one** `conflicts`
  entry covering every strategy for that symbol, grouped by direction
  (`by_direction["BUY"]`, `by_direction["SELL"]`) — a 2-BUY-1-SELL
  symbol is never split into a 2-agreement plus a separate anomaly.
- An entry with a missing/malformed `direction` is excluded from that
  symbol's classification (logged at `debug`), not a crash.

`confidence`/`setup_detected_at` are **passthrough only** — copied
verbatim per strategy, never averaged, compared, or used to rank/select
a "winner." No score, weight, or priority of any kind is computed.

## Terminology note

`OpportunityCache` is status-blind (never reads `Opportunity.status`)
and has no TTL/purge — it retains the LAST `Opportunity` per
`(symbol, strategy)` indefinitely. This view inherits that exactly and
deliberately says "currently cached," never "live," in its own
docstring and output — a conflict reported here could be two strategies
that fired on the same candle, or two that fired hours apart with
neither having re-fired since. Not "fixed" here — that would mean
changing `OpportunityCache`'s own retention/status contract, out of
this task's scope.

## Route

**Intentionally skipped.** No route added to
`app/api/routes/intelligence.py` — at the time this task's route
decision was made, that file was a plausible collision point with the
sibling `strategy_outcomes`/`backtests` persistence track working in
parallel, and decision #115 in this log already documents this exact
failure mode once (two sessions' concurrent edits to a shared file, one
silently dropped on merge). In the event, #120's own landed delivery
didn't touch `intelligence.py` either — but that wasn't knowable when
this task's own decision was made, so skipping was still the right call
given the information available at the time. `get_opportunity_conflicts()`
is ready to be called by a route whenever one is added later.

## Manual merge notes

None. This delivery's only footprint is two new files
(`opportunity_view.py`, `test_opportunity_view.py`) plus three doc
files (`strategy-engine-design.md`, `confirmed-decisions.md`,
`INDEX.md`) — no shared application file was touched, so there is no
insertion point to manually merge, and no interaction with #120's own
already-landed files.

**Housekeeping note, not acted on unilaterally:** `confirmed-
decisions.md` is now a little over 97KB, approaching this project's own
~100KB dated-archive rollover threshold. Worth a rollover pass soon —
not done as part of this delivery since that's a structural change
outside this task's own scope.

## Test suite — before/after, real local Postgres, against the merged-upstream state

Environment: PostgreSQL 16 provisioned directly in this session
(`apt-get install postgresql`), `CREATE USER trading ... SUPERUSER`,
`CREATE DATABASE trading_workspace OWNER trading`, `alembic upgrade
head` (now through migration `0008`, the sibling track's own
`strategy_outcomes`/`backtests` tables). DB wiped and recreated between
every run (this project's own established practice for avoiding
order-dependent failures), against a **freshly re-pulled** second clone
(post-#120, pre-this-task) for the "before" side — not the stale
pre-#120 clone this delivery was originally verified against before the
git update landed.

**Before (freshly re-pulled clone, post-#120), two independent runs:**
- Run 1: 603 total, 601 passed, 2 failed
- Run 2: 603 total, 601 passed, 2 failed
- Failures both runs, identical: the known pre-existing flaky pair from
  decisions #114/#116/#117/#118/#119 —
  `test_vwap_publishes_even_while_sma_is_still_warming_up`,
  `test_daily_levels_carry_level_interaction_once_touched`.

**After (this delivery, on top of the merged-upstream state), two
independent runs:**
- Run 1: 619 total, 617 passed, 2 failed
- Run 2: 619 total, 617 passed, 2 failed
- Failures both runs: the **same** known flaky pair, same test names —
  confirmed by diffing failing test IDs directly against the
  freshly-re-pulled-clone runs, not a new failure mode.
- 619 total = 603 baseline + 16 new `test_opportunity_view.py` tests,
  exactly.

**New tests in isolation**, run 3 times independently:
`test_opportunity_view.py` — 16/16 passed every run, zero flakiness.
Combined with the existing opportunity-adjacent tests
(`test_opportunity_cache.py`, `test_intelligence_routes.py -k
opportunit`) — 26/26 passed every run, zero flakiness.

**Zero regressions**, and zero interaction with #120's own new tests
(`test_performance_intelligence.py`) — not run as part of this
delivery's own isolated checks, but included and passing in every
full-suite run above. `diff -rq` against the freshly-re-pulled clone
(excluding `.pytest_cache`) confirms exactly the 5 files listed above
changed — nothing else, and nothing belonging to #120.

## D4 status

**D4 remains OPEN.** Nothing in this delivery decides, narrows, or
implies a resolution to the "Candidate Selection Score" question.
`strategy-engine-design.md` §10's D4 row states this explicitly and
points to decision #121 (not #120).
