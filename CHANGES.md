# VWAP Strategy — MATCH/SCORE/PROPOSE review (pre-integration)

Copy this into your repo root, overwriting the existing path — replaces
the previous drop note. Same scope discipline as the Momentum drop:
`base_strategy.py` still doesn't exist on this track, this doesn't touch
it or guess further at its shape, everything here is a review of the
logic already in `vwap_strategy.py`, found by tracing the real
Feature/Market State Engine code its assumptions rest on.

## What changed, and why

**1. `match_direction()` now validates its own parameters.**
`trend_score_threshold` must be > 50 — exact same mirror-around-50
failure mode as Momentum's fix (SELL uses `100 - threshold`). Also new:
`vwap_score_low`/`vwap_score_high` must satisfy `50 < low < high <= 100`
— the BUY band is documented as "clearly above the line"; nothing
enforced that a band could dip to 50-or-below, invert, or collapse to
zero-width. Both raise `ValueError` now instead of silently producing a
band that no longer means what the docstring says it means.

**2. PROPOSE now refuses to emit a self-contradictory Opportunity.**
`direction` comes from `market_state.vwap_relationship_score`; the
target/invalidation math uses the LOCAL `features.close`/`vwap` pair.
Per the gap below, those aren't guaranteed to agree. If local `close`
doesn't actually confirm the direction against local `vwap`, this
strategy's own stated thesis — "holding VWAP as a level" — is already
false for the exact data the Opportunity would be built from. Now
returns `None` instead, same convention as every other guard in this
function and the identical fix already shipped for Momentum.

**3. `market_state.timeframe` now recorded in `evidence.conditions`** —
visibility for the gap below, not a behavior change.

## A gap found, refined from the Momentum drop — not fixed here

`MarketStateEngine`'s shared-slot-per-symbol race (full explanation in
`momentum_strategy.py`'s own docstring) applies here too, but I want to
correct rather than just repeat what that note said: it's LESS severe
for VWAP specifically, because VWAP's own default timeframe is 1m, and
1m `FeaturesUpdated` fires more often than any other timeframe — it's
the dominant writer of the shared slot most of the time, not a rare
visitor the way it is for Momentum's 5m default. Still not zero-risk
(a 5m/15m/1h close landing in the same debounce window can briefly
overwrite it), and would become exactly as severe as Momentum's if this
strategy's `timeframe` param were ever reconfigured away from 1m.

## A new gap, more consequential — needs your call, not picked for you

PROPOSE's target is `close + atr_target_multiplier * atr_14`. Traced
`atr_14` all the way to `feature_engine/indicators/atr.py`:  it's
Wilder ATR over the last 14 **complete daily bars**, recomputed once
per `(symbol, ET day)` and frozen — decisions #67/#68 built it as a
session-level statistic deliberately, "reads identically regardless of
chart timeframe," same shape as VWAP itself. That means the default
`atr_target_multiplier=2.0` sizes a **1-minute-chart** level-hold
target off a **daily** range measure — asking a quick VWAP-hold play to
capture 2x the stock's entire typical day's range before exit.

I did not pick a fix, because there isn't an unambiguous one available
from what's already built:

- Keep `atr_14` but use a much smaller multiplier (a fraction of daily
  ATR, e.g. 0.2-0.4x) — cheapest, but the "right" fraction is a guess
  same as every other unvalidated threshold in this file, and it's
  still conceptually a daily statistic standing in for an intraday one.
- Feature Engine would need a genuinely new indicator — an ATR computed
  on the strategy's own intraday timeframe — which doesn't exist
  anywhere in Feature Engine today; every ATR-consuming thing in the
  codebase (Scanner's activity score, the chart HUD) uses the same
  daily one, so this isn't an existing-but-undiscovered option.
- Size the target off something else already available at this
  timeframe instead of ATR entirely (e.g. distance already implied by
  the vwap_relationship_score band itself).

Raising this rather than shipping a guess, same principle as the
Momentum drop's Market State Engine flag — the current test suite and
default config still use the old math (untouched) until you tell me
which way to take it.

## Files changed

- `backend/app/strategy_engine/vwap_strategy.py` — threshold/band
  validation in `match_direction()`, PROPOSE-stage sanity guard,
  `market_state_timeframe` added to evidence, module docstring gained a
  "KNOWN GAPS" section covering both items above.
- `backend/tests/test_vwap_strategy.py` — 7 new tests: 1 for the
  trend-threshold validation, 5 parametrized cases for the band
  validation, 1 for the PROPOSE-stage guard. Existing BUY end-to-end
  test gained one assertion (`market_state_timeframe` in evidence).

## Verified

Same throwaway local stub of `Strategy`/`StrategyConfig`/`Opportunity`/
`every_candle` as the Momentum drop (matching `strategy-engine-
design.md` §1/§3/§4), not committed anywhere. Full
`test_vwap_strategy.py` suite: 18 passed (11 pre-existing + 7 new).
Sanity-checked `test_momentum_strategy.py` still passes against the
same stub (18 passed, no collateral damage) and
`test_strategy_integration_contract.py` — 10 skipped, as before, not
something either strategy file touches. Stub deleted before packaging.

## Next

Your call on the ATR target-sizing question above — that's the one
thing in this drop I'd want an answer on before it's treated as settled
rather than "logic reviewed, one open question." Same reconciliation-
against-real-`base_strategy.py` note as the Momentum drop otherwise
applies here too.
