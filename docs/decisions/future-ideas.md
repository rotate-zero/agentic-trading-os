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

**Why it's cheap to defer:** the `BrokerAdapter` interface (`../architecture/system-design.md` §4.1) already means the Market Data Engine doesn't care whether its data source is live or replayed. When Replay is built, it's a new implementation of that interface (or a wrapper feeding the same `PriceUpdated`/`CandleClosed` events), not a redesign of anything upstream. The door was left open for free; nothing today needs to anticipate it further.

**Trigger to revisit:** whenever manual-trading-experience-driven review of specific historical sessions becomes valuable enough to justify the build — no architectural trigger required, this one's just a scheduling/priority call.

**Where it would plug in:** `broker_adapters/replay_adapter.py`, implementing the same `BrokerAdapter` ABC. Would use the deferred `replay_sessions` table (reserved name, not created — `../architecture/system-design.md` §4.13).

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
