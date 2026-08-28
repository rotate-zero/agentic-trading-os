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

export const COLOR_OPACITY_MIN = 0;
export const COLOR_OPACITY_MAX = 100;
export const COLOR_OPACITY_DEFAULT = 100; // fully opaque — every existing color's prior, unconfigurable behavior

export interface PriceIndicatorInstance {
  id: string; // stable per-instance id — NOT derived from type/period, since
  // two instances of the same type can coexist (e.g. two SMAs) and an id
  // tied to period would have to change (and orphan its chart series) the
  // moment the user edits the period.
  type: OverlayIndicatorType;
  enabled: boolean;
  period?: number; // bars considered — SMA/EMA only; unused (undefined) for VWAP
  color: string; // hex
  opacity: number; // 0-100, applied via hexWithOpacity (utils/hud.ts) at render time — see COLOR_OPACITY_DEFAULT
  lineWidth: number; // px — see PRICE_INDICATOR_LINE_WIDTH_STEP note below
  showPriceLabel: boolean; // last-value tag near the price axis, e.g. "SMA 9 → 22.50"
  // The name portion of that same axis tag ("SMA 9" on its own, independent
  // of whatever the price-value half shows) — decision #81. Lightweight
  // Charts' `lastValueVisible` (showPriceLabel above) only ever hides the
  // VALUE half of a series' price-scale label; its `title` (the name half)
  // renders unconditionally whenever it's a non-empty string, with no
  // built-in flag of its own — confirmed against the library's own issue
  // tracker, not assumed. That gap is exactly what let the name badge stay
  // stuck on screen even with "Show price tag" unchecked. This field is
  // the independent on/off ChartWidget.tsx needs to actually clear `title`
  // to "" rather than leave the library's unconditional behavior showing.
  showNameLabel: boolean;
  // SMA(period)/EMA(period) slope, expressed as an angle in degrees,
  // appended to the name-half tag when both this AND showNameLabel are
  // on — e.g. "SMA 9 ∠+35.2°" (confirmed decision #83). Feature-Engine-
  // only: no client-side fallback exists or is computed for this (unlike
  // the SMA/EMA VALUE itself, which falls back to frontend/src/indicators/
  // when Feature Engine can't serve a given period/timeframe) — an
  // absent/still-warming-up backend series simply means no angle suffix
  // shows, not a "(local)" recomputation. VWAP has no period/slope
  // concept, so this is meaningless there — see OVERLAY_TYPES_WITH_PERIOD
  // above, the same SMA/EMA-only boundary this field is gated on in the
  // menu and in computePriceIndicator (utils/indicators.ts). Defaults
  // false for every EXISTING saved instance on load (WorkspaceContext.tsx's
  // back-fill) — unlike showPriceLabel/showNameLabel's true default,
  // this is genuinely new behavior, not a preserved-unchanged prior
  // always-on state, so off-by-default is the correct backward-compat
  // choice here.
  showSlopeAngle: boolean;
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
    opacity: COLOR_OPACITY_DEFAULT,
    lineWidth: PRICE_INDICATOR_DEFAULT_LINE_WIDTH,
    showPriceLabel: true,
    showNameLabel: true,
    showSlopeAngle: false,
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
  opacity: number; // 0-100, see COLOR_OPACITY_DEFAULT
  lineWidth: number; // px, integer HORIZONTAL_LEVEL_LINE_WIDTH_MIN..MAX — createPriceLine floors
  // fractional widths to an integer physical-pixel count (unlike the overlay
  // line series above), so there's no finer step here; see the
  // PRICE_INDICATOR_LINE_WIDTH_STEP comment for the source-level detail.
  lineStyle: LineStyleOption;
  showPriceLabel: boolean; // the price-axis tag (createPriceLine's axisLabelVisible)
  // Whether the name prefix ("PDH") renders ahead of the price value inside
  // that same tag — decision #81, the createPriceLine counterpart to
  // PriceIndicatorInstance.showNameLabel above. Unlike a line series'
  // separate title/lastValueVisible flags, a price line's `title` and
  // `axisLabelVisible` are already one combined visual unit — there's no
  // library-level bug here the way there is for overlay indicators — but
  // there was still no way to drop just the name prefix and keep the bare
  // price ("494.86" instead of "PDH 494.86"). This only changes what
  // `title` is passed to createPriceLine when the tag is showing;
  // showPriceLabel/axisLabelVisible still owns whether the tag shows at
  // all, so unchecking Show price tag continues to hide the whole thing,
  // same as before this field existed.
  showNameLabel: boolean;
}

