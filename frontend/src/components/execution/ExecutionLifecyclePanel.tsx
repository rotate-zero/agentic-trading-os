import { useEffect, useRef, useState } from "react";
import { useOrderLifecycle, type LifecycleEvent } from "../../hooks/useOrderLifecycle";
import {
  fetchExecutionStartupStatus,
  fetchExitIntents,
  type ExecutionStartupStatusWireShape,
  type ExitIntentsWireShape,
} from "../../services/api-client";

// Same collapsible-width convention ScannerPanel.tsx established and
// BacktestPanel.tsx/BacktestResultsPanel.tsx already reuse verbatim — same
// constants, same resize-handle behavior, same "starts collapsed" posture.
const MIN_WIDTH = 64;
const MAX_WIDTH = 480;
const COLLAPSED_WIDTH = 36;
const DEFAULT_WIDTH = 300;

// Deliberately local component state (collapsed/widthPx), not threaded
// through WorkspaceContext.tsx — same reasoning BacktestResultsPanel.tsx's
// own header comment gives for itself. The event list is transient; the
// separate exit-intent snapshot is fetched again when this panel opens.

// Time-only, like InfoTab.tsx's formatExitTime/AIAnalysisPanel.tsx's
// formatDetectedAt (a "recent activity, today" feed, same posture) — but
// with seconds, since this feed can receive several events for the same
// symbol within one minute (plan -> decision -> approval in quick
// succession) where minute-only resolution would make them indistinguishable.
function formatTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatTriggerTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function formatNum(n: number | null, digits = 2): string {
  return n === null ? "—" : n.toFixed(digits);
}

type Tone = "bull" | "bear" | "signal" | "muted";

const TONE_CLASS: Record<Tone, string> = {
  bull: "text-bull",
  bear: "text-bear",
  signal: "text-signal",
  muted: "text-text-muted",
};

// One label + one detail line per event kind — a simple table/list is
// enough for v1 (§4.3), matching ScannerPanel's ResultRow two-line
// convention rather than inventing a new visual style.
function describeEvent(e: LifecycleEvent): { label: string; tone: Tone; detail: string } {
  switch (e.kind) {
    case "TradePlanned":
      return {
        label: "Trade Planned",
        tone: e.direction === "long" ? "bull" : "bear",
        detail: `${e.direction.toUpperCase()} entry ${formatNum(e.entry)} stop ${formatNum(e.stop)} target ${formatNum(
          e.target,
        )} size ${e.size}${e.rMultiple !== null ? ` (${formatNum(e.rMultiple)}R)` : ""}`,
      };
    case "GovernorDecision":
      return {
        label: "Governor Decision",
        tone: e.action === "rejected" ? "bear" : e.action === "approved" ? "bull" : "signal",
        detail: e.reasons.length > 0 ? `${e.action} — ${e.reasons.join(", ")}` : e.action,
      };
    case "OrderApproved":
      return {
        label: "Order Approved",
        tone: "bull",
        detail: `${e.side} ${e.qty} (${e.positionEffect}, ${e.orderType}${
          e.limitPrice !== null ? ` @ ${formatNum(e.limitPrice)}` : ""
        })`,
      };
    case "PlanRejected":
      return {
        label: "Plan Rejected",
        tone: "bear",
        detail: e.reasons.join(", ") || "no reason given",
      };
    case "OrderStatusChanged":
      return {
        label: "Order Status",
        tone: e.status === "rejected" ? "bear" : "signal",
        detail: `${e.status} — ${e.reason}${e.executionVenue ? ` (${e.executionVenue})` : ""}`,
      };
    case "OrderFilled":
      return {
        label: "Order Filled",
        tone: "bull",
        detail: `${e.side} ${e.qty} @ ${formatNum(e.fillPrice)}`,
      };
    case "PositionClosed":
      return {
        label: "Position Closed",
        tone: e.realizedPnl >= 0 ? "bull" : "bear",
        // rMultipleMissingReason deliberately not surfaced per-row: decision
        // #173 documents that no immutable risk basis exists yet, so R is
        // expected to be absent on every closure for now — showing that as
        // a note on every row would be constant noise, not information.
        // realizedProfit/realizedLoss are decision #173's separate gross
        // breakdown of the same realizedPnl figure already shown, so only
        // fees (distinct money, not part of realizedPnl) is added here.
        detail: `exit ${formatNum(e.exitPrice)}, pnl ${e.realizedPnl >= 0 ? "+" : ""}${formatNum(
          e.realizedPnl,
        )}${e.rMultipleAchieved !== null ? ` (${formatNum(e.rMultipleAchieved)}R)` : ""}${
          e.fees !== null ? `, fees ${formatNum(e.fees)}` : ""
        }`,
      };
  }
}

