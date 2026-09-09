import { useEffect, useRef, useState } from "react";
import {
  fetchOpportunities,
  subscribeSymbol,
  ApiError,
  type OpportunitiesSnapshotWireShape,
  type OpportunityWireShape,
} from "../services/api-client";
import { workspaceSocket, type WireMessage } from "../services/websocket-client";

// The normalized, display-ready shape AIAnalysisPanel actually renders.
// camelCase, flat, `symbol`/`strategy` injected from the wire response's
// own keys (see normalize() below) — same "reshape the wire union into
// something the component doesn't need to type-narrow itself" split
// useIntelligenceState.ts's FeatureTimeframe/FeatureUnit already
// establish for this codebase.
export interface Opportunity {
  symbol: string; // from the outer "symbols" key — not a field on the real Opportunity payload itself
  strategy: string; // also present on the payload's own `strategy` field; the two are expected to agree
  version: string;
  direction: "BUY" | "SELL";
  confidence: number; // 0-100
  structuralInvalidation: number;
  structuralTarget: number;
  expectedHorizonMinutes: number | null;
  // evidence.conditions/reason/basis flattened up a level — nothing else
  // on Opportunity nests this deeply, so there's no ambiguity to
  // preserve by keeping `evidence` as its own sub-object here.
  conditions: Record<string, number | string>;
  reason: string | null;
  basis: "live" | "closed" | null;
  status: "potential" | "waiting" | "actionable" | "expired";
  waitReason: string | null;
  waitExpiresAt: string | null;
  setupDetectedAt: string;
  confirmedAt: string | null;
  decidedAt: string | null;
  receivedAt: string;
}

function normalizeOne(symbol: string, strategy: string, wire: OpportunityWireShape): Opportunity {
  return {
    symbol,
    strategy,
    version: wire.version,
    direction: wire.direction,
    confidence: wire.confidence,
    structuralInvalidation: wire.structural_invalidation,
    structuralTarget: wire.structural_target,
    expectedHorizonMinutes: wire.expected_horizon_minutes ?? null,
    conditions: wire.evidence?.conditions ?? {},
    reason: wire.evidence?.reason ?? null,
    basis: wire.evidence?.basis ?? null,
    status: wire.status,
    waitReason: wire.wait_reason ?? null,
    waitExpiresAt: wire.wait_expires_at ?? null,
    setupDetectedAt: wire.setup_detected_at,
    confirmedAt: wire.confirmed_at ?? null,
    decidedAt: wire.decided_at ?? null,
    receivedAt: wire.received_at,
  };
}

// Flattens {"symbols": {ticker: {strategy: {...}}}} into a flat array —
// the equivalent of useIntelligenceState.ts's own wire.timeframes ->
// FeatureTimeframe[] flattening, for the same reason: the component
// shouldn't need to walk a nested wire dict itself.
function normalize(wire: OpportunitiesSnapshotWireShape): Opportunity[] {
  const out: Opportunity[] = [];
  for (const [symbol, byStrategy] of Object.entries(wire.symbols)) {
    for (const [strategy, opp] of Object.entries(byStrategy)) {
      out.push(normalizeOne(symbol, strategy, opp));
    }
  }
  return out;
}

/**
 * Backend read side for the AI Analysis Panel — GET /intelligence/
 * opportunities (confirmed decision #114) for the initial snapshot; live
 * updates via the "opportunity.new" WebSocket channel
 * (EventType.OPPORTUNITY_CREATED, backend/app/api/websocket/channels.py).
 *
 * Same re-fetch-on-push choice useIntelligenceState.ts already made
 * (rather than merging the WS push in piecemeal): GET /intelligence/
 * opportunities is a cheap, in-memory-only read (OpportunityCache does no
 * I/O — see its own get_snapshot() docstring), so re-fetching costs
 * nothing and avoids a second merge implementation to keep in sync with
 * this cache's own dict-write logic.
 *
 * Also calls subscribeSymbol() on mount/change, same as
 * useIntelligenceState/useLiveCandles — a symbol that isn't already
 * streaming has no FeaturesUpdated/MarketStateChanged for any strategy to
 * fire off in the first place.
 */
export function useOpportunities(symbol: string): { opportunities: Opportunity[]; loading: boolean } {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [loading, setLoading] = useState(true);
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setOpportunities([]); // clear the previous symbol's stale data immediately on switch

    subscribeSymbol(symbol).catch((err: unknown) => {
      const detail = err instanceof ApiError ? err.message : String(err);
      console.error(`useOpportunities(${symbol}): subscribe failed — ${detail}`);
    });

    const load = () => {
      fetchOpportunities(symbolRef.current)
        .then((wire) => {
          if (!cancelled) {
            setOpportunities(normalize(wire));
            setLoading(false);
          }
        })
        .catch((err: unknown) => {
          const detail = err instanceof ApiError ? err.message : String(err);
          console.error(`useOpportunities(${symbolRef.current}): fetch failed — ${detail}`);
          if (!cancelled) setLoading(false);
        });
    };

    load();

    const onUpdate = (msg: WireMessage) => {
      if (msg.symbol !== symbolRef.current) return; // one channel, many symbols — filter client-side
      load();
    };
    const unsubOpportunities = workspaceSocket.subscribe("opportunity.new", onUpdate);

    return () => {
      cancelled = true;
      unsubOpportunities();
    };
  }, [symbol]);

  return { opportunities, loading };
}
