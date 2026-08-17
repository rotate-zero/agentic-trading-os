import { useIntelligenceState } from "./useIntelligenceState";

/**
 * Backend-computed horizontal level values — PDH/PDL/PDC, Camarilla,
 * pre-market H/L, VPOC (confirmed decisions #56, #57, #58) — keyed
 * lowercase exactly as Feature Engine publishes them ("pdh", "cam_r1",
 * "vpoc", ...), matching HorizontalLevelType.toLowerCase() one-to-one, so
 * computeHorizontalLevel (utils/indicators.ts) can look a level up with
 * zero translation table between the two.
 *
 * Deliberately NOT its own REST call or WebSocket subscription — these
 * are all single scalar values, not per-candle series, so there's no
 * "backfill across candle history" need the way useFeatureEngineSeries.ts
 * has for SMA/EMA/VWAP's continuous lines. GET /intelligence/state
 * already serves exactly this (decision #47), and useIntelligenceState.ts
 * already does zero-DB-I/O backfill-plus-live-refetch for it — reused
 * directly here rather than duplicated, the same reasoning Stage 1
 * (decision #54) used to justify NOT reusing it for the series case
 * (there, GET /intelligence/series genuinely hits Postgres, so refetching
 * on every tick would be wasteful; here, refetching a small in-memory
 * snapshot costs nothing).
 *
 * Only non-periodic units (entries.length === 1 && period === null) are
 * included — periodic ones (sma_9, ema_20, ...) belong to
 * useFeatureEngineSeries.ts's overlay-line world, not this one, and
 * wouldn't map onto any HorizontalLevelType regardless.
 */
export function useFeatureEngineLevels(symbol: string, timeframe: string): Record<string, number | undefined> {
  const { timeframes } = useIntelligenceState(symbol);
  const tf = timeframes.find((t) => t.timeframe === timeframe);
  if (!tf) return {};

  const levels: Record<string, number> = {};
  for (const unit of tf.units) {
    if (unit.entries.length === 1 && unit.entries[0].period === null) {
      levels[unit.key] = unit.entries[0].value;
    }
  }
  return levels;
}
