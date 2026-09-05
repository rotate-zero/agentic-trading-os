# Momentum Strategy — MATCH/SCORE/PROPOSE review (pre-integration)

Copy this into your repo root, overwriting the existing path — replaces
the previous drop note.

**Scope, exactly as narrowed in our discussion.** Momentum track only,
no interface work: `base_strategy.py` still doesn't exist in this repo
(that's the other track's build), and this drop doesn't touch it, wait
for it, or guess further at its shape than `momentum_strategy.py`
already had. Everything here is a review of the *logic already in*
`momentum_strategy.py` — its MATCH/SCORE thresholds and PROPOSE math —
found by actually tracing the real `market_state_engine/scoring.py` and
`market_state_engine/engine.py` code its assumptions rest on, not by
re-reading the module's own comments.

## What changed, and why

**1. `match_direction()` now validates its own thresholds.** SELL's
confirmation mirrors each threshold as `100 - threshold`. That only
makes sense above 50 — a misconfigured `StrategyConfig` version with,
say, `trend_score_threshold=40` would let a *bullish* `trend_score` of
55 satisfy SELL's confirmation (`55 <= 100-40`), producing a
backwards-confirmed signal with no error anywhere. Now raises
`ValueError` instead of silently accepting it. Nothing upstream enforces
this today (no `StrategyConfig` validator exists yet, since
`StrategyConfig` itself doesn't exist yet) — this is the one place both
threshold params are guaranteed to pass through regardless of caller.

**2. PROPOSE now refuses to emit a structurally backwards Opportunity.**
`fast_ma` (9-bar average) can sit above `slow_ma` on a bar where the raw
`close` has pulled back below it — MATCH still says BUY (it's reading
the MAs, correctly), but the target/invalidation formulas both silently
invert when that happens: target lands below entry, invalidation sits
above it. The module's own stated thesis ("holding above/below the slow
MA") is already false in that case, so PROPOSE now returns `None`
instead — honest absence, same convention every other guard in this
function already uses.

**3. `market_state.timeframe` now recorded in `evidence.conditions`.**
Not a behavior change — a visibility one, for the gap below.

## A real gap found, not fixed here — needs your call

Tracing `MarketStateEngine._on_features_updated` (not just
`momentum_strategy.py`'s assumptions about it): `_latest_features[symbol]`
is one shared slot, overwritten by whichever timeframe's
`FeaturesUpdated` arrives most recently — 1m/5m/15m/1h all write to the
same slot, no timeframe filter. Since 1m candles close 5x more often
than 5m, `market_state.timeframe` will very often be `"1m"` at the exact
moment Momentum evaluates a 5m candle close. That means the
`trend_score`/`acceleration_score` confirmation MATCH relies on may
silently reflect a different timeframe than the crossover it's
confirming.

I deliberately did **not** hard-gate `evaluate()` on
`market_state.timeframe == params["timeframe"]` to "fix" this — given
the race described, an exact-match gate would make Momentum fail to
fire almost always, which is worse than today's silent behavior. This
also isn't Momentum-specific: any future strategy reading
`market_state.trend_score`/`acceleration_score` (ORB included, once it
reads Market State rather than raw features) inherits the same gap.
Likely real fix is Market State Engine tracking latest-features per
`(symbol, timeframe)` instead of one shared slot — that's a Market State
Engine change, outside what you scoped me to today. Flagging for a
decision rather than picking a fix unilaterally.

Separately, smaller and already just documented (not fixed, not really
fixable from this file): `market_state.trend_score`/`acceleration_score`
are always driven by `sma_20_slope_angle` specifically — hardcoded in
`scoring.py`, decision #93. Momentum's own `slow_period` config param
only changes the crossover comparison, not what "trend confirmation"
measures. A future `StrategyConfig` version with `slow_period != 20`
would be comparing its own crossover against a trend read anchored to a
different period. Noted in the module docstring so nobody assumes
versioning `slow_period` recalibrates both.

## Files changed

- `backend/app/strategy_engine/momentum_strategy.py` — threshold
  validation in `match_direction()`, PROPOSE-stage sanity guard,
  `market_state_timeframe` added to evidence, module docstring gained a
  "KNOWN GAPS" section covering both items above.
- `backend/tests/test_momentum_strategy.py` — 5 new tests: 4
  parametrized cases for the threshold validation (`ValueError`), 1 for
  the PROPOSE-stage guard (`close` on the wrong side of `slow_ma`
  despite a confirmed crossover). Existing BUY end-to-end test gained
  one assertion (`market_state_timeframe` present in evidence).

## Verified

`base_strategy.py` still doesn't exist on this track, so
`momentum_strategy.py` can't actually be imported yet — same blocker
flagged in the module's own INTEGRATION NOTE before this drop. To
exercise these changes anyway (this codebase's own "run things, don't
just inspect" principle), I wrote a throwaway local stub of
`Strategy`/`StrategyConfig`/`Opportunity`/`every_candle` matching
`strategy-engine-design.md` §1/§3/§4 exactly, ran the full
`test_momentum_strategy.py` suite against it (18 passed, including the 5
new tests), sanity-checked `test_vwap_strategy.py` still passed against
the same stub (11 passed, no collateral damage), then **deleted the
stub** — it is not part of this drop and not committed anywhere. Real
verification against the actual `base_strategy.py` is still pending,
same as before this drop.

## Next

Reconcile against the real `base_strategy.py` once that track lands —
`momentum_strategy.py`'s own INTEGRATION NOTE has the specific
assumptions to check (`__init__(self, config)`, `every_candle(timeframe=...)`,
`context: ContextChanged` vs. the doc's `Context`). Your call on the
Market State Engine timeframe gap above — whether it's worth a decision
number now or waiting until a strategy actually goes live against it.
