# Market Activity Scanner (Core Tier) — Design & Implementation Plan

**Status:** DRAFT — not yet confirmed. No application code has been written. This is a direction proposal for Saqib's review, same pre-build stage `daily-levels-design.md` was at before decision #59 locked it. Nothing here should be treated as settled until reviewed and logged as a confirmed decision.
**Revision note:** updated after a follow-up conversation that resolved IBKR's role in this plan (see new §9) — spread tightness (§2) and the Discovered-tier data source (§7) both now have a real answer instead of an open gap, on a timeline, not immediately. The rest of the plan (§1–§6, §8) is unchanged. **Second update:** §2's `ActivityScorer` is now actually built and unit-tested (`app/scanner/scorer.py`, `tests/test_scanner.py`, 6 passing tests) — this resolves §10's old "build now vs. wait for IBKR" question in favor of building now. A placeholder `UniverseProvider` (`app/scanner/universe.py`) and a live pipeline test script (`scripts/test_scanner_pipeline.py`) exist to look at real rankings against a small 6-symbol test set — NOT the real Core-100, still open. `ScanCadenceSchedule`, `MarketActivityScanner` (promotion/orchestration), and `LiveTickRelay` wiring (§1, §4, §5) are not built yet — deliberately out of scope for this pass. **Third update:** a real frontend surface now exists — `GET /scanner/state` (`app/scanner/runner.py`, `app/api/routes/scanner.py`, 3 more passing tests) runs the same `ActivityScorer` on demand and returns ranked JSON; `ScannerPanel.tsx` + `useScannerState.ts` poll it every 15s and render it as a docked panel next to `FeatureEnginePanel`. This is explicitly a smaller thing than §5's `MarketActivityScanner` — on-demand recomputation on every request, not a continuously-running scheduled process, and not wired into `LiveTickRelay` or any WebSocket event. See new §11 for what's built vs. still open on the frontend side specifically.
**Owner:** Saqib
**Companion documents:** [`system-design.md`](./system-design.md) §4.5 (Feature Engine — the sole source of every input this scanner uses), §4.7 (Market Activity Scanner — the original concept this plan implements in full), §4.11 (Trading Workspace UI — the `Market Scanner` frontend widget this plan's output eventually feeds); [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) (decisions #45–#71 — every Feature Engine indicator this plan reads from; #72 — `LiveTickRelay`, the existing consumer this plan's output slots into directly); [`../../backend/app/services/live_tick_relay.py`](../../backend/app/services/live_tick_relay.py); [`../decisions/future-ideas.md`](../decisions/future-ideas.md).

**Why this doc exists:** §4.7 already locked the *shape* (100-symbol universe → composite activity score → top-N promotion → schedule-driven cadence). What it didn't resolve — because Feature Engine wasn't built yet at the time — is which specific `FeatureSet` keys the score actually uses, how the universe itself is supplied, how this wires into the two consumers that already exist and are waiting (`LiveTickRelay.set_active_symbols`, the not-yet-built Scanner frontend widget), and how Trading212's non-canonical symbol format is kept from leaking into any of it. This doc resolves those, and is scoped to the **Core tier only** — a fixed, hand-maintained universe. The Discovered tier (dynamic symbol discovery via a movers/gainers feed) is explicitly out of scope here; see §7.

---

## 0. The concept this plan implements

**Every ~N seconds (schedule-driven, not fixed), score every symbol in a fixed Core universe using only numbers the Feature Engine already publishes, and promote the top 8 to full downstream attention — Strategy Engine evaluation and `LiveTickRelay`'s live-tick pass-through.** Nothing in this plan computes a new indicator. It is entirely a consumer of `FeaturesUpdated`, same posture Level Interaction Engine already has toward Daily Levels.

Four concerns, kept separate on purpose (same discipline `daily-levels-design.md` §0 used for calculation vs. interaction):

