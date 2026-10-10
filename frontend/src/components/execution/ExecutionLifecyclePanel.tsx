import { useEffect, useRef, useState } from "react";
import { useOrderLifecycle, type LifecycleEvent } from "../../hooks/useOrderLifecycle";
import { CandidateObservation } from "./CandidateObservation";
import { RecordedAuthorizations } from "./RecordedAuthorizations";
import {
  fetchExecutionExitRequests,
  fetchExecutionFills,
  fetchExecutionOrders,
  fetchExecutionOutcomeStatus,
  fetchExecutionPositions,
  fetchExecutionStartupStatus,
  fetchExitIntents,
  type ExecutionExitRequestWireShape,
  type ExecutionExitRequestsWireShape,
  type ExecutionFillsWireShape,
  type ExecutionOrdersWireShape,
  type ExecutionOutcomeStatusTradeWireShape,
  type ExecutionOutcomeStatusWireShape,
  type ExecutionPositionWireShape,
  type ExecutionPositionsWireShape,
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
// separate exit-intent, persisted-order, and persisted-fill snapshots are
// fetched again when this panel opens (the persisted-position, recorded
// exit-request and simulated outcome-recording status snapshots too). None is merged into the WebSocket feed, and
// none is the live World View portfolio.

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

// Pure so it's directly testable without rendering — same posture
// formatTime/formatNum/describeEvent above already have in this file.
// Trims and uppercases regardless of how `rawInput` got its value (the
// input's own onChange already uppercases as-typed, same convention
// ScannerPanel.tsx's universe-add input uses, but this is the one place
// that actually forms the wire value) to match GET /execution-orders'
// exact, case-sensitive `symbol` match (decision #181's route docstring); the
// fills section shares it for GET /execution-fills' identical exact match
// (decision #183).
// Empty-after-trim clears the filter (`undefined`) rather than sending
// `symbol=`, which the backend would exact-match against literally
// nothing and return zero rows for, instead of "no filter."
function normalizeSymbolFilter(rawInput: string): string | undefined {
  const trimmed = rawInput.trim().toUpperCase();
  return trimmed === "" ? undefined : trimmed;
}

// Distinct empty-state text depending on whether a filter is active —
// "recorded yet" (nothing has ever been persisted) reads very differently
// from "none for this symbol" (other symbols may well have rows).
function emptyOrdersMessage(appliedSymbol: string | undefined): string {
  return appliedSymbol ? `No simulated orders for ${appliedSymbol}.` : "No simulated orders recorded yet.";
}

// Fills counterpart of emptyOrdersMessage — same distinction, since a filter
// that matches nothing must not read as "the ledger is empty."
function emptyFillsMessage(appliedSymbol: string | undefined): string {
  return appliedSymbol ? `No simulated fills for ${appliedSymbol}.` : "No simulated fills recorded yet.";
}

// Sign of an exact decimal string for tone only — the displayed value stays
// the server's string verbatim (never round-tripped through a number). A
// leading "-" is negative; otherwise a value with any non-zero digit in its
// mantissa is positive, so "0", "0.000000" and "-0.000000" all read as flat.
function pnlSign(value: string): "gain" | "loss" | "flat" {
  const trimmed = value.trim();
  const negative = trimmed.startsWith("-");
  const mantissa = trimmed.replace(/^[+-]/, "").split(/e/i)[0];
  if (!/[1-9]/.test(mantissa)) return "flat";
  return negative ? "loss" : "gain";
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
      <h2 className="border-b border-base-border px-2 py-1.5 font-mono text-[11px] font-semibold text-text-primary">
        WebSocket activity
      </h2>
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

// `symbol` on a settled result is the filter that request was made for. The
// section only shows a result whose `symbol` equals the currently applied
// filter, so a result for a superseded filter can never be displayed.
type ExecutionOrdersLoad =
  | { kind: "loading" }
  | { kind: "error"; symbol: string | undefined; message: string }
  | { kind: "ready"; symbol: string | undefined; data: ExecutionOrdersWireShape };

function RecentSimulatedOrders() {
  const [refreshKey, setRefreshKey] = useState(0);
  // symbolInput is the free-typed box; appliedSymbol is the filter actually
  // in effect (undefined = all symbols, the existing default). Kept apart
  // so typing doesn't refetch on every keystroke and so Refresh (which only
  // bumps refreshKey) naturally keeps whatever filter was last applied.
  const [symbolInput, setSymbolInput] = useState("");
  const [appliedSymbol, setAppliedSymbol] = useState<string | undefined>(undefined);
  const [settled, setLoad] = useState<ExecutionOrdersLoad>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    const requestedSymbol = appliedSymbol;
    setLoad({ kind: "loading" });
    fetchExecutionOrders(requestedSymbol)
      .then((data) => {
        if (active) setLoad({ kind: "ready", symbol: requestedSymbol, data });
      })
      .catch((error: unknown) => {
        if (active) {
          setLoad({ kind: "error", symbol: requestedSymbol, message: error instanceof Error ? error.message : "Request failed" });
        }
      });
    // Runs on unmount (collapse) and before every re-run (Refresh, Apply,
    // Clear): the superseded request's success or failure finds
    // active === false and is dropped, so it can never replace the newer
    // request's loading state, result or error. Refresh and the filter
    // controls stay enabled mid-flight so a hung request can be superseded.
    // The request itself is not aborted; its late response is just ignored.
    return () => { active = false; };
  }, [refreshKey, appliedSymbol]);

  // A settled result is shown only for the filter it was requested for. In
  // the render between an Apply/Clear and its effect starting the new fetch
  // (appliedSymbol already changed, `settled` not yet reset), this reads as
  // loading instead of showing the previous filter's rows or empty message.
  const load: ExecutionOrdersLoad =
    settled.kind !== "loading" && settled.symbol !== appliedSymbol ? { kind: "loading" } : settled;

  const applyFilter = () => setAppliedSymbol(normalizeSymbolFilter(symbolInput));
  const clearFilter = () => {
    setSymbolInput("");
    setAppliedSymbol(undefined);
  };
  const canClear = symbolInput !== "" || appliedSymbol !== undefined;

  return (
    <section className="border-b border-base-border" aria-label="Recent simulated orders">
      <div className="flex items-center justify-between px-2 py-1.5">
        <h2 className="font-mono text-[11px] font-semibold text-text-primary">Recent simulated orders</h2>
        <button
          onClick={() => setRefreshKey((key) => key + 1)}
          className="rounded px-1 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg"
        >
          Refresh
        </button>
      </div>
      <div className="flex items-center gap-1 border-t border-base-border px-2 py-1">
        <input
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && applyFilter()}
          placeholder="Filter symbol"
          aria-label="Filter simulated orders by symbol"
          maxLength={12}
          className="min-w-0 flex-1 rounded border border-base-border bg-base-bg px-1.5 py-0.5 font-mono text-[10px] text-text-primary placeholder:text-text-muted outline-none focus:border-signal"
        />
        <button
          onClick={applyFilter}
          className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg"
        >
          Apply
        </button>
        <button
          onClick={clearFilter}
          disabled={!canClear}
          className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:bg-base-bg disabled:opacity-50"
        >
          Clear
        </button>
      </div>
      {load.kind === "loading" && <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Loading simulated orders…</p>}
      {load.kind === "error" && <p className="px-2 pb-2 font-mono text-[10px] text-bear">Could not fetch simulated orders: {load.message}</p>}
      {load.kind === "ready" && load.data.orders.length === 0 && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">{emptyOrdersMessage(appliedSymbol)}</p>
      )}
      {load.kind === "ready" && load.data.orders.length > 0 && (
        <div className="max-h-48 overflow-y-auto border-t border-base-border">
          {load.data.orders.map((order) => (
            <div key={order.id} className="border-b border-base-border px-2 py-1.5 font-mono text-[10px] last:border-b-0">
              <div className="flex flex-wrap items-center justify-between gap-1">
                <span className="text-text-primary">{order.symbol} · {order.side} · {order.position_effect} {order.qty}</span>
                <span className={order.status === "rejected" ? "text-bear" : "text-signal"}>{order.status}</span>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-1 text-text-muted">
                <span>{order.execution_venue}</span>
                <time dateTime={order.updated_at} title={`Created ${formatTriggerTime(order.created_at)}`}>
                  {formatTriggerTime(order.updated_at)}
                </time>
              </div>
              {order.exit_reason && <div className="text-text-muted">Exit: {order.exit_reason}</div>}
              {order.reject_reason && <div className="text-bear">Rejected: {order.reject_reason}</div>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

// `symbol` on a settled result is the filter that request was made for. The
// section only shows a result whose `symbol` equals the currently applied
// filter, so a result for a superseded filter can never be displayed.
type ExecutionFillsLoad =
  | { kind: "loading" }
  | { kind: "error"; symbol: string | undefined; message: string }
  | { kind: "ready"; symbol: string | undefined; data: ExecutionFillsWireShape };

function RecentSimulatedFills() {
  const [refreshKey, setRefreshKey] = useState(0);
  // Independent of RecentSimulatedOrders' own filter state: same input/applied
  // split, deliberately not shared, so filtering one section never refetches
  // or changes the other. Refresh only bumps refreshKey, so it keeps the
  // last-applied symbol.
  const [symbolInput, setSymbolInput] = useState("");
  const [appliedSymbol, setAppliedSymbol] = useState<string | undefined>(undefined);
  const [settled, setLoad] = useState<ExecutionFillsLoad>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    const requestedSymbol = appliedSymbol;
    setLoad({ kind: "loading" });
    fetchExecutionFills(requestedSymbol)
      .then((data) => {
        if (active) setLoad({ kind: "ready", symbol: requestedSymbol, data });
      })
      .catch((error: unknown) => {
        if (active) {
          setLoad({ kind: "error", symbol: requestedSymbol, message: error instanceof Error ? error.message : "Request failed" });
        }
      });
    // Stale-response guard keyed on both refreshKey and appliedSymbol, as in
    // RecentSimulatedOrders: a response for a superseded request (newer
    // Refresh or a changed filter) is ignored, and nothing is disabled
    // mid-flight, so a hung request can always be replaced.
    return () => { active = false; };
  }, [refreshKey, appliedSymbol]);

  // Same filter-match rule as RecentSimulatedOrders: a settled result for a
  // superseded filter is never displayed.
  const load: ExecutionFillsLoad =
    settled.kind !== "loading" && settled.symbol !== appliedSymbol ? { kind: "loading" } : settled;

  const applyFilter = () => setAppliedSymbol(normalizeSymbolFilter(symbolInput));
  const clearFilter = () => {
    setSymbolInput("");
    setAppliedSymbol(undefined);
  };
  const canClear = symbolInput !== "" || appliedSymbol !== undefined;

  return (
    <section className="border-b border-base-border" aria-label="Recent simulated fills">
      <div className="flex items-center justify-between px-2 py-1.5">
        <h2 className="font-mono text-[11px] font-semibold text-text-primary">Recent simulated fills</h2>
        <button
          onClick={() => setRefreshKey((key) => key + 1)}
          className="rounded px-1 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg"
        >
          Refresh
        </button>
      </div>
      <div className="flex items-center gap-1 border-t border-base-border px-2 py-1">
        <input
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && applyFilter()}
          placeholder="Filter symbol"
          aria-label="Filter simulated fills by symbol"
          maxLength={12}
          className="min-w-0 flex-1 rounded border border-base-border bg-base-bg px-1.5 py-0.5 font-mono text-[10px] text-text-primary placeholder:text-text-muted outline-none focus:border-signal"
        />
        <button
          onClick={applyFilter}
          className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg"
        >
          Apply
        </button>
        <button
          onClick={clearFilter}
          disabled={!canClear}
          className="shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] text-text-muted hover:bg-base-bg disabled:opacity-50"
        >
          Clear
        </button>
      </div>
      {load.kind === "loading" && <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Loading simulated fills…</p>}
      {load.kind === "error" && <p className="px-2 pb-2 font-mono text-[10px] text-bear">Could not fetch simulated fills: {load.message}</p>}
      {load.kind === "ready" && load.data.fills.length === 0 && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">{emptyFillsMessage(appliedSymbol)}</p>
      )}
      {load.kind === "ready" && load.data.fills.length > 0 && (
        <div className="max-h-48 overflow-y-auto border-t border-base-border">
          {load.data.fills.map((fill) => (
            <div key={fill.ledger_seq} className="border-b border-base-border px-2 py-1.5 font-mono text-[10px] last:border-b-0">
              <div className="flex flex-wrap items-center justify-between gap-1">
                <span className="text-text-primary">{fill.symbol} · {fill.qty} @ {fill.price}</span>
                <time className="text-text-muted" dateTime={fill.venue_ts}>{formatTriggerTime(fill.venue_ts)}</time>
              </div>
              {fill.commission !== null && <div className="text-text-muted">Commission: {fill.commission}</div>}
              {fill.anomaly !== null && <div className="text-bear">Anomaly: {fill.anomaly}</div>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

type ExecutionPositionsLoad =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: ExecutionPositionsWireShape };

const POSITION_STATUS_TONE: Record<ExecutionPositionWireShape["status"], Tone> = {
  open: "signal",
  closing: "signal",
  closed: "muted",
};

function PositionRow({ position }: { position: ExecutionPositionWireShape }) {
  // A closed position with nothing held is the one case worth spelling out:
  // "qty 0" alone reads like missing data. Only both facts together get the
  // "flat" wording — a closed row with a non-zero qty (which the ledger should
  // never produce) shows its real qty and is not dressed up as flat.
  const closedFlat = position.status === "closed" && position.qty === 0;
  const sign = position.realized_pnl === null ? null : pnlSign(position.realized_pnl);
  return (
    <div
      className={`border-b border-base-border px-2 py-1.5 font-mono text-[10px] last:border-b-0 ${closedFlat ? "opacity-70" : ""}`}
      data-testid="execution-position-row"
      data-status={position.status}
    >
      <div className="flex flex-wrap items-center justify-between gap-1">
        <span className="text-text-primary">
          {position.symbol} · {position.side}
        </span>
        <span className={TONE_CLASS[POSITION_STATUS_TONE[position.status] ?? "muted"]}>{position.status}</span>
      </div>
      <div className="text-text-muted">
        {closedFlat ? "Qty 0 — closed, nothing held" : `Qty ${position.qty}`} · Avg entry {position.avg_price}
      </div>
      {(position.stop !== null || position.target !== null) && (
        <div className="text-text-muted">
          {position.stop !== null && <span>Stop {position.stop}</span>}
          {position.stop !== null && position.target !== null && <span> · </span>}
          {position.target !== null && <span>Target {position.target}</span>}
        </div>
      )}
      {position.realized_pnl !== null && sign !== null && (
        <div className={sign === "gain" ? "text-bull" : sign === "loss" ? "text-bear" : "text-text-muted"}>
          Gross realized P&amp;L: {position.realized_pnl}
        </div>
      )}
    </div>
  );
}

// Persisted `positions` rows only (GET /intelligence/execution-positions) —
// deliberately NOT the WebSocket feed below and NOT the live World View
// portfolio: no mark price, unrealized P&L or exposure exists here, and
// nothing in this section polls or merges with those surfaces. Refresh stays
// enabled while a request is in flight so a
// slow or hung request can be superseded; the effect cleanup discards the
// superseded response either way.
function RecentSimulatedPositions() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [load, setLoad] = useState<ExecutionPositionsLoad>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    setLoad({ kind: "loading" });
    fetchExecutionPositions()
      .then((data) => {
        if (active) setLoad({ kind: "ready", data });
      })
      .catch((error: unknown) => {
        if (active) setLoad({ kind: "error", message: error instanceof Error ? error.message : "Request failed" });
      });
    // Runs on unmount (collapse) and before every re-run (Refresh): the
    // superseded request's response finds active === false and is dropped.
    return () => { active = false; };
  }, [refreshKey]);

  return (
    <section className="border-b border-base-border" aria-label="Recent simulated positions">
      <div className="flex items-center justify-between px-2 py-1.5">
        <h2 className="font-mono text-[11px] font-semibold text-text-primary">Recent simulated positions</h2>
        <button
          onClick={() => setRefreshKey((key) => key + 1)}
          className="rounded px-1 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg"
        >
          Refresh
        </button>
      </div>
      <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted">
        Persisted snapshot — not the live portfolio. Qty is what is currently held; P&amp;L is gross realized, before
        commissions.
      </p>
      {load.kind === "loading" && <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Loading simulated positions…</p>}
      {load.kind === "error" && <p className="px-2 pb-2 font-mono text-[10px] text-bear">Could not fetch simulated positions: {load.message}</p>}
      {load.kind === "ready" && load.data.positions.length === 0 && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">No simulated positions recorded yet.</p>
      )}
      {load.kind === "ready" && load.data.positions.length > 0 && (
        <div className="max-h-48 overflow-y-auto border-t border-base-border">
          {load.data.positions.map((position) => (
            <PositionRow key={position.position_id} position={position} />
          ))}
        </div>
      )}
    </section>
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
          className="rounded px-1 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg"
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

// Known Position Monitor incident causes (PositionMonitor._incident, decision
// #188). Anything else is shown as its raw code so a newer backend's cause is
// never hidden or mislabelled. Rendered only as React text (never as markup).
const PROTECTION_CAUSE_LABEL: Record<string, string> = {
  snapshot_unavailable: "Position snapshot became unavailable",
  fill_invisible: "Fill not yet visible in the position snapshot",
  tick_overflow: "Tick journal overflowed for a symbol",
  symbol_capacity_loss: "Journal symbol capacity reached; a symbol's ticks were evicted",
  unowed_expiry: "Unowed ticks expired from the journal",
  fill_marker_overflow: "Fill-pending marker capacity reached",
};

const MAX_COUNT_ROWS = 20;
const MAX_INCIDENT_ROWS = 25;
const MAX_TEXT_CHARS = 64;

function causeLabel(cause: string): string {
  return PROTECTION_CAUSE_LABEL[cause] ?? cause;
}

function clipText(value: string): string {
  return value.length > MAX_TEXT_CHARS ? `${value.slice(0, MAX_TEXT_CHARS)}…` : value;
}

// A missing or unparseable timestamp renders as an em dash, never as "now".
function formatOptionalTime(value: unknown): string {
  return typeof value === "string" ? formatTriggerTime(value) : "—";
}

type ProtectionIncidentView = { cause: string; symbol: string | null; at: unknown };

// What the panel is allowed to say about protection diagnostics. Built from
// `unknown` because older backends omit the object or send only part of it;
// each field is null when it was not reported, and null is displayed as
// "not reported" — never as zero, "none" or healthy.
type ProtectionView =
  | { kind: "missing" }
  | { kind: "unavailable" }
  | {
      kind: "reported";
      degraded: boolean;
      incomplete: boolean;
      snapshotUnavailable: boolean | null;
      lostWindow: boolean | null;
      pendingSymbols: string[] | null;
      journalSymbols: number | null;
      journalTicks: number | null;
      limits: { symbols: number; ticksPerSymbol: number; retentionSeconds: number } | null;
      counts: Array<[string, number]> | null;
      incidents: ProtectionIncidentView[] | null;
    };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function countOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

// Pure so the state mapping stays separate from the markup. Contradictory input
// (status "healthy" next to a lost window, a pending fill or an unavailable
// snapshot) resolves to degraded; absent fields only mark the view incomplete.
function readProtectionDiagnostics(raw: unknown): ProtectionView {
  if (!isRecord(raw)) return { kind: "missing" };
  if (raw.status === "unavailable") return { kind: "unavailable" };
  if (raw.status !== "healthy" && raw.status !== "degraded") return { kind: "missing" };

  const snapshotUnavailable = typeof raw.snapshot_unavailable === "boolean" ? raw.snapshot_unavailable : null;
  const lostWindow = typeof raw.lost_window === "boolean" ? raw.lost_window : null;
  const pendingSymbols = Array.isArray(raw.fill_pending_symbols)
    ? raw.fill_pending_symbols.filter((symbol): symbol is string => typeof symbol === "string")
    : null;
  const journalSymbols = countOrNull(raw.journaled_symbols);
  const journalTicks = countOrNull(raw.journaled_ticks);

  let limits: { symbols: number; ticksPerSymbol: number; retentionSeconds: number } | null = null;
  if (isRecord(raw.limits)) {
    const symbols = countOrNull(raw.limits.symbols);
    const ticksPerSymbol = countOrNull(raw.limits.ticks_per_symbol);
    const retentionSeconds = countOrNull(raw.limits.unowed_retention_seconds);
    if (symbols !== null && ticksPerSymbol !== null && retentionSeconds !== null) {
      limits = { symbols, ticksPerSymbol, retentionSeconds };
    }
  }

  let counts: Array<[string, number]> | null = null;
  if (isRecord(raw.incident_counts)) {
    counts = Object.entries(raw.incident_counts)
      .filter((entry): entry is [string, number] => countOrNull(entry[1]) !== null)
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }

  let incidents: ProtectionIncidentView[] | null = null;
  if (Array.isArray(raw.recent_incidents)) {
    incidents = raw.recent_incidents
      .filter(isRecord)
      .map((item) => ({
        cause: typeof item.cause === "string" ? item.cause : "unknown",
        symbol: typeof item.symbol === "string" && item.symbol !== "" ? item.symbol : null,
        at: item.at,
      }));
  }

  const degraded =
    raw.status === "degraded" ||
    snapshotUnavailable === true ||
    lostWindow === true ||
    (pendingSymbols !== null && pendingSymbols.length > 0);
  const incomplete =
    snapshotUnavailable === null || lostWindow === null || pendingSymbols === null ||
    journalSymbols === null || journalTicks === null || counts === null;

  return {
    kind: "reported", degraded, incomplete, snapshotUnavailable, lostWindow, pendingSymbols,
    journalSymbols, journalTicks, limits, counts, incidents,
  };
}

function ProtectionDiagnosticsSummary({ view }: { view: ProtectionView }) {
  if (view.kind === "missing") {
    return (
      <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted" data-testid="protection-diagnostics-missing">
        Protection diagnostics were not reported by this backend, so protection status is unknown.
      </p>
    );
  }
  if (view.kind === "unavailable") {
    return (
      <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted" data-testid="protection-diagnostics-unavailable">
        Protection diagnostics unavailable — no Position Monitor figures to show.
      </p>
    );
  }

  const shownCounts = view.counts === null ? [] : view.counts.slice(0, MAX_COUNT_ROWS);
  const hiddenCounts = view.counts === null ? 0 : view.counts.length - shownCounts.length;
  const newestIncidents = view.incidents === null ? [] : view.incidents.slice(-MAX_INCIDENT_ROWS).reverse();
  const hiddenIncidents = view.incidents === null ? 0 : view.incidents.length - newestIncidents.length;

  return (
    <div className="px-2 pb-1.5 font-mono text-[10px] text-text-muted" data-testid="protection-diagnostics">
      <div data-testid="protection-status" className={view.degraded ? "text-bear" : "text-text-primary"}>
        {view.degraded
          ? "Protection diagnostics: degraded"
          : "Protection diagnostics: no degradation reported in this snapshot"}
        {!view.degraded && view.incomplete && " (some fields not reported)"}
      </div>
      {!view.degraded && (
        <div>A snapshot with no degradation is not a guarantee that positions are protected.</div>
      )}
      <div data-testid="protection-snapshot">
        Position snapshot:{" "}
        {view.snapshotUnavailable === null
          ? "not reported"
          : view.snapshotUnavailable
            ? "unavailable — retained ticks await recovery"
            : "available"}
      </div>
      <div data-testid="protection-pending">
        Fills awaiting visibility:{" "}
        {view.pendingSymbols === null
          ? "not reported"
          : view.pendingSymbols.length === 0
            ? "none"
            : view.pendingSymbols.map(clipText).join(", ")}
      </div>
      <div data-testid="protection-journal">
        Tick journal:{" "}
        {view.journalSymbols === null || view.journalTicks === null
          ? "not reported"
          : `${view.journalTicks} tick${view.journalTicks === 1 ? "" : "s"} across ${view.journalSymbols} symbol${view.journalSymbols === 1 ? "" : "s"}`}
        {" · "}
        {view.limits === null
          ? "limits not reported"
          : `limits ${view.limits.symbols} symbols, ${view.limits.ticksPerSymbol} ticks per symbol, ${view.limits.retentionSeconds}s retention for unowed ticks`}
      </div>
      <div
        data-testid="protection-lost-window"
        className={view.lostWindow === true ? "text-bear" : undefined}
      >
        Retained price history:{" "}
        {view.lostWindow === true
          ? "lost at some point and this stays flagged even after the snapshot recovers. First-touch certainty cannot be established for positions affected by the loss. This is not evidence that an order failed or that a position is unprotected."
          : view.lostWindow === false
            ? "no loss recorded by this monitor"
            : "unknown (not reported)"}
      </div>
      <div data-testid="protection-incident-counts">
        Incidents (cumulative):{" "}
        {view.counts === null ? (
          "not reported"
        ) : view.counts.length === 0 ? (
          "none recorded"
        ) : (
          <ul className="mt-0.5 space-y-0.5">
            {shownCounts.map(([cause, count]) => (
              <li key={cause} title={cause} className="break-all">
                {count} × {clipText(causeLabel(cause))}
              </li>
            ))}
            {hiddenCounts > 0 && <li>+{hiddenCounts} more cause{hiddenCounts === 1 ? "" : "s"}</li>}
          </ul>
        )}
      </div>
      {view.incidents !== null && view.incidents.length > 0 && (
        <details className="mt-0.5" data-testid="protection-recent-incidents">
          <summary className="cursor-pointer text-signal">Recent incidents ({view.incidents.length})</summary>
          <ul className="mt-0.5 space-y-0.5">
            {newestIncidents.map((incident, index) => (
              <li key={index} className="break-all" title={incident.cause}>
                <time>{formatOptionalTime(incident.at)}</time>
                {" · "}
                {incident.symbol === null ? "no symbol" : clipText(incident.symbol)}
                {" · "}
                {clipText(causeLabel(incident.cause))}
              </li>
            ))}
            {hiddenIncidents > 0 && <li>{hiddenIncidents} older retained incident{hiddenIncidents === 1 ? "" : "s"} not shown</li>}
          </ul>
        </details>
      )}
    </div>
  );
}

// "Observed exit triggers" is a read of the running Position Monitor's
// in-memory observations plus its additive protection diagnostics (decisions
// #178, #188) from the single GET /intelligence/exit-intents request. Neither
// says anything about execution progress: recorded exit requests, orders and
// fills have their own sections. Manual Refresh only; the effect cleanup
// discards a superseded or post-unmount response, and Refresh stays enabled
// while a request is in flight. Diagnostics render independently of the trigger
// list: they can be degraded while there are zero observed triggers.
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

  const protection: ProtectionView | null =
    load.kind === "ready" && load.data.monitor_status === "running"
      ? readProtectionDiagnostics(load.data.protection_diagnostics)
      : null;
  const priceHistoryAtRisk =
    protection !== null &&
    protection.kind === "reported" &&
    (protection.lostWindow === true || protection.snapshotUnavailable === true);

  return (
    <section className="border-b border-base-border" aria-label="Observed exit triggers">
      <div className="flex items-center justify-between px-2 py-1.5">
        <h2 className="font-mono text-[11px] font-semibold text-text-primary">Observed exit triggers</h2>
        <button
          onClick={() => setRefreshKey((key) => key + 1)}
          className="rounded px-1 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg"
        >
          Refresh
        </button>
      </div>
      <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted">
        Observed triggers are monitor observations. Check recorded exit requests, orders and fills for execution progress.
      </p>
      {load.kind === "loading" && <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Loading exit triggers…</p>}
      {load.kind === "error" && <p className="px-2 pb-2 font-mono text-[10px] text-bear">Could not fetch exit triggers: {load.message}</p>}
      {load.kind === "ready" && load.data.monitor_status === "unavailable" && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Position Monitor unavailable.</p>
      )}
      {protection !== null && <ProtectionDiagnosticsSummary view={protection} />}
      {load.kind === "ready" && load.data.monitor_status === "running" && load.data.exit_intents.length === 0 && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">
          Position Monitor running — no observed exit triggers.
          {priceHistoryAtRisk && " Lost or unavailable price history means a touch may not have been observed."}
        </p>
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

// Stored EOD placement window, expiry and first fallback of one `eod_flatten`
// request (decision #185). Renders only what the row stores: no clock is read, so
// a missing `eod_expired_at` is never turned into "open" or "expired" (Execution
// records expiry lazily), and no order status is implied. A null field from an
// older backend renders as "not recorded" rather than being guessed.
function EodRequestDetail({ request }: { request: ExecutionExitRequestWireShape }) {
  const fallbackLabel = request.fallback_reason
    ? (EXIT_REASON_LABEL[request.fallback_reason] ?? request.fallback_reason)
    : null;
  return (
    <div className="mt-0.5 border-l border-base-border pl-1.5" data-testid="execution-exit-request-eod">
      <div className="text-text-muted">
        EOD placement window{" "}
        {request.eod_flatten_at && request.eod_close_at ? (
          <>
            <time dateTime={request.eod_flatten_at}>{formatTriggerTime(request.eod_flatten_at)}</time>
            {" → "}
            <time dateTime={request.eod_close_at}>{formatTriggerTime(request.eod_close_at)}</time>
          </>
        ) : (
          "not recorded"
        )}
      </div>
      <div className="text-text-muted" data-testid="execution-exit-request-eod-expiry">
        {request.eod_expired_at ? (
          <>
            Placement eligibility ended{" "}
            <time dateTime={request.eod_expired_at}>{formatTriggerTime(request.eod_expired_at)}</time>
          </>
        ) : (
          "No expiry recorded"
        )}
      </div>
      <div className="text-text-muted" data-testid="execution-exit-request-fallback">
        {fallbackLabel && request.fallback_trigger_price && request.fallback_trigger_ts ? (
          <>
            First {fallbackLabel.toLowerCase()} observation stored: price {request.fallback_trigger_price} at{" "}
            <time dateTime={request.fallback_trigger_ts}>{formatTriggerTime(request.fallback_trigger_ts)}</time>
          </>
        ) : (
          "No stop or target fallback stored"
        )}
      </div>
    </div>
  );
}

type ExitRequestsLoad =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: ExecutionExitRequestsWireShape };

// Durable `exit_requests` rows (GET /intelligence/execution-exit-requests,
// decision #184) — deliberately NOT the in-memory "Observed exit triggers"
// section above (different endpoint, different population, survives restart)
// and NOT orders or fills (no order status or fill is read or implied here).
// Same manual-Refresh shape as RecentSimulatedPositions: Refresh stays
// enabled while a request is in flight so a slow or hung request can be
// superseded, and the effect cleanup discards the superseded response either
// way (as it does after collapse/unmount). No polling, no action buttons.
function RecordedExitRequests() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [load, setLoad] = useState<ExitRequestsLoad>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    setLoad({ kind: "loading" });
    fetchExecutionExitRequests()
      .then((data) => {
        if (active) setLoad({ kind: "ready", data });
      })
      .catch((error: unknown) => {
        if (active) setLoad({ kind: "error", message: error instanceof Error ? error.message : "Request failed" });
      });
    return () => { active = false; };
  }, [refreshKey]);

  return (
    <section className="border-b border-base-border" aria-label="Recorded exit requests">
      <div className="flex items-center justify-between px-2 py-1.5">
        <h2 className="font-mono text-[11px] font-semibold text-text-primary">Recorded exit requests</h2>
        <button
          onClick={() => setRefreshKey((key) => key + 1)}
          className="rounded px-1 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg"
        >
          Refresh
        </button>
      </div>
      <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted">
        A recorded request does not prove an order was placed or that the position is protected. Position status and
        remaining quantity are current, not as of the trigger. For an EOD request, a recorded expiry only ends
        placement eligibility; it does not show an order was cancelled or the position closed. A stored stop or target
        fallback is an observation, not a working protective order. Order status and fills are on their own views.
      </p>
      {load.kind === "loading" && <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Loading recorded exit requests…</p>}
      {load.kind === "error" && <p className="px-2 pb-2 font-mono text-[10px] text-bear">Could not fetch recorded exit requests: {load.message}</p>}
      {load.kind === "ready" && load.data.exit_requests.length === 0 && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">No exit requests recorded yet.</p>
      )}
      {load.kind === "ready" && load.data.exit_requests.length > 0 && (
        <div className="max-h-48 overflow-y-auto border-t border-base-border">
          {load.data.exit_requests.map((request) => (
            <div
              key={request.position_id}
              className="border-b border-base-border px-2 py-1.5 font-mono text-[10px] last:border-b-0"
              data-testid="execution-exit-request-row"
            >
              <div className="flex flex-wrap items-center justify-between gap-1">
                <span className="text-text-primary">{request.symbol} · {EXIT_REASON_LABEL[request.exit_reason] ?? request.exit_reason}</span>
                <time className="text-text-muted" dateTime={request.trigger_ts}>{formatTriggerTime(request.trigger_ts)}</time>
              </div>
              <div className="text-text-muted">Trigger price {request.trigger_price}</div>
              <div className="text-text-muted">Position {request.position_status} · remaining qty {request.remaining_qty}</div>
              {request.exit_reason === "eod_flatten" && <EodRequestDetail request={request} />}
              {request.retry_after !== null && (
                <div className="text-text-muted">
                  Retry after <time dateTime={request.retry_after}>{formatTriggerTime(request.retry_after)}</time>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

type OutcomeStatusLoad =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; data: ExecutionOutcomeStatusWireShape };

const OUTCOME_STATUS_LIMIT = 50;

// Display classification of a stored `trades.outcome_status`. Mirrors the
// server's own bucketing so the list never disagrees with the counts: SQL NULL
// is "pending"; pending_retry / blocked / recorded are known; ANYTHING else —
// including the literal "pending", which the recorder never writes — is
// surfaced verbatim as unexpected instead of being folded into a known bucket
// (and never called "recorded").
function describeOutcomeStatus(status: string | null): { label: string; tone: Tone } {
  switch (status) {
    case null:
      return { label: "Pending", tone: "muted" };
    case "pending_retry":
      return { label: "Pending retry", tone: "signal" };
    case "blocked":
      return { label: "Blocked", tone: "bear" };
    case "recorded":
      return { label: "Recorded", tone: "bull" };
    default:
      return { label: `Unexpected status ${JSON.stringify(status)}`, tone: "bear" };
  }
}

function OutcomeStatusRow({ trade }: { trade: ExecutionOutcomeStatusTradeWireShape }) {
  const { label, tone } = describeOutcomeStatus(trade.outcome_status);
  return (
    <div
      className="border-b border-base-border px-2 py-1.5 font-mono text-[10px] last:border-b-0"
      data-testid="execution-outcome-status-row"
    >
      <div className="flex flex-wrap items-center justify-between gap-1">
        <span className="text-text-primary">{trade.symbol} · {trade.strategy_name}</span>
        <time className="text-text-muted" dateTime={trade.updated_at}>{formatTriggerTime(trade.updated_at)}</time>
      </div>
      <div className="text-text-muted">
        <span className={TONE_CLASS[tone]}>{label}</span>
        {" · "}
        {trade.outcome_id !== null ? "outcome linked" : "no outcome link"}
      </div>
    </div>
  );
}

// Progress of the simulated OutcomeRecorder (decision #186) over closed
// simulated AUTO trades (GET /intelligence/execution-outcome-status). Not a
// portfolio, not a real-money result, and NOT the Info tab's "Recent Closed
// Trades" (that reads the outcome rows themselves; this reads recording status
// on the trade). Same manual-Refresh shape as RecordedExitRequests: Refresh
// stays enabled while a request is in flight so a slow or hung request can be
// superseded, and the effect cleanup discards the superseded response either
// way (as it does after collapse/unmount). No polling, no action buttons.
function SimulatedOutcomeRecording() {
  const [refreshKey, setRefreshKey] = useState(0);
  const [load, setLoad] = useState<OutcomeStatusLoad>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    setLoad({ kind: "loading" });
    fetchExecutionOutcomeStatus(OUTCOME_STATUS_LIMIT)
      .then((data) => {
        if (active) setLoad({ kind: "ready", data });
      })
      .catch((error: unknown) => {
        if (active) setLoad({ kind: "error", message: error instanceof Error ? error.message : "Request failed" });
      });
    return () => { active = false; };
  }, [refreshKey]);

  const ready = load.kind === "ready" ? load.data : null;
  const total = ready
    ? ready.counts.pending + ready.counts.pending_retry + ready.counts.blocked + ready.counts.recorded + ready.counts.other
    : 0;

  return (
    <section className="border-b border-base-border" aria-label="Simulated outcome recording">
      <div className="flex items-center justify-between px-2 py-1.5">
        <h2 className="font-mono text-[11px] font-semibold text-text-primary">Simulated outcome recording</h2>
        <button
          onClick={() => setRefreshKey((key) => key + 1)}
          className="rounded px-1 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg"
        >
          Refresh
        </button>
      </div>
      <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted">
        Closed simulated auto trades only — not a live portfolio or a real-money result. Pending may still be
        recovered by the recorder. A blocked reason is in the server logs, not this API. Counts cover every such
        trade; the list shows the most recently changed. Time is when the trade record last changed. Loaded on
        expansion and Refresh, not a live feed.
      </p>
      {load.kind === "loading" && <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Loading outcome recording status…</p>}
      {load.kind === "error" && <p className="px-2 pb-2 font-mono text-[10px] text-bear">Could not fetch outcome recording status: {load.message}</p>}
      {ready && total === 0 && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">No closed simulated auto trades yet.</p>
      )}
      {ready && total > 0 && (
        <>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 px-2 pb-1.5 font-mono text-[10px]" data-testid="execution-outcome-status-counts">
            <span className={TONE_CLASS.muted}>Pending {ready.counts.pending}</span>
            <span className={TONE_CLASS.signal}>Pending retry {ready.counts.pending_retry}</span>
            <span className={TONE_CLASS.bear}>Blocked {ready.counts.blocked}</span>
            <span className={TONE_CLASS.bull}>Recorded {ready.counts.recorded}</span>
            <span className={ready.counts.other > 0 ? TONE_CLASS.bear : TONE_CLASS.muted}>Other {ready.counts.other}</span>
          </div>
          {ready.trades.length < total && (
            <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted">
              Showing the {ready.trades.length} most recently changed of {total}.
            </p>
          )}
          <div className="max-h-48 overflow-y-auto border-t border-base-border">
            {ready.trades.map((trade) => (
              <OutcomeStatusRow key={trade.trade_id} trade={trade} />
            ))}
          </div>
        </>
      )}
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
        {!collapsed && <CandidateObservation />}
        {!collapsed && <RecordedAuthorizations />}
        {!collapsed && <RecordedExitRequests />}
        {!collapsed && <RecentSimulatedOrders />}
        {!collapsed && <RecentSimulatedFills />}
        {!collapsed && <RecentSimulatedPositions />}
        {!collapsed && <SimulatedOutcomeRecording />}
        {!collapsed && <ExecutionLifecycleBody />}
      </div>
    </div>
  );
}