function EventRow({ event }: { event: LifecycleEvent }) {
  const { label, tone, detail } = describeEvent(event);
  return (
    <div className="flex flex-col gap-0.5 border-b border-base-border px-2 py-1.5">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-medium text-text-primary">{event.symbol}</span>
          <span className={`font-mono text-[10px] ${TONE_CLASS[tone]}`}>{label}</span>
        </div>
        <span className="font-mono text-[9px] text-text-muted">{formatTime(event.receivedAt)}</span>
      </div>
      <div className="pl-0 font-mono text-[10px] text-text-muted">{detail}</div>
    </div>
  );
}

function ExecutionLifecycleBody() {
  const { events } = useOrderLifecycle();

  return (
    <div className="flex-1 overflow-y-auto">
      {events.length === 0 && (
        <div className="px-2 py-3 font-mono text-[11px] text-text-muted">
          No execution activity yet — waiting on the first plan, decision, or order.
        </div>
      )}
      {events.map((e) => (
        <EventRow key={e.id} event={e} />
      ))}
    </div>
  );
}

type StartupStatusLoad =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: ExecutionStartupStatusWireShape };

const STARTUP_STATUS_LABEL: Record<ExecutionStartupStatusWireShape["status"], string> = {
  ready: "Started",
  reconciliation_blocked: "Blocked — reconciliation",
  startup_failed: "Startup failed",
  unavailable: "Unavailable",
};

const STARTUP_STATUS_TONE: Record<ExecutionStartupStatusWireShape["status"], Tone> = {
  ready: "bull",
  reconciliation_blocked: "bear",
  startup_failed: "bear",
  unavailable: "muted",
};

// One compact line, deliberately — this is a startup diagnostic, not a
// second event feed, so it gets the same manual-Refresh/loading/error/
// unavailable shape ObservedExitTriggers below already established for
// this panel rather than its own list-style section.
function StartupStatusLine() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [load, setLoad] = useState<StartupStatusLoad>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    setLoad({ kind: "loading" });
    fetchExecutionStartupStatus()
      .then((data) => {
        if (active) setLoad({ kind: "ready", data });
      })
      .catch((error: unknown) => {
        if (active) setLoad({ kind: "error", message: error instanceof Error ? error.message : "Request failed" });
      });
    return () => { active = false; };
  }, [refreshKey]);

  return (
    <section className="border-b border-base-border" aria-label="Execution pipeline startup status">
      <div className="flex items-center justify-between px-2 py-1.5">
        <h2 className="font-mono text-[11px] font-semibold text-text-primary">Startup status</h2>
        <button
          onClick={() => setRefreshKey((key) => key + 1)}
          disabled={load.kind === "loading"}
          className="rounded px-1 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg disabled:opacity-50"
        >
          Refresh
        </button>
      </div>
      {/* Startup status only — not proof a given opportunity will clear
          Governor's rules, that Portfolio State will stay ready, or that
          open positions' exits are protected (see "Observed exit triggers"
          below for that separate, also-observational surface). */}
      <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted">
        Startup status only — not confirmation an opportunity will pass Governor rules, Portfolio State stays ready, or exits are protected.
      </p>
      {load.kind === "loading" && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Checking startup status…</p>
      )}
      {load.kind === "error" && (
        <p className="px-2 pb-2 font-mono text-[10px] text-bear">Could not fetch startup status: {load.message}</p>
      )}
      {load.kind === "ready" && (
        <p className="px-2 pb-2 font-mono text-[10px]">
          <span className={TONE_CLASS[STARTUP_STATUS_TONE[load.data.status]]}>
            {STARTUP_STATUS_LABEL[load.data.status]}
          </span>
          {load.data.status === "reconciliation_blocked" && load.data.discrepancy_count !== null && (
            <span className="text-text-muted">
              {" "}
              · {load.data.discrepancy_count} discrepanc{load.data.discrepancy_count === 1 ? "y" : "ies"}
            </span>
          )}
        </p>
      )}
    </section>
  );
}

