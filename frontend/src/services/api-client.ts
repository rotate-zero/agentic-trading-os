import { API_BASE_URL } from "../config";
import type { Candle } from "../types/market";

// Matches backend CandleClosed's model_dump(mode="json") shape exactly
// (backend/app/schemas/events/market_data.py) — candle_ts serializes to
// an ISO 8601 string, not unix seconds. Exported so callers building a
// WireMessage's payload into a Candle (useLiveCandles) can type it
// properly instead of casting through `unknown`.
export interface CandleWireShape {
  timeframe: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  candle_ts: string;
}

/**
 * The one real shape seam between backend and frontend: Candle.time
 * (frontend, unix seconds — what Lightweight Charts wants) vs candle_ts
 * (backend, ISO string). Every other field already matches field-for-field.
 */
export function toCandle(wire: CandleWireShape): Candle {
  return {
    time: Math.floor(new Date(wire.candle_ts).getTime() / 1000),
    open: wire.open,
    high: wire.high,
    low: wire.low,
    close: wire.close,
    volume: wire.volume,
  };
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    return body.detail ?? res.statusText;
  } catch {
    return res.statusText;
  }
}

/**
 * Backfill for a symbol — GET /market/candles. Throws ApiError with the
 * backend's actual detail message (e.g. "No historical provider
 * connected...") rather than a generic fetch failure, so callers can
 * show something useful instead of a blank chart with no explanation.
 */
export async function fetchCandles(symbol: string, count = 240, timeframe = "1m"): Promise<Candle[]> {
  const url = `${API_BASE_URL}/market/candles?symbol=${encodeURIComponent(symbol)}&count=${count}&timeframe=${timeframe}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  const data = (await res.json()) as { symbol: string; candles: CandleWireShape[] };
  return data.candles.map(toCandle);
}

/**
 * Tells the backend to start streaming a symbol — POST /market/subscribe.
 * Provider-agnostic (backend/app/api/routes/market.py's generic route,
 * not /finnhub/subscribe or /market-data/subscribe specifically) — the
 * frontend shouldn't need to know which provider is actually connected.
 */
export async function subscribeSymbol(symbol: string): Promise<void> {
  const url = `${API_BASE_URL}/market/subscribe?symbol=${encodeURIComponent(symbol)}`;
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
}

// Matches GET /intelligence/state's response shape exactly (backend/app/
// api/routes/intelligence.py) — confirmed decision #47/#48. A unit bucket
// is EITHER a single value node (PDH/PDL/VWAP-style — no numeric period)
// OR a map of period -> value node (SMA/EMA-style) — the backend's
// `_parse_level_key` picks one shape or the other per key, never both, so
// this is a genuine union, not something to force into one shape here.
export interface LevelInteractionHoldingWireShape {
  anchor_price: number;
  entered_from: "below" | "above" | null;
  entered_ts: string;
}

export interface LevelInteractionWireShape {
  zone: "below" | "inside_aura" | "above";
  touch_count_today: number;
  trading_day: string;
  // Confirmed decision #49 — always present, not just while holding.
  // distance_pct's reference point differs by zone (see the backend's
  // own docstring, LevelInteractionEngine.get_snapshot()): anchored to
  // touch start while holding, live against the CURRENT level value
  // otherwise. null only in the narrow startup window before any close
  // has been cached yet.
  seconds_in_zone: number;
  distance_pct: number | null;
  holding?: LevelInteractionHoldingWireShape;
}

// SMA/EMA slope family (confirmed decisions #83, #85) — sma_slope()/
// ema_slope() (backend indicators/sma.py, indicators/ema.py) publish
// `slope`/`r2` always, `slope_pct`/`slope_angle` only once the current
// SMA/EMA value is nonzero (see that function's own docstring). Nested
// under the OWNING period's FeatureValueNodeWireShape below as of
// decision #85 — previously each of the four rendered as its own
// standalone top-level unit (`_parse_level_key`'s digit-suffix rule
// never grouped them), which is the bug #85 fixes.
export interface FeatureSlopeWireShape {
  slope: number;
  r2: number;
  slope_pct?: number;
  slope_angle?: number;
}

export interface FeatureValueNodeWireShape {
  value: number;
  candle_ts: string;
  level_interaction?: LevelInteractionWireShape;
  slope?: FeatureSlopeWireShape;
}

export type FeatureUnitWireShape = FeatureValueNodeWireShape | Record<string, FeatureValueNodeWireShape>;

export interface IntelligenceTimeframeWireShape {
  close: number;
  units: Record<string, FeatureUnitWireShape>;
}

// Daily Levels (confirmed decisions #59-#61) — a clustered support/
// resistance zone. No level_interaction field yet (unlike
// FeatureValueNodeWireShape above) — Stage 3, LevelInteractionEngine
// reading daily_levels rather than just FeatureSet.features, isn't built.
// level_id is NOT yet stable across days (Stage 1/backend engine.py's own
// docstring flags this same limitation) — don't key any client-side
// state off it expecting continuity yet.
export interface DailyLevelWireShape {
  level_id: string;
  price: number;
  strength: number;
  distinct_candle_count: number;
}

export interface IntelligenceStateWireShape {
  symbol: string;
  timeframes: Record<string, IntelligenceTimeframeWireShape>;
  // Symbol-scoped, not nested under any one timeframe — see
  // GET /intelligence/state's own module docstring for why.
  daily_levels: DailyLevelWireShape[];
}

/** GET /intelligence/state — confirmed decision #47. */
export async function fetchIntelligenceState(
  symbol: string,
  dailyLevelsLookbackDays?: number | null
): Promise<IntelligenceStateWireShape> {
  let url = `${API_BASE_URL}/intelligence/state?symbol=${encodeURIComponent(symbol)}`;
  // null/undefined both mean "server default" (confirmed decision #62) —
  // only append the param when a caller actually chose a specific
  // lookback, so every OTHER consumer of this function (FeatureEnginePanel,
  // etc.) keeps getting exactly the response shape it always has.
  if (dailyLevelsLookbackDays != null) {
    url += `&daily_levels_lookback_days=${encodeURIComponent(dailyLevelsLookbackDays)}`;
  }
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as IntelligenceStateWireShape;
}

// Matches GET /intelligence/series's response shape (confirmed decision
// #54, Stage 1 of the chart migration) — one array per "sma_9"/"ema_20"/
// "vwap" key, same flat-dict-of-keys convention FeatureSet.features
// already uses server-side (decision #50's D1), not a second convention
// to learn. A missing key or an empty array both mean "no backend value
// for this — never warmed up, or this period/timeframe combo isn't one
// Feature Engine computes" — callers (useFeatureEngineSeries) don't need
// to distinguish the two.
export interface FeatureSeriesPointWireShape {
  candle_ts: string;
  value: number;
}

export interface FeatureSeriesWireShape {
  symbol: string;
  timeframe: string;
  series: Record<string, FeatureSeriesPointWireShape[]>;
}

/**
 * GET /intelligence/series — confirmed decision #54. Chart backfill for
 * SMA/EMA/VWAP, as distinct from fetchIntelligenceState's single "current
 * value" snapshot above — see that route's own module docstring for why
 * one endpoint can't serve both needs.
 */
export async function fetchFeatureSeries(
  symbol: string,
  timeframe: string,
  count = 240,
): Promise<FeatureSeriesWireShape> {
  const url = `${API_BASE_URL}/intelligence/series?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&count=${count}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as FeatureSeriesWireShape;
}

