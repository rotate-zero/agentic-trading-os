import { useRef, useState } from "react";
import { useWorkspace } from "../../state/WorkspaceContext";
import { useScannerState } from "../../hooks/useScannerState";
import { useScannerUniverse } from "../../hooks/useScannerUniverse";
import type { ScannerResultWireShape } from "../../services/api-client";

const MIN_WIDTH = 64;
const MAX_WIDTH = 480;
const COLLAPSED_WIDTH = 36;

// v1 score = rvol (regular session) or premarket_volume_ratio
// (pre-market) only — the two share one "activity" slot, mutually
// exclusive by session (Saqib's call, 2026-08-27; premarket_volume_ratio
// wired in 2026-08-28). gap/session-change terms exist in scorer.py and
// still run, but their weights are 0.0 in Settings, so they don't
// currently move the ranking; premarket_volume_ratio's own weight is
// ALSO 0.0 for now — wired in and tested, but inert until Saqib has
// actually looked at real pre-market values (missed 2026-08-28's
// session before this landed; next chance is Monday). The chip row
// below still shows every available reading regardless of weight —
// informative context even for the currently-inert ones. Flip weights
// back on server-side to bring any of them into the ranking; nothing
// here would need to change to reflect that.
const SCORE_BASIS_LABEL = "Ranked by RVOL / PM Vol (v1)";

function formatFeatureChip(key: string, value: number): { label: string; primary: boolean } {
  if (key === "rvol") return { label: `RVOL ${value.toFixed(2)}x`, primary: true };
  if (key === "premarket_volume_ratio") return { label: `PM Vol ${value.toFixed(2)}x`, primary: true };
  if (key === "gap_pct") return { label: `Gap ${value >= 0 ? "+" : ""}${value.toFixed(2)}%`, primary: false };
  if (key === "session_pct_change") return { label: `Day ${value >= 0 ? "+" : ""}${value.toFixed(2)}%`, primary: false };
  if (key === "atr_14_pct") return { label: `ATR ${value.toFixed(2)}%`, primary: false };
  return { label: `${key} ${value}`, primary: false };
}

const FEATURE_DISPLAY_ORDER = ["rvol", "premarket_volume_ratio", "gap_pct", "session_pct_change", "atr_14_pct"];

