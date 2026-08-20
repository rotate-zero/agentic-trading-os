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

// 4h/1d added on the UI side ahead of the data actually being available —
// no free-tier provider currently serves 1-minute (or any minute-level)
// historical backfill to resample these from at real depth (confirmed
// decision #39), so both will show sparse/empty charts until that's
// resolved or a real multi-timeframe backend source exists. Same "build
// the option now, let the data catch up" posture decision #40 took with
// the SMA/EMA instance system ahead of daily bars. resampleCandles
// (utils/resample.ts) and currentBarProgressPct (utils/timerProgress.ts)
// both already handle any Timeframe generically, so nothing about *how*
// a timeframe is consumed needed to change — only these two entries.
export type Timeframe = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";
export const TIMEFRAMES: Timeframe[] = ["1m", "5m", "15m", "1h", "4h", "1d"];

// ---- Overlay indicators: continuous line series drawn over price (SMA, EMA, VWAP) ----
//
// Instance-based rather than fixed presets — a chart can hold any number of
// SMAs/EMAs at once, each with its own period/color/thickness (the "9/20/50
// on a 5m chart, 50/100/200 on daily" requirement can't be expressed as a
// fixed enum). This is UI-only chart-reading convenience, computed
// client-side from whatever candles are already loaded — NOT a Feature
// Engine output. It's never published to the Event Bus and never consumed
// by Strategy/Decision Engine, so it doesn't conflict with
// system-design.md §1's "no generic indicator plugin marketplace" non-goal
// — that non-goal is about keeping algorithmic-decision inputs flowing
// through the one canonical backend Feature Engine, not about what a human
// is allowed to overlay on their own chart for reading price action. See
// confirmed-decisions.md for the full reasoning.
//
// The two fixed EMA9/EMA20 presets that used to live in a separate
// IndicatorType enum are retired — EMA is now just another
// OverlayIndicatorType here, on the same instance model as SMA, so there's
// one system instead of two conflicting ones. Existing sessions with
// EMA9/EMA20 selected are migrated to real instances (period 9 / 20, same
// colors as before) by normalizeSubWindow in WorkspaceContext.tsx, not
// silently dropped.
//
// Add the next kind (Bollinger Bands, a moving-average envelope, ...) by
// extending this union, adding one case to computePriceIndicator
// (utils/indicators.ts) that pulls from its own file under indicators/, and
// reusing everything else (this config shape, the submenu's list/add/remove
// UI, ChartWidget's series wiring, persistence/normalization) unchanged.
export type OverlayIndicatorType = "SMA" | "EMA" | "VWAP";
export const OVERLAY_TYPES_WITH_PERIOD: OverlayIndicatorType[] = ["SMA", "EMA"]; // VWAP is session-anchored, not bar-count-based

export interface PriceIndicatorInstance {
  id: string; // stable per-instance id — NOT derived from type/period, since
  // two instances of the same type can coexist (e.g. two SMAs) and an id
  // tied to period would have to change (and orphan its chart series) the
  // moment the user edits the period.
  type: OverlayIndicatorType;
  enabled: boolean;
  period?: number; // bars considered — SMA/EMA only; unused (undefined) for VWAP
  color: string; // hex
  lineWidth: number; // px — see PRICE_INDICATOR_LINE_WIDTH_STEP note below
  showPriceLabel: boolean; // last-value tag near the price axis, e.g. "SMA 9 → 22.50"
}