// Matches GET /intelligence/opportunities's response shape (confirmed
// decision #114, Stage 2/D10) — a thin passthrough of
// OpportunityCache.get_snapshot() (backend/app/trading_intelligence/
// opportunity_cache.py). Field names/types below are copied directly from
// the real Opportunity model (backend/app/strategy_engine/base_strategy.py),
// re-verified against that file's current contents, NOT from
// frontend/src/types/intelligence.ts's old Phase-5-placeholder shape —
// that guessed shape (symbol/reason/suggested_entry/suggested_stop/
// suggested_target/timestamp as flat top-level fields) never matched what
// actually got built. Real, load-bearing differences: no `symbol` field
// on the payload itself (it's the outer dict's key — see
// OpportunityWireShape's own comment below); no `suggested_entry`
// anywhere (Trade Planning Engine, which would compute one, isn't built);
// `suggested_stop`/`suggested_target` are `structural_invalidation`/
// `structural_target` — the price at which the strategy's own thesis is
// falsified, not a refined trade-ready number (strategy-engine-design.md
// §4); `reason` is NOT a top-level field — it lives nested inside
// `evidence` alongside `conditions`/`basis` (same §4: `reason` is a
// human-readable sentence GENERATED from `conditions` for display,
// `conditions` is the literal MATCH-stage values Performance Intelligence
// later queries). `confidence` is 0-100, not 0-1 (scoring_utils.py's
// `_clamp` default range, confirmed against every real strategy's own
// score_confidence()).
export interface OpportunityEvidenceWireShape {
  conditions: Record<string, number | string>;
  // Present on every real v1 strategy as of decision #99/#111 (all 7
  // populate both) — optional in the type anyway, since `Opportunity.
  // evidence: dict` on the backend is untyped Python and nothing
  // structurally guarantees a future strategy keeps setting either key.
  reason?: string;
  basis?: "live" | "closed";
}

export interface OpportunityWireShape {
  strategy: string;
  version: string;
  direction: "BUY" | "SELL";
  confidence: number; // 0-100
  structural_invalidation: number;
  structural_target: number;
  expected_horizon_minutes?: number | null;
  evidence: OpportunityEvidenceWireShape;
  status: "potential" | "waiting" | "actionable" | "expired";
  wait_reason?: string | null;
  wait_expires_at?: string | null;
  setup_detected_at: string;
  confirmed_at?: string | null;
  decided_at?: string | null;
  // OpportunityCache's own wall-clock read of when IT received the
  // OpportunityCreated event — deliberately separate from
  // setup_detected_at/confirmed_at/decided_at above (all real domain
  // timestamps, derived from features.candle_ts — see base_strategy.py's
  // §7 backtest-safety invariant). Not the field this UI displays as
  // "when" — see useOpportunities.ts for which one was picked and why.
  received_at: string;
}

// {"symbols": {"<TICKER>": {"<strategy_name>": {...OpportunityWireShape}}}}
// — get_snapshot()'s own shape, confirmed decision #114. A (symbol,
// strategy) pair this process has never received an OpportunityCreated
// for is simply absent (honest state over fabricated state, same
// convention every other engine's get_snapshot() already follows) — this
// is NOT pre-populated for every symbol/every registered strategy.
export interface OpportunitiesSnapshotWireShape {
  symbols: Record<string, Record<string, OpportunityWireShape>>;
}

/**
 * GET /intelligence/opportunities — confirmed decision #114. `symbol`
 * scopes to one ticker (same `?symbol=` convention fetchIntelligenceState
 * already uses); omit to get every (symbol, strategy) pair this backend
 * process currently has cached.
 */
export async function fetchOpportunities(symbol?: string): Promise<OpportunitiesSnapshotWireShape> {
  const url = symbol
    ? `${API_BASE_URL}/intelligence/opportunities?symbol=${encodeURIComponent(symbol)}`
    : `${API_BASE_URL}/intelligence/opportunities`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as OpportunitiesSnapshotWireShape;
}

// Matches GET /intelligence/strategy-outcomes's response shape (decision
// #123). Field names/types copied directly from `schemas/performance.py`'s
// `StrategyOutcome` (re-verified against that file's current contents) —
// the route validates every ORM row through that exact Pydantic contract
// before returning it (`model_validate(row, from_attributes=True).
// model_dump(mode="json")`), so this is the real, exact wire shape, not a
// guess. `outcome_id`/`opportunity_id`/`backtest_run_id`/
// `feature_snapshot_id` serialize as plain strings (JSON has no UUID
// type); `trading_day` as "YYYY-MM-DD"; every `datetime` field as a full
// ISO 8601 string.
export interface StrategyOutcomeWireShape {
  outcome_id: string;
  opportunity_id: string;
  schema_version: number;
  strategy_name: string;
  strategy_version: string;
  symbol: string;
  origin: "auto" | "manual";
  is_backtest: boolean;
  backtest_run_id: string | null;
  trading_day: string;
  setup_detected_at: string;
  signal_confirmed_at: string | null;
  decided_at: string | null;
  entry_filled_at: string;
  exit_filled_at: string;
  holding_seconds: number;
  direction: "BUY" | "SELL";
  entry_price: number;
  entry_qty: number;
  exit_price: number;
  exit_qty: number;
  commission_total: number | null;
  slippage_entry: number | null;
  realized_pnl: number;
  realized_r: number;
  exit_reason: "target" | "stop" | "time" | "eod_flatten" | "manual" | "reversal";
  structural_invalidation: number;
  structural_target: number;
  final_stop: number;
  final_target: number;
  confidence_at_signal: number;
  evidence: Record<string, unknown>;
  market_state_at_entry: Record<string, unknown>;
  context_at_entry: Record<string, unknown>;
  market_state_at_exit: Record<string, unknown>;
  context_at_exit: Record<string, unknown>;
  feature_snapshot_id: string | null;
}

export interface StrategyOutcomesWireShape {
  outcomes: StrategyOutcomeWireShape[];
}

/**
 * GET /intelligence/strategy-outcomes — decision #123, `is_backtest`/
 * `backtest_run_id` isolation added by decision #130 and `sweep_id`
 * filtering added by decision #165. Raw recent-rows
 * read, most recent `exit_filled_at` first, capped by `limit` (backend
 * default 50 when omitted, same `Query(default, le=cap)` convention
 * `fetchFeatureSeries`'s `count` param already uses — not `symbol`-scoped
 * like fetchOpportunities above, since `strategy_outcomes` has no symbol
 * filter on this route (deliberately global — see the route's own
 * docstring).
 *
 * The four supported inputs are `limit`, `isBacktest`, `backtestRunId`,
 * and `sweepId`. `isBacktest` defaults to the backend's own default (`false`) when
 * omitted — matching the exact live/backtest strict-selector semantics
 * `fetchWinRateByHour`/`fetchExpectancyBySessionType` below already use
 * via `PerformanceAnalyticsFilters.isBacktest`. `backtestRunId` narrows
 * further to one specific backtest run; the backend rejects it with a
 * 400 (surfaced here as a thrown `ApiError`, not a silently-empty
 * result) if `isBacktest` isn't also `true` — see the route's own
 * docstring for why. `sweepId` resolves membership through the backend's
 * `strategy_outcomes.backtest_run_id -> backtests.run_id -> sweep_id`
 * join and has the same `isBacktest === true` requirement. Both IDs may
 * be supplied and are AND-combined; malformed IDs surface the backend's
 * 400 as `ApiError`, while valid unknown IDs return an empty collection.
 * Kept as independent, individually-optional positional params for
 * source compatibility with existing callers. Query construction
 * still switches to the conditional-append-if-present pattern
 * `_performanceAnalyticsQuery` below already established, rather than
 * this function's own previous single-param ternary — that ternary
 * shape stops scaling past one optional param for the same reason
 * `_performanceAnalyticsQuery`'s own comment gives.
 *
 * `strategy_outcomes` has zero real LIVE rows in production today (no
 * Execution Engine/Position Monitor writes to it yet), but does have
 * real, persisted BACKTEST rows as of decision #128 (Backtest Runner
 * v1) — genuine rows representing simulated execution, not fabricated
 * ones. An empty `outcomes` array from the default (live-only) call is
 * still the honest, expected response today, not an error.
 */
