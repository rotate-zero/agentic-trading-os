import type { Opportunity } from "../types/intelligence";
import type { Candle } from "../types/market";

// Stands in for a real Strategy's OpportunityCreated event (Phase 5).
// Generated from the symbol's own candle data so different tickers show
// different, plausible numbers instead of copy-pasted NVDA prices.
export function generateMockOpportunities(symbol: string, candles: Candle[]): Opportunity[] {
  const last = candles[candles.length - 1]?.close ?? 100;
  const now = new Date().toISOString();

  return [
    {
      symbol,
      strategy: "ORB",
      direction: "BUY",
      confidence: 87,
      reason: "Volume expansion with VWAP confirmation",
      suggested_entry: Number((last * 1.0018).toFixed(2)),
      suggested_stop: Number((last * 0.9922).toFixed(2)),
      suggested_target: Number((last * 1.0195).toFixed(2)),
      timestamp: now,
    },
    {
      symbol,
      strategy: "Momentum",
      direction: "BUY",
      confidence: 74,
      reason: "Higher lows with expanding relative volume",
      suggested_entry: Number((last * 1.003).toFixed(2)),
      suggested_stop: Number((last * 0.9935).toFixed(2)),
      suggested_target: Number((last * 1.013).toFixed(2)),
      timestamp: now,
    },
    {
      symbol,
      strategy: "Pullback",
      direction: "SELL",
      confidence: 22,
      reason: "Weak — trend context contradicts short setup",
      suggested_entry: Number((last * 0.998).toFixed(2)),
      suggested_stop: Number((last * 1.005).toFixed(2)),
      suggested_target: Number((last * 0.985).toFixed(2)),
      timestamp: now,
    },
  ];
}

