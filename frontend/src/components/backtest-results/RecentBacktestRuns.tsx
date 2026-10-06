import { useState } from "react";
import { RECENT_RUNS_LIMIT, useRecentBacktestRuns } from "../../hooks/useRecentBacktestRuns";
import type { BacktestRunWireShape } from "../../services/api-client";

// Same full date+time convention BacktestResultsPanel.tsx's own
// formatDateTime uses (kept local rather than exported from that file to
// avoid a circular import; the two are one line each).
function formatCreatedAt(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function RecentRunRow({
  run,
  selected,
  onViewResults,
}: {
  run: BacktestRunWireShape;
  selected: boolean;
  onViewResults: (runId: string) => void;
}) {
  const symbols = run.symbol_universe.join(", ") || "—";
  return (
    <div
      className={`flex items-start justify-between gap-2 rounded border px-2 py-1.5 ${
        selected ? "border-signal/60 bg-signal/10" : "border-base-border"
      }`}
      data-testid="recent-run-row"
    >
      <div className="min-w-0 flex-1 font-mono">
        <div className="truncate text-[11px] font-medium text-text-primary" title={`${run.strategy_name} ${symbols}`}>
          {run.strategy_name} <span className="text-text-muted">{symbols}</span>
        </div>
        <div className="truncate text-[9px] text-text-muted">created {formatCreatedAt(run.created_at)}</div>
        <div className="truncate text-[9px] text-text-muted" title={`${run.date_range_start} → ${run.date_range_end}`}>
          replay {run.date_range_start} → {run.date_range_end}
        </div>
        {/* data_version is the run's own provenance string, shown verbatim
            (e.g. a fixture scenario, IBKR or stored-candle label). It is
            never re-labelled, so fixture data is never presented as real
            market data. */}
        <div className="truncate text-[9px] text-text-muted" title={run.data_version}>
          data_version {run.data_version}
        </div>
      </div>
      <button
        type="button"
        onClick={() => onViewResults(run.run_id)}
        title={`Load run_id ${run.run_id} into the results view`}
        className="shrink-0 rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary"
      >
        {selected ? "Viewing" : "View results"}
      </button>
    </div>
  );
}

// The list itself lives in its own component so the hook is mounted only
// while the section is expanded: collapsing unmounts it (invalidating any
// in-flight request) and expanding loads a fresh list. Nothing is fetched
// while the section is collapsed.
function RecentRunsList({
  refreshKey,
  selectedRunId,
  onViewResults,
}: {
  refreshKey: string;
  selectedRunId: string | undefined;
  onViewResults: (runId: string) => void;
}) {
  const { runs, loading, error, refetch } = useRecentBacktestRuns({ refreshKey });

  return (
    <div className="flex flex-col gap-1.5 border-t border-base-border p-2">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[9px] leading-snug text-text-muted">
          Showing up to {RECENT_RUNS_LIMIT} recent runs, newest first as returned by the server — not the complete
          history.
        </span>
        {/* Never disabled while loading: a hung request must stay
            recoverable. Each press starts a newer request that supersedes
            every older one. */}
        <button
          type="button"
          onClick={refetch}
          title={loading ? "Request in flight — press to start a fresh one" : "Reload recent runs"}
          className="shrink-0 rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary"
        >
          Refresh runs
        </button>
      </div>

      {error && (
        <div className="rounded border border-bear/40 px-2 py-1.5 font-mono text-[10px] text-bear">
          Failed to load recent runs: {error}
        </div>
      )}
      {!error && loading && runs.length === 0 && (
        <p className="font-mono text-[10px] text-text-muted">Loading recent runs…</p>
      )}
      {!error && !loading && runs.length === 0 && (
        <p className="font-mono text-[10px] text-text-muted">No saved backtest runs found.</p>
      )}
      {runs.length > 0 && (
        <>
          {loading && <p className="font-mono text-[9px] text-text-muted">refreshing…</p>}
          <div className="flex max-h-56 flex-col gap-1 overflow-y-auto">
            {runs.map((run) => (
              <RecentRunRow
                key={run.run_id}
                run={run}
                selected={run.run_id === selectedRunId}
                onViewResults={onViewResults}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Collapsible "Recent runs" browser (task `backtest-run-history`). Lets a
 * person reopen a saved fixture, IBKR or stored-candle backtest after a
 * browser refresh without having kept its run_id. It only LISTS run
 * settings (GET /intelligence/backtest-runs) and reports a choice through
 * `onViewResults`; applying the choice (run_id mode, manual selection) is
 * the caller's job, and nothing here touches shared workspace state.
 * Starts collapsed, like every sibling section in this panel.
 */
export function RecentBacktestRuns({
  refreshKey,
  selectedRunId,
  onViewResults,
}: {
  refreshKey: string;
  selectedRunId: string | undefined;
  onViewResults: (runId: string) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="shrink-0 border-b border-base-border" data-testid="recent-runs-section">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 px-2 py-1 text-left font-mono text-[10px] uppercase tracking-wide text-text-muted hover:text-text-primary"
      >
        <span>{open ? "▾" : "▸"}</span>
        <span>Recent runs</span>
      </button>
      {open && <RecentRunsList refreshKey={refreshKey} selectedRunId={selectedRunId} onViewResults={onViewResults} />}
    </div>
  );
}
