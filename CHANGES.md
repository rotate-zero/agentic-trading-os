# M4 — Market State + Context integration contract (decision #98)

Copy these into your repo root, overwriting the existing paths — this
replaces the previous drop entirely, so this one zip has everything.
Full reasoning is in `docs/decisions/confirmed-decisions.md` #98.

**Scope, exactly as you narrowed it in our discussion.** This is not a
Strategy Engine build. Nothing strategy-specific was added to either
`context_engine/` or `market_state_engine/` — both remain exactly as
unaware of a future Strategy Engine as they were of each other before
this. What this drop actually does: gives both engines a synchronous
"what's the current state" read alongside the events they already
publish, gives `StrategyOutcome`'s snapshot fields a real (tested, not
stubbed) capture contract, and exposes both engines' current state for
live debugging. `strategy_engine/` still doesn't exist. Neither does the
`strategy_outcomes` table, or any Execution/Position Monitor code.

## What's in this drop, task by task

**Task 22 — integration proof, not a Strategy Engine.** No `Strategy`
ABC, no `Opportunity`, no GATE→MATCH→SCORE→PROPOSE loop, no ORB. Instead:
a new test file, `backend/tests/test_strategy_integration_contract.py`,
whose core test (`test_a_future_orb_consumer_can_read_both_engines_
consistently`) stands a plain subscriber in for "a future ORB Strategy"
against both real engines on one shared bus — the same wiring shape
`main.py` uses in production — and proves the event-driven path and the
new synchronous-read path (below) agree. That's the actual proof you
asked for: a future Strategy consumer really can get `ContextChanged`/
`MarketStateChanged` and the corresponding current state, without any
Strategy code existing yet to prove it.

**The synchronous read itself — `get_snapshot()` on both engines, the
real center of this drop.** Neither engine previously had any way to
answer "what do you currently believe" outside of subscribing and
waiting for the next publish.
- `MarketStateEngine.get_snapshot(symbol=None)` — per-symbol `MarketState`
  plus the SPY/QQQ/IWM `CrossSymbolState` composite, the composite always
  included regardless of which symbol you ask for.
- `ContextEngine.get_snapshot(symbol=None)` — transparently merges the
  two internal aggregation paths decision #96 split (global + per-symbol)
  into one `providers` dict per symbol, so a caller doesn't need to know
  that split exists.
Both follow the same rule everything else in this codebase already
follows: a symbol never computed for is simply absent from the response,
never a fabricated placeholder.

