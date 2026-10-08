# Market Activity Scanner (Core Tier) — Design & Implementation Plan

**Status:** DRAFT — not yet confirmed; partial implementation exists. Built: `ActivityScorer`, the on-demand `run_scan` path, persisted/editable Core universe and scanner routes, `ScannerPanel`, `useScannerState`, `useScannerUniverse`, the manual saved-universe feed request (§18.16), the read-only streaming-coverage measurement command (§18.17), and the observation-only `ScannerObservationWorker` (§18.8). The app now starts that worker only with explicit simulated-mode configuration and selected MarketClock sessions (§18.15); `GET /scanner/observation` and the panel's "Scheduled observation" section read its retained result (§18.10), including source feature-candle times (§18.12). Not built: automated universe feed acquisition, `ScanCadenceSchedule`, top-N promotion into `StrategyScheduler`, top-N activation through `LiveTickRelay.set_active_symbols`, the Discovered tier, or spread-tightness scoring/filtering. The DRAFT/proposed label remains pending Saqib's product/design choices.
**Continuous-scanner-design update:** §18 verifies the current implementation and specifies a minimum simulated-only continuous slice. Decision #189 confirms its 60-second cadence, manual-first activation, capacity prerequisite and protective feed retention; §18.7 separates those choices from recommendations still open. Scheduled **observation** can now be enabled explicitly (§18.15), while scanner-driven execution and provider coverage remain unbuilt. Where earlier proposed text differs from current code, use §18's as-built inventory.
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

The diagram below is the historical proposed full module layout. The as-built worker core is described in §18.8; `schedule.py` and `MarketActivityScanner` are not present.

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

**Built — the Scanner panel collapses/resizes now**, matching `FeatureEnginePanel`'s exact pattern: `scannerCollapsed`/`scannerWidthPx` added to `MainWindowState` (`types/workspace.ts`) and `WorkspaceContext.tsx`, same drag-resize handle, same persistence (and the same pre-existing `normalizeMainWindow` gap `featureEngineCollapsed` already has — old saved sessions predating this field get `undefined` rather than a backfilled default; not something introduced by this change, just inherited from following the identical existing pattern — the Scanner side of this gap is fixed in §15 below; `featureEngineCollapsed`/`featureEngineWidthPx` remain unfixed, out of that delivery's scope).

