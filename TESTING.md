# TESTING.md — Strategy Engine Stage 1: base_strategy.py + ORB (decision #99)

## Delete these first — do not just overwrite

This drop **replaces**, not extends, everything currently in
`backend/app/strategy_engine/` and its tests, including this morning's
two updates (the Momentum review + the VWAP review). Delete before
unzipping:

```
backend/app/strategy_engine/momentum_strategy.py
backend/app/strategy_engine/vwap_strategy.py
backend/tests/test_momentum_strategy.py
backend/tests/test_vwap_strategy.py
TESTING.md                              (this file replaces it)
```

**Nothing about the guard fixes or the Market State finding from this
morning's review is lost** — see "What was reconciled" below. The
files themselves are being discarded because they depend on a
`base_strategy.py` that never existed; the *findings* are carried
forward into decision #99 and, where directly applicable, into
`orb_strategy.py` itself.

**Not touched, not deleted:** `test_vwap_ext.py`, `test_strategy_integration_contract.py`,
anything in `feature_engine/` other than the one file listed below,
`market_state_engine/`, `context_engine/`. This drop's only change
outside `strategy_engine/` is additive (see next section).

## What's in this zip

```
backend/app/schemas/events/features.py     (modified — FeatureSet gains open/high/low/volume)
backend/app/feature_engine/engine.py       (modified — _apply_close()/_compute_one() thread real OHLCV through the 1m path only)
backend/app/strategy_engine/base_strategy.py   (new)
backend/app/strategy_engine/orb_strategy.py    (new)
backend/tests/test_base_strategy.py            (new — 9 tests)
backend/tests/test_orb_strategy.py             (new — 18 tests)
docs/architecture/strategy-engine-design.md    (modified — §8 OHLC gap resolved, §12/§13 updated, new §14)
docs/architecture/system-design.md             (modified — FeatureSet payload table, Strategy Engine status lines, Opportunity schema sections)
docs/architecture/trading-intelligence-architecture.md   (modified — §8 status line)
docs/decisions/confirmed-decisions.md          (modified — new #99)
docs/decisions/INDEX.md                        (modified — new row #99)
```

Unzip directly onto the project root — every path above is relative to it.

## Read this before running anything

**`FeatureSet.open`/`.high`/`.low`/`.volume` are populated on 1m only.**
Any code reading these on a 5m/15m/1h `FeatureSet` will get `None` —
this is deliberate (see decision #99), not a bug. If you have any other
in-flight work reading `FeatureSet` fields positionally rather than by
name, double-check it isn't affected (it shouldn't be — the new fields
are appended after `close`, all nullable, all keyword-populated at every
existing call site).

**`Strategy.evaluate()`'s real signature is `(self, symbol, market_state,
features, context)`** — four arguments, not the three shown in
`system-design.md` §4.8's illustrative sketch. If you or another session
write a second strategy against the *old* 3-arg sketch, it will still
import fine (Python doesn't enforce ABC signature matching) but will
raise `TypeError` the moment anything actually calls it with 4 arguments.
See `base_strategy.py`'s own module docstring for why the extra
parameter is there.

**A real bug was found in `MarketStateEngine`, not fixed here.**
`_latest_features[symbol]` is one shared slot regardless of timeframe —
any strategy running on a timeframe slower than 1m (i.e. any future
strategy besides ORB) should know `market_state.trend_score`/etc. can
transiently reflect a different timeframe's close than the one it's
evaluating. Full account in decision #99. Worth a direct decision with
you before Momentum (or anything non-1m) is trusted live.

## What was reconciled from this morning's two pushes

The Momentum session's review of the (now-discarded) `momentum_strategy.py`/
`vwap_strategy.py` found three real things while this build was in
progress. None of it is lost:

1. **Threshold-mirroring guard** — applied directly to `orb_strategy.py`'s
   `match_direction()` (`trend_score_threshold` must be `> 50.0`, same
   fix, same reasoning, own test).
2. **PROPOSE-stage sanity-guard bug** — confirmed NOT to apply to ORB,
   documented why in `orb_strategy.py`'s module docstring, rather than
   silently assumed safe.
3. **The `MarketStateEngine` timeframe race** (above) — genuinely open,
   flagged for you, not something either review or this build should
   have picked a fix for unilaterally.

One unrelated thing found and flagged, not fixed: `docs/architecture/premarket-accumulator-design.md`
still says "DRAFT, no code" — `vwap_ext` is actually already built in
`feature_engine/engine.py`. Feature Engine's own territory, out of scope
here.

## How to verify

```bash
cd backend
pip install -r requirements.txt   # if not already
pytest tests/test_base_strategy.py tests/test_orb_strategy.py -v
```

Expect 27 passed, 0 failed, 0 skipped — no DB required for either file.

Then the full suite, to confirm no regressions from the `FeatureSet`
change:

```bash
pytest tests/ -q
```

Verified in this session (no local Postgres available in the sandbox):
identical pass/fail signature before and after this change — same
pre-existing DB-connectivity failures, same skips, plus these 27 new
tests passing. **Run this against your real local Postgres before
treating it as fully clean** — this is the first real DB-backed
confirmation either way.

## Not verified

Live, or against any backtest harness — neither exists yet. §7's
"never `datetime.now()` inside `evaluate()`" constraint was followed by
construction and is unit-tested via fixed timestamps throughout, but
"byte-identical live and in backtest" itself has no harness to prove it
against until a Backtest Runner exists.

## Next

Momentum, rebuilt fresh against the real `base_strategy.py` (not the
discarded file), assigned to a separate session per your call. The
`MarketStateEngine` timeframe race above is worth deciding before that
session's Momentum is trusted against anything other than 1m.
