// Previous Day Close / High / Low — the three most common "yesterday's
// range" reference levels. All three come from the same previous-session
// candle set, so they're computed together here rather than split into
// three files — see confirmed-decisions.md for why the file split follows
// the user's grouped request rather than one file per individual number.
import type { Candle } from "../types/market";
import { getPreviousTradingDayCandles } from "./sessions";

export interface PreviousDayLevels {
  close: number;
  high: number;
  low: number;
}

export function computePreviousDayLevels(candles: Candle[]): PreviousDayLevels | undefined {
  const prev = getPreviousTradingDayCandles(candles);
  if (prev.length === 0) return undefined;

  const close = prev[prev.length - 1].close;
  let high = -Infinity;
  let low = Infinity;
  for (const c of prev) {
    if (c.high > high) high = c.high;
    if (c.low < low) low = c.low;
  }
  return { close, high, low };
}