1. **Universe** — which symbols are even eligible to be scored. A static list today (§3).
2. **Scoring** — turning each eligible symbol's latest `FeatureSet` into one comparable number (§2).
3. **Promotion** — ranking, taking the top N, and publishing that decision to whoever needs it (§5).
4. **Cadence** — when scoring happens at all (§4, already specified in §4.7 — reused, not redesigned).

---

## 1. Module breakdown

```
backend/app/scanner/
├── __init__.py
├── universe.py       # UniverseProvider — Core-tier symbol list (§3)
├── scorer.py         # ActivityScorer — pure function: FeatureSet -> float (§2)
├── schedule.py        # ScanCadenceSchedule — already specced in system-design.md §4.7, implemented here
└── scanner.py         # MarketActivityScanner — orchestrator; owns nothing the other three do
```

Each piece is independently testable and independently replaceable:

- `UniverseProvider` is an interface (`get_core_universe() -> list[str]`) with one implementation today (static config list). A future `DiscoveredUniverseProvider` (§7) implements the same interface — `MarketActivityScanner` would just union two providers' output, not grow a special case.
- `ActivityScorer.score(feature_set: FeatureSet) -> float` takes a `FeatureSet` and returns a number. No I/O, no state, no knowledge of ranking or promotion — trivially unit-testable against hand-built `FeatureSet` fixtures, same style `indicators/atr.py` already uses.
- `MarketActivityScanner` is the only piece that touches the Event Bus, `LiveTickRelay`, or any store. It asks `UniverseProvider` for symbols, asks Feature Engine's existing snapshot cache for each symbol's latest `FeatureSet`, hands each to `ActivityScorer`, sorts, and publishes (§5). It does not compute anything itself — same "nothing downstream recomputes an indicator" rule §4.5 already states for Strategy Engine, extended to the Scanner explicitly (§4.7 already says this; this plan just holds to it).

This mirrors the split `daily-levels-design.md` §0 used (calculation vs. interaction) one layer up: universe/scoring/cadence are pure-ish concerns, promotion is the only place with side effects.

---

## 2. Activity Scorer — inputs, and one honest gap

**§4.7 names four inputs: relative volume, ATR expansion, gap %, spread tightness.** Checking what Feature Engine actually publishes today against that list:

