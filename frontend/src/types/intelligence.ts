// Mirrors the OpportunityCreated payload contract in system-design.md §10.3.
// This is the same shape a real Strategy will emit in Phase 5 — the AI Analysis
// Panel is being built against the real contract, not a throwaway shape.

export interface Opportunity {
  symbol: string;
  strategy: string;
  direction: "BUY" | "SELL";
  confidence: number; // 0-100
  reason: string;
  suggested_entry: number;
  suggested_stop: number;
  suggested_target: number;
  timestamp: string;
}