export const PRICE_INDICATOR_PERIOD_MIN = 2;
export const PRICE_INDICATOR_PERIOD_MAX = 500;
export const PRICE_INDICATOR_PERIOD_STEP = 1;
export const PRICE_INDICATOR_LINE_WIDTH_MIN = 1;
export const PRICE_INDICATOR_LINE_WIDTH_MAX = 4;
// 0.5, not 1 — Lightweight Charts' overlay line-series renderer passes
// lineWidth straight into the canvas 2D context's (float-valued) lineWidth
// property with no rounding (verified by reading
// node_modules/lightweight-charts/dist/lightweight-charts.production.mjs
// for the installed v4.2.3: the Line-series renderer does
// `context.lineWidth = configuredWidth * verticalPixelRatio` with no
// Math.round/floor anywhere in that path — only the `LineWidth = 1|2|3|4`
// TypeScript type restricts it, not the runtime). So 1.5 genuinely renders
// as a real intermediate thickness, fixing "1 is almost okay, 2 is too
// thick" by giving a step in between, rather than being a fake/rounded
// value. ChartWidget.tsx casts past that TS union deliberately — see the
// comment there. This is coupled to the exact installed version; a future
// lightweight-charts upgrade could change the renderer and silently start
// clamping again, so it's worth re-checking after any upgrade of that
// dependency. Horizontal levels (createPriceLine, below) do NOT get this
// treatment — that renderer explicitly floors to an integer physical pixel
// count, so a fractional step wouldn't reliably produce a visible
// difference there.
export const PRICE_INDICATOR_LINE_WIDTH_STEP = 0.5;
export const PRICE_INDICATOR_DEFAULT_PERIOD = 20;
export const PRICE_INDICATOR_DEFAULT_LINE_WIDTH = 2;

// Cycled through as new instances are added so three fresh SMAs don't all
// land on the same color before the user picks their own — same rationale
// as SubWindowMenu.tsx's CONNECTOR_COLORS.
const PRICE_INDICATOR_COLOR_CYCLE = ["#58A6FF", "#E3B341", "#7EE787", "#F778BA", "#BC8CFF", "#FFA657"];

