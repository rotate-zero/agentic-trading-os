import { useScannerState } from "../../hooks/useScannerState";
import type { ScannerResultWireShape } from "../../services/api-client";

const WIDTH_PX = 260;

// v1 deliberately does NOT wire into WorkspaceContext's resizable-width /
// collapse / session-persistence system the way FeatureEnginePanel and
// InfoTab do (MIN_WIDTH/MAX_WIDTH/COLLAPSED_WIDTH + a stored *_WidthPx
// field + the drag-resize handlers). That's real, separate scope — it
// touches WorkspaceContext's stored layout shape and the
// normalizeSubWindow-style backfill pattern old saved sessions need —
// not something to fold in silently alongside standing up the panel
// itself. Fixed width for now; worth a real decision once this isn't
// sitting on a 6-symbol placeholder universe (scanner-design.md §10).

function scoreColor(rank: number): string {
  if (rank === 0) return "text-signal";
  return "text-text-primary";
}

function formatFeatureChip(key: string, value: number): string {
  if (key === "rvol") return `RVOL ${value.toFixed(2)}x`;
  if (key === "gap_pct") return `Gap ${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
  if (key === "session_pct_change") return `Day ${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
  if (key === "atr_14_pct") return `ATR ${value.toFixed(2)}%`;
  return `${key} ${value}`;
}

// Fixed display order regardless of the dict's own key order, so the
// chip row reads the same for every symbol rather than reshuffling based
// on whatever order Feature Engine happened to compute things in.
const FEATURE_DISPLAY_ORDER = ["rvol", "gap_pct", "session_pct_change", "atr_14_pct"];

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
          <span className={`font-mono text-xs font-semibold ${scoreColor(rank)}`}>{result.score.toFixed(2)}</span>
        </div>
      </div>
      {orderedFeatures.length > 0 && (
        <div className="flex flex-wrap gap-x-2 pl-6 font-mono text-[10px] text-text-muted">
          {orderedFeatures.map((key) => (
            <span key={key}>{formatFeatureChip(key, result.features[key])}</span>
          ))}
        </div>
      )}
    </div>
  );
}

export function ScannerPanel() {
  const { results, skipped, loading, error, lastUpdated, refresh } = useScannerState();

  return (
    <div
      className="flex h-full flex-col border-l border-base-border bg-base-panel"
      style={{ width: WIDTH_PX }}
    >
      <div className="flex shrink-0 items-center justify-between border-b border-base-border px-2 py-1.5">
        <div className="flex flex-col">
          <span className="text-[11px] uppercase tracking-wide text-text-muted">Scanner</span>
          <span className="font-mono text-[9px] text-text-muted">v1 — test universe, not Core-100</span>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary disabled:opacity-50"
        >
          {loading ? "…" : "Refresh"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {error && (
          <div className="px-2 py-3 font-mono text-[11px] text-bear">Failed to load: {error}</div>
        )}
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
      {lastUpdated && (
        <div className="shrink-0 border-t border-base-border px-2 py-1 text-right font-mono text-[9px] text-text-muted">
          Updated {lastUpdated.toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}
