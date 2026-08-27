import { useEffect, useRef, useState } from "react";
import { useDropdownPlacement } from "../../hooks/useDropdownPlacement";
import {
  CANDLE_LIMIT_DEFAULT,
  CANDLE_LIMIT_MAX,
  CANDLE_LIMIT_MIN,
  CANDLE_LIMIT_STEP,
  CHART_STYLES,
  DEFAULT_CHART_BG,
  DEFAULT_CHART_STYLE,
  DEFAULT_GRID_COLOR,
  DEFAULT_TIMER_COLOR,
  HORIZONTAL_LEVEL_GROUPS,
  HORIZONTAL_LEVEL_LABELS,
  HORIZONTAL_LEVEL_LINE_WIDTH_MAX,
  HORIZONTAL_LEVEL_LINE_WIDTH_MIN,
  LINE_STYLE_OPTIONS,
  LINK_CONNECTOR_IDS,
  OVERLAY_TYPES_WITH_PERIOD,
  PRICE_INDICATOR_LINE_WIDTH_MAX,
  PRICE_INDICATOR_LINE_WIDTH_MIN,
  PRICE_INDICATOR_LINE_WIDTH_STEP,
  PRICE_INDICATOR_PERIOD_MAX,
  PRICE_INDICATOR_PERIOD_MIN,
  PRICE_INDICATOR_PERIOD_STEP,
  TIMEFRAMES,
  VOLUME_AVG_BAR_MAX,
  VOLUME_AVG_BAR_MIN,
  VOLUME_AVG_BAR_STEP,
  VOLUME_BAR_COLOR_MODES,
  DAILY_LEVELS_MIN_STRENGTH_FLOOR,
  DAILY_LEVELS_MIN_STRENGTH_CEILING,
  DAILY_LEVELS_LOOKBACK_PRESETS,
  DAILY_LEVELS_LINE_WIDTH_MIN,
  DAILY_LEVELS_LINE_WIDTH_MAX,
  COLOR_OPACITY_MIN,
  COLOR_OPACITY_MAX,
  HUD_VARIABLE_KEYS,
  createDefaultDailyLevelsConfig,
  createDefaultHudConfig,
  createDefaultVolumeBarsConfig,
  createHorizontalLevelInstance,
  createPriceIndicatorInstance,
  priceIndicatorLabel,
  type CandleLimit,
  type ChartStyle,
  type HorizontalLevelInstance,
  type HorizontalLevelType,
  type HudLineConfig,
  type HudSegment,
  type HudVariableKey,
  type LineStyleOption,
  type OverlayIndicatorType,
  type PriceIndicatorInstance,
  type SubWindowConfig,
  type VolumeAvgIndicatorConfig,
  type VolumeAvgLineConfig,
  type VolumeBarColorMode,
} from "../../types/workspace";
import { HUD_VARIABLES } from "../../utils/hud";
import { MOCK_TICKERS } from "../../mocks/tickers";
import { useWorkspace } from "../../state/WorkspaceContext";

const CONNECTOR_COLORS: Record<number, string> = {
  0: "#F85149",
  1: "#E3B341",
  2: "#3FB950",
  3: "#58A6FF",
  4: "#BC8CFF",
  5: "#F778BA",
  6: "#79C0FF",
  7: "#FFA657",
  8: "#7EE787",
  9: "#D2A8FF",
};

function candleLimitLabel(v: CandleLimit): string {
  return v === "all" ? "All" : String(v);
}

function isValidHex(v: string): boolean {
  return /^#[0-9A-Fa-f]{6}$/.test(v);
}

