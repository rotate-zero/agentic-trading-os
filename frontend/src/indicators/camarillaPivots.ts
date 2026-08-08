// Camarilla pivot levels, computed from the previous trading day's
// High/Low/Close using the standard formula. Nine levels: a central pivot
// (PP) plus four resistance (R1-R4) and four support (S1-S4) levels, each
// progressively further from close.
import type { Candle } from "../types/market";
import { getPreviousTradingDayCandles } from "./sessions";

export interface CamarillaPivots {
  pp: number;
  r1: number;
  r2: number;
  r3: number;
  r4: number;
  s1: number;
  s2: number;
  s3: number;
  s4: number;
}

export function computeCamarillaPivots(candles: Candle[]): CamarillaPivots | undefined {
  const prev = getPreviousTradingDayCandles(candles);
  if (prev.length === 0) return undefined;

  let high = -Infinity;
  let low = Infinity;
  for (const c of prev) {
    if (c.high > high) high = c.high;
    if (c.low < low) low = c.low;
  }
  const close = prev[prev.length - 1].close;
  const range = high - low;

  return {
    pp: (high + low + close) / 3,
    r1: close + (range * 1.1) / 12,
    r2: close + (range * 1.1) / 6,
    r3: close + (range * 1.1) / 4,
    r4: close + (range * 1.1) / 2,
    s1: close - (range * 1.1) / 12,
    s2: close - (range * 1.1) / 6,
    s3: close - (range * 1.1) / 4,
    s4: close - (range * 1.1) / 2,
  };
}
