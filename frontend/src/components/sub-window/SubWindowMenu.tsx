import { useEffect, useState } from "react";
import {
  AVAILABLE_INDICATORS,
  CANDLE_LIMIT_DEFAULT,
  CANDLE_LIMIT_MAX,
  CANDLE_LIMIT_MIN,
  CANDLE_LIMIT_STEP,
  DEFAULT_CHART_BG,
  DEFAULT_GRID_COLOR,
  DEFAULT_TIMER_COLOR,
  LINK_CONNECTOR_IDS,
  PRICE_INDICATOR_LINE_WIDTH_MAX,
  PRICE_INDICATOR_LINE_WIDTH_MIN,
  PRICE_INDICATOR_PERIOD_MAX,
  PRICE_INDICATOR_PERIOD_MIN,
  PRICE_INDICATOR_PERIOD_STEP,
  TIMEFRAMES,
  VOLUME_AVG_BAR_MAX,
  VOLUME_AVG_BAR_MIN,
  VOLUME_AVG_BAR_STEP,
  createPriceIndicatorInstance,
  priceIndicatorLabel,
  type CandleLimit,
  type PriceIndicatorInstance,
  type SubWindowConfig,
  type VolumeAvgIndicatorConfig,
  type VolumeAvgLineConfig,
} from "../../types/workspace";
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
    <div className="relative shrink-0">
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
        <div className="absolute left-0 top-full z-30 mt-1 max-h-40 w-40 overflow-y-auto rounded border border-base-border bg-base-panel shadow-xl">
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

type MenuLevel = "root" | "timeframe" | "indicators" | "connector" | "candles" | "background" | "timer" | "volumeAvg" | "sma";

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
}: {
  label?: string;
  value: string;
  onChange: (hex: string) => void;
}) {
  const [hexDraft, setHexDraft] = useState(value);
  useEffect(() => setHexDraft(value), [value]);

  return (
    <div className="flex items-center gap-2 px-2 py-1">
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
      <div className="mt-1 flex items-center gap-1.5 pl-[18px]">
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
      </div>
    </div>
  );
}