export type LineStyleOption = "solid" | "dashed" | "dotted";
export const LINE_STYLE_OPTIONS: LineStyleOption[] = ["solid", "dashed", "dotted"];

// Chart style — which lightweight-charts series type draws the price pane.
// A plain string union, not a config object, deliberately: unlike
// VolumeBarsConfig/DailyLevelsConfig (which bundle several related
// settings), this is one scalar choice, same shape as `backgroundColor`/
// `gridColor` below rather than the nested-object pattern. Confirmed
// decision #73 — extensible on purpose: both `addBarSeries` (OHLC bars,
// matching lightweight-charts' native default styling of thin sticks with
// visible open/close ticks — no custom renderer needed) and
// `addCandlestickSeries` are natively supported by the already-installed
// lightweight-charts version, so this is a pure rendering-layer choice; a
// future third style is one more union member, not a new mechanism.
export type ChartStyle = "candlestick" | "bar";
export const CHART_STYLES: ChartStyle[] = ["candlestick", "bar"];
export const DEFAULT_CHART_STYLE: ChartStyle = "candlestick";

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
    opacity: COLOR_OPACITY_DEFAULT,
    lineWidth: 1,
    lineStyle: "dashed",
    showPriceLabel: true,
    showNameLabel: true,
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
  opacity: number; // 0-100, see COLOR_OPACITY_DEFAULT
}
export const DEFAULT_TIMER_COLOR = "#3FB950"; // bull green, round sweep shape

export function createDefaultTimerConfig(): TimerConfig {
  return { enabled: true, color: DEFAULT_TIMER_COLOR, opacity: COLOR_OPACITY_DEFAULT };
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
  opacity: number; // 0-100, see COLOR_OPACITY_DEFAULT
  barCount: number; // ignored when adjustable is false
  adjustable: boolean;
  // The price-axis tag (createPriceLine's axisLabelVisible) — same field
  // name/meaning as PriceIndicatorInstance.showPriceLabel and
  // HorizontalLevelInstance.showPriceLabel. Previously hardcoded to
  // `true` with no way to turn it off (ChartWidget.tsx's volumeAvg
  // effect); this closes that gap so every label-bearing indicator on
  // the chart follows the same on/off convention, not just three of the
  // four. See confirmed-decisions.md #74.
  showPriceLabel: boolean;
  // Name half of that same tag (e.g. "Day Avg") — decision #82, same
  // createPriceLine name-vs-price split as HorizontalLevelInstance's own
  // showNameLabel (decision #81). Drops just the label prefix, keeping
  // the bare number, when off; showPriceLabel above still owns whether
  // the tag shows at all.
  showNameLabel: boolean;
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
      { id: "day", label: "Day Avg", enabled: true, color: "#D2A8FF", opacity: COLOR_OPACITY_DEFAULT, barCount: 0, adjustable: false, showPriceLabel: true, showNameLabel: true },
      { id: "n1", label: "3-Bar Avg", enabled: true, color: "#58A6FF", opacity: COLOR_OPACITY_DEFAULT, barCount: 3, adjustable: true, showPriceLabel: true, showNameLabel: true },
      { id: "n2", label: "6-Bar Avg", enabled: true, color: "#FFA657", opacity: COLOR_OPACITY_DEFAULT, barCount: 6, adjustable: true, showPriceLabel: true, showNameLabel: true },
      { id: "n3", label: "9-Bar Avg", enabled: true, color: "#7EE787", opacity: COLOR_OPACITY_DEFAULT, barCount: 9, adjustable: true, showPriceLabel: true, showNameLabel: true },
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
  upOpacity: number; // 0-100, see COLOR_OPACITY_DEFAULT
  downColor: string; // hex — two_color mode, bars where close < open
  downOpacity: number; // 0-100, see COLOR_OPACITY_DEFAULT
  singleColor: string; // hex — one_color mode, every bar
  singleOpacity: number; // 0-100, see COLOR_OPACITY_DEFAULT
}

export const DEFAULT_VOLUME_BAR_UP_COLOR = "#3FB950"; // matches candle BULL color
export const DEFAULT_VOLUME_BAR_DOWN_COLOR = "#F85149"; // matches candle BEAR color
export const DEFAULT_VOLUME_BAR_SINGLE_COLOR = "#58A6FF";

