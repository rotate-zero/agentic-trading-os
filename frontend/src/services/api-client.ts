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

export interface FeatureValueNodeWireShape {
  value: number;
  candle_ts: string;
  level_interaction?: LevelInteractionWireShape;
}

export type FeatureUnitWireShape = FeatureValueNodeWireShape | Record<string, FeatureValueNodeWireShape>;

export interface IntelligenceTimeframeWireShape {
  close: number;
  units: Record<string, FeatureUnitWireShape>;
}

export interface IntelligenceStateWireShape {
  symbol: string;
  timeframes: Record<string, IntelligenceTimeframeWireShape>;
}

/** GET /intelligence/state — confirmed decision #47. */
export async function fetchIntelligenceState(symbol: string): Promise<IntelligenceStateWireShape> {
  const url = `${API_BASE_URL}/intelligence/state?symbol=${encodeURIComponent(symbol)}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return (await res.json()) as IntelligenceStateWireShape;
}
