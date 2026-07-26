import type { Candle } from "../types/market";

// "Day average" for the selected timeframe. Mock data has no session/day
// boundary yet (that's the Market Clock, Phase 2/3) so this approximates
// "today" as every bar currently loaded for the active timeframe — once a
// real session boundary exists, this should scope to the actual trading
// day's bars instead of the full loaded window.
export function dayAverageVolume(candles: Candle[]): number {
  if (!candles.length) return 0;
  const total = candles.reduce((sum, c) => sum + c.volume, 0);
  return total / candles.length;
}

// Trailing N-bar average volume, measured back from the most recently loaded
// bar. Clamps to the available candle count so a short data window (or a
// bar count larger than what's loaded) never throws or silently divides by
// the wrong number.
export function trailingAverageVolume(candles: Candle[], barCount: number): number {
  if (!candles.length) return 0;
  const n = Math.max(1, Math.min(barCount, candles.length));
  const slice = candles.slice(candles.length - n);
  const total = slice.reduce((sum, c) => sum + c.volume, 0);
  return total / n;
}
