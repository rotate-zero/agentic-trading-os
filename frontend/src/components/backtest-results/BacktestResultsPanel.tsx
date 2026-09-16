import { Fragment, useEffect, useRef, useState } from "react";
import { useBacktestOutcomes } from "../../hooks/useBacktestOutcomes";
import { useBacktestRunMeta } from "../../hooks/useBacktestRunMeta";
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

// Decision #136's own metadata (`backtests`, not `strategy_outcomes`),
// surfaced alongside this panel's existing outcome-level view rather
// than as a separate sub-view — `runId` is always `BacktestResultsBody`'s
// own `appliedRunId`, so this banner stays in lockstep with whichever
// run the filter below is currently applied to (auto-following or
// manually set) with no new shared state (see useBacktestRunMeta.ts's
// own docstring for the full reasoning). Only rendered when a specific
// run_id IS applied — "everything" (no filter) has no single run's
// metadata to show, so nothing renders in that case, the same
// honest-absence posture this panel already takes elsewhere.
function RunMetaDetail({ run }: { run: BacktestRunWireShape }) {
  const scalarFields: Array<[string, string]> = [
    ["run_id", run.run_id],
    ["sweep_id", run.sweep_id],
    ["config_hash", run.config_hash],
    ["symbol_universe", run.symbol_universe.join(", ")],
    ["data_version", run.data_version],
    ["feature_version", run.feature_version],
    ["walk_forward_fold", run.walk_forward_fold === null ? "—" : String(run.walk_forward_fold)],
    ["is_holdout", String(run.is_holdout)],
    ["created_at", formatDateTime(run.created_at)],
  ];

  return (
    <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 border-t border-base-border bg-base-bg/40 p-2 font-mono text-[10px]">
      {scalarFields.map(([label, value]) => (
        <Fragment key={label}>
          <span className="text-text-muted">{label}</span>
          <span className="truncate text-text-primary" title={value}>
            {value}
          </span>
        </Fragment>
      ))}
    </div>
  );
}

// Compact summary + expand-in-place toggle for the rest — reusing
// OutcomeRow/OutcomeDetail's own established pattern immediately above
// rather than introducing a second expand mechanism in this same file.
function RunMetaBanner({ runId }: { runId?: string }) {
  const { run, loading, error } = useBacktestRunMeta(runId);
  const [expanded, setExpanded] = useState(false);

  if (!runId) return null;

  return (
    <div className="rounded border border-base-border">
      <div className="flex items-center justify-between gap-2 px-2 py-1.5">
        {error && <span className="truncate font-mono text-[10px] text-bear">Run info: {error}</span>}
        {!error && loading && <span className="font-mono text-[10px] text-text-muted">Loading run info…</span>}
        {!error && !loading && !run && (
          <span className="truncate font-mono text-[10px] text-text-muted">
            No run metadata found for this run_id.
          </span>
        )}
        {!error && !loading && run && (
          <>
            <div className="min-w-0 truncate font-mono text-[10px] text-text-primary">
              <span className="text-text-muted">{run.strategy_name}</span> {run.strategy_version} ·{" "}
              {run.date_range_start} → {run.date_range_end} ·{" "}
              <span className="text-text-muted" title={run.sweep_id}>
                sweep {run.sweep_id.slice(0, 8)}
              </span>
            </div>
            <button
              onClick={() => setExpanded(!expanded)}
              title={expanded ? "Hide run details" : "Show run details"}
              className="shrink-0 rounded px-1 py-0.5 font-mono text-[10px] text-text-muted hover:bg-base-bg hover:text-text-primary"
            >
              {expanded ? "▾" : "▸"}
            </button>
          </>
        )}
      </div>
      {run && expanded && <RunMetaDetail run={run} />}
    </div>
  );
}

// Decision #134's own filter-mode split. `lastBacktestRunId` (shared
// WorkspaceContext state, set by BacktestPanel.tsx when a run finishes)
// is a DEFAULT, never a forced value — the task's own scope explicitly
// requires that a person can still type or clear the filter and look at
// something else, and that a run finishing elsewhere must never silently
// overwrite an in-progress manual lookup already sitting in this filter.
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

function BacktestResultsBody() {
  const { lastBacktestRunId } = useWorkspace();
  const [mode, setMode] = useState<RunIdFilterMode>("auto");
  const [runIdInput, setRunIdInput] = useState(lastBacktestRunId ?? "");
  const [appliedRunId, setAppliedRunId] = useState<string | undefined>(lastBacktestRunId ?? undefined);
  const runIdInputRef = useRef<HTMLInputElement>(null);

  // Only fires while in "auto" mode — see this function's own comment
  // block above for why "manual" deliberately stops following.
  useEffect(() => {
    if (mode !== "auto") return;
    setRunIdInput(lastBacktestRunId ?? "");
    setAppliedRunId(lastBacktestRunId ?? undefined);
  }, [lastBacktestRunId, mode]);

  const { outcomes, loading, error, refetch } = useBacktestOutcomes({
    limit: OUTCOMES_LIMIT,
    backtestRunId: appliedRunId,
  });

  const applyFilter = () => {
    const trimmed = runIdInput.trim();
    setMode("manual");
    setAppliedRunId(trimmed === "" ? undefined : trimmed);
  };

  const clearFilter = () => {
    setRunIdInput("");
    setAppliedRunId(undefined);
    setMode("manual"); // explicit "show everything" is itself a manual choice — it must stick, not silently flip back to auto on the next finished run
  };

  const followLatestRun = () => {
    setMode("auto");
    setRunIdInput(lastBacktestRunId ?? "");
    setAppliedRunId(lastBacktestRunId ?? undefined);
  };

  return (
    <>
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
            onKeyDown={(e) => e.key === "Enter" && applyFilter()}
            placeholder="optional — paste a run_id"
            className="min-w-0 flex-1 rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-[10px] text-text-primary placeholder:text-text-muted focus:border-signal focus:outline-none"
          />
          <button
            onClick={applyFilter}
            className="rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:border-signal hover:text-text-primary"
          >
            Apply
          </button>
          {appliedRunId && (
            <button
              onClick={clearFilter}
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

      <div className="flex-1 overflow-y-auto p-2">
        {appliedRunId && (
          <div className="mb-2">
            <RunMetaBanner runId={appliedRunId} />
          </div>
        )}
        {error && (
          <div className="rounded border border-bear/40 px-2 py-3 font-mono text-[11px] text-bear">
            Failed to load: {error}
          </div>
        )}
        {!error && loading && outcomes.length === 0 && (
          <p className="p-1 font-mono text-[11px] text-text-muted">Loading…</p>
        )}
        {!error && !loading && outcomes.length === 0 && (
          <p className="p-1 font-mono text-[11px] text-text-muted">
            {appliedRunId
              ? `No backtest outcomes found for run_id "${appliedRunId}".`
              : "No backtest outcomes recorded yet."}
          </p>
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
        <span className="font-mono text-[9px] text-text-muted">{outcomes.length} row{outcomes.length === 1 ? "" : "s"}</span>
      </div>
    </>
  );
}

// New sibling panel (this task) — the first thing in this codebase that
// renders `strategy_outcomes` rows written with `is_backtest=True`
// (Backtest Runner v1, decision #128) anywhere outside a `curl` call or
// a direct Postgres query. See `strategy-engine-design.md` §7's newest
// as-built note for the full read-path diagram (route → hook → panel).
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
