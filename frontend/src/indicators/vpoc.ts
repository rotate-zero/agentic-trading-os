// Volume Point of Control — the price level with the most traded volume
// during the previous trading day. Computed by bucketing the previous
// session's candles into fixed-width price bins (keyed by each candle's
// typical price, (high+low+close)/3) and summing volume per bin; VPOC is
// the bin with the most volume, reported as that bin's midpoint.
//
// This is a coarse-but-honest approximation, not a true tick-level volume
// profile: real VPOC needs individual trade prices within each candle,
// which this app only has as OHLCV bars. Bucketing by whole-candle typical
// price is the standard fallback when only candle data is available, and
// is clearly documented as such rather than presented as more precise than
// it is.
import type { Candle } from "../types/market";
import { getPreviousTradingDayCandles } from "./sessions";

const BUCKET_COUNT = 24; // previous session's range split into 24 price bins

export function computeVPOC(candles: Candle[]): number | undefined {
  const prev = getPreviousTradingDayCandles(candles);
  if (prev.length === 0) return undefined;

  let high = -Infinity;
  let low = Infinity;
  for (const c of prev) {
    if (c.high > high) high = c.high;
    if (c.low < low) low = c.low;
  }
  if (high === low) return high; // degenerate range guard — a single flat price

  const bucketSize = (high - low) / BUCKET_COUNT;
  const volumeByBucket = new Array<number>(BUCKET_COUNT).fill(0);
  for (const c of prev) {
    const typicalPrice = (c.high + c.low + c.close) / 3;
    let bucket = Math.floor((typicalPrice - low) / bucketSize);
    bucket = Math.min(BUCKET_COUNT - 1, Math.max(0, bucket));
    volumeByBucket[bucket] += c.volume;
  }

  let maxBucket = 0;
  for (let i = 1; i < BUCKET_COUNT; i++) {
    if (volumeByBucket[i] > volumeByBucket[maxBucket]) maxBucket = i;
  }
  return low + bucketSize * (maxBucket + 0.5);
}