export async function fetchStrategyOutcomes(
  limit?: number,
  isBacktest?: boolean,
  backtestRunId?: string,
  sweepId?: string,
): Promise<StrategyOutcomesWireShape> {
  const parts: string[] = [];
  if (limit !== undefined) parts.push(`limit=${encodeURIComponent(limit)}`);
  if (isBacktest !== undefined) parts.push(`is_backtest=${isBacktest}`);
  if (backtestRunId !== undefined) parts.push(`backtest_run_id=${encodeURIComponent(backtestRunId)}`);
  if (sweepId !== undefined) parts.push(`sweep_id=${encodeURIComponent(sweepId)}`);
  const url = `${API_BASE_URL}/intelligence/strategy-outcomes${parts.length > 0 ? `?${parts.join("&")}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as StrategyOutcomesWireShape;
}

// Matches GET /intelligence/backtest-runs's response shape (decision
// #136). Field names/types copied directly from `schemas/performance.py`'s
// `BacktestRun` (re-verified against that file's current contents) — the
// route validates every ORM row through that exact Pydantic contract
// before returning it (`model_validate(row, from_attributes=True).
// model_dump(mode="json")`), same posture StrategyOutcomeWireShape's own
// comment above already documents for its route. `run_id`/`sweep_id`
// serialize as plain strings (JSON has no UUID type); `date_range_start`/
// `date_range_end` as "YYYY-MM-DD"; `created_at` as a full ISO 8601
// string. This is run-LEVEL metadata (one row per backtest run) — a
// different table (`backtests`) and a different granularity than
// StrategyOutcomeWireShape above (one row per closed trade a run
// produced); the two are related only via `run_id`/`backtest_run_id`,
// never merged into one shape.
export interface BacktestRunWireShape {
  run_id: string;
  sweep_id: string;
  strategy_name: string;
  strategy_version: string;
  config_hash: string;
  symbol_universe: string[];
  date_range_start: string;
  date_range_end: string;
  data_version: string;
  feature_version: string;
  walk_forward_fold: number | null;
  is_holdout: boolean;
  created_at: string;
}

export interface BacktestRunsWireShape {
  backtest_runs: BacktestRunWireShape[];
}

/**
 * GET /intelligence/backtest-runs (decision #136) — this table's first
 * frontend reader. Newest-first by `created_at`, capped by `limit`
 * (backend default 50, hard cap 500 — same `Query(default, le=cap)`
 * convention `fetchStrategyOutcomes`'s own `limit` already follows).
 * `runId`/`strategyName`/`sweepId` are independent, optional, AND-combined
 * filters, matching the route's own filter semantics exactly — same
 * conditional-append query construction `fetchStrategyOutcomes` above
 * already uses, not a new pattern. `runId`/`sweepId` are real UUID
 * columns server-side; a malformed value for either throws `ApiError`
 * with the backend's real 400 detail (surfaced, not swallowed), same
 * posture `fetchStrategyOutcomes` already takes for a malformed
 * `backtestRunId`.
 *
 * `run_id` is `BacktestRunRecord`'s own primary key (confirmed directly
 * against `backend/app/models/trading_intelligence.py`), so passing
 * `runId` alone can only ever resolve zero or one row — the shape this
 * function's one real caller (`useBacktestRuns.ts`) relies on to treat
 * `backtest_runs[0]` as "the" run rather than picking arbitrarily from a
 * genuine list.
 */
export async function fetchBacktestRuns(
  limit?: number,
  runId?: string,
  strategyName?: string,
  sweepId?: string,
): Promise<BacktestRunsWireShape> {
  const parts: string[] = [];
  if (limit !== undefined) parts.push(`limit=${encodeURIComponent(limit)}`);
  if (runId !== undefined) parts.push(`run_id=${encodeURIComponent(runId)}`);
  if (strategyName !== undefined) parts.push(`strategy_name=${encodeURIComponent(strategyName)}`);
  if (sweepId !== undefined) parts.push(`sweep_id=${encodeURIComponent(sweepId)}`);
  const url = `${API_BASE_URL}/intelligence/backtest-runs${parts.length > 0 ? `?${parts.join("&")}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as BacktestRunsWireShape;
}

// Matches GET /intelligence/opportunity-conflicts's response shape
// (decision #123) — a genuinely thin passthrough of
// `opportunity_view.get_opportunity_conflicts()` (decision #121); see
// that function's own docstring (backend/app/trading_intelligence/
// opportunity_view.py) for the classification rules behind these two
// shapes. `strategies`/`by_direction`'s entries are the same passthrough
// fields `opportunity_view.py`'s own `_passthrough()` copies — a strict
// subset of OpportunityWireShape above, not the full Opportunity payload.
export interface OpportunityConflictStrategyWireShape {
  strategy: string;
  confidence: number | null;
  setup_detected_at: string | null;
}

export interface OpportunityAgreementWireShape {
  direction: "BUY" | "SELL";
  count: number;
  strategies: OpportunityConflictStrategyWireShape[];
}

export interface OpportunityConflictWireShape {
  count: number;
  by_direction: Partial<Record<"BUY" | "SELL", OpportunityConflictStrategyWireShape[]>>;
}

export interface OpportunityConflictsWireShape {
  agreements: Record<string, OpportunityAgreementWireShape>;
  conflicts: Record<string, OpportunityConflictWireShape>;
}

/**
 * GET /intelligence/opportunity-conflicts — decision #123. `symbol`
 * scopes to one ticker, same convention as fetchOpportunities above; omit
 * to get every symbol this process currently has an agreement or conflict
 * for. A symbol with 0 or 1 currently-cached opportunity is honestly
 * absent from BOTH `agreements` and `conflicts` — not a fabricated
 * "no conflict" entry (see get_opportunity_conflicts()'s own docstring).
 */
export async function fetchOpportunityConflicts(symbol?: string): Promise<OpportunityConflictsWireShape> {
  const url = symbol
    ? `${API_BASE_URL}/intelligence/opportunity-conflicts?symbol=${encodeURIComponent(symbol)}`
    : `${API_BASE_URL}/intelligence/opportunity-conflicts`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as OpportunityConflictsWireShape;
}

// Matches GET /intelligence/win-rate-by-hour's response shape (decision
// #127) — field names/types copied directly from
// `performance_queries.py`'s `HourlyWinRate` dataclass (re-verified
// against that file's current contents), returned via
// `dataclasses.asdict()` with no reshaping in the route. `hour_et` is
// Eastern-time (0-23), converted server-side from the stored UTC
// timestamp — never a raw UTC hour; see that dataclass's own docstring.
export interface HourlyWinRateWireShape {
  hour_et: number;
  total_trades: number;
  win_count: number;
  win_rate: number;
}

export interface WinRateByHourWireShape {
  hourly_win_rates: HourlyWinRateWireShape[];
}

// Matches GET /intelligence/expectancy-by-session-type's response shape
// (decision #127) — field names/types copied directly from
// `performance_queries.py`'s `SessionTypeExpectancy` dataclass.
// `session_type` is `null` for the honest "no calendar.session recorded
// at write time" group, never a synthetic label and never dropped from
// the list — see that dataclass's own docstring.
export interface SessionTypeExpectancyWireShape {
  session_type: string | null;
  trade_count: number;
  expectancy_r: number;
}

export interface ExpectancyBySessionTypeWireShape {
  session_expectancy: SessionTypeExpectancyWireShape[];
}

