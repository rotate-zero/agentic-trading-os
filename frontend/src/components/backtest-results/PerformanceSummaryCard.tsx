import type { BacktestSelectionSummaryWireShape } from "../../services/api-client";

export function PerformanceSummaryCard({
  summary, loading, error,
}: {
  summary: BacktestSelectionSummaryWireShape | null;
  loading: boolean;
  error: string | null;
}) {
  return (
    <section className="rounded border border-base-border bg-base-bg/40 p-2 font-mono text-[10px]">
      <h3 className="mb-1 text-[11px] font-semibold text-text-primary">Performance summary</h3>
      <p className="mb-2 text-text-muted">All recorded outcomes for this selection.</p>
      {loading && <p role="status" className="text-text-muted">Loading performance summary…</p>}
      {error && <p role="alert" className="text-bear">Failed to load performance summary: {error}</p>}
      {!loading && !error && summary && !summary.selection_found && (
        <p className="text-text-muted">No recorded run or sweep found for this selection.</p>
      )}
      {!loading && !error && summary?.selection_found && (
        <div className="flex flex-col gap-2">
          {summary.groups.map((group) => (
            <div
              key={JSON.stringify([group.strategy_name, group.strategy_version, group.config_hash, group.data_version, group.feature_version])}
              className="rounded border border-base-border p-1.5"
            >
              <div className="font-semibold text-text-primary">{group.strategy_name} · {group.strategy_version}</div>
              <div className="break-all text-text-muted">Config: {group.config_hash}</div>
              <div className="break-all text-text-muted">Data: {group.data_version} · Features: {group.feature_version}</div>
              <div className="mt-1 text-text-primary">{group.run_count} run(s) · {group.total_outcomes} outcome(s)</div>
              {group.total_outcomes === 0 ? (
                <p className="text-text-muted">No outcomes recorded for this group. Win rate and mean R are unavailable.</p>
              ) : (
                <div className="text-text-muted">
                  <div>Wins: {group.wins} · Losses: {group.losses} · Breakevens: {group.breakevens}</div>
                  <div>Win rate: {group.win_rate === null ? "—" : `${(group.win_rate * 100).toFixed(2)}%`} · Mean realized R: {group.mean_realized_r === null ? "—" : String(group.mean_realized_r)}</div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <p className="mt-2 text-text-muted">The outcome list and CSV contain only loaded rows; this summary covers the complete selected population.</p>
    </section>
  );
}