type ExitIntentLoad =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: ExitIntentsWireShape };

const EXIT_REASON_LABEL = { stop: "Stop", target: "Target", eod_flatten: "EOD" } as const;

function ObservedExitTriggers() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [load, setLoad] = useState<ExitIntentLoad>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    setLoad({ kind: "loading" });
    fetchExitIntents()
      .then((data) => {
        if (active) setLoad({ kind: "ready", data });
      })
      .catch((error: unknown) => {
        if (active) setLoad({ kind: "error", message: error instanceof Error ? error.message : "Request failed" });
      });
    return () => { active = false; };
  }, [refreshKey]);

  return (
    <section className="border-b border-base-border" aria-label="Observed exit triggers">
      <div className="flex items-center justify-between px-2 py-1.5">
        <h2 className="font-mono text-[11px] font-semibold text-text-primary">Observed exit triggers</h2>
        <button
          onClick={() => setRefreshKey((key) => key + 1)}
          disabled={load.kind === "loading"}
          className="rounded px-1 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg disabled:opacity-50"
        >
          Refresh
        </button>
      </div>
      <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted">
        Observed trigger only — no exit order has been placed and the position has not been closed.
      </p>
      {load.kind === "loading" && <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Loading exit triggers…</p>}
      {load.kind === "error" && <p className="px-2 pb-2 font-mono text-[10px] text-bear">Could not fetch exit triggers: {load.message}</p>}
      {load.kind === "ready" && load.data.monitor_status === "unavailable" && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Position Monitor unavailable.</p>
      )}
      {load.kind === "ready" && load.data.monitor_status === "running" && load.data.exit_intents.length === 0 && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Position Monitor running — no observed exit triggers.</p>
      )}
      {load.kind === "ready" && load.data.monitor_status === "running" && load.data.exit_intents.map((intent) => (
        <div key={intent.position_id} className="border-t border-base-border px-2 py-1.5 font-mono text-[10px]">
          <div className="flex flex-wrap items-center justify-between gap-1">
            <span className="text-text-primary">{intent.symbol} · {EXIT_REASON_LABEL[intent.exit_reason]}</span>
            <time className="text-text-muted" dateTime={intent.trigger_ts}>{formatTriggerTime(intent.trigger_ts)}</time>
          </div>
          <div className="text-text-muted">{intent.side} {intent.qty} · trigger {formatNum(intent.trigger_price)}</div>
        </div>
      ))}
    </section>
  );
}

export function ExecutionLifecyclePanel() {
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
            title={collapsed ? "Expand Execution panel" : "Collapse Execution panel"}
          >
            {collapsed ? "«" : "»"}
          </button>
          {!collapsed && <span className="font-mono text-xs font-semibold text-text-primary">Execution</span>}
        </div>

        {!collapsed && <StartupStatusLine />}
        {!collapsed && <ObservedExitTriggers />}
        {!collapsed && <ExecutionLifecycleBody />}
      </div>
    </div>
  );
}