function ResultRow({ result, rank }: { result: ScannerResultWireShape; rank: number }) {
  const lowConfidence = result.inputs_available < 2;
  const orderedFeatures = FEATURE_DISPLAY_ORDER.filter((k) => k in result.features);

  return (
    <div className="flex flex-col gap-1 border-b border-base-border px-2 py-1.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-4 font-mono text-[10px] text-text-muted">{rank + 1}</span>
          <span className="font-mono text-xs font-medium text-text-primary">{result.symbol}</span>
        </div>
        <div className="flex items-center gap-1.5">
          {lowConfidence && (
            <span
              title={`Only ${result.inputs_available}/3 inputs available — thin reading, not a confident score`}
              className="rounded bg-base-bg px-1 font-mono text-[9px] text-text-muted"
            >
              {result.inputs_available}/3
            </span>
          )}
          <span className={`font-mono text-xs font-semibold ${rank === 0 ? "text-signal" : "text-text-primary"}`}>
            {result.score.toFixed(2)}
          </span>
        </div>
      </div>
      {orderedFeatures.length > 0 && (
        <div className="flex flex-wrap gap-x-2 pl-6 font-mono text-[10px]">
          {orderedFeatures.map((key) => {
            const chip = formatFeatureChip(key, result.features[key]);
            return (
              <span key={key} className={chip.primary ? "font-semibold text-text-primary" : "text-text-muted"}>
                {chip.label}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ResultsTab() {
  const { results, skipped, loading, error, lastUpdated, refresh } = useScannerState();

  return (
    <>
      <div className="flex-1 overflow-y-auto">
        {error && <div className="px-2 py-3 font-mono text-[11px] text-bear">Failed to load: {error}</div>}
        {!error && results.length === 0 && !loading && (
          <div className="px-2 py-3 font-mono text-[11px] text-text-muted">
            No symbols scored yet — waiting on at least one recorded 1m candle.
          </div>
        )}
        {results.map((r, i) => (
          <ResultRow key={r.symbol} result={r} rank={i} />
        ))}
      </div>
      {skipped.length > 0 && (
        <div className="shrink-0 border-t border-base-border px-2 py-1 font-mono text-[9px] text-text-muted">
          No data yet: {skipped.join(", ")}
        </div>
      )}
      <div className="flex shrink-0 items-center justify-between border-t border-base-border px-2 py-1">
        <button
          onClick={refresh}
          disabled={loading}
          className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary disabled:opacity-50"
        >
          {loading ? "…" : "Refresh"}
        </button>
        {lastUpdated && <span className="font-mono text-[9px] text-text-muted">Updated {lastUpdated.toLocaleTimeString()}</span>}
      </div>
    </>
  );
}

function UniverseTab() {
  const { symbols, loading, error, pendingAdd, addSymbol, removeSymbol } = useScannerUniverse();
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const handleAdd = async () => {
    const symbol = input.trim();
    if (!symbol) return;
    const ok = await addSymbol(symbol);
    if (ok) {
      setInput("");
      inputRef.current?.focus();
    }
  };

  return (
    <>
      <div className="flex-1 overflow-y-auto">
        {symbols.length === 0 && !loading && (
          <div className="px-2 py-3 font-mono text-[11px] text-text-muted">Universe is empty — add a symbol below.</div>
        )}
        {symbols.map((s) => (
          <div key={s.symbol} className="flex items-center justify-between border-b border-base-border px-2 py-1.5">
            <span className="font-mono text-xs text-text-primary">{s.symbol}</span>
            <button
              onClick={() => removeSymbol(s.symbol)}
              title={`Remove ${s.symbol} from the universe`}
              className="rounded px-1 font-mono text-[11px] text-text-muted hover:bg-base-bg hover:text-bear"
            >
              ×
            </button>
          </div>
        ))}
      </div>

      <div className="shrink-0 border-t border-base-border p-2">
        {error && <div className="mb-1 font-mono text-[10px] text-bear">{error}</div>}
        <div className="flex gap-1">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            placeholder="e.g. NVDA"
            maxLength={6}
            className="min-w-0 flex-1 rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary placeholder:text-text-muted focus:border-signal focus:outline-none"
          />
          <button
            onClick={handleAdd}
            disabled={pendingAdd || !input.trim()}
            className="rounded border border-base-border px-2 py-1 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary disabled:opacity-50"
          >
            Add
          </button>
        </div>
      </div>
    </>
  );
}

export function ScannerPanel() {
  const { scannerCollapsed, scannerWidthPx, setScannerCollapsed, setScannerWidthPx } = useWorkspace();
  const [tab, setTab] = useState<"results" | "universe">("results");
  const dragStartRef = useRef<{ x: number; width: number } | null>(null);

  const onResizeDown = (e: React.PointerEvent) => {
    e.preventDefault();
    dragStartRef.current = { x: e.clientX, width: scannerWidthPx };
    const onMove = (ev: PointerEvent) => {
      if (!dragStartRef.current) return;
      const delta = dragStartRef.current.x - ev.clientX; // panel is on the right, dragging left grows it
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, dragStartRef.current.width + delta));
      setScannerWidthPx(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const width = scannerCollapsed ? COLLAPSED_WIDTH : scannerWidthPx;

  return (
    <div className="relative flex shrink-0 border-l border-base-border bg-base-panel" style={{ width }}>
      {!scannerCollapsed && (
        <div
          onPointerDown={onResizeDown}
          // Same widened (10px), always-visible handle as FeatureEnginePanel's
          // own resizer — see that component for why 10px over InfoTab's
          // original 6px (decision #49).
          className="absolute left-0 top-0 z-20 h-full w-[10px] -translate-x-1/2 cursor-col-resize bg-base-border/40 hover:bg-signal/40"
        />
      )}

      <div className="flex h-full min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1 border-b border-base-border px-2 py-1">
          <button
            onClick={() => setScannerCollapsed(!scannerCollapsed)}
            className="rounded px-1 py-0.5 font-mono text-xs text-text-muted hover:bg-base-bg hover:text-text-primary"
            title={scannerCollapsed ? "Expand Scanner panel" : "Collapse Scanner panel"}
          >
            {scannerCollapsed ? "«" : "»"}
          </button>
          {!scannerCollapsed && <span className="font-mono text-xs font-semibold text-text-primary">Scanner</span>}
        </div>

        {!scannerCollapsed && (
          <>
            <div className="flex shrink-0 items-center justify-between border-b border-base-border px-2 py-1">
              <div className="flex gap-1">
                <button
                  onClick={() => setTab("results")}
                  className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                    tab === "results" ? "bg-base-bg text-text-primary" : "text-text-muted hover:text-text-primary"
                  }`}
                >
                  Results
                </button>
                <button
                  onClick={() => setTab("universe")}
                  className={`rounded px-1.5 py-0.5 font-mono text-[10px] ${
                    tab === "universe" ? "bg-base-bg text-text-primary" : "text-text-muted hover:text-text-primary"
                  }`}
                >
                  Universe
                </button>
              </div>
              {tab === "results" && <span className="font-mono text-[9px] text-text-muted">{SCORE_BASIS_LABEL}</span>}
            </div>

            {tab === "results" ? <ResultsTab /> : <UniverseTab />}
          </>
        )}
      </div>
    </div>
  );
}
