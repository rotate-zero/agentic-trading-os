import type { ReactNode } from "react";
import { useStrategyOutcomeDetail } from "../../hooks/useStrategyOutcomeDetail";
import type { StrategyOutcomeWireShape } from "../../services/api-client";

type Outcome = StrategyOutcomeWireShape;
type SnapshotField = "market_state_at_entry" | "context_at_entry" | "market_state_at_exit" | "context_at_exit";

const MAX_DEPTH = 8;

// Every instant the backend sends is UTC. An unparseable value is shown verbatim, never hidden.
function utc(value: string | null): string {
  if (value === null) return "not recorded";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toISOString().replace("T", " ").replace(/(?:\.000)?Z$/, " UTC");
}

// Recorded numbers are shown exactly as received — no rounding, and null is never shown as 0.
function num(value: number | null, absent: string): string {
  return value === null ? absent : String(value);
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
}

/**
 * Structured, TEXT-ONLY rendering of nested recorded JSON. Every string is
 * emitted as a React text node (JSON-quoted so `"10"` and `10`, or `"true"` and
 * `true`, stay distinguishable and markup like `<b>x</b>` shows literally); no
 * value ever reaches `dangerouslySetInnerHTML`, `href` or `style`. null, empty
 * objects/lists and empty strings are named explicitly instead of vanishing.
 * Past MAX_DEPTH the remaining subtree is shown as one JSON text line.
 */
