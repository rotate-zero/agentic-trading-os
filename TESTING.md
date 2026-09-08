# TESTING.md — Stage 2 (D10), Track B: OpportunityCache

Unzip this straight to the project root and copy over. See `confirmed-decisions.md`
entry #114 for the full reasoning behind every call below — this file is the
short, practical companion: what changed, how it was verified, and exactly
where to look when merging with Track A's own `main.py` changes.

---

## What's in this delivery

- `backend/app/trading_intelligence/opportunity_cache.py` — **new file.**
  Passive read-side cache for `OpportunityCreated`. Full design reasoning is
  in its own module docstring — read that before touching this file.
- `backend/tests/test_opportunity_cache.py` — **new file.** 10 tests, no DB
  dependency.
- `backend/app/api/routes/intelligence.py` — **append-only.** One new route,
  `GET /intelligence/opportunities`, added at the exact end of the file.
  Nothing else in this file was touched. Track A does not touch this file at
  all, so there's no merge concern here.
- `backend/app/main.py` — **two small additive blocks**, not touched
  anywhere else. See "Merging with Track A" below — Track A is
  independently adding its own separate block to this same shared file for
  the Scheduler, and you'll be merging both by hand.
- `docs/decisions/confirmed-decisions.md` — decision #114 appended.
- `docs/decisions/INDEX.md` — one new row for #114.
- `docs/architecture/strategy-engine-design.md` — D10's status updated to
  reflect Track B landing (Track A still outstanding); new D12 opened for
  the `OpportunityCreated`/`Opportunity` schema-location question; Stage 2's
  checklist item in §12 split into its own Track A/Track B sub-checkboxes.

---

## Decisions surfaced for you, not resolved quietly

Both are flagged in decision #114 and in the new D12 row — repeating here
since they're the two things most worth your actual attention in this
delivery, not just the code:

