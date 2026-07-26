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

// Grid line color lives alongside background color in the same "Background"
// submenu, same format (native swatch + hex field). Default matches the
// theme's previous hardcoded grid line color, so existing layouts render
// identically until someone opts to change it.
export const DEFAULT_GRID_COLOR = "#1E2530";

// Round "radar" progress badge, top-right corner of each sub-window's chart —
// sweeps clockwise 0-100% over the current timeframe bar. No live Market
// Clock exists until Phase 2 (`core/market_clock.py`), so progress is
// approximated from wall-clock time modulo the timeframe's duration (see
// `utils/timerProgress.ts`) rather than a real bar-open timestamp.
export interface TimerConfig {
  enabled: boolean;
  color: string; // hex — the round sweep itself; the surrounding badge frame
  // (black border, fixed grey background) is intentionally NOT customizable,
  // since its whole job is to stay legible no matter what backgroundColor is.
}
export const DEFAULT_TIMER_COLOR = "#3FB950"; // bull green, round sweep shape

export function createDefaultTimerConfig(): TimerConfig {
  return { enabled: true, color: DEFAULT_TIMER_COLOR };
}

// Up to 4 horizontal average-volume lines drawn on the volume pane. Line 1
// ("day") is fixed to represent the whole session at the selected timeframe —
// mock data has no day/session boundary yet (that's Phase 2/3's Market Clock),
// so it's approximated as the average across every bar currently loaded, see
// `utils/volumeAverages.ts`. Lines 2-4 are trailing N-bar averages (default
// 3/6/9) with an adjustable bar count.
export type VolumeAvgLineId = "day" | "n1" | "n2" | "n3";

export interface VolumeAvgLineConfig {
  id: VolumeAvgLineId;
  label: string;
  enabled: boolean;
  color: string; // hex
  barCount: number; // ignored when adjustable is false
  adjustable: boolean;
}

export interface VolumeAvgIndicatorConfig {
  enabled: boolean; // master on/off for the whole indicator
  lines: VolumeAvgLineConfig[]; // always exactly 4, in fixed order (day, n1, n2, n3)
}

export const VOLUME_AVG_BAR_MIN = 2;
export const VOLUME_AVG_BAR_MAX = 50;
export const VOLUME_AVG_BAR_STEP = 1;

export function createDefaultVolumeAvgConfig(): VolumeAvgIndicatorConfig {
  return {
    enabled: false, // opt-in, same convention as the Indicators list starting empty
    lines: [
      { id: "day", label: "Day Avg", enabled: true, color: "#D2A8FF", barCount: 0, adjustable: false },
      { id: "n1", label: "3-Bar Avg", enabled: true, color: "#58A6FF", barCount: 3, adjustable: true },
      { id: "n2", label: "6-Bar Avg", enabled: true, color: "#FFA657", barCount: 6, adjustable: true },
      { id: "n3", label: "9-Bar Avg", enabled: true, color: "#7EE787", barCount: 9, adjustable: true },
    ],
  };
}

export interface SubWindowConfig {
  id: string;
  connector: ConnectorId;
  symbol: string; // only authoritative when connector === 'none'
  timeframe: Timeframe;
  indicators: IndicatorType[];
  candleLimit: CandleLimit;
  backgroundColor: string; // hex, e.g. "#131720"
  gridColor: string; // hex, e.g. "#1E2530"
  timer: TimerConfig;
  volumeAvg: VolumeAvgIndicatorConfig;
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
