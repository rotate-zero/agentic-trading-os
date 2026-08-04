import { useMemo, useRef, useState } from "react";
import { LINK_CONNECTOR_IDS, type InfoConnectorMode } from "../../types/workspace";
import { MOCK_TICKERS } from "../../mocks/tickers";
import { generateMockOpportunities } from "../../mocks/opportunities";
import { useLatestPrices } from "../../hooks/useLatestPrices";
import { useLiveCandles } from "../../hooks/useLiveCandles";
import { AIAnalysisPanel } from "../ai-panel/AIAnalysisPanel";
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

const MIN_WIDTH = 64;
const MAX_WIDTH = 480;
const COLLAPSED_WIDTH = 36;

function GeneralContent() {
  const symbols = useMemo(() => MOCK_TICKERS.map((t) => t.symbol), []);
  const prices = useLatestPrices(symbols);

  const rows = MOCK_TICKERS.map((t) => {
    const last = prices[t.symbol] ?? t.basePrice;
    const changePct = ((last - t.basePrice) / t.basePrice) * 100;
    return { ...t, last, changePct };
  });

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      <div className="text-[11px] uppercase tracking-wide text-text-muted">Market Overview</div>
      <div className="flex flex-col gap-1">
        {rows.map((r) => (
          <div
            key={r.symbol}
            className="flex items-center justify-between rounded border border-base-border px-2 py-1.5"
          >
            <div>
              <div className="font-mono text-xs font-medium text-text-primary">{r.symbol}</div>
              <div className="text-[10px] text-text-muted">{r.name}</div>
            </div>
            <div className="text-right">
              <div className="font-mono text-xs text-text-primary">{r.last.toFixed(2)}</div>
              <div className={`font-mono text-[10px] ${r.changePct >= 0 ? "text-bull" : "text-bear"}`}>
                {r.changePct >= 0 ? "+" : ""}
                {r.changePct.toFixed(2)}%
              </div>
            </div>
          </div>
        ))}
      </div>
      <div className="text-[11px] uppercase tracking-wide text-text-muted">Notes</div>
      <p className="text-xs leading-relaxed text-text-muted">
        General mode isn't tied to any single connector — it's the scrollable, market-wide view. Select a
        connector above to see AI opportunity data for whatever symbol that link group currently holds.
      </p>
    </div>
  );
}

function ConnectorContent({ symbol }: { symbol: string }) {
  const candles = useLiveCandles(symbol);
  const opportunities = useMemo(() => generateMockOpportunities(symbol, candles), [symbol, candles]);
  return <AIAnalysisPanel symbol={symbol} opportunities={opportunities} />;
}

export function InfoTab() {
  const { infoCollapsed, infoWidthPx, setInfoCollapsed, setInfoWidthPx, connectorSymbols } = useWorkspace();
  const [mode, setMode] = useState<InfoConnectorMode>("general");
  const dragStartRef = useRef<{ x: number; width: number } | null>(null);

  const onResizeDown = (e: React.PointerEvent) => {
    e.preventDefault();
    dragStartRef.current = { x: e.clientX, width: infoWidthPx };
    const onMove = (ev: PointerEvent) => {
      if (!dragStartRef.current) return;
      const delta = dragStartRef.current.x - ev.clientX; // panel is on the right, dragging left grows it
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, dragStartRef.current.width + delta));
      setInfoWidthPx(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const width = infoCollapsed ? COLLAPSED_WIDTH : infoWidthPx;

  return (
    <div className="relative flex shrink-0 border-l border-base-border bg-base-panel" style={{ width }}>
      {!infoCollapsed && (
        <div
          onPointerDown={onResizeDown}
          className="absolute left-0 top-0 z-10 h-full w-[6px] -translate-x-1/2 cursor-col-resize hover:bg-signal/30"
        />
      )}

      <div className="flex h-full min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1 border-b border-base-border px-2 py-1">
          <button
            onClick={() => setInfoCollapsed(!infoCollapsed)}
            className="rounded px-1 py-0.5 font-mono text-xs text-text-muted hover:bg-base-bg hover:text-text-primary"
            title={infoCollapsed ? "Expand info tab" : "Collapse info tab"}
          >
            {infoCollapsed ? "«" : "»"}
          </button>
          {!infoCollapsed && <span className="font-mono text-xs font-semibold text-text-primary">Info</span>}
        </div>

        {!infoCollapsed && (
          <>
            <div className="flex flex-wrap gap-1 border-b border-base-border p-2">
              <button
                onClick={() => setMode("general")}
                className={`rounded px-2 py-0.5 font-mono text-[11px] ${
                  mode === "general"
                    ? "bg-base-bg text-text-primary ring-1 ring-text-muted"
                    : "text-text-muted hover:bg-base-bg"
                }`}
              >
                General
              </button>
              {LINK_CONNECTOR_IDS.map((id) => (
                <button
                  key={id}
                  onClick={() => setMode(id)}
                  className="flex h-5 w-5 items-center justify-center rounded font-mono text-[11px]"
                  style={{
                    backgroundColor: mode === id ? CONNECTOR_COLORS[id] : "transparent",
                    color: mode === id ? "#0B0E14" : CONNECTOR_COLORS[id],
                    border: `1px solid ${CONNECTOR_COLORS[id]}`,
                  }}
                >
                  {id}
                </button>
              ))}
            </div>
            <div className="min-h-0 flex-1">
              {mode === "general" ? <GeneralContent /> : <ConnectorContent symbol={connectorSymbols[mode]} />}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
