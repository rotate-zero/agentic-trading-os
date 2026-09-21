import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useBacktestOutcomes } from "../../hooks/useBacktestOutcomes";
import { useBacktestRuns } from "../../hooks/useBacktestRuns";
import { useBacktestSweepOutcomes } from "../../hooks/useBacktestSweepOutcomes";
import { useWorkspace } from "../../state/WorkspaceContext";
import type { BacktestRunWireShape, StrategyOutcomeWireShape } from "../../services/api-client";

// Same collapsible-width convention ScannerPanel.tsx established and
// BacktestPanel.tsx already reused verbatim — same constants, same
// resize-handle behavior, same "starts collapsed" posture, so a fifth
// sidebar doesn't grab space by default either.
const MIN_WIDTH = 64;
const MAX_WIDTH = 480;
const COLLAPSED_WIDTH = 36;
const DEFAULT_WIDTH = 340; // slightly wider than Scanner/Backtest's 300 — this panel's own expand-in-place detail view (below) benefits from a bit more room, though it still resizes like every other sibling.

// This panel's own default read: "everything this table currently has"
// (see BacktestResultsPanel's own top-level comment / the route's own
// docstring — `strategy_outcomes` has zero real LIVE rows today, so
// `is_backtest=true` with no `backtest_run_id` filter is the only view
// worth defaulting to). 500 is the backend's own hard cap
// (`Query(50, le=500)` on the route) — passed explicitly rather than
// omitted, since omitting it would fall back to the route's own
// live-oriented default of 50, which could silently hide older
// backtest rows once more than 50 exist. No pagination UI: the real
// row count today is nowhere near 500, and adding a "load more"
// control for a cap this generous would be building ahead of an actual
// need per this task's own scope discipline.
const OUTCOMES_LIMIT = 500;

// Deliberately local component state (collapsed/widthPx), not threaded
// through WorkspaceContext.tsx — same reasoning BacktestPanel.tsx's own
// header comment already gives for itself: this panel's contents (a
// read of whatever currently exists in `strategy_outcomes`, optionally
// narrowed by a typed-in run_id) have no server-side push to sync
// across tabs and no real reason to survive a reload as "this Main
// Window's" state the way Scanner's own persisted view does. Flagged
// here explicitly, not a silent deviation.
//
// Decision #134 narrows this: the run_id VALUE itself (not this panel's
// collapsed/widthPx chrome) now has a real, stated reason to be shared
// and to survive a reload — see WorkspaceContext.tsx's
// `lastBacktestRunId`/`setLastBacktestRunId`, read below via
// `useWorkspace()`. Collapsed/widthPx stay exactly as they were; only
// the run_id filter's DEFAULT now comes from shared state.

