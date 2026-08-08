// Today's pre-market High/Low — the developing range from the current
// calendar day's 4:00-9:30 AM ET window. Unlike previousDayLevels, this is
// NOT a fixed level: it grows as new pre-market candles arrive and is only
// meaningful before/during today's regular session.
import type { Candle } from "../types/market";
import { getTodayPremarketCandles } from "./sessions";

export interface PremarketLevels {
  high: number;
  low: number;
}

export function computePremarketLevels(candles: Candle[]): PremarketLevels | undefined {
  const pm = getTodayPremarketCandles(candles);
  if (pm.length === 0) return undefined;

  let high = -Infinity;
  let low = Infinity;
  for (const c of pm) {
    if (c.high > high) high = c.high;
    if (c.low < low) low = c.low;
  }
  return { high, low };
}
