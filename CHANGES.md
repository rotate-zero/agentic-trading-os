# Design review's agreed changes — Gap, Volume Spike, and a shared utility

Copy this into your repo root, overwriting the existing path — replaces
the previous drop note (Gap/Volume Spike's initial build). This is the
response to the design-level review of that build: six agreed changes,
made in full, plus one thing discovered mid-change (git was updated
with First Pullback/Reversal) that changed this drop's scope slightly.

## Note: pulled First Pullback/Reversal mid-session

Before implementing, I pulled and found First Pullback and Reversal
(decisions #107–#110) had been built concurrently on another track.
Checked for conflicts first: `base_strategy.py`/`orb_strategy.py`/
`gap_strategy.py`/`volume_spike_strategy.py` were untouched by those
commits, so no code conflicts. Two consequences:

- This drop's own decision got renumbered from a planned #107 to **#111**
  (#107–#110 were already taken).
- `first_pullback_strategy.py`/`reversal_strategy.py` turned out to have
  independently duplicated the exact `_clamp`/threshold-guard pattern
  item 4 below was extracting — a fourth and fifth copy, found in real
  time. Folded both into the same extraction rather than leaving it
  half-finished; verified behavior-neutral against their own suites.

## The six changes

**1 — `regular_open` published by Feature Engine.** `gap_strategy.py`
no longer reconstructs it as `pdc + gap_dollars` — `_update_gap`
(`feature_engine/engine.py`) now publishes it as its own `features` key,
same category as `pdc`/`pdh`/`pdl`, known the moment today's regular
session opens even without a prior day to compare against.

**2 — Gap bounded to `max_minutes_since_open` (v1 default 60 min).**
Nothing previously stopped a match hours after the open — a 2pm reclaim
of `regular_open` read identically to a clean 9:30 hold. MATCH now
refuses past this window. Doesn't resolve the deeper, still-open
question (does an instantaneous close-vs-open comparison really prove
"holding"?) — deliberately deferred pending real outcome data.

**3 — Volume Spike gained `min_absolute_volume` (500 shares) and
`min_body_ratio` (0.3).** Closes two concrete gaps: a ratio-only test
can't catch a small order against a thin/illiquid baseline; a
huge-volume, razor-thin-body candle previously passed as directional on
`close != open` alone. Both computable from data already on the candle.
Deliberately NOT addressed: exhaustion-vs-continuation ambiguity, and
whether the spike candle's own wick makes an impractically wide stop —
both need real outcome data, not a guessed fix.

**4 — New shared `scoring_utils.py` (`clamp`, `trend_magnitude`,
`validate_mirror_threshold`).** Extracted from what was, by this point,
five near-identical copies (ORB, Gap, Volume Spike, plus First
Pullback/Reversal found mid-change). Each strategy's own MATCH
conditions and SCORE weights stayed exactly where they were — this is
mechanical, domain-free arithmetic only, not a "strategy scoring engine."

**5 — `Opportunity.expected_horizon_minutes` added to
`base_strategy.py`.** Every strategy had an implicit, unencoded
expectation of how long its setup should take — lost the moment
`evaluate()` returned. Optional, honest-absence default. Populated by
ORB (45 min), Gap (60 min), Volume Spike (15 min) — each a v1 guess.
Deliberately left unpopulated on First Pullback/Reversal — that's a
value judgment for whichever thread owns them, not something this drop
should decide on their behalf; the field is available to them
automatically since it defaults to `None`.

**6 — Renumbering only**, covered above.

## Files changed

- `backend/app/strategy_engine/scoring_utils.py` — new.
- `backend/app/strategy_engine/base_strategy.py` — `Opportunity.expected_horizon_minutes`.
- `backend/app/feature_engine/engine.py` — `_update_gap` publishes `regular_open`.
- `backend/app/strategy_engine/orb_strategy.py` — `scoring_utils` adoption + `expected_horizon_minutes`.
- `backend/app/strategy_engine/gap_strategy.py` — `regular_open` read directly, `max_minutes_since_open`, `scoring_utils`, `expected_horizon_minutes`.
- `backend/app/strategy_engine/volume_spike_strategy.py` — `min_absolute_volume`, `min_body_ratio`, `scoring_utils`, `expected_horizon_minutes`.
- `backend/app/strategy_engine/first_pullback_strategy.py`, `reversal_strategy.py` — `scoring_utils` adoption only, no behavior change.
- `backend/tests/test_scoring_utils.py` — new, 11 tests.
- `backend/tests/test_gap_strategy.py` — +3 tests (now 18): window-expiry pure tests + an end-to-end stale-signal test.
- `backend/tests/test_volume_spike_strategy.py` — +4 tests (now 22): absolute-floor and body-ratio pure tests, plus their end-to-end counterparts (thin-illiquid-baseline, wide-range-thin-body).
- `backend/tests/test_base_strategy.py` — +1 test for the new field.
- `backend/tests/test_orb_strategy.py` — assertion added to an existing test (no new test).
- `backend/tests/test_feature_engine.py` — assertions added to three existing gap tests for `regular_open` (no new tests; one runs DB-free and passes here, two are DB-gated).
- `docs/decisions/confirmed-decisions.md` — decision #111 appended (no rollover needed, file well under the size trigger).
- `docs/decisions/INDEX.md` — row added for #111.
- `docs/architecture/strategy-engine-design.md` — new §17 covering this change.
- `docs/architecture/trading-intelligence-architecture.md` §8 — also fixed a stale claim found in passing: it still said "First Pullback and Reversal remain unbuilt" after decisions #109/#110 had already built them. Updated to reflect all five built strategies accurately.

## Verified

Full backend suite, before and after this entire change, in this
session's own Postgres-free sandbox (fresh clone, includes decisions
#107–#110): identical 40 pre-existing DB-connectivity failures, 119
skipped, both unchanged; 346 passed vs. the 327-passed baseline
immediately before this change — exactly these 19 new tests, zero
regressions anywhere, including confirmation that folding First
Pullback/Reversal into the `scoring_utils.py` extraction left their own
(skipped, DB-gated) suites unaffected.

**Not verified:** against a real local Postgres (none available in this
session) — the two DB-gated `test_feature_engine.py` assertions for
`regular_open`'s freeze/backfill behavior specifically need your own
local run, same standing limitation every DB-gated file in this
project's log already carries.

## Left open, on purpose

Everything else the design review raised — the exhaustion-vs-
continuation ambiguity for Volume Spike, cooldown-vs-re-arm, restart
persistence for strategy state, promoting the rolling volume baseline to
Feature Engine, an explicit entry-price field on `Opportunity` — was
deliberately left alone. Either they need real backtest/outcome data to
resolve responsibly, or they're not costing anything by waiting. Full
reasoning for each is in the review itself, not repeated here.

## Next

- First Pullback and Reversal's own `expected_horizon_minutes` values,
  if you want them populated — that's a call for whichever thread picks
  those files back up, not made here.
- Momentum and VWAP remain the two unbuilt strategies from the planned
  v1 set.
- Your own local Postgres run for the two DB-gated `regular_open`
  assertions above.
