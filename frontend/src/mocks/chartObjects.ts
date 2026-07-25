import type { ChartObject } from "../types/market";
import type { Candle } from "../types/market";

// Stands in for what the Feature Engine (PDH/PDL) and Strategy Engine (markers,
// zones) will push over the Event Bus from Phase 4/5 onward. Same shape either way.

export function generateMockOverlays(candles: Candle[]): ChartObject[] {
  const closes = candles.map((c) => c.close);
  const high = Math.max(...closes);
  const low = Math.min(...closes);
  const markerTime = candles[Math.floor(candles.length * 0.7)].time;
  const markerPrice = candles[Math.floor(candles.length * 0.7)].close;

  return [
    // Structural reference levels get their own color (info blue) — deliberately
    // distinct from the axis text / grid gray so they don't camouflage themselves.
    { type: "horizontal_line", price: Number((high * 1.01).toFixed(2)), label: "PDH", color: "#58A6FF" },
    { type: "horizontal_line", price: Number((low * 0.99).toFixed(2)), label: "PDL", color: "#58A6FF" },
    {
      type: "marker",
      time: markerTime,
      position: "BUY",
      price: markerPrice,
      confidence: 87,
    },
    {
      type: "rectangle",
      top: Number((low + (high - low) * 0.15).toFixed(2)),
      bottom: Number(low.toFixed(2)),
      label: "Support Zone",
      color: "rgba(63, 185, 80, 0.16)",
      borderColor: "rgba(63, 185, 80, 0.4)",
    },
  ];
}
