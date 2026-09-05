# Trading Intelligence Architecture
**Version:** 1.7 — generalized hotkey/action model (Action Categories, Safety Levels, Hotkey Context, action-to-multi-device bindings); `LONG`/`SHORT` intent actions replace `BUY`/`SELL`/`PROPOSE_LONG`/`PROPOSE_SHORT`; `FocusedTile` renamed `TradeTarget`
**Companion documents:** [`system-design.md`](./system-design.md) — that doc explains *how the system is built* (modules, interfaces, deployment, folder structure). This doc explains *how the system thinks* (market state, context, strategy, decision logic). [`strategy-engine-design.md`](./strategy-engine-design.md) — §8–14's direction lock (decisions #87–88): Strategy internals, versioned configs, entry-timing model, Performance Intelligence's outcome schema, and how Decision Engine/Governor consume it. Concept only, no application code yet. [`../decisions/future-ideas.md`](../decisions/future-ideas.md) holds concepts raised and deliberately deferred, with the reasoning intact, so they don't need to be re-argued from scratch later. [`../decisions/confirmed-decisions.md`](../decisions/confirmed-decisions.md) is the running settled-decisions log. Keep these separate; a change to trading logic shouldn't require touching WebSocket plumbing, and an idea that isn't ready yet shouldn't clutter a document meant to describe what's actually built. See [`../README.md`](../README.md) for how the whole `docs/` tree is organized.

---

## 1. Central Idea

Every component in this system, whatever else it does, is ultimately answering one question: **"What is the current market state?"** — not "what's the latest tick." The chart, the AI, execution, and eventually replay are all *consumers* of state, not owners of their own private view of it. This is what makes the platform a **Trading Intelligence Operating System** rather than a chart with some scripts attached.

The pipeline below mirrors how a discretionary trader actually reasons:

```
What market am I in?          →  Market State
What is today's situation?    →  Context
What opportunities exist?     →  Strategy Engine → Opportunity Engine
Which one is best?            →  Decision Engine
How should I trade it?        →  Trade Planning Engine
Can I afford it?              →  Governor
Execute.                      →  Execution Engine
Manage.                       →  Position Monitor
Learn.                        →  Performance Intelligence
```

---

## 2. Two Kinds of Intelligence

Every module in this system falls into one of two categories. Naming the category a new module belongs to is the fastest way to keep the architecture disciplined as it grows — e.g. a future Order Flow Engine or News Sentiment Engine: which side does it belong on?

**State Intelligence** — describes reality. Answers *"what is happening?"* Has no opinion about what to do about it.
- Feature Engine, Market State Engine, Context Engine, Portfolio State Engine

**Decision Intelligence** — decides action. Answers *"what should we do?"*
- Strategy Engine, Opportunity Engine, Decision Engine, Trade Planning Engine, Governor

**Two hybrids, called out explicitly rather than forced onto one side:**
- **Position Monitor** reads state continuously but exists to decide (hold / partial / exit / reverse) — it's best understood as a Decision Engine that runs continuously against one open position instead of once against a new opportunity.
- **Performance Intelligence** describes the past (pure state, about what already happened) but exists solely to change future decisions — it's State Intelligence whose entire purpose is feeding Decision Intelligence.

