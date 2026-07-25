import type { Opportunity } from "../../types/intelligence";

function confidenceColor(confidence: number) {
  if (confidence >= 70) return "text-bull";
  if (confidence >= 45) return "text-signal";
  return "text-text-muted";
}

function OpportunityRow({ opp }: { opp: Opportunity }) {
  const isBuy = opp.direction === "BUY";
  return (
    <div className="rounded-md border border-base-border bg-base-bg/60 p-3">
      <div className="flex items-center justify-between">
        <span className="font-mono text-sm font-medium text-text-primary">{opp.strategy}</span>
        <span className={`font-mono text-sm font-semibold ${confidenceColor(opp.confidence)}`}>
          {opp.confidence}%
        </span>
      </div>
      <p className="mt-1 text-xs leading-snug text-text-muted">{opp.reason}</p>
      <div className="mt-2 grid grid-cols-3 gap-2 font-mono text-xs">
        <div>
          <div className="text-text-muted">Entry</div>
          <div className={isBuy ? "text-bull" : "text-bear"}>{opp.suggested_entry.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-text-muted">Stop</div>
          <div className="text-bear">{opp.suggested_stop.toFixed(2)}</div>
        </div>
        <div>
          <div className="text-text-muted">Target</div>
          <div className="text-bull">{opp.suggested_target.toFixed(2)}</div>
        </div>
      </div>
    </div>
  );
}

export function AIAnalysisPanel({ symbol, opportunities }: { symbol: string; opportunities: Opportunity[] }) {
  const sorted = [...opportunities].sort((a, b) => b.confidence - a.confidence);
  const top = sorted[0];

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      <div>
        <div className="text-[11px] uppercase tracking-wide text-text-muted">AI Opportunity Score</div>
        <div className="font-mono text-lg font-semibold text-text-primary">{symbol}</div>
      </div>

      {top && (
        <div className="rounded-md border border-signal/30 bg-signal/5 p-3">
          <div className="text-[11px] uppercase tracking-wide text-signal">Final Confidence</div>
          <div className="font-mono text-2xl font-semibold text-signal">{top.confidence}%</div>
        </div>
      )}

      <div className="flex flex-col gap-2">
        {sorted.map((opp, i) => (
          <OpportunityRow key={i} opp={opp} />
        ))}
      </div>
    </div>
  );
}