**Task 23 — `app/trading_intelligence/state_snapshot.py`, new file.** The
"clean mechanism/contract" you asked for, and only that — three
functions (`capture_market_state_snapshot`, `capture_context_snapshot`,
`capture_strategy_outcome_snapshots`) that read the two `get_snapshot()`
methods above and shape the result to match `StrategyOutcome`'s
`market_state_at_entry`/`_at_exit`/`context_at_entry`/`_at_exit` fields
(decision #89). No table, no writer, no notion of what "entry" or "exit"
means — it doesn't know it'll eventually be called from a fill handler
that doesn't exist yet. Tested against real engine data, not stubs.

**Task 24 — compatibility check, not a Replay Engine.** No Replay Engine
or Backtest Runner was built; both stay exactly as deferred as they were
(`strategy-engine-design.md` §7, `future-ideas.md` #5). What the check
actually found:
- `MarketState`/`CrossSymbolState` already carry a domain-safe `candle_ts`
  — verified directly (not assumed) that it survives untouched through
  the new cache and `get_snapshot()`, via a test that publishes a payload
  timestamped years in the past and confirms that exact value comes back.
- `ContextChanged` has **no domain-safe timestamp at all**, and this drop
  doesn't invent one. Context Engine's own cadence is a session-boundary
  loop + a 15-minute per-symbol timer — timer-driven, not candle-driven —
  so there's no candle to borrow a timestamp from. `get_snapshot()`'s
  `evaluated_at` is wall-clock, and says so explicitly rather than
  pretending otherwise. This is a real, pre-existing gap a future Replay
  Engine will need to deal with — flagged, not solved here, since solving
  it means changing how Context Engine triggers itself, which is well
  outside "prepare interfaces."

**Task 25 — two new observability routes, kept separate on purpose.**
`GET /intelligence/market-state` and `GET /intelligence/context`, both
taking an optional `?symbol=` query param, each a thin passthrough of one
engine's `get_snapshot()`. Not merged into one payload — Market State and
Context stay two independently-owned engines; a combined response would
be exactly the kind of premature Strategy-shaped composite you asked me
to avoid.

## One stale doc line fixed along the way

`system-design.md` §4.8 still said "Market State Engine, Context Engine,
and everything below them in this table are still the target shape only,
not yet built" — true when written, false since decisions #92–97, never
updated. Corrected in this drop; Strategy Engine and everything below it
remains accurately described as not yet built.

## Files changed

- `backend/app/market_state_engine/engine.py` — `get_snapshot()`,
  `_latest_market_state`/`_latest_cross_symbol_state` caches, populated
  right before each publish (same ordering the code already used
  elsewhere).
- `backend/app/context_engine/engine.py` — `get_snapshot()`,
  `_latest_global`/`_latest_by_symbol` caches and their `evaluated_at`
  timestamps.
- `backend/app/trading_intelligence/state_snapshot.py` — new.
- `backend/app/api/routes/intelligence.py` — two new routes.
- `backend/tests/test_strategy_integration_contract.py` — new, 10 tests.
- `docs/decisions/confirmed-decisions.md`, `docs/decisions/INDEX.md` —
  decision #98 recorded.
- `docs/architecture/system-design.md` — §4.8 stale line corrected,
  §4.13 `strategy_outcomes` note added.
- `docs/architecture/trading-intelligence-architecture.md` — §4, §5, §8,
  §15 each gained a short note tying decision #98's additions to that
  section, plus a small diagram in §15 distinguishing
  `state_snapshot.py`'s two functions from the still-unbuilt `WorldView`.
- `docs/architecture/strategy-engine-design.md` — §5 notes the real
  capture contract now backing those four fields; §13's resume checklist
  gained a 4th item pointing at what M4 already prepared.

## Verified

Real local Postgres (installed fresh for this session, `alembic upgrade
head` through `0007`, no prior data) — not mocked. `pytest
tests/test_strategy_integration_contract.py`: all 10 new tests pass.
Full existing suite re-run three times for stability: **381 passed all
three runs** (371 baseline + 10 new — decision #93's documented flake did
not reproduce in any of the three runs here either, still understood as
flaky, not newly introduced). One environmental hiccup along the way,
noted rather than hidden: partway through this session Postgres itself
stopped running (container-level, not this drop's code) — the suite
correctly failed 40 tests with plain connection-refused errors when that
happened, restarted cleanly once Postgres was brought back up, and the
three stability runs above are all post-restart. A full FastAPI lifespan
boot exercised end-to-end via `TestClient` (real engines, real lifespan,
no Finnhub/Polygon keys configured) with both new routes hit live —
clean 200s, real data (`GET /intelligence/context` returned a live
`CalendarProvider` read; `GET /intelligence/market-state` returned an
honestly-empty `{"symbols": {}, "market": null}` since nothing had been
computed yet in that particular boot).

**Not verified:** this drop against a database with existing
`strategy_outcomes` rows or any Strategy Engine code, since neither
exists yet — `state_snapshot.py` is proven against real `MarketState`/
`Context` data, not against a real future caller, which by definition
can't exist yet either.

## Next

Strategy Engine Stage 1 itself — `Strategy(ABC)`, `StrategyConfig`,
`Opportunity`, the Gate, and ORB as the first real strategy — is real,
separate work from here, not started by this drop. `strategy-engine-
design.md` §13 has the updated resume checklist for whenever that starts.
