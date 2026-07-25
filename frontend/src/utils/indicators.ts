import type { Candle } from "../types/market";
import type { IndicatorType } from "../types/workspace";

export interface IndicatorPoint {
  time: number;
  value: number;
}

export function sma(candles: Candle[], period: number): IndicatorPoint[] {
  const out: IndicatorPoint[] = [];
  for (let i = period - 1; i < candles.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += candles[j].close;
    out.push({ time: candles[i].time, value: Number((sum / period).toFixed(2)) });
  }
  return out;
}

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

const INDICATOR_COLORS: Record<IndicatorType, string> = {
  EMA9: "#E3B341",
  EMA20: "#F778BA",
  SMA20: "#58A6FF",
  SMA50: "#7EE787",
};

export function computeIndicator(candles: Candle[], type: IndicatorType) {
  const color = INDICATOR_COLORS[type];
  switch (type) {
    case "EMA9":
      return { label: "EMA 9", color, data: ema(candles, 9) };
    case "EMA20":
      return { label: "EMA 20", color, data: ema(candles, 20) };
    case "SMA20":
      return { label: "SMA 20", color, data: sma(candles, 20) };
    case "SMA50":
      return { label: "SMA 50", color, data: sma(candles, 50) };
  }
}
