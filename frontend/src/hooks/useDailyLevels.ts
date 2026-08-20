import { useIntelligenceState } from "./useIntelligenceState";
import type { DailyLevelWireShape } from "../services/api-client";

/**
 * Backend-clustered Daily Levels (confirmed decisions #59-#61) — a thin
 * derivation on useIntelligenceState.ts, same pattern
 * useFeatureEngineLevels.ts already established for PDH/PDL/Camarilla/
 * VPOC: no separate REST call or WebSocket subscription of its own,
 * since useIntelligenceState.ts already does zero-DB-I/O backfill-plus-
 * live-refetch for the whole /intelligence/state payload this is a slice
 * of.
 *
 * Unlike useFeatureEngineLevels.ts, this is symbol-scoped, not
 * (symbol, timeframe)-scoped — daily_levels is the same list regardless
 * of which timeframe the caller happens to be charting (module docstring
 * on GET /intelligence/state), so this hook takes no timeframe argument
 * at all.
 *
 * Returns the raw wire shape directly — level_id, price, strength,
 * distinct_candle_count — rather than reshaping it, since ChartWidget.tsx
 * needs all four fields (price to draw the line, strength for the tag,
 * level_id as React's list key) and there's no union-type wire format to
 * normalize away here the way useIntelligenceState.ts's own normalize()
 * has to for FeatureUnitWireShape.
 *
 * `lookbackDays` (confirmed decision #62) — null/undefined for the
 * server's configured default; a specific value re-clusters server-side
 * from already-cached candles (cheap, no new provider fetch — see
 * engine.py's get_daily_levels() docstring) and refetches immediately
 * when it changes, not on the next live update.
 */
export function useDailyLevels(symbol: string, lookbackDays?: number | null): DailyLevelWireShape[] {
  const { dailyLevels } = useIntelligenceState(symbol, lookbackDays);
  return dailyLevels;
}
