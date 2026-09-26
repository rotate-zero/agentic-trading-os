# Market Activity Scanner (Core Tier) — Design & Implementation Plan

**Status:** DRAFT — not yet confirmed; partial implementation exists. Built: `ActivityScorer`, the on-demand `run_scan` path, persisted/editable Core universe and scanner routes, plus `ScannerPanel`, `useScannerState`, and `useScannerUniverse`. Not built: continuous `MarketActivityScanner`, `ScanCadenceSchedule`, top-N promotion into `StrategyScheduler`, top-N activation through `LiveTickRelay.set_active_symbols`, the Discovered tier, or spread-tightness scoring/filtering. The DRAFT/proposed label is unchanged pending Saqib's product/design decision.
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