// holding_seconds -> human string. Unlike BacktestPanel.tsx's own
// formatElapsed (which only ever needs to show a few minutes of a
// single in-flight run), a persisted StrategyOutcome's holding period
// can span multiple hours (e.g. a position held most of a session), so
// this includes an hours bucket that formatElapsed doesn't need.
function formatHoldingDuration(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds) || totalSeconds < 0) return "—";
  const s = Math.floor(totalSeconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

// Full date+time, not InfoTab.tsx's formatExitTime/AIAnalysisPanel.tsx's
// formatDetectedAt "time only" convention — those both back a
// "recent activity, today" feed where the date is implicitly "now."
// This panel browses backtest runs that can be triggered any day
// (there's no recency assumption for a `backtest_run_id` a person
// pastes in from a prior `curl` call), so the date matters here in a
// way it doesn't for either of those.
function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// Deliberately NOT .toFixed(2) — every quick-glance summary elsewhere
// in this codebase (InfoTab.tsx's RecentClosedTrades, ScannerPanel.tsx's
// feature chips, AIAnalysisPanel.tsx's structuralTarget) rounds to 2
// decimals, which is the right tradeoff for a glanceable feed. This
// panel exists specifically so a person can verify what a backtest
// actually recorded, so realized_pnl/realized_r — the two figures this
// task's own scope calls out by name — render via plain `String()`
// (JS's own shortest-round-trip conversion), never rounded, with an
// explicit "+" added for non-negative values so a zero or small
// positive R never reads as ambiguous next to a negative one.
function formatSignedVerbatim(value: number): string {
  if (!Number.isFinite(value)) return String(value);
  return value >= 0 ? `+${value}` : String(value);
}

// Same is-it-a-buy convention AIAnalysisPanel.tsx's OpportunityRow
// already uses (text-bull for BUY, text-bear for SELL) — reused here
// rather than invented fresh, so a BUY/SELL row reads the same way in
// both places.
function directionColorClass(direction: "BUY" | "SELL"): string {
  return direction === "BUY" ? "text-bull" : "text-bear";
}

// Full-record expand-in-place view. Expand-in-place (a toggle button
// per row revealing a section below it) was chosen over a modal or a
// separate details pane after checking directly: no modal/dialog/
// portal pattern exists anywhere in frontend/src/ today (grepped, not
// assumed) — introducing one just for this single view would add a new
// UI paradigm this codebase doesn't otherwise use, for a task whose own
// scope is "one panel." Expand-in-place keeps the row and its detail
// visually anchored together and costs nothing beyond local per-row
// state, at the cost of pushing rows below it down the list while
// open — an acceptable tradeoff for a panel that's browsed, not
// live-monitored.
function OutcomeDetail({ outcome }: { outcome: StrategyOutcomeWireShape }) {
  const scalarFields: Array<[string, string]> = [
    ["outcome_id", outcome.outcome_id],
    ["opportunity_id", outcome.opportunity_id],
    ["backtest_run_id", outcome.backtest_run_id ?? "—"],
    ["feature_snapshot_id", outcome.feature_snapshot_id ?? "—"],
    ["schema_version", String(outcome.schema_version)],
    ["strategy_version", outcome.strategy_version],
    ["origin", outcome.origin],
    ["trading_day", outcome.trading_day],
    ["setup_detected_at", outcome.setup_detected_at],
    ["signal_confirmed_at", outcome.signal_confirmed_at ?? "—"],
    ["decided_at", outcome.decided_at ?? "—"],
    ["entry_qty", String(outcome.entry_qty)],
    ["exit_qty", String(outcome.exit_qty)],
    ["commission_total", outcome.commission_total === null ? "—" : String(outcome.commission_total)],
    ["slippage_entry", outcome.slippage_entry === null ? "—" : String(outcome.slippage_entry)],
    ["structural_invalidation", String(outcome.structural_invalidation)],
    ["structural_target", String(outcome.structural_target)],
    ["final_stop", String(outcome.final_stop)],
    ["final_target", String(outcome.final_target)],
    ["confidence_at_signal", String(outcome.confidence_at_signal)],
  ];

  const blobFields: Array<[string, Record<string, unknown>]> = [
    ["evidence", outcome.evidence],
    ["market_state_at_entry", outcome.market_state_at_entry],
    ["market_state_at_exit", outcome.market_state_at_exit],
    ["context_at_entry", outcome.context_at_entry],
    ["context_at_exit", outcome.context_at_exit],
  ];

  return (
    <div className="flex flex-col gap-2 border-t border-base-border bg-base-bg/40 p-2">
      <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 font-mono text-[10px]">
        {scalarFields.map(([label, value]) => (
          <Fragment key={label}>
            <span className="text-text-muted">{label}</span>
            <span className="truncate text-text-primary" title={value}>
              {value}
            </span>
          </Fragment>
        ))}
      </div>
      <div className="flex flex-col gap-1.5">
        {blobFields.map(([label, value]) => (
          <div key={label} className="flex flex-col gap-0.5">
            <span className="text-[9px] uppercase tracking-wide text-text-muted">{label}</span>
            <pre className="max-h-40 overflow-auto rounded border border-base-border bg-base-bg p-1.5 font-mono text-[9px] leading-snug text-text-primary">
              {JSON.stringify(value, null, 2)}
            </pre>
          </div>
        ))}
      </div>
    </div>
  );
}

function OutcomeRow({ outcome }: { outcome: StrategyOutcomeWireShape }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded border border-base-border">
      <div className="flex items-center justify-between gap-2 px-2 py-1.5">
        <div className="min-w-0">
          <div className="font-mono text-xs font-medium text-text-primary">
            {outcome.symbol} <span className="text-text-muted">{outcome.strategy_name}</span>{" "}
            <span className={directionColorClass(outcome.direction)}>{outcome.direction}</span>
          </div>
          <div className="truncate font-mono text-[10px] text-text-muted">
            {outcome.entry_price} → {outcome.exit_price} · {outcome.exit_reason} ·{" "}
            {formatHoldingDuration(outcome.holding_seconds)}
          </div>
          <div className="truncate font-mono text-[9px] text-text-muted">
            {formatDateTime(outcome.entry_filled_at)} → {formatDateTime(outcome.exit_filled_at)}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="text-right">
            <div className={`font-mono text-xs ${outcome.realized_pnl >= 0 ? "text-bull" : "text-bear"}`}>
              {formatSignedVerbatim(outcome.realized_pnl)}
            </div>
            <div className={`font-mono text-[10px] ${outcome.realized_r >= 0 ? "text-bull" : "text-bear"}`}>
              {formatSignedVerbatim(outcome.realized_r)}R
            </div>
          </div>
          <button
            onClick={() => setExpanded(!expanded)}
            title={expanded ? "Hide full record" : "Show full record"}
            className="rounded px-1 py-0.5 font-mono text-[10px] text-text-muted hover:bg-base-bg hover:text-text-primary"
          >
            {expanded ? "▾" : "▸"}
          </button>
        </div>
      </div>
      {expanded && <OutcomeDetail outcome={outcome} />}
    </div>
  );
}

