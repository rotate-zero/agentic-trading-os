import { useState } from "react";
import {
  useExecutionAuthorizations,
  type AuthorizationDecisionFilter,
} from "../../hooks/useExecutionAuthorizations";
import type { ExecutionAuthorizationWireShape } from "../../services/api-client";
import { TradeLifecycleDetail } from "./TradeLifecycleDetail";

function utcTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toISOString().replace("T", " ").replace(/(?:\.000)?Z$/, " UTC");
}

function AuthorizationRow({ row, selected, onToggle }: {
  row: ExecutionAuthorizationWireShape;
  selected: boolean;
  onToggle: () => void;
}) {
  const limits = row.limits_snapshot;
  const hasLimits = limits.max_concurrent_positions !== null ||
    limits.fixed_notional_usd !== null || limits.daily_loss_cap_usd !== null;
  return (
    <div className="border-b border-base-border px-2 py-1.5 font-mono text-[10px] last:border-b-0" data-testid="authorization-row">
      <div className="flex flex-wrap items-center justify-between gap-1">
        <span className="text-text-primary">{row.symbol} · {row.strategy_name} {row.strategy_version}</span>
        <span className={row.decision === "rejected" ? "text-bear" : "text-bull"}>{row.decision}</span>
      </div>
      <div className="text-text-muted"><time dateTime={row.created_at}>{utcTime(row.created_at)}</time></div>
      <div className="text-text-muted">
        Requested mode: {row.execution_mode ?? "not recorded"} · Venue: {row.execution_venue ?? "not recorded"}
      </div>
      {row.decision === "rejected" && (
        <div className="break-words text-bear">
          Reasons: {row.reasons?.length ? row.reasons.join(", ") : "none recorded"}
        </div>
      )}
      {row.decision === "approved" && row.reasons !== null && row.reasons.length > 0 && (
        <div className="break-words text-text-muted">Recorded reasons: {row.reasons.join(", ")}</div>
      )}
      {hasLimits && (
        <div className="text-text-muted">
          Limits: positions {limits.max_concurrent_positions ?? "—"} · notional ${limits.fixed_notional_usd ?? "—"} · daily cap ${limits.daily_loss_cap_usd ?? "—"}
        </div>
      )}
      <div className="break-all text-text-muted">Trade ID: {row.trade_id}</div>
      <button
        onClick={onToggle}
        aria-expanded={selected}
        className="mt-0.5 rounded px-1 py-0.5 text-signal hover:bg-base-bg"
      >
        {selected ? "Hide lifecycle" : "View lifecycle"}
      </button>
      {selected && <TradeLifecycleDetail tradeId={row.trade_id} onClose={onToggle} />}
    </div>
  );
}

function AuthorizationHistory() {
  const [filter, setFilter] = useState<AuthorizationDecisionFilter>("all");
  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  const { rows, loading, error, invalidate, refresh } = useExecutionAuthorizations(filter);

  return (
    <div className="border-t border-base-border">
      <div className="flex items-center justify-between gap-1 px-2 py-1">
        <select
          aria-label="Filter recorded authorizations"
          value={filter}
          onChange={(event) => {
            invalidate();
            setSelectedTradeId(null);
            setFilter(event.target.value as AuthorizationDecisionFilter);
          }}
          className="min-w-0 rounded border border-base-border bg-base-bg px-1 py-0.5 font-mono text-[10px] text-text-primary"
        >
          <option value="all">All</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </select>
        <button onClick={refresh} className="rounded px-1 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg">
          Refresh
        </button>
      </div>
      <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted">
        Up to 50 most recent recorded attempts for this filter. Approval is a decision, not a guarantee of an order or fill.
      </p>
      {loading && <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Loading recorded authorizations…</p>}
      {error && (
        <p className="px-2 pb-2 font-mono text-[10px] text-bear">
          Could not fetch recorded authorizations: {error}{rows !== null && " Showing last loaded rows."}
        </p>
      )}
      {!loading && !error && rows?.length === 0 && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">
          No recorded authorizations {filter === "all" ? "yet" : `with decision ${filter}`}.
        </p>
      )}
      {rows !== null && rows.length > 0 && (
        <div className="max-h-96 overflow-y-auto border-t border-base-border">
          {rows.map((row) => (
            <AuthorizationRow
              key={row.trade_id}
              row={row}
              selected={selectedTradeId === row.trade_id}
              onToggle={() => setSelectedTradeId((current) => (current === row.trade_id ? null : row.trade_id))}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function RecordedAuthorizations() {
  const [expanded, setExpanded] = useState(false);
  return (
    <section className="border-b border-base-border" aria-label="Recorded authorizations">
      <button
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between px-2 py-1.5 text-left font-mono text-[11px] font-semibold text-text-primary hover:bg-base-bg"
      >
        <span>Recorded authorizations</span><span className="text-text-muted">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && <AuthorizationHistory />}
    </section>
  );
}