export function createPriceIndicatorInstance(
  type: OverlayIndicatorType,
  existingCount: number,
  period: number = PRICE_INDICATOR_DEFAULT_PERIOD
): PriceIndicatorInstance {
  return {
    id: `${type.toLowerCase()}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    type,
    enabled: true,
    period: type === "VWAP" ? undefined : period,
    color: PRICE_INDICATOR_COLOR_CYCLE[existingCount % PRICE_INDICATOR_COLOR_CYCLE.length],
    lineWidth: PRICE_INDICATOR_DEFAULT_LINE_WIDTH,
    showPriceLabel: true,
  };
}

// Derived, not stored — a label tied to a stale period the user just edited
// would be a second source of truth for the same value.
export function priceIndicatorLabel(instance: PriceIndicatorInstance): string {
  return instance.type === "VWAP" ? "VWAP" : `${instance.type} ${instance.period}`;
}

// ---- Horizontal level indicators (Previous Day Close/High/Low, Pre-Market
// High/Low, Camarilla Pivots, VPOC) ----
//
// Single-price-level indicators, drawn as horizontal lines via
// series.createPriceLine — a different rendering shape from the overlay
// indicators above (which are {time,value}[] continuous series), so they
// get their own instance model rather than being forced into
// PriceIndicatorInstance's shape. Camarilla's nine named levels
// (PP/R1-4/S1-4) are modeled as nine separate instances rather than one
// nested list, so every horizontal level — Camarilla or not — is toggled,
// colored, and styled through the exact same row UI and the exact same
// HorizontalLevelInstance[] list; see SubWindowMenu.tsx's "Levels" submenu.
export type HorizontalLevelType =
  | "PDC"
  | "PDH"
  | "PDL"
  | "PMH"
  | "PML"
  | "CAM_PP"
  | "CAM_R1"
  | "CAM_R2"
  | "CAM_R3"
  | "CAM_R4"
  | "CAM_S1"
  | "CAM_S2"
  | "CAM_S3"
  | "CAM_S4"
  | "VPOC";

export interface HorizontalLevelInstance {
  id: string;
  type: HorizontalLevelType;
  enabled: boolean;
  color: string; // hex
  lineWidth: number; // px, integer HORIZONTAL_LEVEL_LINE_WIDTH_MIN..MAX — createPriceLine floors
  // fractional widths to an integer physical-pixel count (unlike the overlay
  // line series above), so there's no finer step here; see the
  // PRICE_INDICATOR_LINE_WIDTH_STEP comment for the source-level detail.
  lineStyle: LineStyleOption;
  showPriceLabel: boolean; // the price-axis tag (createPriceLine's axisLabelVisible)
}

export type LineStyleOption = "solid" | "dashed" | "dotted";
export const LINE_STYLE_OPTIONS: LineStyleOption[] = ["solid", "dashed", "dotted"];

export const HORIZONTAL_LEVEL_LINE_WIDTH_MIN = 1;
export const HORIZONTAL_LEVEL_LINE_WIDTH_MAX = 4;

export const HORIZONTAL_LEVEL_LABELS: Record<HorizontalLevelType, string> = {
  PDC: "Prev Day Close",
  PDH: "Prev Day High",
  PDL: "Prev Day Low",
  PMH: "Premarket High",
  PML: "Premarket Low",
  CAM_PP: "Camarilla PP",
  CAM_R1: "Camarilla R1",
  CAM_R2: "Camarilla R2",
  CAM_R3: "Camarilla R3",
  CAM_R4: "Camarilla R4",
  CAM_S1: "Camarilla S1",
  CAM_S2: "Camarilla S2",
  CAM_S3: "Camarilla S3",
  CAM_S4: "Camarilla S4",
  VPOC: "VPOC (Prev Day)",
};

const HORIZONTAL_LEVEL_DEFAULT_COLORS: Record<HorizontalLevelType, string> = {
  PDC: "#7D8590",
  PDH: "#3FB950",
  PDL: "#F85149",
  PMH: "#58A6FF",
  PML: "#F778BA",
  CAM_PP: "#E3B341",
  CAM_R1: "#F8B4AB",
  CAM_R2: "#F79A8E",
  CAM_R3: "#F87A6B",
  CAM_R4: "#F85149",
  CAM_S1: "#A8E6B0",
  CAM_S2: "#7EE787",
  CAM_S3: "#4FD860",
  CAM_S4: "#3FB950",
  VPOC: "#BC8CFF",
};

// Groups drive the submenu's "Add" buttons — clicking one adds every
// missing member of that group at once (e.g. "Add Camarilla" adds all nine
// levels in one click) rather than requiring nine individual adds. Members
// already present are skipped, so the button is safely clickable more than
// once.
export const HORIZONTAL_LEVEL_GROUPS: { label: string; types: HorizontalLevelType[] }[] = [
  { label: "Previous Day (Close / High / Low)", types: ["PDC", "PDH", "PDL"] },
  { label: "Pre-Market High / Low", types: ["PMH", "PML"] },
  {
    label: "Camarilla Pivots",
    types: ["CAM_PP", "CAM_R4", "CAM_R3", "CAM_R2", "CAM_R1", "CAM_S1", "CAM_S2", "CAM_S3", "CAM_S4"],
  },
  { label: "VPOC (Previous Day)", types: ["VPOC"] },
];

export function createHorizontalLevelInstance(type: HorizontalLevelType): HorizontalLevelInstance {
  return {
    id: `${type.toLowerCase()}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    type,
    enabled: true,
    color: HORIZONTAL_LEVEL_DEFAULT_COLORS[type],
    lineWidth: 1,
    lineStyle: "dashed",
    showPriceLabel: true,
  };
}


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

// ---- Volume bars (the histogram pane itself, bottom of the chart) ----
//
// Distinct from VolumeAvgIndicatorConfig above, which draws horizontal
// average-volume LINES on top of this pane — this config controls the bars
// underneath them: whether the pane shows at all, and whether each bar is
// colored by up/down (two colors) or a single flat color, each fully
// customizable via hex, same ColorField control used everywhere else in
// this menu (background, grid, timer, volume-avg lines, indicators,
// levels) — deliberately no forced alpha/opacity here for the same reason:
// every other color picker in the app hands back the exact hex chosen, so
// volume bars doing anything else (e.g. a hardcoded 50% alpha) would be the
// one inconsistent picker in the file. Previously this was hardcoded in
// ChartWidget.tsx as a fixed 50%-alpha green/red with no way to turn it
// off; disabling now collapses the volume pane's price-scale margins to
// zero height rather than just hiding the bars, so turning it off actually
// reclaims the vertical space for the candles instead of leaving an empty
// strip.
export type VolumeBarColorMode = "two_color" | "one_color";
export const VOLUME_BAR_COLOR_MODES: VolumeBarColorMode[] = ["two_color", "one_color"];

