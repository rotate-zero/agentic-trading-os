import { useEffect, useState } from "react";
import {
  AVAILABLE_INDICATORS,
  CANDLE_LIMIT_DEFAULT,
  CANDLE_LIMIT_MAX,
  CANDLE_LIMIT_MIN,
  CANDLE_LIMIT_STEP,
  DEFAULT_CHART_BG,
  LINK_CONNECTOR_IDS,
  TIMEFRAMES,
  type CandleLimit,
  type SubWindowConfig,
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

type MenuLevel = "root" | "timeframe" | "indicators" | "connector" | "candles" | "background";

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

export function SubWindowMenu({ config, displaySymbol }: { config: SubWindowConfig; displaySymbol: string }) {
  const { updateSubWindow } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<MenuLevel>("root");
  const [hexDraft, setHexDraft] = useState(config.backgroundColor);

  // Keep the text field in sync if the color changes from elsewhere (e.g. the
  // native swatch picker, or loading a saved layout with a different color).
  useEffect(() => setHexDraft(config.backgroundColor), [config.backgroundColor]);

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
        {config.indicators.length > 0 && (
          <span className="truncate font-mono text-[10px] text-text-muted">{config.indicators.join(", ")}</span>
        )}
        <button
          onClick={() => (open ? closeMenu() : setOpen(true))}
          className="ml-auto shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-text-primary"
        >
          &#8942;
        </button>
      </div>

      {open && (
        <div className="absolute right-0 top-full z-20 w-56 rounded-b-md border border-base-border bg-base-panel p-2 shadow-xl">
          {level === "root" && (
            <div className="flex flex-col">
              <RootRow label="Timeframe" hint={config.timeframe} onClick={() => setLevel("timeframe")} />
              <RootRow
                label="Indicators"
                hint={config.indicators.length ? `${config.indicators.length} active` : "None"}
                onClick={() => setLevel("indicators")}
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
              <div className="flex items-center gap-2 px-2 py-1">
                <input
                  type="color"
                  value={config.backgroundColor}
                  onChange={(e) => updateSubWindow(config.id, { backgroundColor: e.target.value })}
                  className="h-7 w-7 shrink-0 cursor-pointer rounded border border-base-border bg-transparent p-0"
                  title="Pick a color"
                />
                <input
                  value={hexDraft}
                  onChange={(e) => {
                    setHexDraft(e.target.value);
                    if (isValidHex(e.target.value)) updateSubWindow(config.id, { backgroundColor: e.target.value });
                  }}
                  placeholder="#131720"
                  className={`w-24 rounded border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary outline-none focus:border-signal ${
                    isValidHex(hexDraft) ? "border-base-border" : "border-bear/60"
                  }`}
                />
              </div>
              <button
                onClick={() => updateSubWindow(config.id, { backgroundColor: DEFAULT_CHART_BG })}
                className="mt-1 w-full rounded px-2 py-1 text-left font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-text-primary"
              >
                Reset to default
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
