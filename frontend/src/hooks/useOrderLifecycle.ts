import { useEffect, useRef, useState } from "react";
import { workspaceSocket, type WireMessage } from "../services/websocket-client";

// ---------------------------------------------------------------------------
// Wire shapes — mirror backend/app/schemas/events/execution.py exactly
// (snake_case, as JSON puts them on the wire). All seven now have real,
// already-built Pydantic models: GovernorDecision/OrderApproved/
// PlanRejected/OrderFilled predate this task; TradePlanned/OrderStatusChanged
// were made real by decision #171; `PositionClosed` was added by decision
// #173 (`portfolio-state-engine`), after this hook was first written against
// a docs-only field sketch — confirmed by direct diff against execution.py
// that all five originally-guessed fields (position_id/exit_price/
// realized_pnl/r_multiple_achieved/closed_ts) match the real model exactly;
// `PositionClosedWire` below was updated to add the model's further optional
// fields (r_multiple_missing_reason, trade/mode/venue, and the separate
// profit/loss/fee breakdown) once they existed to mirror. It still cannot
// arrive on `main` today — see useOrderLifecycle's own docstring below for
// why — so it's still exercised only against fabricated messages.
// ---------------------------------------------------------------------------

interface GovernorDecisionWire {
  action: "approved" | "approved_reduced" | "delayed" | "watch_only" | "rejected";
  size_multiplier: number | null;
  delay_seconds: number | null;
  reasons: string[];
}

interface OrderApprovedWire {
  order_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  order_type: string;
  limit_price: number | null;
  position_effect: "open" | "close";
}

interface PlanRejectedWire {
  symbol: string;
  reasons: string[];
}

interface TradePlannedWire {
  direction: "long" | "short";
  entry: number;
  stop: number;
  target: number | null;
  size: number;
  r_multiple: number | null;
  max_hold_seconds: number | null;
  scaling_plan: string[] | null;
  trailing_stop_rule: string | null;
  origin: "auto" | "manual";
  corroboration: string[];
}

interface OrderStatusChangedWire {
  order_id: string;
  // Typed as `string`, not backend's current Literal["rejected"] — this
  // delivery only ever publishes "rejected" today (fill processing isn't
  // built, §6.3), but the field is documented to widen (submitted,
  // partially_filled, filled, cancelled) once it does; narrowing here would
  // just mean re-editing this file for a backend change that adds no new
  // frontend field, only a new value of an existing one.
  status: string;
  reason: string;
  execution_venue: string | null;
}

interface OrderFilledWire {
  order_id: string;
  side: "BUY" | "SELL";
  qty: number;
  fill_price: number;
  fill_ts: string;
}

// Mirrors execution.py's real PositionClosed model exactly (decision #173,
// `portfolio-state-engine`) — position_id/exit_price/realized_pnl/
// r_multiple_achieved/closed_ts are the five fields this hook originally
// guessed from system-design.md §10.3's sketch before a real model existed;
// all five matched on arrival, confirmed by direct diff. The rest are new:
// r_multiple_missing_reason explains why R is (for now, always) null — the
// backend has no immutable planned-risk basis yet and deliberately never
// divides by the current stop; trade_id/execution_mode/execution_venue and
// the profit/loss/fee breakdown are separate from realized_pnl (lifetime
// gross P&L), not a decomposition of it.
interface PositionClosedWire {
  position_id: string;
  exit_price: number;
  realized_pnl: number;
  r_multiple_achieved: number | null;
  closed_ts: string;
  r_multiple_missing_reason: string | null;
  trade_id: string | null;
  execution_mode: string | null;
  execution_venue: string | null;
  realized_profit: number | null;
  realized_loss: number | null;
  fees: number | null;
  reported_fees: number | null;
  unknown_fee_count: number | null;
}

// ---------------------------------------------------------------------------
// Normalized, display-ready shape — camelCase, one discriminated union on
// `kind`, same "reshape the wire union into something the component doesn't
// need to type-narrow itself" split useOpportunities.ts's
// OpportunityWireShape -> Opportunity already establishes for this codebase.
// ---------------------------------------------------------------------------

export type LifecycleEventKind =
  | "TradePlanned"
  | "GovernorDecision"
  | "OrderApproved"
  | "PlanRejected"
  | "OrderStatusChanged"
  | "OrderFilled"
  | "PositionClosed";

