# TESTING.md — Fixed five stale "no caller/writer yet" docstrings (decision #161)

## What this closes

Decision #158's own D-item audit of `strategy-engine-open-decisions.md`
inventoried five stale docstring claims and one architecture question,
explicitly leaving all six "for a future task or for Saqib's call." This
delivery closes that list.

## The five stale claims — verified directly, then corrected

All five shared the same root cause: written before decision #128's
Backtest Runner became a real caller/writer of the persistence layer
decision #120 built.

1. **`docs/architecture/strategy-engine-design.md` §5** — "a real
   (unwired) writer" → decision #128 is now a real, wired writer. Also
   corrected the paragraph's "what still doesn't exist" framing (narrowed
   to the LIVE-path caller specifically) and D17's status (resolved for
   Backtest Runner, open for live — matching #158's own wording).
2. **`backend/app/trading_intelligence/performance.py`** module docstring
   — "no real caller wired into it... its only caller today is this
   task's own test suite" → distinguished LIVE (still absent) from
   Backtest Runner (real, since #128, via `BacktestRunner.run()`).
3. **`backend/app/models/trading_intelligence.py`**'s `strategy_outcomes`
   bullet — "no real caller wired yet" → corrected in the same style
   already used one bullet down for `backtests`/#136.
4. **`backend/app/trading_intelligence/state_snapshot.py`** — two
   separate claims: "no migration exists yet" (migration 0008 built at
   #120) and "a Backtest Runner" listed as hypothetical (confirmed via
   `runner.py`'s two real `capture_strategy_outcome_snapshots()` call
   sites — it's real now).
5. **`backend/app/schemas/performance.py`**'s D17 discussion —
   "deliberately UNRESOLVED" / "no code in this module resolves it either
   way" → decision #128 chose option (a) from D17's own text (only call
   `record_strategy_outcome()` once both snapshots are non-`None`,
   discarding otherwise) — resolved for Backtest Runner, still open for
   the live path.

Every claim was verified directly against the live file (`grep`/read)
before editing — none were corrected on the strength of #158's summary
alone.

## D13 — reaffirmed, not silently left stale

D13's resolution ("import `Opportunity` directly, don't move it") rested
on "no cross-module consumer today." Confirmed directly:
`backend/app/backtest_runner/fill_simulator.py` and `runner.py` both
`from app.strategy_engine.base_strategy import Opportunity`. A real
cross-module consumer exists now — but it's Backtest Runner, not the
Opportunity Engine (§9) D13's own text named as its anticipated trigger.

**Call made, stated plainly rather than deferred again:** reaffirmed
as import-directly, not moved. D13's own text already said "no code
change required by this resolution either way," the existing import
already works correctly, and moving the class now would be a pure
consistency change with no functional necessity behind it. This is a
low-stakes, trivially reversible call — if you'd rather have
`Opportunity` moved into `schemas/events/opportunity.py` for consistency
with `MarketState`/`ContextChanged`/`FeatureSet`, that's a small,
easily-scheduled follow-up, not something this correction blocks. The
Opportunity Engine trigger D13 originally named has still not occurred.

## Scope discipline

No code logic, test, or persistence behavior changed anywhere — every
edit is a docstring, a design-doc paragraph, or the D13 table row.

## Verification

- All four edited `.py` files confirmed to still compile
  (`python3 -m py_compile`) — clean.
- `diff -rq` against a freshly-pulled `main` confirms exactly six files
  changed: `docs/architecture/strategy-engine-design.md`,
  `docs/architecture/strategy-engine-open-decisions.md`,
  `backend/app/trading_intelligence/performance.py`,
  `backend/app/models/trading_intelligence.py`,
  `backend/app/trading_intelligence/state_snapshot.py`,
  `backend/app/schemas/performance.py` — plus this file,
  `confirmed-decisions.md`, and `INDEX.md`.
- Decision log tail re-checked both before starting and immediately
  before writing entry #161: #160 was latest both times, no collision.
- No tests run — docs/docstring-only change, matching decision #158's
  own precedent for this exact class of correction.