// Run-level metadata card — surfaces GET /intelligence/backtest-runs
// (decision #136) for the first time anywhere in this codebase. Keyed
// to `appliedRunId` (below), never rendered when it's unset: the
// route's own filter semantics mean a bare run_id resolves to zero or
// one row (BacktestRunRecord's own primary key), so there's exactly one
// well-defined "the run currently being browsed" only once a specific
// run_id is applied — the same read the route's own docstring names as
// its motivating gap ("a caller with only a run_id... can now resolve
// that run's own metadata"). This is run-LEVEL data (one row per
// backtest run: strategy_version, config_hash, symbol_universe,
// date_range, data_version/feature_version, walk_forward_fold,
// is_holdout) — a different table and granularity than the
// StrategyOutcome rows the list below shows (one row per closed trade a
// run produced); the two are related only by run_id/backtest_run_id,
// never merged into one shape or one fetch.
//
// Independent loading/error state from the outcomes list below — same
// "never conflate two different data sources' honest state" reasoning
// OutcomeRow/BacktestResultsBody's own error handling already applies:
// a metadata-fetch failure must never look like "this run has no
// outcomes," and vice versa.
function RunMetadataCard({ runId }: { runId: string }) {
  const { backtestRun, loading, error } = useBacktestRuns({ runId });

  if (loading) {
    return <p className="px-2 py-1 font-mono text-[10px] text-text-muted">Loading run metadata…</p>;
  }
  if (error) {
    return (
      <div className="rounded border border-bear/40 px-2 py-1.5 font-mono text-[10px] text-bear">
        Failed to load run metadata: {error}
      </div>
    );
  }
  if (!backtestRun) {
    // Well-formed run_id, genuinely no matching row in `backtests` —
    // distinct from the error case above, same honest-empty-vs-error
    // split this file's other data source already draws.
    return (
      <p className="px-2 py-1 font-mono text-[10px] text-text-muted">
        No run metadata found for run_id "{runId}".
      </p>
    );
  }

  const fields: Array<[string, string]> = [
    ["strategy_name", backtestRun.strategy_name],
    ["strategy_version", backtestRun.strategy_version],
    ["config_hash", backtestRun.config_hash],
    ["sweep_id", backtestRun.sweep_id],
    ["symbol_universe", backtestRun.symbol_universe.join(", ") || "—"],
    ["date_range", `${backtestRun.date_range_start} → ${backtestRun.date_range_end}`],
    ["data_version", backtestRun.data_version],
    ["feature_version", backtestRun.feature_version],
    ["walk_forward_fold", backtestRun.walk_forward_fold === null ? "—" : String(backtestRun.walk_forward_fold)],
    ["is_holdout", backtestRun.is_holdout ? "yes" : "no"],
    ["created_at", formatDateTime(backtestRun.created_at)],
  ];

  return (
    <div className="rounded border border-base-border bg-base-bg/40 p-2">
      <div className="mb-1 font-mono text-[9px] uppercase tracking-wide text-text-muted">Run metadata</div>
      <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 font-mono text-[10px]">
        {fields.map(([label, value]) => (
          <Fragment key={label}>
            <span className="text-text-muted">{label}</span>
            <span className="truncate text-text-primary" title={value}>
              {value}
            </span>
          </Fragment>
        ))}
      </div>
    </div>
  );
}

