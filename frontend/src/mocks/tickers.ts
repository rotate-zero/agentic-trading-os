export interface MockTicker {
  symbol: string;
  name: string;
  basePrice: number;
}

// Small fixed universe stands in for real symbol search (Phase 1 has no broker/search API yet).
export const MOCK_TICKERS: MockTicker[] = [
  { symbol: "NVDA", name: "NVIDIA Corp", basePrice: 225 },
  { symbol: "TSLA", name: "Tesla Inc", basePrice: 260 },
  { symbol: "AAPL", name: "Apple Inc", basePrice: 195 },
  { symbol: "MSFT", name: "Microsoft Corp", basePrice: 430 },
  { symbol: "AMD", name: "Advanced Micro Devices", basePrice: 145 },
];

export function tickerSeed(symbol: string): number {
  let hash = 0;
  for (let i = 0; i < symbol.length; i++) {
    hash = (hash * 31 + symbol.charCodeAt(i)) % 100000;
  }
  return hash || 1;
}

export function basePriceFor(symbol: string): number {
  return MOCK_TICKERS.find((t) => t.symbol === symbol)?.basePrice ?? 100;
}
