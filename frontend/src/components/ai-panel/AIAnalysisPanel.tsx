import type { Opportunity } from "../../hooks/useOpportunities";
import type { FundamentalsContext, NewsContext } from "../../hooks/useContextSnapshot";
import type { OpportunityAgreementWireShape, OpportunityConflictWireShape } from "../../services/api-client";

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

// Small additional section (decision #123) surfacing GET /intelligence/
// opportunity-conflicts alongside the opportunity list above — NOT a new
// panel type, just one more rounded-md block in the same flex column
// every other section here already uses. Renders nothing at all when
// neither prop is set (the honest 0/1-cached-opportunity absence
// get_opportunity_conflicts() itself already represents by leaving the
// symbol out of both collections — see useOpportunityConflicts.ts).
function ConflictStatus({
  agreement,
  conflict,
}: {
  agreement: OpportunityAgreementWireShape | null;
  conflict: OpportunityConflictWireShape | null;
}) {
  if (!agreement && !conflict) return null;

  if (conflict) {
    const buyCount = conflict.by_direction.BUY?.length ?? 0;
    const sellCount = conflict.by_direction.SELL?.length ?? 0;
    return (
      <div className="rounded-md border border-bear/30 bg-bear/5 p-2 text-[11px] text-bear">
        <span className="uppercase tracking-wide">Conflict</span> — {buyCount} BUY vs {sellCount} SELL
      </div>
    );
  }

  if (!agreement) return null; // unreachable given the guard above, but keeps TS's narrowing honest below

  return (
    <div className="rounded-md border border-bull/30 bg-bull/5 p-2 text-[11px] text-bull">
      <span className="uppercase tracking-wide">Agreement</span> — {agreement.count} strategies aligned{" "}
      {agreement.direction}
    </div>
  );
}

// Compact, minimal Fundamentals/News surfacing (this task) — Fundamentals/
// News are per-symbol Context Engine providers (decision #96), so they
// land here alongside decision #123's own ConflictStatus section, not in
// InfoTab.tsx's GeneralContent (that's Calendar's spot instead — see
// InfoTab.tsx's own MarketSessionSummary comment, since Calendar is
// market-wide, not symbol-specific). Unlike ConflictStatus, this section
// never renders null: a real Opportunity/Strategy user still benefits
// from knowing "no fundamentals data yet" or "no recent headlines"
// rather than the section silently vanishing, per this task's own
// honest-empty-state requirement. Rendered from the shared top part of
// this component (see below) so it shows up regardless of whether
// Opportunities happen to be loading/empty/populated — Context Engine
// data has nothing to do with whether Strategy Engine has fired yet.
function formatMarketCap(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  return `$${v.toFixed(0)}`;
}

function formatEarningsDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function SymbolContextSummary({
  fundamentals,
  news,
}: {
  fundamentals: FundamentalsContext | null;
  news: NewsContext | null;
}) {
  // `fundamentals` null means Context Engine has never evaluated this
  // symbol at all yet; a non-null row with every field null means it
  // HAS evaluated but no `symbol_fundamentals` refresh has landed yet
  // (fundamentals.py's own `_read` docstring) — both read the same to a
  // user ("nothing to show"), so one shared message covers both rather
  // than distinguishing a difference nobody here can act on. `sector` is
  // deliberately never shown — it's permanently null in this build
  // (Finnhub has no separate sector field, decision #96), not a value
  // that's ever "not yet fetched," so surfacing it would only ever read
  // as a dead placeholder.
  const hasFundamentals =
    fundamentals !== null &&
    (fundamentals.industry != null || fundamentals.marketCap != null || fundamentals.nextEarningsDate != null);

  return (
    <div className="rounded-md border border-base-border bg-base-bg/60 p-2 text-[11px] text-text-muted">
      <div className="flex flex-wrap items-center gap-x-1.5">
        {hasFundamentals ? (
          <>
            {fundamentals!.industry && <span className="text-text-primary">{fundamentals!.industry}</span>}
            {fundamentals!.marketCap != null && <span>Mkt cap {formatMarketCap(fundamentals!.marketCap)}</span>}
            {fundamentals!.nextEarningsDate && (
              <span>Earnings {formatEarningsDate(fundamentals!.nextEarningsDate)}</span>
            )}
          </>
        ) : (
          <span>No fundamentals data yet.</span>
        )}
      </div>
      <div className="mt-1">
        {/* news === null: Context Engine hasn't evaluated this symbol
            yet. news.present === false: it has, and found nothing —
            same neutral copy for a genuine "no recent headlines" symbol
            and for SPY/QQQ/IWM's unconditional exclusion (decision #94)
            — the wire shape is identical either way, so this UI
            shouldn't (and structurally can't) imply a difference. */}
        {news === null
          ? "No news data yet."
          : news.present
            ? `News: ${news.importance} · ${news.count15m} in last 15m`
            : "No recent headlines."}
      </div>
    </div>
  );
}

export function AIAnalysisPanel({
  symbol,
  opportunities,
  loading,
  agreement = null,
  conflict = null,
  fundamentals = null,
  news = null,
}: {
  symbol: string;
  opportunities: Opportunity[];
  loading: boolean;
  agreement?: OpportunityAgreementWireShape | null;
  conflict?: OpportunityConflictWireShape | null;
  fundamentals?: FundamentalsContext | null;
  news?: NewsContext | null;
}) {
  const sorted = [...opportunities].sort((a, b) => b.confidence - a.confidence);
  const top = sorted.find((o) => o.status === "actionable") ?? sorted[0];

  // Context Engine's Fundamentals/News (this task) live in the shared
  // header block below, rendered in every branch — deliberately NOT
  // gated behind the Opportunities loading/empty checks above/below:
  // whether Strategy Engine has fired anything for this symbol is
  // unrelated to whether Context Engine has Fundamentals/News for it.
  const header = (
    <div>
      <div className="text-[11px] uppercase tracking-wide text-text-muted">AI Opportunity Score</div>
      <div className="font-mono text-lg font-semibold text-text-primary">{symbol}</div>
    </div>
  );

  if (loading && opportunities.length === 0) {
    return (
      <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
        {header}
        <SymbolContextSummary fundamentals={fundamentals} news={news} />
        <div className="p-3 text-center text-[11px] text-text-muted">Loading {symbol}…</div>
      </div>
    );
  }

  if (opportunities.length === 0) {
    return (
      <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
        {header}
        <SymbolContextSummary fundamentals={fundamentals} news={news} />
        <div className="flex flex-col gap-2 p-1 text-center text-[11px] text-text-muted">
          <p>No opportunities reported yet for {symbol}.</p>
          <p>Strategy Engine evaluates on every candle/market-state update — nothing has fired here yet.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      {header}
      <SymbolContextSummary fundamentals={fundamentals} news={news} />

      {top && (
        <div className="rounded-md border border-signal/30 bg-signal/5 p-3">
          <div className="text-[11px] uppercase tracking-wide text-signal">
            {top.status === "actionable" ? "Final Confidence" : `Top Confidence (${top.status})`}
          </div>
          <div className="font-mono text-2xl font-semibold text-signal">{top.confidence.toFixed(0)}%</div>
        </div>
      )}

      <ConflictStatus agreement={agreement} conflict={conflict} />

      <div className="flex flex-col gap-2">
        {sorted.map((opp) => (
          <OpportunityRow key={`${opp.strategy}:${opp.version}`} opp={opp} />
        ))}
      </div>
    </div>
  );
}