export function createDefaultVolumeBarsConfig(): VolumeBarsConfig {
  return {
    enabled: true,
    colorMode: "two_color",
    upColor: DEFAULT_VOLUME_BAR_UP_COLOR,
    upOpacity: COLOR_OPACITY_DEFAULT,
    downColor: DEFAULT_VOLUME_BAR_DOWN_COLOR,
    downOpacity: COLOR_OPACITY_DEFAULT,
    singleColor: DEFAULT_VOLUME_BAR_SINGLE_COLOR,
    singleOpacity: COLOR_OPACITY_DEFAULT,
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
  opacity: number; // 0-100, see COLOR_OPACITY_DEFAULT — one opacity for the one shared
  // color, consistent with the "single uniform color/width for all levels,
  // not a per-level gradient" decision already made for this indicator
  lineWidth: number; // px, integer DAILY_LEVELS_LINE_WIDTH_MIN..MAX floor, same
  // createPriceLine physical-pixel-rounding note as HorizontalLevelInstance.lineWidth
  showPriceLabels: boolean; // the price-axis tag, same as HorizontalLevelInstance.showPriceLabel
  // Name half of that same tag (the "DL-N" strength prefix) — decision
  // #82, same createPriceLine name-vs-price split as HorizontalLevelInstance
  // and VolumeAvgLineConfig's own showNameLabel. Off drops just the "DL-N"
  // prefix and keeps the bare price; showPriceLabels above still owns
  // whether the tag shows at all. Defaults true (unlike showPriceLabels,
  // which defaults false) because "DL-N" — not the price — is this
  // indicator's own documented primary way strength reads visually (see
  // showPriceLabels' own comment below); turning names off by default
  // would have silently removed that on every existing session.
  showNameLabels: boolean;
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
    opacity: COLOR_OPACITY_DEFAULT,
    lineWidth: 1,
    showPriceLabels: false, // off by default — a dense cluster of levels with every
    // axis label showing at once is exactly the "too many things on screen" case
    // Saqib's own filter/toggle plan exists for; the short "DL-N" line title
    // (ChartWidget.tsx) is the primary way strength reads visually, labels are
    // an opt-in on top of that
    showNameLabels: true, // matches the always-on "DL-N" behavior every
    // existing session was already rendering — see the field's own comment above
  };
}

// ---- HUD text box (small on-chart readout, top of the price pane) ----
//
// A floating, multi-line text overlay pinned to the top of the chart pane —
// same "fixed frame chrome over the candles" mechanism as TimerBadge.tsx,
// but showing live Feature Engine values instead of bar-progress. Each of
// its (always exactly 3, same "fixed slot count, per-slot enabled" shape as
// VolumeAvgIndicatorConfig.lines above) lines is user-composed from an
// ordered mix of literal text and live variables — not a fixed template —
// so a line reads as e.g. "GAP +1.20%  DAY +0.85%" by combining two
// variable segments with a plain-text separator between them, and the
// person can freely add more text or swap in different variables.
//
// Variables map 1:1 onto specific Feature Engine keys (engine.py's
// indicators/gap.py, session_change.py, atr.py, rvol.py, and
// _update_vwap's session_volume) via useHudFeatures.ts — see that hook's
// own docstring for the exact key-by-key mapping and why ATR is the
// period-14 value (atr_14/atr_14_pct) rather than a shorter period: no
// other period is computed anywhere in the system (feature_engine_atr_period
// is a single global setting, currently 14), a real gap surfaced and
// resolved with Saqib in chat rather than silently substituted. See
// confirmed-decisions.md #75.
export type HudVariableKey =
  | "gap_pct"
  | "gap_dollars"
  | "session_pct_change"
  | "session_dollar_change"
  | "atr"
  | "atr_pct"
  | "rvol"
  | "session_volume";

// Label/formatting metadata lives in utils/hud.ts (HUD_VARIABLES), not
// here — this file stays pure shape/config, same split the rest of
// workspace.ts keeps between "what's configurable" (here) and "how it's
// computed/rendered" (utils/, ChartWidget.tsx).
export const HUD_VARIABLE_KEYS: HudVariableKey[] = [
  "gap_pct",
  "gap_dollars",
  "session_pct_change",
  "session_dollar_change",
  "atr",
  "atr_pct",
  "rvol",
  "session_volume",
];

export interface HudTextSegment {
  id: string;
  kind: "text";
  value: string;
}

export interface HudVariableSegment {
  id: string;
  kind: "variable";
  variable: HudVariableKey;
}

export type HudSegment = HudTextSegment | HudVariableSegment;

