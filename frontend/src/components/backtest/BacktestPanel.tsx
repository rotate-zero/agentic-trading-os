import { useMemo, useRef, useState } from "react";
import { useBacktestRun } from "../../hooks/useBacktestRun";
import {
  BACKTEST_SCENARIOS,
  BACKTEST_STRATEGY_NAMES,
  type DiscardedSignalWireShape,
} from "../../services/api-client";

const MIN_WIDTH = 64;
const MAX_WIDTH = 480;
const COLLAPSED_WIDTH = 36;
const DEFAULT_WIDTH = 300;

// Deliberately local component state, not wired into WorkspaceContext.tsx
// the way ScannerPanel's own scannerCollapsed/scannerWidthPx are (that
// state is per-Main-Window, persisted to localStorage, and pushed across
// browser tabs via crossTabSync.ts). This panel's own contents — a form
// plus one run's in-flight/finished state — are meaningless to persist
// or sync: a run in progress belongs to the browser tab that started it,
// not to "whichever Main Window is active," and there's no server-side
// event for a backtest run to push updates to a second tab anyway (this
// route is a synchronous HTTP call, not something OpportunityCache-style
// WebSocket state could represent). Reusing ScannerPanel's exact
// MIN_WIDTH/MAX_WIDTH/COLLAPSED_WIDTH convention (same constants, same
// resize-handle behavior, same "starts collapsed" posture) keeps the
// visual/interaction shape consistent without extending
// WorkspaceContextValue, MainWindowState's localStorage schema, or
// crossTabSync's sync payload for state that has no reason to survive a
// reload. Flagged here explicitly rather than silently deviating from
// the pattern — worth reconsidering only if a future need (e.g. showing
// a run's progress from a second tab) actually shows up.
function formatElapsed(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function DiscardedSignalRow({ signal }: { signal: DiscardedSignalWireShape }) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-base-border px-2 py-1.5 last:border-b-0">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] font-medium text-text-primary">{signal.reason}</span>
        <span className="font-mono text-[9px] text-text-muted">{signal.signal_candle_ts}</span>
      </div>
      <span className="font-mono text-[10px] text-text-muted">{signal.detail}</span>
    </div>
  );
}

