# Daily Levels — Design & Implementation Plan
**Status:** Stage 0 confirmed (`confirmed-decisions.md` #59). Concept locked across a three-way review (Saqib + Claude + Grok + ChatGPT, in that order) over several rounds. **No application code has been written yet** — this document and decision #59 are the direction lock, matching decision #50's own precedent for the chart migration.
**Owner:** Saqib
**Companion documents:** [`system-design.md`](./system-design.md) (§4.5 Feature Engine — the module this plan extends; §4.8 Level Interaction Engine — the module this plan reuses, mostly unmodified), [`feature-engine-chart-migration.md`](./feature-engine-chart-migration.md) (the precedent this plan borrows its staging/open-decisions-table format from), [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) (decisions #45–#58 — the Feature Engine / Level Interaction Engine foundation this plan builds on; #59 — this plan's own direction lock), [`../decisions/future-ideas.md`](../decisions/future-ideas.md), [`../roadmap/phase-roadmap.md`](../roadmap/phase-roadmap.md) (Phase 4 status).

**Why this doc exists:** same reason `feature-engine-chart-migration.md` exists — this is too large for one sitting, and it's a genuinely new shape of indicator for this codebase (a variable-length collection with cross-day identity, not a scalar). If a session ends mid-build, the next session should read this doc plus the latest `confirmed-decisions.md` entries before touching anything, rather than re-deriving the concept from a diff.

---

## 0. The concept this plan implements

**Detect meaningful daily support/resistance zones by clustering the open and close prices of up to N days of 1D candles, then hand the resulting levels to the existing, unmodified Level Interaction Engine for touch/reject/conquer tracking — exactly like every other level type it already tracks.**

Two concerns, kept deliberately separate (explicit, from the three-way review, and worth stating as a standing rule for this feature, not just a starting note):
1. **Level calculation** — "where are the historically important daily price zones?" A pure function of price history. Never influenced by what price later does at those zones.
2. **Level interaction** — "what does price do when it reaches them?" Entirely the existing `LevelInteractionEngine` machinery (decision #46), extended (§5 below) to read from a new data shape, but with its own state-machine logic untouched.

Keeping these separate is what let PDH/PDL/Camarilla/VPOC (decisions #56–57) get interaction tracking for free — Daily Levels is the first case where the *calculation* side is genuinely novel (a clustering algorithm, a variable count of outputs, cross-day identity), so it's worth being explicit that the *interaction* side still isn't.

---

## 1. Clustering algorithm (resolved, corrected during design review)

**Input:** up to `daily_levels_lookback_days` 1D candles for a symbol (§2). Every candle contributes two points: its open and its close.

**A real bug was caught before this got built, not after.** Grok's original proposal tested each new candidate point against the cluster's *running* average as it existed before that point was added. Run the worked example (points 100.10, 100.20, 100.40; 0.2% tolerance) through that rule literally: 100.10+100.20 join fine (avg 100.15), but 100.40 is 0.25 away from 100.15, and 0.2% of 100.15 is only ≈0.2003 — it fails the test. That produces a 2-point level and a discarded singleton, not the 3-point, ~100.23 level everyone actually wanted. Grok's own "why this works" walkthrough asserted 100.40 passes that check; it doesn't. Caught by re-deriving the arithmetic directly rather than trusting the walkthrough — same "verify forwarded AI claims against ground truth" practice as decision #48.

**Corrected rule — validated against the whole tentative cluster, not the stale average:**

```
sort all points ascending
while unused points remain:
    seed = lowest unused point
    cluster = [seed]
    for each remaining unused point p, in ascending order:
        tentative = cluster + [p]
        new_avg = mean(tentative)
        if EVERY member of tentative is within cluster_pct of new_avg:
            cluster = tentative      # p accepted, consumed
        else:
            break                    # p rejected — becomes next seed candidate, not discarded outright
    validate(cluster)                # §1.1
    mark all members of cluster as used
```

Re-run the worked example with this rule: {100.10, 100.20} → avg 100.15, both within tolerance → accept. Add 100.40 → new avg 100.2333; check all three against it (0.133, 0.033, 0.167 away) — all under the ≈0.2005 threshold → accept. Final: 3 points, avg ≈ 100.23, strength 3. Matches intent.

A rejected candidate is never simply thrown away — it becomes the seed of the *next* cluster attempt against every remaining point, so it gets a fair, full shot at pairing with something else later in the sorted sequence.

### 1.1 Cluster validity — same-candle rule

A candle contributes an open point *and* a close point. Without a guard, a single small-range or doji candle could cluster its own open and close together and produce a "level" with strength 2 from **one candle** — contradicting "minimum 2 1D candles." Resolved (per ChatGPT's proposal, which Saqib confirmed):

- Each cluster tracks `distinct_candle_count` alongside `strength` (point count).
- **Validity gate: `distinct_candle_count >= 2`.** Strength stays the total point count regardless — a candle's open *and* close can both contribute to a cluster's strength once at least one other candle is also in it (e.g. candle A's open+close plus candle B's open → strength 3, distinct_candle_count 2 → valid).
- A cluster that fails the gate (2 points, 1 candle) is discarded, same as a true singleton.

### 1.2 Resolved: no relaxation/bias mechanism for points that fail to cluster

Raised directly by Saqib: what about a point that's rejected from one cluster and then never finds a partner at all? **Decision: don't add a bias or relaxed-tolerance retry.** The algorithm above already gives every point a complete, fair evaluation — as a join candidate for the cluster ahead of it in sorted order, and as the seed of its own cluster attempt against everything after it. A point that survives both passes with no partner genuinely has no confirming price point anywhere in the lookback window, which is the correct signal to discard it, not a gap to patch. A forced-inclusion bias would (a) pull a cluster's average toward a price nobody actually revisited within tolerance, diluting what "strength" and the eventual touch-aura mean, and (b) introduce an unprincipled magnitude/direction parameter with no natural stopping rule. If too few or too many levels are forming in practice, the two real, visible levers are:
- `daily_levels_cluster_pct` — widen to catch more confluence, narrow to be stricter.
- `daily_levels_lookback_days` — more history gives an isolated point more chances to be confirmed by a later session.
Both are config, not hardcoded, and both are cheap to tune without touching the algorithm itself.

### 1.3 Config (defaults, all tunable)

| Setting | Default | Notes |
|---|---|---|
| `daily_levels_lookback_days` | 180 | Saqib's own fallback: drop to 90 if Stage 1's empirical Polygon check (§2) shows depth/rate-limit problems at 180. |
| `daily_levels_cluster_pct` | 0.2% | The clustering aura from the original spec. |
| `daily_levels_min_distinct_candles` | 2 | §1.1's validity gate. |

---

## 2. Data source — behind a clean interface, not hard-wired

**Resolved (per ChatGPT's proposal, endorsed by Saqib): approve the external-provider dependency, but isolate it.**

```
DailyLevelCalculator
        ↓  (asks for N days of 1D candles for a symbol)
DailyHistoryProvider   ← new interface
        ↓
PolygonAdapter (first implementation)
```

**Why this is a real precedent break, not a detail — confirmed directly against the code, not assumed:** `market.py`'s `_MINUTES_PER_UNIT` routing shows `"1d"` always goes to Polygon's real EOD bars, never self-recorded or `candle_aggregator`-derived (decision #44). Every Feature Engine indicator built so far (decisions #45, #51–53, #56–57) sources its history exclusively through `candle_store`/`candle_aggregator` — local, self-recorded data. Feature Engine's live engine has never once reached an external provider directly. Daily Levels will be the first one that needs to, because waiting for 180 self-recorded trading days would make the feature useless for the better part of a year.

**`DailyHistoryProvider` is the abstraction that keeps this from leaking into the clustering algorithm itself** — `DailyLevelCalculator` never imports or knows about Polygon. Today's implementation wraps `PolygonAdapter`; when IBKR's historical role is verified (still pending — see confirmed-decisions.md's standing open items), it can supply the same 180 daily candles without the calculator changing at all.

**Caching, not a fetch on every recompute:** 180 daily candles don't change intraday — cache per `(symbol, trading_day)`, refreshed once when a new 1D bar closes (once per ET calendar day), not re-fetched on every Feature Engine tick the way `_update_previous_day()`-style state currently isn't either. Daily Levels needs its **own** once-per-day trigger, distinct from `_update_previous_day()` — that function only ever fetches *one* prior day's already-recorded 1m rows; this needs up to 180 days of genuine 1D bars from the provider layer.

**Not yet done, flagged explicitly as a Stage 1 prerequisite, not assumed away:** an empirical check of what Polygon/Massive's free tier actually returns for a 180-bar daily request — actual depth available, pagination behavior, rate limits — against the real key, same standing practice as decisions #32/#39. The 180 vs. 90 default (§1.3) should be set from that result, not guessed.

---

## 3. Schema — a new field, not a hack on the flat dict

**Resolved (per ChatGPT's proposal, endorsed by Saqib): don't force this into `FeatureSet.features: dict[str, float]`.**

Every existing indicator (decision #50) is a single scalar per key. Daily Levels is a variable-length list of `(price, strength)` pairs, 0 to 15+ entries, reshaping day to day. Encoding it as `daily_level_1`/`daily_level_1_strength`/... into the flat dict would work mechanically but would leak strength values into `LevelInteractionEngine`'s generic key-iteration (§5) as if they were levels themselves.

```
FeatureSet
├── features: dict[str, float]        # unchanged — sma_20, pdh, cam_r1, ...
└── daily_levels: list[DailyLevel]    # new field
      ├── level_id: str               # e.g. "DL-00123" — the persistent identity (§4)
      ├── price: float                # this cluster's current average
      ├── strength: int               # total contributing points
      └── distinct_candle_count: int  # §1.1's validity metadata, kept for transparency/debugging
```

**Scope: symbol-keyed, not `(symbol, timeframe)`-keyed** — same reasoning as VWAP (decision #53) and PDH/PDL/Camarilla/VPOC (decision #56–57): these levels don't vary by chart timeframe, so the same `daily_levels` list is attached to whichever `FeatureSet` a given close produces, across `1m`/`5m`/`15m`/`1h` uniformly.

This is a genuine, acknowledged exception to decision #50's "flat dict" rule — the right kind, per Grok's forward-looking note: a collection-valued feature is a different data type from a scalar one, and future collection-shaped features (volume profile levels, liquidity zones, pivot clusters) would reasonably follow this same `list[...]` pattern rather than each inventing its own hack.

---

## 4. Identity — persistent `level_id`, proximity-reconciled, never rank-based

**Resolved (per ChatGPT's proposal, explicitly endorsed by Saqib over rank-based keying): the calculated price is not the identity.**

Rank-based keys (`daily_level_1` = strongest today, `daily_level_2` = next, ...) were rejected: if the strongest zone's *rank* changes day to day — a new point promotes a formerly-weaker cluster, or an aging-out point demotes the current strongest — the same key would silently point at a physically different price zone, and `LevelInteractionEngine`'s touch history for that key would look continuous when it isn't. Confirmed against the code: the engine's whole state model (`level_interaction_state`, decision #46) assumes a `level_key` means the same thing every time it's seen again.

Instead: every level gets a durable, opaque `level_id` (`DL-00001`, ...) minted once, whose *price* evolves day to day while its identity doesn't:

```
DL-00123
    2026-08-17 → 100.20
    2026-08-18 → 100.24
    2026-08-19 → 100.27
```

**Day-over-day reconciliation, run once per new 1D bar close (same cadence as §2's recompute):**
1. Recompute today's clusters fresh from the rolled-forward 180-day window (§1) — level calculation never looks at yesterday's identities while doing this.
2. Match each of today's clusters to yesterday's surviving levels by nearest price, within `daily_levels_identity_match_pct` (default: reuse `daily_levels_cluster_pct`, 0.2%, as a starting point — kept as its own separate setting since day-over-day drift and within-cluster spread are conceptually different questions that happen to share a default, not proven to need the same value; revisit once real drift is observed).
3. A matched level carries its `level_id` forward with the new price/strength. An unmatched survivor from yesterday is **archived**, not deleted — its `level_id` and price history stay queryable, it just stops appearing in the live `daily_levels` list. An unmatched new cluster mints a fresh `level_id`.

**New persistence needed (not built in this pass):** a `daily_levels_state` table — `level_id`, `symbol`, current `price`/`strength`, and enough history to support the reconciliation matching and archived-level lookups. Distinct from `level_interaction_state` (decision #46), which will separately key its own rows by this same `level_id` once §5 is built — the two tables answer different questions (`daily_levels_state`: "what is this zone and where is it," `level_interaction_state`: "what has price done there").

---

## 5. Level Interaction Engine — a real, scoped extension (not zero-cost, for the first time)

Decision #46's `LevelInteractionEngine` has been genuinely generic since it was built — it walks every key in `FeatureSet.features` with zero awareness of what the key means, and decision #56 proved directly that new level types (`pdh`, `cam_pp`, ...) get tracked automatically, no code change. Daily Levels breaks that streak, on purpose: `daily_levels` is a separate field with a different shape (a list of objects, not dict entries), so the engine needs a small, explicit addition to also walk that list — keyed by each entry's `level_id`, valued by its `price` — alongside its existing `features.items()` loop. Everything else about the engine (aura, touch/holding/rejected/conquered, gap-through and cold-start-unknown-origin handling, daily reset) is reused completely unmodified; only the *iteration surface* grows.

This is the first time this engine's "zero code changes for a new level type" property doesn't hold, and it's worth recording as a deliberate, acknowledged exception rather than something that happens quietly in a diff.

---

## 6. Frontend — explicitly out of scope for the first pass (open decision)

**Checked directly, not assumed:** `frontend/src/indicators/` has no clustering/daily-level equivalent at all — no `dailyLevels.ts`. Every other Feature Engine indicator (SMA, EMA, VWAP, PDH/PDL/PDC, Camarilla, premarket, VPOC) ported an *existing* frontend implementation, verified the backend against it, and got the "(local)" fallback pattern (decision #54/#58) essentially for free because there was something to fall back to. Daily Levels has neither.

**Recommendation, not yet confirmed:** scope the first build to backend calculation + persistence + Level Interaction integration only (Stages 1–3 below), the same way the chart migration itself front-loaded backend work (decisions #51–53, #56–57) well before Stage 1 wired anything into the chart. Chart rendering — plotting a variable-count set of horizontal levels, deciding how strength affects line weight/opacity, deciding what happens when there are "too many" levels on screen (Saqib's own stated plan: filter/toggle in the UI once it's visibly a problem, not solve it algorithmically now) — becomes its own later stage, explicitly scoped rather than folded in as an afterthought.

---

## 7. Open decisions — still genuinely open

| # | Decision needed | Where it blocks | Status |
|---|---|---|---|
| D1 | `daily_levels_lookback_days` final default (180 vs. 90) | Stage 1 | Depends on the empirical Polygon depth/rate-limit check (§2) — not yet run. |
| D2 | `daily_levels_identity_match_pct` final value | Stage 2 | Starting default = `daily_levels_cluster_pct` (0.2%); revisit once real day-over-day drift is observed against live data. |
| D3 | Whether Stage 4 (frontend rendering) is in scope for this feature's first delivery, or a fully separate later piece of work | Stage 4 | **Resolved (decision #61):** yes, in scope, and built ahead of Stages 2/3 — see Stage 4's own checkbox below for why that ordering was fine. |

Everything else (clustering rule, same-candle validity, no-bias decision, data-source layering, schema shape, identity approach) is resolved above.

---

## 8. Guiding constraints (carried from `feature-engine-chart-migration.md` and standing project principles — not new rules)

- **Level calculation and level interaction stay separate** (§0) — the interaction engine never influences what counts as a level.
- **Approximations surfaced, never hidden** — `distinct_candle_count` stays visible alongside `strength` (§1.1) specifically so the same-candle rule's effect is inspectable, not implicit.
- **Dead code flagged, not deleted** — n/a until Stage 4 gives the frontend something to potentially retire.
- **Docs updated in the same change that changes architecture**, not after.
- **Real backend tests against real local Postgres**, no mocks standing in for the architectural claim being tested.
- **Empirical checks against real provider keys before locking a default** (§2's D1) — the same standing practice as decisions #32/#39/#51.

---

## 9. Staged plan

- [x] **Stage 0 — Lock the direction in writing (no application code).** This document + `confirmed-decisions.md` #59.
- [x] **Stage 1 — Backend calculation.** Confirmed decision #60. `broker_registry.get_historical_provider()` reused as-is for the `DailyHistoryProvider` role (no new interface needed — it already existed); clustering algorithm + same-candle validity gate in `indicators/daily_levels.py`; `daily_levels` field on `FeatureSet`; wired into `engine.py` via a new async `_maybe_refresh_daily_levels`, gated once-per-(symbol, ET day) same as previous-day/premarket. **Mints a fresh `level_id` every day — Stage 2's cross-day reconciliation is still not built**, so don't treat these ids as stable yet. 11 new tests (8 pure clustering, 3 engine-wiring with a fake historical provider) plus the full existing suite: **165/165 passing** against real local Postgres. **D1 (empirical Polygon depth/rate-limit check) is still genuinely outstanding** — no network route to Polygon existed in the environment this was built in; the 180-day default is unverified, same status as when this doc was first written.
- [x] **Stage 2 — Identity & persistence.** Confirmed decision #63. `daily_levels_state` table (migration 0003) — one row per (symbol, level_id), `level_id` derived from the row's own DB identity at first mint (`f"{symbol}-DL-{row.id}"`), never rank-based. Day-over-day reconciliation: greedy nearest-price matching between today's fresh clusters and yesterday's still-`active` rows within `daily_levels_identity_match_pct`, closest pairs claimed first; a matched cluster carries its level_id forward with updated price/strength; an unmatched survivor is archived (`status`/`archived_day` set), not deleted; an unmatched cluster mints a fresh row. Restart-survival: a fresh `FeatureEngine` instance checks the DB for today's already-confirmed levels before ever considering a provider fetch — proven with a real test using a second, "poisoned" fake provider that would return visibly different data if it were ever reached, confirming the short-circuit actually fires rather than coincidentally matching. `get_daily_levels()`'s custom-lookback path (decision #62) deliberately stays OUT of this — ephemeral, `-preview-`-marked ids, not persisted or reconciled, since it's a what-if display control, not the tracked default.
- [x] **Stage 3 — Level Interaction integration.** Confirmed decision #64. `_process_one`'s `features.items()` loop gained one sibling loop over `daily_levels`, calling the SAME `_process_level` unmodified — `level_id` as `level_key`, `price` as `level_value`, exactly the generic contract every dict-based level type already satisfies. `get_snapshot()` needed zero changes at all. Proven exactly as this checkbox originally specified: real tests publishing `daily_levels` and confirming touch/holding/rejected/conquered against a `level_id` the engine was never told about by name, plus a mixed-event test proving a scalar `features` level and a `daily_levels` entry in the SAME `FeaturesUpdated` are tracked independently. `GET /intelligence/state` now also attaches `level_interaction` (keyed by timeframe) to each `daily_levels` entry — the gap decision #61 explicitly left open.
- [x] **Stage 4 — Frontend (scope resolved: yes, built ahead of Stages 2/3).** Confirmed decision #61. D3 resolved by Saqib: backend-first *within* Stage 1, but frontend rendering itself came before Stage 2/3, not after — chart rendering only needed Stage 1's raw price/strength/level_id, not cross-day identity or interaction tracking, so nothing here actually blocked on either. `GET /intelligence/state` now carries a symbol-scoped top-level `daily_levels` array; `ChartWidget.tsx` draws one price line per level via a new dedicated effect (not folded into `horizontalLevels`, since that's a fixed-type instance list and this is a variable-count backend collection). **Strength shown as a text tag on each line's title ("Daily Level · Strength N"), not encoded visually** — Saqib's own call: one uniform color/width for every level (`DailyLevelsConfig`), not a per-level gradient. New `dailyLevelsConfig` block in `SubWindowConfig` (enable toggle, min-strength filter — Saqib's own stated "filter in the UI" plan from §6 — color, width, price-label toggle), with backward-compat hydration for saved layouts. `tsc -b`/`vite build` both clean. **Not yet click-through verified in an actual browser** — same standing gap this project has flagged consistently across every chart-affecting change.

---

## 10. How to resume this in a new session

1. Read this file's checkboxes for the last completed stage.
2. Read `confirmed-decisions.md`'s most recent entries — if a stage above says a decision needs writing before coding, check it isn't already there before re-deciding it.
3. Do not start Stage 2 before Stage 1's empirical Polygon check (D1) has actually run — the lookback default it resolves affects the cache/fetch shape Stage 1 builds.
4. Do not start Stage 3 before Stage 2's `level_id` persistence exists — the interaction engine has nothing stable to key against otherwise.
5. Update this file's checkboxes as part of the same change that completes a stage.