export interface HudLineConfig {
  id: "line1" | "line2" | "line3";
  enabled: boolean;
  segments: HudSegment[];
}

export type HudAlign = "left" | "right";

export interface HudConfig {
  enabled: boolean; // master on/off for the whole box
  lines: HudLineConfig[]; // always exactly 3, in fixed order (line1, line2, line3)
  backgroundColor: string; // hex
  backgroundOpacity: number; // 0-100, applied on top of backgroundColor (see hexWithOpacity in utils/hud.ts)
  textColor: string; // hex
  textOpacity: number; // 0-100, see COLOR_OPACITY_DEFAULT — independent of backgroundOpacity,
  // e.g. a fully-opaque background with slightly faded text, or vice versa
  align: HudAlign; // which corner of the chart pane the box sits in
}

export const DEFAULT_HUD_BACKGROUND = "#131720"; // matches DEFAULT_CHART_BG — reads as part of the
// chart's own chrome by default, same "blend in until deliberately changed" choice DEFAULT_GRID_COLOR made
export const DEFAULT_HUD_TEXT_COLOR = "#E6EDF3"; // matches theme.colors.text.primary, tailwind.config.js

let hudSegmentCounter = 0;
function hudSegmentId(): string {
  hudSegmentCounter += 1;
  return `hud-seg-${Date.now()}-${hudSegmentCounter}`;
}

function textSeg(value: string): HudTextSegment {
  return { id: hudSegmentId(), kind: "text", value };
}
function varSeg(variable: HudVariableKey): HudVariableSegment {
  return { id: hudSegmentId(), kind: "variable", variable };
}

export function createDefaultHudConfig(): HudConfig {
  return {
    enabled: false, // opt-in, same convention as Volume Avg/Daily Levels starting disabled
    lines: [
      { id: "line1", enabled: true, segments: [varSeg("gap_pct"), textSeg("  "), varSeg("session_pct_change")] },
      { id: "line2", enabled: true, segments: [varSeg("atr"), textSeg("  "), varSeg("session_dollar_change")] },
      { id: "line3", enabled: true, segments: [varSeg("rvol"), textSeg("  "), varSeg("session_volume")] },
    ],
    backgroundColor: DEFAULT_HUD_BACKGROUND,
    backgroundOpacity: 70,
    textColor: DEFAULT_HUD_TEXT_COLOR,
    textOpacity: COLOR_OPACITY_DEFAULT,
    align: "left", // TimerBadge already owns the top-right corner; left avoids the two overlapping by default
  };
}

export interface SubWindowConfig {
  id: string;
  connector: ConnectorId;
  symbol: string; // only authoritative when connector === 'none'
  timeframe: Timeframe;
  // Decision #72 — deliberately NOT a Timeframe value ("tick" isn't a
  // backend-fetchable resolution; GET /market/candles has no such
  // timeframe). Independent boolean layered on top of `timeframe` instead:
  // true only ever makes sense while timeframe === "1m" (see SubWindowMenu's
  // toggle, which enforces that pairing), and means "also apply throttled
  // PriceSnapshot updates to the currently-forming bar," not "fetch a
  // different resolution."
  liveTick: boolean;
  priceIndicators: PriceIndicatorInstance[]; // opt-in, starts empty — SMA/EMA/VWAP
  horizontalLevels: HorizontalLevelInstance[]; // opt-in, starts empty — PDH/PDL/Camarilla/VPOC/etc.
  candleLimit: CandleLimit;
  chartStyle: ChartStyle; // candlestick (default) or bar — confirmed decision #73
  backgroundColor: string; // hex, e.g. "#131720"
  backgroundOpacity: number; // 0-100, see COLOR_OPACITY_DEFAULT
  gridColor: string; // hex, e.g. "#1E2530"
  gridOpacity: number; // 0-100, see COLOR_OPACITY_DEFAULT
  timer: TimerConfig;
  volumeAvg: VolumeAvgIndicatorConfig;
  volumeBars: VolumeBarsConfig;
  dailyLevelsConfig: DailyLevelsConfig; // confirmed decision #61 — see that type's own comment
  hud: HudConfig; // on-chart Feature Engine readout box, confirmed decision #75 — see that type's own comment
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
  // Scanner panel — same independent-collapsible-sidebar pattern as
  // Feature Engine above, not reusing its fields (a third, separate
  // panel). No per-panel "symbol" field the way Feature Engine has one:
  // Scanner shows a whole ranked universe, not one symbol's detail.
  scannerCollapsed: boolean;
  scannerWidthPx: number;
}