// Always-visible, leftmost — separate from the "further options" menu now.
// Typing + Enter (or picking a suggestion) jumps straight to that symbol.
function TickerSearch({ config, displaySymbol }: { config: SubWindowConfig; displaySymbol: string }) {
  const { setSubWindowSymbol } = useWorkspace();
  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);
  const placement = useDropdownPlacement(focused, anchorRef);

  const suggestions = query
    ? MOCK_TICKERS.filter(
        (t) =>
          t.symbol.toLowerCase().includes(query.toLowerCase()) || t.name.toLowerCase().includes(query.toLowerCase())
      )
    : MOCK_TICKERS;

  const pick = (symbol: string) => {
    setSubWindowSymbol(config.id, symbol);
    setQuery("");
    setFocused(false);
  };

  return (
    <div className="relative shrink-0" ref={anchorRef}>
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 150)} // let a suggestion's onMouseDown fire first
        onKeyDown={(e) => {
          if (e.key === "Enter" && suggestions[0]) pick(suggestions[0].symbol);
        }}
        placeholder={displaySymbol}
        className="w-16 rounded border border-base-border bg-base-bg px-1.5 py-0.5 font-mono text-[11px] text-text-primary outline-none transition-all focus:w-32 focus:border-signal"
      />
      {focused && (
        <div
          className={`absolute left-0 z-30 w-40 overflow-y-auto rounded border border-base-border bg-base-panel shadow-xl ${
            placement.vertical === "down" ? "top-full mt-1" : "bottom-full mb-1"
          }`}
          style={{ maxHeight: Math.min(placement.maxHeight, 160) }}
        >
          {suggestions.map((t) => (
            <button
              key={t.symbol}
              onMouseDown={() => pick(t.symbol)}
              className="flex w-full items-center justify-between px-2 py-1 text-left font-mono text-[11px] text-text-primary hover:bg-base-bg"
            >
              <span>{t.symbol}</span>
              <span className="text-text-muted">{t.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

type MenuLevel = "root" | "timeframe" | "overlay" | "levels" | "connector" | "candles" | "chartStyle" | "background" | "timer" | "volumeAvg" | "volumeBars" | "dailyLevels" | "hud";

const VOLUME_BAR_COLOR_MODE_LABELS: Record<VolumeBarColorMode, string> = {
  two_color: "2-Color",
  one_color: "1-Color",
};

const CHART_STYLE_LABELS: Record<ChartStyle, string> = {
  candlestick: "Candlestick",
  bar: "Bar (OHLC)",
};

function RootRow({
  label,
  hint,
  swatch,
  onClick,
}: {
  label: string;
  hint: string;
  swatch?: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center justify-between rounded px-2 py-1.5 text-left hover:bg-base-bg"
    >
      <span className="font-mono text-xs text-text-primary">{label}</span>
      <span className="flex items-center gap-1.5 font-mono text-[11px] text-text-muted">
        {swatch && <span className="h-2.5 w-2.5 rounded-full border border-base-border" style={{ backgroundColor: swatch }} />}
        {hint}
        <span>&rsaquo;</span>
      </span>
    </button>
  );
}

function BackRow({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="mb-1 flex w-full items-center gap-1 rounded px-2 py-1 text-left font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-text-primary"
    >
      <span>&lsaquo;</span> {label}
    </button>
  );
}

// Shared swatch + hex-field control — same format used for chart background,
// grid color, and the timer color, per the "same format" request. Keeps its
// own draft state so a partially-typed invalid hex doesn't get lost or
// clobbered by the last-committed value while the user is still typing.
function ColorField({
  label,
  value,
  onChange,
  opacity,
  onOpacityChange,
}: {
  label?: string;
  value: string;
  onChange: (hex: string) => void;
  // Optional: when both are given, a compact opacity slider (0-100)
  // renders after the hex field. Optional rather than required because a
  // handful of colors in this app genuinely have no opacity companion
  // (none currently, as of decision #76 — every color field got one —
  // but kept optional so a future color-only field doesn't need a fake
  // opacity prop just to satisfy this signature).
  opacity?: number;
  onOpacityChange?: (opacity: number) => void;
}) {
  const [hexDraft, setHexDraft] = useState(value);
  useEffect(() => setHexDraft(value), [value]);

  return (
    <div className="flex flex-wrap items-center gap-2 px-2 py-1">
      {label && <span className="w-16 shrink-0 font-mono text-[10px] text-text-muted">{label}</span>}
      <input
        type="color"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-7 w-7 shrink-0 cursor-pointer rounded border border-base-border bg-transparent p-0"
        title="Pick a color"
      />
      <input
        value={hexDraft}
        onChange={(e) => {
          setHexDraft(e.target.value);
          if (isValidHex(e.target.value)) onChange(e.target.value);
        }}
        placeholder={value}
        className={`w-24 rounded border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary outline-none focus:border-signal ${
          isValidHex(hexDraft) ? "border-base-border" : "border-bear/60"
        }`}
      />
      {opacity !== undefined && onOpacityChange && (
        <div className="flex min-w-[100px] flex-1 items-center gap-1.5">
          <input
            type="range"
            min={COLOR_OPACITY_MIN}
            max={COLOR_OPACITY_MAX}
            value={opacity}
            onChange={(e) => onOpacityChange(Number(e.target.value))}
            className="h-1 flex-1 accent-signal"
            title="Opacity"
          />
          <span className="w-8 shrink-0 text-right font-mono text-[10px] text-text-muted">{opacity}%</span>
        </div>
      )}
    </div>
  );
}

// One row per volume-average line: checkbox to enable, an optional bar-count
// stepper (only for the 3 trailing-average lines, not "Day Avg"), and its own
// compact color swatch + hex field.
function VolumeAvgLineRow({
  subWindowId,
  volumeAvg,
  line,
  updateSubWindow,
}: {
  subWindowId: string;
  volumeAvg: VolumeAvgIndicatorConfig;
  line: VolumeAvgLineConfig;
  updateSubWindow: (id: string, patch: Partial<SubWindowConfig>) => void;
}) {
  const [hexDraft, setHexDraft] = useState(line.color);
  useEffect(() => setHexDraft(line.color), [line.color]);

  const patchLine = (linePatch: Partial<VolumeAvgLineConfig>) => {
    updateSubWindow(subWindowId, {
      volumeAvg: {
        ...volumeAvg,
        lines: volumeAvg.lines.map((l) => (l.id === line.id ? { ...l, ...linePatch } : l)),
      },
    });
  };

  const stepBarCount = (direction: 1 | -1) => {
    const next = line.barCount + direction * VOLUME_AVG_BAR_STEP;
    patchLine({ barCount: Math.min(VOLUME_AVG_BAR_MAX, Math.max(VOLUME_AVG_BAR_MIN, next)) });
  };

  return (
    <div className="mb-2 rounded border border-base-border/60 px-2 py-1.5">
      <div className="flex items-center gap-1.5">
        <input
          type="checkbox"
          checked={line.enabled}
          onChange={(e) => patchLine({ enabled: e.target.checked })}
          className="h-3 w-3 shrink-0 accent-signal"
        />
        <span className="flex-1 truncate font-mono text-[11px] text-text-primary">{line.label}</span>
        {line.adjustable && (
          <div className="flex shrink-0 items-center gap-1">
            <button
              onClick={() => stepBarCount(-1)}
              disabled={line.barCount <= VOLUME_AVG_BAR_MIN}
              className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
            >
              &minus;
            </button>
            <span className="w-5 text-center font-mono text-[10px] text-text-primary">{line.barCount}</span>
            <button
              onClick={() => stepBarCount(1)}
              disabled={line.barCount >= VOLUME_AVG_BAR_MAX}
              className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
            >
              +
            </button>
          </div>
        )}
      </div>
      <label className="mt-1 flex items-center gap-1.5 pl-[18px] font-mono text-[10px] text-text-muted">
        <input
          type="checkbox"
          checked={line.showPriceLabel}
          onChange={(e) => patchLine({ showPriceLabel: e.target.checked })}
          className="h-3 w-3 accent-signal"
        />
        Show price tag
      </label>
      <label className="mt-1 flex items-center gap-1.5 pl-[18px] font-mono text-[10px] text-text-muted">
        <input
          type="checkbox"
          checked={line.showNameLabel}
          onChange={(e) => patchLine({ showNameLabel: e.target.checked })}
          className="h-3 w-3 accent-signal"
        />
        Show label
      </label>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 pl-[18px]">
        <input
          type="color"
          value={line.color}
          onChange={(e) => patchLine({ color: e.target.value })}
          className="h-5 w-5 shrink-0 cursor-pointer rounded border border-base-border bg-transparent p-0"
          title="Pick a color"
        />
        <input
          value={hexDraft}
          onChange={(e) => {
            setHexDraft(e.target.value);
            if (isValidHex(e.target.value)) patchLine({ color: e.target.value });
          }}
          placeholder={line.color}
          className={`w-20 rounded border bg-base-bg px-1 py-0.5 font-mono text-[10px] text-text-primary outline-none focus:border-signal ${
            isValidHex(hexDraft) ? "border-base-border" : "border-bear/60"
          }`}
        />
        <input
          type="range"
          min={COLOR_OPACITY_MIN}
          max={COLOR_OPACITY_MAX}
          value={line.opacity}
          onChange={(e) => patchLine({ opacity: Number(e.target.value) })}
          className="h-1 w-12 accent-signal"
          title="Opacity"
        />
        <span className="w-7 shrink-0 text-right font-mono text-[10px] text-text-muted">{line.opacity}%</span>
      </div>
    </div>
  );
}

// One HUD line's editor: enable checkbox, then its ordered segments
// (literal text + variables) rendered as small removable chips, plus
// controls to append a new text segment or a new variable segment.
// Variable chips show the catalog label (utils/hud.ts's HUD_VARIABLES),
// not the raw key, so "GAP"/"DAY"/"ATR"/... reads the same here as it
// will on the actual chart. Segments are an ordered array specifically so
// a line can freely mix "some text" + a variable + "more text" rather
// than being locked to a fixed template — the literal ask ("text field
// will only contain text and variables") taken at face value rather than
// simplified into a fixed two-variable-per-line shape.
let hudMenuSegmentCounter = 0;
function newHudSegmentId(): string {
  hudMenuSegmentCounter += 1;
  return `hud-seg-${Date.now().toString(36)}-${hudMenuSegmentCounter}`;
}

function HudLineEditor({
  line,
  index,
  onChange,
}: {
  line: HudLineConfig;
  index: number;
  onChange: (patch: Partial<HudLineConfig>) => void;
}) {
  const [addVariable, setAddVariable] = useState<HudVariableKey | "">("");

  const updateTextSegment = (segId: string, value: string) => {
    onChange({ segments: line.segments.map((s) => (s.id === segId && s.kind === "text" ? { ...s, value } : s)) });
  };
  const removeSegment = (segId: string) => {
    onChange({ segments: line.segments.filter((s) => s.id !== segId) });
  };
  const addText = () => {
    const seg: HudSegment = { id: newHudSegmentId(), kind: "text", value: " " };
    onChange({ segments: [...line.segments, seg] });
  };
  const addVar = (key: HudVariableKey) => {
    const seg: HudSegment = { id: newHudSegmentId(), kind: "variable", variable: key };
    onChange({ segments: [...line.segments, seg] });
  };

  return (
    <div className="mb-2 rounded border border-base-border/60 px-2 py-1.5">
      <label className="mb-1 flex items-center gap-1.5 font-mono text-[11px] text-text-primary">
        <input
          type="checkbox"
          checked={line.enabled}
          onChange={(e) => onChange({ enabled: e.target.checked })}
          className="h-3 w-3 accent-signal"
        />
        Line {index + 1}
      </label>
      <div className="mb-1.5 flex flex-wrap items-center gap-1">
        {line.segments.length === 0 && (
          <span className="font-mono text-[10px] text-text-muted">Empty — add text or a variable below</span>
        )}
        {line.segments.map((seg) =>
          seg.kind === "variable" ? (
            <span
              key={seg.id}
              className="flex items-center gap-1 rounded bg-signal/20 px-1.5 py-0.5 font-mono text-[10px] text-signal"
            >
              {HUD_VARIABLES[seg.variable].label}
              <button onClick={() => removeSegment(seg.id)} className="text-signal/70 hover:text-signal" title="Remove">
                &times;
              </button>
            </span>
          ) : (
            <span key={seg.id} className="flex items-center gap-1 rounded border border-base-border bg-base-bg px-1 py-0.5">
              <input
                value={seg.value}
                onChange={(e) => updateTextSegment(seg.id, e.target.value)}
                placeholder="text"
                className="w-12 bg-transparent font-mono text-[10px] text-text-primary outline-none"
              />
              <button onClick={() => removeSegment(seg.id)} className="text-text-muted hover:text-text-primary" title="Remove">
                &times;
              </button>
            </span>
          )
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <button
          onClick={addText}
          className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-signal"
        >
          + Text
        </button>
        <select
          value={addVariable}
          onChange={(e) => {
            const key = e.target.value as HudVariableKey | "";
            if (key) {
              addVar(key);
              setAddVariable("");
            }
          }}
          className="rounded border border-base-border bg-base-bg px-1 py-0.5 font-mono text-[10px] text-text-primary outline-none focus:border-signal"
        >
          <option value="">+ Variable&hellip;</option>
          {HUD_VARIABLE_KEYS.map((key) => (
            <option key={key} value={key}>
              {HUD_VARIABLES[key].label} ({key})
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

// One row per overlay instance (SMA/EMA/VWAP) — checkbox to enable, a period
// stepper (bars considered, hidden for VWAP since it's session-anchored, not
// bar-count-based), a fractional line-width stepper (thickness — 0.5 steps;
// see PRICE_INDICATOR_LINE_WIDTH_STEP in types/workspace.ts for why that's
// safe here specifically), a price-label visibility checkbox, its own color
// swatch + hex field, and a remove button. Modeled directly on
// VolumeAvgLineRow above; the next overlay kind should extend
// OverlayIndicatorType rather than inventing a new row shape.
function OverlayIndicatorRow({
  subWindowId,
  priceIndicators,
  instance,
  updateSubWindow,
}: {
  subWindowId: string;
  priceIndicators: PriceIndicatorInstance[];
  instance: PriceIndicatorInstance;
  updateSubWindow: (id: string, patch: Partial<SubWindowConfig>) => void;
}) {
  const [hexDraft, setHexDraft] = useState(instance.color);
  useEffect(() => setHexDraft(instance.color), [instance.color]);

  const hasPeriod = OVERLAY_TYPES_WITH_PERIOD.includes(instance.type);

  const patchInstance = (patch: Partial<PriceIndicatorInstance>) => {
    updateSubWindow(subWindowId, {
      priceIndicators: priceIndicators.map((p) => (p.id === instance.id ? { ...p, ...patch } : p)),
    });
  };

  const removeInstance = () => {
    updateSubWindow(subWindowId, { priceIndicators: priceIndicators.filter((p) => p.id !== instance.id) });
  };

  const stepPeriod = (direction: 1 | -1) => {
    const current = instance.period ?? PRICE_INDICATOR_PERIOD_MIN;
    const next = current + direction * PRICE_INDICATOR_PERIOD_STEP;
    patchInstance({ period: Math.min(PRICE_INDICATOR_PERIOD_MAX, Math.max(PRICE_INDICATOR_PERIOD_MIN, next)) });
  };

  const stepLineWidth = (direction: 1 | -1) => {
    const next = instance.lineWidth + direction * PRICE_INDICATOR_LINE_WIDTH_STEP;
    patchInstance({ lineWidth: Math.min(PRICE_INDICATOR_LINE_WIDTH_MAX, Math.max(PRICE_INDICATOR_LINE_WIDTH_MIN, next)) });
  };

  return (
    <div className="mb-2 rounded border border-base-border/60 px-2 py-1.5">
      <div className="flex items-center gap-1.5">
        <input
          type="checkbox"
          checked={instance.enabled}
          onChange={(e) => patchInstance({ enabled: e.target.checked })}
          className="h-3 w-3 shrink-0 accent-signal"
        />
        <span className="flex-1 truncate font-mono text-[11px] text-text-primary">{priceIndicatorLabel(instance)}</span>
        <button
          onClick={removeInstance}
          title="Remove"
          className="shrink-0 rounded px-1 font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-bear"
        >
          &times;
        </button>
      </div>
      <div className="mt-1 flex items-center gap-3 pl-[18px]">
        {hasPeriod && (
          <div className="flex items-center gap-1">
            <span className="font-mono text-[9px] uppercase tracking-wide text-text-muted">Bars</span>
            <button
              onClick={() => stepPeriod(-1)}
              disabled={(instance.period ?? 0) <= PRICE_INDICATOR_PERIOD_MIN}
              className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
            >
              &minus;
            </button>
            <span className="w-7 text-center font-mono text-[10px] text-text-primary">{instance.period}</span>
            <button
              onClick={() => stepPeriod(1)}
              disabled={(instance.period ?? 0) >= PRICE_INDICATOR_PERIOD_MAX}
              className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
            >
              +
            </button>
          </div>
        )}
        <div className="flex items-center gap-1">
          <span className="font-mono text-[9px] uppercase tracking-wide text-text-muted">Width</span>
          <button
            onClick={() => stepLineWidth(-1)}
            disabled={instance.lineWidth <= PRICE_INDICATOR_LINE_WIDTH_MIN}
            className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
          >
            &minus;
          </button>
          <span className="w-6 text-center font-mono text-[10px] text-text-primary">{instance.lineWidth}</span>
          <button
            onClick={() => stepLineWidth(1)}
            disabled={instance.lineWidth >= PRICE_INDICATOR_LINE_WIDTH_MAX}
            className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
          >
            +
          </button>
        </div>
      </div>
      <label className="mt-1 flex items-center gap-1.5 pl-[18px] font-mono text-[10px] text-text-muted">
        <input
          type="checkbox"
          checked={instance.showPriceLabel}
          onChange={(e) => patchInstance({ showPriceLabel: e.target.checked })}
          className="h-3 w-3 accent-signal"
        />
        Show price tag
      </label>
      <label className="mt-1 flex items-center gap-1.5 pl-[18px] font-mono text-[10px] text-text-muted">
        <input
          type="checkbox"
          checked={instance.showNameLabel}
          onChange={(e) => patchInstance({ showNameLabel: e.target.checked })}
          className="h-3 w-3 accent-signal"
        />
        Show label
      </label>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 pl-[18px]">
        <input
          type="color"
          value={instance.color}
          onChange={(e) => patchInstance({ color: e.target.value })}
          className="h-5 w-5 shrink-0 cursor-pointer rounded border border-base-border bg-transparent p-0"
          title="Pick a color"
        />
        <input
          value={hexDraft}
          onChange={(e) => {
            setHexDraft(e.target.value);
            if (isValidHex(e.target.value)) patchInstance({ color: e.target.value });
          }}
          placeholder={instance.color}
          className={`w-20 rounded border bg-base-bg px-1 py-0.5 font-mono text-[10px] text-text-primary outline-none focus:border-signal ${
            isValidHex(hexDraft) ? "border-base-border" : "border-bear/60"
          }`}
        />
        <input
          type="range"
          min={COLOR_OPACITY_MIN}
          max={COLOR_OPACITY_MAX}
          value={instance.opacity}
          onChange={(e) => patchInstance({ opacity: Number(e.target.value) })}
          className="h-1 w-12 accent-signal"
          title="Opacity"
        />
        <span className="w-7 shrink-0 text-right font-mono text-[10px] text-text-muted">{instance.opacity}%</span>
      </div>
    </div>
  );
}

// One row per horizontal level instance (Previous Day Close/High/Low,
// Pre-Market High/Low, each individual Camarilla level, VPOC) — checkbox to
// enable, an integer line-width stepper (createPriceLine floors fractional
// widths, so no 0.5 step here — see types/workspace.ts), a 3-way line-style
// selector (solid/dashed/dotted), a price-label visibility checkbox, color,
// and remove.
function HorizontalLevelRow({
  subWindowId,
  horizontalLevels,
  instance,
  updateSubWindow,
}: {
  subWindowId: string;
  horizontalLevels: HorizontalLevelInstance[];
  instance: HorizontalLevelInstance;
  updateSubWindow: (id: string, patch: Partial<SubWindowConfig>) => void;
}) {
  const [hexDraft, setHexDraft] = useState(instance.color);
  useEffect(() => setHexDraft(instance.color), [instance.color]);

  const patchInstance = (patch: Partial<HorizontalLevelInstance>) => {
    updateSubWindow(subWindowId, {
      horizontalLevels: horizontalLevels.map((l) => (l.id === instance.id ? { ...l, ...patch } : l)),
    });
  };

  const removeInstance = () => {
    updateSubWindow(subWindowId, { horizontalLevels: horizontalLevels.filter((l) => l.id !== instance.id) });
  };

  const stepLineWidth = (direction: 1 | -1) => {
    const next = instance.lineWidth + direction;
    patchInstance({ lineWidth: Math.min(HORIZONTAL_LEVEL_LINE_WIDTH_MAX, Math.max(HORIZONTAL_LEVEL_LINE_WIDTH_MIN, next)) });
  };

  return (
    <div className="mb-2 rounded border border-base-border/60 px-2 py-1.5">
      <div className="flex items-center gap-1.5">
        <input
          type="checkbox"
          checked={instance.enabled}
          onChange={(e) => patchInstance({ enabled: e.target.checked })}
          className="h-3 w-3 shrink-0 accent-signal"
        />
        <span className="flex-1 truncate font-mono text-[11px] text-text-primary">{HORIZONTAL_LEVEL_LABELS[instance.type]}</span>
        <button
          onClick={removeInstance}
          title="Remove"
          className="shrink-0 rounded px-1 font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-bear"
        >
          &times;
        </button>
      </div>
      <div className="mt-1 flex items-center gap-3 pl-[18px]">
        <div className="flex items-center gap-1">
          <span className="font-mono text-[9px] uppercase tracking-wide text-text-muted">Width</span>
          <button
            onClick={() => stepLineWidth(-1)}
            disabled={instance.lineWidth <= HORIZONTAL_LEVEL_LINE_WIDTH_MIN}
            className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
          >
            &minus;
          </button>
          <span className="w-4 text-center font-mono text-[10px] text-text-primary">{instance.lineWidth}</span>
          <button
            onClick={() => stepLineWidth(1)}
            disabled={instance.lineWidth >= HORIZONTAL_LEVEL_LINE_WIDTH_MAX}
            className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
          >
            +
          </button>
        </div>
        <div className="flex items-center gap-1">
          {LINE_STYLE_OPTIONS.map((style) => (
            <button
              key={style}
              onClick={() => patchInstance({ lineStyle: style })}
              title={style}
              className={`flex h-5 w-6 items-center justify-center rounded border font-mono text-[9px] uppercase ${
                instance.lineStyle === style
                  ? "border-signal bg-signal/20 text-signal"
                  : "border-base-border text-text-muted hover:border-signal"
              }`}
            >
              {style === "solid" ? "\u2015" : style === "dashed" ? "- -" : "\u00b7\u00b7\u00b7"}
            </button>
          ))}
        </div>
      </div>
      <label className="mt-1 flex items-center gap-1.5 pl-[18px] font-mono text-[10px] text-text-muted">
        <input
          type="checkbox"
          checked={instance.showPriceLabel}
          onChange={(e) => patchInstance({ showPriceLabel: e.target.checked })}
          className="h-3 w-3 accent-signal"
        />
        Show price tag
      </label>
      <label className="mt-1 flex items-center gap-1.5 pl-[18px] font-mono text-[10px] text-text-muted">
        <input
          type="checkbox"
          checked={instance.showNameLabel}
          onChange={(e) => patchInstance({ showNameLabel: e.target.checked })}
          className="h-3 w-3 accent-signal"
        />
        Show label
      </label>
      <div className="mt-1 flex flex-wrap items-center gap-1.5 pl-[18px]">
        <input
          type="color"
          value={instance.color}
          onChange={(e) => patchInstance({ color: e.target.value })}
          className="h-5 w-5 shrink-0 cursor-pointer rounded border border-base-border bg-transparent p-0"
          title="Pick a color"
        />
        <input
          value={hexDraft}
          onChange={(e) => {
            setHexDraft(e.target.value);
            if (isValidHex(e.target.value)) patchInstance({ color: e.target.value });
          }}
          placeholder={instance.color}
          className={`w-20 rounded border bg-base-bg px-1 py-0.5 font-mono text-[10px] text-text-primary outline-none focus:border-signal ${
            isValidHex(hexDraft) ? "border-base-border" : "border-bear/60"
          }`}
        />
        <input
          type="range"
          min={COLOR_OPACITY_MIN}
          max={COLOR_OPACITY_MAX}
          value={instance.opacity}
          onChange={(e) => patchInstance({ opacity: Number(e.target.value) })}
          className="h-1 w-12 accent-signal"
          title="Opacity"
        />
        <span className="w-7 shrink-0 text-right font-mono text-[10px] text-text-muted">{instance.opacity}%</span>
      </div>
    </div>
  );
}

export function SubWindowMenu({ config, displaySymbol }: { config: SubWindowConfig; displaySymbol: string }) {
  const { updateSubWindow } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<MenuLevel>("root");
  const anchorRef = useRef<HTMLDivElement>(null);
  const placement = useDropdownPlacement(open, anchorRef);

  const activeVolumeAvgLines = config.volumeAvg.lines.filter((l) => l.enabled).length;
  const activeOverlayCount = config.priceIndicators.filter((p) => p.enabled).length;
  const activeLevelCount = config.horizontalLevels.filter((l) => l.enabled).length;

  const closeMenu = () => {
    setOpen(false);
    setLevel("root");
  };

  const addOverlayInstance = (type: OverlayIndicatorType) => {
    updateSubWindow(config.id, {
      priceIndicators: [...config.priceIndicators, createPriceIndicatorInstance(type, config.priceIndicators.length)],
    });
  };

  // Adds whichever members of the group aren't already present — safe to
  // click more than once (e.g. clicking "Camarilla" again after removing
  // just R1 only re-adds R1, not all nine).
  const addHorizontalLevelGroup = (types: HorizontalLevelType[]) => {
    const missing = types.filter((t) => !config.horizontalLevels.some((l) => l.type === t));
    if (missing.length === 0) return;
    updateSubWindow(config.id, {
      horizontalLevels: [...config.horizontalLevels, ...missing.map(createHorizontalLevelInstance)],
    });
  };

  // First press from "All" always lands on the default (20) regardless of
  // direction — only once on a specific number do +/- actually step by 5.
  const stepCandleLimit = (direction: 1 | -1) => {
    if (config.candleLimit === "all") {
      updateSubWindow(config.id, { candleLimit: CANDLE_LIMIT_DEFAULT });
      return;
    }
    const next = config.candleLimit + direction * CANDLE_LIMIT_STEP;
    updateSubWindow(config.id, { candleLimit: Math.min(CANDLE_LIMIT_MAX, Math.max(CANDLE_LIMIT_MIN, next)) });
  };

  return (
    <div className="relative" ref={anchorRef}>
      <div className="flex items-center gap-2 border-b border-base-border bg-base-panel px-2 py-1">
        <TickerSearch config={config} displaySymbol={displaySymbol} />
        {config.connector !== "none" && (
          <span
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ backgroundColor: CONNECTOR_COLORS[config.connector] }}
            title={`Connector ${config.connector}`}
          />
        )}
        <span className="truncate font-mono text-xs font-semibold text-text-primary">{displaySymbol}</span>
        <span className="shrink-0 font-mono text-[10px] text-text-muted">
          {config.liveTick ? "tick" : config.timeframe}
        </span>
        {activeOverlayCount > 0 && (
          <span className="truncate font-mono text-[10px] text-text-muted">
            {config.priceIndicators.filter((p) => p.enabled).map(priceIndicatorLabel).join(", ")}
          </span>
        )}
        <button
          onClick={() => (open ? closeMenu() : setOpen(true))}
          className="ml-auto shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-text-primary"
        >
          &#8942;
        </button>
      </div>

      {open && (
        <div
          className={`absolute right-0 z-20 overflow-y-auto rounded-md border border-base-border bg-base-panel p-2 shadow-xl ${
            placement.vertical === "down" ? "top-full mt-1 rounded-t-none" : "bottom-full mb-1 rounded-b-none"
          } ${level === "volumeAvg" || level === "overlay" || level === "levels" || level === "hud" ? "w-72" : "w-56"}`}
          style={{ maxHeight: placement.maxHeight }}
        >
          {/* A flat max-h-[80vh] used to live here — it capped the panel's
              OWN height, but did nothing about WHERE it was anchored: a
              sub-window near the bottom of a busy grid has its toolbar
              already most of the way down the viewport, so "80% of the
              viewport" measured from there still ran off the bottom of
              the screen with no scrollbar ever appearing (overflow-y-auto
              only fires when content exceeds the panel's own max-height
              box, not when the panel itself sits beyond the visible
              viewport). useDropdownPlacement (shared with LayoutsMenu,
              GridPicker, and FeatureEnginePanel's ticker search — same
              bug, same fix, one hook) measures the anchor's real position
              and flips the panel to open upward with an accurate
              max-height when there isn't enough room below, instead of a
              fixed fraction that assumes the anchor is near the top. */}
          {level === "root" && (
            <div className="flex flex-col">
              <RootRow label="Timeframe" hint={config.liveTick ? "Tick" : config.timeframe} onClick={() => setLevel("timeframe")} />
              <RootRow
                label="Indicators"
                hint={activeOverlayCount > 0 ? `${activeOverlayCount} active` : "None"}
                onClick={() => setLevel("overlay")}
              />
              <RootRow
                label="Levels"
                hint={activeLevelCount > 0 ? `${activeLevelCount} active` : "None"}
                onClick={() => setLevel("levels")}
              />
              <RootRow
                label="Connector"
                hint={config.connector === "none" ? "None" : String(config.connector)}
                onClick={() => setLevel("connector")}
              />
              <RootRow label="Candles" hint={candleLimitLabel(config.candleLimit)} onClick={() => setLevel("candles")} />
              <RootRow
                label="Chart Style"
                hint={CHART_STYLE_LABELS[config.chartStyle]}
                onClick={() => setLevel("chartStyle")}
              />
              <RootRow
                label="Background"
                hint={config.backgroundColor}
                swatch={config.backgroundColor}
                onClick={() => setLevel("background")}
              />
              <RootRow
                label="Timer"
                hint={config.timer.enabled ? "On" : "Off"}
                swatch={config.timer.enabled ? config.timer.color : undefined}
                onClick={() => setLevel("timer")}
              />
              <RootRow
                label="Volume Avg"
                hint={config.volumeAvg.enabled ? `${activeVolumeAvgLines} line${activeVolumeAvgLines === 1 ? "" : "s"}` : "Off"}
                onClick={() => setLevel("volumeAvg")}
              />
              <RootRow
                label="Volume Bars"
                hint={config.volumeBars.enabled ? VOLUME_BAR_COLOR_MODE_LABELS[config.volumeBars.colorMode] : "Off"}
                swatch={
                  config.volumeBars.enabled
                    ? config.volumeBars.colorMode === "one_color"
                      ? config.volumeBars.singleColor
                      : config.volumeBars.upColor
                    : undefined
                }
                onClick={() => setLevel("volumeBars")}
              />
              <RootRow
                label="Daily Levels"
                hint={config.dailyLevelsConfig.enabled ? `Strength \u2265 ${config.dailyLevelsConfig.minStrength}` : "Off"}
                swatch={config.dailyLevelsConfig.enabled ? config.dailyLevelsConfig.color : undefined}
                onClick={() => setLevel("dailyLevels")}
              />
              <RootRow
                label="HUD"
                hint={config.hud.enabled ? `${config.hud.lines.filter((l) => l.enabled).length} line(s)` : "Off"}
                swatch={config.hud.enabled ? config.hud.textColor : undefined}
                onClick={() => setLevel("hud")}
              />
            </div>
          )}

          {level === "timeframe" && (
            <div>
              <BackRow label="Timeframe" onClick={() => setLevel("root")} />
              {/* Decision #72 — "Tick" lives in this same list, not a
                  separate menu section: conceptually it's the person
                  picking what the currently-forming bar does, exactly
                  like picking 1m/5m/etc picks what a closed bar means.
                  It always forces timeframe to "1m" together with
                  liveTick — tick fluidity has no meaning against a 5m/
                  15m/1h/1d bar (see live_tick_relay.py: it only ever
                  tracks the current 1m bar) — and picking any real
                  timeframe below turns liveTick back off, so the two
                  never end up in an inconsistent combination. */}
              <button
                onClick={() => {
                  updateSubWindow(config.id, { timeframe: "1m", liveTick: true });
                  setLevel("root");
                }}
                className={`flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs ${
                  config.liveTick ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                }`}
              >
                <span>Tick</span>
                <span className="text-[10px] text-text-muted">live</span>
              </button>
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  onClick={() => {
                    updateSubWindow(config.id, { timeframe: tf, liveTick: false });
                    setLevel("root");
                  }}
                  className={`flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs ${
                    !config.liveTick && config.timeframe === tf ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          )}

          {level === "overlay" && (
            <div>
              <BackRow label="Indicators" onClick={() => setLevel("root")} />
              <div className="max-h-72 overflow-y-auto pr-0.5">
                {config.priceIndicators.length === 0 && (
                  <div className="px-2 py-1 font-mono text-[11px] text-text-muted">None added yet.</div>
                )}
                {config.priceIndicators.map((instance) => (
                  <OverlayIndicatorRow
                    key={instance.id}
                    subWindowId={config.id}
                    priceIndicators={config.priceIndicators}
                    instance={instance}
                    updateSubWindow={updateSubWindow}
                  />
                ))}
              </div>
              <div className="mt-1 grid grid-cols-3 gap-1">
                <button
                  onClick={() => addOverlayInstance("SMA")}
                  className="rounded border border-dashed border-base-border px-2 py-1 text-center font-mono text-[11px] text-text-muted hover:border-signal hover:text-signal"
                >
                  + SMA
                </button>
                <button
                  onClick={() => addOverlayInstance("EMA")}
                  className="rounded border border-dashed border-base-border px-2 py-1 text-center font-mono text-[11px] text-text-muted hover:border-signal hover:text-signal"
                >
                  + EMA
                </button>
                <button
                  onClick={() => addOverlayInstance("VWAP")}
                  className="rounded border border-dashed border-base-border px-2 py-1 text-center font-mono text-[11px] text-text-muted hover:border-signal hover:text-signal"
                >
                  + VWAP
                </button>
              </div>
            </div>
          )}

          {level === "levels" && (
            <div>
              <BackRow label="Levels" onClick={() => setLevel("root")} />
              <div className="max-h-72 overflow-y-auto pr-0.5">
                {config.horizontalLevels.length === 0 && (
                  <div className="px-2 py-1 font-mono text-[11px] text-text-muted">None added yet.</div>
                )}
                {config.horizontalLevels.map((instance) => (
                  <HorizontalLevelRow
                    key={instance.id}
                    subWindowId={config.id}
                    horizontalLevels={config.horizontalLevels}
                    instance={instance}
                    updateSubWindow={updateSubWindow}
                  />
                ))}
              </div>
              <div className="mt-1 flex flex-col gap-1">
                {HORIZONTAL_LEVEL_GROUPS.map((group) => {
                  const allPresent = group.types.every((t) => config.horizontalLevels.some((l) => l.type === t));
                  return (
                    <button
                      key={group.label}
                      onClick={() => addHorizontalLevelGroup(group.types)}
                      disabled={allPresent}
                      className="rounded border border-dashed border-base-border px-2 py-1 text-center font-mono text-[11px] text-text-muted hover:border-signal hover:text-signal disabled:cursor-default disabled:opacity-30 disabled:hover:border-base-border disabled:hover:text-text-muted"
                    >
                      {allPresent ? "\u2713 " : "+ "}
                      {group.label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {level === "connector" && (
            <div>
              <BackRow label="Connector" onClick={() => setLevel("root")} />
              <button
                onClick={() => {
                  updateSubWindow(config.id, { connector: "none" });
                  setLevel("root");
                }}
                className={`flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs ${
                  config.connector === "none" ? "bg-base-bg text-text-primary" : "text-text-primary hover:bg-base-bg"
                }`}
              >
                None
              </button>
              <div className="mt-1 grid grid-cols-5 gap-1 px-2">
                {LINK_CONNECTOR_IDS.map((id) => (
                  <button
                    key={id}
                    onClick={() => {
                      updateSubWindow(config.id, { connector: id });
                      setLevel("root");
                    }}
                    className="flex h-6 w-6 items-center justify-center rounded font-mono text-[11px]"
                    style={{
                      backgroundColor: config.connector === id ? CONNECTOR_COLORS[id] : "transparent",
                      color: config.connector === id ? "#0B0E14" : CONNECTOR_COLORS[id],
                      border: `1px solid ${CONNECTOR_COLORS[id]}`,
                    }}
                  >
                    {id}
                  </button>
                ))}
              </div>
            </div>
          )}

          {level === "candles" && (
            <div>
              <BackRow label="Candles" onClick={() => setLevel("root")} />
              <button
                onClick={() => updateSubWindow(config.id, { candleLimit: "all" })}
                className={`mb-2 flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs ${
                  config.candleLimit === "all" ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                }`}
              >
                All
                {config.candleLimit === "all" && <span>&#10003;</span>}
              </button>
              <div className="flex items-center justify-center gap-3 py-1">
                <button
                  onClick={() => stepCandleLimit(-1)}
                  disabled={config.candleLimit !== "all" && config.candleLimit <= CANDLE_LIMIT_MIN}
                  className="flex h-7 w-7 items-center justify-center rounded border border-base-border font-mono text-sm text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
                >
                  &minus;
                </button>
                <span className="w-12 text-center font-mono text-sm text-text-primary">
                  {config.candleLimit === "all" ? "—" : config.candleLimit}
                </span>
                <button
                  onClick={() => stepCandleLimit(1)}
                  disabled={config.candleLimit !== "all" && config.candleLimit >= CANDLE_LIMIT_MAX}
                  className="flex h-7 w-7 items-center justify-center rounded border border-base-border font-mono text-sm text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
                >
                  +
                </button>
              </div>
            </div>
          )}

          {level === "chartStyle" && (
            <div>
              <BackRow label="Chart Style" onClick={() => setLevel("root")} />
              {/* Global default is DEFAULT_CHART_STYLE ("candlestick"),
                  applied to every newly-created sub-window; this screen is
                  the per-sub-window override, same "shared default,
                  independently overridable" shape every other display
                  setting in this menu already has (background/grid, volume
                  bars, etc.) — confirmed decision #73. A future third
                  style is one more entry in CHART_STYLES, no menu
                  restructuring needed. */}
              <div className="grid grid-cols-2 gap-1 px-2">
                {CHART_STYLES.map((style) => (
                  <button
                    key={style}
                    onClick={() => updateSubWindow(config.id, { chartStyle: style })}
                    className={`rounded border px-2 py-1 text-center font-mono text-[11px] ${
                      config.chartStyle === style
                        ? "border-signal text-signal"
                        : "border-base-border text-text-muted hover:text-text-primary"
                    }`}
                  >
                    {CHART_STYLE_LABELS[style]}
                  </button>
                ))}
              </div>
              <button
                onClick={() => updateSubWindow(config.id, { chartStyle: DEFAULT_CHART_STYLE })}
                className="mt-2 w-full rounded px-2 py-1 text-left font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-text-primary"
              >
                Reset to default
              </button>
            </div>
          )}

          {level === "background" && (
            <div>
              <BackRow label="Background" onClick={() => setLevel("root")} />
              <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                Chart background
              </div>
              <ColorField
                value={config.backgroundColor}
                onChange={(hex) => updateSubWindow(config.id, { backgroundColor: hex })}
                opacity={config.backgroundOpacity}
                onOpacityChange={(opacity) => updateSubWindow(config.id, { backgroundOpacity: opacity })}
              />
              <div className="mb-1 mt-2 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                Grid lines
              </div>
              <ColorField
                value={config.gridColor}
                onChange={(hex) => updateSubWindow(config.id, { gridColor: hex })}
                opacity={config.gridOpacity}
                onOpacityChange={(opacity) => updateSubWindow(config.id, { gridOpacity: opacity })}
              />
              <button
                onClick={() =>
                  updateSubWindow(config.id, {
                    backgroundColor: DEFAULT_CHART_BG,
                    backgroundOpacity: COLOR_OPACITY_MAX,
                    gridColor: DEFAULT_GRID_COLOR,
                    gridOpacity: COLOR_OPACITY_MAX,
                  })
                }
                className="mt-1 w-full rounded px-2 py-1 text-left font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-text-primary"
              >
                Reset to default
              </button>
            </div>
          )}

          {level === "timer" && (
            <div>
              <BackRow label="Timer" onClick={() => setLevel("root")} />
              <button
                onClick={() => updateSubWindow(config.id, { timer: { ...config.timer, enabled: !config.timer.enabled } })}
                className={`mb-2 flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs ${
                  config.timer.enabled ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                }`}
              >
                Show timer
                {config.timer.enabled && <span>&#10003;</span>}
              </button>
              <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                Sweep color
              </div>
              <ColorField
                value={config.timer.color}
                onChange={(hex) => updateSubWindow(config.id, { timer: { ...config.timer, color: hex } })}
                opacity={config.timer.opacity}
                onOpacityChange={(opacity) => updateSubWindow(config.id, { timer: { ...config.timer, opacity } })}
              />
              <button
                onClick={() => updateSubWindow(config.id, { timer: { ...config.timer, color: DEFAULT_TIMER_COLOR, opacity: COLOR_OPACITY_MAX } })}
                className="mt-1 w-full rounded px-2 py-1 text-left font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-text-primary"
              >
                Reset color to default
              </button>
            </div>
          )}

          {level === "volumeAvg" && (
            <div>
              <BackRow label="Volume Avg" onClick={() => setLevel("root")} />
              <button
                onClick={() => updateSubWindow(config.id, { volumeAvg: { ...config.volumeAvg, enabled: !config.volumeAvg.enabled } })}
                className={`mb-2 flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs ${
                  config.volumeAvg.enabled ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                }`}
              >
                Show on volume pane
                {config.volumeAvg.enabled && <span>&#10003;</span>}
              </button>
              <div className="max-h-72 overflow-y-auto pr-0.5">
                {config.volumeAvg.lines.map((line) => (
                  <VolumeAvgLineRow
                    key={line.id}
                    subWindowId={config.id}
                    volumeAvg={config.volumeAvg}
                    line={line}
                    updateSubWindow={updateSubWindow}
                  />
                ))}
              </div>
            </div>
          )}

          {level === "volumeBars" && (
            <div>
              <BackRow label="Volume Bars" onClick={() => setLevel("root")} />
              <button
                onClick={() => updateSubWindow(config.id, { volumeBars: { ...config.volumeBars, enabled: !config.volumeBars.enabled } })}
                className={`mb-2 flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs ${
                  config.volumeBars.enabled ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                }`}
              >
                Show volume bars
                {config.volumeBars.enabled && <span>&#10003;</span>}
              </button>
              {config.volumeBars.enabled && (
                <>
                  <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">Color mode</div>
                  <div className="mb-2 grid grid-cols-2 gap-1 px-2">
                    {VOLUME_BAR_COLOR_MODES.map((mode) => (
                      <button
                        key={mode}
                        onClick={() => updateSubWindow(config.id, { volumeBars: { ...config.volumeBars, colorMode: mode } })}
                        className={`rounded border px-2 py-1 text-center font-mono text-[11px] ${
                          config.volumeBars.colorMode === mode
                            ? "border-signal text-signal"
                            : "border-base-border text-text-muted hover:text-text-primary"
                        }`}
                      >
                        {VOLUME_BAR_COLOR_MODE_LABELS[mode]}
                      </button>
                    ))}
                  </div>
                  {config.volumeBars.colorMode === "two_color" ? (
                    <>
                      <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                        Up (close &ge; open)
                      </div>
                      <ColorField
                        value={config.volumeBars.upColor}
                        onChange={(hex) => updateSubWindow(config.id, { volumeBars: { ...config.volumeBars, upColor: hex } })}
                        opacity={config.volumeBars.upOpacity}
                        onOpacityChange={(opacity) => updateSubWindow(config.id, { volumeBars: { ...config.volumeBars, upOpacity: opacity } })}
                      />
                      <div className="mb-1 mt-2 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                        Down (close &lt; open)
                      </div>
                      <ColorField
                        value={config.volumeBars.downColor}
                        onChange={(hex) => updateSubWindow(config.id, { volumeBars: { ...config.volumeBars, downColor: hex } })}
                        opacity={config.volumeBars.downOpacity}
                        onOpacityChange={(opacity) => updateSubWindow(config.id, { volumeBars: { ...config.volumeBars, downOpacity: opacity } })}
                      />
                    </>
                  ) : (
                    <>
                      <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">Bar color</div>
                      <ColorField
                        value={config.volumeBars.singleColor}
                        onChange={(hex) => updateSubWindow(config.id, { volumeBars: { ...config.volumeBars, singleColor: hex } })}
                        opacity={config.volumeBars.singleOpacity}
                        onOpacityChange={(opacity) => updateSubWindow(config.id, { volumeBars: { ...config.volumeBars, singleOpacity: opacity } })}
                      />
                    </>
                  )}
                  <button
                    onClick={() => updateSubWindow(config.id, { volumeBars: createDefaultVolumeBarsConfig() })}
                    className="mt-2 w-full rounded px-2 py-1 text-left font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-text-primary"
                  >
                    Reset to default
                  </button>
                </>
              )}
            </div>
          )}

          {level === "dailyLevels" && (
            <div>
              <BackRow label="Daily Levels" onClick={() => setLevel("root")} />
              <button
                onClick={() =>
                  updateSubWindow(config.id, { dailyLevelsConfig: { ...config.dailyLevelsConfig, enabled: !config.dailyLevelsConfig.enabled } })
                }
                className={`mb-2 flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs ${
                  config.dailyLevelsConfig.enabled ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                }`}
              >
                Show Daily Levels
                {config.dailyLevelsConfig.enabled && <span>&#10003;</span>}
              </button>
              {config.dailyLevelsConfig.enabled && (
                <>
                  <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                    Min strength — hides weaker levels; each is tagged with its own
                    strength on the chart regardless
                  </div>
                  <div className="mb-2 flex items-center gap-1 px-2">
                    <button
                      onClick={() =>
                        updateSubWindow(config.id, {
                          dailyLevelsConfig: {
                            ...config.dailyLevelsConfig,
                            minStrength: Math.max(DAILY_LEVELS_MIN_STRENGTH_FLOOR, config.dailyLevelsConfig.minStrength - 1),
                          },
                        })
                      }
                      disabled={config.dailyLevelsConfig.minStrength <= DAILY_LEVELS_MIN_STRENGTH_FLOOR}
                      className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
                    >
                      &minus;
                    </button>
                    <span className="w-6 text-center font-mono text-[10px] text-text-primary">
                      {config.dailyLevelsConfig.minStrength}
                    </span>
                    <button
                      onClick={() =>
                        updateSubWindow(config.id, {
                          dailyLevelsConfig: {
                            ...config.dailyLevelsConfig,
                            minStrength: Math.min(DAILY_LEVELS_MIN_STRENGTH_CEILING, config.dailyLevelsConfig.minStrength + 1),
                          },
                        })
                      }
                      disabled={config.dailyLevelsConfig.minStrength >= DAILY_LEVELS_MIN_STRENGTH_CEILING}
                      className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
                    >
                      +
                    </button>
                  </div>
                  <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                    Price range — hides levels outside this band; leave a
                    field blank for no bound on that side (decision #62)
                  </div>
                  <div className="mb-2 flex items-center gap-1 px-2">
                    <input
                      type="number"
                      inputMode="decimal"
                      placeholder="Min"
                      value={config.dailyLevelsConfig.minPrice ?? ""}
                      onChange={(e) =>
                        updateSubWindow(config.id, {
                          dailyLevelsConfig: {
                            ...config.dailyLevelsConfig,
                            minPrice: e.target.value === "" ? null : Number(e.target.value),
                          },
                        })
                      }
                      className="w-16 rounded border border-base-border bg-base-bg px-1 py-0.5 font-mono text-[10px] text-text-primary focus:border-signal focus:outline-none"
                    />
                    <span className="font-mono text-[10px] text-text-muted">&ndash;</span>
                    <input
                      type="number"
                      inputMode="decimal"
                      placeholder="Max"
                      value={config.dailyLevelsConfig.maxPrice ?? ""}
                      onChange={(e) =>
                        updateSubWindow(config.id, {
                          dailyLevelsConfig: {
                            ...config.dailyLevelsConfig,
                            maxPrice: e.target.value === "" ? null : Number(e.target.value),
                          },
                        })
                      }
                      className="w-16 rounded border border-base-border bg-base-bg px-1 py-0.5 font-mono text-[10px] text-text-primary focus:border-signal focus:outline-none"
                    />
                    <span className="font-mono text-[9px] text-text-muted">USD</span>
                  </div>
                  <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                    History — how far back to cluster from (decision #62,
                    re-clustered server-side from already-fetched data, no
                    extra fetch)
                  </div>
                  <div className="mb-2 flex flex-wrap gap-1 px-2">
                    {DAILY_LEVELS_LOOKBACK_PRESETS.map((preset) => (
                      <button
                        key={preset.label}
                        onClick={() =>
                          updateSubWindow(config.id, {
                            dailyLevelsConfig: { ...config.dailyLevelsConfig, lookbackDays: preset.days },
                          })
                        }
                        className={`rounded border px-1.5 py-0.5 font-mono text-[10px] ${
                          config.dailyLevelsConfig.lookbackDays === preset.days
                            ? "border-signal bg-signal/20 text-signal"
                            : "border-base-border text-text-primary hover:border-signal"
                        }`}
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                  <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                    Color — one color for every level; strength shows as a tag on
                    each line instead
                  </div>
                  <ColorField
                    value={config.dailyLevelsConfig.color}
                    onChange={(hex) => updateSubWindow(config.id, { dailyLevelsConfig: { ...config.dailyLevelsConfig, color: hex } })}
                    opacity={config.dailyLevelsConfig.opacity}
                    onOpacityChange={(opacity) => updateSubWindow(config.id, { dailyLevelsConfig: { ...config.dailyLevelsConfig, opacity } })}
                  />
                  <div className="mb-1 mt-2 flex items-center gap-1 px-2">
                    <span className="font-mono text-[9px] uppercase tracking-wide text-text-muted">Width</span>
                    <button
                      onClick={() =>
                        updateSubWindow(config.id, {
                          dailyLevelsConfig: {
                            ...config.dailyLevelsConfig,
                            lineWidth: Math.max(DAILY_LEVELS_LINE_WIDTH_MIN, config.dailyLevelsConfig.lineWidth - 1),
                          },
                        })
                      }
                      disabled={config.dailyLevelsConfig.lineWidth <= DAILY_LEVELS_LINE_WIDTH_MIN}
                      className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
                    >
                      &minus;
                    </button>
                    <span className="w-4 text-center font-mono text-[10px] text-text-primary">{config.dailyLevelsConfig.lineWidth}</span>
                    <button
                      onClick={() =>
                        updateSubWindow(config.id, {
                          dailyLevelsConfig: {
                            ...config.dailyLevelsConfig,
                            lineWidth: Math.min(DAILY_LEVELS_LINE_WIDTH_MAX, config.dailyLevelsConfig.lineWidth + 1),
                          },
                        })
                      }
                      disabled={config.dailyLevelsConfig.lineWidth >= DAILY_LEVELS_LINE_WIDTH_MAX}
                      className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
                    >
                      +
                    </button>
                  </div>
                  <button
                    onClick={() =>
                      updateSubWindow(config.id, {
                        dailyLevelsConfig: { ...config.dailyLevelsConfig, showPriceLabels: !config.dailyLevelsConfig.showPriceLabels },
                      })
                    }
                    className={`mt-1 flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-[11px] ${
                      config.dailyLevelsConfig.showPriceLabels ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                    }`}
                  >
                    Show price on axis
                    {config.dailyLevelsConfig.showPriceLabels && <span>&#10003;</span>}
                  </button>
                  <button
                    onClick={() =>
                      updateSubWindow(config.id, {
                        dailyLevelsConfig: { ...config.dailyLevelsConfig, showNameLabels: !config.dailyLevelsConfig.showNameLabels },
                      })
                    }
                    className={`mt-1 flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-[11px] ${
                      config.dailyLevelsConfig.showNameLabels ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                    }`}
                  >
                    Show "DL-N" label
                    {config.dailyLevelsConfig.showNameLabels && <span>&#10003;</span>}
                  </button>
                  <button
                    onClick={() => updateSubWindow(config.id, { dailyLevelsConfig: createDefaultDailyLevelsConfig() })}
                    className="mt-2 w-full rounded px-2 py-1 text-left font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-text-primary"
                  >
                    Reset to default
                  </button>
                </>
              )}
            </div>
          )}

          {level === "hud" && (
            <div>
              <BackRow label="HUD" onClick={() => setLevel("root")} />
              <button
                onClick={() => updateSubWindow(config.id, { hud: { ...config.hud, enabled: !config.hud.enabled } })}
                className={`mb-2 flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs ${
                  config.hud.enabled ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                }`}
              >
                Show HUD
                {config.hud.enabled && <span>&#10003;</span>}
              </button>
              {config.hud.enabled && (
                <>
                  <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                    Lines — each line mixes literal text with live values (GAP,
                    DAY, ATR, P/L, RVOL, VOL); ATR is ATR(14), the only period
                    the backend computes
                  </div>
                  <div className="px-2">
                    {config.hud.lines.map((line, idx) => (
                      <HudLineEditor
                        key={line.id}
                        line={line}
                        index={idx}
                        onChange={(patch) =>
                          updateSubWindow(config.id, {
                            hud: {
                              ...config.hud,
                              lines: config.hud.lines.map((l) => (l.id === line.id ? { ...l, ...patch } : l)),
                            },
                          })
                        }
                      />
                    ))}
                  </div>
                  <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                    Position
                  </div>
                  <div className="mb-2 grid grid-cols-2 gap-1 px-2">
                    {(["left", "right"] as const).map((align) => (
                      <button
                        key={align}
                        onClick={() => updateSubWindow(config.id, { hud: { ...config.hud, align } })}
                        className={`rounded border px-2 py-1 text-center font-mono text-[11px] capitalize ${
                          config.hud.align === align
                            ? "border-signal text-signal"
                            : "border-base-border text-text-muted hover:text-text-primary"
                        }`}
                      >
                        {align}
                      </button>
                    ))}
                  </div>
                  <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">Background</div>
                  <ColorField
                    value={config.hud.backgroundColor}
                    onChange={(hex) => updateSubWindow(config.id, { hud: { ...config.hud, backgroundColor: hex } })}
                    opacity={config.hud.backgroundOpacity}
                    onOpacityChange={(opacity) => updateSubWindow(config.id, { hud: { ...config.hud, backgroundOpacity: opacity } })}
                  />
                  <div className="mb-1 mt-2 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">Text color</div>
                  <ColorField
                    value={config.hud.textColor}
                    onChange={(hex) => updateSubWindow(config.id, { hud: { ...config.hud, textColor: hex } })}
                    opacity={config.hud.textOpacity}
                    onOpacityChange={(opacity) => updateSubWindow(config.id, { hud: { ...config.hud, textOpacity: opacity } })}
                  />
                  <button
                    onClick={() => updateSubWindow(config.id, { hud: createDefaultHudConfig() })}
                    className="mt-2 w-full rounded px-2 py-1 text-left font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-text-primary"
                  >
                    Reset to default
                  </button>
                </>
              )}
            </div>
          )}

        </div>
      )}
    </div>
  );
}
