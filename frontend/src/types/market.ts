// Mirrors the Candle + ChartObject contracts defined in
// system-design.md §4.10 (Visualization Engine) and §10 (Event Data Contracts).
// When the real backend exists, these types stay the same — only the data source changes.

export interface Candle {
  time: number; // unix seconds, matches Lightweight Charts' UTCTimestamp
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type ChartObject =
  | { type: "horizontal_line"; price: number; label: string; color?: string }
  | {
      type: "marker";
      time: number;
      position: "BUY" | "SELL";
      price: number;
      confidence: number;
    }
  | {
      type: "rectangle";
      top: number;
      bottom: number;
      label: string;
      color?: string;
      borderColor?: string;
    };
