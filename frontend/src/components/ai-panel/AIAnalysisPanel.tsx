import type { Opportunity } from "../../hooks/useOpportunities";

function confidenceColor(confidence: number) {
  if (confidence >= 70) return "text-bull";
  if (confidence >= 45) return "text-signal";
  return "text-text-muted";
}

// Status badge (design fork #3, TESTING.md) — "waiting"/"potential"/
// "expired" previously rendered identically to "actionable" (a live,
// currently-true signal), which misrepresented what's actually going on.
// "actionable" gets no badge at all — it's the default, expected state,
// not something that needs calling out.
function StatusBadge({ status }: { status: Opportunity["status"] }) {
  if (status === "actionable") return null;
  const style: Record<Exclude<Opportunity["status"], "actionable">, string> = {
    waiting: "border-signal/40 text-signal",
    potential: "border-text-muted/40 text-text-muted",
    expired: "border-text-muted/30 text-text-muted line-through",
  };
  return (
    <span className={`rounded border px-1 py-0.5 font-mono text-[9px] uppercase tracking-wide ${style[status]}`}>
      {status}
    </span>
  );
}

// setup_detected_at is the recommended default (design fork #4, TESTING.md)
// — the real domain timestamp (derived from features.candle_ts, §7's
// backtest-safety invariant), not received_at (OpportunityCache's own
// wall-clock arrival time — a cache-internal detail, not something the
// setup itself happened at).
function formatDetectedAt(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function OpportunityRow({ opp }: { opp: Opportunity }) {
  const isBuy = opp.direction === "BUY";
  const isActionable = opp.status === "actionable";
  const conditionEntries = Object.entries(opp.conditions);

  return (
    <div
      className={`rounded-md border border-base-border bg-base-bg/60 p-3 ${isActionable ? "" : "opacity-60"}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-sm font-medium text-text-primary">{opp.strategy}</span>
          <StatusBadge status={opp.status} />
        </div>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-text-muted">{formatDetectedAt(opp.setupDetectedAt)}</span>
          <span className={`font-mono text-sm font-semibold ${confidenceColor(opp.confidence)}`}>
            {opp.confidence.toFixed(0)}%
          </span>
        </div>
      </div>

      {/* evidence.reason — a real sentence GENERATED server-side from
          evidence.conditions (strategy-engine-design.md §4: "reason
          stays a human-readable string, generated from conditions for
          display"), never client-fabricated. Falls back honestly rather
          than inventing text if a future strategy ever leaves it unset. */}
      <p className="mt-1 text-xs leading-snug text-text-muted">{opp.reason ?? "No evidence reason reported."}</p>

      {opp.status === "waiting" && opp.waitReason && (
        <p className="mt-1 text-[11px] leading-snug text-signal/90">Waiting: {opp.waitReason}</p>
      )}

      {conditionEntries.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-text-muted">
          {conditionEntries.map(([key, value]) => (
            <span key={key}>
              {key}=<span className="text-text-primary">{typeof value === "number" ? value.toFixed(2) : value}</span>
            </span>
          ))}
        </div>
      )}

      {/* Entry column dropped (design fork #2, TESTING.md) — Trade
          Planning Engine, which would compute a real entry price, isn't
          built yet; there's no suggested_entry anywhere on the real
          Opportunity payload to show. "Stop"/"Target" relabeled to name
          what structural_invalidation/structural_target actually are:
          where the strategy's OWN thesis breaks, not a refined,
          trade-ready plan (strategy-engine-design.md §4's Strategy ->
          Trade Planning Engine diagram — this is a REQUIRED STARTING
          POINT for that engine, not a finished stop/target pair). */}
      <div className="mt-2 grid grid-cols-2 gap-2 font-mono text-xs">
        <div title="Structural invalidation — price at which this setup's own thesis is falsified">
          <div className="text-text-muted">Invalidation</div>
          <div className="text-bear">{opp.structuralInvalidation.toFixed(2)}</div>
        </div>
        <div title="Structural target — not a refined, trade-ready target">
          <div className="text-text-muted">Structural target</div>
          <div className={isBuy ? "text-bull" : "text-bear"}>{opp.structuralTarget.toFixed(2)}</div>
        </div>
      </div>
    </div>
  );
}

export function AIAnalysisPanel({
  symbol,
  opportunities,
  loading,
}: {
  symbol: string;
  opportunities: Opportunity[];
  loading: boolean;
}) {
  const sorted = [...opportunities].sort((a, b) => b.confidence - a.confidence);
  const top = sorted.find((o) => o.status === "actionable") ?? sorted[0];

  if (loading && opportunities.length === 0) {
    return <div className="p-3 text-center text-[11px] text-text-muted">Loading {symbol}…</div>;
  }

  if (opportunities.length === 0) {
    return (
      <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-text-muted">AI Opportunity Score</div>
          <div className="font-mono text-lg font-semibold text-text-primary">{symbol}</div>
        </div>
        <div className="flex flex-col gap-2 p-1 text-center text-[11px] text-text-muted">
          <p>No opportunities reported yet for {symbol}.</p>
          <p>Strategy Engine evaluates on every candle/market-state update — nothing has fired here yet.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      <div>
        <div className="text-[11px] uppercase tracking-wide text-text-muted">AI Opportunity Score</div>
        <div className="font-mono text-lg font-semibold text-text-primary">{symbol}</div>
      </div>

      {top && (
        <div className="rounded-md border border-signal/30 bg-signal/5 p-3">
          <div className="text-[11px] uppercase tracking-wide text-signal">
            {top.status === "actionable" ? "Final Confidence" : `Top Confidence (${top.status})`}
          </div>
          <div className="font-mono text-2xl font-semibold text-signal">{top.confidence.toFixed(0)}%</div>
        </div>
      )}

      <div className="flex flex-col gap-2">
        {sorted.map((opp) => (
          <OpportunityRow key={`${opp.strategy}:${opp.version}`} opp={opp} />
        ))}
      </div>
    </div>
  );
}
