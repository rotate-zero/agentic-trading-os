import { useMemo, useRef, useState } from "react";
import { useWorkspace } from "../../state/WorkspaceContext";
import { useIntelligenceState, type FeatureTimeframe, type FeatureUnit, type FeatureUnitEntry } from "../../hooks/useIntelligenceState";
import type { FeatureSlopeWireShape } from "../../services/api-client";
import { useDropdownPlacement } from "../../hooks/useDropdownPlacement";
import { MOCK_TICKERS } from "../../mocks/tickers";

const MIN_WIDTH = 64;
const MAX_WIDTH = 480;
const COLLAPSED_WIDTH = 36;

// Confirmed decision #49: shows BOTH clock time and candle count, not one
// or the other. They serve different questions — clock time answers "how
// long has this actually been developing" and stays meaningful even
// compared across different timeframes; candle count answers "how does
// this look on my chart" and is what a technical trader typically counts
// by eye ("held for 4 bars"). Both are cheap to show together, so there
// was no real reason to force a choice between them.
function parseTimeframeSeconds(timeframe: string): number {
  const match = /^(\d+)([mhd])$/.exec(timeframe);
  if (!match) return 60; // Feature Engine is 1m-only today anyway (decisions #45/#46) — just a safe fallback
  const [, numStr, unit] = match;
  const num = Number(numStr);
  if (unit === "m") return num * 60;
  if (unit === "h") return num * 3600;
  return num * 86400; // "d"
}

function formatZoneDuration(totalSeconds: number, timeframeSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const clock = totalSeconds < 60 ? `${totalSeconds}s` : seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`;
  const candles = Math.floor(totalSeconds / timeframeSeconds);
  return `${clock} (${candles} candle${candles === 1 ? "" : "s"})`;
}

function zoneColor(zone: string): string {
  if (zone === "above") return "text-bull";
  if (zone === "below") return "text-bear";
  return "text-signal"; // inside_aura — actively touching, the notable state
}

// Symbol search — same TickerSearch pattern as
// components/sub-window/SubWindowMenu.tsx (query state, MOCK_TICKERS
// suggestions, Enter/click to pick), adapted for setFeatureEnginePanelSymbol
// instead of setSubWindowSymbol. Deliberately its own independent symbol,
// not tied to any connector — this panel analyzes whatever's typed into
// it, per the original "search field selects a symbol for analysis" design.
function SymbolSearch({ current }: { current: string }) {
  const { setFeatureEnginePanelSymbol } = useWorkspace();
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
    setFeatureEnginePanelSymbol(symbol.toUpperCase());
    setQuery("");
    setFocused(false);
  };

  return (
    <div className="relative shrink-0 border-b border-base-border p-2" ref={anchorRef}>
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => setFocused(true)}
        onBlur={() => setTimeout(() => setFocused(false), 150)} // let a suggestion's onMouseDown fire first
        onKeyDown={(e) => {
          if (e.key === "Enter" && query.trim()) pick(query.trim());
        }}
        placeholder={current}
        className="w-full rounded border border-base-border bg-base-bg px-2 py-1 font-mono text-xs text-text-primary outline-none focus:border-signal"
      />
      {focused && (
        <div
          className={`absolute left-2 right-2 z-30 overflow-y-auto rounded border border-base-border bg-base-panel shadow-xl ${
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

function VariableRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-text-muted">{label}</span>
      <span className="font-mono text-text-primary">{value}</span>
    </div>
  );
}