1. **Does `OpportunityCreated`'s payload import `Opportunity` directly from
   `strategy_engine/base_strategy.py`, or move/mirror it into a new
   `schemas/events/opportunity.py`** to match the convention `MarketState`/
   `ContextChanged`/`FeatureSet` already follow? This delivery proceeds with
   **import directly** as its working default (reasoning in #114/D12), but
   it's a real fork, not a foregone conclusion — worth deciding before
   Track A's own `OpportunityCreated` publish path is finalized, since
   that's the actual write side this choice governs.
2. **Retention: last Opportunity per `(symbol, strategy)` pair**, not a
   rolling list of the last K. Simplest honest option given nothing
   downstream reads this cache yet — revisit once the Opportunity Engine
   (§9) actually needs more than "the latest."

---

## Merging with `main.py` (Track A + Track B both touch this file)

Track B's entire footprint in `main.py` is two clearly-delimited blocks,
found by searching for `Track B` in the file:

**1. Startup block** — inserted immediately after
`level_interaction_engine.start()`, before the Market State Engine section:

```python
    # --- Track B: OpportunityCache (decision #112/D10, #114) --------------
    # Passive read-side cache for OpportunityCreated — see
    # app/trading_intelligence/opportunity_cache.py's own module docstring
    # for full scope (NOT the Opportunity Engine, §9, which doesn't exist
    # yet). A bus subscriber like LevelInteractionEngine/MarketStateEngine
    # here, so it follows the same unconditional-start, stop-after-bus
    # posture as those two. Local import (not hoisted to this file's
    # top-of-file import block) so this whole addition stays a single,
    # self-contained, easily-merged insertion — see TESTING.md for the
    # exact insertion points (this block, plus its matching stop() call in
    # the shutdown `finally` block below).
    from app.trading_intelligence.opportunity_cache import get_opportunity_cache

    opportunity_cache = get_opportunity_cache(bus)
    opportunity_cache.start()
    # --- end Track B startup block ------------------------------------------
```

**2. Shutdown line** — inserted in the `finally` block, immediately after
`await market_state_engine.stop()` (i.e. after the bus stops, alongside the
other subscriber engines' own stop calls, per decision #47):

```python
        # --- Track B: OpportunityCache shutdown (decision #112/D10, #114) ---
        # Matches the startup block above — a bus subscriber, so it stops
        # AFTER the bus, alongside feature_engine/level_interaction_engine/
        # market_state_engine just above (same reasoning, decision #47).
        # In practice this is a no-op (see opportunity_cache.py's own
        # docstring — no background task, nothing to drain), but it's
        # called anyway for lifecycle-interface consistency with every
        # other engine here.
        await opportunity_cache.stop()
        # --- end Track B shutdown block ------------------------------------
```

**If Track A's `main.py` already landed first:** don't apply this
delivery's whole `main.py` file — instead, paste these two blocks into
Track A's version at the equivalent points (any spot after the bus and the
other engines exist is fine for the startup block; any spot after
`await bus.stop()` is fine for the shutdown block — ordering relative to
Track A's own Scheduler start/stop doesn't matter, since `OpportunityCache`
has no dependency on the Scheduler being started first). That's the whole
merge — two copy-pasted blocks, nothing else in the file to reconcile.

---

## Verification

Full backend suite, run in a venv built fresh for this delivery
(`python3 -m venv` + `pip install -r requirements.txt`) — **no Postgres
available in this session's sandbox**, same standing limitation flagged in
most entries in this log:

| | Passed | Failed | Skipped |
|---|---|---|---|
| Baseline (untouched clone, same sandbox) | 370 | 40 | 138 |
| This delivery | 380 | 40 | 138 |

The 40 failures are **byte-for-byte the identical set of test names** in
both runs (diffed directly, not eyeballed) — all pre-existing
DB-connectivity failures (`psycopg2.OperationalError: connection to server
at "localhost" ... Connection refused`), unrelated to this change. The +10
passed are exactly `test_opportunity_cache.py`'s own suite. Zero
regressions anywhere else.

Additionally smoke-tested end-to-end, not just via the unit tests: ran
`main.py`'s full `lifespan()` (startup through shutdown) directly, published
a real `OpportunityCreated` envelope onto the real `EventBus` mid-lifespan,
and confirmed `get_opportunity_cache().get_snapshot()` read back the
correct data through the actual wiring — not a stub, the real
`main.py`/`EventBus`/`OpportunityCache` path together.

This delivery's own `main.py` and `intelligence.py` changes were also
re-applied to a **second, completely independent fresh clone** (simulating
exactly how you'll apply this zip) and the full suite re-run there —
identical 380/40/138 result, confirming the delivery is self-contained and
doesn't depend on any leftover state from the session that built it.

**Not verified:** against a real local Postgres (none available here).
Not needed for `OpportunityCache` itself (no DB dependency at all), but
worth knowing before you treat the 40-failure baseline as fully explained —
decision #113's session, which did have Postgres provisioned, saw a
different (much smaller) failure count, so "40" is specific to a
Postgres-less sandbox, not a fixed property of the suite.

`GET /intelligence/opportunities` deliberately has no dedicated route-level
test — matches the existing precedent for `GET /market-state`/`GET /context`
(confirmed directly: neither has one anywhere in `backend/tests/`). All
three are thin passthroughs; the underlying engine's own unit tests are
treated as sufficient coverage for this pattern in this codebase already.

---

## What's still outstanding (not this delivery's scope)

- **Track A itself** — nothing in the codebase yet actually calls a live
  `Strategy.evaluate()` or publishes a real `OpportunityCreated`.
  `OpportunityCache` is fully built and tested against synthetic envelopes,
  but has nothing live to cache until Track A lands.
- Declarative `gate_conditions` enforcement (§2b) and Opportunity Engine
  ranking (§9) — both explicitly out of scope per D10, untouched here.
- The two open design questions above (D12: schema location; retention: K
  vs. latest-only) — flagged, not blocking, revisit when a real consumer
  or a real Track A implementation gives you something concrete to decide
  against.
