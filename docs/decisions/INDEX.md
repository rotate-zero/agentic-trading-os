# Decision Index


One line per confirmed decision, #1 through #82, across every file in this directory. Read this first — it's a fraction of the cost of reading the full history, and points to exactly which file has the full text of anything relevant to the current task. See `README.md` for the full read/write/maintain protocol.

| # | Summary | File |
|---|---|---|
| 1 | Broker order of implementation — REVISED. | `archive/001-060.md` |
| 2 | Candle storage | `archive/001-060.md` |
| 3 | Scanner cadence | `archive/001-060.md` |
| 4 | Redis | `archive/001-060.md` |
| 5 | Context Engine is a composed aggregator | `archive/001-060.md` |
| 6 | Governor's decision schema is widened now, implemented narrow. | `archive/001-060.md` |
| 7 | World View is a read-only composite facade | `archive/001-060.md` |
| 8 | Event data contracts are formalized before Phase 1 code | `archive/001-060.md` |
| 9 | Event Bus dispatch is two-lane, not multi-tier priority. | `archive/001-060.md` |
| 10 | Stateful engines that need periodic-but-not-constant recompute (Market State Engine, Position… | `archive/001-060.md` |
| 11 | `docs/` is the authoritative location for all architecture and design documentation | `archive/001-060.md` |
| 12 | Partition-boundary literals in migrations must carry an explicit UTC offset (`+00`), never a bare… | `archive/001-060.md` |
| 13 | Use `ib_async`, not `ib_insync`, for the IBKR adapter. | `archive/001-060.md` |
| 14 | IBKR 2FA: switch to IB Key, not SMS; Gateway set to auto-restart, not auto-logoff. | `archive/001-060.md` |
| 15 | `BrokerAdapter`'s `Tick` type includes `symbol`, distinct from the Event Bus's `PriceUpdated`… | `archive/001-060.md` |
| 16 | Tick-to-candle bucketing lives above the adapter, not inside it. | `archive/001-060.md` |
| 17 | `is_connected()` is part of the `BrokerAdapter` ABC itself, not an IBKR-specific extension. | `archive/001-060.md` |
| 18 | One shared adapter registry (`app/services/broker_registry.py`), not per-route globals. | `archive/001-060.md` |
| 19 | Participation added as a 7th Market State dimension. | `archive/001-060.md` |
| 20 | Context Engine v1 provider list expanded: `SectorCorrelationProvider` and `NewsFlagProvider`. | `archive/001-060.md` |
| 21 | `ContextProvider` is explicitly generalized to arbitrary refresh cadence, not just… | `archive/001-060.md` |
| 22 | Manual trading reuses the existing pipeline via a single `plan(TradeRequest) -> TradePlan`… | `archive/001-060.md` |
| 23 | Manual trade input is abstracted behind a device-agnostic Input Layer, not built for a specific… | `archive/001-060.md` |
| 24 | Hotkey targeting resolves against an explicit, per-tab `FocusedTile`, changed only by deliberate… | `archive/001-060.md` |
| 25 | A single `LONG`/`SHORT` action per direction replaces the… | `archive/001-060.md` |
| 26 | Hotkey actions are modeled generically: Action Categories route dispatch, Safety Levels gate… | `archive/001-060.md` |
| 27 | `FocusedTile` renamed `TradeTarget`; explicit non-triggers documented. | `archive/001-060.md` |
| 28 | Split `BrokerAdapter` into `MarketDataProvider` (base) + `BrokerAdapter(MarketDataProvider)` (adds… | `archive/001-060.md` |
| 29 | `ib_async` pinned to `>=2.1,<3.0`, not `<2.0`. | `archive/001-060.md` |
| 30 | `PolygonAdapter` built around free-tier constraints, not against an assumed real-time feed. | `archive/001-060.md` |
| 31 | `IBKRIngestBridge` renamed `TickIngestBridge`, retyped against `MarketDataProvider`. | `archive/001-060.md` |
| 32 | `FinnhubAdapter` built for genuine real-time streaming; historical stock candles explicitly not… | `archive/001-060.md` |
| 33 | `broker_registry` split into two independent named roles — `streaming` and `historical` — replacing… | `archive/001-060.md` |
| 34 | `main.py`'s auto-connect calls the same shared `connect_polygon()`/`connect_finnhub()` functions… | `archive/001-060.md` |
| 35 | Frontend mock-swap resumed and completed for `candles.ts`; a new generic `POST /market/subscribe`… | `archive/001-060.md` |
| 36 | `generateMockOverlays` (`mocks/chartObjects.ts`) needed an empty-candles guard, since… | `archive/001-060.md` |
| 37 | `UnhandledExceptionMiddleware` (`app/core/error_handling.py`) added as a plain ASGI middleware, NOT… | `archive/001-060.md` |
| 38 | Tests now blank `FINNHUB_API_KEY`/`POLYGON_API_KEY` for the duration of every test… | `archive/001-060.md` |
| 39 | `PolygonAdapter.get_historical()` (and its polling counterpart) now categorizes the free/Basic… | `archive/001-060.md` |
| 40 | Feature Engine kickoff: chart-pane SMA rebuilt as an instance-based model (`PriceIndicatorInstance`… | `archive/001-060.md` |
| 41 | Feature Engine chart-reading indicators expanded significantly: EMA fully migrated onto the SMA… | `archive/001-060.md` |
| 42 | Chart zoom-reset bug fixed; candle-close latency bug fixed; volume bars made fully customizable;… | `archive/001-060.md` |
| 43 | The `candles`/`symbols` tables (scaffolded Phase 2, unused since) now have a writer and a reader —… | `archive/001-060.md` |
| 44 | Frontend chart timeframe wiring fixed at the root; backend gained session-local aggregation for… | `archive/001-060.md` |
| 45 | Backend Feature Engine started: server-side SMA, published as `FeaturesUpdated` on every `1m`… | `archive/001-060.md` |
| 46 | Level Interaction Engine built: touch/holding/rejected/conquered tracking for any level… | `archive/001-060.md` |
| 47 | Backend read side for the Feature Engine panel: `get_snapshot()` on both engines, `GET… | `archive/001-060.md` |
| 48 | Feature Engine panel built (frontend) — `GET /intelligence/state`'s first real consumer — plus a… | `archive/001-060.md` |
| 49 | First real browser feedback on the Feature Engine panel (decision #48) — one real gap closed, one… | `archive/001-060.md` |
| 50 | Chart indicators redirected onto the Feature Engine migration path — decisions #40/#41's… | `archive/001-060.md` |
| 51 | Feature Engine extended to 5m/15m/1h — Stage 2 of the chart migration (decision #50), "Option A2":… | `archive/001-060.md` |
| 52 | EMA added to Feature Engine — Stage 3's first indicator (decision #50), resolving D3: full-window… | `archive/001-060.md` |
| 53 | VWAP added to Feature Engine — Stage 3's second indicator (decision #50). | `archive/001-060.md` |
| 54 | Stage 1 of the chart migration (decision #50): the Chart now actually consumes Feature Engine for… | `archive/001-060.md` |
| 55 | A real local-environment failure, diagnosed and fixed, plus `feature_engine/indicators.py` split… | `archive/001-060.md` |
| 56 | PDH/PDL/PDC, Camarilla pivots, and pre-market H/L added to Feature Engine — Stage 3's remaining… | `archive/001-060.md` |
| 57 | VPOC added to Feature Engine — Stage 3's last indicator, resolving D5 more simply than that… | `archive/001-060.md` |
| 58 | Stage 1 extended to cover all seven horizontal-level types from decisions #56–57 — PDH/PDL/PDC, all… | `archive/001-060.md` |
| 59 | Daily Levels — concept locked across a three-way review (Saqib + Claude + Grok + ChatGPT),… | `archive/001-060.md` |
| 60 | Daily Levels Stage 1 built and tested — clustering algorithm, same-candle validity gate,… | `archive/001-060.md` |
| 61 | Daily Levels Stage 4 built and tested — the indicator is now on the chart. | `archive/061-079.md` |
| 62 | Daily Levels: root-caused Saqib's "no levels showing" report, then shipped a price-range filter, a… | `archive/061-079.md` |
| 63 | Daily Levels Stage 2 built and tested — persistent, price-proximity-reconciled level identity, and… | `archive/061-079.md` |
| 64 | Daily Levels Stage 3 built and tested — LevelInteractionEngine now tracks… | `archive/061-079.md` |
| 65 | Daily Levels menu panel scrollbar fixed, and D1 (the last open item across all four build stages)… | `archive/061-079.md` |
| 66 | Two small standing open items picked up together, deliberately staying off the Trading Intelligence… | `archive/061-079.md` |
| 67 | Feature Engine indicator expansion — direction locked for five new families (ATR, Session % / $… | `archive/061-079.md` |
| 68 | Feature Engine indicator expansion — D1/D2/D3 resolved by Saqib directly (shared daily-candle… | `archive/061-079.md` |
| 69 | Feature Engine indicator expansion — Stage 2 (ATR(1D,14) + ATR%) built and tested against a real… | `archive/061-079.md` |
| 70 | Feature Engine indicator expansion — Stage 3 (Linear Regression) and Stage 4 (KAMA) built together… | `archive/061-079.md` |
| 71 | Relative Volume (RVOL) built and tested against a real local Postgres — a sixth Feature Engine… | `archive/061-079.md` |
| 72 | LiveTickRelay — throttled "tick fluidity" for the chart's currently-forming 1m bar, on a small (max… | `archive/061-079.md` |
| 73 | Chart Style — candlestick vs. | `archive/061-079.md` |
| 74 | Volume Avg lines gained a per-line price-axis label toggle (`showPriceLabel`) — closes the one… | `archive/061-079.md` |
| 75 | On-chart HUD text box — a floating, per-line-configurable Feature Engine readout, pinned to the top… | `archive/061-079.md` |
| 76 | Dropdown-panel clipping fixed for all 5 real panels in the app — a shared placement hook… | `archive/061-079.md` |
| 77 | Opacity added to every hex-color field in the app — SMA/EMA/VWAP lines, horizontal levels, Timer,… | `archive/061-079.md` |
| 78 | Backfilled entry — Saved Layouts (named save/load/delete, JSON export/import) already exists in… | `archive/061-079.md` |
| 79 | `confirmed-decisions.md` split into an open file + frozen archive + index, to keep session-start reading cost bounded | `archive/061-079.md` |
| 80 | Volume Bars opacity confirmed already shipped (#77); real bug was `useDropdownPlacement` inflating panel max-height past available viewport space on narrow/short screens — fixed in the shared hook for every dropdown panel at once. | `confirmed-decisions.md` |
| 81 | Axis-label name half ("PDH", "SMA 9") gained its own on/off toggle (`showNameLabel`), separate from the existing price-value toggle (`showPriceLabel`) — closes a Lightweight Charts quirk where an indicator's name stayed visible even with the value hidden. | `confirmed-decisions.md` |
| 82 | #81's `showNameLabel` toggle extended to Volume Avg lines and Daily Levels, the two indicator types it deliberately left out — every label-bearing indicator now follows the same on/off convention. | `confirmed-decisions.md` |
| 83 | SMA/EMA slope-as-angle, computed in the Feature Engine (live + historical paths), percentage-normalized (not ATR — same cross-timeframe objection as #67), lookback tied to each period, surfaced as a numeric `∠+35.2°` label suffix behind a new per-instance toggle. | `confirmed-decisions.md` |
| 84 | `LevelInteractionEngine.stop()`'s FK-violation-on-teardown race root-caused to `task.cancel()` not actually stopping an in-flight `to_thread` DB write; fixed with a poison-pill queue drain. `CandleRecorder`/`FeatureEngine` flagged as likely sharing the same latent bug, not yet fixed. | `confirmed-decisions.md` |
