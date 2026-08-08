import type { Candle } from "../types/market";
import type { IndicatorPoint } from "./types";

export function ema(candles: Candle[], period: number): IndicatorPoint[] {
  const out: IndicatorPoint[] = [];
  const k = 2 / (period + 1);
  let prev: number | null = null;
  candles.forEach((c, i) => {
    if (i < period - 1) return;
    if (prev === null) {
      // seed with SMA of the first `period` closes
      const seedSlice = candles.slice(0, period);
      prev = seedSlice.reduce((s, x) => s + x.close, 0) / period;
    } else {
      prev = c.close * k + prev * (1 - k);
    }
    out.push({ time: c.time, value: Number(prev.toFixed(2)) });
  });
  return out;
}
