# Strategy Engine — Build History (§14–§18)
**Owner:** Saqib
**Split from `strategy-engine-design.md`** (`split-strategy-engine-design-doc`, decision #142) — this file holds what was that document's §14–§18; section numbers are unchanged, and the content is unedited by the split.
**What this is:** the as-built account of how each of the 7 v1 strategies (ORB, Gap, Volume Spike, First Pullback, Reversal, Momentum, VWAP) actually got built, including design reviews and corrections along the way. Read this when working on one of these strategies specifically — for a first-pass understanding of Strategy Engine, read `strategy-engine-design.md` instead.
**Companion documents:** [`strategy-engine-design.md`](./strategy-engine-design.md) (§0–§6, §8–§9, §11–§13 — the design these strategies were built against), [`strategy-engine-open-decisions.md`](./strategy-engine-open-decisions.md) (D9/D11, surfaced during this build history), [`backtest-runner-design.md`](./backtest-runner-design.md), [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md).

---

## 14. ORB — Stage 1's first strategy, and what actually happened getting there (decision #99)

**What was found before any of this was built:** `momentum_strategy.py` and `vwap_strategy.py` already existed in the repo, from an earlier concurrent session — undocumented. Both imported a `base_strategy.py` that didn't exist anywhere. Their own docstrings admitted this and flagged three unverified interface assumptions. A `TESTING.md` at the repo root claimed decision #98 for that work — but the real decision #98 in `confirmed-decisions.md` is the M4 integration milestone, a different piece of work entirely. Two concurrent sessions minted the same decision number; only the M4 session's doc updates actually landed. The momentum/vwap code was orphaned, silently, with no working `pytest` collection possible. Discarded rather than built on top of — see the session's own discussion for the full reasoning. *(Historical note, as of decision #99: Momentum was assigned to be rebuilt fresh, by a separate session, against the now-real interface below. Both it and VWAP are since built — decision #113, §18 — against this same interface, discarded draft not consulted.)*

**GATE/MATCH/SCORE/PROPOSE, applied to ORB's actual state machine** (not the discarded sketch's time-boxed trigger — see `orb_strategy.py`'s module docstring, correction #1):

```
 every 1m candle, all session long (trigger = every_candle("1m"))
      │
      ▼
 ┌─────────────────────────┐   minutes_since_open      ┌──────────────────────────┐
 │  FORMING                │ ─────< or_minutes ───────▶│  accumulate this         │
 │  (0 candles..or_minutes)│                            │  candle's high/low into  │
 └─────────────────────────┘                            │  the running OR;         │
      │                                                  │  return None             │
      │ minutes_since_open >= or_minutes                └──────────────────────────┘
      ▼
 ┌─────────────────────────┐   candles_seen < or_minutes
 │  freeze the range        │──────────────────────────▶ return None, permanently,
 │  (or_formed = True)      │   (gap / late start)        for the rest of this day
 └─────────────────────────┘   — honest absence,          (module docstring's
      │                          not a fabricated range    accepted limitation)
      │ candles_seen == or_minutes
      ▼
 ┌─────────────────────────┐
 │  GATE passed — MATCH:    │   close > or_high & trend/volume confirm  ──▶ BUY
 │  breakout test against   │
 │  the frozen or_high/low  │   close < or_low  & trend/volume confirm  ──▶ SELL
 └─────────────────────────┘
      │
      │ direction not in fired_directions (else: already proposed today, return None)
      ▼
 SCORE (trend + volume + breakout-strength blend) → PROPOSE an Opportunity,
 mark direction as fired for today. Reset entirely at the next trading_day
 (MarketClock-derived, never wall-clock).
```

**State is symbol-keyed inside the strategy instance, never published.** One `ORBStrategy` instance serves every symbol (same singleton-with-internal-keying shape `FeatureEngine`/`MarketStateEngine`/`LevelInteractionEngine` already use) — `evaluate()` takes `symbol: str` explicitly so that keying is possible (base_strategy.py's own docstring, assumption #4). The opening range itself is Saqib's explicit call to keep private rather than publish to `FeatureSet` — accepted trade-off: no persistence, no restart-survival mid-day (see `orb_strategy.py`'s module docstring for the full accounting of that limitation).

**Verification:** `backend/tests/test_base_strategy.py` (9 tests) + `backend/tests/test_orb_strategy.py` (18 tests) — pure GATE/MATCH/SCORE math plus a full multi-day, multi-symbol `evaluate()` simulation (formation → freeze → breakout → fire-once → reversal → day rollover → independent per-symbol state → honest absence on missing OHLC or a missed formation window). Full existing suite re-run against this change: identical pass/fail signature to the pre-change baseline (40 pre-existing DB-connectivity failures in a sandbox with no local Postgres, 91 skipped, both unchanged) plus these 27 new tests passing — zero regressions from the `FeatureSet`/`feature_engine/engine.py` OHLC threading.

**A second reconciliation, from an independent set of concurrent-session findings, folded in before this was finalized.** A separate session (assigned Momentum) reviewed the discarded `momentum_strategy.py`/`vwap_strategy.py` in place rather than rebuilding fresh, and found two real bugs plus one Feature Engine documentation gap — see decision #99's full account for all three, and `match_direction()`'s own docstring in `orb_strategy.py` for the one applied directly here (a threshold-mirroring guard). The `MarketStateEngine._latest_features` per-symbol timeframe race those reviews found remains open, flagged for Saqib, not fixed by this build.

---

## 15. Gap and Volume Spike — the second and third strategies after ORB (decisions #104, #105)

Both built against the real `base_strategy.py`, after decision #103 closed the `MarketStateEngine` per-symbol timeframe race §14 flagged — neither has the shared-slot exposure ORB/Momentum/VWAP each had to reason about. Full reasoning for every design choice below lives in each module's own docstring (`gap_strategy.py`, `volume_spike_strategy.py`) and decisions #104/#105 — this section is a summary and a diagram, not a duplicate of either.

**Gap — is today's opening gap holding (continuation), or already given back (reversal)?**

```
 every 1m candle, regular session only (trigger = every_candle("1m"))
      │
      ▼
 gap_pct / gap_dollars / pdc present in features.features?
      │ no ──▶ return None (no gap yet, or no prior trading day — honest absence)
      │ yes
      ▼
 already fired today for this symbol?
      │ yes ──▶ return None (gap_pct's sign is frozen for the day — only one
      │          possible direction, unlike ORB's two-sided range)
      │ no
      ▼
 regular_open = pdc + gap_dollars        (reconstructed, not separately tracked)
      │
      ▼
 MATCH: |gap_pct| >= min_gap_pct  &  volume_regime_score confirms  &
        gap_pct > 0 → close > regular_open & trend_score confirms   ──▶ BUY
        gap_pct < 0 → close < regular_open & trend_score confirms   ──▶ SELL
      │
      ▼
 SCORE (trend + volume + gap-size blend) → PROPOSE an Opportunity,
 invalidation = regular_open (the same level MATCH just tested against),
 mark fired for today. Reset entirely at the next trading_day.
```

**Volume Spike — did this candle print unusually heavy volume, and which way did the market move on it?**

```
 every 1m candle, regular session only (trigger = every_candle("1m"))
      │
      ▼
 open/high/low/volume all present? ── no ──▶ return None (pre-#99 shape,
      │ yes                                   or an aggregated FeatureSet)
      ▼
 rolling baseline has lookback_bars samples yet?
      │ no ──▶ push this candle's volume, return None (honest warm-up,
      │         same discipline as ORB's candles_seen < or_minutes)
      │ yes
      ▼
 baseline = mean(prior lookback_bars volumes)   (this candle NOT included)
 volume_ratio = this candle's volume / baseline
 push this candle into the window for the NEXT candle's baseline
      │
      ▼
 within cooldown_minutes of the last fire for this symbol?
      │ yes ──▶ return None (floor against re-firing the same still-elevated move)
      │ no
      ▼
 MATCH: volume_ratio >= spike_ratio_threshold  &  volume_regime_score confirms  &
        close > open → trend_score confirms   ──▶ BUY
        close < open → trend_score confirms   ──▶ SELL
      │
      ▼
 SCORE (trend + volume + spike-excess blend) → PROPOSE an Opportunity,
 invalidation = this candle's own low/high (the same bar MATCH just tested),
 record last_fired_ts. Reset the whole baseline + cooldown at the next trading_day.
```

**Three design choices worth naming, since each deliberately diverges from ORB's own precedent rather than copying it wholesale:**

1. **Gap's `_GapState.fired` is a plain `bool`, not ORB's `fired_directions: set`.** A gap's direction is fixed by `gap_pct`'s own sign for the whole day (`_update_gap`, decisions #67/#68) — there is never a second, opposite direction for a reversal to test against the way ORB's fixed range allows both sides.
2. **Volume Spike uses a per-symbol cooldown timer, not a fired-once-per-day flag.** Unlike Gap (one fixed daily event) or ORB (one fixed daily range), a volume spike is a recurring pattern — a busy session can produce several genuine, independent spikes hours apart, so a once-per-day cap would discard real signal. The cooldown only floors against re-firing the immediate next candle or two of the *same* still-elevated move.
3. **Volume Spike needed genuinely new per-symbol state (a rolling volume baseline) that no other engine provides — the same "strategy-private state" precedent ORB's own opening range already established, not new territory.** `rvol` (Feature Engine, decision #71) is a day-level, time-of-day-normalized proxy; Market State's `volume_regime_score` already interprets it. Neither answers "was THIS candle anomalous relative to this symbol's own last N bars" — a narrower, single-candle claim nothing else in the codebase computes.

**Verification:** `backend/tests/test_gap_strategy.py` (15 tests) + `backend/tests/test_volume_spike_strategy.py` (18 tests) — pure GATE/MATCH/SCORE math plus end-to-end multi-day, multi-symbol `evaluate()` simulations for each (session gating, day rollover, independent per-symbol state, honest absence on missing data). Full existing suite re-run against this change: identical pass/fail signature to the pre-change baseline (40 pre-existing DB-connectivity failures, 91 skipped, both unchanged) plus these 33 new tests passing — zero regressions.

---

## 16. First Pullback and Reversal — design, review, and build (decisions #107, #108, #109, #110)

The last two strategies from trading-intelligence-architecture.md §8's planned v1 set (ORB, Gap, Volume Spike built; Momentum still assigned elsewhere). **Built** — `first_pullback_strategy.py`/`reversal_strategy.py`, plus a small shared `level_touch_tracking.py` module both depend on. The design below (decision #107) went through an external design review before any code was written, per Saqib's own "short design note first, then code" instruction; the review's findings and this file's response to them are decision #108, folded into the sections below rather than kept as a separate narrative — see "What the design review changed" near the end of this section for the review itself and how each point was resolved. Both strategies read `LevelInteractionEngine`'s touch/holding/rejected/conquered vocabulary (decision #46) as a settled input — same "consume, don't rebuild" boundary discipline `orb_strategy.py` already applies to `trend_score`/`volume_regime_score`. Neither strategy re-derives zone classification, Aura width, or touch counting — that's `LevelInteractionEngine`'s own job.

**Shared mechanism both strategies build on: private per-symbol touch tracking, one candle late.**

`get_snapshot()` (decision #47) only exposes the CURRENT steady `zone` — by the time a touch resolves and `zone` moves on, the transient `status` ("rejected"/"conquered") that produced it is already gone from the snapshot; it only ever existed on the `LevelInteractionChanged` event itself, which neither strategy can subscribe to yet (see the D9 callout below). Both strategies work around this identically: while `zone == "inside_aura"`, remember `entered_from` in a small private dataclass keyed by symbol; on a later candle, once `zone` is no longer `"inside_aura"`, compare the resolved zone to the remembered `entered_from` — equal means REJECTED (bounced back out the side it came from), different means CONQUERED (broke through) — the exact same rule `level_interaction_engine.py`'s own module docstring defines, just re-derived one candle after the fact instead of read off the authoritative event.

```
 watching?  (private per-symbol state: level_key, entered_from, trading_day)
      │
      no ──▶ zone == "inside_aura" this candle?
      │            │ no  ──▶ nothing to watch yet, return None
      │            │ yes ──▶ remember entered_from + trading_day, start watching, return None
      │
      yes ──▶ zone still "inside_aura"?
                   │ yes ──▶ still resolving, return None (no allows_waiting yet — see below)
                   │ no  ──▶ resolved. compare resolved zone to remembered entered_from:
                                  equal      → REJECTED (bounced back)
                                  not equal  → CONQUERED (broke through)
                              stop watching this touch either way
```

**First Pullback — is this the trend's first pullback to a key reference level today, and did the level hold?**

```
 every 1m candle (trigger = every_candle("1m"))
      │
      ▼
 features.timeframe == "1m"?  ── no ──▶ return None
      │ yes
      ▼
 established trend? |trend_score - 50| past trend_score_threshold
 (mirror-around-50, threshold > 50 — same guard as match_direction(), decision #99)
      │ no ──▶ return None (no trend, nothing to pull back within)
      │ yes  →  direction = BUY if trend_score ≥ threshold, SELL if ≤ 100-threshold
      ▼
 get_level_interaction_engine().get_snapshot(symbol)
   .get(timeframe, {}).get(level_key)
      │ missing ──▶ return None (level not tracked yet — honest absence, not an error)
      ▼
 touch_count_today == 1?  (the FIRST pullback specifically — a later touch is a
      │                     different, not-yet-built strategy family)
      │ no ──▶ return None
      ▼
 [shared touch-tracking mechanism above] → resolved REJECTED, in trend's favor?
      │ CONQUERED, or not yet resolved ──▶ return None (see mechanism diagram)
      │ REJECTED
      ▼
 already fired today for this symbol?  ── yes ──▶ return None
      │ no
      ▼
 SCORE (trend + volume_regime_score + resolution distance_pct blend) → PROPOSE,
 invalidation = anchor_price (the level's own value when the touch began —
 a re-test failing below/above that same value falsifies the thesis),
 mark fired for today.
```

**Reversal — has an established trend's key level just been conquered against it?**

Same touch-tracking mechanism, opposite confirming outcome, and deliberately NOT restricted to the first touch — a reversal is often the second or third test that finally breaks, not the first, so `touch_count_today` is read for SCORE but never gates MATCH the way it does for First Pullback.

```
 every 1m candle (trigger = every_candle("1m"))
      │
      ▼
 features.timeframe == "1m"?  ── no ──▶ return None
      │ yes
      ▼
 trend_score still shows the OLD, about-to-be-tested direction past threshold?
      │ no ──▶ return None (no established direction left to reverse)
      │ yes  →  the trend being tested is BUY-side if trend_score ≥ threshold, SELL-side if ≤ 100-threshold
      ▼
 get_level_interaction_engine().get_snapshot(symbol).get(timeframe, {}).get(level_key)
      │ missing ──▶ return None
      ▼
 [shared touch-tracking mechanism above] → resolved this candle?
      │ not yet, or REJECTED (level held, trend intact) ──▶ return None, keep counting touches
      │ CONQUERED
      ▼
 already fired a reversal today for this symbol?  ── yes ──▶ return None
      │ no
      ▼
 MATCH confirmed — direction is the MIRROR of the trend just broken
 (an established uptrend conquered downward proposes SELL, not BUY)
      │
      ▼
 SCORE (broken-trend strength + volume_regime_score + touch_count_today blend —
 a break after several prior holds is stronger evidence than a break on the
 very first test) → PROPOSE,
 invalidation = anchor_price, mark fired for today.
```

**`level_key` is a `StrategyConfig.params` value (v1 default `"vwap"`), not a hardcoded constant — same "not its own strategy class" precedent §3 already sets for SMA 9/20.** A second `StrategyConfig` version pointed at `"sma_20"` gives a second First Pullback variant for free, no new code.

**D9 — why neither strategy reads `LevelInteractionChanged`'s `status` field directly, and why `base_strategy.py` doesn't change.** The clean, event-driven design would be `trigger = on_event("LevelInteractionChanged")` with the event payload passed into `evaluate()` — but `base_strategy.py`'s own docstring already flags that no Scheduler exists to wire `on_event(...)` triggers to anything live, and `evaluate()`'s fixed 4-argument signature (`symbol`, `market_state`, `features`, `context`) has no slot for an arbitrary triggering event's payload regardless. Building that wiring for two strategies, ahead of ORB/Gap/Volume Spike ever needing it, would be exactly the kind of speculative generality §11 already argues against. Both strategies instead call `get_level_interaction_engine().get_snapshot(symbol)` directly inside `evaluate()` — the same free-function-singleton pattern `orb_strategy.py` already uses for `get_market_clock()` — and accept the one-candle-late re-derivation described above as the cost of not touching the shared interface. Full open item logged as §10's D9.

**`allows_waiting` stays `False` for both, v1.** First Pullback's "touch just started, not yet resolved" moment (the `watching` state in the mechanism diagram above) is a natural fit for a `status="waiting"` Opportunity under §8's ACT/WAIT/ABANDON model, rather than silently returning `None` until resolution. But D5 (the waiting-value model itself) is explicitly deferred until a real strategy needs it, and `allows_waiting` defaults `False` everywhere in v1 — First Pullback would be the first real trigger for D5, not decided here. Flagged rather than built speculatively, same restraint §11 asks for.

**Not yet decided, still open after the build:** the exact SCORE blend weights and `trend_score_threshold` default (v1 guess, unvalidated against real score distributions, same caveat every other strategy's calibration constants already carry).

---

### What the design review changed (decision #108)

Before any code was written, this design went to an external review (Saqib's standing practice of consulting multiple AI systems before bringing consolidated direction back — see the project's own "Approach & patterns"). The review's five points and this file's response, in the order raised:

1. **"Make sure the strategy's reconstruction is exactly equivalent to `LevelInteractionEngine`'s own definition — inspect the real implementation, don't assume."** It wasn't equivalent as first drafted. Reading `level_interaction_engine.py`'s actual `_process_level`/`_process_one` against the original "watch while `inside_aura`, compare on resolution" sketch found two real gaps, both now fixed in `level_touch_tracking.py` (see its own module docstring for the full mechanism): **gap-through** (a zone jumping straight between `below`/`above` with `inside_aura` never observed — the engine still counts it as a touch and always calls it `conquered`; the original sketch would never have seen it at all, since it only started watching on an `inside_aura` observation) and **cold-start-unknown-origin** (`entered_from is None`, the engine's own "can't determine which side" case, decision #46 — the original sketch would have guessed a direction instead of declining to classify). Both are now handled identically to the engine's own rules, not approximated.

2. **A third issue, not raised by the review, surfaced by that same source-reading: `get_snapshot()` is fed by an async queue + `asyncio.to_thread` worker (`_worker_loop`/`_process_one`), not computed synchronously on the triggering `FeaturesUpdated`.** A strategy reading `get_snapshot()` right after publishing a candle has no built-in guarantee the engine has finished processing that exact candle yet — and unlike Context Engine's `get_snapshot()` (which exposes `evaluated_at`), `LevelInteractionEngine.get_snapshot()` exposed no recency signal at all. Resolved by exposing `last_applied_candle_ts` per entry (`level_interaction_engine.py`, this decision) — the same `_last_applied_ts` value `_process_one`'s own out-of-order guard already tracked internally, now surfaced rather than computed fresh. Both strategies check it before ever calling into `level_touch_tracking.observe_resolution()`, and skip the candle entirely (not just return early with a stale trust) on a lagging read — see either strategy's own GATE section and `level_touch_tracking.py`'s module docstring for why feeding a stale entry through the tracker would corrupt its own zone history.

3. **"Keep interaction detection separate from strategy recognition"** — confirmed clean, with one adjustment: rather than each strategy independently re-deriving `entered_from`/`anchor_price`, `level_touch_tracking.py` reads those verbatim from `get_snapshot()`'s own `holding` dict and never recomputes them; the ONE thing genuinely reconstructed is the final "same side or opposite side" comparison the engine's own `status` field would answer directly if a strategy could reach it. `LevelInteractionEngine` still owns `zone`/`touch_count_today`/`entered_from`/`anchor_price`/`trading_day` outright.

4. **"Do not build event infrastructure speculatively."** Agreed, unchanged — `base_strategy.py` was never modified; the `get_level_interaction_engine().get_snapshot(symbol)` polling pattern (D9) stands as designed, and `level_touch_tracking.py` is deliberately isolated so it can be deleted outright, no other file touched, the day real `on_event(LevelInteractionChanged)` wiring exists.

5. **"Separate MATCH from SCORE strictly."** Confirmed already the shape both strategies were designed with — `volume_regime_score`, resolution distance, and touch count were already SCORE-only in the original sketch. Flagged explicitly in both files' own module docstrings: this is a deliberate divergence from `orb_strategy.py`'s `match_direction()`, which DOES use `volume_regime_score` as a MATCH-stage participation floor. Not retrofitted onto ORB/Gap/Volume Spike — a live open question for whoever next touches any of the three, not resolved here.

6. **Invalidation semantics.** `structural_invalidation = anchor_price` kept, with the framing tightened rather than the logic changed: both files' docstrings now say explicitly that this is a THESIS boundary ("falsified if a later candle closes back through this value"), not an executable stop price — that translation is Trade Planning Engine/Position Monitor's job downstream (neither built yet), matching `Opportunity.structural_invalidation`'s own field comment in `base_strategy.py`.

7. **First Pullback vs. Reversal touch-count gating, and `level_key` configurability.** Both already matched the review's description exactly (First Pullback gates on `touch_count_today == 1`, Reversal never does; `level_key` a params default) — confirmed, no change.

### The build itself

`level_touch_tracking.py` — the shared resolution-reconstruction module described above, its own file so the two strategies never carry two subtly different copies of "rejected vs. conquered." `first_pullback_strategy.py` and `reversal_strategy.py` — both follow `orb_strategy.py`'s GATE → MATCH → SCORE → PROPOSE shape exactly, `_FirstPullbackState`/`_ReversalState` mirroring `_ORBState`'s per-symbol dict shape. One asymmetry worth flagging: Reversal's MATCH condition (CONQUERED) genuinely includes gap-throughs, which never have an `anchor_price` to invalidate against — falls back to `features.features.get(level_key)` (the level's live current value, read straight off the same `FeaturesUpdated` already in hand) rather than failing to propose. First Pullback never hits this path (it only ever fires on `rejected`, and gap-throughs are unconditionally `conquered`), so no equivalent fallback was needed there.

**Verification:** `backend/tests/test_level_touch_tracking.py` (13 tests, pure — every scenario in that module's own docstring: normal reject/conquer, both gap-through directions, both cold-start flavors, day rollover) + `backend/tests/test_first_pullback_strategy.py` (16 tests) + `backend/tests/test_reversal_strategy.py` (12 tests) — each strategy's suite covers pure GATE/MATCH/SCORE math, the staleness guard in isolation (a stubbed engine, no DB needed to prove that branch), and end-to-end `evaluate()` runs against a REAL `EventBus` + REAL `LevelInteractionEngine` + REAL Postgres (same posture `test_level_interaction_engine.py` already established — skipped as a whole, not failed, if Postgres isn't reachable), publishing genuine `FeaturesUpdated` sequences and reading genuine engine-computed zone transitions rather than a hand-rolled substitute. Full suite re-run against this change: 486 passed, 0 failed (up from the pre-change 445 passed baseline — the 41 new tests, zero regressions).

---

## 17. Gap/Volume Spike design review, and the six changes it produced (decision #111)

A structured design-level review of decisions #104/#105 (not a code/test review — an explicit audit of layer ownership, trading semantics, timing/entry model, `Opportunity` semantics, state lifecycle, and multi-symbol/multi-day isolation). Full review reasoning isn't reproduced here — it lives in this session's own record — this section covers what changed as a result and why, for future sessions that need the "what's different now and why" without the full review transcript.

**Six items, all six made:**

1. **`regular_open` published by Feature Engine, no longer reconstructed.** `gap_strategy.py`'s own `pdc + gap_dollars` algebra is gone — `_update_gap` (`feature_engine/engine.py`) now publishes `regular_open` as its own `features` key, the same generic-market-fact category as `pdc`/`pdh`/`pdl`, independent of whether `gap_pct`/`gap_dollars` are also present (known the moment today's regular session opens, even without a prior day to compare against).

2. **Gap bounded to `max_minutes_since_open` (v1 default 60, unvalidated).** MATCH now refuses past this window regardless of how confirming everything else is — a real gap the original build had no answer for (nothing stopped a 2pm reclaim of `regular_open` from reading identically to a clean 9:30 hold). Computed via `MarketClock.minutes_since_open()` in `evaluate()`'s own orchestration, passed into `match_direction()` as a plain value — the pure function itself stays clock-free. Does NOT resolve the deeper, still-open question of whether an instantaneous `close` vs. `regular_open` comparison actually proves "holding" rather than "currently on the right side" — deliberately deferred pending real outcome data.

3. **Volume Spike gained `min_absolute_volume` (v1 default 500 shares) and `min_body_ratio` (v1 default 0.3).** Two concrete MATCH-stage gaps closed: a ratio-only test can't tell a genuine spike from a small order against a thin/illiquid baseline; a huge-volume, razor-thin-body candle previously passed as directional on `close != open` alone. `body_ratio = abs(close-open)/(high-low)`, computed from OHLC already on the candle, safe by construction (`close != open` already guarantees `high > low`). Deliberately NOT addressed: whether high volume + directional close actually means continuation vs. exhaustion, and whether sizing the stop off the spike candle's own wick can produce an impractically wide risk/target — both flagged as needing real outcome data, not a guessed fix.

4. **`scoring_utils.py` — new shared module (`clamp`, `trend_magnitude`, `validate_mirror_threshold`).** `_clamp()` and the `trend_score_threshold > 50.0` mirror-guard had been copy-pasted, near-verbatim, across `orb_strategy.py`, `gap_strategy.py`, `volume_spike_strategy.py` — extracted, with each strategy's own MATCH conditions and SCORE weights staying exactly where they were. **Mid-change discovery:** a `git pull` immediately before this item found `first_pullback_strategy.py`/`reversal_strategy.py` (decisions #107–#110, merged concurrently) had independently duplicated the identical pattern a fourth and fifth time — folded into the same extraction rather than left half-finished, verified behavior-neutral against their own test suites.

5. **`Opportunity.expected_horizon_minutes: int | None = None` added to `base_strategy.py`.** Every strategy had an implicit, un-encoded expectation of how long its setup should take — lost the moment `evaluate()` returned, since nothing on the schema carried it. Optional, honest-absence default. Deliberately not an expiry mechanism (`wait_expires_at` already owns that, WAITING-path only) — descriptive metadata for a future Decision Engine to reason with. Populated by ORB (45 min), Gap (60 min), Volume Spike (15 min) — each a v1 guess. Deliberately left unpopulated on First Pullback/Reversal, that thread's own call to make.

6. Renumbering only — this was drafted as decision #107 before the concurrent First Pullback/Reversal work (which claimed #107–#110) was pulled; became #111 with no content change.

**Verification:** `backend/tests/test_scoring_utils.py` (new, 11 tests), plus 8 new tests across `test_gap_strategy.py` (18, was 15) and `test_volume_spike_strategy.py` (22, was 18) pinning the two new Volume Spike checks and the Gap window against the exact failure modes the review described, plus one new `test_base_strategy.py` test for the schema addition. Full suite: 346 passed, 40 pre-existing DB failures (unchanged), 119 skipped (unchanged) — 327-passed baseline + these 19 new tests, zero regressions, including confirmation that the `scoring_utils.py` fold-in left First Pullback/Reversal's own (skipped, DB-gated) suites unaffected.

---

## 18. Momentum and VWAP — design, external review, and build (decision #113)

The sixth and seventh, and last, v1 strategies from `trading-intelligence-architecture.md` §8's planned set. Went through an external (ChatGPT) design review before any code was written — same "design note first, then code" practice §16 already documents for First Pullback/Reversal. Full review transcript isn't reproduced here; this section covers what was decided and built.

### Momentum

**Question:** is an existing directional move accelerating, right now, regardless of time of day or any specific price level? Deliberately not time-boxed or level-based — that distinction is what keeps this genuinely different from ORB (§14), not a restatement of it. First real consumer of `MarketState.acceleration_score` — checked directly against `market_state_engine/scoring.py` rather than assumed from the field's name: it's `trend_score`'s own rate of change, no volume folded in.

```
 every 1m candle, all regular session
      │
      ▼
 ┌───────────────────────────┐
 │ 1. GATE                   │  regular session, high/low present,
 │    is it worth checking?  │  acceleration_score not null, swing
 │                           │  window warmed up, not in cooldown
 └─────────────┬─────────────┘
               │ pass
               ▼
 ┌───────────────────────────┐
 │ 2. MATCH                  │  volume floor (participation) →
 │    accelerating +         │  acceleration off-neutral in one
 │    directional + backed   │  direction (PRIMARY) → trend_score
 │                           │  leaning the same way (lighter
 │                           │  CONTEXT bar, not "established")
 └─────────────┬─────────────┘
               │ true
               ▼
 ┌───────────────────────────┐
 │ 3. SCORE                  │  0.45×acceleration + 0.25×trend
 │    → confidence           │  + 0.30×volume — same hierarchy
 │                           │  as MATCH, numerically
 └─────────────┬─────────────┘
               │
               ▼
 ┌───────────────────────────┐
 │ 4. PROPOSE                │  invalidation = prior N-bar swing
 │    → Opportunity          │  low/high (own private state);
 │                           │  target = mechanical R-multiple
 └───────────────────────────┘
```

Trend's MATCH-stage threshold (`DEFAULT_TREND_CONTEXT_THRESHOLD = 55.0`, own params key `trend_context_threshold`) is deliberately NOT `scoring_utils.ESTABLISHED_TREND_SCORE_THRESHOLD` — a lighter, distinct question ("at least leaning the right way") from Reversal/VWAP's "established" claim; reusing the shared 60/40 bar here would filter out exactly the earliest, most valuable part of a fresh move, where `trend_score` is still crossing the low-50s while `acceleration_score` is already extreme.

Invalidation is a new private rolling swing lookback (`DEFAULT_LOOKBACK_BARS = 10`), not ATR, not `LevelInteractionEngine` — the review's own preferred framing ("the structure supporting the move failed" vs. "price moved X against me"). `FeatureSet` publishes no swing-high/low, so this needed genuinely new per-symbol state, same category as ORB's opening range / Volume Spike's rolling baseline. During the first `lookback_bars` minutes of each trading day per symbol, this file simply doesn't fire — honest-absence, same precedent Volume Spike's own baseline warm-up already set, deliberately not the review's suggested ATR-during-warm-up fallback (one fewer code path, and the warm-up window is small enough that "don't fire yet" is a fully acceptable v1 answer).

Cadence is `volume_spike_strategy.py`'s exact cooldown precedent (`DEFAULT_COOLDOWN_MINUTES = 5`), not once-per-day (ORB/Gap) — Momentum's whole point is catching multiple genuinely independent acceleration phases across a session. The review's further refinement (recognizing "still the same episode" rather than treating every cooldown-cleared candle as brand new) was explicitly NOT built — acceptable for v1 per the review's own conclusion, deferred until real live behavior shows the plain cooldown is insufficient.

**Momentum vs. ORB — co-firing accepted directly, no arbitration added.** Confirmed by both Saqib and the review: the two ask genuinely different questions (a specific morning-range level breaking vs. the market already moving and gaining force, independent of any level), and nothing in either file or `base_strategy.py` should arbitrate between them — matching `base_strategy.py`'s own framing that strategy competition is downstream (Opportunity/Decision Engine's job), not this layer's.

### VWAP

**Question:** has intraday VWAP-side control transitioned, before an established trend exists? Two candidate designs were considered and rejected first:

- **Relationship persistence** (`vwap_relationship_score` confirming strength + trend agreeing, no `LevelInteractionEngine`) — rejected: risked being "Momentum with a different gating field," not a genuinely different trading question.
- **Naive two-candle cross** (`close` vs. `vwap`, no engine) — rejected as too primitive: would false-trigger on exactly the chop `LevelInteractionEngine`'s Aura band already exists to absorb, and would be a second, cruder, silently-drifting definition of "meaningful move" alongside the engine's own authoritative one.

What shipped instead — a genuine `conquered` resolution, reused verbatim from `level_touch_tracking.observe_resolution()` (decisions #107/#108, zero new touch/cross-detection code), gated to `trend_score`'s neutral band:

```
 every 1m candle
      │
      ▼
 ┌───────────────────────────┐
 │ 1. GATE                   │  snapshot fresh (last_applied_
 │    is it worth checking?  │  candle_ts staleness guard, D9),
 │                           │  resolution == "conquered"
 └─────────────┬─────────────┘
               │ pass
               ▼
 ┌───────────────────────────┐
 │ 2. MATCH                  │  trend_score in the NEUTRAL band
 │    control transitioned,  │  only (D11's shared constant,
 │    no established trend   │  Reversal's exact complement) →
 │                           │  direction = the RESOLVED zone
 │                           │  itself, never a mirror of trend
 └─────────────┬─────────────┘
               │ true
               ▼
 ┌───────────────────────────┐
 │ 3. SCORE                  │  0.55×distance_pct (transition
 │    → confidence           │  strength) + 0.45×volume;
 │                           │  touch_count logged, not scored
 └─────────────┬─────────────┘
               │
               ▼
 ┌───────────────────────────┐
 │ 4. PROPOSE                │  invalidation = anchor_price
 │    → Opportunity          │  (live level value if gap-through);
 │                           │  target = mechanical R-multiple
 └───────────────────────────┘
```

**Direction is the structural opposite of Reversal's own `match_direction()`.** Reversal bets against whichever trend is established — its direction comes from mirroring `trend_score`, never from which way the conquest itself moved. VWAP has no established trend to mirror against by construction (the neutral-band gate guarantees that), so direction comes from the conquest's own resolved `zone` instead. This is the strongest evidence the two strategies ask genuinely different questions rather than the same one with a relabeled gate.

**Disjoint from Reversal by construction, not left to downstream arbitration — Saqib's explicit call**, made directly rather than "co-firing is fine" (the accepted answer for Momentum/ORB above): VWAP and Reversal read the exact same underlying event and would otherwise near-duplicate each other on almost every candle with opposite direction conventions, which is a meaningfully higher correlation than the Momentum/ORB case and worth a real gate. `scoring_utils.ESTABLISHED_TREND_SCORE_THRESHOLD` (promoted out of Reversal's own former private constant) is the single number both strategies read, so the neutral-band/established-band partition has no gap and no overlap by construction — see D11 for what this does NOT guarantee (the two configs can still drift if retuned independently later).

**Cadence — fires on a genuine control transition, via `_VWAPState.last_fired_zone`.** Checked directly against `level_touch_tracking.py`'s own classification rule (`"rejected" if current_zone == entered_from else "conquered"`): two consecutive `conquered` resolutions in the engine's own unbroken stream always alternate zones by construction, so raw same-zone-twice-in-a-row can't happen there. The real, reachable repeat case is more specific: VWAP only sees the conquests that land in the neutral band, so it never observes the conquests that happen while trend is established (Reversal's window) — the zone can drift back to a value VWAP already fired for while VWAP wasn't watching. `last_fired_zone` catches exactly that; `test_vwap_strategy.py` proves both directions — the repeat is suppressed, and a genuinely new zone immediately afterward still fires.

**SCORE uses `distance_pct`, not `touch_count_today`.** `get_snapshot()`'s `distance_pct`, on a resolved zone, was confirmed (by reading `level_interaction_engine.py` directly) to be computed from `_latest_close`/`_latest_level_value` — candle-derived, not `seconds_in_zone`'s wall-clock `datetime.now()` — so it's backtest-safe per §7's invariant. `touch_count_today` is logged for future calibration but not weighted: unlike Reversal, where more prior touches before a break is straightforwardly stronger evidence, the sign of that relationship for a VWAP conquest isn't obvious (could mean a well-tested level finally giving way, or an already-choppy session) — flagged rather than guessed.

### `structural_target` — a schema constraint both strategies hit the same way

The review's recommendation for both strategies was "don't force a strategy-specific projection; Trade Planning decides target/risk/sizing downstream." `Opportunity.structural_target: float` is currently REQUIRED (not `Optional`) on `base_strategy.py` — changing that schema would be a bigger, cross-cutting change touching all 5 other built strategies, out of scope for this build. Reconciled by using the same mechanical R-multiple (`close ± target_r_multiple * risk`, default 2.0) every other v1 strategy already computes — a schema-completeness value, not a claim that the number is meaningful trading advice.

### The build itself

`momentum_strategy.py` and `vwap_strategy.py`, both following `orb_strategy.py`'s GATE → MATCH → SCORE → PROPOSE shape exactly. `scoring_utils.py` gained `ESTABLISHED_TREND_SCORE_THRESHOLD` and `trend_established_side()` (D11); `reversal_strategy.py` refactored onto both — behavior unchanged, its own `match_direction()` now delegates to the shared helper rather than repeating the same `>=`/`<=` classification a second file also needed verbatim. One stale comment in `orb_strategy.py` corrected in the same change — it cited the discarded draft's momentum threshold value (decision #99) as precedent for a number this rebuild deliberately doesn't reuse.

**Verification — against a real local Postgres, not DB-free-only.** Unlike most entries in this log, this session provisioned PostgreSQL 16 directly and ran `alembic upgrade head`, so every DB-gated test in this delivery actually ran rather than being skipped — including the two most logically intricate VWAP scenarios (the same-zone-repeat suppression across an established-trend window, the day-rollover reset) that would otherwise only be trace-verified by hand. `backend/tests/test_momentum_strategy.py` (new, 18 tests, no DB dependency), `backend/tests/test_vwap_strategy.py` (new, 19 tests, DB-gated, all run and passed), `backend/tests/test_scoring_utils.py` (+6, now 17), `backend/tests/test_reversal_strategy.py` (all 11 re-run unchanged, confirming the refactor is behavior-neutral). Full suite: 546 passed; 2 pre-existing failures (`test_feature_engine.py::test_vwap_publishes_even_while_sma_is_still_warming_up`, `test_intelligence_routes.py::test_daily_levels_carry_level_interaction_once_touched`) confirmed identical against a completely untouched second clone of `main` — pre-existing, unrelated to this change, not investigated further here.

**Stage 2 (decision #112/D10) is now unblocked** — see §12.