// One row per SMA instance — checkbox to enable, a period stepper (bars
// considered), a line-width stepper (thickness), its own color swatch + hex
// field, and a remove button. Modeled directly on VolumeAvgLineRow above;
// the next indicator kind to get this treatment should follow the same
// shape rather than inventing a new one.
function SmaIndicatorRow({
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

  const patchInstance = (patch: Partial<PriceIndicatorInstance>) => {
    updateSubWindow(subWindowId, {
      priceIndicators: priceIndicators.map((p) => (p.id === instance.id ? { ...p, ...patch } : p)),
    });
  };

  const removeInstance = () => {
    updateSubWindow(subWindowId, { priceIndicators: priceIndicators.filter((p) => p.id !== instance.id) });
  };

  const stepPeriod = (direction: 1 | -1) => {
    const next = instance.period + direction * PRICE_INDICATOR_PERIOD_STEP;
    patchInstance({ period: Math.min(PRICE_INDICATOR_PERIOD_MAX, Math.max(PRICE_INDICATOR_PERIOD_MIN, next)) });
  };

  const stepLineWidth = (direction: 1 | -1) => {
    const next = instance.lineWidth + direction;
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
        <div className="flex items-center gap-1">
          <span className="font-mono text-[9px] uppercase tracking-wide text-text-muted">Bars</span>
          <button
            onClick={() => stepPeriod(-1)}
            disabled={instance.period <= PRICE_INDICATOR_PERIOD_MIN}
            className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
          >
            &minus;
          </button>
          <span className="w-7 text-center font-mono text-[10px] text-text-primary">{instance.period}</span>
          <button
            onClick={() => stepPeriod(1)}
            disabled={instance.period >= PRICE_INDICATOR_PERIOD_MAX}
            className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
          >
            +
          </button>
        </div>
        <div className="flex items-center gap-1">
          <span className="font-mono text-[9px] uppercase tracking-wide text-text-muted">Width</span>
          <button
            onClick={() => stepLineWidth(-1)}
            disabled={instance.lineWidth <= PRICE_INDICATOR_LINE_WIDTH_MIN}
            className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
          >
            &minus;
          </button>
          <span className="w-4 text-center font-mono text-[10px] text-text-primary">{instance.lineWidth}</span>
          <button
            onClick={() => stepLineWidth(1)}
            disabled={instance.lineWidth >= PRICE_INDICATOR_LINE_WIDTH_MAX}
            className="flex h-5 w-5 items-center justify-center rounded border border-base-border font-mono text-[11px] text-text-primary hover:border-signal disabled:opacity-30 disabled:hover:border-base-border"
          >
            +
          </button>
        </div>
      </div>
      <div className="mt-1 flex items-center gap-1.5 pl-[18px]">
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
      </div>
    </div>
  );
}

export function SubWindowMenu({ config, displaySymbol }: { config: SubWindowConfig; displaySymbol: string }) {
  const { updateSubWindow } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<MenuLevel>("root");

  const activeVolumeAvgLines = config.volumeAvg.lines.filter((l) => l.enabled).length;
  const activeSmaCount = config.priceIndicators.filter((p) => p.enabled).length;

  const closeMenu = () => {
    setOpen(false);
    setLevel("root");
  };

  const toggleIndicator = (ind: (typeof AVAILABLE_INDICATORS)[number]) => {
    const has = config.indicators.includes(ind);
    updateSubWindow(config.id, {
      indicators: has ? config.indicators.filter((i) => i !== ind) : [...config.indicators, ind],
    });
  };

  const addSmaInstance = () => {
    updateSubWindow(config.id, {
      priceIndicators: [...config.priceIndicators, createPriceIndicatorInstance("SMA", config.priceIndicators.length)],
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
    <div className="relative">
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
        <span className="shrink-0 font-mono text-[10px] text-text-muted">{config.timeframe}</span>
        {(config.indicators.length > 0 || activeSmaCount > 0) && (
          <span className="truncate font-mono text-[10px] text-text-muted">
            {[...config.indicators, ...config.priceIndicators.filter((p) => p.enabled).map(priceIndicatorLabel)].join(", ")}
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
          className={`absolute right-0 top-full z-20 rounded-b-md border border-base-border bg-base-panel p-2 shadow-xl ${
            level === "volumeAvg" || level === "sma" ? "w-72" : "w-56"
          }`}
        >
          {level === "root" && (
            <div className="flex flex-col">
              <RootRow label="Timeframe" hint={config.timeframe} onClick={() => setLevel("timeframe")} />
              <RootRow
                label="Indicators"
                hint={config.indicators.length ? `${config.indicators.length} active` : "None"}
                onClick={() => setLevel("indicators")}
              />
              <RootRow
                label="SMA"
                hint={activeSmaCount > 0 ? `${activeSmaCount} active` : "None"}
                onClick={() => setLevel("sma")}
              />
              <RootRow
                label="Connector"
                hint={config.connector === "none" ? "None" : String(config.connector)}
                onClick={() => setLevel("connector")}
              />
              <RootRow label="Candles" hint={candleLimitLabel(config.candleLimit)} onClick={() => setLevel("candles")} />
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
            </div>
          )}

          {level === "timeframe" && (
            <div>
              <BackRow label="Timeframe" onClick={() => setLevel("root")} />
              {TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  onClick={() => {
                    updateSubWindow(config.id, { timeframe: tf });
                    setLevel("root");
                  }}
                  className={`flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs ${
                    config.timeframe === tf ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          )}

          {level === "indicators" && (
            <div>
              <BackRow label="Indicators" onClick={() => setLevel("root")} />
              {AVAILABLE_INDICATORS.map((ind) => (
                <button
                  key={ind}
                  onClick={() => toggleIndicator(ind)}
                  className={`flex w-full items-center justify-between rounded px-2 py-1 text-left font-mono text-xs ${
                    config.indicators.includes(ind) ? "bg-signal/20 text-signal" : "text-text-primary hover:bg-base-bg"
                  }`}
                >
                  <span>{ind}</span>
                  {config.indicators.includes(ind) && <span>&#10003;</span>}
                </button>
              ))}
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

          {level === "background" && (
            <div>
              <BackRow label="Background" onClick={() => setLevel("root")} />
              <div className="mb-1 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                Chart background
              </div>
              <ColorField
                value={config.backgroundColor}
                onChange={(hex) => updateSubWindow(config.id, { backgroundColor: hex })}
              />
              <div className="mb-1 mt-2 px-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
                Grid lines
              </div>
              <ColorField value={config.gridColor} onChange={(hex) => updateSubWindow(config.id, { gridColor: hex })} />
              <button
                onClick={() => updateSubWindow(config.id, { backgroundColor: DEFAULT_CHART_BG, gridColor: DEFAULT_GRID_COLOR })}
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
              <ColorField value={config.timer.color} onChange={(hex) => updateSubWindow(config.id, { timer: { ...config.timer, color: hex } })} />
              <button
                onClick={() => updateSubWindow(config.id, { timer: { ...config.timer, color: DEFAULT_TIMER_COLOR } })}
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

          {level === "sma" && (
            <div>
              <BackRow label="SMA" onClick={() => setLevel("root")} />
              <div className="max-h-72 overflow-y-auto pr-0.5">
                {config.priceIndicators.length === 0 && (
                  <div className="px-2 py-1 font-mono text-[11px] text-text-muted">No SMAs added yet.</div>
                )}
                {config.priceIndicators.map((instance) => (
                  <SmaIndicatorRow
                    key={instance.id}
                    subWindowId={config.id}
                    priceIndicators={config.priceIndicators}
                    instance={instance}
                    updateSubWindow={updateSubWindow}
                  />
                ))}
              </div>
              <button
                onClick={addSmaInstance}
                className="mt-1 w-full rounded border border-dashed border-base-border px-2 py-1 text-center font-mono text-[11px] text-text-muted hover:border-signal hover:text-signal"
              >
                + Add SMA
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
