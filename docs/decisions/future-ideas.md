# Future Ideas — Deferred Concepts

**Companion documents:** [`../architecture/system-design.md`](../architecture/system-design.md) (how it's built), [`../architecture/trading-intelligence-architecture.md`](../architecture/trading-intelligence-architecture.md) (how it thinks), [`confirmed-decisions.md`](./confirmed-decisions.md) (settled, as opposed to deferred). This doc is neither of the first two — it's a holding pen for good ideas that came up during design but aren't needed for current requirements. Nothing here is rejected forever. Each entry exists so that revisiting it later starts from the reasoning already worked out, instead of from scratch.

**How to use this doc:** before reviving anything below, check whether its "trigger" condition has actually occurred. If it hasn't, the honest move is to leave it here, not to convince yourself the trigger is close enough.

---

## 1. Knowledge Engine

**What it is:** a layer answering "what usually happens," "what historically works," "what regime are we in" — e.g. average ORB success rate, current VIX regime, earnings-season effects, sector rotation.

**Why deferred:** it splits into two things on inspection. Regime/calendar facts (Fed week, VIX regime, earnings season) are already covered by Context Engine's provider model (`../architecture/trading-intelligence-architecture.md` §5) — a `RegimeProvider` or `EarningsCalendarProvider` slots in there directly, no new top-level stage needed. Historical-success-rate facts ("ORB wins 68% of the time in low-VIX regimes") need real backtest data to mean anything, and Replay/Backtesting is itself deferred (§5 below) — so this half of the idea would launch as an empty shell.

**Trigger to revisit:** once Replay/Backtesting exists and there's a real corpus of historical strategy outcomes to query, "historical tendency" becomes a natural extension of Performance Intelligence rather than a new module.

**Where it would plug in:** regime/calendar half → new Context providers, no architecture change. Historical-tendency half → an extension of Performance Intelligence's schema, not a new engine.

---

## 2. Attention Engine

**What it is:** an explicit prioritization layer — "NVDA exploding + SPY flat + Fed speech + breadth collapsing → focus reasoning here first."

**Why deferred (leaning toward not needed at all):** Scanner already narrows 100 symbols down to a handful before anything reasons about them — that's the attention mechanism, just not named as one. The systemic case that makes this sound compelling (breadth collapsing → suppress everything) is already covered two other ways: breadth is one of the six Market State dimensions, it flows into Context, and a systemic dampening rule belongs in the Governor, which already has "watch only" and "delayed" in its widened decision schema (`../architecture/trading-intelligence-architecture.md` §12) to express exactly that. Building a separate Attention Engine risks re-implementing prioritization logic that already has a home.

**Trigger to revisit:** only if, in practice, Scanner's ranking demonstrably produces bad prioritization that Market State + Context + Governor can't already express. Don't build this speculatively — build it if the gap shows up.

**Where it would plug in, if it's ever needed:** between Scanner and Strategy Scheduler, reading Market State/Context rather than duplicating either.

---

## 3. Uncertainty Propagation

**What it is:** treating confidence scores as first-class and propagating them through the pipeline — e.g. an opportunity's final confidence reflecting the confidence of the Market State inputs and Context signals that fed into it, not just the strategy's own read.

**Why deferred:** this is real, but it's solving a precision problem before there's data to know whether the precision is earned. An 85%-confidence opportunity that only wins 40% of the time in practice makes propagated uncertainty actively misleading, not neutral — it dresses up an uncalibrated number in more mathematical clothing. Performance Intelligence's job is to answer "are our confidence scores calibrated against real outcomes" first; propagation is only worth building on top of confidence scores that have already been checked against that.

**Trigger to revisit:** once there's enough live (or replayed) trade history that Performance Intelligence can report actual win-rate by confidence bucket. If 85%-confidence opportunities actually win around 85% of the time, propagation is worth the complexity. If they don't, propagating an uncalibrated number just compounds the problem.

**Where it would plug in:** every `*Changed`/`*Created` event in §10 of `../architecture/system-design.md` would need a `confidence` field alongside its current fields — additive, so this wouldn't be a breaking schema change when it happens.

---

## 4. Full Write-Everywhere World Model

**What it is:** the original proposal — a single persistent object representing Market + Portfolio + Environment + Trader + Strategies + History + current beliefs, that "everything reads from and everything writes to."

**Why deferred (in this form specifically):** "everything writes to it" is the same failure mode Feature Engine and Portfolio State Engine exist to prevent — multiple writers to shared state is how state becomes inconsistent, not how it stays coherent. A lighter version of the good part of this idea — a **read-only composite snapshot** assembled from the existing single-owner sources — was adopted instead (`../architecture/trading-intelligence-architecture.md` §15, `world_view/composite.py`). That covers "one place to see everything the system currently believes" without introducing multiple writers to one object.

**Trigger to revisit:** only if a genuine need for *shared mutable* world state emerges — e.g. a future planning/reasoning module that needs to hypothesize against a modified copy of world state without touching the real one (closer to a simulation/scratch-space use case than a live-state one). That's a different problem from what's needed today.

**Where it would plug in:** would likely sit alongside or replace `world_view/composite.py`, gaining a constrained write path (e.g. through the Event Bus, so writes stay attributable and ordered) rather than direct mutation from arbitrary consumers.

---

## 5. Replay Engine

**What it is:** rewind the platform to a specific historical moment and have the entire system — chart, AI, signals — behave exactly as it would live, fed from recorded data instead of a broker feed.

**Why deferred:** explicitly out of scope for now, per project simplicity — not because it's architecturally hard.

**Why it's cheap to defer:** the `MarketDataProvider` interface (`../architecture/system-design.md` §4.1) already means the Market Data Engine doesn't care whether its data source is live or replayed. When Replay is built, it's a new implementation of that interface (or a wrapper feeding the same `PriceUpdated`/`CandleClosed` events), not a redesign of anything upstream. The door was left open for free; nothing today needs to anticipate it further.

**Trigger to revisit:** whenever manual-trading-experience-driven review of specific historical sessions becomes valuable enough to justify the build — no architectural trigger required, this one's just a scheduling/priority call.

**Where it would plug in:** `broker_adapters/replay_provider.py`, implementing `MarketDataProvider` (not `BrokerAdapter` — replay has no execution to fake, same reasoning as `confirmed-decisions.md` #28). Would use the deferred `replay_sessions` table (reserved name, not created — `../architecture/system-design.md` §4.13).

---

## 6. Simulation Mode

**What it is:** inject synthetic market events or recorded sessions to test AI behavior without connecting to a broker at all — distinct from Replay in that the data doesn't have to be real.

**Why deferred:** same reasoning as Replay, and lower priority — Replay against real historical sessions is more directly useful given three years of manual trading experience to draw on; synthetic injection is a testing-infrastructure nice-to-have on top of that.

**Trigger to revisit:** once Replay exists, Simulation Mode is a small extension (a synthetic-data adapter alongside the replay adapter) rather than a separate effort — natural to build them back-to-back rather than Simulation first.

**Where it would plug in:** another `BrokerAdapter` implementation, e.g. `broker_adapters/simulation_adapter.py`, generating synthetic events instead of replaying recorded ones.

---

## 7. TimescaleDB Migration

**What it is:** convert `candles` from a natively-partitioned plain-Postgres table into a Timescale hypertable, gaining compression, continuous aggregates, and retention policies.

**Why deferred:** already reasoned through in detail in `../architecture/system-design.md` §6.1 — not duplicated here. Short version: it's an additive Postgres extension, not a database migration, so deferring costs nothing.

**Trigger to revisit:** multi-timeframe backtesting at scale becomes a real requirement (see `../architecture/system-design.md` §6.1 and §9).

---

## 8. Redis / Multi-Process Agents

**What it is:** move from an in-process cache/queue to Redis, enabling agents or other modules to run as separate processes rather than within one FastAPI monolith.

**Why deferred:** already reasoned through in `../architecture/system-design.md` §6 — no need for the operational overhead of a second service while this remains a single-operator modular monolith.

**Trigger to revisit:** agents or other modules actually need to run as separate processes (e.g. for compute isolation or independent scaling).

---

## 9. FundamentalsProvider (Quarterly-Tier Context)

**What it is:** a Context provider surfacing company fundamentals — revenue, profitability, debt, cash, leadership/quality signals — as slow-refresh context, using the same `ContextProvider` interface as `GapProvider` or `VolatilityRegimeProvider` (`../architecture/trading-intelligence-architecture.md` §5), just triggered on filings/earnings events instead of a seconds-scale timer.

**Why deferred:** not because fundamentals don't matter for this system's future — they're explicitly kept out of "don't build at all" territory (see `confirmed-decisions.md` #21) — but because a confirmed structured-data source isn't settled yet. `ib_async` may partially cover this; needs a real spike, not an assumption, before committing to a schema.

**Trigger to revisit:** a confirmed fundamentals data source, or the point at which swing/position-holding-period trading becomes an actual near-term goal rather than an open door to keep available. In the meantime, fundamentals still inform which symbols enter the 100-name scanning universe — that's a one-time screen, not a live provider, and needs no architecture change to keep doing today.

**Where it would plug in:** `trading_intelligence/context_engine/providers/fundamentals_provider.py`, triggered by an `EarningsReleased`/filing event rather than the `DebounceScheduler`'s seconds-scale rhythm.

---

## 10. Expectation / Surprise Provider

**What it is:** markets move on the delta between expected and actual, not the raw number — same-magnitude EPS beats send stocks in opposite directions depending on what was priced in beforehand. This would extend `CalendarProvider`'s scheduled events (earnings, Fed decisions, CPI) with a consensus/expected value, and compute the surprise once the real value lands.

**Why deferred:** needs a consensus-estimate data source (earnings estimates, economic forecast consensus) this system has no confirmed access to yet — same class of gap as Fundamentals (#9), not a design problem.

**Trigger to revisit:** a confirmed consensus-estimate data source. Worth prioritizing highly once that's settled — of everything parked in this document, this is one of the more clearly load-bearing concepts, not a nice-to-have.

**Where it would plug in:** extends `CalendarProvider`'s event schema with an optional expected-value field; a companion provider (or the same one) computes and publishes the delta once the actual value posts.

---

## 11. Fundamentals-Informed Sizing / Confidence

**What it is:** letting fundamental quality — not just technical setup — influence position size, confidence, or holding duration. Two symbols with identical technical setups don't have to get identical treatment.

**Why deferred:** a two-step idea, and only the first step (visibility, via #9) is safe to build now. Wiring fundamentals into Decision Engine's arbitration or Trade Planning's Kelly sizing means inventing a real number for "how much does low fundamental quality shrink size" — exactly the kind of number that shouldn't be guessed before it can be checked, per the same reasoning already applied to Uncertainty Propagation (#3).

**Trigger to revisit:** `FundamentalsProvider` (#9) is live, and Performance Intelligence exists with enough history to check whether fundamentals-adjusted sizing actually improves outcomes versus technical-only sizing.

**Where it would plug in:** new optional fields on the Opportunity/Decision schema (`../architecture/system-design.md` §10), consumed by `governor/position_sizing.py`.

---

## 12. Macro / Global Slow-Tier Provider

**What it is:** a Context provider for macro conditions that drift over days-to-weeks — currency, commodity prices, broad rate levels, geopolitical events — as opposed to the scheduled macro *events* (Fed day, CPI print) `CalendarProvider` already covers.

**Why deferred:** lowest priority of the slow-tier providers parked here. The scheduled-event slice that actually matters intraday is already covered; unscheduled macro drift matters far more for swing/position holding periods than for a scanner-driven intraday system, and the "keep the door open" reasoning (`confirmed-decisions.md` #21) applies with less force here than to Fundamentals or Expectation, which have a more direct line to individual per-symbol decisions.

**Trigger to revisit:** same trigger as Fundamentals (#9) — swing/position trading becomes an active goal — but revisit it after #9 and #10, not alongside them.

**Where it would plug in:** `trading_intelligence/context_engine/providers/macro_provider.py`, same slow-refresh pattern as Fundamentals.

---

## 13. Causal Psychology Inference / Market Participant Taxonomy

**What it is:** going beyond Participation's observable buy/sell imbalance (`../architecture/trading-intelligence-architecture.md` §4, shipped) to explain *why* — panic vs. genuine excitement vs. short covering vs. options-hedging flow — and, relatedly, a real breakdown of who's trading (retail, institutions, HFTs, options dealers) rather than an inferred aggregate.

**Why deferred:** distinguishing these causes needs data this system has no confirmed source for — short interest, options gamma exposure/dealer positioning, order-flow-level participant classification. Labeling a volume spike "panic" using only price/volume data that's equally consistent with short covering would be a confidently wrong label, not a useful one — worse than leaving it unlabeled. Same calibration discipline already applied to Uncertainty Propagation (#3): don't dress up a guess as inference.

**Trigger to revisit:** confirmed access to options-flow or short-interest data (via IBKR or a dedicated feed). Once that exists, this becomes a genuine inference layer built on real signal rather than a relabeling of Participation.

**Where it would plug in:** a new Context provider, or, if the inference turns out complex enough to need its own reasoning loop, a discrete module reading Participation plus the new data source — evaluate which it needs at that time rather than assuming now.

---

## 14. Position Monitor / Trade Management Integration for Manually-Initiated Positions

**What it is:** once a manually-initiated trade (`../architecture/trading-intelligence-architecture.md` §18) is filled, letting Position Monitor actively manage it — move stops, flag weakening, suggest partial exits — the same way it already does for AI-originated positions; and extending the Trade Management widget with manual-specific controls (e.g. hotkey-driven `MOVE_STOP` / close-fraction commands) for positions the human is actively watching.

**Why deferred:** explicitly scoped out of the first manual-trading iteration on purpose, not overlooked — see `trading-intelligence-architecture.md` §18.8. Building it now would mean designing Position Monitor's behavior for a position type (`origin="manual"`) before there's any real usage of manual entry to learn from. A manually-opened position today is recorded in `trades`/`positions` exactly like any other and simply isn't yet under any origin-aware management logic.

**Trigger to revisit:** manual trade entry ships and real usage shows demand for in-position manual control — i.e. traders are actually taking manual entries often enough that "how does Position Monitor treat this" becomes a live question rather than a hypothetical one.

**Where it would plug in:** `position_monitor/monitor.py` gains origin-aware branching (parallel to Trade Planning Engine's `TradeRequest.origin` branching, decision #22); Trade Management widget gains the deferred `MOVE_STOP`/close-fraction `InputCommand` types held back in decision #23. Also unblocks #16 (Emergency actions) — the two share the same prerequisite.

---

## 15. Auto-Focus / Attention-Directing Hotkey Target

**What it is:** having the system itself shift hotkey focus toward whatever's most urgent right now — e.g. automatically focusing the tile where a high-confidence Opportunity just fired, or where a Position Monitor "weakening" flag just appeared — so a fast reflex action can follow immediately without a manual click first.

**Why deferred:** directly conflicts with the safety rule established for hotkey targeting (`trading-intelligence-architecture.md` §18.6/§18.10, decision #24) — `TradeTarget` (renamed from `FocusedTile` in v1.7, decision #27) is only ever supposed to move on a deliberate user action, specifically so a bound hotkey never fires against a target that changed underneath the person without them choosing it. Doing this well needs a genuinely unmistakable UX (e.g. a distinct "suggested" state requiring its own explicit acceptance before it becomes the actual fire target) that doesn't exist yet and shouldn't be improvised under this feature's current scope.

**Trigger to revisit:** a specific, opt-in UX pattern is designed that makes a system-suggested focus change unmistakable and separately confirmable from firing on it.

**Where it would plug in:** `frontend/src/input/` would need a distinct input path (not reusing `TradeTarget` directly) so a system suggestion and a user's actual click never share the same state.

---

## 16. Emergency Actions — `PANIC`, `FLATTEN_SYMBOL`, `CANCEL_ALL_ORDERS`

**What it is:** a small set of hotkey-bound actions for getting out fast — `PANIC` closes every open position immediately, `FLATTEN_SYMBOL` closes just the focused symbol's position, `CANCEL_ALL_ORDERS` pulls every resting order. Arguably some of the highest-value actions a manual trader can have.

**Why deferred:** every one of them needs to read and act on an *existing* position — Position Monitor / Trade Management territory, already explicitly out of scope for this iteration (`trading-intelligence-architecture.md` §18.8, and entry #14 in this file). Building any of them now would mean quietly reopening a boundary that's been confirmed more than once, and would need real construction (an emergency exit path through Execution Engine) that doesn't exist yet.

**Priority note:** unlike most entries in this file, this one shouldn't sit at the back of the queue once its trigger condition is met. When Position Monitor/Trade Management integration for manual positions (#14) starts, these should be near the front of that work, not an afterthought — the value-to-effort ratio on an emergency kill-switch is unusually high once the underlying position-management plumbing exists at all.

**Trigger to revisit:** Position Monitor/Trade Management integration for manual-origin positions (#14) ships.

**Where it would plug in:** a new `Emergency` category action, `SafetyLevel.DOUBLE_CONFIRM` (`trading-intelligence-architecture.md` §18.10) — the two safety levels reserved and unused today exist specifically so this doesn't need another schema migration when it's built. Routes through Execution Engine like any other exit, once Position Monitor can construct one for a manually-tracked position.

---

## 17. Market Data Provider Selection (Polygon.io / Finnhub / Databento / others) — RESOLVED, kept for the comparison notes

**Status:** `PolygonAdapter` (`confirmed-decisions.md` #30) and `FinnhubAdapter` (`confirmed-decisions.md` #32) are both built — this entry has graduated from "deferred idea" to "done," but the vendor comparison below stays on record since it's still the relevant reasoning if a provider ever needs to change (rate-limit pain on Polygon's free tier, a paid-tier upgrade, Databento turning out to be a better fit, etc.). Polygon and Finnhub aren't redundant with each other — Polygon serves historical backfill (which Finnhub's free tier can't do at all), Finnhub serves real-time streaming (which Polygon's free tier can't do at all) — see `confirmed-decisions.md` #33 for how they're coordinated.

**Candidates checked, with findings (Polygon.io and Finnhub chosen and built; the rest are notes for later, not final rankings):**
- **Polygon.io** — verified accessible from Bangladesh by reading its Terms of Service directly: the only geographic restriction is OFAC-embargoed countries and the SDN sanctions list, not a nationality/residency gate. Free/Basic tier confirmed via account: 15-minute delayed, 5 REST calls/minute, no WebSocket access at all. `PolygonAdapter` built around exactly those constraints — see `confirmed-decisions.md` #30 for the design.
- **Finnhub** — free tier confirmed to give genuine real-time WebSocket streaming for US equities (independently verified via a third-party April 2026 latency test, ~150ms, not just Finnhub's own marketing), but historical stock candles are paywalled and 403 on free keys (confirmed via a real GitHub issue). `FinnhubAdapter` built around exactly that split — streaming only, `get_historical()` raises `HistoricalDataUnavailableError` — see `confirmed-decisions.md` #32.
- **Databento** — attestation-based exchange licensing (self-serve, instant approval for non-professional/non-redistributing use), not nationality-gated. No explicit restriction found. Worth a look if Polygon's free-tier limits (especially the 5 calls/minute ceiling) become a real bottleneck, or if Finnhub's free tier ever tightens the way its historical-candles access already did once.
- **Alpaca Market Data** — likely not independent of the same account system that ruled out Alpaca brokerage (`confirmed-decisions.md` #1): account creation still asks for Country of Tax Residence, with Alpaca's own docs noting unlisted countries are paper-trading-only. Not pursued further without direct evidence otherwise.
- **London Strategic Edge** — a real company, but under a year old (first funding round November 2025) and offering an unusually large amount of claimed real-time institutional-grade data entirely free. Real exchange data licensing is genuinely expensive — a company this young giving this much away free is a real yellow flag for anything feeding live trades. Possibly fine for exploratory backtesting once vetted further; not for production use yet.
- **Twelve Data** — surfaced as a currently-active alternative (broad exchange coverage) but not vetted as deeply as the others above, and no longer needed now that Finnhub covers the real-time-streaming gap.

**Trigger to revisit:** the free tier's 5-calls/minute budget becomes a real constraint (e.g. wanting more than a handful of symbols polled at a reasonable cadence), or a paid Polygon tier / a different vendor entirely becomes worth the cost once real trading volume justifies it.

**Where a replacement would plug in:** same place `PolygonAdapter`/`FinnhubAdapter` do — `app/broker_adapters/<vendor>_provider.py` implementing `MarketDataProvider`, registered through the existing `broker_registry` (streaming and/or historical role, as appropriate — see `confirmed-decisions.md` #33) and the existing `GET /market/candles` route. No route changes needed; that's the whole point of the interface (`confirmed-decisions.md` #28).

---

## 18. Workspace Preset Save / Export

**What it is:** saving a named preset of a workspace's grid formation (rows/cols layout) plus which components/panels are configured into it — and, secondarily, exporting a preset (e.g. to share or reuse across machines). Confirmed as a real, wanted feature, not something to discard.

**Correction to the record:** `confirmed-decisions.md` #35 characterized `frontend/src/components/workspace/GridPresetPicker.tsx` as an unrelated pre-existing bug — dead code referencing a `GRID_PRESETS` export and `preset`/`setPreset` context fields that don't exist anywhere, found incidentally during an unrelated task and flagged for a fix-or-delete decision. That technical description is still accurate (the file is still broken exactly that way, and still not imported by anything real). What #35 didn't know: the file isn't random abandoned code — it's an incomplete first attempt at *this* feature, wired against a `preset`/`setPreset` shape `WorkspaceContext` never actually grew, while the real, live grid implementation (`GridPicker.tsx`, `gridLayout`/`setGridLayout`) went a different direction. So the honest status is "a real, wanted feature with one abandoned false start sitting in the tree," not "dead code, disposition TBD."

**Priority:** explicitly not now. `GridPresetPicker.tsx` stays exactly as it is — untouched, still filtered out of `tsc -b` output per #35's existing convention — until this is actually picked up.

**Trigger to revisit:** whenever workspace preset save/(export) actually becomes the next thing to build.

**Where it would plug in:** `WorkspaceContext` would need real `gridLayout`+configured-components snapshot/restore support (a preset is more than just the grid shape — it's the grid shape *and* what's configured into it), most naturally alongside the existing session persistence (`loadSession()`/whatever already persists `infoWidthPx` etc. — same mechanism, larger payload). `GridPresetPicker.tsx` is a starting sketch for the picker UI, not a foundation to build on as-is — the `GRID_PRESETS`/`preset`/`setPreset` shape it assumes doesn't match `gridLayout`'s real shape and would need redesigning against whatever the actual save/restore data model ends up being, not just patched to compile.

---

## 19. Crypto Trading (via IBKR)

**What it is:** extending the platform beyond US equities/ETFs to spot cryptocurrency (BTC, ETH, and a handful of others), trading it through the same IBKR account and, eventually, the same pipeline.

**IBKR side — real capability, eligibility unconfirmed.** IBKR offers genuine spot crypto (real custody via Paxos/Zero Hash, not CFDs, per IBKR's own product pages — a third-party comparison site's CFD claim looks stale/wrong against IBKR's own consistent description elsewhere), gated behind a separate Trading Permissions request (Crypto Basic or Crypto Plus) in Client Portal, subject to a position cap (30% of combined account equity, or $3M). The TWS API supports it directly — a `Crypto` contract type, historical `whatToShow` values of `TRADES`/`BID`/`ASK`/`MIDPOINT`/`BID_ASK` — but **order types are limited to Market and Limit only, no stops**, which would matter once Execution Engine exists. **Not verified: whether Bangladesh is actually an eligible region.** IBKR rolled crypto out market-by-market (US, then EEA as of March 2026, Hong Kong separately via OSL) — one third-party aggregator claims Bangladesh is covered, but that's not confirmed against an authoritative IBKR source. Same "check the real account before assuming" discipline as entry #17's Polygon/Finnhub geographic checks — this needs a direct look at Client Portal's Trading Permissions page, not a search result.

**Why deferred — the real architectural gap isn't the broker, it's session logic.** The data/execution layer already generalizes: `BrokerAdapter`, `Candle`, `Tick`, and `FeatureSet` (`dict[str, float]`) don't encode any assumption about asset class — adding crypto here would mean a new `Crypto` contract branch in `IBKRAdapter` alongside the existing `Stock` one, nothing else. `MarketClock` (`app/core/market_clock.py`) is the actual blocker: Mon–Fri only, a hardcoded NYSE holiday calendar, `is_market_open()`/`current_session()` both hard-return `CLOSED` on weekends — all of it built for a market that closes, which crypto never does. And a real chunk of what's already shipped depends on that directly, not just cosmetically:
- VWAP resets on `is_regular_session()` (09:30–16:00 ET, decision #53) — no equivalent boundary exists for a 24/7 market.
- PDH/PDL/PDC, Gap %, and Daily Levels (decisions #56, #67–68, #59) all assume a discrete trading day with a real open and close — crypto's "day" would have to be an arbitrary UTC-midnight convention invented for this purpose, not an exchange fact `MarketClock` can just report.
- `ScanCadenceSchedule` (`system-design.md` §4.7) is keyed to session windows (tight at the open, sparse midday) — meaningless for something that never opens.

None of this is hard, but it's a second session model to design and maintain (effectively a `CryptoClock` or a `MarketClock` mode), not a config flag — worth being honest about the size of it up front rather than discovering it mid-build.

**Trigger to revisit:** crypto trading becomes an actual near-term goal (not just "worth knowing IBKR can do it"), AND Bangladesh eligibility is confirmed directly in Client Portal.

**Where it would plug in:** `IBKRAdapter` gains a `Crypto` contract branch (broker/execution layer — generalizes cleanly, per above). Session logic is the new work: either a parallel `CryptoClock`-equivalent that reports one continuous 24/7 "session," or a `MarketClock` mode switch — a real design decision, not something to default into silently when this gets picked up. Feature Engine indicators that assume a discrete day (VWAP reset, PDH/PDL, Gap %, Daily Levels) would each need their own decision about what "session" means for an asset that never closes, made explicitly per indicator rather than assumed uniformly.

---

## 20. Time-to-Target Estimator (Temporal Expectation) & Hypothesis Health

**What it is:** a read-only component answering "given this particular setup, how quickly should this price move normally happen if the hypothesis is correct" — not a fixed property of a strategy, but a conditional estimate assembled at query time from four input groups: (a) strategy prior — a per-`StrategyConfig` base velocity input (e.g. `base_velocity_bars`), never the claimed answer itself; (b) market environment — time of day, market/sector regime, catalyst; (c) current setup — momentum, RVOL, acceleration, distance to target; (d) path to target — resistance/support structure standing between entry and target. Output is a range, never a fake-precise timestamp: `{window_low, window_high, source, sample_size}`, `source` one of `"heuristic" | "historical_empirical" | "conditional_model"` — the same provenance discipline `evidence.basis` (`strategy-engine-design.md` §8) already applies to live-vs-closed data, extended to temporal claims so the system is never quietly more confident about "when" than it's earned.

Downstream, this becomes **Hypothesis Health**: Position Monitor compares realized price/time progress against the Estimator's refreshed window on each re-evaluation tick, distinguishing "losing money, hypothesis intact" from "flat P&L, hypothesis already failing" — a materially better signal for its existing "Exit?" question (`trading-intelligence-architecture.md` §13) than price/stop distance alone.

**Why deferred:** three of the four input groups already have a home and need no new engine — (b) is Context Engine's composed-provider model (`NewsFlagProvider`, `SectorCorrelationProvider` — §5) plus Market State Engine's regime dimensions (§4, decision #19) plus `MarketClock`; (c) is Feature Engine's published values, read into `evidence.conditions` the same way MATCH already does; (d) is Daily Levels + `LevelInteractionEngine`'s existing generic touch/hold/reject/conquered tracking (decisions #46, #59–65), which already answers "how contested is this price zone" for any level key. None of that is new work by itself. What's actually new — the Estimator, and Hypothesis Health as a Position Monitor input — has no ground truth to validate against yet: `strategy_engine/` doesn't exist in code (Stage 0 only, decisions #87–88), so there isn't a single `StrategyOutcome` row to check even a heuristic estimate against. Building sophisticated estimation now would mean tuning four variable groups against nothing — the same trap this project has avoided everywhere else (Polygon depth, IBKR access, confidence calibration in #3 above).

**Boundary, stated precisely so it doesn't drift once this is picked up:** `StrategyConfig` supplies a *prior input* (`base_velocity_bars` or equivalent), never the `expected_time_to_target` claim itself — that claim is always the Estimator's output, computed fresh from current context on every call. Freezing an "expected time" onto the immutable, versioned `StrategyConfig` would go stale the moment market regime shifts under a live version; keeping it query-time-only in the Estimator matches `strategy-engine-design.md` §0's own "population of versioned strategy candidates, queried per-context, never looked up as a stored fact."

**Governor and Decision Engine gain no new authority.** Hypothesis Health degrading on an open position is new evidence for Position Monitor's own existing "Exit?" question — closing that position is already squarely Position Monitor's job. Once it closes and capital frees up, a competing opportunity goes through Decision Engine and Governor exactly like any other new opportunity; Governor's derate/veto lane (`approved_reduced`/`watch_only`/`delayed`) may use weak temporal evidence as one more input — same already-earmarked slot as #11 above — but it is never the thing deciding whether an *existing* position stays open. That would blur the arbitration-vs-derating split `strategy-engine-design.md` §6 already locked down.

**Visualization companion, noted for continuity, not scoped:** a spatial rendering where each tracked opportunity/position is an animated figure walking from entry toward target — obstacles from Daily Levels/`LevelInteractionEngine`'s path data, terrain from Context Engine's environment providers (this entry's group (b), and #12's macro layer for the broadest version — war, global economic conditions), pace from the Estimator's current window, visibly slowing or stumbling as Hypothesis Health degrades. Not required for the Estimator to function — a human-facing debug/QA view for whenever this ships, likely a faster gut-check on Estimator misbehavior than a table of numbers. No design work done; parked here only so the idea isn't lost.

**Trigger to revisit:** Strategy Engine reaches Stage 1 (ORB, per `strategy-engine-design.md` §12) AND enough live or backtested `StrategyOutcome` rows exist to check even a bare heuristic against reality. At that point, ship the Estimator minimally — group (a) only, `source="heuristic"`, effectively a pass-through of `StrategyConfig`'s prior — alongside ORB's own Stage 1 build, so `Opportunity`'s schema shape is right from day one. Groups (b)/(c)/(d) and Hypothesis Health stay deferred past that until real outcome data justifies the added complexity.

**Where it would plug in:** `trading_intelligence/time_to_target/estimator.py`, structurally a sibling of `context_engine/` and `world_view/composite.py` — composed, read-only, no owned state, called by Strategy at PROPOSE (`strategy-engine-design.md` §1) and again by Position Monitor per re-evaluation tick (§13's existing `DebounceScheduler` cadence). New fields when built: `Opportunity.expected_time_to_target: Expectation | None` (§4's schema), `StrategyOutcome.time_to_target_seconds: float | None` (§5's schema, `None` if never reached — honest-state, same as every other unmeasured field). `StrategyConfig.params` (§3) gains the prior input only, per the boundary above. One hard prerequisite specific to ORB: opening-range levels (`opening_range_high`/`low`) aren't computed anywhere in the codebase yet — confirmed by grep, not assumed — so ORB needs that built regardless of this entry.

---

## 21. Exit Trigger Attribution (`exit_trigger` on `StrategyOutcome`)

**What it is:** a field distinguishing *who or what initiated* a trade's exit, separate from `exit_reason` (*why* it exited — `target|stop|time|eod_flatten|manual|reversal`, `strategy-engine-design.md` §5). E.g. `exit_trigger: strategy | governor | broker | market_close` — the same stop-out could be the strategy's own logic firing, Governor forcing an early exit under a derate/veto rule (§6), a broker-side event (margin call, halt), or the day-trading rule's forced close. `exit_reason` and `exit_trigger` are orthogonal: a `stop` exit could be strategy-initiated or Governor-forced, and today's schema can't tell the two apart.

**Why deferred:** raised directly during the `StrategyOutcome` schema review (decision #89) alongside `eod_flatten` and `slippage_entry`, both of which shipped in that same decision. This one didn't, for a concrete reason — Governor's derate/veto branches (`approved_reduced`/`watch_only`/`delayed`) are still unimplemented (`trading-intelligence-architecture.md` §12), so there's no live source that would ever populate `exit_trigger: governor` yet. Adding the field now would mean it's `None` for every row until that machinery exists — same "defer generality until a concrete gap appears" reasoning `strategy-engine-design.md` §11 already applies elsewhere, not a rejection of the idea itself.

**Trigger to revisit:** once Governor's derate/veto branches are real and can actually initiate an exit, or once the `backtests`/live split (§7) surfaces a case where *why* an exit happened (`exit_reason`) isn't enough to explain a strategy's apparent underperformance without also knowing *who* cut the trade short.

**Where it would plug in:** `StrategyOutcome.exit_trigger` (`strategy-engine-design.md` §5's Ledger group, additive/optional — doesn't bump `schema_version` when it lands, per that field's own versioning rule).