// Shared real filter surface for both routes below — `strategyName`/
// `strategyVersion` (passing a version without a name is a backend 400,
// the same `_validate_strategy_filters()` guard both
// `get_win_rate_by_hour()`/`get_expectancy_by_session_type()` enforce
// themselves) and `isBacktest` (a strict live/backtest selector already
// on the query layer itself — `false` on the backend when omitted,
// never blended — see performance_queries.py's own docstring). No
// filter beyond these three exists on either function; none is invented
// at this layer.
export interface PerformanceAnalyticsFilters {
  strategyName?: string;
  strategyVersion?: string;
  isBacktest?: boolean;
}

// A small helper, unlike the inline `url +=` pattern fetchIntelligenceState
// above uses — that pattern fits one optional param bolted onto a
// mandatory `symbol=`; both routes below have THREE independent optional
// params and no mandatory one, so building the same way here would mean
// tracking "is this the first param appended yet" by hand at each call
// site. Same conditional-append-if-present semantics either way.
function _performanceAnalyticsQuery(filters?: PerformanceAnalyticsFilters): string {
  const parts: string[] = [];
  if (filters?.strategyName !== undefined) {
    parts.push(`strategy_name=${encodeURIComponent(filters.strategyName)}`);
  }
  if (filters?.strategyVersion !== undefined) {
    parts.push(`strategy_version=${encodeURIComponent(filters.strategyVersion)}`);
  }
  if (filters?.isBacktest !== undefined) {
    parts.push(`is_backtest=${filters.isBacktest}`);
  }
  return parts.length > 0 ? `?${parts.join("&")}` : "";
}

/**
 * GET /intelligence/win-rate-by-hour — decision #127. Global/strategy-
 * level, not symbol-scoped (the route has no `symbol` filter — same
 * posture as fetchStrategyOutcomes above). `strategy_outcomes` has zero
 * real rows in production today (no Execution Engine/Position Monitor
 * writes to it yet); an empty `hourly_win_rates` array is the honest,
 * expected response, not an error. Throws `ApiError` (e.g. status 400
 * for a `strategyVersion` passed without `strategyName`) rather than
 * returning a value — callers must distinguish a request error from a
 * genuinely empty result themselves (see `usePerformanceAnalytics`).
 */
export async function fetchWinRateByHour(filters?: PerformanceAnalyticsFilters): Promise<WinRateByHourWireShape> {
  const url = `${API_BASE_URL}/intelligence/win-rate-by-hour${_performanceAnalyticsQuery(filters)}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as WinRateByHourWireShape;
}

/**
 * GET /intelligence/expectancy-by-session-type — decision #127. Same
 * filter surface, empty-state posture, and error-throwing behavior as
 * fetchWinRateByHour immediately above.
 */
export async function fetchExpectancyBySessionType(
  filters?: PerformanceAnalyticsFilters,
): Promise<ExpectancyBySessionTypeWireShape> {
  const url = `${API_BASE_URL}/intelligence/expectancy-by-session-type${_performanceAnalyticsQuery(filters)}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as ExpectancyBySessionTypeWireShape;
}

// Matches GET /intelligence/context's response shape exactly
// (ContextEngine.get_snapshot(), backend/app/context_engine/engine.py) —
// verified directly against that method's own docstring/implementation.
// "global" is always present (the last evaluate_all() result — today:
// CalendarProvider only), even when `symbol` is omitted or never
// evaluated. "symbols" has at most one entry when `symbol` is passed —
// per get_snapshot()'s own docstring, that entry's "providers" dict
// MERGES the global path's own Calendar output with that ticker's
// Fundamentals/News (server-side merge, decision #96's two-path split),
// so symbols[ticker].providers CAN also carry a "calendar" key,
// duplicating global.providers.calendar. This app reads Calendar only
// from "global" (InfoTab.tsx's GeneralContent, market-wide) and
// Fundamentals/News only from "symbols" (AIAnalysisPanel, per-symbol)
// rather than reading the duplicate — see useContextSnapshot.ts.
export interface CalendarProviderWireShape {
  session: "pre_market" | "open" | "lunch" | "power_hour" | "after_hours" | "closed";
  is_market_open: boolean;
  is_half_day: boolean;
  minutes_since_open: number;
  fed_day: boolean;
  trading_day: string;
}

// FundamentalsProvider (backend/app/context_engine/providers/
// fundamentals.py) — every field is honestly null, not a fabricated
// zero/placeholder, when no `symbol_fundamentals` refresh has ever run
// for this symbol yet (see that file's own `_read` docstring: "every
// field genuinely unknown, not checked-and-confirmed-empty"). `sector`
// is permanently null for every symbol in this build regardless of
// refresh state (Finnhub's `/stock/profile2` has no separate sector
// field — decision #96); `industry` is the one real classification
// field that DOES get populated.
export interface FundamentalsProviderWireShape {
  sector: string | null;
  industry: string | null;
  profile_updated_at: string | null;
  market_cap: number | null;
  market_cap_updated_at: string | null;
  revenue_ttm: number | null;
  net_income_ttm: number | null;
  operating_cash_flow_ttm: number | null;
  financials_period: string | null;
  financials_updated_at: string | null;
  next_earnings_date: string | null;
  earnings_updated_at: string | null;
}

// NewsFlagProvider (backend/app/context_engine/providers/news.py) —
// `present: false` unconditionally for SPY/QQQ/IWM (decision #94:
// Finnhub's `/company-news` mislabels generic broad-market news as
// fund-specific for ETFs) — wire-identical to a genuine "nothing
// recent" result for any other symbol; the frontend cannot and should
// not try to tell the two apart.
export interface NewsProviderWireShape {
  present: boolean;
  count_15m: number;
  recency_seconds: number | null;
  importance: "high" | "medium" | "low" | "none";
}

export interface ContextProvidersWireShape {
  calendar?: CalendarProviderWireShape;
  fundamentals?: FundamentalsProviderWireShape;
  news?: NewsProviderWireShape;
}

export interface ContextGlobalWireShape {
  providers: ContextProvidersWireShape;
  evaluated_at: string | null;
}

export interface ContextSymbolWireShape {
  providers: ContextProvidersWireShape;
  evaluated_at: string;
}

export interface ContextSnapshotWireShape {
  global: ContextGlobalWireShape;
  symbols: Record<string, ContextSymbolWireShape>;
}

/**
 * GET /intelligence/context — confirmed decision #98 (built), unsurfaced
 * anywhere in the UI until this task. `symbol` optional, same convention
 * as fetchOpportunities/fetchOpportunityConflicts above: omit for the
 * market-wide "global" section only, pass a ticker to also populate
 * "symbols" for that one ticker.
 */
export async function fetchContextSnapshot(symbol?: string): Promise<ContextSnapshotWireShape> {
  const url = symbol
    ? `${API_BASE_URL}/intelligence/context?symbol=${encodeURIComponent(symbol)}`
    : `${API_BASE_URL}/intelligence/context`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as ContextSnapshotWireShape;
}

// Matches GET /scanner/state's response shape (v1, on-demand — not the
// continuous MarketActivityScanner docs/architecture/scanner-design.md
// §5 describes, not built yet). `features` only ever carries whichever
// of rvol/gap_pct/session_pct_change/atr_14_pct that symbol actually has
// right now — same "missing means not-yet, not zero" convention as
// IntelligenceStateWireShape above.
export interface ScannerResultWireShape {
  symbol: string;
  score: number;
  inputs_available: number;
  features: Record<string, number>;
}

export interface ScannerStateWireShape {
  universe: string[];
  results: ScannerResultWireShape[]; // already sliced to top_n by the backend
  total_scored: number; // how many of `universe` had data at all, before the top_n cut
  skipped: string[]; // cold start (no 1m FeatureSet yet) — not an error
}

