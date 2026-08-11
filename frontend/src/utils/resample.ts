// NO LONGER CALLED from the live render path (see confirmed-decisions.md —
// frontend timeframe wiring / session-local aggregation). SubWindow.tsx now
// requests config.timeframe directly from the backend via useLiveCandles,
// which candle_aggregator.py serves for 5m/15m/1h (session-aware, unlike
// the index-based bucketing below) and Polygon serves for 1d. Left in place
// rather than deleted — nothing else currently imports it, so it's a
// removal candidate on the next pass through this file, not touched here.
//
import type { Candle } from "../types/market";
import type { Timeframe } from "../types/workspace";

const MINUTES_PER_TIMEFRAME: Record<Timeframe, number> = {
  "1m": 1,
  "5m": 5,
  "15m": 15,
  "1h": 60,
  "4h": 240,
  "1d": 1440,
};

// Mock candles are generated at 1-minute granularity; this groups them into
// larger buckets so timeframe switching is a real transformation, not a label
// swap. Same function signature works once real multi-timeframe data exists —
// it would just become a no-op passthrough for whichever timeframe the backend
// already aggregated.
//
// 4h/1d buckets are added to the map (Timeframe now includes them, see
// types/workspace.ts) but bucketing stays index-position-based like every
// other entry here — NOT aligned to calendar/session boundaries (a real 1d
// bar should start at the exchange's session open, not "whichever 1440-
// candle chunk of loaded history happens to line up"). Not worth fixing
// until real multi-day historical backfill exists to feed it (no free-tier
// provider currently serves 1-minute backfill at all — confirmed decision
// #39) — today, live data plus this app's short-lived in-memory buffer
// rarely spans enough history for a 4h bucket, let alone a 1d one, to
// contain more than a handful of source candles, so the misalignment isn't
// yet visible. Revisit alongside #39.
export function resampleCandles(oneMinCandles: Candle[], timeframe: Timeframe): Candle[] {
  const bucketSize = MINUTES_PER_TIMEFRAME[timeframe];
  if (bucketSize === 1) return oneMinCandles;

  const result: Candle[] = [];
  for (let i = 0; i < oneMinCandles.length; i += bucketSize) {
    const bucket = oneMinCandles.slice(i, i + bucketSize);
    if (bucket.length === 0) continue;
    result.push({
      time: bucket[0].time,
      open: bucket[0].open,
      high: Math.max(...bucket.map((c) => c.high)),
      low: Math.min(...bucket.map((c) => c.low)),
      close: bucket[bucket.length - 1].close,
      volume: bucket.reduce((sum, c) => sum + c.volume, 0),
    });
  }
  return result;
}