export interface VolumeBarsConfig {
  enabled: boolean; // master on/off — false removes the volume pane entirely
  colorMode: VolumeBarColorMode;
  upColor: string; // hex — two_color mode, bars where close >= open
  downColor: string; // hex — two_color mode, bars where close < open
  singleColor: string; // hex — one_color mode, every bar
}

export const DEFAULT_VOLUME_BAR_UP_COLOR = "#3FB950"; // matches candle BULL color
export const DEFAULT_VOLUME_BAR_DOWN_COLOR = "#F85149"; // matches candle BEAR color
export const DEFAULT_VOLUME_BAR_SINGLE_COLOR = "#58A6FF";

export function createDefaultVolumeBarsConfig(): VolumeBarsConfig {
  return {
    enabled: true,
    colorMode: "two_color",
    upColor: DEFAULT_VOLUME_BAR_UP_COLOR,
    downColor: DEFAULT_VOLUME_BAR_DOWN_COLOR,
    singleColor: DEFAULT_VOLUME_BAR_SINGLE_COLOR,
  };
}

// ---- Daily Levels (confirmed decisions #59-#61) ----
//
// Unlike HorizontalLevelInstance above, this is NOT a list the user builds
// one instance at a time — Feature Engine publishes a variable-COUNT set
// of clustered support/resistance zones per symbol (0 to 15+, reshaping
// day to day), all the same "type," so there's nothing per-instance to
// configure. One config object controls how ALL of them render, same
// single-object-with-enabled-flag shape as VolumeAvgIndicatorConfig
// above, not the multi-instance HorizontalLevelInstance[] shape.
//
// No local/"(local)" fallback exists for this one (unlike every
// HorizontalLevelType, which has a real frontend/src/indicators/*.ts to
// fall back to) — daily-levels-design.md §6 flagged this gap directly:
// there was never an existing client-side implementation to port from.
// When nothing's connected on the backend, this simply renders nothing,
// same as the rest of this app's "empty means not-yet, not zero"
// convention — never a silently-wrong local computation standing in.
export interface DailyLevelsConfig {
  enabled: boolean; // opt-in, same convention as Volume Avg and the Indicators list
  minStrength: number; // hide clusters below this point-count — Saqib's own stated
  // plan for "too many levels": filter/toggle in the UI, not an algorithmic cap
  // server-side (daily-levels-design.md §6) — this IS that control.
  // Price-range filter (confirmed decision #62) — null means "no bound
  // on this side." A SECOND, more direct way to cut down on-screen
  // clutter than minStrength alone: Saqib's own reported symptom was
  // levels spanning the symbol's whole 180-day price history visually
  // burying other indicators, so restricting to a band around the
  // current price (e.g. 90-110) is a more precise tool than filtering
  // by strength when the actual problem is HOW FAR AWAY a level is, not
  // how weak it is.
  minPrice: number | null;
  maxPrice: number | null;
  // How many of the most recent 1D candles to cluster from (confirmed
  // decision #62) — re-clustered on demand server-side from ALREADY
  // fetched/cached candles (engine.py's get_daily_levels()), so changing
  // this is cheap: no new provider call, just a fast in-memory re-run of
  // the same clustering function on a shorter slice. null means "use the
  // server's configured default" (daily_levels_lookback_days, 180 unless
  // changed) rather than duplicating that number here — one source of
  // truth for what "default" means.
  lookbackDays: number | null;
  color: string; // hex
  lineWidth: number; // px, integer DAILY_LEVELS_LINE_WIDTH_MIN..MAX floor, same
  // createPriceLine physical-pixel-rounding note as HorizontalLevelInstance.lineWidth
  showPriceLabels: boolean; // the price-axis tag, same as HorizontalLevelInstance.showPriceLabel
}