**The governing rule for this whole document, worth stating once, plainly (decision #91):** Feature Engine measures. Market State interprets. Context Engine describes the world outside the market. Strategy decides. A new module's placement in the two categories above should be checkable against this sentence before it's checkable against anything else — it's also what settled §4/§5's boundary question once it existed to check against.

---

## 3. The Full Pipeline

```
                    Market Data (Broker Adapter)
                                │
                                ▼
                        Feature Engine  ─────────────────┐
                                │                          │
                                ▼                          ▼
                     Market State Engine            Scanner (100 → N)
                                │                          │
                                ▼                          │
                        Context Engine                     │
                                │                          │
                                └────────────┬──────────────┘
                                             ▼
                                  Strategy Scheduler
                                             │
                                             ▼
                                    Strategy Engine
                       (ORB, Momentum, Pullback, VWAP, Gap,
                        Reversal, Volume Spike, News, ...)
                                             │
                                             ▼
                                  Opportunity Engine
                              (what exists — no decisions)
                                             │
                                             ▼
                     Decision Engine  ◄──── Portfolio State
                    (which opportunity wins, if any)
                                             │
                                             ▼
                             Trade Planning Engine
                    (entry, stop, target, size, R, max hold,
                     scaling, trailing stop)
                                             │
                                             ▼
                   Governor (Risk & Policy)  ◄──── Portfolio State
                                             │
                                             ▼
                                   Execution Engine
                                             │
                                             ▼
                                  Position Monitor
                    (still valid? weakening? partial? exit?)
                                             │
                                             ▼
                          Performance Intelligence
                                             │
                        ┌────────────────────┴─────────────────────┐
                        ▼                                          ▼
                Strategy Engine                          Trade Planning Engine
              (reweight / retire)                       (recalibrate sizing/stops)
```

**Portfolio State** and **Market Clock** are drawn as side inputs rather than pipeline stages because they're shared services, not steps — every stage that needs "what do we currently hold" or "what time/session is it" reads them directly rather than having that information passed down the chain. Decision Engine, Governor, Position Monitor, and Performance Intelligence all read Portfolio State independently.

---

## 4. Market State — Has Memory, Not a Snapshot

```
                            Feature Engine
                     (per-symbol price/volume math —
                      SPY/QQQ/IWM are symbols here too)
                                  │
                    ┌─────────────┴──────────────┐
                    ▼                             ▼
             Any traded symbol             Market State Engine
             (Symbol Features)             ├── per-symbol scores
                                            └── cross-symbol scores
                                                (SPY/QQQ/IWM synthesis)
                                                     │
                                                     ▼
                                             MarketStateChanged
```

The mistake to avoid: treating market state as a single current value.

```
Trend:      Bullish
```

Instead, every state dimension carries its own trajectory — and, as of decision #91, that trajectory is a **number**, not a label:

```
trend_score: 82
```

**Scores are normalized state measurements, not probabilities or predictions (decision #91).** `trend_score: 82` doesn't mean "82% chance NVDA goes up" — it means "NVDA's current measured trend state sits at 82 on a 0–100 scale." 0–100 rather than 1–100, deliberately: both ends of the scale need to be real, reachable values, not an awkward off-by-one. Directional dimensions (Trend, VWAP relationship) run bearish→bullish across the full range; magnitude-only dimensions (Volatility, Volume) run quiet→extreme with no bearish pole at all — `50` means something different on each, and any future band mapping has to carry both shapes, not assume one.

**v1 ships scores only — no bands, no tags, no duration-in-state, no confidence, no `changed_at` (decision #91).** The duration/strength/confidence/previous shape above is still the eventual destination, not abandoned — but duration specifically can't be defined against a raw score without wobbling on every recompute (82, then 79, then 84, none of it a real "change"). Duration needs to key off a classified band, and bands don't exist yet. Rather than build a half-working memory layer, v1 keeps only the rolling window Market State Engine already needs for its own computation (see Implementation note below) — enough to derive **Acceleration** (a score's own rate of change over that window) as a first-class dimension, without needing bands to do it. The full band/classification system — score ranges, human-readable labels, real duration-in-band — is deliberately deferred; see [`../decisions/future-ideas.md`](../decisions/future-ideas.md) #22. **The score is the only thing that's ever a stored fact; a tag is always computed live from whatever the current band config says, never persisted** — a persisted tag would silently go stale the moment a band boundary is retuned, a fourth versioning problem alongside `strategy_version`, `schema_version`, and `data_version`/`feature_version`. Two independent reasons this is the right sequencing, not just the convenient one: band boundaries (`60–79 = Bullish`) are a guess until real score distributions from real market data exist to set them from evidence, not assumption — and a tag throws away exactly the information a score exists to preserve (81 and 89 both round to `Bullish`; nothing downstream can ever tell them apart again once that happens).

**Per-symbol dimensions, v1 (decision #91):** Trend, Volatility regime, Volume regime, VWAP relationship, Session type, Acceleration. Each produces one `<dimension>_score`, computed for any tracked symbol — including SPY/QQQ/IWM, which are ordinary symbols to Feature Engine and Market State alike, not a special case. `Market breadth`, previously listed here, is removed — it was never actually a per-symbol property, just mis-filed as one; see Cross-symbol dimensions below for what replaces it in v1, and `future-ideas.md` #23 for full breadth, deferred.

**Build note (decision #93) — two deviations from the list above, both flagged rather than silently decided.** `Session type` is dropped from the v1 build entirely: unlike the other four, it doesn't have a natural directional (bearish↔bullish) or magnitude (quiet↔extreme) reading, and rather than invent one, it's left unbuilt and flagged to revisit — `scoring.py` (`app/market_state_engine/scoring.py`) has no `session_type_score` function at all, not a stub returning a placeholder. `Acceleration` ships scoped to Trend's own rate of change specifically, not one value per dimension and not an adaptive pick of whichever dimension moved most this window — the simplest reading of "a score's own rate of change" that still answers the classic momentum-acceleration question, with the other interpretations available to revisit if Trend-only turns out to be too narrow in practice.

**Read-side snapshot access (decision #98, M4).** Everything above describes what gets *published* — `MarketStateEngine.get_snapshot(symbol=None)` is the companion synchronous read added alongside it, for a consumer whose own trigger fires independent of the last publish (a future Strategy's scheduled MATCH stage being the motivating case, not a dependency — Market State Engine has no knowledge Strategy Engine exists). Same shape either way: per-symbol `MarketState`, plus the cross-symbol composite included regardless of which symbol was asked for. `candle_ts` is preserved exactly as published — this is the one property a future Replay Engine will depend on, verified directly (`test_candle_ts_survives_as_domain_time_not_wall_clock`), not assumed.

**Participation is the observable half of market psychology, not the causal half — unaffected by the score-first change.** §7's agent-design table already asks "who is in control — buyers or sellers?" as an example question; this is where it gets a real answer, once it exists. Feature Engine still has no signed-volume/uptick-downtick raw signal to compute it from — an observable, no different in kind from relative volume or gap %, but not built yet. Once that signal exists, Participation joins the per-symbol list above as another `<dimension>_score`, no design change needed. What Participation deliberately does *not* claim, even once built, is *why*: the same volume imbalance can mean panic, excitement, short covering, or options-hedging flow, and telling those apart needs data (options gamma exposure, short interest) this system has no confirmed source for yet. That causal-inference layer is real, and it's kept visible rather than dropped — see [`../decisions/future-ideas.md`](../decisions/future-ideas.md) #13 — but faking it from data that can't support the distinction would produce a confidently wrong label, not a useful one.

**Cross-symbol dimensions, v1 — SPY/QQQ/IWM pulled forward from "Phase 5 scaffolding" to now (decision #91), kept deliberately small.** SPY, QQQ, and IWM get tracked as always-on subjects — same Feature Engine → Market State pipeline as any other symbol, same `DebounceScheduler`, just a tighter ceiling (~3–5s vs. ~10s) since broad-market state is what everything else gets compared against. Each gets its own full per-symbol score set above; Market State Engine then synthesizes a small, explicit set of cross-symbol scores on top:

```python
class CrossSymbolState(BaseModel):
    spy_direction_score: float        # SPY's own trend_score, surfaced directly
    qqq_direction_score: float
    iwm_direction_score: float
    trend_alignment_score: float      # how closely the three agree in direction
    risk_on_score: float              # QQQ/IWM strength relative to SPY —
                                       # risk-on when growth/small-cap lead, not lag
    qqq_leadership_score: float       # is tech leading or lagging the broader tape
    iwm_confirmation_score: float     # does small-cap confirm or diverge from
                                       # SPY/QQQ's read
```

No correlation matrices, no full advancing/declining breadth, no sector rotation engine — deliberately not v1. Three symbols is a cheap, good-enough approximation of broad market behavior; a real breadth system is its own data problem (needs a wide symbol universe this platform doesn't track continuously yet) and is tracked separately, deferred, in `future-ideas.md` #23.

**One rule governs where a new comparison-style dimension goes, cross-symbol or not: if it's a comparison between two price/volume series, it's Market State's job, no matter how many symbols are involved.** This is also where sector ETF relative strength belongs once it's built — "is this symbol moving with or against its sector ETF" is structurally identical to what the cross-symbol scores above already do, just at sector-ETF granularity instead of broad-index granularity. Not v1; tracked in `future-ideas.md` #24, revisited once SPY/QQQ/IWM alone prove useful enough to justify the next tier. Sector/industry *membership* (which sector a symbol belongs to, as opposed to how it's trading relative to that sector) is a separate, static, non-price fact — it lives on `symbol_fundamentals` instead (§5), not here.

**Implementation note, flagged deliberately because it's easy to get wrong:** this makes the Market State Engine *stateful* — it holds a rolling window per symbol, not just the latest computed score. In v1, that window's only job is enough score history to compute Acceleration; it does not yet need to reconstruct "how long has this been in a given band," since bands don't exist yet. **This revises the restart-behavior decision below, which was written before score-first existed** — the original question ("does the engine rebuild 'bullish for 23 minutes' from `market_state_history` on a backend restart, or wake up with duration reset to zero — decision: rebuild from persisted history") assumed duration-in-band was already being tracked. v1's rolling window is short enough that a cold start on restart is an acceptable, honest simplification for now, not a silent gap — but the original restart decision deserves a real re-look once bands actually get built, not an assumption that it still applies unchanged as written.

**Recompute cadence, decided now for Phase 2 wiring:** Market State Engine doesn't recompute on every tick, and it doesn't run on a fixed timer either — both are wrong for different reasons (every tick is wasteful given how rarely trend/volatility/volume regime actually change; a fixed timer misses fast-moving regime shifts between ticks of the timer). It uses the shared `DebounceScheduler` (`system-design.md` §8, `core/debounce_scheduler.py`): recompute is triggered by relevant upstream events (`FeaturesUpdated`, `ContextChanged`, a volatility spike crossing threshold), floored to no more than once per ~1 second so a burst of ticks doesn't cause redundant recompute, and ceilinged to at least once every ~10 seconds so state can't go stale even in a quiet market (~3–5s for SPY/QQQ/IWM, per above). This is the same event-driven-with-bounds shape as Scanner's cadence schedule (`system-design.md` §4.7) and Strategy triggers (§8 below) — a pattern this system already uses twice, extended here rather than reinvented.

**Where composite (cross-symbol) state is persisted (decision #91):** no separate table. A synthetic row inside the same `market_state_history` mechanism (sentinel symbol, e.g. `symbol = "__MARKET__"`), computed on the same `DebounceScheduler` cadence as everything else — same reasoning `strategy_outcomes` already applies to live and backtest rows sharing one schema, distinguished by a flag, rather than splitting into two tables for what's structurally the same kind of record.

**Data source confirmation — closed by decision #95's M0 spike.** Polygon daily bars for SPY/QQQ/IWM confirmed clean (correct weekday bar counts, no gaps, healthy volume/price ranges, no ETF-specific quirks) — the empirical-before-architectural check this project applies elsewhere, not assumed correct just because they're liquid, well-known tickers. (Finnhub's free-tier WebSocket trade feed was also found to be IEX-only, ~10% of true ETF volume at best — a real constraint, but on tick-level Participation work, not on the Feature Engine's candle-derived `sma_20_slope_angle` that `trend_score` — and so `spy_direction_score`/`qqq_direction_score`/`iwm_direction_score` — is actually computed from. Not a blocker for the cross-symbol synthesis built here, decision #97.)

---

## 5. Context Engine — Composed Providers, Not One Engine

Market State describes the market. Context describes the *situation* — and the same market state means different things depending on it.

> Bullish trend, first 15 minutes, gap up, near PDH, Fed day, high relative volume, inside yesterday's range — is a completely different trade than the same "Bullish" reading at 1pm on a quiet Tuesday.

**The boundary rule, settled after catching redundancy directly rather than by design review alone (decision #90): if it's a comparison between two price/volume series, it's Market State's job — no matter how many symbols are involved. Everything else is Context's.** `GapProvider`, `LevelsProvider`, and `VolatilityRegimeProvider` all failed that test — each was a re-label of a number Feature Engine or Daily Levels already computes (Gap%, level proximity), or the same comparison Market State's own Volatility-regime dimension already performs with more memory than a stateless re-check would have. All three are cut from Context Engine entirely, not kept as thin wrappers — a consolidation-only wrapper was considered and rejected: it would mean two delivery paths for the same fact, which defeats "compute once, consume everywhere" as surely as recomputing it would. Strategy reads Gap%/level proximity straight from `FeaturesUpdated`/Daily Levels; it reads Volatility regime from `MarketStateChanged` (§4). `SectorCorrelationProvider` is cut too, via a split rather than outright removal — see below. Context is going to keep growing regardless — economic calendar, OPEX, macro regime, news, sentiment, seasonality all plausibly belong here eventually — so keeping today's boundary strict is what keeps that growth from turning Context into a dumping ground instead of an architecture change every time something new gets added.

Treating Context as one engine with a growing pile of `if` branches would turn it into the least maintainable module in the system within a few months. Instead, Context Engine is a thin **aggregator** over independent, individually-testable **context providers**, each responsible for one question:

```python
class ContextProvider(ABC):
    name: str
    async def evaluate(self, market_state: MarketState) -> dict: ...
```

**M1 build note (decision #92) — two deliberate deviations from the signature above, both flagged rather than silently decided.** `MarketState` doesn't exist yet (Market State Engine, M2, isn't built), so the shipped `ContextProvider.evaluate()` currently takes no `market_state` argument at all — typing it against a guessed-at shape risked exactly the kind of rework M0's own Finnhub-dependent providers are being held back to avoid. Goes back on once M2 lands. Relatedly, `ContextEngine`'s v1 trigger is a session-boundary loop (`MarketClock.next_session_boundary()`), not a `MarketStateChanged` subscription — that event doesn't exist yet either, and `CalendarProvider` is the one provider whose own cadence ("changes on session/day boundaries," below) matches a boundary loop directly. Revisit both once M2 exists and/or once Fundamentals/News join with their own cadences.

**M1-remainder build note (decision #96) — a third base class, once Fundamentals/News actually needed one.** The `ContextProvider` signature above (and #92's own fix to it) is implicitly market-wide — no symbol anywhere. That was fine when `CalendarProvider` was the only provider, but `FundamentalsProvider`/`NewsFlagProvider` are inherently per-symbol, and §5 never actually resolved how "one `ContextChanged` event" was supposed to work once some providers are global and others aren't — it just wasn't relevant until now. Resolved with a second interface, `SymbolContextProvider` (`evaluate(self, symbol: str) -> dict`), and a second aggregation method, `ContextEngine.evaluate_for_symbol(symbol)`, publishing its own `ContextChanged(symbol=X)` — `evaluate_all()`/`ContextChanged(symbol=None)` stays exactly as #92 built it, untouched, for `CalendarProvider` alone. The per-symbol path triggers on its own 15-minute timer per tracked symbol (not tick-driven — News/Fundamentals have nothing to do with price ticks, matching "cadence their underlying reality actually changes at" below), reading the Scanner Universe once at `ContextEngine.start()` — a symbol added to the universe afterward doesn't get its own loop until a restart, a real v1 limitation, flagged rather than silently accepted.

v1 providers: `CalendarProvider` (session timing, Fed days, holidays — via Market Clock; built — `MarketClock` plus a small hardcoded 2026 FOMC-date set for Fed-day awareness, which `MarketClock` itself doesn't have; decision #92), `NewsFlagProvider` (presence/count/recency for a symbol's recent headlines — deliberately not sentiment scoring; see [`../decisions/future-ideas.md`](../decisions/future-ideas.md) #13 for why NLP sentiment stays deferred; built per decision #96), `FundamentalsProvider` (sector/industry, TTM revenue/net income/operating cash flow, next earnings date — promoted from `future-ideas.md` #9 now that a data source is confirmed; decision #90's shape, built per decision #96). Context Engine calls each registered provider and merges their output into one `ContextChanged` event (see `system-design.md` §10 for the payload contract). Adding a new context dimension later — OPEX, macro, sentiment — means writing one new provider, not touching the aggregator or anything downstream.

**Sector membership vs. sector relationship — the same boundary rule applied once more, and the pattern to reuse whenever a "sector-adjacent" idea comes up again (decision #90).** Sector/industry *membership* is a static, non-price fact — it's just a field on `symbol_fundamentals` below, no dedicated provider needed. Sector ETF *price-relationship* ("is this symbol moving with or against its sector right now") is a price/volume comparison, so it belongs in Market State's cross-symbol layer (§4) once it's built, not Context — deliberately not v1, tracked in `future-ideas.md` #24. No standalone `SectorCorrelationProvider` either way.

**`NewsFlagProvider`'s output is a compact derived-field group, not a bare boolean, and nothing behind it is ever persisted (decision #90).**

```
news:
    present:           true
    count_15m:         3
    recency_seconds:   180
    importance:        "high"
```

Still computed from a short rolling window per symbol, discardable once evaluated — every field above is derived, none of it is raw headline text, a link, or a source, and a stored `evidence.conditions` value reads `news_flag_active: true`, never the headline that set it. Same "evidence stores interpretation, not measurement" boundary decision #89 already applies to Feature Engine's raw values, applied here to raw news content — storing the actual article would just be re-creating a second, uncontrolled copy of something Context Engine already reduced to a compact signal for exactly this reason. `importance` is a keyword/volume heuristic, explicitly not language understanding — the line between "basic classification" and something closer to reading the article is Hermes' job (below), not this provider's.

**`NewsFlagProvider` build note (decision #96).** Fetches `/company-news` over a trailing 24h window; `count_15m`/`recency_seconds` computed from whatever falls inside that window, `importance` "high" on either a volume burst (3+ articles in 15 minutes) or a keyword hit against a small first-pass list (`earnings`, `fda`, `lawsuit`, `bankruptcy`, ...) — genuinely a first-pass heuristic, not a validated one, exactly as loosely specified above; worth iterating on with real data. **Never called for SPY/QQQ/IWM at all** — M0's spike found `/company-news` for an ETF ticker returns generic broad-market news mislabeled as related, not fund-specific (confirmed decision #94) — those symbols get `present: false` unconditionally, no API call attempted. Field names (`headline`, `datetime`, `related`) are Finnhub's documented schema, not independently re-verified against a live response — this build had no live Finnhub key or network access; worth a real smoke test before trusting in production.

**Providers refresh at whatever cadence their underlying reality actually changes at — this was always true of the interface, now made explicit because it matters for what comes next.** `CalendarProvider` changes on session/day boundaries; `FundamentalsProvider`/`NewsFlagProvider` re-evaluate on their own 15-minute per-symbol timer (decision #96), independent of both Calendar's boundary loop and of price ticks. Nothing about the `ContextProvider`/`SymbolContextProvider` interfaces assumes tick-speed refresh — a provider whose underlying reality only changes quarterly (earnings, balance-sheet data) is exactly as valid a provider as one that changes daily; it just triggers on a different event and sits idle otherwise. `FundamentalsProvider` is this pattern's first real instance, not just its justification: it reads from `symbol_fundamentals` (decision #90), a table refreshed on its own schedule, not fetched live on every `evaluate()` call — profile fields (industry only; see below) on a slow weekly batch, `market_cap` on its own daily refresh since it moves with price rather than staying static. **One deviation from what this paragraph originally proposed (decision #96):** financial-statement fields are checked and re-derived UNCONDITIONALLY every day for every tracked symbol, not gated on first comparing against `next_earnings_date` — at 6 symbols × 2 calls/day this costs nothing against either of decision #94's 60/min buckets, and the unconditional check is simpler than the originally-proposed gate while landing on the same outcome (a day with nothing new just re-derives the same numbers and moves on). This is what keeps a path open to slower-moving context — macro, sentiment — without it costing anything today: same abstraction, sparser trigger, no new module. See [`../decisions/future-ideas.md`](../decisions/future-ideas.md) #10–#12 for the remaining slow-tier providers this unlocks once their own data sources are settled.

**`symbol_fundamentals` table shape, and where it's sourced from (decision #90, built per decision #96).** Data source is Finnhub — already the project's real-time provider, so this adds no new third-party account or key, unlike the FMP/Alpha Vantage split an external reference project used. `/stock/profile2` covers industry (see below re: sector); `/stock/financials-reported` covers income/cash-flow, via decision #94's validated cumulative-to-discrete TTM derivation (`app/context_engine/fundamentals_derivation.py`); `/calendar/earnings` covers the next earnings date. **`sector` is a real column that's permanently unpopulated** — Finnhub's `/stock/profile2` provides exactly one classification field (`finnhubIndustry`), not a separate sector-and-industry pair this schema originally assumed; duplicating that one field into both columns would be fabricating a second dimension that doesn't exist in the source data, so `industry` gets the real value and `sector` stays `NULL`, flagged rather than silently faked. `marketCapitalization` (profile2) and each `/calendar/earnings` event's `date` field are Finnhub's documented schema, not independently re-verified against live output the same way the TTM math and rate-limit buckets were (decision #94) — this build had no live Finnhub key; worth a real smoke test before trusting in production.

```python
class SymbolFundamentals(BaseModel):
    symbol: str                           # PK
    sector: str | None
    industry: str | None
    profile_updated_at: datetime          # sector/industry — slow weekly batch, rarely changes

    market_cap: float | None
    market_cap_updated_at: datetime       # moves with price — daily, split from profile above

    revenue_ttm: float | None
    net_income_ttm: float | None
    operating_cash_flow_ttm: float | None
    financials_period: str | None         # e.g. "2026-Q2" — which filing these figures are as-of
    financials_updated_at: datetime       # refreshed once a new filing is expected to have landed,
                                           # checked daily against next_earnings_date, not fetched live

    next_earnings_date: date | None
    earnings_updated_at: datetime         # cheap, refreshed daily

    data_source: str                      # "finnhub" — honest provenance, same discipline as
                                           # decision #89's data_version/feature_version on backtests
```

**Reuse verdict on the external reference project (`equity-fundamental-analysis`).** Not reused directly — different stack conventions (in-memory dict cache, no persistence; FMP + Alpha Vantage instead of the already-integrated Finnhub; response shapes built for a UI card, not `ContextProvider.evaluate() -> dict`'s flat convention; two live API keys committed in plaintext in the source, a finding worth acting on independent of this decision). What carried over conceptually: the field list a fundamentals payload actually needs (sector/industry, TTM revenue/net income/cash flow, next earnings date) and the quarterly year-over-year comparison logic, both reflected in the schema above, re-sourced from Finnhub rather than ported as code.

**Read-side snapshot access (decision #98, M4) — and the timestamp gap it surfaced, left open on purpose.** `ContextEngine.get_snapshot(symbol=None)` is the synchronous companion to publication above, same motivation and shape-convention as Market State's own (§4) — a per-symbol read transparently merges the global (`evaluate_all()`) and per-symbol (`evaluate_for_symbol()`) paths into one `providers` dict, so a consumer doesn't need to know decision #96 split them internally. Unlike Market State's `candle_ts`, there is no domain-safe timestamp to attach here: `ContextProvider.evaluate()`/`SymbolContextProvider.evaluate()` take no timestamp parameter, and this section's own cadence description above (session-boundary loop, 15-minute per-symbol timer) is timer-driven, not candle-driven — there's no candle to borrow a timestamp from the way Market State's cross-symbol composite borrows one across SPY/QQQ/IWM. `get_snapshot()`'s `evaluated_at` is therefore wall-clock, stated as such rather than implied to be domain-safe — a real property a future Replay Engine will need to reckon with, not solved here since solving it means changing how Context Engine triggers itself, out of scope for what decision #98 set out to do.

**Hermes — a named, agentic provider, scaffolding proposed for Phase 5:** beyond `NewsFlagProvider`'s deliberately narrow presence/count signal above, Context Engine is planned to host a named agent — Hermes — that reads and analyzes the news reports `NewsFlagProvider` flags, rather than just counting that something fired. This is the system's first LLM-in-the-loop component — everything else in Feature Engine, Market State, and the rest of Context Engine is deterministic numeric computation — which makes it a meaningfully different kind of module, with different failure modes and a different verification approach than the rest of this document assumes. Data source, storage, and analysis method are deliberately left open for the implementation step; this entry only reserves Hermes's place in the pipeline.

---

## 6. Portfolio State — Treated Like Market State

Portfolio State is the account-side mirror of Market State: continuously maintained, not computed ad hoc when someone needs it. It's a shared service, not something owned by whichever module asked for it first.

| Consumer | Needs |
|---|---|
| Decision Engine | exposure, correlation across open positions |
| Governor | capital, buying power, daily loss consumed |
| Position Monitor | unrealized P&L, average cost |
| Performance Intelligence | historical position data |

Updated on `OrderFilled` / `PositionClosed`. Never recomputed independently by a consumer — same principle as Feature Engine (§ below): compute once, read everywhere.

---

## 7. Agent Design Philosophy — Question-Based, Not Indicator-Based

Don't design a module around "an EMA strategy." Design it around a question it answers. This is a subtle shift with real leverage: it makes every module extensible, because a new module just needs a new question, not a rewrite of how modules relate to each other.

| Module | Question |
|---|---|
| Trend (part of Market State Engine) | What is the dominant trend? |
| Participation (part of Market State Engine, §4) | Who is in control — buyers or sellers? |
| Liquidity | Where is liquidity likely sitting? |
| Breakout (a Strategy) | Is this breakout likely to continue? |
| Risk (the Governor) | Can we afford this trade? |

**Important distinction this raises:** "Trend" and "Participation" are **state-builders** — they feed Market State/Context and belong to State Intelligence. "Breakout" and "Momentum" are **setup-detectors** — they consume that state to decide whether a trade exists, and belong to Strategy Engine, i.e. Decision Intelligence. Both are "agents" in the loose sense, but they don't sit in the same pipeline stage. Keep that boundary explicit once there are a dozen of these — otherwise it becomes unclear whether a Trend module is "competing" with an ORB module, when they're not even doing the same job.

---

## 8. Strategy Engine

Each strategy answers one question and produces an **Opportunity Object**, never a bare BUY/SELL:

```
NVDA
  Momentum:  92
  ORB:       80
  Pullback:  10
```
or, expanded:
```
AAPL — ORB
  Confidence:  82
  Entry:       220.10
  Stop:        219.30
```

No trade yet. Only opportunity. Strategies don't run on a shared clock — each declares its own trigger (every candle, only after 9:35, only after a volume spike, every tick) via the Strategy Scheduler, so timing logic isn't duplicated across strategies (see `system-design.md` §4.7's Scanner cadence for the same pattern applied one layer up).

Planned initial strategy set: ORB, Momentum, First Pullback, VWAP, Gap, Reversal, Volume Spike. News is listed as a future addition, not a v1 strategy.

**Internal design locked; ORB (Stage 1's first strategy) is now built (decision #99).** The four-stage `evaluate()` anatomy (GATE/MATCH/SCORE/PROPOSE), the Gate's two layers (per-strategy trigger + declarative environmental conditions), immutable/versioned `StrategyConfig` (family → configuration, never edited in place), and an ACT/WAIT/ABANDON entry-timing model (a strategy may act immediately or deliberately defer, never forced to wait for a candle close unless its own hypothesis specifically needs one) are locked in `strategy-engine-design.md` (decisions #87–88). `strategy_engine/` now exists: `base_strategy.py` (the real `Strategy`/`StrategyConfig`/`Opportunity`/`ScheduleTrigger`) and `orb_strategy.py` (decision #99) — see `strategy-engine-design.md` §14 for the full build account, including an earlier undocumented attempt (`momentum_strategy.py`/`vwap_strategy.py`, built against a `base_strategy.py` that didn't exist yet) that was found orphaned and discarded rather than built on top of. Momentum is being rebuilt fresh, assigned to a separate session, against this now-real interface.

**What M4 (decision #98) prepared for this, without building any of it.** Both of MATCH's inputs are now readable synchronously — `MarketStateEngine.get_snapshot()` and `ContextEngine.get_snapshot()` (§4/§5 above) — and `StrategyOutcome`'s entry/exit snapshot fields have a real, tested capture contract (`app/trading_intelligence/state_snapshot.py`) waiting for whichever future module closes a trade. Neither engine gained any awareness that Strategy Engine exists; the dependency direction stays Market State/Context → (future) Strategy Engine, never the reverse — decision #98 was explicit about this being the boundary not to cross.

---

## 9. Opportunity Engine

Reads every Opportunity Object produced for a symbol across all strategies and ranks them. **It does not decide anything.** Its entire job is answering "which opportunities currently exist, and how do they compare" — arbitration is explicitly not its responsibility, which is why Decision Engine exists as a separate stage.

---

## 10. Decision Engine

Arbitrates when opportunities compete — same symbol, conflicting directions (Momentum says BUY, Reversal says SELL), or multiple symbols competing for the same limited capital. Reads Portfolio State (current exposure, correlation to existing positions) to make that call. Outputs at most one `OpportunitySelected` per available capital slot — everything else is discarded at this stage, not silently overridden later.

This is the layer that resolves: *Opportunity: 95% confidence. Trade Planner: ready. Decision Engine still has to decide whether this opportunity gets acted on at all before planning even starts.*

**Direction-locked, not yet built:** once Performance Intelligence (§14) has real outcome data, context-sliced performance evidence becomes a new arbitration input here — a tie-breaker between competing opportunities, not a replacement for Portfolio State. `strategy-engine-design.md` §6 (decision #87). Whether Decision Engine and Governor (§12) eventually merge into one component is explicitly left open there, not decided.

---

## 11. Trade Planning Engine

Answers *"if we trade this, how?"* — a fundamentally different question from *"should we trade?"* (that's Decision Engine and Governor's job). Produces:

- Entry
- Stop
- Target
- Position size (fractional Kelly — capital preservation first)
- R multiple
- Maximum hold time
- Scaling plan
- Trailing stop rule

---

## 12. Governor (Risk & Policy)

The heart of the system — not because it calculates anything sophisticated, but because it's the layer allowed to say **no** after everything upstream said yes.

```
Opportunity:    95%
Trade Planner:  ready
Governor:       "No."

Reasons:
  Already long NVDA
  Daily loss limit reached
  Too correlated to existing positions
  Fed speech in 8 minutes
  Risk budget exhausted
```

Reads Portfolio State (capital, buying power, daily loss consumed) and Context (scheduled events, session type) to make that call. Every rejection is logged with its reason — a rejected plan is exactly as valuable a data point as an approved one when Performance Intelligence later asks "are we too conservative, or exactly conservative enough?"

**The Governor's output schema is wider than a binary approve/reject, even though v1 only implements two of the branches.** A real risk manager doesn't just say yes or no — they say "reduce size," "wait 3 minutes," or "watch only, don't act." Building the schema for that now costs nothing (it's a type definition), and avoids a breaking change to every downstream consumer later:

```python
class GovernorDecision(BaseModel):
    action: Literal["approved", "approved_reduced", "delayed", "watch_only", "rejected"]
    size_multiplier: float | None = None   # used only when action == "approved_reduced"
    delay_seconds: int | None = None       # used only when action == "delayed"
    reasons: list[str]
```

**v1 implements `approved` and `rejected` only.** `approved_reduced`, `delayed`, and `watch_only` are real branches in the type but return `NotImplementedError` (or simply never get triggered by v1 rule logic) until there's a concrete rule that needs them. This is a schema decision made now, not a feature built now — the distinction matters.

**The first concrete rule for the unused branches is now direction-locked (not built):** context-sliced Performance Intelligence evidence (§14) derating or delaying a trade whose strategy configuration has weak evidence in the current context — same category as "daily loss limit reached." A hard boundary is written down alongside it: Governor may derate/delay a trade this way; it may **not** retire or modify a `StrategyConfig` — that stays human-only. `strategy-engine-design.md` §6 (decision #87); `future-ideas.md` #11 names `governor/position_sizing.py` as this rule's eventual home.

---

## 13. Position Monitor

Underweighted in early drafts of this system — deliberately elevated here. Not a passive "position is open" tracker. It continuously asks, against live Market State and Features:

```
Still valid?
Momentum weakening?
Move stop?
Take partial?
Exit?
Reverse?
Hold?
```

This is an active decision-making module, not a display widget — hence its classification as hybrid State/Decision Intelligence in §2.

**Cadence:** like Market State Engine (§4), Position Monitor uses the shared `DebounceScheduler` rather than a fixed poll timer — re-evaluate immediately on a relevant event (price crossing near stop/target, a `MarketStateChanged` on the held symbol), no more than once per ~1 second, at least once every ~10 seconds regardless of whether anything relevant fired. An open position shouldn't silently go un-evaluated for a long stretch, but it also doesn't need re-evaluation on every single tick when nothing has actually changed.

---

## 14. Performance Intelligence

Deliberately not called "Analytics" — analytics sounds passive, and this module's entire reason for existing is to change future behavior. It answers:

```
Why are we losing?
Which strategy underperforms?
Morning vs. afternoon?
Gap days?
High VIX regimes?
Low float names?
Fridays?
```

Feeds back into two places: **Strategy Engine** (reweight or retire underperforming strategies) and **Trade Planning Engine** (recalibrate sizing/stop logic based on realized outcomes, not assumptions). This is the seed of an eventual optimization engine, though building that optimization loop itself is out of scope for now.

**Schema direction-locked, not yet built:** an atomic `StrategyOutcome` record per closed trade (strategy + immutable version, evidence snapshot, market/context state at entry and exit, realized R/net P&L) persists to `strategy_outcomes` (renamed from `strategy_performance` — decision #89) — "rank," "expectancy by regime," and every other performance vector are `GROUP BY` queries over this table, computed on demand, never stored as a fact on the strategy itself. Reweighting/retirement stays human-reviewed for v1: automation may search and evaluate (a Backtest Runner, extending the deferred Replay Engine — `future-ideas.md` #5), but promoting, retiring, or modifying a live `StrategyConfig` requires Saqib's sign-off, no exception. `strategy-engine-design.md` §5/§7 (decisions #87, #89).

---

## 15. World View — Read-Only Composite Snapshot

There's a real idea worth keeping from the "everything should revolve around a persistent World Model" suggestion — but not in the form it was proposed. "Everything reads from it, everything writes to it" is the exact anti-pattern Feature Engine and Portfolio State Engine exist to prevent (§7 of `system-design.md`, principle 8: compute once, consume everywhere). A shared object with many writers is how state gets inconsistent, not how it stays coherent.

What's actually valuable is a **read-only composite view**: a facade that assembles Market State + Portfolio State + Context + recent Performance Intelligence output into one coherent snapshot of "what does the system currently believe," for consumers that want the whole picture at once — a debug dashboard, or a future LLM-based reasoning layer that needs one prompt-sized summary instead of five separate queries.

```python
class WorldView:
    async def snapshot(self, symbol: str | None = None) -> WorldViewSnapshot: ...
    # assembles from existing single-owner sources — owns nothing itself
```

Single-writer-per-domain stays fully intact — `WorldView` has no state of its own and no write path. It's purely a read aggregator, the same relationship Context Engine has to its providers (§5). The full "everything writes to it" version is parked in [`../decisions/future-ideas.md`](../decisions/future-ideas.md) in case a genuine need for shared mutable world state emerges later — it hasn't yet.

**Not to be confused with `app/trading_intelligence/state_snapshot.py` (decision #98, M4).** That module is two functions, not a class, reading exactly two sources (Market State + Context, not Portfolio State or Performance Intelligence too), for exactly one purpose (shaping `StrategyOutcome`'s entry/exit snapshot fields) — a narrow, purpose-built read, not a general-purpose facade. `WorldView` above remains "hasn't yet":

```
MarketStateEngine.get_snapshot()  ──┐
                                     ├──▶  state_snapshot.py  ──▶  (future) Execution / Position
ContextEngine.get_snapshot()      ──┘      (2 functions, 1 job)      Monitor → StrategyOutcome

                                     WorldView (still not built)
                                     would sit here instead, reading
                                     Market State + Portfolio State +
                                     Context + Performance Intelligence
                                     for a different job: one summary
                                     for a dashboard or LLM prompt.
```

---

## 16. Explicitly Deferred (Not Forgotten)

Deferred ideas — this round's and earlier rounds' — now live in one place: [**`../decisions/future-ideas.md`**](../decisions/future-ideas.md). That includes Replay Engine, Simulation Mode, Knowledge Engine, Attention Engine, uncertainty propagation, the full write-everywhere World Model, and — added this round — a quarterly-tier `FundamentalsProvider`, an Expectation/Surprise provider, fundamentals-informed sizing, a macro slow-tier provider, and causal psychology / market-participant inference (options flow, short interest). Centralizing them there (rather than re-explaining reasoning in whichever doc happened to be open when the idea came up) is what keeps this doc and `system-design.md` from drifting — the same reasoning that justified splitting into two documents in the first place applies to a third.

One piece of reasoning worth keeping visible here rather than only in the future-ideas doc, because it's load-bearing for Phase 3: `system-design.md`'s `BrokerAdapter` interface already means the Market Data Engine doesn't care whether its source is live or replayed. Replay, whenever it's built, becomes a new implementation of that interface — not a redesign of anything upstream. That's why deferring it now costs nothing later.

---

## 17. Bridge to Software Architecture

Every concept above has a concrete home in `system-design.md`. Use this table when a conversation starts drifting between "how it thinks" and "how it's built" — that's the signal to switch documents.

| Trading-intelligence concept | Code location (`system-design.md`) |
|---|---|
| Market State (with memory) | `trading_intelligence/market_state_engine.py` → `market_state_history` table |
| Participation (Market State dimension) | `feature_engine/indicators/` (signed volume / tick imbalance) → `trading_intelligence/market_state_engine.py` |
| Context (composed providers) | `trading_intelligence/context_engine/` (`engine.py` + `providers/`) (derived, not persisted) |
| Sector/correlation context | `trading_intelligence/context_engine/providers/sector_correlation_provider.py` |
| News-flag context | `trading_intelligence/context_engine/providers/news_flag_provider.py` |
| Strategy Engine | `trading_intelligence/strategy_engine/` → `ai_decisions`, `feature_snapshots` |
| Opportunity Engine | `trading_intelligence/opportunity_engine.py` → `ai_decisions` |
| Decision Engine | `trading_intelligence/decision_engine.py` → `ai_decisions` |
| Trade Planning Engine | `trading_intelligence/trade_planning_engine.py` → `trades` (draft); single `plan(TradeRequest)` interface, see §18 |
| Governor (widened decision schema) | `governor/governor.py`, `risk_rules.py`, `position_sizing.py` → `trades` (approved/rejected) |
| Position Monitor | `position_monitor/monitor.py` → `positions` |
| Performance Intelligence | `performance_intelligence/analyzer.py` → `strategy_outcomes` |
| Portfolio State | `portfolio_state/engine.py` |
| Market Clock | `core/market_clock.py` |
| Event Bus + event contracts | `event_bus/bus.py`, `events.py` → see `system-design.md` §10; two dispatch lanes (critical vs. normal), see §4.4 |
| Feature Engine | `feature_engine/engine.py`, `indicators.py` → `feature_snapshots` |
| World View (read-only facade) | `world_view/composite.py` — reads only, owns nothing |
| Shared update-policy utility (DebounceScheduler) | `core/debounce_scheduler.py` — used by Market State Engine (§4) and Position Monitor (§13) |
| Execution Mode (auto/manual) | `execution_engine/mode.py` (flag + `ExecutionModeChanged` event) → `portfolio_state` — see §18 |
| Approval Queue | `execution_engine/approval_queue.py` → `trades` (`status=pending_confirmation`) — see §18 |
| Input Layer / `InputCommand` | `input_layer/` (device adapters + shared schema) — frontend-owned; backend never sees which physical device fired — see §18 |

If a trading-logic change doesn't map to a row in this table, it's a signal the code structure needs to catch up — not that the mapping should be skipped.

---

## 18. Manual Trading & Execution Modes

Manual trading is not a second system running alongside the AI pipeline — it is a second *source* feeding the same pipeline, and a second *behavior* at the Governor→Execution boundary. Nothing in §3–§12 changes. This revision (v1.5) folds in three refinements: a single public Trade Planning interface, removal of a component that duplicated what the Input Layer already does, and a fully generalized command vocabulary.

```
Input Device → Input Layer → TradeRequest → Trade Planning Engine → TradePlan → Governor → Execution Engine → Broker
                                                      ▲
                                    Decision Engine ──┘ (auto path, unchanged)
```

### 18.1 One public Trade Planning interface

Agreed, and it's a real improvement over v1.4's two methods (`plan()` / `plan_manual()`). Trade Planning Engine should not expose a different method per origin — that just means every future origin (a signals import, copy-trading, whatever comes next) needs its own new public method forever. One interface, one input type that describes its own origin:

```python
trade_planning_engine.plan(request: TradeRequest) -> TradePlan
```

The engine branches internally on `request.origin` (auto-path sizing uses the opportunity's edge estimate; manual-path sizing uses the corroboration check in §18.4) — but that's an implementation detail inside one function, not two public contracts. Decision Engine, Governor, and Execution Engine still require zero new logic; they only ever see the resulting `TradePlan`.

### 18.2 `TradeRequest`

```python
class TradeRequest(BaseModel):
    origin: Literal["auto", "manual"]
    symbol: str
    direction: Literal["long", "short"]
    opportunity: OpportunitySelected | None = None  # required when origin == "auto"
    manual_size: ManualSize | None = None           # optional when origin == "manual";
                                                     # absent = size via corroborated Kelly (§18.4),
                                                     # if any, otherwise rejected — no silent default.
                                                     # presence/absence also drives Approval Queue
                                                     # routing regardless of ExecutionMode — see §18.5
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None                # required when order_type == "limit"
```

### 18.3 `TradePlan` — unchanged from v1.4

```python
class TradePlan(BaseModel):
    symbol: str
    direction: Literal["long", "short"]
    entry: float
    stop: float
    target: float | None = None
    size: int
    r_multiple: float | None = None
    max_hold_seconds: int | None = None
    scaling_plan: list[str] | None = None
    trailing_stop_rule: str | None = None
    origin: Literal["auto", "manual"]
    corroboration: list[str] = []   # symbols/strategy names of any Opportunity
                                     # Objects independently agreeing with this
                                     # trade; empty is a valid, meaningful value
```

`origin` carries forward from `TradeRequest` into `TradePlan` unchanged, which is what lets Governor's reasons, the Approval Queue, and Performance Intelligence (§14) distinguish manual from AI-originated trades without special-casing — same "widen now, narrow implementation" pattern as `GovernorDecision` (confirmed decision #6).

### 18.4 Success-rate evaluation — corroboration, not a fabricated score

A manually proposed trade has no strategy backing it by definition, so there's no honest probability to attach to it out of nothing. When `plan()` receives `origin == "manual"`, it does one cheap, real thing instead: reads (never decides) current Opportunity Objects for the symbol from Opportunity Engine (§9). If an active strategy already independently sees the same setup, that strategy's real confidence is surfaced and recorded in `corroboration`. If nothing corroborates it, the plan says so explicitly rather than presenting a number. Sizing follows the same rule — fractional-Kelly sizing (§11) needs a real edge estimate; with no corroborating strategy, `manual_size` (the human's own dollar/percentage/share input) is required, not optional, and Kelly doesn't run.

### 18.5 Execution Mode

```python
class ExecutionMode(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"
```

One value, system-wide, at a time — owned by Portfolio State (account-level state, same as buying power or daily loss consumed) and broadcast as `ExecutionModeChanged` so Execution Engine and the frontend both react without polling (system-design.md principle 4). Deliberately an enum, not a bare bool, so a third mode (simulation, review-only — explicitly not designed now) is additive later, not breaking.

**Execution Engine (system-design.md §4.9) is the only module that changes behavior**, and only where it currently "routes through `BrokerAdapter.place_order`":

- `mode == auto` → unchanged. `OrderApproved` flows straight through to `place_order`.
- `mode == manual` → the approved `TradePlan` is written to an **Approval Queue** instead (`status=pending_confirmation`) and a `PlanAwaitingConfirmation` event notifies the UI. A human action — `ManualConfirmOrder` or `ManualDiscardOrder` — actually calls `place_order`, or discards it.

A discarded/ignored plan is logged with the same discipline as a Governor rejection (§12: *"a rejected plan is exactly as valuable a data point as an approved one"*).

**One exception, independent of mode.** If a manual `TradeRequest` has no `manual_size` (§18.2) — a proposed idea, not yet a committed size — it always lands in the Approval Queue for review, even when `ExecutionMode == auto`. Nobody, human or Kelly's algorithm, decided a size yet, so nothing should be able to fire it yet either — that's a property of the request, not of the mode. A sized manual request, and every auto-origin request, follows the mode rule above unchanged. This is what makes a single `LONG`/`SHORT` action work for both what used to be `BUY`/`SELL` and `PROPOSE_LONG`/`PROPOSE_SHORT` (§18.6) — the branch moved from "which command was pressed" to "was a size actually given."

**Note for `system-design.md`:** §4.9 needs this same mode-check added — out of scope for this doc, flagged so it doesn't drift (confirmed decision #11's own rule).

### 18.6 Input Layer

Correct abstraction, same justification as v1.4: nothing above `BrokerAdapter` should know which broker is behind it (architectural principle 1); nothing above the Input Layer should know which physical device fired a command. Device adapters (Gamepad API, keyboard, Stream Deck webhook, voice-to-text) live in the frontend, all normalizing to one shape, sent over the existing WebSocket Gateway (system-design.md §4.12) — no new transport.

**Commands are trading intentions, not order verbs — `LONG`/`SHORT` replace `BUY`/`SELL`/`PROPOSE_LONG`/`PROPOSE_SHORT`.** v1.6 kept two commands per direction — a sized, committing one and an unsized, review-only one — and had to work around `SELL` being ambiguous between "open a short" and "close a long." Both problems trace to the same cause: the command was encoding an execution decision (fire now vs. review first) that belongs to Governor and `ExecutionMode`, not to the hotkey. One command per direction removes both at once — there's no second verb to keep in sync, and `SHORT` never needs a footnote explaining what it doesn't mean.

```python
class ManualSize(BaseModel):
    mode: Literal["shares", "percentage", "dollars"]
    value: float

class InputCommand(BaseModel):
    action: Literal["LONG", "SHORT", "APPROVE", "DISCARD"]
    symbol: str | None = None        # None = current TradeTarget (§18.10)
    order_type: Literal["market", "limit"] = "market"
    limit_price: float | None = None
    size: ManualSize | None = None   # present = commit now; absent = propose for review — §18.5
```

`size`'s presence, not the action name, is what used to be the `BUY` vs. `PROPOSE_LONG` distinction — pressing `LONG` with a size preset behaves like the old `BUY`; pressing it with no size behaves like the old `PROPOSE_LONG`. One action, one meaning ("I want long exposure on this symbol"), two possible payloads, decided downstream by §18.5's rule rather than by two different buttons meaning two different things.

`CLOSE` and `REVERSE` — real, valuable future actions — are deliberately not in this enum yet. Both require reading and acting on an *existing* position, which is Position Monitor/Trade Management territory and explicitly out of scope this iteration (§18.8). They're reserved names, not built actions — see `future-ideas.md` #14 and #16.

**Symbol targeting — `TradeTarget` (renamed from `FocusedTile`).** `symbol: None` resolving to "whatever's targeted" only works if that's one real, singular, always-known piece of state — and Phase 1's workspace (multiple tabbed Main Windows, each an 8×8 grid of sub-window tiles, each tile its own symbol) has no such concept yet. It needs to be added, not assumed. Renamed from v1.6's `FocusedTile` specifically because "focus" is an overloaded word in frontend work — it invites confusion with DOM/keyboard focus, which this concept explicitly is not tied to:

- A `TradeTarget{subWindowId, symbol} | null`, tracked **per tab** — clicking a tile sets that tab's target, and nothing else does. Not a hover. Not keyboard tab-order. Not mouse position. Not a new Opportunity, a price alert, or an approaching stop firing in the background. A hotkey acting on a target that silently changed underneath you is exactly the failure mode this whole design exists to avoid.
- A hotkey always resolves against the **active tab's** remembered target. Switch tabs, and `LONG`/`SHORT` immediately target whatever that tab's target is — there's no such thing as acting on a tile in a tab you aren't currently looking at.
- No target yet set in the active tab → symbol-targeted actions are refused outright, with a clear on-screen state — never defaulted to an arbitrary tile.
- A persistent highlighted border on the target tile is mandatory, always rendered. What a hotkey is about to act on is never something you have to remember or infer.
- Persists with the rest of the workspace layout (Phase 1's existing localStorage mechanism, §4.11) — reloading restores it, and the highlight is what makes that visible rather than silent.

This is a distinct concept from the **Approval Queue cursor**, formalized here as `QueueCursor` — the position within the pending-plans list that `APPROVE`/`DISCARD` act on. That's unrelated to any tile or tab; conflating the two would mean a chart click could accidentally change which trade you're about to confirm.

### 18.7 No dedicated `ManualPlanBuilder`

Agreed — removing it. The only thing it would have done is translate an `InputCommand` into a `TradeRequest`, and that translation belongs to the Input Layer itself: it already owns "normalize whatever the device sent into one shape" (§18.6), and `TradeRequest` is just that shape's next stop. Adding a named backend component for a pure data reshape would be structure for its own sake — exactly the kind of thing this project's own discipline (confirmed decisions, `future-ideas.md`) exists to avoid building before it's earned. If the translation ever grows real logic — permissioning, rate-limiting a trigger-happy hotkey, multi-step confirmation state — that's a legitimate trigger condition for promoting it to a real component then, not now.

### 18.8 Explicitly deferred: Position Monitor & Trade Management for manual positions

This is a conscious design decision, not an omission. Position Monitor, Trade Management, and any post-entry manual workflow (stop moves, partial exits, reversal) are untouched by this iteration. Once a manual `TradePlan` is accepted and filled, it's recorded exactly where an auto-executed one is — the existing `trades` table (§17 bridge table) — `origin="manual"` is sufficient to distinguish it; no new logging infrastructure. A manually-opened position, once filled, is managed no differently than any other open position today, which is to say: not yet actively managed by Position Monitor logic that's aware of manual origin — that integration is future work.

Worth logging as a `future-ideas.md` entry with an explicit trigger condition (e.g. "once manual entry has real usage data showing demand for in-position manual control") so it's recoverable later rather than needing to be re-argued from scratch — happy to draft that entry alongside this doc if useful.

### 18.9 Changes made beyond what was requested

- Unified `plan()`/`plan_manual()` into the single interface requested (§18.1), and removed `ManualPlanBuilder` as its own file/row in the bridge table (§18.7) — it was already redundant with the Input Layer once the single interface existed.
- Named and resolved the `SELL` ambiguity explicitly (§18.6) rather than letting v1.4's silent omission of it stand unexplained. *(Superseded in v1.7 — `SELL` itself was later dropped in favor of `SHORT`; see §18.6.)*
- Added `order_type`/`limit_price` to both `TradeRequest` and `InputCommand`, since "order type" was in the requested parameter list but hadn't been modeled anywhere yet.
- Kept `symbol` as a top-level field rather than folding it into `params`/`size` — held this position rather than adopting the flatter version, with reasoning in §18.6, since it's addressing information rather than a sizing/type parameter.

### 18.10 Hotkey Module, Actions & Bindings

Kept as a self-contained module, per your instruction — the rest of the frontend (chart, grid, workspace) doesn't know a controller, or any other device, exists.

**Action Categories.** Every dispatchable action belongs to exactly one category, and the category — not a case-by-case decision — determines where it's routed:

| Category | Examples (this iteration) | Routed to |
|---|---|---|
| Trading | `LONG`, `SHORT` | Backend, via `TradeRequest` -> Trade Planning Engine (§18.1) |
| Queue | `APPROVE`, `DISCARD` | Backend — `APPROVE` fires `ManualConfirmOrder`, `DISCARD` fires `ManualDiscardOrder` (§18.5), acting on `QueueCursor` |
| Navigation | `NEXT_TILE`, `NEXT_QUEUE_ITEM` | Frontend only — never touches the backend |
| UI | `SHOW_BINDING_LEGEND` | Frontend only |
| Emergency | *(none built yet — see below)* | — |

Navigation and UI actions never construct a `TradeRequest` or reach the WebSocket Gateway at all. This is what "the Input Layer knows nothing about trading logic" means concretely: not that trading-shaped actions don't exist, but that non-trading categories take a genuinely separate, simpler path chosen by category — the dispatcher never hardcodes "everything eventually becomes a trade."

**Hotkey Context.** Before category routing happens at all, the Input Layer checks what the user is currently doing. If a text input has focus (renaming a tab, a search box) or a modal is open, only `Emergency`-category actions are eligible — everything else, Navigation included, is suppressed. This was a real gap in v1.6: without it, a keyboard-bound action (once a keyboard adapter exists) could fire while someone is typing into a search box, or a controller press could land while a confirmation dialog is on screen expecting a different answer. `HotkeyContext` (`chart` | `modal` | `text_input` | `settings`) is frontend-only state, read by the dispatcher, never sent to the backend.

**Safety Levels.** Each action declares one, rather than a single hardcoded "arm-then-fire" rule bolted onto two specific commands:

```python
class SafetyLevel(int, Enum):
    IMMEDIATE = 0        # single press
    HOLD_AND_PRESS = 1   # arm input held, then action input pressed -- the hold is the confirmation
    HOLD_TIMED = 2        # arm input held for a fixed duration (not used by any action yet)
    DOUBLE_CONFIRM = 3    # two distinct presses required (not used by any action yet)
```

This iteration only populates levels 0–1 — `LONG`/`SHORT` with a `size` set (committing capital) and `APPROVE` are `HOLD_AND_PRESS`; an unsized `LONG`/`SHORT` (proposing, not committing — §18.5's review step is the safety net here, not the gesture), `DISCARD`, and every Navigation/UI action are `IMMEDIATE`. Levels 2–3 exist now specifically so the Emergency actions below have somewhere to land later without another enum migration.

**Bind actions, not buttons.** A binding maps one action to one input on one device — the reverse of keying by button:

```python
class Binding(BaseModel):
    action: str             # e.g. "LONG"
    device: Literal["gamepad", "keyboard", "streamdeck", "voice"]
    input: str               # device-specific: "RB+A", "Ctrl+L", "button_3"
```

Multiple devices can bind the same action simultaneously — gamepad *and* keyboard both mapped to `LONG` is a normal configuration, not a conflict, since bindings are keyed by `(action, device)`, not one global button table. `bindingMap.ts` stores a list of these, persisted with the rest of the workspace layout (§4.11).

```
frontend/src/input/
├── deviceAdapters/
│   ├── gamepadAdapter.ts    # polls Gamepad API (rAF loop), emits edge-triggered RawInputEvent -- button-down transitions only
│   └── types.ts             # RawInputEvent -- device-agnostic; keyboard/Stream Deck/voice adapters land here later
├── bindingMap.ts             # Binding[] -- user-configurable, persisted with workspace layout
├── safetyLevels.ts           # Action -> SafetyLevel table
├── hotkeyContext.ts          # tracks chart/modal/text_input/settings -- gates all dispatch
├── commandDispatcher.ts      # RawInputEvent + bindingMap + HotkeyContext + TradeTarget/QueueCursor -> routes by Action Category
└── useInputLayer.ts          # the module's only public export -- one hook, mounted once at the app shell
```

`useInputLayer.ts` is the entire public surface. `TradeTarget` and `QueueCursor` deliberately don't live in this module — they're core workspace state (written by tile clicks and queue-navigation UI), and the Input Layer only reads them. Same read-only relationship §15 already established for World View reading state it doesn't own.

**Default bindings** (Xbox layout, gamepad device — fully remappable via `bindingMap.ts`; a starting proposal, not a locked-in decision):

| Input | Action | Safety Level | Notes |
|---|---|---|---|
| Hold **RB** + tap **A** | `LONG` (sized) | 1 | Current size preset, current `TradeTarget` |
| Hold **RB** + tap **X** | `SHORT` (sized) | 1 | Same sizing |
| **D-pad Up** | `LONG` (unsized) | 0 | Always lands in the queue for review — §18.5 |
| **D-pad Down** | `SHORT` (unsized) | 0 | Same |
| **D-pad Left / Right** | Cycle size preset | 0 | 3 configurable tiers — local UI state, not a dispatched action |
| Hold **LB** + tap **A** | `APPROVE` | 1 | Acts on top of Approval Queue |
| Hold **LB** + tap **B** | `DISCARD` | 0 | Acts on top of Approval Queue |
| **Y** | `NEXT_QUEUE_ITEM` | 0 | Navigation — moves `QueueCursor`, no trading effect |
| **Start** | `SHOW_BINDING_LEGEND` | 0 | UI |

**Feedback, not just input.** Haptic pulses on Safety Level 1 transitions — one pattern armed-and-ready, one for Governor-approved, one for Governor-rejected — give an eyes-off-chart channel for exactly the moments reflex speed matters. A Governor rejection reason surfaces at the point of the action that triggered it, not only in a log reviewed later.

**Emergency actions — agreed valuable, deliberately not built here.** `PANIC` (close every open position), `FLATTEN_SYMBOL`, and `CANCEL_ALL_ORDERS` would be some of the highest-value actions a manual trader could have. But every one of them needs to read and act on *existing* positions — Position Monitor/Trade Management territory, already twice explicitly deferred this iteration (§18.8). Building any of them now would quietly reopen a boundary that's been confirmed more than once. Logged as `future-ideas.md` #16, flagged high priority for whenever Position Monitor integration starts — not a "maybe later," a "build this early in that phase."

**Deliberately not solved here:** limit orders (no clean way for a controller to type a price — the natural source later is the chart crosshair, not typed entry) and a richer order payload (risk preset, time-in-force) — real, worth having, but nothing in a market-order-only iteration uses them yet. `InputCommand`'s `size`/`order_type` fields (§18.6) are where they'd extend, not a redesign.

### 18.11 This revision — verdicts on the reviewed proposals

| # | Suggestion | Verdict | Where |
|---|---|---|---|
| 1 | Unify `BUY`/`SELL`/`PROPOSE_*` into intent actions, let Governor + `ExecutionMode` decide | **Adopted, refined** — `size` presence, not mode, decides queueing; an unsized request queues in every mode, not only Manual, so an idea nobody sized never fires unreviewed | §18.5, §18.6 |
| 2 | Drop `BUY`/`SELL` for `LONG`/`SHORT` | **Adopted** | §18.6 |
| 3 | Formalize symbol targeting as one source of truth | **Adopted, renamed** `FocusedTile` -> `TradeTarget`; explicit non-triggers listed | §18.6 |
| 4 | Hotkey Context (textbox/modal gating) | **Adopted** — real gap, not previously addressed | §18.10 |
| 5 | Architecture shouldn't name Xbox buttons directly | **Already true in code** (`bindingMap` existed in v1.6); doc presentation now clearly separates Action (architecture) from default binding (config) | §18.10 |
| 6 | Action Categories | **Adopted** | §18.10 |
| 7 | Emergency actions (`PANIC`, flatten, cancel-all) | **Agreed valuable, not built** — all read/act on existing positions, already-deferred territory; logged as high-priority future work | `future-ideas.md` #16 |
| 8 | Multi-step Safety Levels | **Adopted** — only levels 0–1 populated today; 2–3 reserved for Emergency actions | §18.10 |
| 9 | Bind actions, not buttons; multiple devices per action | **Adopted** | §18.10 |
| 10 | Richer order payload (risk preset, time-in-force) | **Noted as an extension point**, not added to the schema — nothing in a market-order-only iteration uses them | §18.10 |
| 11 | Fully generic Input Layer, no trading knowledge | **Adopted** — Action Category routing means Navigation/UI actions never construct a `TradeRequest` | §18.10 |

The one place the suggestion as literally written wasn't taken: unconditional "Governor + `ExecutionMode` decide" (point 1) would let an algorithmically-sized, never-reviewed idea fire with zero human look in Auto mode. Kept the review step; moved what triggers it from "which command was pressed" to "was a size actually given."
