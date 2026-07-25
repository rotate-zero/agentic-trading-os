import type { Candle } from "../types/market";
import type { Timeframe } from "../types/workspace";

const MINUTES_PER_TIMEFRAME: Record<Timeframe, number> = {
  "1m": 1,
  "5m": 5,
  "15m": 15,
  "1h": 60,
};

// Mock candles are generated at 1-minute granularity; this groups them into
// larger buckets so timeframe switching is a real transformation, not a label
// swap. Same function signature works once real multi-timeframe data exists —
// it would just become a no-op passthrough for whichever timeframe the backend
// already aggregated.
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
