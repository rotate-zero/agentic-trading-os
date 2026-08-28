# Pre-Market Volume + Extended VWAP — Design

**Status:** DRAFT — not yet confirmed. No application code written. Same pre-build stage as `daily-levels-design.md` and `feature-engine-indicator-expansion.md` before their own direction locks.
**Owner:** Saqib
**Companion documents:** [`system-design.md`](./system-design.md) §4.5 (Feature Engine — the module this plan extends) and §4.3 (`MarketClock`, whose `Session.PRE_MARKET` window — 4:00am–9:30am ET — this plan reads off directly, not a new time boundary invented here); [`feature-engine-indicator-expansion.md`](./feature-engine-indicator-expansion.md) (precedent this plan borrows its staging/open-questions format from); [`scanner-design.md`](./scanner-design.md) §2/§12 (the motivating use case — ORB needs a way to rank symbols on pre-market activity, which today's `rvol`/`ActivityScorer` structurally cannot do; this doc is the Feature Engine side of closing that gap, Scanner-side consumption is future work, §6); `app/feature_engine/engine.py`'s `_update_vwap` (confirmed decision #53) and `_update_premarket` (confirmed decision #56) — the two existing methods this plan's accumulator design directly borrows from, cited by name throughout.

**Why this doc exists:** two problems that turned out to share one root cause and one fix. (1) ORB — the first strategy Saqib intends to build — needs to identify "in play" symbols from pre-market behavior, but `rvol` is structurally regular-session-only (inherited from `_update_vwap`'s own accumulator, decision #53), so it says nothing until well after the opening bell, which is often already past ORB's actual decision window. (2) Our `vwap` diverges from other platforms' VWAP, most visibly early in the regular session — because `vwap` deliberately resets at 9:30am (decision #53) while platforms like ThinkorSwim typically accumulate from 4:00am. Both trace back to the same fact: nothing today accumulates price/volume during `Session.PRE_MARKET`. One new accumulator fixes both.

---

## 0. Scope

**One new thing, consumed two ways:** a single continuous price/volume accumulator spanning `Session.PRE_MARKET` through the end of regular session (4:00am–4:00pm ET), replacing nothing existing. From it:

1. **`vwap_ext`** — a VWAP line that starts accumulating at 4:00am instead of 9:30am. This is what closes the platform-discrepancy gap (§2).
2. **`premarket_volume_ratio`** — read the same accumulator's cumulative volume *during* the pre-market window itself, compared against that symbol's own historical pre-market volume. This is what ORB screening actually needs (§2).

**Explicitly NOT in scope for this pass:**
- **The existing `vwap`/`session_volume` (decision #53) are untouched.** `vwap_ext` is a new, separate key — not a redefinition. Level Interaction Engine, the chart overlay, and anything else already keying off `vwap` keeps working exactly as today. Same additive posture ATR-14/Camarilla/KAMA all used when they were added alongside rather than instead of what existed.
- **Wiring `premarket_volume_ratio` into `ActivityScorer` or a new ORB-specific scan type** (`scanner-design.md`) — real, separate follow-up work once the feature itself exists and has been watched against real pre-market sessions. Building the scan logic before the underlying feature is validated would be designing against a guess.
- **Chart display of `vwap_ext`** — whether/how it shows on the chart (a second line? a toggle replacing `vwap`?) is a real product decision, not resolved here.

---

## 1. Why one accumulator, not two

The naive design is two separate things: a "pre-market-only" accumulator (frozen once regular session starts, like `pmh`/`pml` already are) feeding `premarket_volume_ratio`, and a completely separate "extended" accumulator feeding `vwap_ext`. That's redundant — both would track literally the same running `(cumulative_pv, cumulative_volume)` for the same window, just read at different times.

**One accumulator, read twice:**
- Read its `cumulative_volume` *while* `current_session() == Session.PRE_MARKET` → that's "pre-market volume so far," the input `premarket_volume_ratio` needs.
- Let it keep accumulating (never resetting at 9:30am) → its VWAP derivation throughout the regular session *is* `vwap_ext`.

This generalizes `_update_vwap`'s existing shape in exactly one way: its reset trigger changes from "a new **regular session** has started" (`state["session_start"] != session_start`) to "a new **trading day** has started" (`state["for_day"] != today` — literally `_update_premarket`'s own reset trigger, decision #56, reused rather than reinvented). Its session gate changes from `is_regular_session()` to "session is `PRE_MARKET`, `OPEN`, `LUNCH`, or `POWER_HOUR`" (i.e., not `CLOSED` and not `AFTER_HOURS` — see §5 for why after-hours is excluded by default). Everything else — the restart-safe backfill-from-`candle_store` pattern, `vwap_from_accumulator()` for the actual VWAP math, `typical_price()` for the per-bar contribution — is reused verbatim, not reinvented.

**Proposed home:** a new `_update_vwap_ext` method and a new `self._vwap_ext_state` dict, structurally a close sibling of `_update_vwap` — not folded into `_update_vwap` itself (keeping the existing, working method completely untouched and low-risk to the rest of the system), and not folded into `_update_premarket` either (that method's whole contract is "high/low, frozen at premarket's end" — bolting a continuously-running-past-that-point accumulator onto it would break its own documented freeze behavior for anything reading `pmh`/`pml`).

---

## 2. New `FeatureSet.features` keys

| Key | Meaning | Derived from |
|---|---|---|
| `vwap_ext` | VWAP accumulated from 4:00am, not reset at 9:30am. Directly comparable to most other platforms' default VWAP. | `vwap_from_accumulator()` (existing, reused) on the new accumulator's running totals. |
| `session_volume_ext` | Cumulative volume since 4:00am — the extended-accumulator equivalent of today's `session_volume`. | Same accumulator, published alongside `vwap_ext` exactly the way `_update_vwap` already publishes `session_volume` alongside `vwap`. |
| `premarket_volume_ratio` | How this symbol's pre-market volume-so-far compares to its own historical pre-market volume, time-of-day-normalized. | `session_volume_ext`'s value **while still inside the `PRE_MARKET` window**, run through the *same* `rvol()` pure function already in `indicators/rvol.py` — reused, not reimplemented — against a pre-market-specific baseline (§4) instead of `avg_daily_volume`. |

`premarket_volume_ratio` is honestly absent (not zero, not estimated) outside the pre-market window and before the historical baseline exists for a symbol — same convention every other indicator in this system already follows.

---

## 3. The empirical gate — pre-market historical baseline

`premarket_volume_ratio` needs each symbol's own **average pre-market volume** over some historical lookback (parallel to `rvol`'s `avg_daily_volume`, decision #71). That needs 1-minute historical bars covering the 4:00–9:30am window on prior days — not just a single daily OHLCV bar, which may or may not even separate pre-market volume from the rest of the day depending on the provider's own convention.

**Not verified yet, and this genuinely gates the build, same shape as every other data-source check this project has run before committing:**
- Does Polygon's free tier expose 1-minute bars covering pre-market hours, and does its 5-calls/minute ceiling make pulling this for a real universe (even just today's placeholder 6 symbols, let alone a real Core-100) practical?
- Does Polygon's *daily* bar already fold pre-market volume into one number (making it useless for isolating a pre-market-only baseline), or report it separately?
- IBKR's 1-minute historical (confirmed up to ~1 year, §9 of `scanner-design.md`) almost certainly has this — but IBKR is deliberately deferred until after Trading Intelligence/Performance Intelligence per Saqib's own sequencing call. Worth naming explicitly: **this is now a second, independent reason the IBKR subscription matters**, alongside spread tightness — not a blocker to *today's* accumulator/`vwap_ext` work (that needs no historical baseline at all), but a real blocker to `premarket_volume_ratio` specifically until either Polygon proves capable or IBKR lands.

**Practical staging this suggests:** `vwap_ext`/`session_volume_ext` (§2's first two rows) have zero historical-data dependency — they're pure today's-accumulator reads, buildable and testable right now. `premarket_volume_ratio` (the actual ORB-enabling piece) is real Feature Engine work but is bottlenecked on the empirical check above. Worth doing that check before writing `premarket_volume_ratio`'s code, not after.

---

## 4. Open design questions

1. **Time-normalization within pre-market.** `rvol()`'s existing formula assumes roughly linear volume distribution across its window (`elapsed_minutes / total_session_minutes`). Regular session already stretches that assumption somewhat; pre-market volume is more front/back-loaded (thin in the middle of the night, building toward the open) than regular session's own distribution, so reusing the same linear assumption is a weaker approximation here. Proposed: ship it anyway as the starting point (zero new formula to write or validate, same "reuse and tune from observed behavior" posture the Scanner's own equal weights used) and revisit if real pre-market rankings look obviously wrong once watched — not assumed to be a problem preemptively.
2. **Should the extended accumulator include after-hours (4:00pm–8:00pm), or stop at the close?** Proposed default: stop at regular-session close. After-hours volume is typically thin and can distort a VWAP reference more than help it, and this isn't what motivated either problem this doc solves. Easy to widen later if Saqib wants an `AFTER_HOURS`-inclusive variant too.
3. **Does the chart ever show `vwap_ext`, and does it replace `vwap` as the default overlay or run alongside it?** Not resolved here — a real product/UI decision, not a data question.
4. **`premarket_volume_ratio`'s exact baseline lookback window** (how many prior days, matching `rvol`'s own `feature_engine_rvol_lookback_days` — same config knob, or a separate one?) — depends on what the empirical check in §3 finds is actually available.

---

## 5. Relationship to the Scanner (future work, not this pass)

Once `premarket_volume_ratio` exists and has been watched against a few real pre-market sessions, the natural next step is a genuinely new scan type — "pre-market movers" — sitting alongside (not replacing) the current RVOL-only Core scan, since the two answer different questions at different times of day: `premarket_volume_ratio` for building an ORB watchlist *before* the bell, today's `rvol`-based score for monitoring activity *after* it. Both could plug into the same `ActivityScorer`/`UniverseProvider` shape scanner-design.md already established — this isn't a new Scanner architecture, just a new input once it exists. Deliberately not designed further here; that's real work for once this doc's Feature Engine side is built and validated.

---

## 6. Build status

**Built and verified (real Postgres 16, real event bus, 245/245 backend tests passing — no regressions):** `vwap_ext`/`session_volume_ext`, exactly per §1's single-accumulator design — `_update_vwap_ext` in `engine.py`, a new `self._vwap_ext_state` dict, fully separate from `_vwap_state`. 6 new tests in `tests/test_vwap_ext.py`, including one that directly demonstrates the fix this doc exists for: a pre-market bar at 50 followed by a regular-open bar at 100 gives `vwap == 100` (unchanged) but `vwap_ext == 75` (pre-market's contribution carried through) — that divergence, at the exact moment `vwap` resets to a single bar, is the discrepancy against other platforms this closes.

One existing test (`test_vwap_publishes_even_while_sma_is_still_warming_up`) needed its exact-equality assertion updated to include the two new keys — expected, not a regression; the same test was already touched once before for `session_volume`'s own addition.

**Not built — still gated on §3's empirical question:** `premarket_volume_ratio`. `scripts/check_premarket_data_availability.py` now exists to actually answer that question against a real Polygon key (checks both whether pre-market 1m bars return real data, and — via its printed guidance — whether the daily bar already folds pre-market volume in). Needs to be run with `POLYGON_API_KEY` set before this feature's code gets written.

**Two bugs caught during this work, both fixed, worth recording:** (1) the new empirical-check script initially referenced a class named `PolygonProvider`, which doesn't exist — the real class is `PolygonAdapter`; caught by actually importing the script rather than trusting it from memory. (2) Both this script and the earlier-delivered `scripts/test_scanner_pipeline.py` failed with `ModuleNotFoundError: No module named 'app'` when run exactly as their own docstrings instructed (`cd backend && python scripts/foo.py`) — Python puts the script's own directory on `sys.path`, not the invoking working directory, so any script importing `app.*` directly (unlike `verify_roundtrip.py`, which only ever talks to a running server over HTTP and never hit this) needs an explicit `sys.path` fix. Both scripts now have it.

---

## 7. Fifth update — `premarket_volume_ratio` built, and a genuine pre-existing bug found along the way

Saqib confirmed directly (real Polygon key, real 1m pre-market bars observed) that §3's empirical gate is satisfied. `premarket_volume_ratio` is now built:

- `_maybe_refresh_premarket_baseline` — a new async method, same shape as `_maybe_refresh_daily_levels` (once-per-(symbol, ET day) gate, same provider-error handling), but fetching 1-MINUTE bars (not 1-day) and grouping by trading day itself, since a single daily bar can't isolate how much of it was pre-market. Deliberately a separate cache (`self._premarket_baseline_cache`) from the existing `self._daily_candle_cache` — different granularity, not worth forcing through one shape.
- `_update_premarket_volume_ratio` — reuses `indicators/rvol.py`'s `rvol()` function verbatim against the pre-market-specific baseline and the pre-market window's own 330-minute total (4:00-9:30), renaming its output key from `rvol` to `premarket_volume_ratio` so the two never collide. Only ever published while still inside `Session.PRE_MARKET` — no continuation story the way `vwap_ext` has.
- New config: `feature_engine_premarket_lookback_days` (default 5) — deliberately separate from `feature_engine_rvol_lookback_days` even though both default to 5, since a symbol's daily volume and its pre-market volume are different distributions; tuning one shouldn't silently move the other.
- 10 new tests (`tests/test_premarket_volume_ratio.py`) — direct-poke math/gating tests plus fake-provider tests covering the fetch/filter/group-by-day logic directly, since that logic has no existing precedent in this codebase to lean on for coverage.

**A real, pre-existing bug found and fixed along the way, unrelated to this feature's own logic:** adding a second consumer of the shared historical-provider seam broke 6 existing tests whose fake providers tracked a single global call count instead of counting per-timeframe — fixed by making those fakes timeframe-aware rather than by weakening what they actually protect (the daily-levels 1d-fetch-sharing claim). Separately, 4 tests (3 in `test_feature_engine.py`, 1 in `test_intelligence_routes.py`) used `datetime.now(timezone.utc)` as their base timestamp instead of a fixed anchor — a latent flakiness that predates this work (`pmh`/`pml` already published unconditionally during pre-market before any of this was built) but only actually triggered once the sandbox's real wall-clock crossed into ET pre-market hours during this session. Fixed by anchoring all four to a fixed Saturday (`_et(2026, 8, 15, 12, 0)`), guaranteed `Session.CLOSED` regardless of what hour tests run. **255/255 backend tests passing**, confirmed stable across two consecutive runs while the sandbox's own clock remained in pre-market hours the whole time — not a coincidence of timing.