/**
 * GET /scanner/state — v1, on-demand. `symbols` overrides the persisted
 * universe ad hoc, without changing what's actually stored (use
 * addScannerUniverseSymbol/removeScannerUniverseSymbol for that).
 * `topN` defaults to the backend's own default (8) when omitted.
 */
export async function fetchScannerState(symbols?: string[], topN?: number): Promise<ScannerStateWireShape> {
  const params = new URLSearchParams();
  if (symbols && symbols.length > 0) params.set("symbols", symbols.join(","));
  if (topN !== undefined) params.set("top_n", String(topN));
  const query = params.toString();
  const url = `${API_BASE_URL}/scanner/state${query ? `?${query}` : ""}`;

  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as ScannerStateWireShape;
}

export interface ScannerUniverseEntryWireShape {
  symbol: string;
  added_at: string;
}

/** GET /scanner/universe — the persisted universe GET /scanner/state
 * actually scores by default now (scanner_universe_symbols, migration
 * 0004), not app/scanner/universe.py's TEST_UNIVERSE constant. */
export async function fetchScannerUniverse(): Promise<ScannerUniverseEntryWireShape[]> {
  const res = await fetch(`${API_BASE_URL}/scanner/universe`);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  const wire = (await res.json()) as { symbols: ScannerUniverseEntryWireShape[] };
  return wire.symbols;
}

/**
 * POST /scanner/universe — idempotent (re-adding an existing symbol is
 * a no-op, not an error). Throws ApiError with status 400 if the symbol
 * doesn't look like a valid ticker — format-only validation, doesn't
 * confirm the symbol actually trades anywhere (see the backend's own
 * is_valid_ticker_format docstring for exactly what that does and
 * doesn't check).
 */
export async function addScannerUniverseSymbol(symbol: string): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/scanner/universe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ symbol }),
  });
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  const wire = (await res.json()) as { symbol: string; added: boolean };
  return wire.symbol;
}

/** DELETE /scanner/universe/{symbol} — returns whether a row was
 * actually removed; not finding the symbol isn't treated as an error
 * on either end. */
export async function removeScannerUniverseSymbol(symbol: string): Promise<boolean> {
  const res = await fetch(`${API_BASE_URL}/scanner/universe/${encodeURIComponent(symbol)}`, { method: "DELETE" });
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  const wire = (await res.json()) as { symbol: string; removed: boolean };
  return wire.removed;
}

// ---------------------------------------------------------------------
// Backtest Runner trigger route (POST /backtest/run, decision #131) —
// BacktestPanel.tsx's only backend dependency.
//
// The 7 real v1 strategy names and 4 real fixture scenario names below
// are hardcoded rather than fetched from the backend. Neither
// `strategy_engine.scheduler.default_registry()` nor
// `backtest_runner/scenarios.py`'s `available_scenarios()` is reachable
// over HTTP anywhere in this codebase today — exposing either would mean
// adding a new backend route or editing scheduler.py/scenarios.py
// directly, both explicitly out of this task's frontend-only file
// boundary. Both lists were confirmed directly against that Python
// source (not guessed) before being copied here; POST /backtest/run's
// own 400 response body already lists the real valid values as a live
// fallback if either list ever silently drifts out of sync with the
// backend — see BacktestPanel.tsx's error-rendering for why that's
// enough of a safety net not to duplicate here as a second source of
// truth.
export const BACKTEST_STRATEGY_NAMES = [
  "ORB",
  "Gap",
  "Volume Spike",
  "FirstPullback",
  "Reversal",
  "Momentum",
  "VWAP",
] as const;

export interface BacktestScenarioInfo {
  name: string;
  description: string;
  candleCount: number;
}

// Descriptions condensed from scenarios.py's own per-scenario strings;
// candleCount drives BacktestPanel.tsx's "~Ns expected" hint before a
// run starts (see that route's own docstring: ~1 measured second of
// engine-settle time per replayed candle, not a formula — these counts
// are read directly off _SCENARIO_FILES, not computed).
export const BACKTEST_SCENARIOS: BacktestScenarioInfo[] = [
  {
    name: "first_pullback_vwap_dip",
    description:
      "Established uptrend + a dip into, and rejected back out of, VWAP's aura band. Verified to fire FirstPullback (BUY, target hit).",
    candleCount: 130,
  },
  {
    name: "reversal_vwap_break",
    description:
      "Established uptrend + a genuine break-through of VWAP (conquered, not rejected). Verified to fire Reversal (SELL, stopped out).",
    candleCount: 140,
  },
  {
    name: "vwap_neutral_conquest",
    description:
      "Flat/choppy session with a genuine VWAP conquest partway through. Verified to fire VWAP (SELL, target hit).",
    candleCount: 140,
  },
  {
    name: "volume_gated_baseline",
    description:
      "Generic moderate-uptrend session, not engineered to trigger any particular strategy. The only scenario available for ORB / Gap / Volume Spike / Momentum — their MATCH conditions hard-gate on volume_regime_score, which is structurally always 0.0 in any BacktestRunner replay today (no historical data provider wired into the replay stack). Verified to run cleanly (outcomes_recorded=0, no discarded signals) against all four — a 0 here is an honest, expected result, not an error.",
    candleCount: 120,
  },
];

// BacktestRunResult's real fields (backend/app/backtest_runner/runner.py),
// returned verbatim by the route via dataclasses.asdict() — no
// Performance Analytics wrapping, no reshaping. UUIDs/timestamps stay
// plain strings here, same treatment CandleWireShape's candle_ts gets
// above; nothing in BacktestPanel.tsx needs them as parsed types.
export interface DiscardedSignalWireShape {
  signal_candle_ts: string;
  reason: string;
  detail: string;
}

export interface BacktestRunResultWireShape {
  run_id: string;
  sweep_id: string;
  outcomes_recorded: number;
  discarded_signals: DiscardedSignalWireShape[];
}

/**
 * POST /backtest/run (decision #131) — triggers one real BacktestRunner
 * replay against a named fixture scenario and returns its real
 * BacktestRunResult. All three params are required FastAPI Query(...)
 * params on the real route (confirmed directly against backtest.py —
 * not a JSON body), so this follows subscribeSymbol's existing
 * POST-with-query-params-in-the-URL convention above rather than
 * addScannerUniverseSymbol's JSON-body convention; no other adjustment
 * to this file's fetch/API_BASE_URL usage was needed for a POST call.
 *
 * This remains a synchronous request, but replay settlement uses exact
 * engine/bus queue completion and no longer waits roughly one real second
 * per candle for Market State's live debounce floor (decision #157).
 */
export async function triggerBacktest(
  strategyName: string,
  symbol: string,
  scenario: string,
): Promise<BacktestRunResultWireShape> {
  const url =
    `${API_BASE_URL}/backtest/run?strategy_name=${encodeURIComponent(strategyName)}` +
    `&symbol=${encodeURIComponent(symbol)}&scenario=${encodeURIComponent(scenario)}`;
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as BacktestRunResultWireShape;
}

// ---------------------------------------------------------------------------
// POST /backtest/run/ibkr (decision #152) —
// the real-historical-data sibling to POST /backtest/run above. Confirmed
// directly against backend/app/api/routes/backtest.py's run_ibkr_backtest():
// same POST-with-query-params convention (strategy_name/symbol/start/end,
// no JSON body), and dataclasses.asdict(await runner.run()) on the exact
// same BacktestRunner/BacktestRunResult as the fixture route — so
// BacktestRunResultWireShape above is reused verbatim, not redeclared.
//
// The one real difference this file has to account for: every error this
// route can raise beyond the plain-string 409 (decision #132's live-data
// guard, unchanged) comes back as `detail: {code, message}` — the first
// object-shaped `detail` anywhere in this codebase (confirmed by reading
// backtest.py's _validate_ibkr_range/_ibkr_backtest_client_id and
// ibkr_historical.py's full IBKRHistoricalAcquisitionError hierarchy
// directly, not assumed). The shared parseErrorDetail()/ApiError above stay
// completely untouched — every existing caller's contract (`detail` is
// always a plain string today) keeps holding. This block is purely
// additive: a dedicated error class that preserves `code` alongside the
// message, and a dedicated parser only this wrapper calls.