function ResultsView({
  runId,
  sweepId,
  outcomesRecorded,
  discardedSignals,
}: {
  runId: string;
  sweepId: string;
  outcomesRecorded: number;
  discardedSignals: DiscardedSignalWireShape[];
}) {
  return (
    <div className="flex flex-col gap-2 border-t border-base-border p-2">
      <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 font-mono text-[10px]">
        <span className="text-text-muted">run_id</span>
        <span className="truncate text-text-primary" title={runId}>
          {runId}
        </span>
        <span className="text-text-muted">sweep_id</span>
        <span className="truncate text-text-primary" title={sweepId}>
          {sweepId}
        </span>
        <span className="text-text-muted">outcomes_recorded</span>
        {/* Neutral styling regardless of value — 0 is an honest,
            expected result for many (strategy, scenario) pairs per this
            route's own docstring (four of the seven strategies are
            structurally unreachable in any BacktestRunner replay today),
            never rendered as an error or a "nothing happened" warning. */}
        <span className="font-semibold text-text-primary">{outcomesRecorded}</span>
      </div>

      <div className="flex flex-col gap-1">
        <span className="text-[10px] uppercase tracking-wide text-text-muted">
          discarded_signals {discardedSignals.length > 0 ? `(${discardedSignals.length})` : ""}
        </span>
        {discardedSignals.length === 0 ? (
          <p className="font-mono text-[10px] text-text-muted">None.</p>
        ) : (
          <div className="rounded border border-base-border">
            {discardedSignals.map((s, i) => (
              <DiscardedSignalRow key={`${s.signal_candle_ts}-${i}`} signal={s} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function BacktestForm() {
  const { status, result, error, elapsedSeconds, run } = useBacktestRun();
  const [strategyName, setStrategyName] = useState("");
  const [scenario, setScenario] = useState("");
  const [symbol, setSymbol] = useState("");
  const symbolInputRef = useRef<HTMLInputElement>(null);

  const running = status === "running";
  const selectedScenario = useMemo(() => BACKTEST_SCENARIOS.find((sc) => sc.name === scenario), [scenario]);
  const canRun = !running && strategyName !== "" && scenario !== "" && symbol.trim() !== "";

  const handleRun = () => {
    if (!canRun) return;
    run(strategyName, symbol.trim(), scenario);
  };

  return (
    <>
      <div className="flex flex-col gap-2 p-2">
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">Strategy</span>
          <select
            value={strategyName}
            onChange={(e) => setStrategyName(e.target.value)}
            disabled={running}
            className="rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary outline-none focus:border-signal disabled:opacity-50"
          >
            <option value="">Select…</option>
            {BACKTEST_STRATEGY_NAMES.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">Scenario</span>
          <select
            value={scenario}
            onChange={(e) => setScenario(e.target.value)}
            disabled={running}
            className="rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary outline-none focus:border-signal disabled:opacity-50"
          >
            <option value="">Select…</option>
            {BACKTEST_SCENARIOS.map((sc) => (
              <option key={sc.name} value={sc.name}>
                {sc.name} (~{sc.candleCount}s)
              </option>
            ))}
          </select>
          {selectedScenario && (
            <span className="font-mono text-[9px] leading-snug text-text-muted">{selectedScenario.description}</span>
          )}
        </label>

        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">Symbol</span>
          <input
            ref={symbolInputRef}
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && handleRun()}
            disabled={running}
            placeholder="e.g. ZBTR1"
            maxLength={12}
            className="rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-xs text-text-primary placeholder:text-text-muted outline-none focus:border-signal disabled:opacity-50"
          />
          {/* Arbitrary label for this run's StrategyOutcome rows, not a
              real ticker lookup — matches the route's own param
              description verbatim rather than implying validation this
              route doesn't do. */}
          <span className="font-mono text-[9px] text-text-muted">Label only — not a real ticker lookup.</span>
        </label>

        <button
          onClick={handleRun}
          disabled={!canRun}
          className="rounded bg-signal/20 px-2 py-1 font-mono text-xs text-signal hover:bg-signal/30 disabled:opacity-40"
        >
          {running ? "Running…" : "Run Backtest"}
        </button>
        <span className="font-mono text-[9px] leading-snug text-text-muted">
          Runs serialize on the backend — only one Backtest Runner execution happens at a time in this process, so
          this panel disables itself rather than queue a second one.
        </span>
      </div>

      {running && (
        <div className="flex flex-col gap-1 border-t border-base-border px-2 py-2">
          <span className="font-mono text-xs font-semibold text-signal">Running… {formatElapsed(elapsedSeconds)} elapsed</span>
          <span className="font-mono text-[9px] text-text-muted">
            Fully synchronous — roughly 1 real second per replayed candle
            {selectedScenario ? `, ~${selectedScenario.candleCount}s typical for this scenario` : ""}. This is
            expected, not a stall.
          </span>
        </div>
      )}

      {status === "error" && error && (
        <div className="border-t border-base-border px-2 py-2">
          <p className="font-mono text-[11px] text-bear">{error}</p>
        </div>
      )}

      {status === "done" && result && (
        <ResultsView
          runId={result.run_id}
          sweepId={result.sweep_id}
          outcomesRecorded={result.outcomes_recorded}
          discardedSignals={result.discarded_signals}
        />
      )}
    </>
  );
}

export function BacktestPanel() {
  const [collapsed, setCollapsed] = useState(true); // starts collapsed, same reasoning ScannerPanel/FeatureEnginePanel already use — a fourth sidebar shouldn't grab space by default either
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
            title={collapsed ? "Expand Backtest panel" : "Collapse Backtest panel"}
          >
            {collapsed ? "«" : "»"}
          </button>
          {!collapsed && <span className="font-mono text-xs font-semibold text-text-primary">Backtest</span>}
        </div>

        {!collapsed && (
          <div className="flex-1 overflow-y-auto">
            <BacktestForm />
          </div>
        )}
      </div>
    </div>
  );
}
