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
import type { IndicatorPoint } from "../indicators/types";

export type { IndicatorPoint } from "../indicators/types";

// Overlay indicators (SMA/EMA/VWAP) — continuous {time,value}[] series drawn
// as a line on the price pane. Dispatched by instance.type so a new kind is
// one new case here plus its own file under indicators/.
//
// backendSeries (confirmed decision #54, Stage 1 of the chart migration) is
// OPTIONAL and keyed "sma_9"/"ema_20"/"vwap" — the same flat convention
// FeatureSet.features already uses server-side. When Feature Engine has a
// value for this exact instance (matching period AND timeframe), that's
// used directly — Feature Engine becomes the source of truth, not a second
// calculator running alongside sma.ts/ema.ts/vwap.ts. When it doesn't (a
// non-standard period like SMA(37) nobody's configured server-side, or a
// timeframe Feature Engine doesn't compute for at all — see
// feature_engine_sma_periods/feature_engine_ema_periods in config.py),
// this falls back to the exact same local computation as before —
// unchanged, not deprecated — with " (local)" appended to the label so
// the fallback is visible rather than silently indistinguishable from a
// backend-sourced line (system-design.md's "approximations surfaced,
// never hidden" principle, applied here to a data-source difference
// rather than a numeric approximation). frontend/src/indicators/*.ts
// aren't going anywhere yet; they retire file-by-file only once nothing
// calls into them for the periods/timeframes actually in use.
export function computePriceIndicator(
  candles: Candle[],
  instance: PriceIndicatorInstance,
  backendSeries?: Record<string, IndicatorPoint[] | undefined>
) {
  const label = priceIndicatorLabel(instance);
  const { color, opacity, lineWidth, showPriceLabel } = instance;
  switch (instance.type) {
    case "SMA": {
      const period = instance.period ?? 20;
      const backend = backendSeries?.[`sma_${period}`];
      if (backend && backend.length > 0) return { label, color, opacity, lineWidth, showPriceLabel, data: backend };
      return { label: `${label} (local)`, color, opacity, lineWidth, showPriceLabel, data: sma(candles, period) };
    }
    case "EMA": {
      const period = instance.period ?? 20;
      const backend = backendSeries?.[`ema_${period}`];
      if (backend && backend.length > 0) return { label, color, opacity, lineWidth, showPriceLabel, data: backend };
      return { label: `${label} (local)`, color, opacity, lineWidth, showPriceLabel, data: ema(candles, period) };
    }
    case "VWAP": {
      const backend = backendSeries?.["vwap"];
      if (backend && backend.length > 0) return { label, color, opacity, lineWidth, showPriceLabel, data: backend };
      return { label: `${label} (local)`, color, opacity, lineWidth, showPriceLabel, data: vwap(candles) };
    }
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
//
// backendLevels (confirmed decision #58, Stage 1 extended to horizontal
// levels) is OPTIONAL and keyed lowercase exactly as HorizontalLevelType
// already is ("pdh", "cam_r1", "vpoc", ...) — see
// useFeatureEngineLevels.ts's own docstring for where it comes from. Same
// prefer-backend-fall-back-to-local pattern computePriceIndicator already
// established for SMA/EMA/VWAP: when Feature Engine has a value for this
// exact level, it's used directly; when it doesn't (no previous trading
// day within the configured lookback yet — an honest gap, not an error),
// this falls back to the EXACT SAME local computation as before,
// unchanged, with " (local)" appended to the label.
export function computeHorizontalLevel(
  candles: Candle[],
  instance: HorizontalLevelInstance,
  backendLevels?: Record<string, number | undefined>
) {
  const label = HORIZONTAL_LEVEL_LABELS[instance.type];
  const backendPrice = backendLevels?.[instance.type.toLowerCase()];
  const price = backendPrice ?? resolveHorizontalLevelPrice(candles, instance.type);
  if (price === undefined) return undefined;
  return {
    label: backendPrice !== undefined ? label : `${label} (local)`,
    price,
    color: instance.color,
    opacity: instance.opacity,
    lineWidth: instance.lineWidth,
    lineStyle: instance.lineStyle,
    showPriceLabel: instance.showPriceLabel,
  };
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
