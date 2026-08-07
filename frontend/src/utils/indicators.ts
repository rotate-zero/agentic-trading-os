import type { Candle } from "../types/market";
import type { IndicatorType, PriceIndicatorInstance } from "../types/workspace";
import { priceIndicatorLabel } from "../types/workspace";

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
};

export function computeIndicator(candles: Candle[], type: IndicatorType) {
  const color = INDICATOR_COLORS[type];
  switch (type) {
    case "EMA9":
      return { label: "EMA 9", color, data: ema(candles, 9) };
    case "EMA20":
      return { label: "EMA 20", color, data: ema(candles, 20) };
  }
}

// Generic price-pane indicator instances (types/workspace.ts's
// PriceIndicatorInstance) — SMA today, dispatched the same switch-based way
// as computeIndicator above so a second kind is one new case, not a new
// function shape. Unlike computeIndicator, color/lineWidth come from the
// instance itself (user-editable per instance) rather than a fixed lookup
// table, since that's the whole reason this model exists.
export function computePriceIndicator(candles: Candle[], instance: PriceIndicatorInstance) {
  const label = priceIndicatorLabel(instance);
  switch (instance.type) {
    case "SMA":
      return { label, color: instance.color, lineWidth: instance.lineWidth, data: sma(candles, instance.period) };
  }
}