// Compact per-run strip for sweep_id filter mode — one line per
// BacktestRunRecord resolved for the applied sweep_id (GET
// /intelligence/backtest-runs?sweep_id=..., decision #136, confirmed
// directly to already support this filter). Deliberately NOT a second
// RunMetadataCard per run: that card's full field grid (11 rows) times up
// to BACKTEST_SWEEP_MAX_PAIRS (20) runs would dwarf the outcomes list
// below it for a browsing view whose real subject is the sweep's own
// outcomes, not any one run's full settings — a person who wants a
// specific run's own full metadata can already get it by switching this
// panel's filter type to run_id and pasting that run_id in, so this strip
// only needs to answer "which pairs make up this sweep, and how many
// outcomes did each one produce" at a glance.
//
// `outcomeCountByRunId` is computed by the caller (BacktestResultsBody)
// from the same merged outcomes list already fetched for the list below —
// this component does no fetching of its own. Existing purely to make an
// honest outcomes_recorded=0 pair visible even though it contributes zero
// rows to the merged list underneath (see useBacktestSweepOutcomes.ts's
// own comment for why this matters) — reusing "known 0" vs "still
// loading" distinction via the `loading` prop, same honest-state
// discipline the rest of this panel already follows.
function RunsInSweepStrip({
  runs,
  outcomeCountByRunId,
  loading,
}: {
  runs: BacktestRunWireShape[];
  outcomeCountByRunId: Map<string, number>;
  loading: boolean;
}) {
  if (loading && runs.length === 0) {
    return <p className="px-2 py-1 font-mono text-[10px] text-text-muted">Resolving sweep…</p>;
  }
  if (runs.length === 0) return null;

  return (
    <div className="mb-2 rounded border border-base-border bg-base-bg/40 p-2">
      <div className="mb-1 font-mono text-[9px] uppercase tracking-wide text-text-muted">
        Runs in this sweep ({runs.length})
      </div>
      <div className="flex flex-col gap-1">
        {runs.map((r) => (
          <div key={r.run_id} className="flex items-center justify-between gap-2 font-mono text-[10px]">
            <span className="min-w-0 flex-1 truncate text-text-primary" title={r.run_id}>
              {r.symbol_universe.join(", ") || "—"} <span className="text-text-muted">{r.run_id}</span>
            </span>
            <span className="shrink-0 text-text-muted">{outcomeCountByRunId.get(r.run_id) ?? 0} outcome(s)</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Shared scrollable outcomes list + footer, reused by both filter types
// below (run_id and sweep_id resolve to a `StrategyOutcomeWireShape[]`
// through two different hooks/paths, but render identically once they
// have one — OutcomeRow above already handles a mixed-symbol,
// mixed-strategy list correctly, which is exactly what a sweep's own
// merged outcomes are). `extraContent` is rendered above the list —
// RunMetadataCard for run_id mode, RunsInSweepStrip for sweep_id mode —
// kept as a slot here rather than duplicating this whole block twice.
function OutcomesListSection({
  outcomes,
  loading,
  error,
  refetch,
  emptyMessage,
  extraContent,
}: {
  outcomes: StrategyOutcomeWireShape[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
  emptyMessage: string;
  extraContent?: React.ReactNode;
}) {
  return (
    <>
      <div className="flex-1 overflow-y-auto p-2">
        {extraContent}
        {error && (
          <div className="rounded border border-bear/40 px-2 py-3 font-mono text-[11px] text-bear">
            Failed to load: {error}
          </div>
        )}
        {!error && loading && outcomes.length === 0 && (
          <p className="p-1 font-mono text-[11px] text-text-muted">Loading…</p>
        )}
        {!error && !loading && outcomes.length === 0 && (
          <p className="p-1 font-mono text-[11px] text-text-muted">{emptyMessage}</p>
        )}
        {outcomes.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {outcomes.map((o) => (
              <OutcomeRow key={o.outcome_id} outcome={o} />
            ))}
          </div>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-between border-t border-base-border px-2 py-1">
        <button
          onClick={refetch}
          disabled={loading}
          className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary disabled:opacity-50"
        >
          {loading ? "…" : "Refresh"}
        </button>
        <span className="font-mono text-[9px] text-text-muted">
          {outcomes.length} row{outcomes.length === 1 ? "" : "s"}
        </span>
      </div>
    </>
  );
}

// Decision #134's own filter-mode split, UNCHANGED below for run_id.
// `lastBacktestRunId` (shared WorkspaceContext state, set by
// BacktestPanel.tsx when a run finishes) is a DEFAULT, never a forced
// value — the task's own scope explicitly requires that a person can
// still type or clear the filter and look at something else, and that a
// run finishing elsewhere must never silently overwrite an in-progress
// manual lookup already sitting in this filter.
//
// The real, stated decision (not a silent default): this panel starts
// in "auto" mode and stays there — continuously following whatever
// `lastBacktestRunId` currently is, including across new runs finishing
// while this panel is already open — right up until the person
// interacts with the filter themselves (Apply OR Clear), at which point
// it switches to "manual" and freezes: further runs finishing elsewhere
// update the SHARED value (so BacktestPanel.tsx's own "prefilled" note
// stays true) but no longer touch what THIS panel is showing, exactly
// the "don't clobber an in-progress manual lookup" requirement. A small
// explicit "↺ follow latest run" control is the only way back to "auto"
// from "manual" — re-collapsing/re-expanding the panel would also reset
// it (this component unmounts on collapse), but that's not a
// discoverable way to ask for it, so this task adds the explicit control
// rather than relying on that side effect.
type RunIdFilterMode = "auto" | "manual";

// decision #163: sweep_id gets its OWN explicit
// filter mode/type, not a generalized "run_id or sweep_id" single value.
// Considered and rejected: both are real UUID strings with no way to
// tell them apart from the string alone without a round-trip query, so a
// single merged input would need to guess which endpoint to call, or
// call both and pick whichever resolves — either adds real, invisible
// complexity for a person who already knows which kind of ID they're
// pasting. Two named, explicit filter TYPES (a small tab-style toggle,
// same visual language BacktestPanel.tsx's own fixture/IBKR/sweep mode
// toggle already establishes) is clearer both to read and to type
// against. sweep_id's own auto/manual state mirrors RunIdFilterMode's
// exactly, driven by lastBacktestSweepId instead of lastBacktestRunId —
// the same real, valuable follow-through decision #134 built for run_id,
// scoped IN here rather than deferred, since the mechanism (one more
// WorkspaceContext field, mirrored end to end) already existed to extend.
type SweepIdFilterMode = "auto" | "manual";
type FilterType = "run_id" | "sweep_id";

function BacktestResultsBody() {
  const { lastBacktestRunId, lastBacktestSweepId } = useWorkspace();
  const [filterType, setFilterType] = useState<FilterType>("run_id");

  // --- run_id filter state — unchanged from before this delivery ---
  const [mode, setMode] = useState<RunIdFilterMode>("auto");
  const [runIdInput, setRunIdInput] = useState(lastBacktestRunId ?? "");
  const [appliedRunId, setAppliedRunId] = useState<string | undefined>(lastBacktestRunId ?? undefined);
  const runIdInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (mode !== "auto") return;
    setRunIdInput(lastBacktestRunId ?? "");
    setAppliedRunId(lastBacktestRunId ?? undefined);
  }, [lastBacktestRunId, mode]);

  // --- sweep_id filter state — new, mirrors run_id's exactly ---
  const [sweepMode, setSweepMode] = useState<SweepIdFilterMode>("auto");
  const [sweepIdInput, setSweepIdInput] = useState(lastBacktestSweepId ?? "");
  const [appliedSweepId, setAppliedSweepId] = useState<string | undefined>(lastBacktestSweepId ?? undefined);
  const sweepIdInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (sweepMode !== "auto") return;
    setSweepIdInput(lastBacktestSweepId ?? "");
    setAppliedSweepId(lastBacktestSweepId ?? undefined);
  }, [lastBacktestSweepId, sweepMode]);

  // Both hooks are always called (React's own rules of hooks forbid
  // calling one of them only when filterType matches) — `enabled`/a
  // `sweepId` of `undefined` is each hook's own explicit way to skip its
  // real network call while the OTHER filter type is the one currently
  // shown, rather than always fetching in the background for a result
  // that will never render. See each hook's own comment for this exact
  // reasoning.
  const {
    outcomes: runOutcomes,
    loading: runLoading,
    error: runError,
    refetch: runRefetch,
  } = useBacktestOutcomes({
    limit: OUTCOMES_LIMIT,
    backtestRunId: appliedRunId,
    enabled: filterType === "run_id",
  });

  const {
    runs: sweepRuns,
    outcomes: sweepOutcomes,
    loading: sweepLoading,
    error: sweepError,
    refetch: sweepRefetch,
  } = useBacktestSweepOutcomes({
    limit: OUTCOMES_LIMIT,
    sweepId: filterType === "sweep_id" ? appliedSweepId : undefined,
  });

  const outcomeCountByRunId = useMemoOutcomeCounts(sweepOutcomes);

  const applyRunFilter = () => {
    const trimmed = runIdInput.trim();
    setMode("manual");
    setAppliedRunId(trimmed === "" ? undefined : trimmed);
  };

  const clearRunFilter = () => {
    setRunIdInput("");
    setAppliedRunId(undefined);
    setMode("manual"); // explicit "show everything" is itself a manual choice — it must stick, not silently flip back to auto on the next finished run
  };

  const followLatestRun = () => {
    setMode("auto");
    setRunIdInput(lastBacktestRunId ?? "");
    setAppliedRunId(lastBacktestRunId ?? undefined);
  };

  const applySweepFilter = () => {
    const trimmed = sweepIdInput.trim();
    setSweepMode("manual");
    setAppliedSweepId(trimmed === "" ? undefined : trimmed);
  };

  const clearSweepFilter = () => {
    setSweepIdInput("");
    setAppliedSweepId(undefined);
    setSweepMode("manual");
  };

  const followLatestSweep = () => {
    setSweepMode("auto");
    setSweepIdInput(lastBacktestSweepId ?? "");
    setAppliedSweepId(lastBacktestSweepId ?? undefined);
  };

  return (
    <>
      <div className="flex shrink-0 rounded-none border-b border-base-border font-mono text-[10px]" role="tablist" aria-label="Filter type">
        <button
          type="button"
          role="tab"
          aria-selected={filterType === "run_id"}
          onClick={() => setFilterType("run_id")}
          className={`flex-1 px-2 py-1 ${
            filterType === "run_id" ? "bg-signal/20 text-signal" : "text-text-muted hover:bg-base-bg"
          }`}
        >
          run_id
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={filterType === "sweep_id"}
          onClick={() => setFilterType("sweep_id")}
          className={`flex-1 px-2 py-1 ${
            filterType === "sweep_id" ? "bg-signal/20 text-signal" : "text-text-muted hover:bg-base-bg"
          }`}
        >
          sweep_id
        </button>
      </div>

      {filterType === "run_id" && (
        <div className="flex shrink-0 flex-col gap-1 border-b border-base-border p-2">
          <div className="flex items-center justify-between">
            <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">Filter by run_id</span>
            {mode === "auto" && lastBacktestRunId && (
              <span
                className="font-mono text-[9px] text-signal"
                title="Automatically following the most recently finished Backtest Runner run"
              >
                auto
              </span>
            )}
          </div>
          <div className="flex gap-1">
            <input
              ref={runIdInputRef}
              value={runIdInput}
              onChange={(e) => setRunIdInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && applyRunFilter()}
              placeholder="optional — paste a run_id"
              className="min-w-0 flex-1 rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-[10px] text-text-primary placeholder:text-text-muted focus:border-signal focus:outline-none"
            />
            <button
              onClick={applyRunFilter}
              className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary"
            >
              Apply
            </button>
            {appliedRunId && (
              <button
                onClick={clearRunFilter}
                className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary"
              >
                Clear
              </button>
            )}
          </div>
          {mode === "manual" && lastBacktestRunId && (
            <button
              onClick={followLatestRun}
              className="self-start rounded border border-base-border px-1.5 py-0.5 font-mono text-[9px] text-text-muted hover:border-signal hover:text-text-primary"
              title="Switch back to automatically following the most recently finished run"
            >
              ↺ Follow latest run
            </button>
          )}
          {appliedRunId && (
            <span className="truncate font-mono text-[9px] text-text-muted" title={appliedRunId}>
              Showing run_id={appliedRunId} {mode === "auto" ? "(auto)" : "(manually set)"}
            </span>
          )}
        </div>
      )}

      {filterType === "sweep_id" && (
        <div className="flex shrink-0 flex-col gap-1 border-b border-base-border p-2">
          <div className="flex items-center justify-between">
            <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">Filter by sweep_id</span>
            {sweepMode === "auto" && lastBacktestSweepId && (
              <span
                className="font-mono text-[9px] text-signal"
                title="Automatically following the most recently finished sweep"
              >
                auto
              </span>
            )}
          </div>
          <div className="flex gap-1">
            <input
              ref={sweepIdInputRef}
              value={sweepIdInput}
              onChange={(e) => setSweepIdInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && applySweepFilter()}
              placeholder="paste a sweep_id"
              className="min-w-0 flex-1 rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-[10px] text-text-primary placeholder:text-text-muted focus:border-signal focus:outline-none"
            />
            <button
              onClick={applySweepFilter}
              className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary"
            >
              Apply
            </button>
            {appliedSweepId && (
              <button
                onClick={clearSweepFilter}
                className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary"
              >
                Clear
              </button>
            )}
          </div>
          {sweepMode === "manual" && lastBacktestSweepId && (
            <button
              onClick={followLatestSweep}
              className="self-start rounded border border-base-border px-1.5 py-0.5 font-mono text-[9px] text-text-muted hover:border-signal hover:text-text-primary"
              title="Switch back to automatically following the most recently finished sweep"
            >
              ↺ Follow latest sweep
            </button>
          )}
          {appliedSweepId && (
            <span className="truncate font-mono text-[9px] text-text-muted" title={appliedSweepId}>
              Showing sweep_id={appliedSweepId} {sweepMode === "auto" ? "(auto)" : "(manually set)"}
            </span>
          )}
          {/* Deliberately no "show everything" default the way run_id
              mode's own empty-filter state has — see
              useBacktestSweepOutcomes.ts's own comment for why there is
              no meaningful "every sweep merged together" view. */}
          {!appliedSweepId && (
            <span className="font-mono text-[9px] leading-snug text-text-muted">
              No "show everything" view for sweeps — paste or wait for a sweep_id to browse its combined outcomes.
            </span>
          )}
        </div>
      )}

      {filterType === "run_id" ? (
        <OutcomesListSection
          outcomes={runOutcomes}
          loading={runLoading}
          error={runError}
          refetch={runRefetch}
          emptyMessage={
            appliedRunId
              ? `No backtest outcomes found for run_id "${appliedRunId}".`
              : "No backtest outcomes recorded yet."
          }
          extraContent={
            appliedRunId ? (
              <div className="mb-2">
                <RunMetadataCard runId={appliedRunId} />
              </div>
            ) : undefined
          }
        />
      ) : (
        <OutcomesListSection
          outcomes={sweepOutcomes}
          loading={sweepLoading}
          error={sweepError}
          refetch={sweepRefetch}
          emptyMessage={
            appliedSweepId
              ? `No backtest outcomes found for sweep_id "${appliedSweepId}" (its own runs may have honestly recorded zero each — see the sweep summary above).`
              : "No sweep_id applied yet."
          }
          extraContent={
            appliedSweepId ? (
              <RunsInSweepStrip runs={sweepRuns} outcomeCountByRunId={outcomeCountByRunId} loading={sweepLoading} />
            ) : undefined
          }
        />
      )}
    </>
  );
}

// Small local helper, not a new hooks/ file — groups the sweep's own
// merged outcomes by backtest_run_id so RunsInSweepStrip can show each
// resolved run's real outcome count without a second fetch. Recomputed
// via useMemo only when the outcomes array reference actually changes
// (a new merged result from useBacktestSweepOutcomes.ts), not on every
// render.
function useMemoOutcomeCounts(outcomes: StrategyOutcomeWireShape[]): Map<string, number> {
  return useMemo(() => {
    const counts = new Map<string, number>();
    for (const o of outcomes) {
      if (!o.backtest_run_id) continue;
      counts.set(o.backtest_run_id, (counts.get(o.backtest_run_id) ?? 0) + 1);
    }
    return counts;
  }, [outcomes]);
}

// New sibling panel (decision #133) — the first thing in this codebase
// that renders `strategy_outcomes` rows written with `is_backtest=True`
// (Backtest Runner v1, decision #128) anywhere outside a `curl` call or
// a direct Postgres query. See `strategy-engine-design.md` §7's newest
// as-built note for the full read-path diagram (route → hook → panel).
//
// This delivery adds `RunMetadataCard` (above): the first frontend
// reader of `GET /intelligence/backtest-runs` (decision #136) — a
// different table (`backtests`) and granularity (one row per run's own
// settings) than the `StrategyOutcome` rows this panel already showed,
// surfaced here because `appliedRunId` (below) already carries the
// exact run_id linkage both data sources need, with zero new shared
// state (`WorkspaceContext.tsx` unchanged).
export function BacktestResultsPanel() {
  const [collapsed, setCollapsed] = useState(true); // starts collapsed, same reasoning every other sibling panel here already uses
  const [widthPx, setWidthPx] = useState(DEFAULT_WIDTH);
  const dragStartRef = useRef<{ x: number; width: number } | null>(null);

  const onResizeDown = (e: React.PointerEvent) => {
    e.preventDefault();
    dragStartRef.current = { x: e.clientX, width: widthPx };
    const onMove = (ev: PointerEvent) => {
      if (!dragStartRef.current) return;
      const delta = dragStartRef.current.x - ev.clientX; // panel is on the right, dragging left grows it
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, dragStartRef.current.width + delta));
      setWidthPx(next);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  const width = collapsed ? COLLAPSED_WIDTH : widthPx;

  return (
    <div className="relative flex shrink-0 border-l border-base-border bg-base-panel" style={{ width }}>
      {!collapsed && (
        <div
          onPointerDown={onResizeDown}
          className="absolute left-0 top-0 z-20 h-full w-[10px] -translate-x-1/2 cursor-col-resize bg-base-border/40 hover:bg-signal/40"
        />
      )}

      <div className="flex h-full min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-1 border-b border-base-border px-2 py-1">
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="rounded px-1 py-0.5 font-mono text-xs text-text-muted hover:bg-base-bg hover:text-text-primary"
            title={collapsed ? "Expand Backtest Results panel" : "Collapse Backtest Results panel"}
          >
            {collapsed ? "«" : "»"}
          </button>
          {!collapsed && <span className="font-mono text-xs font-semibold text-text-primary">Backtest Results</span>}
        </div>

        {!collapsed && <BacktestResultsBody />}
      </div>
    </div>
  );
}