export const DAILY_LEVELS_MIN_STRENGTH_FLOOR = 2; // matches the backend's own
// daily_levels_min_distinct_candles validity gate — a level can't have
// fewer than 2 contributing points in the first place (indicators/daily_levels.py),
// so a UI filter below this number would just be a no-op, not a real lower bound
export const DAILY_LEVELS_MIN_STRENGTH_CEILING = 20;
export const DAILY_LEVELS_LINE_WIDTH_MIN = 1;
export const DAILY_LEVELS_LINE_WIDTH_MAX = 4;
export const DEFAULT_DAILY_LEVELS_COLOR = "#D29922"; // amber — distinct from every
// existing HORIZONTAL_LEVEL_DEFAULT_COLORS entry and from VWAP/SMA/EMA's own
// default palette, so Daily Levels reads as its own thing on a busy chart

// Confirmed decision #62 — a small fixed set of common choices rather
// than a free-form day-count input; matches Saqib's own examples ("past
// 30 days, 60 days"). The backend accepts ANY positive integer (it's
// just a slice length), so this list is a UI convenience, not a
// server-side constraint — SubWindowMenu.tsx could grow a custom-value
// input later without any backend change.
export const DAILY_LEVELS_LOOKBACK_PRESETS: { label: string; days: number | null }[] = [
  { label: "30d", days: 30 },
  { label: "60d", days: 60 },
  { label: "90d", days: 90 },
  { label: "Default", days: null },
];

export function createDefaultDailyLevelsConfig(): DailyLevelsConfig {
  return {
    enabled: false, // opt-in, same convention as Volume Avg and the Indicators list starting empty
    minStrength: DAILY_LEVELS_MIN_STRENGTH_FLOOR,
    minPrice: null,
    maxPrice: null,
    lookbackDays: null, // server default
    color: DEFAULT_DAILY_LEVELS_COLOR,
    lineWidth: 1,
    showPriceLabels: false, // off by default — a dense cluster of levels with every
    // axis label showing at once is exactly the "too many things on screen" case
    // Saqib's own filter/toggle plan exists for; the short "DL-N" line title
    // (ChartWidget.tsx) is the primary way strength reads visually, labels are
    // an opt-in on top of that
  };
}

export interface SubWindowConfig {
  id: string;
  connector: ConnectorId;
  symbol: string; // only authoritative when connector === 'none'
  timeframe: Timeframe;
  priceIndicators: PriceIndicatorInstance[]; // opt-in, starts empty — SMA/EMA/VWAP
  horizontalLevels: HorizontalLevelInstance[]; // opt-in, starts empty — PDH/PDL/Camarilla/VPOC/etc.
  candleLimit: CandleLimit;
  backgroundColor: string; // hex, e.g. "#131720"
  gridColor: string; // hex, e.g. "#1E2530"
  timer: TimerConfig;
  volumeAvg: VolumeAvgIndicatorConfig;
  volumeBars: VolumeBarsConfig;
  dailyLevelsConfig: DailyLevelsConfig; // confirmed decision #61 — see that type's own comment
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
  // Feature Engine panel (confirmed decision #48) — deliberately separate
  // from infoCollapsed/infoWidthPx, not reusing them: this is a second,
  // independent vertically-collapsible panel, not a mode of the Info tab.
  // Its OWN symbol, not tied to any connector — the panel analyzes
  // whatever symbol is typed into it, same "search field selects a
  // symbol for analysis" design agreed in discussion.
  featureEngineCollapsed: boolean;
  featureEngineWidthPx: number;
  featureEnginePanelSymbol: string;
}