interface LifecycleEventCommon {
  // Synthetic — none of these seven payloads carries an id field common to
  // all of them (order_id/position_id only appear on some), so this is a
  // client-side monotonic counter, unique per received message, used only
  // as a React list key.
  id: string;
  kind: LifecycleEventKind;
  // From envelope.symbol, which every publisher of these events sets today
  // (governor/engine.py, execution_engine/engine.py both pass symbol=...
  // on every publish call) — typed as possibly-missing anyway since
  // WireMessage.symbol is optional on the wire and nothing here should
  // assume a backend invariant it doesn't itself enforce.
  symbol: string;
  // From envelope.timestamp; falls back to the moment this client received
  // the message if the envelope's own timestamp is missing or unparsable —
  // labelled as a fallback nowhere in the UI, since the two are within
  // network latency of each other in practice.
  receivedAt: string;
}

export interface TradePlannedEvent extends LifecycleEventCommon {
  kind: "TradePlanned";
  direction: "long" | "short";
  entry: number;
  stop: number;
  target: number | null;
  size: number;
  rMultiple: number | null;
  origin: "auto" | "manual";
}

export interface GovernorDecisionEvent extends LifecycleEventCommon {
  kind: "GovernorDecision";
  action: GovernorDecisionWire["action"];
  sizeMultiplier: number | null;
  delaySeconds: number | null;
  reasons: string[];
}

export interface OrderApprovedEvent extends LifecycleEventCommon {
  kind: "OrderApproved";
  orderId: string;
  side: "BUY" | "SELL";
  qty: number;
  orderType: string;
  limitPrice: number | null;
  positionEffect: "open" | "close";
}

export interface PlanRejectedEvent extends LifecycleEventCommon {
  kind: "PlanRejected";
  reasons: string[];
}

export interface OrderStatusChangedEvent extends LifecycleEventCommon {
  kind: "OrderStatusChanged";
  orderId: string;
  status: string;
  reason: string;
  executionVenue: string | null;
}

export interface OrderFilledEvent extends LifecycleEventCommon {
  kind: "OrderFilled";
  orderId: string;
  side: "BUY" | "SELL";
  qty: number;
  fillPrice: number;
  fillTs: string;
}

export interface PositionClosedEvent extends LifecycleEventCommon {
  kind: "PositionClosed";
  positionId: string;
  exitPrice: number;
  realizedPnl: number;
  rMultipleAchieved: number | null;
  closedTs: string;
  rMultipleMissingReason: string | null;
  realizedProfit: number | null;
  realizedLoss: number | null;
  fees: number | null;
}

export type LifecycleEvent =
  | TradePlannedEvent
  | GovernorDecisionEvent
  | OrderApprovedEvent
  | PlanRejectedEvent
  | OrderStatusChangedEvent
  | OrderFilledEvent
  | PositionClosedEvent;

const DEFAULT_MAX_EVENTS = 200;