/**
 * IbkrBacktestError extends ApiError so every existing `instanceof
 * ApiError` check anywhere in this codebase still catches it, while adding
 * the one extra field this route's errors carry that no other route's do:
 * the backend's own stable machine-readable `code` (e.g.
 * "ibkr_historical_timeout"), or `null` when the response was the plain-
 * string 409 shape (live-data guard) or an unrecognized/unparseable body —
 * BacktestPanel.tsx's classifier falls back safely on `null` rather than
 * assuming a code is always present.
 */
export class IbkrBacktestError extends ApiError {
  constructor(
    message: string,
    status: number,
    public readonly code: string | null,
  ) {
    super(message, status);
    this.name = "IbkrBacktestError";
  }
}

/**
 * Reads `detail` once and returns both fields it might carry. Handles all
 * three real shapes this route can return: a plain string (409, decision
 * #132's unchanged live-data guard), `{code, message}` (422/503/400/502/504
 * — every case in backtest.py's _validate_ibkr_range/
 * _ibkr_backtest_client_id and ibkr_historical.py's IBKRHistoricalAcquisitionError
 * subclasses), and — defensively — anything else (network failure before a
 * body exists, a future backend change), which falls back to
 * `res.statusText` with `code: null` rather than surfacing a raw object.
 * Never returns anything but a plain string message; BacktestPanel.tsx
 * never has a stack trace or a raw response object to accidentally render.
 */
async function parseIbkrErrorDetail(res: Response): Promise<{ message: string; code: string | null }> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    const detail = body.detail;
    if (typeof detail === "string") {
      return { message: detail, code: null };
    }
    if (detail && typeof detail === "object" && typeof (detail as { message?: unknown }).message === "string") {
      const code = (detail as { code?: unknown }).code;
      return { message: (detail as { message: string }).message, code: typeof code === "string" ? code : null };
    }
    return { message: res.statusText, code: null };
  } catch {
    return { message: res.statusText, code: null };
  }
}

/**
 * POST /backtest/run/ibkr — acquires real IBKR historical OHLCV first,
 * disconnects the isolated acquisition adapter, then replays synchronously.
 * `start`/`end` must already be timezone-aware UTC ISO-8601 strings (see
 * BacktestPanel.tsx's Eastern-time conversion — this wrapper does no time
 * interpretation of its own, matching `symbol`/`strategyName` above passing
 * through as-is with no client-side reshaping).
 *
 * The request is synchronous and includes external IBKR acquisition, whose
 * duration can vary independently of replay. Replay itself uses decision
 * #157's exact settlement and no longer waits one second per candle.
 */
