// Session VWAP (Volume-Weighted Average Price), anchored to each regular
// session's open (9:30 AM ET) — the standard equities VWAP convention.
// Cumulative from the session's first regular-hours candle, recomputed on
// every call rather than incrementally maintained; this app's data volumes
// (a rolling few hundred candles) make incremental state unnecessary.
//
// Pre-market and after-hours candles are excluded from the accumulation
// (they don't participate in the standard VWAP anchor), so the line only
// appears/extends during regular hours and resets at the next session.
import type { Candle } from "../types/market";
import type { IndicatorPoint } from "./types";
import { toEasternWallClock, isRegularSession } from "./sessions";

export function vwap(candles: Candle[]): IndicatorPoint[] {
  const points: IndicatorPoint[] = [];
  let cumulativePV = 0;
  let cumulativeVolume = 0;
  let currentDay: string | null = null;

  for (const c of candles) {
    const wc = toEasternWallClock(c.time);
    if (!isRegularSession(wc)) continue;
    if (wc.dateKey !== currentDay) {
      currentDay = wc.dateKey;
      cumulativePV = 0;
      cumulativeVolume = 0;
    }
    const typicalPrice = (c.high + c.low + c.close) / 3;
    cumulativePV += typicalPrice * c.volume;
    cumulativeVolume += c.volume;
    if (cumulativeVolume > 0) {
      points.push({ time: c.time, value: Number((cumulativePV / cumulativeVolume).toFixed(2)) });
    }
  }
  return points;
}
