# TESTING.md — Market State M3 (`CrossSymbolState`) delivery, decision #97

## Scope

Completes the Market State Engine — SPY/QQQ/IWM cross-symbol synthesis
(`CrossSymbolState`) on top of decision #93's already-shipped per-symbol
engine. Picks up exactly where the handoff prompt left off: `_persist_
cross_symbol` (the one piece that wasn't written yet) plus everything
downstream of it — write-time assertions, stale-doc fixes, tests, decision
log entry. Backend only; nothing in `frontend/` references Market State
cross-symbol state yet, so nothing there needed touching.

Fetched the live repo fresh at session start (`docs/decisions/INDEX.md`
confirmed decisions through #96 already landed, including `symbol_
fundamentals`'s migration claiming `0006` first in parallel work) — this
delivery was built against that, not against the stale sandbox state the
handoff prompt described.

## What changed

- `backend/alembic/versions/0007_cross_symbol_state.py` — new migration.
  Renumbered from an earlier session's `0006` draft to `0007`, `down_
  revision="0006"`, chaining after decision #96's `symbol_fundamentals`
  migration (which took `0006` first). Adds 7 nullable `CrossSymbolState`
  columns to `market_state_history`; relaxes the 4 original per-symbol
  score columns from `NOT NULL` to nullable.
- `backend/app/models/market_state.py` — `MarketStateHistory` extended
  with the 7 new columns; docstring explains the two mutually exclusive
  row shapes (per-symbol vs. `__MARKET__` sentinel) the table now holds.
- `backend/app/schemas/events/market_state.py` — new `CrossSymbolState`
  Pydantic model (`timeframe`, `candle_ts`, 7 score fields).
- `backend/app/market_state_engine/scoring.py` — 4 new pure functions:
  `trend_alignment_score`, `risk_on_score`, `qqq_leadership_score`,
  `iwm_confirmation_score`. Same formulas locked in decision #91 §4,
  unit-tested, no design changes.
- `backend/app/market_state_engine/engine.py`:
  - `_CROSS_SYMBOL_TICKERS`, `_CROSS_SYMBOL_MAX_INTERVAL_SECONDS = 4.0`,
    `_CROSS_SYMBOL_SENTINEL = "__MARKET__"` module constants
  - `_cross_symbol_trend` cache (ticker → latest `trend_score`)
  - `_on_features_updated` gives SPY/QQQ/IWM the tighter debounce ceiling
  - `_worker_loop` attempts cross-symbol synthesis after any of the
    three completes its own per-symbol compute, in its own nested
    try/except so a synthesis failure doesn't get misattributed to the
    triggering symbol's own (already-successful) per-symbol compute
  - `_compute_cross_symbol()` — new; returns `None` until all three have
    reported at least once
  - `_persist_cross_symbol()` — new; mirrors `_persist`'s structure and
    error-handling exactly
  - Write-time assertions added to both `_persist` and `_persist_cross_
    symbol`, enforcing the two-shape split (decision #89's `entry_qty
    == exit_qty` precedent, application-level, not a DB `CHECK`)
- Stale "M3 not built here" comments corrected: `backend/app/main.py`,
  `backend/app/market_state_engine/__init__.py`, `engine.py`'s own
  module docstring, `docs/architecture/system-design.md` §10.3, `docs/
  architecture/trading-intelligence-architecture.md` §4 (the "data
  source confirmation still needed" line now points at decision #95's
  already-closed spike)
- `backend/tests/test_market_state_scoring.py` — 14 new tests for the 4
  new formulas (neutral/saturation/clamping cases, same style as the
  existing 13)
- `backend/tests/test_market_state_engine.py` — 4 new integration tests:
  no synthesis until all three report, synthesis fires with correct
  scores once they do, sentinel row persists with the correct two-shape
  split (checked at the data layer, not just in-process), tighter
  debounce ceiling actually wired per-scheduler
- `docs/decisions/confirmed-decisions.md` — decision #97 appended (full
  write-up: formulas, persistence architecture, migration renumbering,
  stale-doc fixes, and one item explicitly flagged rather than resolved
  — see "Not in this delivery" below)
- `docs/decisions/INDEX.md` — #97 row added; the file's own "#1 through
  #95" header text corrected to "#1 through #97" (stale since #96
  landed, not something introduced by this delivery, but a one-line fix
  adjacent to what was already being touched)

## Not in this delivery — flagged for a direct decision, not bundled in

Decision #92 dropped `ContextProvider.evaluate()`'s `market_state`
parameter, noting it "goes back on once M2 lands." M2 landed with #93;
decision #96's `SymbolContextProvider` work (a separate, parallel
thread) touched `context_engine/provider.py` but didn't touch this
specific question — confirmed by reading that file fresh this session,
not assumed. Now that M3 exists too, there's a real `MarketState`/
`CrossSymbolState` type to pass, closing the reason #92 gave for leaving
it out. Left out of this build deliberately: it's a Context Engine
interface change, not a Market State one, and folding it in here would
be exactly the kind of speculative bundling the project's own
scope-discipline convention flags against. Raised in decision #97's own
text — worth a quick call before it gets built, not before it's raised.

## Verification performed

- Fresh local Postgres 16 (this environment's own instance — no sudo
  needed for install here, same as before). `alembic upgrade head`
  applies cleanly through `0007`. `alembic downgrade 0006` then
  `upgrade head` again both verified — `\d market_state_history` checked
  by hand before/after each direction to confirm the exact column shape
  (nullability included, not just presence/absence).
- `pytest tests/test_market_state_scoring.py tests/test_market_state_engine.py`
  — 35 tests, all pass (17 pre-existing from decision #93 + 18 new).
- Full suite run three times for stability against the same live
  Postgres: **369 passed, 2 failed**, identically all three runs —
  `test_sma_ema_slope_family_groups_under_the_owning_period_and_is_
  excluded_from_level_interaction` and its documented neighbor
  (`test_daily_levels_carry_level_interaction_once_touched`), the exact
  pre-existing flake decision #93 already flagged and left alone. Not
  assumed unrelated — actually re-confirmed by extracting a second,
  completely clean copy of this session's own starting tarball (no M3
  changes applied at all) and running the same two tests against it:
  reproduces the identical `KeyError`/zone-mismatch failure
  independently. Baseline test count before this delivery: 353 passed,
  0 failed (re-verified at session start); 353 + 18 new = 371 total,
  matching 369 + 2 exactly.
- Full FastAPI lifespan startup-and-shutdown exercised end-to-end via
  `app.router.lifespan_context` (not just import-checked) — logs confirm
  `MarketStateEngine` starts/stops in the same position decision #93
  established (after the bus, unlike `ContextEngine`), no warnings, no
  orphaned tasks, `CrossSymbolState`'s presence doesn't change engine
  startup ordering at all (same subscriber, same singleton).
- `grep -rn "M3\|CrossSymbol\|not built here" backend/app/main.py
  backend/app/market_state_engine/` — confirmed no remaining stale
  "not built here" language anywhere in the touched modules.

**Not verified:** this migration's `ALTER COLUMN ... nullable` half
against a `market_state_history` table that already has production rows
in the 4 now-relaxed columns — this environment's Postgres was created
fresh for this session, so that path was never exercised against
populated data. Worth a dry run against a copy of your real data before
applying to it directly, same caveat every migration in this project
carries until that's actually been done once.

## How to apply

Unzip directly into your project root, overwriting existing paths
(nothing here conflicts with anything you've pushed since — checked
against the live `main` tarball at session start, not an assumption).
Then, from `backend/`:

```
alembic upgrade head
pytest tests/test_market_state_scoring.py tests/test_market_state_engine.py
pytest   # full suite, if you want the full-suite confirmation locally too
```

You push to git yourself after local verification, per usual — nothing
in this delivery touches git.