export async function triggerIbkrBacktest(
  strategyName: string,
  symbol: string,
  start: string,
  end: string,
): Promise<BacktestRunResultWireShape> {
  const url =
    `${API_BASE_URL}/backtest/run/ibkr?strategy_name=${encodeURIComponent(strategyName)}` +
    `&symbol=${encodeURIComponent(symbol)}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    const { message, code } = await parseIbkrErrorDetail(res);
    throw new IbkrBacktestError(message, res.status, code);
  }
  return (await res.json()) as BacktestRunResultWireShape;
}

// ---------------------------------------------------------------------------
// POST /backtest/sweep (decision #163) — the third,
// additive trigger path: one strategy across an explicit symbols×scenarios
// cross-product, run sequentially, sharing one real sweep_id. Confirmed
// directly against backend/app/api/routes/backtest.py's run_backtest_sweep():
// same POST-with-query-params convention as triggerBacktest/
// triggerIbkrBacktest above (no JSON body) — `symbols`/`scenarios` are each
// sent as a REPEATED query key (`?symbols=A&symbols=B`), FastAPI's own
// convention for a `list[str] = Query(...)` param, confirmed against the
// route signature rather than guessed. Every error this route raises
// (unknown strategy_name/scenario, empty symbols/scenarios, batch-size
// exceeded, the unchanged decision #132 live-data guard) is a PLAIN STRING
// `detail` — confirmed directly, every `HTTPException` in this route passes
// a bare f-string, never the `{code, message}` object shape
// triggerIbkrBacktest's route uses — so this reuses the existing shared
// `ApiError`/`parseErrorDetail` exactly like triggerBacktest above, with no
// new error class needed.
//
// Mirrors `_MAX_SWEEP_PAIRS` (backtest.py) as a real, named constant rather
// than a magic number in BacktestPanel.tsx — client-side enforcement is a
// convenience so a doomed request is never sent, but the backend's own
// check (re-verified directly, same value) stays the real authority; if
// these two numbers were ever changed independently, the backend's own 400
// message is still the honest last word, same "not a second source of
// truth" posture BACKTEST_STRATEGY_NAMES/BACKTEST_SCENARIOS's own comment
// block above already states for the 400 fallback.
export const BACKTEST_SWEEP_MAX_PAIRS = 20;

// Mirrors backend/app/api/routes/backtest.py's SweepPairResult/
// BacktestSweepResult dataclasses field-for-field (confirmed directly, not
// guessed) — the route returns `dataclasses.asdict(sweep_result)` verbatim,
// same "no reshaping" posture BacktestRunResultWireShape above already
// documents for the other two trigger routes. `run_id`/`outcomes_recorded`
// are `null` only for a genuine per-pair failure (the route's own
// `SweepPairResult` docstring: a `BacktestRunRecord` row may still exist in
// that case, its `run_id` just isn't retrievable from this response).
// `discarded_signals` reuses `DiscardedSignalWireShape` above verbatim —
// same per-signal shape `BacktestRunResult` already carries, unchanged by
// the sweep route.
export interface BacktestSweepPairResultWireShape {
  symbol: string;
  scenario: string;
  run_id: string | null;
  outcomes_recorded: number | null;
  discarded_signals: DiscardedSignalWireShape[];
  error: string | null;
}

export interface BacktestSweepResultWireShape {
  sweep_id: string;
  strategy_name: string;
  pairs_requested: number;
  pairs_succeeded: number;
  pairs_failed: number;
  runs: BacktestSweepPairResultWireShape[];
}

/**
 * POST /backtest/sweep — one strategy across the explicit cross-product of
 * `symbols` × `scenarios`, sequential, sharing one real `sweep_id`.
 * Fixture-only (no IBKR sweep path exists — see the route's own docstring
 * for why). `symbols`/`scenarios` are sent as repeated query keys via
 * `URLSearchParams.append`, matching `list[str] = Query(...)`'s real wire
 * convention on the backend (confirmed directly, not assumed to be a JSON
 * array body the way e.g. addScannerUniverseSymbol's POST elsewhere in this
 * file is).
 *
 * This remains one fully synchronous HTTP request for the entire sweep —
 * the backend gives no per-pair progress signal (confirmed directly against
 * the route: it builds and returns one complete `BacktestSweepResult` only
 * after every pair has run), so this wrapper has no partial/incremental
 * result to expose either; BacktestPanel.tsx's own wait-state copy is
 * written around that same honest limitation, not routed around it here.
 *
 * Batch-size and unknown-strategy/-scenario validation both happen
 * server-side before any run starts (backend's own pre-execution check) —
 * this wrapper does not duplicate that validation; BacktestPanel.tsx
 * enforces `BACKTEST_SWEEP_MAX_PAIRS` client-side only to avoid sending a
 * request already known to fail, never as a second source of truth for
 * what the backend will actually accept.
 */
export async function triggerBacktestSweep(
  strategyName: string,
  symbols: string[],
  scenarios: string[],
): Promise<BacktestSweepResultWireShape> {
  const params = new URLSearchParams();
  params.append("strategy_name", strategyName);
  for (const symbol of symbols) params.append("symbols", symbol);
  for (const scenario of scenarios) params.append("scenarios", scenario);
  const url = `${API_BASE_URL}/backtest/sweep?${params.toString()}`;
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as BacktestSweepResultWireShape;
}

// ---------------------------------------------------------------------------
// Data Feed Status (Finnhub / Polygon) — read-only visibility into the two
// providers `app/main.py`'s lifespan auto-connects on startup if their API
// keys are configured (soft-fail on a missing key or a real connect error —
// an optional data source must never crash the whole app). Previously
// reachable only via curl against GET /finnhub/status / GET
// /market-data/status; zero frontend representation until this task. See
// useDataFeedStatus.ts for the polling hook that consumes these, and
// components/header/DataFeedStatus.tsx for the small header indicator.

/**
 * GET /finnhub/status (backend/app/api/routes/finnhub_data.py). Finnhub only
 * ever registers as the STREAMING provider, never historical (that route's
 * own module docstring — its free tier can't serve historical candles at
 * all, decision #32/#33), so unlike Polygon below there's no `role` field
 * here to show — connected genuinely just means "connected."
 */
export interface FinnhubStatusWireShape {
  connected: boolean;
}

export async function fetchFinnhubStatus(): Promise<FinnhubStatusWireShape> {
  const res = await fetch(`${API_BASE_URL}/finnhub/status`);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as FinnhubStatusWireShape;
}

/**
 * GET /market-data/status (backend/app/api/routes/market_data.py). Polygon
 * is always the historical provider once connected, and is ALSO promoted to
 * the streaming role — a 15-minute-delayed fallback — whenever nothing else
 * (i.e. Finnhub) has already claimed it (decision #33; confirmed directly
 * against that route's own `broker_registry.get_streaming_provider()`
 * check). `role` is a genuine three-way state, not a boolean, and is
 * deliberately kept that way here rather than collapsed.
 */
export interface MarketDataStatusWireShape {
  connected: boolean;
  role: "historical+streaming" | "historical" | null;
}

export async function fetchMarketDataStatus(): Promise<MarketDataStatusWireShape> {
  const res = await fetch(`${API_BASE_URL}/market-data/status`);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as MarketDataStatusWireShape;
}

/**
 * POST /finnhub/connect (backend/app/api/routes/finnhub_data.py) — the one
 * manual-recovery affordance this task adds, deliberately for Finnhub only.
 * Real, current gap confirmed directly in
 * broker_adapters/finnhub_provider.py: an unexpected WebSocket close sets
 * the adapter's connected flag to false with NO auto-reconnect ("that's
 * Phase 4's Market Data Engine (ConnectionManager)," per that file's own
 * comment) — today, the only way to restore it is to call this route again,
 * and before this task there was no UI path to do that at all, only curl.
 * Confirmed directly with Saqib before adding this wrapper (see the
 * decision #143 log entry for the finding and
 * outcome). Polygon has no equivalent gap — it's REST-polling based with no
 * persistent socket to drop (PolygonAdapter's own docstring) — so it
 * deliberately gets no matching wrapper here.
 *
 * Surfaces the real backend outcomes distinctly rather than collapsing them
 * into a boolean: `"already_connected"` (idempotent — nothing changed),
 * `"connected"` (a real new connection, with the backend's own `note`); a
 * missing `FINNHUB_API_KEY` (400) or a real connect failure (502) both
 * throw via the existing `ApiError`/`parseErrorDetail` convention above,
 * same as every other wrapper in this file — no new error shape invented.
 */
export interface FinnhubConnectResultWireShape {
  status: "connected" | "already_connected";
  note?: string;
}

export async function connectFinnhub(): Promise<FinnhubConnectResultWireShape> {
  const res = await fetch(`${API_BASE_URL}/finnhub/connect`, { method: "POST" });
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as FinnhubConnectResultWireShape;
}

// ---------------------------------------------------------------------------
// IBKR Broker Connection (backend/app/api/routes/broker.py) — manual
// connect/disconnect/subscribe/unsubscribe/status control for the one real
// BrokerAdapter this app has (IBKRAdapter). Previously reachable only via
// curl; zero frontend representation until this task. See
// useBrokerStatus.ts for the polling + action hook that consumes these,
// and components/broker/BrokerPanel.tsx for the new <main> sibling panel.
//
// IBKR requires Gateway/TWS already running and logged in externally
// before POST /broker/connect can succeed (broker.py's own module
// docstring) — there's no auto-connect for IBKR on backend startup the
// way Finnhub/Polygon get one (app/main.py's lifespan), so "not
// connected" is this adapter's normal, expected resting state here, not
// a fault to alarm about.
//
// `symbol` on subscribe/unsubscribe is a real FastAPI QUERY parameter,
// NOT a JSON body — confirmed directly against broker.py's actual route
// signatures (`async def subscribe(symbol: str) -> dict`, no `Body(...)`
// annotation anywhere, so FastAPI treats a bare `str` param as a query
// param regardless of HTTP method) rather than assumed from this task's
// own prompt text, which described it as a body field — same correction
// discipline decision #127's own entry documents for a similarly stale
// prompt claim elsewhere in this codebase. Same
// POST-with-query-params-in-the-URL convention subscribeSymbol() and
// triggerBacktest() above already use for the identical real reason.

export interface BrokerStatusWireShape {
  connected: boolean;
}

/**
 * GET /broker/status — cheap, always-fresh read. IBKRAdapter.is_connected()
 * is a local `self._ib.isConnected()` check (confirmed directly against
 * ibkr_adapter.py) — no round trip to Gateway, so polling this often costs
 * nothing server-side.
 */
export async function fetchBrokerStatus(): Promise<BrokerStatusWireShape> {
  const res = await fetch(`${API_BASE_URL}/broker/status`);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as BrokerStatusWireShape;
}

export interface BrokerConnectResultWireShape {
  status: "connected" | "already_connected";
}

/**
 * POST /broker/connect — takes over BOTH the streaming and historical
 * broker_registry roles from whatever was previously connected there
 * (Finnhub and/or Polygon, if either auto-connected on startup —
 * broker.py's own module docstring explains why that takeover is safe
 * here specifically: connecting IBKR is always a deliberate manual
 * action, unlike backend startup's own provider ordering, which needs a
 * tie-breaking rule instead). A real, expected failure mode: a 502 whose
 * `detail` tells the caller Gateway/TWS isn't running or isn't logged in
 * — thrown via the normal ApiError path like any other failed call, not
 * a special case, so callers can show the backend's own actual message
 * verbatim rather than collapsing it into a generic "connection failed."
 */
export async function connectBroker(): Promise<BrokerConnectResultWireShape> {
  const res = await fetch(`${API_BASE_URL}/broker/connect`, { method: "POST" });
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as BrokerConnectResultWireShape;
}

/**
 * POST /broker/disconnect — always 200, even if nothing was connected;
 * broker_registry clears both the streaming and historical roles
 * regardless, so this is safe to call unconditionally.
 */
export async function disconnectBroker(): Promise<void> {
  const res = await fetch(`${API_BASE_URL}/broker/disconnect`, { method: "POST" });
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
}

export interface BrokerSubscribeResultWireShape {
  status: "subscribed";
  symbol: string;
}

/**
 * POST /broker/subscribe?symbol=... — 400s with "Not connected — call
 * POST /broker/connect first" if nothing's connected yet, or with the
 * backend's own SymbolNotFoundError message (via qualifyContractsAsync's
 * checked-not-ignored result — see ibkr_adapter.py's `_qualify()` for
 * why that check exists) if IBKR can't resolve the symbol to a tradeable
 * contract. Both surface as a normal ApiError with the backend's real
 * detail text.
 */
export async function subscribeBrokerSymbol(symbol: string): Promise<BrokerSubscribeResultWireShape> {
  const url = `${API_BASE_URL}/broker/subscribe?symbol=${encodeURIComponent(symbol)}`;
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as BrokerSubscribeResultWireShape;
}

export interface BrokerUnsubscribeResultWireShape {
  status: "unsubscribed";
  symbol: string;
}

/**
 * POST /broker/unsubscribe?symbol=... — 400s only if nothing's connected
 * at all (adapter is None). Unlike subscribe, unsubscribing a symbol
 * that was never subscribed is NOT itself an error — confirmed directly
 * against ibkr_adapter.py's `unsubscribe()`: it pops the symbol from its
 * own private `_contracts` dict with a `None` default and silently skips
 * the IBKR call entirely when nothing was there, rather than raising.
 */
export async function unsubscribeBrokerSymbol(symbol: string): Promise<BrokerUnsubscribeResultWireShape> {
  const url = `${API_BASE_URL}/broker/unsubscribe?symbol=${encodeURIComponent(symbol)}`;
  const res = await fetch(url, { method: "POST" });
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as BrokerUnsubscribeResultWireShape;
}

// ---------------------------------------------------------------------------
// Market State Engine (decision #147) — GET
// /intelligence/market-state, confirmed decision #98, M4 task 25. Built,
// working, and reachable since then; zero frontend representation before
// this task (confirmed by grep across frontend/src — the only prior
// mentions of "market_state"/"MarketState" anywhere were unrelated
// StrategyOutcome snapshot field names, not this route). Same
// "backend capability nobody can see" gap decision #125 closed for
// Context Engine — this wire shape/fetch wrapper mirrors
// fetchContextSnapshot's own optional-symbol convention exactly.

// MarketStateEngine.get_snapshot()'s own per-symbol row (schemas/events/
// market_state.py::MarketState, model_dump(mode="json")) — verified
// field-for-field against that Pydantic model directly, not assumed from
// models/market_state.py's DB column names (which happen to match here,
// but aren't the same schema). `acceleration_score` is `null` on a
// symbol's first-ever recompute — no prior trend_score yet to derive a
// rate of change from (decision #93) — a normal, expected absence, not
// an error.
export interface MarketStateSymbolWireShape {
  timeframe: string;
  candle_ts: string;
  trend_score: number;
  volatility_regime_score: number;
  volume_regime_score: number;
  vwap_relationship_score: number;
  acceleration_score: number | null;
}

// CrossSymbolState (schemas/events/market_state.py) — SPY/QQQ/IWM's
// synthesized composite (decision #91 §4, this build #97). All 7 score
// fields are required on this type because the engine itself only ever
// constructs one once every field is real (get_snapshot() returns
// "market": null until then, never a partially-filled object — see the
// envelope type below).
export interface MarketStateCompositeWireShape {
  timeframe: string;
  candle_ts: string;
  spy_direction_score: number;
  qqq_direction_score: number;
  iwm_direction_score: number;
  trend_alignment_score: number;
  risk_on_score: number;
  qqq_leadership_score: number;
  iwm_confirmation_score: number;
}

// MarketStateEngine.get_snapshot()'s own envelope — verified directly
// against that method's docstring/implementation, not guessed:
// "symbols" holds at most one entry when a `symbol` argument is passed
// (zero if that symbol hasn't been computed yet — honest absence, never
// a fabricated default), every computed symbol when omitted. "market" is
// included whenever the composite has been synthesized at least once,
// REGARDLESS of which `symbol` was requested or omitted — broad-market
// state isn't scoped to the request, the same way a strategy reading one
// symbol's own state would also want to know what SPY/QQQ/IWM are doing
// without a second call.
export interface MarketStateSnapshotWireShape {
  symbols: Record<string, MarketStateSymbolWireShape>;
  market: MarketStateCompositeWireShape | null;
}

/**
 * GET /intelligence/market-state — confirmed decision #98 (built), thin
 * passthrough of MarketStateEngine.get_snapshot(), unsurfaced anywhere in
 * the UI until this task. `symbol` optional, same convention as
 * fetchContextSnapshot/fetchOpportunities above: omit to get every
 * symbol this process has computed plus the cross-symbol composite; pass
 * a ticker to scope "symbols" to just that one (still alongside "market"
 * whenever available — see MarketStateSnapshotWireShape's own comment).
 */
export async function fetchMarketStateSnapshot(symbol?: string): Promise<MarketStateSnapshotWireShape> {
  const url = symbol
    ? `${API_BASE_URL}/intelligence/market-state?symbol=${encodeURIComponent(symbol)}`
    : `${API_BASE_URL}/intelligence/market-state`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as MarketStateSnapshotWireShape;
}

// Matches GET /intelligence/world-view's response shape (decision #150)
// exactly — verified directly against `WorldViewSnapshot`
// (backend/app/world_view/composite.py), not guessed from the route's
// own docstring. `market_state`/`context` are `MarketStateEngine`'s and
// `ContextEngine`'s own complete, unmodified `get_snapshot(symbol)`
// envelopes — WorldView neither flattens nor changes their honest
// absent-symbol/global/cross-symbol behavior, so they reuse
// `MarketStateSnapshotWireShape`/`ContextSnapshotWireShape` exactly
// rather than re-declaring the same shapes under new names.
// `performance`'s per-population shape reuses `HourlyWinRateWireShape`/
// `SessionTypeExpectancyWireShape` for the same reason: World View's own
// `_read_performance()` converts the exact same
// `get_win_rate_by_hour()`/`get_expectancy_by_session_type()` dataclass
// rows fetchWinRateByHour/fetchExpectancyBySessionType already expose,
// just called once per population (`is_backtest=False`/`True`) and
// nested under "live"/"backtest" instead of returned as two separate
// route responses. `portfolio` is `dict[str, Any] | None` on the
// backend and always `None`/JSON `null` in v1 — Portfolio State has no
// application implementation yet (composite.py's own module docstring);
// this is "source unavailable," never a fabricated empty portfolio.
export interface WorldViewPerformancePopulationWireShape {
  hourly_win_rates: HourlyWinRateWireShape[];
  session_expectancy: SessionTypeExpectancyWireShape[];
}

export interface WorldViewPerformanceWireShape {
  live: WorldViewPerformancePopulationWireShape;
  backtest: WorldViewPerformancePopulationWireShape;
}

export interface WorldViewSnapshotWireShape {
  symbol: string | null;
  market_state: MarketStateSnapshotWireShape;
  context: ContextSnapshotWireShape;
  performance: WorldViewPerformanceWireShape;
  portfolio: Record<string, unknown> | null;
}

/**
 * GET /intelligence/world-view — decision #150, thin passthrough of
 * `WorldView().snapshot(symbol)`, zero frontend representation until
 * this task (confirmed by grep across frontend/src/ before starting).
 * `symbol` optional, same convention as fetchContextSnapshot/
 * fetchMarketStateSnapshot above — it scopes `market_state`/`context`
 * only (both passed through unchanged to their own engines); it has no
 * effect on `performance`/`portfolio`, which are system-wide/unscoped
 * regardless of `symbol` (see WorldViewSnapshotWireShape's own comment
 * and `composite.py`'s module docstring).
 */
export async function fetchWorldView(symbol?: string): Promise<WorldViewSnapshotWireShape> {
  const url = symbol
    ? `${API_BASE_URL}/intelligence/world-view?symbol=${encodeURIComponent(symbol)}`
    : `${API_BASE_URL}/intelligence/world-view`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as WorldViewSnapshotWireShape;
}