function Value({ value, depth }: { value: unknown; depth: number }): ReactNode {
  if (value === null) return <span className="italic text-text-muted">null</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-text-muted">[ ] (empty list)</span>;
    if (depth >= MAX_DEPTH) return <span className="break-all">{safeJson(value)}</span>;
    return (
      <ul className="ml-2 border-l border-base-border pl-2">
        {value.map((item, index) => (
          <li key={index} className="break-words">
            <span className="text-text-muted">[{index}]</span> <Value value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span className="text-text-muted">{"{ }"} (empty object)</span>;
    if (depth >= MAX_DEPTH) return <span className="break-all">{safeJson(value)}</span>;
    return (
      <ul className="ml-2 border-l border-base-border pl-2">
        {entries.map(([key, item]) => (
          <li key={key} className="break-words">
            <span className="text-text-muted">{key}:</span> <Value value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === "string") {
    return value === ""
      ? <span className="text-text-muted">"" (empty text)</span>
      : <span className="whitespace-pre-wrap break-all">{JSON.stringify(value)}</span>;
  }
  return <span>{String(value)}</span>;
}

function Section({ title, testId, children }: { title: string; testId: string; children: ReactNode }) {
  return (
    <div className="border-t border-base-border px-2 py-1" data-testid={testId}>
      <div className="font-semibold text-text-primary">{title}</div>
      {children}
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="break-all text-text-muted">
      {label}: <span className="text-text-primary">{children}</span>
    </div>
  );
}

function Snapshot({ title, field, outcome }: { title: string; field: SnapshotField; outcome: Outcome }) {
  const value = outcome[field];
  const reason = outcome.snapshot_missing_reasons?.[field];
  return (
    <div className="mt-1" data-testid={`outcome-snapshot-${field}`}>
      <div className="text-text-primary">{title}</div>
      {value === null ? (
        <div className="text-text-muted">
          Not recorded — the snapshot is absent for this outcome and is not reconstructed.{" "}
          {reason !== undefined ? <>Reason code: <span className="text-text-primary">{reason}</span>.</> : "No reason code was recorded."}
        </div>
      ) : (
        <div className="text-text-muted"><Value value={value} depth={0} /></div>
      )}
    </div>
  );
}

// Population wording comes only from the row's own recorded labels; it never infers a different population.
function populationNote(outcome: Outcome): string {
  switch (outcome.execution_mode) {
    case "simulated":
      return "Simulated execution result — not real-money trading.";
    case "backtest":
      return "Backtest result from a historical replay run — not a simulated-execution trade.";
    default:
      return `Recorded execution mode "${outcome.execution_mode}", shown exactly as stored.`;
  }
}

function Evidence({ outcome }: { outcome: Outcome }) {
  const reasons = outcome.snapshot_missing_reasons;
  const reasonEntries = reasons === null ? [] : Object.entries(reasons);
  return (
    <div data-testid="outcome-evidence-body">
      <div className="px-2 py-1 text-text-muted">
        <div className="text-text-primary">{populationNote(outcome)}</div>
        <div>
          Mode: <span className="text-text-primary">{outcome.execution_mode}</span> · Venue:{" "}
          <span className="text-text-primary">{outcome.execution_venue}</span> · is_backtest: {String(outcome.is_backtest)}
        </div>
      </div>

      <Section title="Strategy attribution" testId="outcome-attribution">
        <Row label="Strategy">{outcome.strategy_name}</Row>
        <Row label="Strategy version">{outcome.strategy_version}</Row>
        <Row label="Symbol / direction">{outcome.symbol} {outcome.direction}</Row>
        <Row label="Origin">{outcome.origin}</Row>
        <Row label="Backtest run">{outcome.backtest_run_id ?? "none recorded"}</Row>
        <Row label="Outcome ID">{outcome.outcome_id}</Row>
        <Row label="Opportunity ID">{outcome.opportunity_id}</Row>
        <Row label="Feature snapshot ID">{outcome.feature_snapshot_id ?? "none recorded"}</Row>
        <Row label="Record schema version">{outcome.schema_version}</Row>
      </Section>

      <Section title="Timing (UTC)" testId="outcome-timing">
        <Row label="Trading day">{outcome.trading_day}</Row>
        <Row label="Setup detected">{utc(outcome.setup_detected_at)}</Row>
        <Row label="Signal confirmed">{utc(outcome.signal_confirmed_at)}</Row>
        <Row label="Decided">{utc(outcome.decided_at)}</Row>
        <Row label="Entry filled">{utc(outcome.entry_filled_at)}</Row>
        <Row label="Exit filled">{utc(outcome.exit_filled_at)}</Row>
        <Row label="Holding">{outcome.holding_seconds}s</Row>
      </Section>

      <Section title="Recorded trade values" testId="outcome-trade-values">
        <Row label="Entry">{outcome.entry_qty} @ {outcome.entry_price}</Row>
        <Row label="Exit">{outcome.exit_qty} @ {outcome.exit_price}</Row>
        <Row label="Exit reason">{outcome.exit_reason}</Row>
        <Row label="Commission">{num(outcome.commission_total, "not available (not recorded)")}</Row>
        <Row label="Entry slippage">{num(outcome.slippage_entry, "not recorded")}</Row>
        <Row label={"Realized P&L"}>{outcome.realized_pnl}</Row>
        <Row label="Realized R">{outcome.realized_r}</Row>
        <Row label="Confidence at signal">{outcome.confidence_at_signal}</Row>
        {outcome.commission_total === null && (
          <div className="text-text-muted">
            No commission was recorded for this outcome. P&amp;L and R are shown as stored; no commission adjustment is assumed or applied here.
          </div>
        )}
      </Section>

      <Section title="Structural and final levels" testId="outcome-levels">
        <Row label="Structural invalidation">{outcome.structural_invalidation}</Row>
        <Row label="Structural target">{outcome.structural_target}</Row>
        <Row label="Final stop">{outcome.final_stop}</Row>
        <Row label="Final target">{outcome.final_target}</Row>
      </Section>

      <Section title="Strategy evidence" testId="outcome-evidence">
        <div className="text-text-muted"><Value value={outcome.evidence} depth={0} /></div>
      </Section>

      <Section title="Entry and exit snapshots" testId="outcome-snapshots">
        <Snapshot title="Market state at entry" field="market_state_at_entry" outcome={outcome} />
        <Snapshot title="Context at entry" field="context_at_entry" outcome={outcome} />
        <Snapshot title="Market state at exit" field="market_state_at_exit" outcome={outcome} />
        <Snapshot title="Context at exit" field="context_at_exit" outcome={outcome} />
        <div className="mt-1 text-text-muted" data-testid="outcome-missing-reasons">
          Recorded missing-reason codes:{" "}
          {reasonEntries.length === 0
            ? "none recorded"
            : reasonEntries.map(([field, code], index) => (
                <span key={field}>{index > 0 && "; "}{field} = <span className="text-text-primary">{code}</span></span>
              ))}
        </div>
      </Section>
    </div>
  );
}

/**
 * Read-only recorded-evidence view for ONE closed outcome (task
 * `recorded-outcome-evidence-detail`), opened from a Recent Closed Trades row.
 * Mounted only while an outcome is selected, so closing it unmounts the hook
 * and any in-flight response is ignored; switching the `outcomeId` prop
 * supersedes the previous request. It shows the backend row as recorded — no
 * missing value is filled in, no snapshot rebuilt, nothing recomputed — and
 * offers no trading actions and no link to a trade (no persisted relationship
 * between an outcome and a trade is read here).
 */
export function StrategyOutcomeEvidence({ outcomeId, onClose }: { outcomeId: string; onClose: () => void }) {
  const { outcome, loading, error, notFound, refresh } = useStrategyOutcomeDetail(outcomeId);
  return (
    <div
      className="mt-1 rounded border border-base-border font-mono text-[10px]"
      data-testid="strategy-outcome-evidence"
      role="region"
      aria-label="Recorded outcome evidence"
    >
      <div className="flex items-center justify-between gap-1 px-2 py-1">
        <span className="text-text-primary">
          Recorded evidence{outcome !== null ? ` — ${outcome.symbol}` : ""}
        </span>
        <span>
          <button onClick={refresh} aria-busy={loading} className="rounded px-1 py-0.5 text-signal hover:bg-base-bg">
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <button onClick={onClose} className="rounded px-1 py-0.5 text-text-muted hover:bg-base-bg">Hide</button>
        </span>
      </div>
      {loading && outcome === null && <p role="status" className="px-2 pb-1 text-text-muted">Loading recorded evidence…</p>}
      {notFound && <p role="alert" className="px-2 pb-1 text-bear">This outcome was not found. It may have been removed.</p>}
      {error !== null && (
        <p role="alert" className="px-2 pb-1 text-bear">
          Could not fetch recorded evidence: {error}{outcome !== null && " Showing the last loaded evidence."}
        </p>
      )}
      {outcome !== null && (
        <div className="max-h-96 overflow-y-auto">
          <Evidence outcome={outcome} />
        </div>
      )}
    </div>
  );
}