| §4.7 input | `FeatureSet.features` key | Status |
|---|---|---|
| Relative volume | `rvol` | ✅ built (decision #71) |
| Gap % | `gap_pct` | ✅ built (decisions #67–#68) |
| ATR expansion | `atr_14_pct` | ✅ built, but see note below |
| Spread tightness | — | ❌ not available |

**Spread tightness cannot be built with either current data source.** It needs live bid/ask (L1 quote) data. Finnhub's free WebSocket tier streams trades, not quotes; Polygon's free tier has no real-time access at all (15-minute-delayed REST only). Neither provider can honestly answer "how tight is the spread right now." Rather than fabricate a proxy for it, **v1 drops it and scores on the three inputs that are real.**

**This is now a two-phase plan, not an indefinite gap (see §9 for the full decision):** IBKR gives genuine real-time bid/ask once the $10/month market data subscription is purchased — confirmed capable, not a guess. Saqib has decided to buy that subscription, but only after Trading Intelligence and Performance Intelligence are built first. So: **v1** (this doc, buildable now) scores on rvol/gap/session-change against whatever's already streaming; **v2** adds a fourth `spread_pct` input once the IBKR subscription lands, per §9. Nothing about v1's formula shape needs to change to accommodate v2 later — adding a fourth weighted term to the function in this section is additive.

**ATR expansion, precisely:** `atr_14_pct` is a *frozen daily baseline* (yesterday's 14-day ATR%, per decision #68's design — it does not move intraday). "Expansion" implies comparing today's *realized* range against that baseline, not reporting the baseline alone. Proposed: use `atr_14_pct` as the volatility-normalization denominator for the other two inputs rather than as a raw scoring input by itself — e.g. `|gap_pct| / atr_14_pct` expresses "is today's gap large *for this specific stock's normal volatility*," which is more honest than comparing raw gap % across a universe that mixes low-ATR and high-ATR names. Open question for Saqib in §10.

**Proposed v1 formula (config-driven, not hardcoded):**

```python
def score(fs: FeatureSet) -> float:
    rvol = fs.features.get("rvol", 0.0)
    gap = abs(fs.features.get("gap_pct", 0.0))
    atr_pct = fs.features.get("atr_14_pct")
    session_chg = abs(fs.features.get("session_pct_change", 0.0))

    gap_normalized = gap / atr_pct if atr_pct else gap
    session_normalized = session_chg / atr_pct if atr_pct else session_chg

    return (
        WEIGHT_RVOL * rvol
        + WEIGHT_GAP * gap_normalized
        + WEIGHT_SESSION_CHANGE * session_normalized
    )
```

`WEIGHT_RVOL`/`WEIGHT_GAP`/`WEIGHT_SESSION_CHANGE` live in `scanner_config` (§8), default `1.0` each pending real tuning — same "start honest and equal-weighted, tune from observed behavior, don't guess a sophisticated weighting scheme upfront" posture as `atr.py`'s own proxy-vs-textbook tradeoff.

A symbol missing `rvol` (not enough history yet — `rvol.py` returns `{}` in that case, same honest-gap convention every indicator uses) scores using whatever it does have, never a fabricated default of the missing pieces at zero disguised as a real reading. Symbols with **no** `FeatureSet` at all yet (never streamed) are simply absent from that scan cycle's ranking — not scored as zero, not treated as an error.

---

## 3. Core Universe (`UniverseProvider`)

A static, Saqib-curated list of ~100 canonical symbols (`"AAPL"`, `"AMD"`, etc. — the same plain-ticker format `Tick`/`Candle`/`OrderRequest` already use everywhere in the codebase). Lives in a `scanner_universe` config table (own table, not folded into `scanner_config`, since one is a list of symbols and the other is scoring/cadence parameters — same "don't conflate two different config shapes" instinct behind keeping `daily_levels_cluster_pct` and `daily_levels_identity_match_pct` as separate settings in decision #59 despite sharing a default value).

`UniverseProvider.get_core_universe() -> list[str]` is the entire interface. Swappable later for a DB-backed or admin-editable version without `MarketActivityScanner` changing at all.

**Not resolved here — Saqib's call, not mine:** the actual 100 symbols. Out of scope for this design pass; flagged in §10.

---

## 4. Cadence

Reuses §4.7's `ScanCadenceSchedule` exactly as already specified — no redesign:

```python
DEFAULT_SCAN_SCHEDULE = [
    ScanWindow(start="09:30", end="10:00", interval_seconds=5),
    ScanWindow(start="10:00", end="11:30", interval_seconds=20),
    ScanWindow(start="11:30", end="14:30", interval_seconds=90),
    ScanWindow(start="14:30", end="15:30", interval_seconds=20),
    ScanWindow(start="15:30", end="16:00", interval_seconds=5),
]
```

Keyed against `MarketClock.current_session()` (already built — §4.3 — the same clock Scanner and Strategy Scheduler were always meant to share). Config-table-backed (`scanner_schedule`), not hardcoded, exactly as §4.7 already states.

---

## 5. Promotion — where the ranking actually goes

Top N (default **8**) — deliberately the same number as `LiveTickRelay.DEFAULT_MAX_ACTIVE_SYMBOLS`, which is not a coincidence to preserve: that constant exists *specifically* for "whatever a scanning process currently flags as most active" per its own module docstring, and its `max_active_symbols` ceiling raises `ValueError` on anything larger — so N=8 isn't just a config default, it's a hard contract with a consumer that already exists and already enforces it.

On every scan cycle, `MarketActivityScanner`:

1. Computes the full ranked list (all universe symbols with a valid score, sorted descending).
2. Calls `LiveTickRelay.set_active_symbols(top_8_symbols)` directly — the exact integration point `main.py`'s own comment already anticipates (`"/market/active-symbols call today; Market Scanner eventually"`). This is the one existing TODO this plan closes.
3. Publishes a new `ScannerRankingUpdated` event (full ranked list, not just top 8 — a frontend Scanner widget showing "what's next in line" needs more than the cutoff) onto the normal Event Bus lane.
4. Keeps the latest ranking in an in-memory snapshot, exposed via a new `GET /scanner/state` endpoint — same `get_snapshot()` pattern Feature Engine and Level Interaction Engine already use for `GET /intelligence/state`, not a new pattern.

**Promoted symbols are not automatically forwarded to Strategy Engine** in this plan — Strategy Engine doesn't exist yet (Phase 5). `ScannerRankingUpdated` is the hook a future Strategy Scheduler subscribes to; this plan only guarantees the event exists and carries the right shape, not that anything downstream reacts yet (same "widen the schema, implement narrow" pattern `BrokerAdapter`'s unwired `place_order` already uses).

---

## 6. Trading212 symbol translation — and why it does NOT belong in the Scanner

Raised in the original ask, worth being explicit about: **the Scanner never sees a Trading212-formatted symbol, and shouldn't.** Every module in this plan — `UniverseProvider`, `ActivityScorer`, `MarketActivityScanner`, the `ScannerRankingUpdated` event — deals exclusively in the same canonical plain-ticker format Feature Engine, IBKR, Polygon, and Finnhub already share. That's consistent with the existing `BrokerAdapter` contract (`base.py`): symbol translation is each adapter's own problem, resolved at the boundary where a broker-specific call is actually made — `IBKRAdapter` already does this internally (resolving a canonical ticker to an IBKR `Contract`), and `SymbolNotFoundError` is already generalized across providers for exactly this reason.

**Proposed shape, for whenever Trading212 integration actually starts (Phase 5/6, not now):**

```python
class SymbolMapper(ABC):
    @abstractmethod
    def to_broker_symbol(self, canonical: str) -> str: ...
    @abstractmethod
    def from_broker_symbol(self, broker_symbol: str) -> str: ...

class IdentitySymbolMapper(SymbolMapper):
    """Default — used by IBKR/Polygon/Finnhub, which already speak canonical tickers."""
    def to_broker_symbol(self, canonical: str) -> str: return canonical
    def from_broker_symbol(self, broker_symbol: str) -> str: return broker_symbol

class Trading212SymbolMapper(SymbolMapper):
    """The one adapter that actually needs translation. Table-driven, not
    algorithmic — see the format note below before this gets built."""
    ...
```

`Trading212Adapter` (when built) calls `self._symbol_map.to_broker_symbol(order.symbol)` immediately before any T212 API call, and `from_broker_symbol()` on anything T212 returns (positions, fills) before it re-enters the system. Nothing else in the codebase — Scanner included — ever imports `Trading212SymbolMapper`.

**A format note, not a spec:** the example given (`amd_us_stock`) doesn't match Trading212's publicly documented instrument-ticker convention, which uses a `TICKER_EXCHANGE_TYPE` shape (e.g. `AAPL_US_EQ`) per their own API docs — but this needs verifying against a real call to T212's instrument-metadata endpoint with a live key before anything is hardcoded, same empirical-check discipline as the Polygon 180-day check (decision #59) and the Finnhub historical-data 403 (decision #32) — both of which turned out to not match assumption-based guesses. **Not doing that verification now** — it's Phase 5/6 scope and T212 is already flagged as "beta, not battle-tested." Recording the interface shape here so Scanner work today doesn't box in that decision later.

---

## 7. Explicitly out of scope: Discovered tier

The two-tier universe idea (Core, fixed; Discovered, pulled dynamically from a movers/gainers feed) is real and worth building eventually, but this plan is Core-only. Reasons to keep it separate rather than build both at once:

- **The data-source question for Discovered tier now has a real answer, on IBKR's timeline (§9), not Polygon's.** Polygon's "Top Market Movers" endpoint access on the free tier was flagged as unverified when this doc was first drafted. IBKR's native `reqScannerSubscription` (confirmed to exist and work — `TOP_PERC_GAIN`, `MOST_ACTIVE`, `HOT_BY_VOLUME`, etc.) is the better candidate once IBKR is live, and doesn't carry Polygon's unverified-access risk. Still not built now — `DiscoveredUniverseProvider` stays deferred until Core tier itself is stable — but the eventual source is decided, not open.
- `UniverseProvider` (§3) is deliberately an interface for exactly this reason — adding a second implementation later is additive, not a rework.
- **The 100-symbol concurrency prerequisite is resolved for the eventual (IBKR) state, but still open for however Core tier ships in the meantime.** Even Core-tier-only, the Scanner needs Feature Engine actively computing `FeatureSet`s for all ~100 Core symbols simultaneously. If Core tier ships on IBKR from the start, this is a non-issue — confirmed 100 concurrent real-time market-data lines by default, an exact match for the Core-100 universe. If Core tier ships first against whatever's already streaming (Finnhub), the concurrency ceiling on Finnhub's free WebSocket tier is still **not verified** — `future-ideas.md`'s Finnhub entry only confirms real-time streaming works, not at what scale. Which of these two paths actually happens is §9's open sequencing question.

---

## 8. Config additions (proposed)

| Setting | Default | Notes |
|---|---|---|
| `scanner_top_n` | 8 | Hard-capped by `LiveTickRelay.DEFAULT_MAX_ACTIVE_SYMBOLS` — raising one without the other breaks §5's `set_active_symbols` call. |
| `scanner_weight_rvol` | 1.0 | §2 formula. |
| `scanner_weight_gap` | 1.0 | §2 formula, ATR-normalized. |
| `scanner_weight_session_change` | 1.0 | §2 formula, ATR-normalized. |
| `scanner_schedule` | §4's `DEFAULT_SCAN_SCHEDULE` | Already specced in §4.7 — table, not hardcoded. |
| `scanner_universe` | Saqib-curated ~100 symbols | §3 — not resolved in this doc. |

---

## 9. IBKR integration — what's decided, what's still open

Resolved in a follow-up conversation, worth capturing here rather than leaving implicit:

**Decided — IBKR becomes the primary data source, on a deferred timeline.** Once the $10/month "US Securities Snapshot and Futures Value Bundle" is purchased, IBKR supplies real-time bid/ask (closing §2's spread-tightness gap), its 100-line default market-data concurrency exactly matches the Core-100 universe (closing §7's concurrency question), and its native `reqScannerSubscription` becomes the real Discovered-tier data source (superseding the unverified Polygon idea in §7). None of this is a guess — checked directly against IBKR's own documentation, not assumed.

**Decided — sequencing.** Saqib is building Trading Intelligence and Performance Intelligence first; the IBKR subscription purchase (and the work gated on it — bid/ask wiring in `IBKRAdapter`, the new `spread_pct` indicator, Discovered-tier via IBKR's scanner) comes after. This is a real, deliberate ordering choice, not neglect: Trading Intelligence consumes the same `FeatureSet` abstraction regardless of which provider is underneath (§4.5's own rule), so none of that work is blocked by which data source Feature Engine happens to read from today. Performance Intelligence's real gate is Execution Engine (Phase 6, needs IBKR's *execution* API, not its *market data* subscription) — a separate unlock from the $10 bundle entirely.

**Still open — not resolved in that conversation, needs Saqib's call before Stage 2 of the IBKR build:** once adopted, does IBKR run *parallel* to Finnhub (Finnhub keeps building 1m candles exactly as today via the already-tested `TickIngestBridge`/`CandleRecorder` pipeline; IBKR is a second, independent subscription supplying only bid/ask for the Core-100), or does it *replace* Finnhub as the tick source entirely (IBKR can supply both trades and quotes on one subscription, one fewer provider to maintain)? Recommendation leans parallel, per "don't rewrite unrelated modules" — but this is an architecture fork, not a detail, and shouldn't default silently either way.

**Still open — not resolved in that conversation, and arguably the more immediate question:** does this Scanner (§1–§6) get built now, running v1's 3-input score against whatever's already streaming (Finnhub), and get upgraded to v2's 4-input score once the IBKR subscription lands later — or does the whole Scanner build also wait, so it ships once already sitting on IBKR from day one? Both are legitimate; this doc doesn't assume either. Worth Saqib's explicit call, since it decides what (if anything) gets built next out of this document.

---

## 10. Open questions for Saqib (nothing below is decided)

1. **The actual Core-100 symbol list.** Not a technical question — needs Saqib's own criteria (liquidity, sector spread, personal watchlist history, etc.). `app/scanner/universe.py`'s `TEST_UNIVERSE` (6 liquid names) is a placeholder only, used for pipeline testing — not a proposal for the real list.
2. **ATR-normalizing gap/session-change, vs. scoring them raw** — implemented as normalizing (§2), matching the "volatility-relative move" scan type Saqib chose to test with. Worth confirming this reads correctly once real rankings are visible via `scripts/test_scanner_pipeline.py`, but not an open design choice anymore.
3. **Weight defaults (1.0/1.0/1.0)** — shipped as-is in `Settings` (`scanner_weight_rvol`/`scanner_weight_gap`/`scanner_weight_session_change`). Tune from what `scripts/test_scanner_pipeline.py` actually shows, not from theory.
4. ~~Build-now-on-Finnhub vs. wait-for-IBKR~~ — **resolved: build now.** `ActivityScorer` is built and tested against current APIs (Finnhub/Polygon), independent of the IBKR timeline in §9.
5. **IBKR parallel vs. replace Finnhub** (§9) — still open, and now more concrete: whichever way this goes, it changes how `spread_pct` gets wired into the same `FeatureSet` this scorer already consumes, not how the scorer itself works.
6. **`MarketActivityScanner`/`ScanCadenceSchedule`/promotion (§1, §4, §5) aren't built yet.** Worth doing once the test script's rankings look right on the placeholder universe — or worth waiting for the real Core-100 list (#1) first, so the orchestrator isn't built and tuned against symbols that'll be thrown away. Saqib's call.

None of the above blocks anything currently built. They matter for what gets built next.

## 11. Frontend v1 — what's built, what's deliberately deferred

**Built:** `GET /scanner/state` (on-demand, recomputes every call — cheap, since it's an in-memory read off `FeatureEngine.get_snapshot()`, same posture `GET /intelligence/state` already has). `ScannerPanel.tsx` — a fixed-width docked panel mounted alongside `FeatureEnginePanel` in both `App.tsx` workspace shells, showing rank/symbol/score/inputs-available badge, plus the raw rvol/gap/session-change/ATR values that produced each score so a reading can be sanity-checked rather than trusted blind. `useScannerState.ts` polls every 15s.

**Deliberately not built, and why each is a real scope line, not an oversight:**

- **No WebSocket push.** There's no `ScannerRankingUpdated` event to subscribe to — that only exists once `MarketActivityScanner` (§5) does. Polling is the correct v1 answer, not a placeholder for something forgotten.
- **No resizable width / collapse / session persistence.** `FeatureEnginePanel` and `InfoTab` both have `MIN_WIDTH`/`MAX_WIDTH`/`COLLAPSED_WIDTH` wired into `WorkspaceContext`'s stored layout and drag-resize handlers. `ScannerPanel` is fixed-width. Adding that machinery means touching `WorkspaceContext`'s persisted shape and the `normalizeSubWindow`-style backfill pattern old saved sessions need (per the standing "backfill for every new config field" principle) — real work, not a checkbox, and not worth doing against a 6-symbol placeholder universe.
- **No symbol/universe editor in the UI.** The panel shows whatever the backend defaults to (`TEST_UNIVERSE`). Editing the universe from the frontend is meaningful only once there's a real Core-100 to edit (§10 open question #1) — building an editor for a 6-symbol placeholder would need rebuilding anyway.
- **No sub-window / grid integration.** `ScannerPanel` is a fixed docked panel (same category as `FeatureEnginePanel`), not a `SubWindowGrid` tile — it was never a candidate for the grid's drag/drop/multi-monitor system, which is for chart windows specifically.

Verification before this was sent: `tsc -b` (only the known, pre-existing `GridPresetPicker.tsx` errors, decision #35 — nothing new), `vite build` (clean), 9 backend tests passing (6 scorer + 3 runner). No live browser check was possible in this environment, same standing caveat every frontend delivery carries.

## 12. Fourth update — RVOL-only scoring, persisted/editable universe, top-8 display, collapsible panel

**Decided — score on RVOL alone for now.** `scanner_weight_gap` and `scanner_weight_session_change` are set to `0.0` in `Settings` (were `1.0`). The ATR-normalized gap/session-change terms in `scorer.py` still run — a symbol's `inputs_available` count still reflects whether that data existed — they just don't currently move the ranking. Flipping the weights back above `0.0` brings them back into the score; no code change needed.

**Built — the real Core-100 doesn't exist yet, but the placeholder is no longer hardcoded.** `scanner_universe_symbols` (migration 0004, seeded with the same 6 `TEST_UNIVERSE` symbols) replaces the Python constant as `GET /scanner/state`'s default source. `GET/POST/DELETE /scanner/universe` let the universe actually be edited — add/remove a symbol without a code change or redeploy. Validation is **format-only** (1-5 letters, optional share-class suffix like `BRK.B`) — deliberately NOT a check that the symbol actually trades anywhere or has live data, which would need a real Finnhub/Polygon/IBKR call this doesn't make. Worth revisiting if a format-valid-but-dead ticker turns out to be a real nuisance in practice.

**Built — top-8 display.** `GET /scanner/state?top_n=8` (default) slices the ranked list before returning it; `total_scored` in the response says how many of the full universe actually had data, independent of the display cut.

**Built — the Scanner panel collapses/resizes now**, matching `FeatureEnginePanel`'s exact pattern: `scannerCollapsed`/`scannerWidthPx` added to `MainWindowState` (`types/workspace.ts`) and `WorkspaceContext.tsx`, same drag-resize handle, same persistence (and the same pre-existing `normalizeMainWindow` gap `featureEngineCollapsed` already has — old saved sessions predating this field get `undefined` rather than a backfilled default; not something introduced by this change, just inherited from following the identical existing pattern).

**Verification:** this round was checked against **real infrastructure**, not mocks — PostgreSQL 16 installed fresh, all four migrations (0001-0004) run against it, every universe CRUD function exercised directly against real rows, then the actual FastAPI app booted and every route hit over real HTTP (`GET /scanner/universe`, `POST` both a valid and a format-invalid symbol, `GET /scanner/state` with and without overrides, `DELETE`). 14 backend tests passing (6 scorer + 3 runner + 5 new universe tests, the last of these run against the same real Postgres instance). One real bug was caught and fixed during this process: the first draft of the universe test used a `test_feature_engine.py`-style double-underscore test ticker, which correctly failed the new format validation it was supposed to be testing around — fixed by using a format-valid placeholder ticker instead, not by weakening the validation. `tsc -b` and `vite build` both clean (only the standing `GridPresetPicker` errors, decision #35).