**Verification:** this round was checked against **real infrastructure**, not mocks — PostgreSQL 16 installed fresh, all four migrations (0001-0004) run against it, every universe CRUD function exercised directly against real rows, then the actual FastAPI app booted and every route hit over real HTTP (`GET /scanner/universe`, `POST` both a valid and a format-invalid symbol, `GET /scanner/state` with and without overrides, `DELETE`). 14 backend tests passing (6 scorer + 3 runner + 5 new universe tests, the last of these run against the same real Postgres instance). One real bug was caught and fixed during this process: the first draft of the universe test used a `test_feature_engine.py`-style double-underscore test ticker, which correctly failed the new format validation it was supposed to be testing around — fixed by using a format-valid placeholder ticker instead, not by weakening the validation. `tsc -b` and `vite build` both clean (only the standing `GridPresetPicker` errors, decision #35).

---

## 13. Fifth update — universe DB calls moved off the event loop (`scanner-route-db-offload`)

**Problem.** `GET /scanner/state`'s default-universe path and `GET`/`POST`/`DELETE /scanner/universe` called `app/scanner/universe.py`'s synchronous SQLAlchemy functions directly from their `async` route handlers. Each of those functions already opens and closes its own `Session` (§3/§12 above), but running that call directly ON the event loop meant a slow read/write — a lock wait, a slow query, a stalled connection — held up every other request this process was serving for its duration, not just the Scanner one.

**Fix — `asyncio.to_thread` at the route boundary, nothing changed below it.** Every call site in `app/api/routes/scanner.py` now `await`s `asyncio.to_thread(...)` around the same function call that was there before — same convention `app/services/candle_store.py`/`app/api/routes/market.py` already established elsewhere in this codebase (see that module's own docstring). Nothing inside `app/scanner/universe.py` changed: each function still creates its own `Session`, uses it, and closes it in a `finally`, now just running inside a worker thread instead of on the event loop. Validation, the `TEST_UNIVERSE` fallback, response shapes, and the POST route's `ValueError`→400 mapping are byte-identical to before.

**Deliberately NOT touched:** `run_scan()` and `FeatureEngine.get_snapshot()` (§5, `runner.py`). `get_snapshot()`'s own docstring already states it is a "pure in-memory dict read — no I/O, safe to call directly from an async route handler without `asyncio.to_thread`"; inspection confirmed this is accurate (a plain dict walk over `self._latest`, no DB, no network), so wrapping it would add thread-hop overhead for zero blocking-risk benefit. `scorer.py`'s scoring math is likewise pure computation.

**Diagram 1 — data flow, this process, before vs. after:**

```
BEFORE
  Frontend (ScannerPanel / useScannerUniverse)
        │  HTTP
        ▼
  GET/POST/DELETE /scanner/universe , GET /scanner/state    [async route, event loop]
        │
        ▼
  app/scanner/universe.py   (sync SQLAlchemy, runs ON the event loop)
        │
        ▼
  PostgreSQL (symbols / scanner_universe_symbols)

  A slow call here blocks every other request this process is
  serving for its duration — Feature Engine writes, other routes,
  everything sharing this one event loop.

AFTER
  Frontend (ScannerPanel / useScannerUniverse)
        │  HTTP
        ▼
  GET/POST/DELETE /scanner/universe , GET /scanner/state    [async route, event loop]
        │
        │  await asyncio.to_thread(fn, ...)
        ▼
  worker thread (default executor)
        │                                     event loop is free here —
        │  app/scanner/universe.py            other requests (e.g. GET
        │  (same sync SQLAlchemy code,        /health, GET /intelligence/
        │   own Session, own commit/close)    state) still get served
        ▼
  PostgreSQL (symbols / scanner_universe_symbols)
        │
        ▼
  result returned to the awaiting route handler, back on the event loop
```

**Diagram 2 — internal flow within `app/api/routes/scanner.py` (what moved, what didn't):**

```
GET /scanner/state
  ?symbols= given?  ──yes──►  parse comma list in-process (no DB, unchanged)
        │ no
        ▼
  await asyncio.to_thread(DbUniverseProvider(SessionLocal).get_core_universe)   ← NEW: off-loop
        │  empty? → fall back to TEST_UNIVERSE (in-process, unchanged)
        ▼
  run_scan(universe, ...)                           ← UNCHANGED: stays on the event loop
        │
        ├─► get_feature_engine().get_snapshot()      pure in-memory read, no I/O (own docstring)
        └─► score_symbol() per symbol                 pure computation, no I/O
        ▼
  JSON response (same shape as before)

GET /scanner/universe     → await asyncio.to_thread(list_universe_symbols, SessionLocal)         ← NEW
POST /scanner/universe    → await asyncio.to_thread(add_symbol_to_universe, SessionLocal, sym)   ← NEW
                              (ValueError still caught → HTTPException 400, unchanged)
DELETE /scanner/universe/{symbol}
                           → await asyncio.to_thread(remove_symbol_from_universe, SessionLocal, symbol)  ← NEW
```

**Testing.** New `backend/tests/test_scanner_route_concurrency.py` — two focused tests, real ASGI transport, no sleeps: a `threading.Event`-controlled fake `list_universe_symbols` blocks until released; one proves `GET /health` still answers while a blocked `GET /scanner/universe` request is stuck in its worker thread, the other proves two concurrent blocked Scanner requests both complete rather than one starving the other. Verified the test is a genuine regression guard, not a false positive, by temporarily reverting the `asyncio.to_thread` wrapping and confirming it then fails (times out) before re-applying the fix. Full backend suite: 1109 passed/0 failed on the untouched baseline (fresh `main` pull, real Postgres 16), 1111 passed/0 failed with this delivery (exactly +2, the new concurrency tests) — zero regressions. The existing scoring/response-shape assertions in `test_scanner.py`/`test_scanner_runner.py`/`test_scanner_universe.py` (17/17) still pass unchanged.

**Footprint**, confirmed by `diff -rq` against a freshly re-pulled `main`: edited — `backend/app/api/routes/scanner.py` (`asyncio.to_thread` wrapping only, no behavior change). New — `backend/tests/test_scanner_route_concurrency.py`. Untouched, exactly as scoped: `app/scanner/universe.py`, `app/scanner/runner.py`, `app/scanner/scorer.py`, `main.py`, every execution/frontend file, and the continuous `MarketActivityScanner`/`ScanCadenceSchedule`/promotion path (§4/§5 above — still not built).

**No decision number assigned** for this delivery, per standing instruction: moving already-synchronous, already-correct DB work off the event loop is an operational fix at the route boundary, not a new architectural decision — nothing about universe semantics, validation, scoring, or the API contract changed.

---

## 14. Sixth update — `?symbols=` override now validated with the same ticker-format rule as the persisted universe (`scanner-override-ticker-validation`)

**Problem.** §12's persisted universe (`POST /scanner/universe`) enforces `is_valid_ticker_format` (1-5 letters, optional share-class suffix like `BRK.B`) before a symbol can ever be added. `GET /scanner/state`'s ad hoc `?symbols=` override (§0/§5, predates §12) never got the same treatment — it only `strip()`/`upper()`'d each comma-separated entry, so `?symbols=AAPL,,TSLA` (a stray comma) or `?symbols=AAPL,123` (a malformed ticker) were silently accepted as literal universe entries. Each invalid entry then reached `run_scan()`, which can't distinguish "genuinely malformed input" from "a real ticker that just hasn't streamed yet" — both come back in `skipped`, so the caller had no way to tell a typo from a cold start. The override and the persisted universe were, in effect, enforcing two different contracts for what counts as a valid symbol.

**Fix — reuse `is_valid_ticker_format` at the override boundary, fail fast instead of degrading silently.** `app/api/routes/scanner.py` gains one new private helper, `_parse_symbols_override()`, called only when `symbols is not None` (an explicit override, whether or not it's empty) — genuinely omitting the query parameter still takes the untouched `DbUniverseProvider`/`TEST_UNIVERSE` fallback path (§12/§13), unchanged. The helper: strips and uppercases each comma-separated entry; rejects the whole override with HTTP 400 if it's empty/whitespace-only; rejects any individual empty entry (a stray or trailing comma) with 400; rejects any entry that fails `is_valid_ticker_format` with 400 (same message convention `add_symbol_to_universe`'s own `ValueError` already uses); and deduplicates valid entries, keeping first-seen order rather than sorting or silently dropping order information. Nothing below the parse changes — `run_scan`, scoring, ranking, `top_n` slicing, and every universe CRUD route are byte-identical to before. No new size limit is introduced on the override (an unusually long but format-valid list is not rejected here, same posture the persisted universe itself takes — see §3's own "not resolved here" note on the real Core-100 count).

**Diagram 1 — request flow through `GET /scanner/state`, before vs. after:**

```
BEFORE
  ?symbols= given?
        │
       yes ──► [s.strip().upper() for s in symbols.split(",")]   (no validation at all)
        │            │
        │            ▼
        │      universe = [...]  (may contain "", "123", duplicates, in
        │                         whatever order split() produced them)
        │
       no  ──► DbUniverseProvider(...).get_core_universe()  (§13: off-loop)
                    │  empty? → TEST_UNIVERSE fallback
                    ▼
              universe = [...]
        │
        ▼
  run_scan(universe, ...)   ← malformed entries silently land in `skipped`,
                              indistinguishable from a real cold-start symbol
        ▼
  200 JSON response (or a confusing all-skipped result)

AFTER
  symbols is not None?  (explicit override, empty string included —
  distinct from the parameter being absent entirely)
        │
       yes ──► _parse_symbols_override(symbols)          ← NEW
        │            │
        │            ├─ whole string empty/whitespace?  ──► HTTP 400
        │            ├─ any entry empty after strip?     ──► HTTP 400
        │            ├─ any entry fails                  ──► HTTP 400
        │            │  is_valid_ticker_format?
        │            └─ else: dedup, first-seen order kept
        │            ▼
        │      universe = [...]  (every entry format-valid, unique,
        │                         in the order first seen)
        │
       no  ──► DbUniverseProvider(...).get_core_universe()  (§13: UNCHANGED)
                    │  empty? → TEST_UNIVERSE fallback   (UNCHANGED)
                    ▼
              universe = [...]
        │
        ▼
  run_scan(universe, ...)   ← UNCHANGED: every symbol reaching this line is
                              now guaranteed format-valid; `skipped` means
                              only "no FeatureSet yet," never "was malformed"
        ▼
  200 JSON response, or 400 with a specific detail message before
  run_scan() (or any DB call) ever runs
```

**Diagram 2 — internal flow of `_parse_symbols_override()`:**

```
_parse_symbols_override(symbols: str) -> list[str]
        │
        ▼
  symbols.strip() == ""?  ──yes──►  raise HTTPException(400, "must not be empty")
        │ no
        ▼
  for raw in symbols.split(","):
        │
        ▼
  item = raw.strip().upper()
        │
        ├─ item == ""?  ──yes──►  raise HTTPException(400, "empty entry ...")
        │
        ├─ not is_valid_ticker_format(item)?  ──yes──►  raise HTTPException(
        │                                                 400, "'<item>' doesn't
        │                                                 look like a valid ticker ...")
        │                                                 (same wording
        │                                                 add_symbol_to_universe's
        │                                                 own ValueError uses)
        │
        └─ item not in seen?  ──yes──►  seen.add(item); normalized.append(item)
                                (else: silently drop — duplicate, first
                                 occurrence already kept)
        │
        ▼  (loop over every comma-separated entry)
  return normalized
```

**Testing.** New `backend/tests/test_scanner_state_route.py` — 11 focused HTTP-route tests, direct ASGI transport against the real (unstarted) app, no lifespan needed: valid multi-symbol normalization (trim/uppercase, verified by round-tripping through `run_scan`'s own honest `skipped` list rather than trusting the echoed `universe` field alone), duplicate-entry dedup preserving first-seen order, a `BRK.B`-style share-class suffix accepted, a lowercase-only input NOT rejected for case (the other direction of the rule, so this isn't just testing "everything 400s"), an invalid-format ticker (400, message names the bad entry), a too-long ticker (400), an empty entry from a stray internal comma (400), a trailing comma (400), an explicitly empty `?symbols=` (400), a whitespace-only override (400), and the omitted-parameter path (no `symbols` key in the query string at all) asserting a non-empty `universe` list and never a 400 — the one test in this file that reads the real persisted `scanner_universe_symbols` table via the untouched `DbUniverseProvider`/`TEST_UNIVERSE` path. Verified these are a genuine regression guard, not false positives: temporarily reverted `app/api/routes/scanner.py` to its pre-fix form and re-ran the file — 7 of 11 tests failed (every 400 case returned 200 instead) — then restored the fix and confirmed 11/11 passed again. Also corrected a now-stale claim in `test_scanner_runner.py`'s own module docstring, which said `GET /scanner/state` had "nothing route-specific to get wrong beyond what manual verification already checked" — no longer accurate once the override gained real, route-specific validation logic of its own; corrected to point at this new file instead of removing the claim silently.

No PostgreSQL was preinstalled in this environment for this delivery either — installed PostgreSQL 16.15 locally, created the `trading`/`trading_workspace` role and database per this project's own documented convention, and ran `alembic upgrade head` (through `0014`) before running anything. Full backend suite baseline, freshly re-pulled `main` before any change: **1111 passed, 0 failed**. With this delivery applied: **1122 passed, 0 failed** (exactly +11, the new route tests; zero regressions elsewhere). The existing scoring/orchestration/universe-CRUD assertions in `test_scanner.py`/`test_scanner_runner.py`/`test_scanner_universe.py`/`test_scanner_route_concurrency.py` (19/19) still pass unchanged.

**Footprint**, confirmed by `diff -rq` against a freshly re-pulled `main` immediately before packaging (identical to the `main` this task started from — no concurrent changes to reconcile): edited — `backend/app/api/routes/scanner.py` (new `_parse_symbols_override()` helper plus the `symbols is not None` branch condition; every other line unchanged), `backend/tests/test_scanner_runner.py` (docstring correction only, no test logic changed). New — `backend/tests/test_scanner_state_route.py`. Untouched, exactly as scoped: `app/scanner/universe.py` (only *called*, not edited — `is_valid_ticker_format` is reused as-is), `app/scanner/runner.py`, `app/scanner/scorer.py`, `main.py`, every execution/frontend file, universe CRUD behavior, scoring, ranking, `top_n`, and the continuous `MarketActivityScanner`/`ScanCadenceSchedule`/promotion path (§4/§5 — still not built).

**No decision number assigned** for this delivery, for the same reason §13 gives none: this reuses an existing, already-decided validation rule (`is_valid_ticker_format`, format-only, deliberately not a liveness/tradability check — see that function's own docstring) to close a consistency gap at a second call site, rather than deciding anything new about what a valid ticker is, how universe membership works, how scoring/ranking behaves, or the shape of the API contract's success path. The only contract change is that malformed input now fails fast with a specific 400 instead of silently degrading into an all-skipped scan — an operational/correctness fix at the route boundary, not a product or architecture decision.

---

## 15. Seventh update — Scanner panel state now survives loading a session saved before the Scanner panel existed

**Problem.** §12 added `scannerCollapsed`/`scannerWidthPx` to `MainWindowState` and noted, at the time, that it was inheriting a pre-existing `normalizeMainWindow` gap rather than fixing it: `normalizeSubWindow` backfills every field it adds (chart style, opacity, HUD, etc. — see that function's own comments), and `normalizeMainWindow` itself already does the same for `lastBacktestRunId`/`lastBacktestSweepId` (decisions #134/#163), but never gained an equivalent backfill for the two Scanner fields when they were introduced. A `trading-workspace:session` blob written by any pre-§12 build of the app has no `scannerCollapsed`/`scannerWidthPx` keys at all, so `JSON.parse` leaves both `undefined` on the restored `MainWindowState` at runtime, despite the TypeScript type claiming they're always present. `ScannerPanel.tsx` reads them unguarded: `scannerCollapsed` undefined is falsy, so `!scannerCollapsed` evaluates true and the panel renders in its *expanded* branch — but `width: scannerWidthPx` is then `undefined`, and the drag-resize handler's `dragStartRef.current.width + delta` becomes `NaN`, permanently breaking that Main Window's resize handle until the tab is reloaded onto a fresh default.

**Fix — the same `??` backfill pattern already used two fields above it, applied to both Scanner fields.** `normalizeMainWindow()` gains two lines: `scannerCollapsed: w.scannerCollapsed ?? true` and `scannerWidthPx: w.scannerWidthPx ?? 300` — the exact defaults `makeMainWindow()` itself uses for a freshly created Main Window, so an old session with no Scanner state renders identically to a brand new one instead of an undefined-width panel. `??` (nullish coalescing), not `||`, is required for `scannerCollapsed` specifically: an old-but-not-ancient session that explicitly saved `scannerCollapsed: false` (user had expanded the panel) must not be silently re-collapsed by the backfill — only `??` leaves an explicit `false` alone, where `||` would treat it as absent. Nothing else in `normalizeMainWindow`, `normalizeSubWindow`, `loadSession`, or `ScannerPanel.tsx` changed — no API calls, polling, or universe editing were touched, and `featureEngineCollapsed`/`featureEngineWidthPx` are left with the identical unfixed gap §12 already documented, since backfilling those was not part of this task's approved scope.

**Diagram 1 — data flow, session restore, before vs. after:**

```
BEFORE
  Browser localStorage["trading-workspace:session"]
  (written by a pre-§12 build — no scannerCollapsed/scannerWidthPx key)
        │
        ▼
  WorkspaceProvider mounts → loadSession()
        │  JSON.parse(raw)
        ▼
  parsed.mainWindows[i]              ← scannerCollapsed, scannerWidthPx
                                        simply absent from this object
        │
        ▼
  normalizeMainWindow(w)             ← backfills lastBacktestRunId/
                                        lastBacktestSweepId only
        │
        ▼
  MainWindowState.scannerCollapsed = undefined   ← falsy → renders EXPANDED
  MainWindowState.scannerWidthPx   = undefined
        │
        ▼
  ScannerPanel.tsx: style={{ width: undefined }}      ← broken layout
                    dragStartRef.current.width = undefined
                    → resize handler computes NaN      ← resize permanently broken

AFTER
  Browser localStorage["trading-workspace:session"]
  (same old pre-§12 blob — untouched, no migration writes anything back)
        │
        ▼
  WorkspaceProvider mounts → loadSession()
        │  JSON.parse(raw)
        ▼
  parsed.mainWindows[i]              ← same absent keys as before
        │
        ▼
  normalizeMainWindow(w)             ← NEW: also backfills
                                        scannerCollapsed ?? true
                                        scannerWidthPx   ?? 300
        │
        ▼
  MainWindowState.scannerCollapsed = true     ← same as a fresh makeMainWindow()
  MainWindowState.scannerWidthPx   = 300
        │
        ▼
  ScannerPanel.tsx: style={{ width: 300 }} but panel renders COLLAPSED
                    (COLLAPSED_WIDTH shown instead — see width = scannerCollapsed
                     ? COLLAPSED_WIDTH : scannerWidthPx)
                    resize handle inert while collapsed, works correctly
                    once expanded — dragStartRef.current.width is a real number
```

**Diagram 2 — internal flow within `normalizeMainWindow()` (only the two new lines shown in context):**

```
normalizeMainWindow(w: MainWindowState) -> MainWindowState
        │
        ▼
  { ...w,
      subWindows: w.subWindows.map(normalizeSubWindow),        UNCHANGED
      lastBacktestRunId:   w.lastBacktestRunId   ?? null,      UNCHANGED
      lastBacktestSweepId: w.lastBacktestSweepId ?? null,      UNCHANGED
      scannerCollapsed:    w.scannerCollapsed    ?? true,      ← NEW
      scannerWidthPx:      w.scannerWidthPx      ?? 300,       ← NEW
  }
        │
        ▼
  w.scannerCollapsed is `false` (explicit)?  ──► `??` passes `false` through
                                                  unchanged (NOT `||`, which
                                                  would treat falsy `false`
                                                  as missing and overwrite it)
  w.scannerCollapsed is `undefined`?         ──► becomes `true`
  w.scannerWidthPx   is a number (any value)? ──► passes through unchanged
  w.scannerWidthPx   is `undefined`?          ──► becomes `300`
```

**Testing.** No frontend test runner exists in this repository (`frontend/package.json` defines no `test` script and no `.test.`/`.spec.` file exists anywhere under `frontend/`), so verification here follows the same posture every other frontend-only delivery in this doc has carried (§11's own standing caveat): `tsc -b` and `vite build` for static/build correctness, plus direct execution of the changed function against representative fixtures. For the latter, `normalizeMainWindow`/`makeMainWindow` were temporarily exported from a same-directory scratch copy of `WorkspaceContext.tsx` and exercised with `tsx` (Node, no browser) against five fixture shapes: (1) a fully old session missing both fields — backfills to `true`/`300`; (2) a current session with explicit non-default values (`scannerCollapsed: false`, `scannerWidthPx: 420`) — both preserved exactly, confirming `??` does not clobber an explicit `false`; (3) a current session with explicit default values (`true`/`300`) — passes through unchanged; (4) a partial/hand-edited session missing only `scannerCollapsed` with `scannerWidthPx` present — each field backfills independently; (5) unrelated fields (`lastBacktestRunId`, `subWindows`) on the same object — confirmed still correct, guarding against scope creep inside the same function. All 10 assertions passed. Confirmed this is a genuine regression guard, not a false positive: re-ran the identical fixtures against the function with the two new lines removed — the 3 assertions covering the two missing-field cases failed exactly as expected (falling back to `undefined`), the other 7 (explicit-value and unrelated-field cases) still passed, then the fix was restored and re-confirmed at 10/10. The scratch copy and harness were both deleted before packaging; they are not part of this delivery. `tsc -b`: clean, no errors. `vite build`: clean production build (103 modules transformed, no new warnings beyond the pre-existing chunk-size advisory).

**Footprint**, confirmed by `diff -rq` against a freshly re-pulled `main` immediately before packaging (`main` had advanced to include decision #181, `execution-orders-route`, since this task started — confirmed zero file overlap: that delivery touched only `backend/app/api/routes/intelligence.py`, `backend/tests/test_execution_orders_route.py`, `docs/architecture/execution-engine-design.md`, `docs/decisions/confirmed-decisions.md`, and `docs/decisions/INDEX.md`): edited — `frontend/src/state/WorkspaceContext.tsx` (two lines added inside `normalizeMainWindow`, plus their comment; nothing else in the file changed), `docs/architecture/scanner-design.md` (this section, plus one clause appended to §12's own sentence pointing forward to it). Untouched, exactly as scoped: `ScannerPanel.tsx`, `normalizeSubWindow`, `loadSession`, `loadSavedLayouts`, `featureEngineCollapsed`/`featureEngineWidthPx` (left with the identical unfixed gap), every backend file, every other frontend panel, all API calls and polling hooks, and universe editing.

**No decision number assigned**, per this project's own standing instruction and the precedent §13/§14 already set: this backfills a documented gap using the identical `??`-default pattern `normalizeMainWindow` already applies to `lastBacktestRunId`/`lastBacktestSweepId` two lines above it — nothing about the Scanner panel's fields, defaults, or contract is new or changed, only a pre-existing restoration bug is corrected.

**Later restoration update:** The separate Feature Engine gap called out in §12 and §15 is now fixed in `normalizeMainWindow()` with the same nullish backfill pattern for its collapsed state, width, and selected symbol. See `system-design.md` §4.11 for the current session restoration flow.

---

## 16. Eighth update — Scanner results request safety (`scanner-results-request-safety`)

Frontend-only; no backend, `api-client.ts`, `useScannerUniverse`, `WorkspaceContext.tsx`, dependency or lockfile change, and no decision number (the delivery slug identifies it). Changed: `frontend/src/hooks/useScannerState.ts`, `frontend/src/components/scanner/ScannerPanel.tsx`. Ranking, scoring, `GET /scanner/state` query semantics (omitted `symbols` = persisted universe, explicit `symbols` = override, empty array sends no parameter exactly as before), the 15-second interval, universe editing and panel layout are unchanged.

**Problem (reproduced against the base before editing).** (1) `useScannerState` had no request-order or unmount guard, so a slow older response could overwrite a newer ranking, error or timestamp. (2) The 15-second poll fired regardless of an outstanding request and stacked requests behind a slow backend. (3) Refresh was disabled while `loading`, so a hung request could not be replaced. (4) An older request's `finally` cleared `loading` while a newer request was still pending. (5) After a symbols-override change, the previous override's rows, skipped symbols, error and timestamp stayed visible until the new response arrived. (6) A failed refresh left the old rows with no indication beyond the error and, in the same render path, could not be told apart from "no data yet".

**Behavior now.**

| Situation | Result |
|---|---|
| Manual Refresh while a request is pending | Always enabled; starts a newer request that supersedes every older one |
| Older success or failure after a newer request started | Discarded: results, skipped, universe, error, loading and `lastUpdated` untouched |
| Older request settles while the newest is pending | `loading` stays true |
| 15 s poll tick while the newest request is pending | Skipped; resumes on the first tick after it settles |
| Symbols-override change, unmount, collapse / tab switch | Interval cleared, outstanding requests invalidated |
| Render after an override change, before the effect | Loading with no rows, skipped, universe, error or timestamp from the previous override |
| New array, identical contents | Same query: no refetch, polling not restarted |
| Refresh under the same query | Existing rows, skipped, universe and `lastUpdated` stay until the newer result settles |
| Failure under the same query | Last successful rows and `lastUpdated` kept; explicit `error` set (panel adds "showing last successful result"); cleared by the next success |
| Failure with no prior success for the query | Empty rows plus `error` |
| Initial load / genuine empty / failure | `loading` with no data / `!loading`, no error, no rows / `error` — three distinct states |

**Mechanism.** One request counter in a ref, as in the Backtest Results hooks (`backtest-results-refresh-recovery`). `load()` takes the next number and applies its response only if still the latest. The effect cleanup advances the counter and clears the interval. Settled state is stored as a snapshot tagged with the request key (`"omitted"` or the JSON of the symbols array) and returned only while that key is current. A separate ref marks the newest request pending, read by the poll tick only; manual Refresh ignores it. Fetches are not cancelled: a superseded request completes and its response is discarded. No fetching framework or polling service was added.

**Diagram 1 — component data flow.**

```
 WorkspaceContext (scannerCollapsed / scannerWidthPx) -- unchanged
        │
        ▼
 ScannerPanel ── Results tab mounted ──► ResultsTab ──► useScannerState(symbols?)
   (collapse / Universe tab unmounts                          │  request key = "omitted" | JSON(symbols)
    ResultsTab: timer cleared,                                │  load() from: mount, 15 s poll, Refresh
    in-flight work invalidated)                               ▼
                                                  fetchScannerState(symbols)  [api-client.ts, unchanged]
                                                              │
                                                              ▼
                                                    GET /scanner/state  [backend, unchanged]
 ResultsTab ◄── { results, skipped, universe, loading, error, lastUpdated, refresh }
                for the CURRENT key only ── Refresh button never disabled

 Universe tab ──► useScannerUniverse  (independent, unchanged)
```

**Diagram 2 — request lifecycle and polling inside `useScannerState`.**

```
 load()   [mount | override change | poll tick (only if !pending) | manual Refresh (always)]
   id = ++latest ; pending = true
   snapshot = same key ? { ...prev, loading: true }
                       : { key, empty data, error: null, ts: null, loading: true }
   fetchScannerState(symbols)
      ├─ success ─► id !== latest ? discard
      │                          : pending=false; snapshot = { key, wire data, error: null, ts: now, loading: false }
      └─ failure ─► id !== latest ? discard
                                 : pending=false;
                                   same key ? { ...prev, error, loading: false }   (rows + ts kept)
                                            : { key, empty data, error, ts: null, loading: false }

 effect:  load(); interval = setInterval(tick, 15 000)      tick: if (!pending) load()
 cleanup [override change | unmount]:  clearInterval ; latest += 1 ; pending = false

 render:  snapshot.key === currentKey ? snapshot
                                      : { loading: true, no rows, no error, no timestamp }
```

**Verification.** See `TESTING.md` (`scanner-results-request-safety`): a throwaway jsdom + React harness kept outside the repository, controllable deferred `fetch` responses and fake timers against the real hook and `ScannerPanel`; 61 checks pass, 25 fail on the unchanged base.

## 17. Ninth update — Scanner universe mutation recovery (`scanner-universe-mutation-recovery`)

Frontend-only; no backend, `api-client.ts`, `useScannerState`, ranking, scoring, `WorkspaceContext.tsx`, dependency or lockfile change, and no decision number (the delivery slug identifies it). Changed: `frontend/src/hooks/useScannerUniverse.ts` and `UniverseTab` in `frontend/src/components/scanner/ScannerPanel.tsx`; `ResultsTab` (§16) is unchanged. Ticker validation, the idempotent `POST`/`DELETE /scanner/universe` semantics, server symbol ordering and panel layout are unchanged.

**Problem (reproduced against the base before editing).** (1) `useScannerUniverse` had no request-order or unmount guard. (2) A failed optimistic delete set `error`, then the reconciliation GET succeeded and cleared it, so the failure vanished. (3) A failed initial GET rendered "Universe is empty". (4) Add was disabled while pending, but Enter still submitted again and removes overlapped. (5) A rejected write and a failed reload shared one `error`, so a successful write followed by a failed GET looked like a failed write. (6) A delayed add completion cleared text typed meanwhile. (7) A late completion after a tab switch still started a reconciliation read.

**Behavior now.**

| Situation | Result |
|---|---|
| Older read settles after a newer read, or after a mutation started | Discarded: list, `loading`, `loadError` untouched |
| Add/remove while another mutation is pending (click, Enter, same tick) | Ignored by a synchronous ref guard; nothing is sent; controls disabled and labelled |
| Failed DELETE | Row restored; `mutationError` set and kept through the reconciliation GET; cleared when the next mutation starts |
| Successful write, reload fails | `staleAfterWrite`: "Change saved, but reloading … may be out of date"; last confirmed list kept; not a write failure |
| Read fails after a successful load | Last confirmed list kept, `loadError` shown, Retry re-reads; success clears it |
| Initial load / initial failure / empty / populated | "Loading universe…" / error + Retry (no "empty" message) / "Universe is empty" / rows — four distinct states |
| Unmount, collapse, tab switch | Late read and write completions change nothing and start no reconciliation read; a request already sent is **not cancelled** and may still complete on the backend |
| Text typed during a pending add | Preserved; the field is cleared only if it still holds the submitted text |

**Mechanism.** One read counter in a ref (same pattern as §16): first load, Retry and each reconciliation read take the next number and apply only if still latest and mounted; starting a mutation advances the counter. A second ref, `mutatingRef`, is set before the first `await` (render-late state could not stop two same-tick calls) and released when the write settles, so a new mutation may start during the reconciliation read and supersedes it. The confirmed list is never edited optimistically: the pending-remove row is merely filtered out of what the hook returns. Manual `refresh()` is a no-op while a mutation is pending (its own reconciliation read follows). Settled state is one object (`confirmed, hasLoaded, loading, loadError, staleAfterWrite, mutationError, mutation`).

**Diagram 1 — component data flow.**

```
 ScannerPanel (tab state; Universe tab mounted/unmounted)  -- Results tab / useScannerState unchanged (§16)
        │ Universe tab mounted
        ▼
 UniverseTab ──► useScannerUniverse()
   input text, Add / Enter / ×, Retry           │ read(afterWrite)  : mount, Retry, after every write
   shows: loading | initial failure |           │ mutate(kind, sym) : POST add, DELETE remove
          empty | rows + mutationError +        ▼
          reload warning + pending line   fetchScannerUniverse / addScannerUniverseSymbol /
        ▲                                 removeScannerUniverseSymbol   [api-client.ts, unchanged]
        │                                         │
        └── { symbols, hasLoaded, loading,        ▼
              loadError, staleAfterWrite,   GET / POST / DELETE /scanner/universe  [backend, unchanged]
              mutationError, pendingAdd,
              pendingRemove, mutating, addSymbol, removeSymbol, refresh }
 Universe tab unmounts: counter advanced, mounted=false; requests already sent still complete server-side.
```

**Diagram 2 — mutation and reconciliation inside `useScannerUniverse`.**

```
 mutate(kind, sym)
   mutatingRef set?  ── yes ──► return false (nothing sent)
   mutatingRef = true ; latestRead += 1 ; mutation = {kind, sym} ; mutationError = null
   await write()   [remove: row hidden in the returned list only]
      ├─ ok    ─► ok = true
      └─ error ─► failure = message
   mutatingRef = false
   unmounted? ── yes ──► return ok (no state, no read)
   mutation = null ; mutationError = failure
   ok && remove ? confirmed -= sym          (failed remove: row simply reappears)
   read(afterWrite = ok) ; return ok
 read(afterWrite)
   id = ++latestRead ; loading = true ; GET /scanner/universe
      ├─ ok    ─► id stale or unmounted ? discard
      │             : confirmed = list ; hasLoaded ; loading=false ; loadError=null ; staleAfterWrite=false
      └─ error ─► id stale or unmounted ? discard
                    : loading=false ; loadError=msg ; staleAfterWrite ||= afterWrite   (list + mutationError kept)
 refresh() = read(false), ignored while mutatingRef
```

**Verification.** See `TESTING.md` (`scanner-universe-mutation-recovery`): a throwaway jsdom + React harness kept outside the repository with controllable deferred `fetch` responses against the real hook and `ScannerPanel`; 94 checks pass, 33 fail on the unchanged base.

---

## 18. Continuous scanner design (`continuous-scanner-design`; observation core built as `scanner-observation-worker`; lifespan opt-in built as `scanner-observation-lifespan`; context hot-add built as `context-universe-hot-add`; status read built as `scanner-observation-status`; provider subscription inventory read built as `provider-subscription-diagnostics`; observation source candle timestamps built as `scanner-observation-source-timestamps`; protected feed requests built as `protected-feed-reconciliation`; protected feed request status built as `protected-feed-reconciliation-status`)

This section records the verified full-system design and, in §18.8, the tested observation worker core. The application now owns that worker under an explicit observation-only opt-in (§18.15). Continuous universe feed acquisition and promotion are not deployed. Decision #189 confirms only the directions identified in §18.7; the remaining filter and switch details are recommendations. It supersedes the *as-built* implications of §§0, 4–5: the old cadence table is a draft, `ScannerRankingUpdated` does not exist, `GET /scanner/state` still recomputes per request, `StrategyScheduler` has no scanner eligibility input, and relay activation does not control strategy evaluation. Scanner scores are activity observations, not authorizations to execute.

### 18.1 Verified inventory and missing connections

| Concern | Current source / behavior | Missing for continuous operation |
|---|---|---|
| Universe | `backend/app/scanner/universe.py`: `DbUniverseProvider` reads `scanner_universe_symbols`; add/remove/list are persistent, format-only operations. `backend/app/api/routes/scanner.py:get_scanner_state` falls back to `TEST_UNIVERSE` on an empty DB universe and accepts a validated `?symbols=` override. | The lifespan-owned observation worker rereads this table each admitted cycle and treats empty as a successful empty result (§18.8, §18.15). An explicit operator action can request feeds for the saved set (§18.16); automated acquisition remains open. |
| Score | `backend/app/scanner/scorer.py:score_symbol` consumes existing 1m `FeatureSet` values; `backend/app/scanner/runner.py:run_scan` reads `FeatureEngine.get_snapshot()` and ranks on demand. It skips symbols with no 1m snapshot, but **includes** a snapshot with zero usable inputs and score zero. `GET /scanner/state?top_n=` cuts only the response. | The observation worker now retains full results, skipped symbols and status (§18.8). A promotion filter remains unapproved. |
| Strategy | `backend/app/strategy_engine/scheduler.py:StrategyScheduler._on_market_state_changed` triggers on every per-symbol `MarketStateChanged` with matching cached features and context, then applies strategy gate conditions. It reads no scanner set. `backend/app/context_engine/engine.py` loads the DB universe at startup and, since `context-universe-hot-add` (§18.9), rereads it after each successful `POST /scanner/universe` to start per-symbol context loops for newly added symbols. Removal never stops a loop. | A narrow **entry-evaluation** eligibility read at the scheduler. The ContextEngine refresh for additions is built (§18.9); a ContextEngine retirement/ownership rule for removed symbols is not. Neither scanner membership nor score may be read by Governor or Execution as authorization. |
| Feed | `backend/app/api/routes/market.py:subscribe` calls the current streaming provider. The explicit `POST /scanner/request-universe-feeds` action requests the saved universe sequentially from that same current owner (§18.16). `FinnhubAdapter`, `PolygonAdapter` and `IBKRAdapter` each own transient subscription sets; there is no central manual-subscription owner or capacity registry. `main.py` connects providers and, after execution restoration succeeds, starts the protected-feed owner (§18.13). It does not automatically subscribe the scanner universe. Finnhub now supervises and restores its own WebSocket requests after an unexpected close. | Automated universe reconciliation, manual ownership for a full-union capacity check, and provider capacity/delivery validation remain open. The active adapter's **local** record is readable (`GET /market/subscription-status`, §18.11); it proves none of those conditions. A separate read-only command counts what actually reaches the backend `/ws` boundary in an explicit window (§18.17); it measures observed events only and sets no threshold. |
| Relay | `backend/app/services/live_tick_relay.py:LiveTickRelay.set_active_symbols` replaces a maximum-eight set; its `PriceUpdated` subscriber emits throttled `PriceSnapshot` only for that set. `POST /market/active-symbols` can set it manually. | Optional scanner-owned chart activation, with a defined interaction with manual relay settings. It is **not** a provider subscription or a strategy eligibility gate. |
| UI | `ScannerPanel` / `useScannerState` poll on-demand `GET /scanner/state`; `useScannerUniverse` edits the DB universe. The Universe tab has a separate manual feed-request action (§18.16). Chart/watchlist subscriptions use `frontend/src/hooks/useLatestPrices.ts` and the market subscribe route; broker-panel subscriptions have their own local UI record. | The scheduled-result read surface (§18.10) becomes available when the explicitly configured lifespan owner starts (§18.15); per-row source feature-candle times are visible (§18.12). Staleness policy and promotion visibility remain open. UI selection is not the scanner's authority. |
| Protection | `PositionMonitor` reads `PortfolioStatePositionReader` and bus-wide `PriceUpdated`/`CandleClosed`, journals ticks before held-position visibility can settle, and has an EOD timer. `SimulatedVenue`, `ReferencePriceTracker` and Portfolio State consume their own tick/order paths. The owner in §18.13 requests feeds for restored simulated open positions and working orders independently of scanner membership. | Confirmed ongoing delivery and capacity remain open. The periodic request may be delayed or fail; monitor journal capacity/loss behavior remains a separate limit. |

The four symbol sets—provider subscriptions, relay chart activation, StrategyScheduler entry eligibility and UI selection—**do not share one runtime registry today**. The persisted scanner universe is shared by the scanner route, ContextEngine's startup load and its add-only refresh (§18.9); an explicit operator action can copy it into local provider requests (§18.16), but it is not a continuously reconciled subscription or eligibility registry. Manual provider subscription and UI selection can indirectly cause data to appear, but neither calls `set_active_symbols` or writes a scheduler gate. `backend/app/core/debounce_scheduler.py:DebounceScheduler` is a bounded event-recompute utility, not a session cadence schedule. `backend/app/core/market_clock.py:MarketClock.current_session` supplies session labels; its OPEN/LUNCH/POWER_HOUR boundaries do not match every clock time in §4's proposed 5/20/90/20/5-second table. Its verified holiday/early-close coverage is 2026–2028; scheduling that must fail closed outside covered years must check `has_calendar_for_year()`.

### 18.2 Minimum simulated-only slice and responsibility boundaries

```
scanner_universe_symbols ──► scheduled owner ──► run_scan ──► recorded current result
          │                       ▲                 ▲                    │
          │                       │                 │                    ├─► observe/status read
          │                       │            FeatureEngine 1m           │
          │                       │                 ▲                    └─► eligibility set
          │                       │          CandleClosed                  │      │
          │                       │                 ▲                      │      ▼
          │                       └── MarketClock  provider ─► TickIngestBridge ─► StrategyScheduler
          │                                │             │                   entry evaluate only
          └──── feed need: score universe ──┘             │
                  + restored positions + working orders  ├─► PositionMonitor / SimulatedVenue
                  + manual provider subscriptions        └─► LiveTickRelay ─► PriceSnapshot (chart)

StrategyScheduler ─► OpportunityCreated ─► existing Governor ─► existing simulated Execution
                                    (no scanner score/eligibility authorization input)
```

The scheduled owner runs only when explicitly enabled for simulated operation. **Manual is the default; an operator explicitly switches to automation only after external feed requirements are met** (decision #189). `off` (no worker or gate), `observe` (scan and record; current strategy behavior unchanged), and `promote` (publish an eligibility set for **new** StrategyScheduler evaluations) are a recommended implementation shape; the switch mechanism and exact gate remain open. The owner subscribes the persisted universe to the provider **before** expecting scores, because FeatureEngine cannot score symbols with no candles. Before enabling automation, verify the active provider's effective subscription capacity and delivery for the **distinct union** of the whole scanner universe, retained manual subscriptions, open-position symbols and working-order symbols—not merely top N or the relay's eight chart symbols. The Core target is about 100, but no real Finnhub ceiling is established; IBKR's documented default of 100 lines cannot be assumed sufficient when manual/protected symbols extend that union. Polygon fallback must satisfy its REST polling budget across the same union. Unknown capacity, rejected subscriptions, stale/missing feed data or an unhealthy connection keep automation disabled or degraded; no silent clipping to fit capacity. A successful scan means the universe read and runner completed, not that every symbol had data. It records `scanned_at`, session, universe revision/snapshot, full ranked results, skipped symbols, eligible set and any subscription/coverage warnings. A persisted result is recommended for restart visibility, but its exact storage schema is an implementation choice within the later approved slice. A cold start with no successful result has an explicit unavailable state, not an empty ranking asserted as current.

Promotion is **an entry-processing selection**, not a new Governor rule. Put a small eligibility reader at `StrategyScheduler` immediately before `evaluate()` for new triggers; `off`/`observe` must remain pass-through. Do not suppress `FeaturesUpdated`, `MarketStateChanged`, `PriceUpdated`, position monitoring, venue fills or exit dispatch for an ineligible symbol. Existing queued opportunities and accepted orders are not revoked when eligibility changes. The full result is recorded before changing eligibility, and an activation failure leaves the last successful eligibility set in place with a visible degraded status. The result read surface must distinguish `last_success_at` from `last_attempt_at` and say whether that result is active or stale. No `ScannerRankingUpdated` event is needed for the minimum slice; if added later, document its consumers rather than assuming the §5 draft event exists.

There is a strategy-specific consequence to measure: `backend/app/strategy_engine/orb_strategy.py` builds its opening range from the 09:30–09:45 ET candles. A symbol that only becomes eligible later cannot retroactively produce an ORB entry for that day. The scanner cannot backfill a missed strategy trigger by score alone. Likewise, a session pause stops fresh ranking, but does not create an after-hours entry prohibition; existing strategy gate conditions and the simulated venue's regular-session guard keep their own documented roles.

`LiveTickRelay.set_active_symbols` remains a chart-fluidity convenience. A scanner write to its wholesale-replace API can overwrite a manual `POST /market/active-symbols` set. The minimum proposal therefore needs an explicit ownership rule before writing it (C4); `promote` can activate at most eight chart symbols, but **chart activation is never the source of strategy eligibility or protective feed retention**. Avoid a scanner-driven provider unsubscribe in this slice. The provider's subscription state is transient and route-level unsubscribes can still remove a needed symbol today; later implementation must either guard those paths or reassert required subscriptions and report any observation gap. Add-only scanner operations do not imply unlimited provider capacity.

### 18.3 Scheduled scan lifecycle

```
start after bus + FeatureEngine + provider connection + restored Portfolio State
  └─ manual default? no automated promotion / unchanged scheduler
     explicit automation switch: verify provider health + full-union capacity
        ├─ unknown/insufficient coverage → stay manual; report blocker
        └─ ready: read clock coverage and DB universe
        ├─ session outside approved window / unsupported calendar → wait; no promotion
        └─ due and idle → mark attempt → read latest universe → ensure feed needs
                         → run_scan (one in-flight cycle only)
                         ├─ exception / coverage failure → record error; retain last success and eligibility
                         └─ success → atomically replace current result
                                      → if promote: calculate approved eligible set
                                      → update scheduler gate; optionally update relay under C4
                                      → set last_success_at → wait until next due boundary
  session transition → recompute next due time; never replay missed intervals
  shutdown → stop new cycles; await/cancel the one in-flight cycle safely;
             prevent late eligibility/relay writes; let normal provider/monitor shutdown follow
```

Use a 60-second scan interval (decision #189), monotonic elapsed time for interval waits, and wall-clock ET only for session windows. A single lock or task owner prevents overlap; a tick while busy is coalesced into at most one later run, not two parallel promotions. Re-read the DB universe at each run, so an edit between scans affects the next run; capture a single immutable universe for that run. A manual on-demand scan neither starts nor resets this timer. A failed DB read, provider subscribe, score pass or activation is isolated and visible; it does not replace the last successful result with partial data. Session close/holiday pauses new scans under the recommended regular-session coverage; existing positions and orders retain feed needs independent of cadence. At restart, prior result is historical until a current scan succeeds; do not silently treat it as fresh entry eligibility. The exact cold-start behavior in `promote` remains a switch-design detail.

### 18.4 Symbol ownership and safe removal

```
DB scanner universe ─► score/feed need ───┐
UI/manual subscription ─► manual feed need ├─► streaming provider subscriptions
restored open positions ─► protected need ┤            │
working entry/exit orders ─► protected need ┘            └─► PriceUpdated / CandleClosed
                                                               ├─► PositionMonitor observations
                                                               ├─► SimulatedVenue fills
                                                               └─► ReferencePriceTracker

scan ranking ─► entry eligibility (may remove a symbol) ─► StrategyScheduler only
scan ranking ─► optional ≤8 chart set ─► LiveTickRelay only
```

Removal from `scanner_universe_symbols` or from top-N ends **future scanner-led entry evaluation only after the next successful eligibility update**. It must not erase a held symbol's provider feed. `PortfolioStatePositionReader.get_open_positions()` supplies held symbols; the existing durable order ledger/Portfolio State in-flight view supplies working entry and exit symbols, including an accepted exit still working after the bell. Reconcile these needs at startup after ledger restoration and on order/position transitions; also retry subscription after provider reconnect. Position Monitor's tick journal may retain an observation while Portfolio State catches up, but it cannot compensate for a provider that stops sending ticks. If a manually issued provider-specific unsubscribe bypasses this owner, the gap must be detected/repaired or explicitly blocked in the implementation slice. A capacity ceiling that prevents adding a protected symbol is a safety failure to surface; it must not be solved by dropping a held symbol. This feed requirement already exists independently of continuous scanning, so it is a prerequisite to safe promotion, not a new exit policy.

For this minimum slice, keep scanner subscriptions additive and never use `provider.unsubscribe()` on a scan demotion/universe edit. Manual subscriptions remain operator-owned; position and working-order observations are protected independently (decision #189). This preserves those symbols but can accumulate subscriptions until provider limits bind. Recheck the distinct-union capacity whenever the universe, manual set, protected set or provider changes; a new need that exceeds capacity is reported immediately, never handled by silently dropping an existing protected feed. A ref-counted ownership ledger is a later capacity remedy only after real provider measurements; it must include manual, scanner, order and position owners and preserve a symbol until every owner releases it. Existing `POST /market/subscribe`, provider-specific subscribe/unsubscribe routes, and `POST /market/active-symbols` retain their request/response contracts; internal coordination must not silently change them. The scanner's scheduled result must not replace `GET /scanner/state`'s on-demand recompute, `?symbols=` override, `top_n`, `skipped`, or empty-DB fallback. Expose scheduled status separately if needed.

### 18.5 File-by-file implementation plan (future approved implementation)

| File/module | Narrow change |
|---|---|
| `backend/app/scanner/schedule.py` (new) | MarketClock-aware due-time calculation for the approved cadence/windows; inject clock and sleep for deterministic tests. No use of `DebounceScheduler` as an implicit wall-clock calendar. |
| `backend/app/scanner/scanner.py` | Observation lifecycle, immutable result/status, no overlap, universe reread and post-read replay admission are built (§18.8, §18.15). Scanner universe subscription and promotion remain future work. Reuse `run_scan` and `DbUniverseProvider`. |
| `backend/app/scanner/runner.py`, `scorer.py` | Reuse unchanged unless a focused test finds a genuine contract gap; keep zero-input rows in on-demand output, filter only at promotion. |
| `backend/app/strategy_engine/scheduler.py` | Inject optional eligibility reader; check before each new strategy evaluation, with pass-through default for off/observe and existing backtest/tests. Do not change `gate_conditions` or opportunity payloads. |
| `backend/app/context_engine/engine.py` | **Built for additions (§18.9, `context-universe-hot-add`):** `refresh_symbol_loops()` rereads the universe and starts loops for new symbols without restarting global calendar work or changing per-symbol context contracts. Retiring loops for removed symbols is not built. |
| `backend/app/services/live_tick_relay.py`, `backend/app/api/routes/market.py`, provider-specific subscription routes | No relay semantic change; coordinate optional scanner chart writes and manual set ownership per C4. Record manual provider needs in a shared, inspectable owner so the full-union capacity check is possible, and coordinate direct provider unsubscribe safety. Preserve route contracts. *Partially informed by §18.11:* a read-only per-adapter local record now exists; it is not that shared owner. |
| `backend/app/portfolio_state/*`, `backend/app/execution_engine/*` | Read existing restored position/working-order state through a narrow adapter; no exit or accounting policy changes. Add transition notifications only if needed to reassert feed need promptly. |
| `backend/app/main.py`, `backend/app/core/config.py` | The observation-only simulated-mode opt-in, explicit session validation and lifespan owner are built (§18.15). Promotion/filter settings, any subscription owner and automated scanner execution remain future work. |
| `backend/app/api/routes/scanner.py` | Keep on-demand state/universe endpoints intact; the distinct scheduled status/results read is **built** (§18.10, `scanner-observation-status`) with no scan-triggering side effect. |
| `backend/tests/test_continuous_scanner.py` and focused integration tests | Cover the acceptance cases below with fake clock/provider/DB and real in-process bus components. Provider trials stay separate. |
| `docs/architecture/scanner-design.md`, `docs/roadmap/phase-roadmap.md`, `CHANGES.md`, `TESTING.md` | Update as-built behavior, approval/decision record as needed, and verification evidence with the code delivery. |

### 18.6 Deterministic acceptance criteria for the future slice

1. **S1 session:** fake ET clock at each approved window's first and exclusive last instant, weekend, holiday, half-day, DST transition and an uncovered calendar year proves exactly the approved due/skip behavior; no after-close catch-up or duplicate run. The chosen schedule is C1.
2. **S2 overlap:** a blocked scan plus multiple timer ticks yields at most one active run and one coalesced follow-up; shutdown blocks late result/eligibility/relay writes and drains/cancels the owner before providers disconnect.
3. **S3 failure:** DB, provider and scorer failures keep the previous result and eligibility, increment failed-attempt state and expose the cause class; startup with no result is explicitly unavailable. A partial provider subscription does not claim full coverage.
4. **S4 edits:** adding/removing a DB-universe symbol between runs changes the next scheduled input exactly once; an edit during a run does not mutate that run's captured input. The on-demand `?symbols=`, `top_n`, `skipped`, fallback and universe CRUD responses remain byte compatible.
5. **EL1 entry:** in promote, only the approved eligible set reaches new `StrategyScheduler.evaluate()` calls; off/observe preserve all current eligible triggers. Missing features/context still skip by existing rules. Zero-input rows do not promote. C2 controls score/N/hysteresis.
6. **EL2 authority:** a symbol losing eligibility does not cancel a queued opportunity, approved order, existing position or exit; Governor/Execution receive no scanner score/eligibility field and do not read the scanner. A test with an already accepted order proves it can still fill after scanner demotion.
7. **EL3 protection:** with a held position and working exit, remove its symbol from universe and top-N; feed subscription remains, bus ticks reach PositionMonitor and SimulatedVenue, and a stop/target observation/close remains possible. Repeat after process restart/ledger restoration and a simulated provider reconnect. Any forced provider unsubscribe is detected/repaired or refused.
8. **UI1 separation:** a manual chart selection and provider subscribe do not change scanner eligibility; a scanner eligibility change does not silently change the selected chart. Relay's eight-symbol limit and manual ownership follow C4.
9. **CAP1 capacity and switch:** with a fake provider cap, automation refuses or degrades when the distinct union of scanner universe, manual subscriptions, open positions and working orders exceeds capacity, even if top N is below the cap. It never drops a protected symbol to fit and never treats an unknown cap as verified. At exactly the verified capacity it proceeds only with successful delivery checks. A provider disconnect, partial subscription failure, stale data or a newly added manual/protected symbol that exceeds the cap is visible and prevents a new promotion; recovery does not silently flip a manual operator setting to automation.

Local doubles and an in-process event bus can prove S1–S4, EL1–EL3, UI1 and CAP1, including ordering and failure injection. They cannot prove the real Finnhub WebSocket symbol ceiling/reconnect, IBKR market-data-line capacity, Polygon REST polling budget (its fallback fetches each subscribed symbol), pre-market feature availability, or 100-symbol real-feed coverage without dropped observations. Those need a configured provider and a measured live session; the synthetic Phase 4 load result (decision #169) establishes only downstream processing capacity. Do not turn a provider trial into a backend-suite requirement for this documentation task.

### 18.7 Documented decisions, recommendations and unresolved choices

**Documented and now confirmed:** Decision #3 originally chose a variable, config-driven scanner schedule. Saqib's later direction in decision #189 chooses a **fixed 60-second interval for the first continuous slice**, correcting #3 for this scope without altering its historical text or the draft table in `system-design.md` §4.7. The latter's N=4 also conflicts with this document's N=8 proposal and relay cap; no N is newly approved here. Decision #189 further confirms manual operation by default, an explicit operator switch to automation only after external feed/capacity obstacles are resolved, and independent retention of manual and protected symbol observations. Scanner scorer weights are as built in `backend/app/core/config.py` (RVOL 1.0, gap/session 0.0, pre-market ratio 0.0). Existing simulated-only execution and protective-exit policies remain in force. Current on-demand scanner, universe CRUD and manual subscription APIs are built contracts.

**Open implementation/product details; recommendations only:**

| ID | Open detail and recommendation | Tradeoff / alternative |
|---|---|---|
| C1 | **Session coverage:** regular session only, keyed to MarketClock's covered calendar. The 60-second interval itself is decided. | Pre-market scans need meaningful pre-market scoring and provider coverage; its current score weight is 0.0. After-hours scans add feed work without a confirmed use. |
| C2 | **Eligibility:** N=8, at least one usable input and score > 0, no hysteresis initially; measure entrants/leavers in observe mode. | Zero-input/zero-score rows otherwise become arbitrary entrants; strict cutoff can flap. K-cycle retention adds policy. N=4 would follow `system-design.md` §4.7 but differs from this doc and the relay's capacity. Neither N changes the full-universe provider-capacity prerequisite. |
| C3 | **Switch mechanics:** recommend `off` / `observe` / `promote`, default off, operator configuration plus restart, and no promotion until a successful current scan. Manual default and explicit operator enablement are decided; the interface/restart behavior is not. | A runtime toggle needs an authorization and transition contract; observe offers quality data while leaving strategy evaluation unchanged. Provider recovery alone must not enable automation. |
| C4 | **Relay/manual coexistence:** recommend scanner relay writes only when its computed set changes, with visible override of a manual relay set. Manual provider subscriptions and protected symbols must be retained by decision #189. | A later scanner write may still replace a manual chart set. A shared owner ledger can preserve manual relay priority and permit safe provider unsubscribe if verified capacity requires it, at greater scope. |

No threshold/top-N, hysteresis, session-coverage, switch-interface, relay-priority or new execution policy is approved by decision #189. `ContextEngine`'s add-side universe refresh is now built (§18.9); its removal/ownership behavior and the missing held/order feed re-subscription remain implementation prerequisites. Neither permits scanner membership to control protective exits.

### 18.8 Tested observation worker core (`scanner-observation-worker`)

`backend/app/scanner/scanner.py:ScannerObservationWorker` is an opt-in, in-process owner with `start()`, `stop()` and `get_snapshot()`. A caller supplies an `eligible()` predicate. The core delivery supplied no production session default; the later lifespan delivery supplies an explicitly configured one (§18.15). The fixed 60-second monotonic cadence is decision #189's first-slice interval. The first eligible due point is immediate at start. Each subsequent due point is anchored to that start; missed boundaries are coalesced into at most one immediate next cycle. An ineligible due point reads no universe and starts no scan.

**Component data flow:**

```
caller: eligible() + start()/stop()      configured scanner weights
                 │                                  │
                 ▼                                  ▼
        ScannerObservationWorker ── due/admitted ──► run_scan (owning event loop)
                 │                                      ▲
                 │ asyncio.to_thread                     │ get_snapshot(symbol)
                 ▼                                      │
        DbUniverseProvider ── worker-owned Session       FeatureEngine 1m cache
                 │
                 ▼
        scanner_universe_symbols

        run_scan ── full ranked rows + skipped ──► immutable ObservationSnapshot
                                                   │
                                                   └─► get_snapshot() (local reader only)
```

The DB read is synchronous SQLAlchemy work offloaded with `asyncio.to_thread`; `DbUniverseProvider` opens and closes its own session inside that thread. The returned symbol list is copied into a tuple before scoring, so an edit after capture affects the next cycle, not the active `run_scan` input. `run_scan` and `FeatureEngine.get_snapshot()` remain on the worker's owning event loop, following the on-demand scanner route's convention. The worker uses configured scorer weights and retains every ranked row (including zero-score/zero-input rows) and every skipped symbol. It applies no `TEST_UNIVERSE` fallback, top-N cut or promotion threshold. An empty persisted universe is a successful empty observation.

**Internal lifecycle:**

```
stopped ── start() ──► running: next_due = monotonic now
                         │
                         ├─ due + ineligible ──► advance to next 60s boundary
                         │
                         └─ due + eligible ──► mark attempt/cycle_running
                                                  │
                                      to_thread DB read → capture tuple
                                                  │
                                      run_scan on owning loop
                                                  │
                           success ──► replace full result + last_success_at
                           failure ──► retain last success + set last_error
                                                  │
                                      advance/coalesce due boundaries

stop() ──► invalidate generation + stop timer ──► drain active DB read/task
                                                   └─► stopped; no late publish
restart ──► new generation + one timer; old completion cannot publish
```

`ObservationSnapshot` is a frozen value: captured universe, ranked rows, skipped symbols, `last_attempt_at`, `last_success_at`, `last_error`, `running` (worker lifecycle) and `cycle_running` (an active admitted cycle). Row feature maps are copied and read-only; callers cannot mutate the retained result. Each row also carries `source_candle_ts`, the candle time of the FeatureSet that supplied its inputs (§18.12). `last_success_at is None` means no cycle has succeeded; a non-null timestamp with empty tuples means a genuine successful empty observation; a non-null `last_error` means the latest attempt failed and the prior success remains visible. A successful cycle clears the error. Snapshots are in memory only; a process restart begins with no successful result. The only HTTP read of this snapshot is the separate `GET /scanner/observation` (§18.10), which reports unavailable unless a reader is installed.

`stop()` blocks new cycles immediately, invalidates the generation before awaiting the owner task, and drains an active offloaded read. Cancelling a stop caller cannot abandon the read: shutdown still waits for the thread to finish, then propagates cancellation. A hung database call can therefore delay shutdown; `asyncio.to_thread` cancellation does not stop its underlying thread. The core delivery included no provider subscriptions, relay writes, strategy eligibility, switch modes or application-startup wiring; §18.15 now supplies only the last of these. C2 promotion thresholds, C4 relay/manual priority and feed-capacity validation remain open before promotion.

### 18.9 Context hot-add for universe additions (`context-universe-hot-add`)

**Problem.** `ContextEngine` loaded `scanner_universe_symbols` once at `start()`. A symbol added through `POST /scanner/universe` was persisted and scored by the on-demand scanner, but got no per-symbol `ContextChanged` or snapshot entry until the next application restart. This delivery closes that gap for **additions only**. It reuses the existing universe selection (`ContextEngine._load_scanner_universe_symbols`, non-backtest symbols), the same providers, the same immediate first evaluation and the same 15-minute per-symbol cadence; the global calendar loop, `evaluate_for_symbol()` output and `get_snapshot()` shape are unchanged. No decision number was needed: §18.5 already named this refresh as a prerequisite under decision #189, and nothing here changes a decided policy (the canonical index, log and archive were checked at base `2a483e4`; the log ends at #189, the archive at #184).

**Component data flow:**

```
POST /scanner/universe {symbol}
   │ asyncio.to_thread
   ▼
add_symbol_to_universe ──commit──► symbols + scanner_universe_symbols      (unchanged)
   │ committed; everything below is a SEPARATE, best-effort step
   ▼
request.app.state.context_engine
   │  None (no lifespan / shutting down) ──► skip; route never calls get_context_engine()
   ▼ running engine
ContextEngine.refresh_symbol_loops() ─ owned task ─► to_thread ─► _load_scanner_universe_symbols
   │                                                                   ▲ same read bootstrap uses
   ▼ ticker list
_track_symbols()  ── already tracked? skip ── stopped / stale generation? create nothing
   │ new symbol
   ▼
_symbol_loop(symbol) ─► evaluate_for_symbol ─► FundamentalsProvider + NewsFlagProvider
   │                         │                          │
   │ every 15 min            ├─► _latest_by_symbol ─► get_snapshot() ─► StrategyScheduler, World View,
   │                         │                                        GET /intelligence/context (readers)
   └─────────────────────────┴─► ContextChanged(symbol) ─► Event Bus ─► WebSocket "intelligence.context"
                                                       (response {"symbol","added":true} returned either way)
Untouched: global _loop (CalendarProvider, session boundaries), every existing symbol loop,
           DELETE /scanner/universe/{symbol}, GET /scanner/state scoring.
```

**Internal refresh and lifecycle flow:**

```
start():  _running=True, generation++ ─► global _loop + bootstrap task (read universe ─► _track_symbols)

refresh_symbol_loops():
   not _running ───────────────────────────────► return []      (never started / stopped / stopping)
   create owned refresh task (captures generation), caller waits with asyncio.wait
        │  caller cancelled ─► owned task keeps going (a committed addition is not lost)
        ▼
   to_thread(universe read)
        ├─ raises ─► logger.exception("ContextEngine universe refresh failed ...") ─► re-raise
        │            tracking untouched; the next refresh retries from scratch
        └─ symbols ─► _track_symbols(symbols, generation)      (synchronous: no await between
                         │                                      "tracked?" and create_task, so
                         │                                      bootstrap + N refreshes serialize)
                         ├─ _running False or generation changed ─► [] (stale completion ignored)
                         └─ create loops only for untracked symbols ─► return the new ones

stop():  _running=False, generation++   (before any await: late reads become no-ops)
         cancel global loop ─► cancel+await bootstrap ─► cancel+await refresh tasks
         ─► cancel+await every symbol loop (including removed symbols') ─► clear
```

**Route contract.** `POST /scanner/universe` returns exactly `{"symbol": <normalized>, "added": true}` as before; invalid tickers still return 400 and trigger nothing. The refresh runs after the commit and any exception from it is logged (`ContextEngine universe refresh failed ...` with traceback from the engine, plus one warning line naming the committed symbol from the route) and swallowed, so a refresh failure never reports a committed addition as failed. The refresh is triggered on every successful POST, including an idempotent re-add of a symbol already present, which doubles as a manual retry. `main.py` publishes the **running** engine as `app.state.context_engine` just before the lifespan yields and clears it first thing at shutdown; without it (tests without a lifespan, shutdown) the route skips the refresh and never instantiates or starts an engine. `GET /scanner/universe`, `DELETE /scanner/universe/{symbol}` and `GET /scanner/state` are untouched.

**Honest limitations.**

- **Add-only.** A symbol removed from the universe keeps its context loop (15-minute evaluations, snapshot entry, `ContextChanged` events) until the engine stops. This is deliberate: other activity may still read that symbol's context and safe ownership/retirement is a separate task. After a restart, removed symbols are no longer bootstrapped. Re-adding a still-tracked symbol creates no second loop.
- **No automatic retry.** A failed refresh is visible only in the log. The next successful `POST /scanner/universe` (even a re-add of the same symbol) or an explicit internal `refresh_symbol_loops()` call retries; there is no periodic reconciliation, no refresh endpoint and no status field. A universe change made any other way (direct SQL, another process) triggers nothing until the next refresh or restart.
- **A failed bootstrap read is still not retried by the engine itself** (unchanged behavior); a later refresh will, as a side effect, track every persisted symbol.
- **A crashed loop is not restarted.** If a symbol's loop task ends with an exception (a provider raising from `evaluate_for_symbol`), its entry stays in the tracked set, so refresh will not replace it, and `stop()` re-raises that exception when it awaits the task. Both behaviors predate this delivery and were left unchanged.
- **The HTTP request waits for one extra universe read** (not for the initial evaluations, which run in the new loop tasks).
- **A read orphaned by `stop()`** (`asyncio.to_thread` cannot interrupt its thread) finishes harmlessly and its result is discarded.
- **Per-process.** Only the process handling the POST refreshes its own engine.
- **Out of scope and unchanged:** provider subscriptions and feed capacity, relay activation, strategy eligibility, observation-worker wiring and scanner scoring. Hot-added context does not mean the symbol is receiving market data.

### 18.10 Scheduled observation status read (`scanner-observation-status`)

**Purpose.** §18.8's worker retains a snapshot that nothing could read over HTTP. This delivery added a **separate, read-only** view of that snapshot: `GET /scanner/observation` and a distinct, collapsible "Scheduled observation" section in `ScannerPanel`. It is independent of `GET /scanner/state` (which still scans on demand, unchanged) and of universe CRUD. At that delivery nothing installed the reader in production; §18.15 now installs it only under the explicit observation opt-in. With the default disabled setting, the response remains `status: "unavailable"`. No decision number was needed for the original read-surface delivery (the canonical index and log were re-checked at base `abeb2b9`: both ended at #189).

**Component data flow:**

```
main.py lifespan ── enabled + execution ready ──► app.state.scanner_observation_reader
                  disabled / blocked / failed ──► slot empty, read is "unavailable"
                                                  ▲
                                                  │ get_snapshot(): one synchronous, I/O-free read
 Browser                                          │
 ScannerPanel ── manual Refresh ──► GET /scanner/observation ──► get_scanner_observation_reader
   "Scheduled observation"                                         (getattr on app.state; never builds
        ▲                                                           or starts a worker)
        └────────── JSON: unavailable | available ◄────────────────────────┘

 Not touched by a read: scanner_universe_symbols / DbUniverseProvider, run_scan, FeatureEngine,
 market-data providers, LiveTickRelay, StrategyScheduler, Event Bus, any DB write or worker state.
 GET /scanner/state and /scanner/universe* are separate routes with unchanged behavior.
```

**Internal read flow (backend):**

```
GET /scanner/observation
   │
   ▼  Depends(get_scanner_observation_reader)   narrow Protocol: get_snapshot() only
reader is None ─────────────► {"status":"unavailable","reason":...,"worker":null,"observation":null}
   │ reader installed (running OR stopped)
   ▼
reader.get_snapshot()          exactly once per request, on the event loop; frozen ObservationSnapshot
   ▼
_project_observation(snapshot)
   ├─ retained       last_success_at is None → "none" │ rows present → "populated" │ else → "empty"
   ├─ latest_attempt last_error set → "failed" │ cycle_running → "in_progress" │ no attempt → "none"
   │                 │ last_success_at >= last_attempt_at → "succeeded" │ otherwise → "interrupted"
   ├─ copies         tuples / read-only mappings → fresh lists and dicts (consumer edits cannot reach
   │                 the worker); non-finite floats → null; last_error capped at 500 characters
   └─ timestamps     any datetime → UTC ISO-8601 with "Z" (naive values are treated as UTC)
   ▼
200 JSON      worker {running, cycle_running}; observation {retained, latest_attempt, universe, results,
              skipped, last_attempt_at, last_success_at, last_error}
```

**Response contract.** `status` is `"unavailable"` (no reader installed; `worker` and `observation` are `null`) or `"available"`. When available, `results` is the worker's **complete** retained ranked set in its order — no top-N cut, no filter, zero-score rows included — and `skipped` its skipped symbols. A reader that is still installed after the worker stopped returns the retained snapshot with `worker.running: false`; if the lifecycle owner later clears the slot to `None` (the convention of the other `app.state` readers), the response becomes unavailable again. The worker's own state machine is unchanged.

| Worker snapshot | `retained` | `latest_attempt` | Meaning |
|---|---|---|---|
| initial, never ran | `none` | `none` | never attempted — not a successful empty result |
| first cycle running | `none` | `in_progress` | nothing retained yet |
| success, no rows | `empty` | `succeeded` | genuine successful empty result (empty universe, or every symbol skipped) |
| success with rows | `populated` | `succeeded` | complete results with `last_success_at` |
| next cycle running | previous | `in_progress` | previous results still served |
| latest attempt failed | previous (`none` if it never succeeded) | `failed` | `last_error` set; prior success and its timestamp retained |
| attempt invalidated by `stop()` | previous | `interrupted` | began, neither published nor failed; stopped worker keeps serving the last success |

`interrupted` goes beyond the three states the task required to be distinguished (never successful, successful empty, failed latest attempt) because the worker really produces it and reporting it as failed or succeeded would be wrong.

**Frontend flow (hook and component):**

```
ScannerPanel
 ├─ tab content: ResultsTab (useScannerState, 15 s poll) │ UniverseTab        (behavior unchanged)
 └─ ScheduledObservationSection   collapsed by default; header button toggles `open`
        open ─► ObservationBody mounts ─► useScannerObservation
                   │ mount: load() = request #1          Refresh click: load() = request #n+1 (never disabled)
                   ▼ fetchScannerObservation ─ GET /scanner/observation
        completion:  requestId === latest ?  apply : discard
           success ─► data replaced, error cleared      failure ─► last data kept, error set
        collapse the section / collapse the panel / unmount ─► body unmounts ─► cleanup bumps the counter
        (re-expanding mounts a new body that loads afresh; no polling, no timer, no WebSocket)
```

The section loads once when expanded and thereafter only on manual Refresh. It shows: unavailable (with the backend's reason and a note that nothing is being scanned on a schedule); worker running/stopped and scan in progress/idle; the latest-attempt notice (failed with its error and, when present, "showing results retained from the last success at <UTC>", interrupted, first scan in progress, never attempted); successful-empty; and populated rows labelled "Activity observations — not execution recommendations", with universe/scored/skipped counts (skipped symbols in a tooltip). Every available state carries the note that worker availability does not establish healthy feed delivery or complete coverage. A failed **request** (HTTP/network) is a distinct notice from a worker whose latest attempt failed. Rows deliberately omit the Results tab's top-rank highlight so they cannot be read as a ranking or recommendation.

**Limitations.**

- **Default unavailable.** The later §18.15 lifespan delivery installs a reader only when observation is explicitly enabled and simulated execution startup is ready. Promotion/top-N (C2), switch mechanics (C3), relay coexistence (C4), provider subscriptions and capacity validation remain open and unbuilt.
- **Per process and in memory.** A restart begins with `never attempted`; a stopped worker's results are only as old as `last_success_at`, and no staleness threshold is applied or implied.
- **`last_error` is the worker's `ExceptionType: message` text,** capped at 500 characters and rendered as plain text; it may contain exception detail from the database layer.
- **Reader failure is not masked.** An exception from a reader's `get_snapshot()` propagates as a 500; the real worker's read cannot raise.
- **Timestamps** in the response are UTC; the panel's "Read" time is the browser's local time of the read. The per-row `source_candle_ts` and the server `read_at` were added later by §18.12.
- **Not proven by this read-surface delivery:** real feed delivery or coverage, a real lifespan install, and real FeatureEngine contents. §18.15 now verifies lifespan ownership and real FeatureEngine reads against controlled local data. The frontend has no committed test runner; its race behavior was exercised with a temporary jsdom harness outside the repository, as in earlier frontend deliveries.

### 18.11 Provider subscription diagnostics (`provider-subscription-diagnostics`)

**Purpose.** §18.1 and §18.4 observe that provider subscriptions are transient, adapter-owned and unreadable, which blocks any later full-union capacity check. The `provider-subscription-diagnostics` delivery added only a **read**: the active streaming adapter's own record of which symbols it has requested, shown by `GET /market/subscription-status` and a collapsible "Subscription diagnostics" section in `BrokerPanel`. The subsequent protected request owner is documented in §18.13. Neither component replaces `GET /market/feed-status` (recorded-candle age for one symbol — a different measurement); capacity and delivery remain unverified under decision #189.

**What the inventory is — and is not.** It is the adapter instance's **local bookkeeping**: a symbol is in it once the adapter's own `subscribe()` call returned without raising (Finnhub: after the WebSocket `send`; IBKR: after qualification and `reqMktData`; Polygon: when added to the polled set) and leaves it on `unsubscribe()`. A successful subscribe call does not establish provider acknowledgement, live tick delivery, ownership by any consumer, or that the account may hold that many subscriptions. The response says so on every call (`basis: "locally_tracked_requests"`, a fixed `note`), and `capacity` and `delivery` are always `{"status": "unknown"}` with no limit: the repository holds no concrete runtime evidence for either (§7's Finnhub ceiling question and §18.6 CAP1 stay open, decision #169 measured only downstream processing), and nothing is inferred from a provider's name or documentation. Because it is per adapter instance, it reflects every caller of that instance (this panel, other tabs, `curl`, provider-specific routes) — unlike the panel's own `subscribedSymbols`, which records only actions taken through that one browser hook and is unchanged.

**Component data flow:**

```
Browser                                         Backend (read path only)
BrokerPanel ── expand section / Refresh ──► GET /market/subscription-status
 "Subscription diagnostics"                            │
        ▲                                              ▼
        │                              broker_registry.get_streaming_provider()   (registry read —
        │                                              │                            never constructs,
        │                                              │                            connects or replaces)
        │                     ┌────────────────────────┼─────────────────────────────┐
        │                  None                  provider present                      │
        │                     │                        │ is_connected()  (local flag / ib.isConnected())
        │                     │                        ▼                              │
        │                     │      FinnhubAdapter ── get_subscription_snapshot() ◄──┤  in-memory copy:
        │                     │      PolygonAdapter ──        (optional capability)   │  tuple(sorted(local set))
        │                     │      IBKRAdapter    ──                                │  no socket, no REST,
        │                     │      anything else ── no method ─► "not supported"    │  no API call
        └──── JSON ◄──────────┴────────────────────────┘
        (unavailable | available) · count · symbols · capacity unknown · delivery unknown

 Not touched by a read: connect / subscribe / unsubscribe / disconnect, TickIngestBridge, Event Bus,
 LiveTickRelay, scanner universe, ContextEngine, candle store, the historical role, execution venue.
 Not replaced: GET /market/feed-status, POST /market/subscribe, POST /market/active-symbols,
 /broker/*, /finnhub/*, /market-data/* (all unchanged).
```

**Internal read flow (route):**

```
GET /market/subscription-status          (async, awaits nothing; one synchronous pass)
  provider = registry.get_streaming_provider()
  ├─ None ───────────────────────────► status "unavailable", reason no_streaming_provider
  │                                      provider null · connected null · inventory unavailable
  └─ present: identity = {id: class attr provider_id | "unknown", class_name}
        connected = is_connected()      raises → null  ─► inventory unavailable: connection_state_unknown
        ├─ not connected ─────────────► inventory unavailable: provider_not_connected
        │                                 (a retained local record is NOT presented as an inventory)
        ├─ lacks the capability ──────► inventory unavailable: inventory_not_supported
        ├─ snapshot() raises, or returns something other than a sequence of str
        │                             ─► inventory unavailable: snapshot_failed   (logged; still HTTP 200)
        └─ ok ─► symbols = sorted(copy)   inventory available: count, symbols (count 0 = genuinely empty)
  every body: basis, capacity unknown, delivery unknown, fixed note
```

**Adapter capability.** `SubscriptionInventory` (`broker_adapters/base.py`) is a `runtime_checkable` Protocol with one synchronous method, `get_subscription_snapshot() -> tuple[str, ...]`. It is deliberately **not** an abstract method of `MarketDataProvider`, so existing implementations and test doubles keep working and a provider that cannot answer honestly simply lacks it. `FinnhubAdapter`, `PolygonAdapter` and `IBKRAdapter` implement it as `tuple(sorted(<their own set/dict>))` and each has a `provider_id` class attribute (`finnhub`, `polygon`, `ibkr`). The tuple is a new immutable object; the adapters' internal collections are never exposed, and a later subscribe cannot change an earlier snapshot. Since §18.13, Finnhub and IBKR clear old-session records on disconnect; Polygon retains its polling set. The route always hides inventory while disconnected.

**Response contract.**

| Field | Values |
|---|---|
| `status` / `reason` | `available`, or `unavailable` + `no_streaming_provider` — whether a streaming provider is registered at all |
| `provider` | `{id, class_name}` (`id` is `unknown` for a provider without `provider_id`) or `null` |
| `connected` | `true` / `false` / `null` (read failed or no provider) |
| `inventory.availability` | `available` or `unavailable` — never an empty list standing in for "unknown" |
| `inventory.reason` | `null`, `no_streaming_provider`, `provider_not_connected`, `inventory_not_supported`, `snapshot_failed`, `connection_state_unknown` |
| `inventory.count` / `symbols` | integer and ascending list when available, else `null` |
| `capacity`, `delivery` | `{status: "unknown", limit: null}`, `{status: "unknown"}` |

**Frontend flow:**

```
BrokerPanel   (connect / disconnect / subscribe form / panel-local list — unchanged, never trigger a diagnostics read)
 └─ SubscriptionDiagnosticsSection   collapsed by default; header toggles `open`
        open ─► SubscriptionDiagnosticsBody mounts ─► useSubscriptionStatus
                   │ mount: load() = request #1          Refresh click: load() = request #n+1 (never disabled)
                   ▼ fetchSubscriptionStatus ─ GET /market/subscription-status
        completion:  requestId === latest ?  apply : discard
           success ─► reading replaced, error cleared     failure ─► last reading kept, error set
        collapse the section / collapse the panel / unmount ─► body unmounts ─► cleanup bumps the counter
        (re-expanding mounts a new body that loads afresh; no polling, no timer, no WebSocket)
```

Distinct states: loading; request failure with nothing loaded (shown as a failure, not as "unavailable"); failed Refresh with the last reading kept and labelled; unavailable with a reason-specific sentence (no provider, not connected, unsupported, read failed, connection unknown; an unrecognized future reason is shown as its code); available-but-empty ("Connected; no subscribe requests are locally recorded"); and populated (count plus symbols in backend order). Every reading is labelled "Locally tracked requests only — not provider acknowledgement, proof of live delivery, ownership or capacity" and shows "Capacity: unknown · Delivery: unknown".

**Limitations.**

- **Local record only.** After a provider-side rejection, silent server drop, or reconnect, the local set can disagree with what the provider is actually streaming; nothing here detects that. A Finnhub `send` that raised partway through a multi-symbol subscribe leaves earlier symbols recorded and the failing one not.
- **Disconnect record.** Finnhub and IBKR clear their old connection's request record on disconnect, allowing a later same-instance connection to receive fresh protected requests (§18.13). Polygon retains its in-process polling set. The route hides all three inventories while disconnected. None of these records proves that a provider actually delivers ticks after a reconnection.
- **Streaming role only.** A provider that holds only the historical role (e.g. Polygon when Finnhub streams) is not reported; Polygon's REST polling budget remains unmeasured.
- **Full-union capacity is still unmeasured.** The read lists one adapter's requests; it does not include open-position or working-order needs (§18.4), and it cannot say whether the account may hold the §18.2 union.
- **No refresh coupling.** The section reads on expand and on manual Refresh only; subscribing from the panel does not update it until Refresh.
- **Not proven here:** any real Finnhub, Polygon or IBKR session. Tests use the real adapters over fake transports; the frontend has no committed test runner and was exercised with a temporary jsdom harness outside the repository, as in earlier frontend deliveries.

### 18.12 Source feature-candle timestamps on the observation (`scanner-observation-source-timestamps`)

**Purpose.** A scan that completed a moment ago can still have scored feature data from a candle that closed days earlier: `last_success_at` (§18.10) says when the scan finished, not what data it saw. This delivery carries the 1m FeatureSet `candle_ts` that actually supplied each row's score inputs into the retained observation, serves it as an additive per-row `source_candle_ts` on `GET /scanner/observation`, and shows it in the "Scheduled observation" section next to the last-success time. It is **descriptive visibility only**: no staleness threshold, no eligibility gate, no provider subscription, no lifecycle startup, no cadence/session change, no scoring change and no promotion. Open items C1–C4 (§18.7) are unchanged; visible timestamps do not establish adequate coverage and do not authorize automation. No decision number was needed: the delivery adds read-only fields and changes no decided policy (the canonical index and log were re-checked at base `79041bc`: both end at #189, the archive at #184).

**Component data flow (source snapshot to observation):**

```
FeatureEngine._latest[(symbol,"1m")]  FeatureSet{candle_ts, close, features}   (replaced by the engine on every closed candle)
        │  get_snapshot(symbol)  — ONE read per symbol per scan; returns fresh dicts, candle_ts as ISO string
        ▼
run_scan (owning event loop)
   tf_data ──► FeatureSet(candle_ts=tf_data["candle_ts"], features=tf_data["features"])      one object, built once
                  ├──► score_symbol(...)  ──► score, inputs_available                       scoring inputs
                  └──► ScanResult.source_candle_ts = feature_set.candle_ts                  same object ⇒ same snapshot
   no 1m row at all ──► symbol skipped (no ScanResult, no timestamp)
        │  (results, skipped)
        ▼
ScannerObservationWorker._cycle
   ObservedScanRow(symbol, score, inputs_available, read-only features, source_candle_ts)
        ▼  success only
immutable ObservationSnapshot  ◄── failed/interrupted later cycle changes only error/attempt fields;
        │                           the retained rows (and their source timestamps) are the same objects
        ▼  get_snapshot(): one synchronous read per request
GET /scanner/observation  ──► per row source_candle_ts (UTC "Z" | null) + top-level read_at (server clock)
        ▼
ScheduledObservationSection ── per-row "Source candle <UTC> · age <d h m s> at server read" | "time unknown"

 Not involved: a second FeatureEngine read, GET /scanner/state (unchanged), providers, LiveTickRelay, StrategyScheduler,
 scanner_universe_symbols (read only by the worker's cycle as before), any write.
```

**Internal capture and projection flow:**

```
run_scan, per symbol                              worker cycle                         route projection (_project_observation)
snapshot = engine.get_snapshot(symbol)            results ─► rows tuple                row.source_candle_ts
tf_data = snapshot[symbol]["1m"] │ None ─► skip   getattr(r,"source_candle_ts",None)    ├─ None ─────────────► null  (unknown)
feature_set = FeatureSet(...tf_data...)           (a scan double without the field     ├─ naive datetime ───► treated as UTC, "…Z"
   (ISO string ─► datetime; unparseable is the    yields None, never an error)         ├─ aware datetime ───► converted to UTC, "…Z"
    scan's existing failure, unchanged)           success ─► new snapshot              └─ future vs read_at ► reported as stored
score = score_symbol(symbol, feature_set, ...)    failure ─► replace(last_error, …),      (no clamp, no comparison server-side)
ScanResult(..., source_candle_ts=                            rows untouched          read_at = clock() immediately after the single
            feature_set.candle_ts)                                                    get_snapshot(); also present when unavailable
sort by score (unchanged)
```

Frontend row rendering follows one decision list: no timestamp → "Source candle time unknown"; unparseable → shown verbatim as an unreadable timestamp (as text, never interpreted); no usable `read_at` → time shown with "age unavailable (no server read time)"; source later than `read_at` → time shown with "later than the server read time — age not shown" (never clamped to zero or labelled recent); otherwise "age <largest two units> at server read". The section also shows "Scan completed <last_success_at> · server read <read_at>", the earliest–latest known source candle across the rows with a count of unknown ones, and a note that scan completion time and data time differ.

**Capture rule.** The timestamp is read from the same `FeatureSet` object that was built from the single `get_snapshot(symbol)` row and then scored, so it cannot be paired with inputs from a different snapshot even if the engine replaces its state between reads, between the read and scoring, or between symbols (each symbol is internally consistent; symbols may legitimately come from different engine moments, exactly as before). `run_scan` performs no additional FeatureEngine read. Scores, ordering, zero-input rows and skipped-symbol behavior are unchanged.

**Compatibility.** `ScanResult` gains `source_candle_ts: datetime | None = None` as its last, defaulted field, so existing positional and keyword constructors work and mean "unknown". `ObservedScanRow` gains the same defaulted field; the worker copies it with `getattr(..., None)` so an injected `scan` double that predates the field is reported as unknown rather than raising. `GET /scanner/state` builds its rows from explicit keys and is byte-for-byte unchanged (no `source_candle_ts`, no `read_at`). On `GET /scanner/observation` both additions are additive: a per-row `source_candle_ts` (ISO-8601 UTC with `Z`, or `null`) and a top-level `read_at` (ISO-8601 UTC, also present in the `unavailable` response). The frontend types mark both optional so an older backend renders as "unknown"/"age unavailable".

**Source age — what it can and cannot say.** The age shown is `read_at − source_candle_ts`, measured against the **server's clock at the moment of that response's snapshot read**, not the browser's clock and not a live counter; a later Refresh produces a new `read_at` and a new age. It describes how old the scored candle was at that read and deliberately carries no verdict:

- A candle time is old when the market is closed, a symbol is quiet or halted, a symbol is not subscribed or not receiving ticks, or the feed is degraded — the timestamp cannot tell these apart. An old time is not proof of a feed fault, and a recent time is not proof of healthy delivery, adequate coverage or a complete universe.
- It reflects only the 1m FeatureSet; other timeframes and the daily/context inputs are not represented.
- Rows from one scan can come from different candles; the earliest–latest range is descriptive, not a quality score.
- The retained result's age keeps growing between scans and while a stopped worker serves its last success, so a long-stopped worker simply shows old source times and an old `last_success_at`.
- A future source time (clock skew or a bad value) is shown as stored with "age not shown"; it is never clamped into apparent freshness.
- The codebase's existing convention treats a naive datetime as UTC. `FeatureEngine` itself normalizes a naive incoming `candle_ts` to UTC before computing (`engine.py`), so published source times are timezone-aware; the route's naive-as-UTC rule only matters for a hand-built value.
- No threshold, color, badge or "stale"/"fresh" classification exists here by design: choosing one is a policy decision tied to the open session and coverage questions (C1, C2) and was not part of this task.

**Tests and validation** are recorded in `TESTING.md` (`scanner-observation-source-timestamps`): the real `run_scan` over a real `FeatureEngine` with controlled snapshots, the real worker and routes, and a temporary jsdom harness for the real `ScannerPanel` (the repository still has no committed frontend test runner).

**Limitations.** At the source-timestamp delivery, production reported "unavailable" because nothing installed the worker. The later §18.15 opt-in can now install it. Only the 1m source candle is captured; no history of earlier source times is retained beyond the current snapshot; the per-process, in-memory limits of §18.10 are unchanged.

### 18.13 Protected feed reconciliation (`protected-feed-reconciliation`)

Decision #189's protective feed requirement is now an application-owned subscription request loop, separate from the unstarted scanner observation worker. It starts only after the simulated execution pipeline restores Portfolio State, reconciles its venue without a discrepancy, hydrates the Position Monitor and reaches `ready`. A blocked or failed execution startup does not start it and remains blocked or failed. It reads the authoritative `positions` and `orders` tables directly through a narrow read-only projection: positive-quantity simulated positions in `open` or `closing`, and simulated orders in the existing non-terminal set `approved`, `submitted`, `partially_filled`, `unknown` (`portfolio_state.reconciliation.NON_TERMINAL_ORDER_STATUSES`). Closed positions and terminal `filled`, `cancelled`, `rejected`, `expired` orders are excluded. The distinct union is independent of `scanner_universe_symbols`, scanner ranks, chart relay activation and manual subscriptions.

**Component data flow:**

```
committed simulated entry/exit order or position ─► synchronous wake signal ─┐
registry takeover / current Finnhub reconnection ─► synchronous wake signal ─┤
60-second fallback timer ──────────────────────────────────────────────────────┤
                                                                             ▼
positions (simulated, open/closing, qty > 0) ─┐                    ProtectedFeedReconciler
                                               ├─► read_protected_symbols ──────┤
orders (simulated, non-terminal) ───────────────┘          (owned DB session)          │
                                                                                       ▼
current broker_registry streaming provider ─► local inventory ─► missing request symbols
                                                            │          │
                                                            │          └─► provider.subscribe([symbol])
                                                            ▼
                                                TickIngestBridge ─► PriceUpdated
                                                                      ├─► PositionMonitor
                                                                      └─► SimulatedVenue
```

**Internal reconciliation flow:**

```
execution ready ─► start one owner ─► immediate cycle ─► wait for wake or 60 seconds ─► repeat
                                  │
                                  └─ worker thread opens/closes DB session
                                     ├─ read fails ─► log, retry later (never infer empty)
                                     └─ distinct symbols ─► current registry identity
                                        ├─ missing/disconnected ─► skip, retry later
                                        └─ optional local inventory ─► request absent symbols one by one
                                           ├─ failure ─► log, continue; retry later
                                           └─ success ─► request recorded locally, delivery still unknown
shutdown ─► stop/cancel and settle owned cycle ─► provider disconnect

request_reconcile(): if running, set one pending flag; no DB/provider work and no task
cycle start: clear flag BEFORE reading ─► wake during blocked work survives
cycle end: pending flag gives ONE follow-up; repeated requests coalesce
failed/deferred cycle: at least min(1 second, interval) before a wake-driven retry
stop: unregister callback ─► reject late wakes ─► cancel and settle owner
```

The loop has one in-flight cycle; additional attempts during it are skipped. Every cycle rereads the ledger and the current streaming role, so a replacement receives its own requests. Finnhub retains desired symbols across an unexpected socket loss but clears its current-socket inventory, then restores desired requests through its adapter-local retry owner (`system-design.md` §4.2). Manual disconnect clears both sets. IBKR discards old-session local bookkeeping when disconnected; Polygon retains its in-process polling set. Providers without usable inventory receive repeated requests under the adapter's idempotent subscription behavior. Inventory only avoids unnecessary calls: neither its presence nor a returned `subscribe()` establishes provider acknowledgement, capacity or continuing tick delivery. The loop never unsubscribes, so shrinking needs leaves manual and earlier requested symbols alone. A provider switch during a request is rechecked before the next symbol; the next cycle targets the new role.

`protected-feed-event-wake` adds a synchronous, nonblocking signal to this same owner. Execution signals only after the new simulated entry-order insert, new close-order reservation, or positive open/closing position commit returns. `OrderApproved` precedes the order row and `OrderFilled` precedes the position commit, so those bus events cannot prove the protected query will see the change. Provider installation signals after the registry role changes; Finnhub signals after a same-instance reconnect restores its desired requests, and only if it is still the registry owner. The owner always rereads the authoritative ledger and the **current** provider; callbacks do no query or subscription work. Signals are in-memory and can be lost across process failure or a narrow commit-to-notification window. A query already in flight can predate a commit; the pending wake then causes one follow-up if delivered. The 60-second interval still starts after a completed cycle and recovers missed signals, failures and later state changes. Failed or deferred cycles enforce a short cooldown against event-driven retry storms. A wake reduces request delay but cannot guarantee instant or continuous protection.

Finnhub reconnects after an unexpected WebSocket close and restores retained desired requests; a symbol first needed during the outage is requested after the reconnection wake (or a later periodic cycle). Verified capacity across the full manual/scanner/protected union, provider acknowledgement and continuous delivery remain prerequisites outside this slice. Polygon's free-tier feed remains delayed. Existing direct unsubscribe routes can still create a gap until a later cycle. The owner's last attempt is readable through §18.14.

### 18.14 Protected feed reconciliation status (`protected-feed-reconciliation-status`)

§18.13's owner made protected-symbol subscription requests, but its failures were visible only in logs. This slice adds a **read-only status view of the owner's last attempt**. It added no policy at delivery: decision #189, the original 60-second cadence, the symbol-selection rules, the retry behavior and the shutdown ordering were unchanged, and no new decision number was needed then. The later `protected-feed-event-wake` adds prompt signals while retaining this snapshot's fields and outcome meanings. The unresolved scanner policies (Top-N/filter, switch details, capacity verification, operator-enabled automation) stay open.

**What it records.** `ProtectedFeedReconciler.get_snapshot()` returns a `ProtectedFeedSnapshot`: a frozen dataclass whose fields are scalars, datetimes and tuples only (no list, dict or set), so a consumer cannot mutate the owner's state. It carries: running and cycle-in-progress flags, the configured interval, attempts started/finished, latest attempt start and finish times, the latest cycle outcome, the latest read attempt and its outcome, the **last successful** protected-set read (its own time and sorted symbols), the provider seen by the latest lookup and its connection state, and a retained per-symbol `RequestRecord` (the cycle's start time, provider identity, whether the provider's local inventory was readable, and one outcome per symbol).

**Distinctions made explicit**

| Condition | Where it shows |
|---|---|
| No reconciler installed | route `status: "unavailable"`, `reason: "reconciler_not_installed"` (reader absent from `app.state`) |
| Installed, never attempted | `reconciler.state: "never_attempted"`, no timestamps, `protected_set.availability: "never_read"` |
| First attempt still running | `reconciler.state: "first_attempt_in_progress"` |
| Protected-set read failed | `reconciler.state: "protected_set_read_failed"`, `protected_set.latest_read: "failed"` with the fixed code `protected_set_read_failed`; nothing was requested |
| No / disconnected / unreadable streaming provider | `no_streaming_provider`, `provider_disconnected`, `provider_check_failed` |
| Successful empty set | `protected_set.availability: "read"`, `symbols: []`, `count: 0`, `latest_read: "succeeded"` |
| Locally present subscription | per-symbol `locally_present` (found in the adapter's own record; no request made) |
| Request returned | per-symbol `request_returned` (`subscribe()` returned without raising) |
| Request failed | per-symbol `request_failed` with a coarse `error_class` (`timeout`, `connection`, `other`) |
| Cycle ended before a symbol had a result | per-symbol `no_outcome`; cycle state `interrupted` or `provider_disconnected` |

**Retention rules.** A later failed database read never overwrites the last successful read and never appears as an empty set: the route returns the retained symbols with their own `read_at` and a separate `latest_read: "failed"` marker. Likewise the request outcomes are replaced only by a cycle that reached the request step (provider present and connected); a later cycle that failed earlier leaves them in place, labelled with their own cycle time and provider. Reading status never changes any of this.

**Evidence wording.** `locally_present` and `request_returned` are request evidence only. The route and panel never call them verified delivery, acknowledgement or confirmed protection. They explain that committed exposure and provider changes prompt a re-check, the periodic interval recovers misses, and a cycle or outage can still delay requests. This still does not establish continuous tick delivery or capacity for the full manual/scanner/protected union.

**Safe errors.** The route carries fixed codes and coarse classes only. Raw exception text (which can contain connection strings or credentials) stays in the backend logs, unchanged from §18.13.

**Component data flow:**

```
main.py lifespan ─ owns ─► ProtectedFeedReconciler ─ installs after start ─► app.state.protected_feed_status_reader
        │                         │                                                     │ (cleared before the owner stops,
        │                         │ cycle writes (event loop only)                      │  on rollback, and in the final finally)
        │                         ▼                                                     ▼
        │              ProtectedFeedSnapshot (frozen)  ◄── get_snapshot() ◄── GET /market/protected-feed-status
        │                                                   (sync, I/O-free)      │  one read; projects to a fresh dict
        ▼                                                                         ▼
 provider disconnect (after owner stop)                              BrokerPanel "Protected feed" section
                                                                      (manual Refresh · useProtectedFeedStatus)
                                       beside the existing "Subscription diagnostics" section
```

The status route never starts a cycle, reads the database, subscribes or touches a provider; it does not take the reconciler's lock.

**Internal snapshot flow:**

```
reconcile_once ─► (stopping or lock held? skip: not an attempt) ─► take lock
   ├─ snapshot ◄─ cycle_in_progress, attempts_started+1, last_attempt_at
   ├─ _cycle(attempt)        _Attempt = private mutable accumulator (never exposed)
   │     ├─ read ──fails──► attempt.read_failed ; outcome protected_set_read_failed
   │     ├─ read ──ok────► attempt.read_symbols (empty set is a real result)
   │     ├─ provider lookup ─► none │ check failed │ disconnected ─► outcome set, stop here
   │     └─ inventory ─► per symbol: locally_present │ request_returned │ request_failed(class)
   │           └─ stopping / provider replaced / disconnected mid-cycle ─► rest = no_outcome
   └─ finally _record(attempt)   (also on cancellation, as "interrupted")
         builds ONE new frozen snapshot and swaps the reference (no await in between)
         ├─ last_set_read replaced only by a successful read
         └─ request_record replaced only by a cycle that reached the request step
get_snapshot() ─► copy of the current reference with `running` filled in ─► consumer
```

**Frontend.** `BrokerPanel` gains a distinct collapsible "Protected feed" section next to "Subscription diagnostics". It loads once on expanding and on each manual Refresh (no polling), shows protected symbols with the time of their last successful read, the latest attempt start/finish times, the provider and its connection state, and per-symbol outcomes. Initial loading, an HTTP failure, a backend "unavailable" answer and a worker failure state are shown differently. After a failed Refresh the last reading stays on screen with its server read time and a notice; a later success clears it. Each request is numbered so a superseded response is ignored, and unmounting (collapsing the section or panel) invalidates anything in flight. Existing connect, disconnect, subscribe and diagnostics behavior is unchanged.

### 18.15 Explicitly enabled observation lifespan (`scanner-observation-lifespan`)

The application starts the existing `ScannerObservationWorker` only when `SCANNER_OBSERVATION_ENABLED=true`, a nonempty list of selected session labels is configured, and the simulated execution startup reaches `ready`. The default is disabled. Valid labels are `pre_market`, `open`, `lunch`, `power_hour` and `after_hours`; `closed`, duplicates and unknown labels are rejected at settings validation. `execution_mode` remains simulated-only. The worker keeps its fixed 60-second monotonic cadence from #189, scores the persisted Core universe using the **same lifespan-owned FeatureEngine** cache that on-demand scanning reads, and retains results for the existing read-only route. No second intelligence pipeline, universe subscription, strategy gate or relay activation is created.

An exact opt-in `.env` example for the three regular-session labels is:

```dotenv
EXECUTION_MODE=simulated
SCANNER_OBSERVATION_ENABLED=true
SCANNER_OBSERVATION_SESSIONS='["open","lunch","power_hour"]'
```

This example selects those three labels explicitly; it does not imply that pre-market or after-hours is covered. The MarketClock owns label boundaries, holidays and early closes. Each due point checks the selected label **and** `has_calendar_for_year()` for its Eastern trading year; an uncovered year fails closed even though MarketClock's generic session methods can approximate it. A holiday or the period after a half-day close is `closed` and is never admitted. Ineligible due points do no universe read or scoring.

**Component startup and data flow:**

```
validated Settings ── disabled ─────────────────────────────► reader = None
        │ enabled + explicit sessions + simulated startup ready
        ▼
main.py lifespan ── owns one ScannerObservationWorker ──► app.state.scanner_observation_reader
        │                         │                                    │
        │                         ├─ to_thread ─► DbUniverseProvider ─► scanner_universe_symbols
        │                         └─ run_scan ─► lifespan FeatureEngine 1m cache
        │                                           │
        │                                  retained ObservationSnapshot
        │                                           │
        └─ stop and clear ◄─────────────────────────┴── GET /scanner/observation ─► ScannerPanel

GET /scanner/state stays on-demand; provider subscriptions, StrategyScheduler and LiveTickRelay receive no scanner writes.
```

**Internal admission and shutdown:**

```
60-second due point
  ├─ replay/connection singleton slot busy ─► skip (no universe read)
  ├─ uncovered ET year / unselected or closed session ─► skip
  └─ admitted ─► offloaded universe read ─► slot + session + calendar recheck
                                            ├─ busy / ineligible / stopping ─► no scoring or publication
                                            └─ free ─► synchronous run_scan on owning loop
                                                       └─ retain full result + source candle timestamps

startup blocked/failed ─► no worker, no reader
worker start fails ─► stop partial worker, leave reader empty; execution stays ready
shutdown/rollback ─► clear reader ─► stop worker and drain read ─► stop FeatureEngine/providers
```

The post-read admission check sits immediately before synchronous scoring. It repeats the selected-session and covered-year predicate as well as the replay-slot check, so a session ending during the read cannot publish an out-of-session observation. Scoring has no await, so a replay cannot install its temporary process-wide FeatureEngine between that check and the scan. A replay that begins during the offloaded read causes that attempt to publish nothing; a later admitted cycle can use the restored live singleton. This uses the existing replay/connection slot and leaves replay serialization unchanged. The read thread can delay shutdown if it hangs, as §18.8 already documents. Results are process-local observations of **existing** FeatureEngine data, not fresh data acquisition; a symbol without a 1m FeatureSet is skipped. Enabling this service does not subscribe the universe or verify provider capacity, acknowledgement, delivery, coverage or data freshness. C2 promotion thresholds and C4 relay/manual priority remain open, as do automated universe feed acquisition and automated promotion. The separate manual action is §18.16.

### 18.16 Manual saved-universe feed requests (`scanner-universe-feed-request`)

The operator may press **Request universe feeds** on the Scanner Universe tab. The backend captures the persisted `scanner_universe_symbols` set using `DbUniverseProvider` in an offloaded, session-owning read. An empty saved set is a successful empty batch; unlike on-demand `GET /scanner/state`, this action never falls back to `TEST_UNIVERSE`. A failed read returns a fixed 503 detail. A missing or disconnected current streaming provider returns a fixed 409 detail. No provider is constructed, connected, installed or removed.

**Component data flow:**

```
operator ─► ScannerPanel / Universe tab ─► POST /scanner/request-universe-feeds
                                               │
                                               ├─ to_thread ─► DbUniverseProvider ─► saved scanner_universe_symbols
                                               │                                (owned Session, read only)
                                               └─ registry current streaming owner ─► SubscriptionInventory (optional)
                                                                                  └─ subscribe([symbol]) sequentially
                                               │
                     captured universe + provider identity + per-symbol local outcomes
                                               ▼
                                    Universe tab result snapshot

GET /scanner/state and scheduled observation ─► scoring existing FeatureEngine data
StrategyScheduler / LiveTickRelay ◄───────────── no writes from this action
```

**Internal capture, request and result flow:**

```
POST ─► batch lock busy? 409 ─► replay/connection slot busy? 409
  └─ one batch owner ─► offloaded saved-set read ─► error? 503
                         │
                         └─ replay slot recheck ─► hold existing replay/connection lock
                               ├─ current provider absent/disconnected? 409
                               └─ for each captured symbol, in saved order:
                                    owner/connection recheck ─► changed? remaining = not_attempted
                                    current local inventory contains symbol? ─► locally_present
                                    otherwise await subscribe([symbol]) ─► request_returned
                                                                  └─ raises ─► request_failed(class)
                               └─ return completed | partial_failure | interrupted, release locks
```

A duplicate batch gets 409 immediately; there is no per-symbol task or queued batch. A provider takeover or disconnect stops later requests to that old instance and returns `not_attempted` for the remainder. A request already inside `subscribe()` can finish while takeover occurs; the next symbol rechecks ownership. Each result keeps the **captured** universe even if an edit commits during the batch. The UI guards same-tick repeated clicks, shows pending and partial outcomes, and ignores a late response after the Universe tab unmounts; already-sent provider requests are not cancelled by a tab switch. Inventory hits and returned `subscribe()` calls are local request evidence, not provider acknowledgement. Error classes are fixed (`timeout`, `connection`, `other`); raw exceptions and credentials are not returned. No unsubscribe, universe write, chart activation, promotion or scanner eligibility change occurs.

Manual workflow: explicitly configure observer sessions (§18.15) if scheduled observation is wanted → connect a streaming provider → press **Request universe feeds** for the saved universe → inspect `GET /market/subscription-status` and the observation source-candle times (§18.11–§18.12). This is a point-in-time request, not automated reconciliation. Subsequent universe edits, provider replacement and disconnected sessions require another operator request unless an adapter's own reconnection behavior restores its desired set. Verified full-union capacity, real delivery, automated universe reconciliation, C2 promotion thresholds and C4 relay/manual priority remain unresolved.


### 18.17 Streaming coverage measurement (`streaming-coverage-measurement`)

`backend/scripts/measure_streaming_coverage.py` (logic in `backend/app/measurement/streaming_coverage.py`) observes a **running** backend from outside and reports which monitored symbols produced ticks, closed 1m candles and 1m feature updates on the existing `/ws` channels `market.tick`, `market.candle` and `features.updated` during an explicit window. It is an operator tool, not part of the live pipeline: it adds no route, registry entry, bridge, provider call or trading policy. Its only effects on the backend are one additional WebSocket that subscribes to those three existing outbound channels, and read-only GETs of `/market/subscription-status` (beginning and end) plus `/scanner/universe` (only with `--scanner-universe`). It never connects a provider, requests feeds, edits the universe or starts trading.

**Operator workflow.** The command observes; it does not acquire. Feeds must first be requested with the existing manual action, then measured:

```bash
# 1. backend running with a streaming provider connected (existing steps, e.g. Broker panel / Finnhub connect)
# 2. request the saved universe's feeds with the existing manual action (Universe tab "Request universe feeds") ...
curl -X POST http://127.0.0.1:8000/scanner/request-universe-feeds
# 3. ... then measure an explicit window (from backend/):
python scripts/measure_streaming_coverage.py --backend-url http://127.0.0.1:8000 \
    --scanner-universe --duration 600 --json-report ./coverage-report.json
python scripts/measure_streaming_coverage.py --backend-url http://127.0.0.1:8000 \
    --symbols AAPL,MSFT,NVDA --duration 300
```

Exactly one of `--symbols` and `--scanner-universe` is required. `--duration` is seconds (> 0). `--json-report PATH` writes the full report (atomic replace; the path is checked before the window starts). `--table-rows N` limits console rows (default 50, `0` = all). Credentials in the URL (`http://user:pass@host`) are sent as Basic auth and never printed or stored; query strings and fragments in the URL are rejected. HTTP reads ignore proxy environment variables. Exit codes: `0` completed window (zero events included), `2` invalid input, `3` failed setup/precondition, `4` interrupted or incomplete (connection loss, SIGINT/SIGTERM, cancellation), `5` JSON report could not be written. A report (console and, when requested, JSON) is produced for every outcome after input validation, including failed setup and interruption.

**Component data flow:**

```
operator ─► measure_streaming_coverage.py ─► Measurement (one run)
                 │                               │
                 │  GET /scanner/universe  ◄─────┤ (only --scanner-universe; captured ONCE; empty = precondition failure)
                 │  GET /market/subscription-status ◄── beginning snapshot, and again after the window
                 │                               │
                 │   /ws: {"action":"subscribe","channel":C}  for C in market.tick, market.candle, features.updated
                 │   backend replies {"channel":"_meta","subscribed":C}   (window starts after the LAST ack)
                 ▼
 running backend:  provider ─► TickIngestBridge ─► EventBus ─► WebSocketGateway ─► ConnectionManager ─► this socket
                   (all untouched; the measurement socket is one more subscriber of three channels)
                 │
                 ▼
        console table  /  optional JSON report (explicit path)
```

**Internal collection and report flow:**

```
build_config ─► invalid? exit 2 (before any connection)        capture monitored set once (explicit | saved universe)
      │                                                          └─ empty/unreadable? failed_setup, exit 3
      ▼
start diagnostics ─► unreadable? failed_setup, exit 3 ─► connect /ws ─► send 3 subscribes ─► reader task
                                                                            │
 reader: recv ─► CoverageCollector.process(raw, monotonic_now, utc_now)   (synchronous, bounded)
   setup phase : _meta ack ─► latency (monotonic); events before the last ack ─► pre_window counters only
   last ack    : window_start = monotonic_now; deadline = start + duration
   measuring   : after deadline ─► post_window_ignored
                 invalid JSON / not object / missing channel,symbol,payload / bad timeframe / bad source ts ─► malformed(kind)+sample
                 candle|features with timeframe != 1m ─► other_timeframe counter
                 symbol not in captured set (exact match) ─► unmonitored counter + ≤20 sample symbols
                 otherwise per (symbol, category): count, first/last SOURCE ts, first/last RECEIPT (monotonic offset + UTC label),
                 max inter-arrival gap, source-ts regression / duplicate counters
 main wait: first of  deadline reached (completed) | stop/signal (interrupted) | reader ended (interrupted, connection_lost)
      ▼
close /ws (code 1000) ─► end diagnostics (failure recorded, not fatal) ─► build_report ─► render console / write JSON ─► exit code
```

**What is recorded.** Per monitored symbol and category (`ticks`, `candles_1m`, `features_1m`): `count`, `first_source_ts`/`last_source_ts` (the event's own time: tick `exchange_ts`, candle/feature `candle_ts`, which is the bar's open time), `first_received_offset_s`/`last_received_offset_s` (seconds since window start on a monotonic clock), `first_received_utc`/`last_received_utc` (wall-clock labels only), and `max_gap_s`. Source time and receipt time are never mixed, and no latency is computed from the two clocks. The report lists the captured monitored set, symbols with no observed event per category and in all categories, totals, per-channel acknowledgement latency, malformed counts by kind, filtered other-timeframe, unmonitored, unexpected-channel, `_meta`-error, pre-window and post-window counters, source-timestamp regression/duplicate counters, and the connection outcome (interruption flag, close code/reason, offset). Beginning/end diagnostics keep provider identity (`id`, `class_name`), `connected`, the inventory's availability/reason/basis/count and how many monitored symbols appear in that local record; capacity and delivery are not copied or inferred. State is bounded: three aggregates per monitored symbol, at most 20 anomaly samples (excerpts cut to 120 characters), at most 20 unmonitored sample symbols and 16 distinct labels per counter; raw ticks are never retained.

**Interpretation limits.** Counts are events observed at the backend WebSocket boundary in one window. They do not prove provider capacity, lossless upstream delivery, profitability or full protective coverage, and they support no "100 symbols, no dropped ticks" claim. Zero events can mean a quiet symbol, a closed market, a missing subscription or a fault; the tool never classifies the cause, and a completed zero-event window is a successful measurement (exit `0`). A closed 1m candle or feature update can occur at most once per symbol per minute, so a very short window can legitimately show none even when ticks flow. The beginning and end subscription snapshots are two reads and cannot show that no provider change occurred between them; an unreadable end snapshot is recorded without failing the window, whereas an unreadable beginning snapshot fails setup. No automation-enablement threshold is defined here, and C2 promotion, C4 relay priority, verified full-union capacity and automated acquisition remain open (§18.16). Verification so far is synthetic: a controlled local WebSocket/HTTP server, plus the production gateway/route code under uvicorn with a provider double (`TESTING.md`). No real-feed trial has been recorded.
