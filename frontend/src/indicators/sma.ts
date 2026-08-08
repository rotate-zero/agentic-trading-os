import type { Candle } from "../types/market";
import type { IndicatorPoint } from "./types";

export function sma(candles: Candle[], period: number): IndicatorPoint[] {
  const out: IndicatorPoint[] = [];
  for (let i = period - 1; i < candles.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += candles[j].close;
    out.push({ time: candles[i].time, value: Number((sum / period).toFixed(2)) });
  }
  return out;
}