function normalizeOne(msg: WireMessage, mintId: () => string): LifecycleEvent | null {
  const eventType = msg.event_type;
  const payload = msg.payload;
  if (!eventType || !payload) return null; // "_meta" subscribe/unsubscribe acks land here too — not lifecycle events

  const common = {
    id: mintId(),
    symbol: msg.symbol ?? "—",
    receivedAt: msg.timestamp ?? new Date().toISOString(),
  };

  switch (eventType) {
    case "TradePlanned": {
      const w = payload as unknown as TradePlannedWire;
      return {
        ...common,
        kind: "TradePlanned",
        direction: w.direction,
        entry: w.entry,
        stop: w.stop,
        target: w.target ?? null,
        size: w.size,
        rMultiple: w.r_multiple ?? null,
        origin: w.origin,
      };
    }
    case "GovernorDecision": {
      const w = payload as unknown as GovernorDecisionWire;
      return {
        ...common,
        kind: "GovernorDecision",
        action: w.action,
        sizeMultiplier: w.size_multiplier ?? null,
        delaySeconds: w.delay_seconds ?? null,
        reasons: w.reasons ?? [],
      };
    }
    case "OrderApproved": {
      const w = payload as unknown as OrderApprovedWire;
      return {
        ...common,
        kind: "OrderApproved",
        orderId: w.order_id,
        side: w.side,
        qty: w.qty,
        orderType: w.order_type,
        limitPrice: w.limit_price ?? null,
        positionEffect: w.position_effect,
      };
    }
    case "PlanRejected": {
      const w = payload as unknown as PlanRejectedWire;
      return {
        ...common,
        kind: "PlanRejected",
        reasons: w.reasons ?? [],
      };
    }
    case "OrderStatusChanged": {
      const w = payload as unknown as OrderStatusChangedWire;
      return {
        ...common,
        kind: "OrderStatusChanged",
        orderId: w.order_id,
        status: w.status,
        reason: w.reason,
        executionVenue: w.execution_venue ?? null,
      };
    }
    case "OrderFilled": {
      const w = payload as unknown as OrderFilledWire;
      return {
        ...common,
        kind: "OrderFilled",
        orderId: w.order_id,
        side: w.side,
        qty: w.qty,
        fillPrice: w.fill_price,
        fillTs: w.fill_ts,
      };
    }
    case "PositionClosed": {
      const w = payload as unknown as PositionClosedWire;
      return {
        ...common,
        kind: "PositionClosed",
        positionId: w.position_id,
        exitPrice: w.exit_price,
        realizedPnl: w.realized_pnl,
        rMultipleAchieved: w.r_multiple_achieved ?? null,
        closedTs: w.closed_ts,
        rMultipleMissingReason: w.r_multiple_missing_reason ?? null,
        realizedProfit: w.realized_profit ?? null,
        realizedLoss: w.realized_loss ?? null,
        fees: w.fees ?? null,
      };
    }
    default:
      return null; // an event type reaching "orders.status" this hook doesn't (yet) know how to render
  }
}

/**
 * Live execution-lifecycle feed — the first frontend consumer of any of
 * TradePlanned/OrderApproved/PlanRejected/GovernorDecision/
 * OrderStatusChanged (decision #171 made these real, critical-lane events
 * for the first time; this task, decision #174, is what
 * `execution-engine-design.md` §8 names as the thing that now unblocks).
 *
 * Deliberately NOT the useOpportunities.ts re-fetch-on-push pattern: there
 * is no REST endpoint backing this (no new one is in scope, §4.2) and
 * nothing to re-fetch — this is a live feed, not a queryable history, so
 * every WS message IS the update, kept in a bounded, reverse-chronological,
 * in-memory list. Subscribes once to "orders.status" for the whole app
 * (not scoped to a symbol/subscribeSymbol() the way market-data hooks are —
 * these are global lifecycle events, not per-symbol streaming state).
 *
 * OrderFilled and PositionClosed cannot arrive today, though for different
 * reasons than when this hook was first written. OrderFilled: unchanged —
 * nothing in the backend publishes it yet (fill processing isn't built,
 * decision #171's own "Not done" section), re-confirmed by grep against the
 * current tree. PositionClosed: it now HAS a real payload model and joined
 * envelope.py's CRITICAL_EVENT_TYPES (decision #173, `portfolio-state-engine`)
 * — the worker that would publish it exists and is unit-tested — but that
 * decision's own words are explicit that "no production implementation of
 * this Protocol ships here" and the "PositionLedgerPort adapter / startup /
 * outcome recovery" remain "not wired," so nothing in a running system
 * instantiates that worker yet. Both cases are implemented and exercised
 * here against fabricated WS messages only (see TESTING.md) so the panel
 * doesn't need a second wiring pass once either actually starts publishing.
 */
export function useOrderLifecycle(maxEvents: number = DEFAULT_MAX_EVENTS): { events: LifecycleEvent[] } {
  const [events, setEvents] = useState<LifecycleEvent[]>([]);
  const nextId = useRef(0);

  useEffect(() => {
    const onMessage = (msg: WireMessage) => {
      const normalized = normalizeOne(msg, () => `evt-${nextId.current++}`);
      if (!normalized) return;
      setEvents((prev) => [normalized, ...prev].slice(0, maxEvents));
    };

    const unsubscribe = workspaceSocket.subscribe("orders.status", onMessage);
    return unsubscribe;
  }, [maxEvents]);

  return { events };
}
