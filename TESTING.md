# TESTING.md — Momentum & VWAP strategies (decision #98)

## What's in this zip

```
backend/app/strategy_engine/
    __init__.py            (new, empty — package marker only)
    momentum_strategy.py   (new)
    vwap_strategy.py       (new)
backend/tests/
    test_momentum_strategy.py  (new)
    test_vwap_strategy.py      (new)
docs/architecture/strategy-engine-design.md   (modified — new §1a + Status line)
docs/decisions/confirmed-decisions.md         (modified — new #98)
docs/decisions/INDEX.md                       (modified — new row #98)
```

Unzip directly onto the project root — every path above is relative to it.

## Read this before running anything

`backend/app/strategy_engine/base_strategy.py` does not exist in the
repo yet — it's being built in a separate, concurrent session I have
no visibility into. `momentum_strategy.py`/`vwap_strategy.py` import
`Strategy`/`StrategyConfig`/`Opportunity`/`every_candle` from it per
the documented interface (`system-design.md` §4.8, `strategy-engine-
design.md` §3-4), but **three specific assumptions are unverified
against the real file** — flagged in both module docstrings and in
decision #98:

1. `Strategy.__init__(self, config: StrategyConfig)` / `self.config` —
   no `__init__` is shown in the documented ABC.
2. `every_candle(timeframe=...)` — the documented signature shows no
   arguments. Each `evaluate()` defensively re-checks
   `features.timeframe` regardless, so this is harmless if wrong.
3. `context: ContextChanged` — the ABC's type hint says `Context`; the
   real, already-built schema (decision #92) is `ContextChanged`.

**Until `base_strategy.py` lands, `pytest backend/tests/` will report a
collection error on these two test files** (import error on the
not-yet-existing module) — expected, not a bug in either strategy
file. Every other existing test in the suite is unaffected; pytest
reports collection failures per-file, not suite-wide.

## What's actually been verified, and how

Since I couldn't import the real `base_strategy.py`, I built a small,
throwaway, spec-conformant local stand-in for `Strategy`/
`StrategyConfig`/`Opportunity`/`ScheduleTrigger` in a scratch sandbox —
**not included in this zip** — purely to exercise both strategy files
end to end before delivering them. Against that stand-in:

```
24 passed in 0.10s
  test_momentum_strategy.py — 13 tests
  test_vwap_strategy.py     — 11 tests
```

Covers, per strategy: the pure GATE/MATCH/SCORE functions in
isolation (crossover direction, band membership, mirror-image
SELL logic, volume-floor rejection, honest-absence-vs-fabricated-zero
for the optional regression signal, confidence saturation/clamping —
same style as `backend/tests/test_market_state_scoring.py`), plus a
full `evaluate()` pass confirming the assembled `Opportunity`'s
`structural_invalidation`/`structural_target`/`evidence` values are
arithmetically correct, and the GATE-stage `None` returns (wrong
timeframe, missing warm-up data, missing `acceleration_score`) fire
correctly.

**Not verified:** anything touching the real `base_strategy.py`,
since it doesn't exist yet. Once it lands:

1. Re-point the imports at the top of both files if the module path or
   names differ from `app.strategy_engine.base_strategy` /
   `Strategy`/`StrategyConfig`/`Opportunity`/`every_candle`.
2. Check the three numbered assumptions above against the real file —
   fix `MomentumStrategy.__init__`/`VWAPStrategy.__init__` and the
   `trigger = every_candle(...)` lines first if they don't match.
3. Run `pytest backend/tests/test_momentum_strategy.py
   backend/tests/test_vwap_strategy.py -v` — should pass as-is if the
   real interface matches the documented spec; the pure-function tests
   (no `Strategy` dependency at all) will pass regardless.
4. Run the full suite once both strategy files and `base_strategy.py`
   coexist, to confirm nothing else was disturbed.

## Everything else in this delivery needs no further verification

The `docs/` changes are additive — a new §1a section, one new decision
entry, one new INDEX row — nothing existing was restructured or
renumbered.
