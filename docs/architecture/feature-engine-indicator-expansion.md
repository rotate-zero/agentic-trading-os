# Feature Engine — Indicator Expansion (ATR, Session Change, Gap, Regression, KAMA)
**Status:** Stage 0 confirmed (`confirmed-decisions.md` #67); D1/D2/D3 resolved (`confirmed-decisions.md` #68); Stage 1 (Session % Change + Gap) and Stage 2 (ATR) both complete (`confirmed-decisions.md` #68, #69). Stage 3 (Regression) is next.
**Owner:** Saqib
**Companion documents:** [`system-design.md`](./system-design.md) (§4.5 Feature Engine — the module this plan extends), [`daily-levels-design.md`](./daily-levels-design.md) (precedent this plan borrows its staging/open-decisions format from, and the module whose daily-candle fetch this plan reuses — see §5), [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) (decisions #45–#59, #63 — the Feature Engine foundation this plan builds on; #67 — this plan's own direction lock), [`../decisions/future-ideas.md`](../decisions/future-ideas.md) (regression/KAMA acceleration — parked here, §8), [`../roadmap/phase-roadmap.md`](../roadmap/phase-roadmap.md) (Phase 4 status).

**Why this doc exists:** five genuinely different indicator families in one request, several with real open design questions (config shape, normalization reference, KAMA parameter completeness, data-source reuse) that needed resolving before code — same reason `daily-levels-design.md` exists rather than starting from a diff.

---

## 0. Scope

Five feature families, all additive keys on the existing `FeatureSet.features` dict (§10.2 of `system-design.md`: additive optional fields don't bump `FeaturesUpdated`'s version):

1. **ATR(1D, 14)** + ATR%
2. **Session % Change** + Session $ Change
3. **Gap %** + Gap $
4. **Linear Regression** (value, slope, normalized slope, deviation, R²) — configurable timeframe/period, starting `1m/9` + `5m/9`
5. **KAMA** (value, slope, normalized slope, $ distance, % distance, Efficiency Ratio) — configurable timeframe/`(er_period, fast_period, slow_period)`, starting `1m/9` + `5m/9`

**Explicitly out of scope for this pass** (Saqib's own calls, not assumed):
- No chart/frontend work — `historical.py`, `GET /intelligence/series`, and any config-panel UI stay untouched. These land as `FeatureSet` payload data only.
- No slope-change/acceleration output — needs Feature Engine to retain last-computed-slope state per `(symbol, timeframe, period)`, a real addition on top of an already-multi-part change. Parked in `future-ideas.md`.
- No new dependency (numpy/scipy) — regression OLS and R² are closed-form on a 9–21-point window; plain Python is enough.

---

## 1. ATR(1D, 14) + ATR%

**Definition — Wilder's classic ATR**, computed from the last `period` **complete** daily bars, strictly before today:

```
TR_i = max(high_i - low_i, |high_i - prev_close_i|, |low_i - prev_close_i|)
ATR_seed = mean(TR_1 .. TR_period)                       # simple average over the first `period` TRs
ATR_i = (ATR_{i-1} * (period - 1) + TR_i) / period        # Wilder smoothing thereafter
```

Needs `period + 1` daily candles (each `TR_i` needs the prior day's close), all strictly before today.

**Look-ahead policy — reuses the exact rule already established for PDH/PDL/PDC** (`_update_previous_day`, decision #56): only daily bars from a trading day strictly before today ever contribute. Today's still-forming daily bar never enters the calculation, and ATR is recomputed once per `(symbol, ET day)`, not on every candle close — it does not grow or shrink intraday. This directly resolves the "no accidental look-ahead" requirement from Saqib's brief by following precedent rather than inventing a new policy.

**ATR% denominator — resolved (D2) as the frozen completed daily-bar close, not the live intraday close.** ATR% = `ATR / last_complete_daily_close * 100`, where `last_complete_daily_close` is the close of the most recent complete prior daily bar — the same close value that feeds the final `TR` in the ATR calculation itself (equivalently, `pdc`). Recomputed once per `(symbol, ET day)` alongside ATR, and frozen for the rest of the day, same cadence as ATR itself — deliberately not `close`-reactive the way Session % Change is. Keeps ATR and ATR% moving together as one stable daily reference pair rather than one static number (ATR) paired with one intraday-reactive one (ATR% against live close), which was the alternative considered and rejected.

**Undefined case:** fewer than `period + 1` complete prior daily bars available (fresh symbol/deployment) → omit `atr_14`/`atr_14_pct` from `features` entirely, same "empty means not-yet, not zero" convention `_update_previous_day` already uses.

**Data source — resolved (D1) and implemented (Stage 2, decision #69): reuses Daily Levels' existing daily-candle fetch via a shared cache, instead of a second provider call.** Daily Levels (decision #59/#60) already fetches up to `daily_levels_lookback_days` (180) days of full-OHLCV `1d` candles per symbol via `broker_registry.get_historical_provider()`, refreshed once per `(symbol, ET day)`. ATR(14) only needs the most recent 15 of those. Rather than couple ATR directly to Daily Levels' private cache field, `self._daily_levels_state[symbol]["candles"]` was extracted out into its own shared `self._daily_candle_cache[symbol]`, populated once per `(symbol, ET day)` in the exact same place the old nested key used to be written, with Daily Levels and ATR both reading from it — a real, scoped touch to existing Daily Levels code (same engine, same daily-refresh gate, factored so two features share one fetch instead of duplicating it), confirmed by Saqib directly rather than assumed, and proven directly (not just architecturally argued) by `test_atr_reuses_daily_levels_shared_fetch_without_a_second_provider_call`'s `fake.call_count == 1` assertion.

---

## 2. Session % Change + Session $ Change

**Definition:** `close - pdc`, where `pdc` is the previous regular-session close Feature Engine already computes (decision #56). Continuously updates through pre-market, regular session, and after-hours — it's just "current price vs. yesterday's close," recomputed on every candle close, same as VWAP/PDC/PMH's "extra features" merge.

```
session_pct_change = (close - pdc) / pdc * 100
session_dollar_change = close - pdc
```

**No new state needed.** Both are pure functions of `close` (already in hand) and `pdc` (already computed by `_update_previous_day`) — cheapest addition of the five families, folded straight into the existing `extra` dict alongside `vwap`/`pdc`/`pmh`/`pml` in `_compute_one`.

**Undefined case:** `pdc` itself unavailable (no prior trading day in the lookback window yet) → omit both keys, same gap `pdc` itself already represents honestly.

---

## 3. Gap % + Gap $

**Definition:** traditional opening gap — `regular_open - pdc` — established once at the regular-session open and **frozen for the rest of the day**, deliberately distinct from Session % Change's continuous drift (Saqib's own explicit call in the brief).

```
gap_pct = (regular_open - pdc) / pdc * 100
gap_dollars = regular_open - pdc
```

**Capture mechanism — same "established once, frozen" shape as pre-market H/L's freeze (decision #56), triggered at the opposite end of the session.** `pmh`/`pml` accumulate through `PRE_MARKET` and freeze once regular session starts; Gap captures a single value on the **first** `1m` candle where `MarketClock.current_session(candle_ts)` transitions into `OPEN` for `today`, and simply never updates again that day. Reset once per `(symbol, ET day)`, same trigger shape as every other daily-reset state in this engine.

**New requirement: `open` needs to be read.** `_compute_one` currently extracts `high`/`low`/`close`/`volume` from the candle payload but not `open`, even though `CandleClosed`/`Candle` already carries it. Small addition, called out because it's easy to miss.

**Undefined case:** either `regular_open` (today's open hasn't happened yet — pre-market `FeatureSet`s) or `pdc` (no prior day yet) missing → omit both keys. A `FeatureSet` published during pre-market will have Session % Change defined (it only needs `pdc`) but Gap undefined (it also needs `regular_open`) — an intentional asymmetry, not an inconsistency, worth documenting so it isn't mistaken for a bug later.

---

## 4. Config model — the real conceptual conflict

**Existing shape doesn't fit.** SMA/EMA today are configured as one flat `list[int]` of periods (`feature_engine_sma_periods = [9, 20, 50]`), applied *uniformly* to every timeframe this engine computes (1m/5m/15m/1h, via `_AGGREGATED_WIDTHS` fan-out) — i.e., `sma_9` gets computed on all four timeframes from that timeframe's own window, with no per-timeframe period variation.

Regression and KAMA need genuinely different flexibility: independent `(timeframe, period)` pairs, where the timeframe list itself is indicator-specific (both start scoped to `1m` + `5m` only — no `15m`/`1h`). **Resolved:** a new config shape, one list of dicts per indicator family, which becomes the single source of truth for *both* which periods *and* which timeframes:

```python
feature_engine_regression_configs: list[dict] = [
    {"timeframe": "1m", "period": 9},
    {"timeframe": "5m", "period": 9},
]

feature_engine_kama_configs: list[dict] = [
    {"timeframe": "1m", "er_period": 9, "fast_period": 2, "slow_period": 30},
    {"timeframe": "5m", "er_period": 9, "fast_period": 2, "slow_period": 30},
]

feature_engine_kama_seed_multiplier: int = 5   # mirrors feature_engine_ema_seed_multiplier's role,
                                                 # applied to slow_period the same way EMA applies it to period
```

Adding `{"timeframe": "1m", "period": 21}` or `{"timeframe": "5m", "period": 13}` later is a config change, not a code change — matches Saqib's explicit "do not hard-code only these two combinations" requirement.

**KAMA parameter completeness — resolved per Saqib's own flag in the brief.** `er_period` alone doesn't define classic Kaufman KAMA; `fast_period`/`slow_period` (conventionally 2/30) are explicit config fields, not hardcoded constants, converted internally to smoothing constants:

```
fastSC = 2 / (fast_period + 1)
slowSC = 2 / (slow_period + 1)
```

---

## 5. Linear Regression — formulas

Computed from the trailing `period` closes already sitting in the existing per-`(symbol, timeframe)` rolling window (`self._windows`) — no new state, just a bigger `_window_capacity` (see §7) and a read of a different trailing slice, the same way `sma()`/`ema()` already each slice their own required length from one shared window.

**OLS fit** against equally-spaced bar index `x = 0 .. period-1`:

```
slope, intercept = ordinary_least_squares(x, closes)
regression_value = slope * (period - 1) + intercept          # fitted value at the most recent bar
deviation = close - regression_value                          # $ distance, current price vs. the fit
r2 = 1 - (sum of squared residuals) / (sum of squared deviations from mean(closes))
```

**Normalization — resolved: local intraday volatility, not the daily ATR family.** Slope is $/bar; normalizing it against ATR(1D,14) would need a cross-timeframe unit conversion (a 1-minute slope against a whole-day range) that was never cleanly defined in the original brief. Using the **standard deviation of the same window's closes** instead keeps the normalization self-contained — no cross-feature dependency, no unit-conversion assumption, and it naturally scales for both a low-priced/low-volatility stock and a high-priced/high-volatility one, which is exactly the stated goal ("not directly comparable between a $20 stock and a $500 stock").

```
regression_slope_norm = slope / stdev(closes_in_window)
```

**Undefined cases:** fewer than `period` closes in the window yet → omit all regression keys for that `(symbol, timeframe, period)`. `stdev(closes_in_window) == 0` (perfectly flat window) → omit `_slope_norm` specifically, but still publish `value`/`slope`/`deviation`/`r2` (slope itself is well-defined at exactly 0 in a flat window; only the normalization's denominator is degenerate).

**Naming** (mirrors `sma_{period}` — no timeframe embedded in the key, since the key is already scoped by which `FeatureSet`/timeframe it's published under):

```
regression_9_value
regression_9_slope
regression_9_slope_norm
regression_9_deviation
regression_9_r2
```

---

## 6. KAMA — formulas

**Efficiency Ratio**, over `er_period` bars:

```
ER = |close_t - close_{t-n}| / sum(|close_i - close_{i-1}|  for i in (t-n+1) .. t)      # n = er_period
```

**Smoothing constant and recursive value:**

```
SC_t = (ER_t * (fastSC - slowSC) + slowSC) ** 2
KAMA_t = KAMA_{t-1} + SC_t * (close_t - KAMA_{t-1})
```

**Warm-up — same "no exact finite-window recursion" problem `ema()` already solved, arguably worse here.** KAMA's smoothing constant self-adjusts on ER, so convergence quality after a fixed number of warm-up bars isn't as predictable as EMA's fixed geometric decay. Resolved the same way EMA was: seed `KAMA` with `SMA(er_period)` at the start of the window, then recurse forward through the window's full length, discarding the burn-in — governed by `feature_engine_kama_seed_multiplier` (§4) applied to `slow_period`, the parameter that drives the longest memory in the recursion (not `er_period`, which is comparatively short).

**Slope — resolved (D3) as a 1-bar delta.** Regression's slope is an explicit OLS fit over the whole window; KAMA has no equivalent "fit," so the natural analog is the simple rate of change of the KAMA value itself:

```
kama_slope = KAMA_t - KAMA_{t-1}
kama_slope_norm = kama_slope / stdev(closes_in_window)          # same local-volatility normalization as regression
```

**Distance — both dollar and percentage, per the original brief's two separate bullets** (Saqib's approval message said "price distance" as shorthand; the fuller original spec explicitly asked for both forms, honored here):

```
kama_dist = close - kama_value
kama_dist_pct = (close - kama_value) / kama_value * 100
```

**Undefined cases:** insufficient window depth for the seeded recursion → omit all `kama_*` keys for that `(symbol, timeframe, config)`. `ER` denominator (`sum of absolute bar-to-bar changes`) `== 0` (perfectly flat window) → `ER` itself is genuinely `0/0`; omit `kama_{er_period}_er` specifically for that candle rather than fabricating `0` or `1` — an honest gap, not an error, same convention as everywhere else in this engine.

**Naming:**

```
kama_9
kama_9_slope
kama_9_slope_norm
kama_9_dist
kama_9_dist_pct
kama_9_er
```

(Keyed by `er_period` in the name, matching `regression_{period}` — `fast_period`/`slow_period` don't need to appear in the key since a given `(timeframe, er_period)` combination is expected to carry one fixed `fast_period`/`slow_period` pair per the config list, not multiple.)

---

## 7. Window capacity — a shared extension, not a new cache

`self._window_capacity` today is `max(sma_max_period, ema_max_period * ema_seed_multiplier)`. It needs to grow to also cover regression's max configured `period` and KAMA's `slow_period * kama_seed_multiplier` (the longer of `er_period` and the seeded-recursion requirement) — one shared deque per `(symbol, timeframe)` still backs all four indicator families, each slicing the trailing length it actually needs, same pattern SMA/EMA already establish. No new per-indicator window/cache.

**A real new constraint this introduces:** regression and KAMA are configured for `1m`/`5m` only, not `15m`/`1h` — but the shared window exists per `(symbol, timeframe)` for whichever timeframes actually fire. `_apply_close`/`_compute_aggregated` need one added check per indicator family ("does this `(indicator, timeframe)` combination appear in the configured list?") before computing regression/KAMA features for a given close — a small, explicit branch, not a parallel code path. SMA/EMA never needed this check because they apply uniformly to every timeframe that fires; regression/KAMA are the first indicator families where that isn't true.

---

## 8. Deferred — recorded here so it isn't re-argued from scratch later

- **Slope-change / acceleration** (regression and/or KAMA) — genuinely useful per Saqib's own brief, but needs Feature Engine to retain the *previous* computed slope per `(symbol, timeframe, period)`, a new piece of state beyond "recompute from window" that every other indicator here uses. Parked in `future-ideas.md`, same treatment KAMA signal-line systems/VIDYA/ALMA already got in the original brief ("future experiments," not this pass).
- **Regression channels / additional regression-derived signals** — explicitly out of scope per the original brief; not reconsidered here.
- **Chart rendering for any of these five families** — explicitly out of scope for this pass (§0). Worth noting for later: regression and KAMA are structurally SMA/EMA-shaped (naturally chart-able as continuous overlay lines via `historical.py`); ATR/Session-change/Gap are structurally PDH/VWAP-shaped (single scalars, `GET /intelligence/state` only, no `historical.py` involvement) — this shape difference should inform whichever gets picked up first if/when chart work resumes.

---

## 9. Open decisions — all resolved

| # | Decision needed | Where it blocks | Status |
|---|---|---|---|
| D1 | Whether ATR reuses Daily Levels' cached daily-candle fetch (shared `self._daily_candle_cache`) vs. its own fully independent fetch | Stage 2 | **Resolved (decision #68):** shared cache. |
| D2 | ATR% denominator — live close vs. the daily bar's own close, frozen like ATR itself | Stage 2 | **Resolved (decision #68):** frozen completed daily-bar close. |
| D3 | KAMA slope lookback — 1-bar delta vs. `er_period`-bar delta | Stage 4 | **Resolved (decision #68):** 1-bar delta. |

Nothing remains open — config shape, normalization reference, KAMA parameter completeness, naming convention, look-ahead policy, and `None`-handling were resolved in the original Stage 0 lock; D1–D3 above resolved by Saqib directly ahead of Stage 1.

---

## 10. Guiding constraints (carried from `daily-levels-design.md` / standing project principles — not new rules)

- **Approximations and judgment calls surfaced, never hidden** — §1's ATR% denominator, §6's KAMA slope lookback, and §1's data-source reuse are flagged explicitly rather than silently decided.
- **Honest `None`/omitted keys, never a fabricated value** — every undefined case above is spelled out per feature family, same "empty means not-yet, not zero" convention this engine already uses throughout.
- **No accidental look-ahead** — every new feature is computed strictly from already-closed bars; ATR/Gap additionally exclude the current in-progress period (today's daily bar / today's regular open before it happens) by construction, not by a runtime check that could be bypassed.
- **Compute once, consume everywhere** — §1's shared daily-candle cache extension exists specifically to avoid a second Daily Levels-shaped fetch.
- **Docs updated in the same change that changes architecture**, not after.
- **Real backend tests against real local Postgres**, no mocks standing in for the architectural claim being tested — same discipline as every prior Feature Engine decision.

---

## 11. Staged plan

- [x] **Stage 0 — Lock the direction in writing (no application code).** This document + `confirmed-decisions.md` #67.
- [x] **Stage 1 — Session % Change + Gap.** Built (`indicators/session_change.py`, `indicators/gap.py`, `FeatureEngine._update_gap`), tested at all three of this suite's tiers (pure math / in-memory / real-Postgres cold-start), 9 new tests passing, full suite at 187 passing. `confirmed-decisions.md` #68.
- [x] **Stage 2 — ATR(1D, 14) + ATR%.** Built (`indicators/atr.py`, `FeatureEngine._update_atr`), D1 implemented as a shared `self._daily_candle_cache` extracted out of Daily Levels' own fetch (zero second provider call — proven directly in `test_atr_reuses_daily_levels_shared_fetch_without_a_second_provider_call`), D2 implemented as the frozen last-daily-bar close. 6 new tests passing, full suite at 196 passing on a clean DB. `confirmed-decisions.md` #69.
- [ ] **Stage 3 — Linear Regression.** New config shape (`feature_engine_regression_configs`), window capacity extension, per-timeframe applicability check in `_apply_close`/`_compute_aggregated`.
- [ ] **Stage 4 — KAMA.** Resolves D3. Reuses Stage 3's config-shape and window-capacity-extension machinery; adds its own seed-multiplier warm-up.

Not a hard ordering requirement — Session/Gap and ATR don't depend on Regression/KAMA's config-shape work or vice versa — but Stage 3's config-shape and window-capacity groundwork is worth having in place before Stage 4, since KAMA reuses both directly rather than inventing its own version.

---

## 12. How to resume this in a new session

1. Read this file's checkboxes for the last completed stage.
2. Read `confirmed-decisions.md`'s most recent entries — if a stage above says a decision needs writing before coding, check it isn't already there before re-deciding it.
3. D1/D2/D3 (§9) are real open questions, not rhetorical — confirm with Saqib before Stage 2 (D1/D2) or Stage 4 (D3) lock in behavior that's hard to change later without a silent semantic shift in already-published feature keys.
4. Update this file's checkboxes as part of the same change that completes a stage.