// SMA/EMA slope-family sub-values (confirmed decision #85 — closes the
// gap flagged in decision #83's own write-up, where sma_9_slope/_r2/
// _slope_pct/_slope_angle each rendered as their OWN standalone
// accordion row, literally labeled e.g. "sma_9_slope_angle", instead of
// nesting under the SMA-9 entry the way sma_9's own value does).
//
// `slope_pct`/`slope_angle` are the two the backend omits when the
// current SMA/EMA value is exactly 0 (indicators/sma.py::sma_slope()'s
// own docstring) — `slope`/`r2` are shown unconditionally since the
// backend always publishes both. Labeled "slope"/"angle"/"fit quality"
// per the report's own suggested wording, not the raw key names — a
// reader of this panel shouldn't need to already know what "r2" means.
function formatSigned(value: number, decimals: number, suffix: string): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(decimals)}${suffix}`;
}

function SlopeBlock({ slope }: { slope: FeatureSlopeWireShape }) {
  return (
    <div className="flex flex-col gap-0.5 border-t border-base-border/40 pt-1">
      {slope.slope_pct !== undefined ? (
        <VariableRow label="slope" value={formatSigned(slope.slope_pct, 4, "%/bar")} />
      ) : (
        // slope_pct omitted only when the current SMA/EMA value is
        // exactly 0 — falls back to the raw (unnormalized) $/bar
        // coefficient rather than showing nothing.
        <VariableRow label="slope" value={formatSigned(slope.slope, 6, "/bar")} />
      )}
      {slope.slope_angle !== undefined && <VariableRow label="angle" value={formatSigned(slope.slope_angle, 1, "°")} />}
      <VariableRow label="fit quality" value={slope.r2.toFixed(3)} />
    </div>
  );
}

function UnitValueBlock({ entry, timeframeSeconds }: { entry: FeatureUnitEntry; timeframeSeconds: number }) {
  const li = entry.levelInteraction;
  return (
    <div className="rounded border border-base-border/60 bg-base-bg px-2 py-1.5 text-[11px]">
      <div className="mb-1 flex items-center justify-between">
        {entry.period !== null && <span className="font-mono text-xs font-semibold text-text-primary">{entry.period}</span>}
        <span className="font-mono text-xs text-text-primary">{entry.value.toFixed(4)}</span>
      </div>
      {entry.slope && <SlopeBlock slope={entry.slope} />}
      {li ? (
        <div className="flex flex-col gap-0.5 border-t border-base-border/40 pt-1">
          <VariableRow label="zone" value={li.zone} />
          <VariableRow label="touches today" value={String(li.touch_count_today)} />
          <VariableRow
            label={li.holding ? "holding for" : "in zone for"}
            value={formatZoneDuration(li.seconds_in_zone, timeframeSeconds)}
          />
          {li.distance_pct !== null && (
            <VariableRow label="distance" value={`${li.distance_pct >= 0 ? "+" : ""}${li.distance_pct.toFixed(3)}%`} />
          )}
          {li.holding && <VariableRow label="entered from" value={li.holding.entered_from ?? "unknown"} />}
        </div>
      ) : (
        <div className="text-text-muted">no Level Interaction state yet</div>
      )}
      {li && (
        <div className={`mt-1 border-t border-base-border/40 pt-1 text-center font-mono text-[10px] font-semibold ${zoneColor(li.zone)}`}>
          {li.zone.toUpperCase()}
        </div>
      )}
    </div>
  );
}

// Horizontally collapsible per-unit accordion (SMA, EMA, PDH, PDL, ...) —
// collapse state is local, ephemeral UI state, not persisted: which
// sections happen to be open isn't something worth surviving a reload,
// unlike the panel's own collapsed/width (WorkspaceContext).
function FeatureUnitAccordion({ unit, timeframeSeconds }: { unit: FeatureUnit; timeframeSeconds: number }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded border border-base-border">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-2 py-1 font-mono text-xs font-semibold uppercase tracking-wide text-text-primary hover:bg-base-bg"
      >
        <span>{unit.key}</span>
        <span className="text-text-muted">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 border-t border-base-border p-1.5">
          {unit.entries.map((entry) => (
            <UnitValueBlock key={entry.period ?? unit.key} entry={entry} timeframeSeconds={timeframeSeconds} />
          ))}
        </div>
      )}
    </div>
  );
}

function TimeframeSection({ tf }: { tf: FeatureTimeframe }) {
  const timeframeSeconds = parseTimeframeSeconds(tf.timeframe);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[11px] uppercase tracking-wide text-text-muted">{tf.timeframe}</span>
        <span className="font-mono text-[11px] text-text-muted">close {tf.close.toFixed(4)}</span>
      </div>
      {tf.units.length === 0 ? (
        <div className="rounded border border-dashed border-base-border p-2 text-center text-[11px] text-text-muted">
          No features computed on this timeframe yet.
        </div>
      ) : (
        tf.units.map((unit) => <FeatureUnitAccordion key={unit.key} unit={unit} timeframeSeconds={timeframeSeconds} />)
      )}
    </div>
  );
}

function FeatureEnginePanelContent({ symbol }: { symbol: string }) {
  const { timeframes, loading } = useIntelligenceState(symbol);

  if (loading && timeframes.length === 0) {
    return <div className="p-3 text-center text-[11px] text-text-muted">Loading {symbol}…</div>;
  }

  if (timeframes.length === 0) {
    return (
      <div className="flex flex-col gap-2 p-3 text-center text-[11px] text-text-muted">
        <p>No Feature Engine data yet for {symbol}.</p>
        <p>Waiting for enough 1m candles to compute the first value (SMA needs 9+).</p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-2">
      {timeframes.map((tf) => (
        <TimeframeSection key={tf.timeframe} tf={tf} />
      ))}
    </div>
  );
}

export function FeatureEnginePanel() {
  const { featureEngineCollapsed, featureEngineWidthPx, setFeatureEngineCollapsed, setFeatureEngineWidthPx, featureEnginePanelSymbol } =
    useWorkspace();
  const dragStartRef = useRef<{ x: number; width: number } | null>(null);

  const onResizeDown = (e: React.PointerEvent) => {
    e.preventDefault();
    dragStartRef.current = { x: e.clientX, width: featureEngineWidthPx };
    const onMove = (ev: PointerEvent) => {
      if (!dragStartRef.current) return;
      const delta = dragStartRef.current.x - ev.clientX; // panel is on the right, dragging left grows it
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, dragStartRef.current.width + delta));
      setFeatureEngineWidthPx(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const width = featureEngineCollapsed ? COLLAPSED_WIDTH : featureEngineWidthPx;
  // Memoized only so a re-render from an unrelated context field doesn't
  // spuriously remount SymbolSearch's local query/focus state.
  const symbol = useMemo(() => featureEnginePanelSymbol, [featureEnginePanelSymbol]);

  return (
    <div className="relative flex shrink-0 border-l border-base-border bg-base-panel" style={{ width }}>
      {!featureEngineCollapsed && (
        <div
          onPointerDown={onResizeDown}
          // Widened from InfoTab's 6px (confirmed decision #49) — the
          // underlying drag logic here is byte-identical to InfoTab's own
          // working resize handle, so the most likely real cause of "can't
          // grab it" is simply that 6px is a thin target to land a cursor
          // on precisely, made worse by being the SECOND such handle
          // sitting right next to the first (InfoTab's). z-20, not z-10,
          // so there's no ambiguity about which of the two adjacent
          // handles wins in the shared boundary area. Also visible at rest
          // now, not just on hover, so it's actually discoverable.
          className="absolute left-0 top-0 z-20 h-full w-[10px] -translate-x-1/2 cursor-col-resize bg-base-border/40 hover:bg-signal/40"
        />
      )}

      <div className="flex h-full min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1 border-b border-base-border px-2 py-1">
          <button
            onClick={() => setFeatureEngineCollapsed(!featureEngineCollapsed)}
            className="rounded px-1 py-0.5 font-mono text-xs text-text-muted hover:bg-base-bg hover:text-text-primary"
            title={featureEngineCollapsed ? "Expand Feature Engine panel" : "Collapse Feature Engine panel"}
          >
            {featureEngineCollapsed ? "«" : "»"}
          </button>
          {!featureEngineCollapsed && <span className="font-mono text-xs font-semibold text-text-primary">Feature Engine</span>}
        </div>

        {!featureEngineCollapsed && (
          <>
            <SymbolSearch current={symbol} />
            <div className="min-h-0 flex-1">
              <FeatureEnginePanelContent symbol={symbol} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
