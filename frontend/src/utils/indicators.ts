// Dispatcher layer: wires config instances (types/workspace.ts) to the
// calculation functions living under frontend/src/indicators/. Each
// indicator's actual math is in its own file there — this file only knows
// how to route a config instance to the right function and shape the
// result for ChartWidget; it does no calculation itself.
import type { Candle } from "../types/market";
import type { HorizontalLevelInstance, PriceIndicatorInstance } from "../types/workspace";
import { HORIZONTAL_LEVEL_LABELS, priceIndicatorLabel } from "../types/workspace";
import { sma } from "../indicators/sma";
import { ema } from "../indicators/ema";
import { vwap } from "../indicators/vwap";
import { computePreviousDayLevels } from "../indicators/previousDayLevels";
import { computePremarketLevels } from "../indicators/premarketLevels";
import { computeCamarillaPivots } from "../indicators/camarillaPivots";
import { computeVPOC } from "../indicators/vpoc";

export type { IndicatorPoint } from "../indicators/types";

// Overlay indicators (SMA/EMA/VWAP) — continuous {time,value}[] series drawn
// as a line on the price pane. Dispatched by instance.type so a new kind is
// one new case here plus its own file under indicators/.
export function computePriceIndicator(candles: Candle[], instance: PriceIndicatorInstance) {
  const label = priceIndicatorLabel(instance);
  const { color, lineWidth, showPriceLabel } = instance;
  switch (instance.type) {
    case "SMA":
      return { label, color, lineWidth, showPriceLabel, data: sma(candles, instance.period ?? 20) };
    case "EMA":
      return { label, color, lineWidth, showPriceLabel, data: ema(candles, instance.period ?? 20) };
    case "VWAP":
      return { label, color, lineWidth, showPriceLabel, data: vwap(candles) };
  }
}

// Horizontal level indicators (Previous Day Close/High/Low, Pre-Market
// High/Low, Camarilla Pivots, VPOC) — resolve to a single price number (or
// undefined when there isn't enough history yet — see sessions.ts). Grouped
// calculations (previousDayLevels, camarillaPivots) are computed once per
// call rather than once per instance, since e.g. all three Camarilla
// resistance levels share the same previous-session lookup; this dispatcher
// doesn't try to cache across calls, but SubWindow.tsx's useMemo means it
// only runs when candles or the level list actually change.
export function computeHorizontalLevel(candles: Candle[], instance: HorizontalLevelInstance) {
  const label = HORIZONTAL_LEVEL_LABELS[instance.type];
  const price = resolveHorizontalLevelPrice(candles, instance.type);
  if (price === undefined) return undefined;
  return { label, price, color: instance.color, lineWidth: instance.lineWidth, lineStyle: instance.lineStyle, showPriceLabel: instance.showPriceLabel };
}

function resolveHorizontalLevelPrice(candles: Candle[], type: HorizontalLevelInstance["type"]): number | undefined {
  switch (type) {
    case "PDC":
    case "PDH":
    case "PDL": {
      const levels = computePreviousDayLevels(candles);
      if (!levels) return undefined;
      return type === "PDC" ? levels.close : type === "PDH" ? levels.high : levels.low;
    }
    case "PMH":
    case "PML": {
      const levels = computePremarketLevels(candles);
      if (!levels) return undefined;
      return type === "PMH" ? levels.high : levels.low;
    }
    case "VPOC":
      return computeVPOC(candles);
    case "CAM_PP":
    case "CAM_R1":
    case "CAM_R2":
    case "CAM_R3":
    case "CAM_R4":
    case "CAM_S1":
    case "CAM_S2":
    case "CAM_S3":
    case "CAM_S4": {
      const pivots = computeCamarillaPivots(candles);
      if (!pivots) return undefined;
      const key = type.slice(4).toLowerCase() as "pp" | "r1" | "r2" | "r3" | "r4" | "s1" | "s2" | "s3" | "s4";
      return pivots[key];
    }
  }
}
