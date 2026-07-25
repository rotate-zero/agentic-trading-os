import type { Candle } from "../types/market";
import { basePriceFor, tickerSeed } from "./tickers";

// Deterministic mock data so Phase 1 doesn't depend on any live source.
// Replace this file's data source with a WebSocket feed in Phase 2 —
// nothing that consumes `Candle[]` needs to change.

function seededRandom(seed: number) {
  let s = seed;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

export function generateMockCandles(symbol: string = "NVDA", count = 240): Candle[] {
  const rand = seededRandom(tickerSeed(symbol));
  const candles: Candle[] = [];
  let price = basePriceFor(symbol);
  const startTime = Math.floor(Date.now() / 1000) - count * 60;

  for (let i = 0; i < count; i++) {
    const drift = (rand() - 0.48) * (price * 0.0035);
    const open = price;
    const close = Math.max(1, open + drift);
    const high = Math.max(open, close) + rand() * (price * 0.0018);
    const low = Math.min(open, close) - rand() * (price * 0.0018);
    const volume = Math.round(5000 + rand() * 20000);

    candles.push({
      time: startTime + i * 60,
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      close: Number(close.toFixed(2)),
      volume,
    });

    price = close;
  }

  return candles;
}

export const MOCK_SYMBOL = "NVDA";

