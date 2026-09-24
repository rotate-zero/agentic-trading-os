import { useRef, useState } from "react";
import { useOrderLifecycle, type LifecycleEvent } from "../../hooks/useOrderLifecycle";

// Same collapsible-width convention ScannerPanel.tsx established and
// BacktestPanel.tsx/BacktestResultsPanel.tsx already reuse verbatim — same
// constants, same resize-handle behavior, same "starts collapsed" posture.
const MIN_WIDTH = 64;
const MAX_WIDTH = 480;
const COLLAPSED_WIDTH = 36;
const DEFAULT_WIDTH = 300;

// Deliberately local component state (collapsed/widthPx), not threaded
// through WorkspaceContext.tsx — same reasoning BacktestResultsPanel.tsx's
// own header comment gives for itself: this panel's contents are a live
// feed with no server-side "current value" to sync across tabs and no
// reason to survive a reload, so there's nothing here WorkspaceContext's
// shared state would actually buy.

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

        {!collapsed && <ExecutionLifecycleBody />}
      </div>
    </div>
  );
}
