// Connector 'none' = unlinked (this sub-window owns its own symbol).
// Connector 0-9 = a symbol-sync group: sub-windows sharing a connector always
// show the same symbol, but keep their own timeframe/indicators independently.
// Connector groups are GLOBAL — shared across every Main Window, not scoped to
// one. A connector-0 window in Layout 1 and a connector-0 window in Layout 2
// show the same symbol. This lives above MainWindowState for exactly that reason.
export type ConnectorId = "none" | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9;
export const LINK_CONNECTOR_IDS: Exclude<ConnectorId, "none">[] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];

// Info tab has no "none" — "General" already means "not tied to one connector".
export type InfoConnectorMode = "general" | Exclude<ConnectorId, "none">;

export type Timeframe = "1m" | "5m" | "15m" | "1h";
export const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h"];

export type IndicatorType = "EMA9" | "EMA20" | "SMA20" | "SMA50";
export const AVAILABLE_INDICATORS: IndicatorType[] = ["EMA9", "EMA20", "SMA20", "SMA50"];

// "Maintain a fixed number of candles, new ones replace old ones" — implemented
// as a visible-range constraint (last N bars), not data deletion. Behaves like a
// zoom that stays pinned to the latest data as it updates, per the spec's own
// "act like a zoom function" framing. A free variable adjusted via a -/+
// stepper, not a fixed preset list.
export type CandleLimit = "all" | number;
export const CANDLE_LIMIT_MIN = 5;
export const CANDLE_LIMIT_MAX = 500;
export const CANDLE_LIMIT_STEP = 5;
export const CANDLE_LIMIT_DEFAULT = 20; // where the stepper lands leaving "All"

// Grid is a free rows x cols choice (1-8 each), picked via an Excel-style hover
// grid — not a fixed preset list. Confirmed notation: RxC = rows x cols.
export interface GridLayout {
  rows: number;
  cols: number;
}
export const MAX_GRID_DIM = 8;

export const DEFAULT_CHART_BG = "#131720";

export interface SubWindowConfig {
  id: string;
  connector: ConnectorId;
  symbol: string; // only authoritative when connector === 'none'
  timeframe: Timeframe;
  indicators: IndicatorType[];
  candleLimit: CandleLimit;
  backgroundColor: string; // hex, e.g. "#131720"
}

export const DEFAULT_SYMBOL = "NVDA";

// A named, saved snapshot of one Main Window: its grid, every sub-window's
// full config, AND a snapshot of what each connector it used was showing at
// save time. That last part matters because connectors are global — without
// it, loading an old layout could silently pick up whatever a connector
// happens to be showing *now* instead of what this layout actually saved.
export interface SavedLayout {
  id: string;
  name: string;
  savedAt: string; // ISO timestamp
  gridLayout: GridLayout;
  rowHeights: number[];
  colWidths: number[];
  subWindows: SubWindowConfig[];
  connectorSymbolsSnapshot: Partial<Record<Exclude<ConnectorId, "none">, string>>;
}

// One Main Window = its own grid, its own sub-windows, its own info tab.
// Connector *symbols* are NOT here — they're global (see ConnectorId above).
// That's the whole point of "sub-windows on different Main Windows still
// connected by the same connector."
export interface MainWindowState {
  id: string;
  label: string;
  gridLayout: GridLayout;
  rowHeights: number[]; // fractions summing to 1, shared across the whole grid
  colWidths: number[]; // fractions summing to 1, shared across the whole grid
  subWindows: SubWindowConfig[]; // length === gridLayout.rows * gridLayout.cols
  infoCollapsed: boolean;
  infoWidthPx: number;
}
