import { useCallback, useEffect, useState } from "react";
import { ApiError, fetchStrategyOutcomes, type StrategyOutcomeWireShape } from "../services/api-client";

// The normalized, display-ready shape the "Recent Closed Trades" section
// actually renders — same camelCase-flattening split useOpportunities.ts's
// own Opportunity type establishes. Deliberately a SUBSET of
// StrategyOutcomeWireShape's full field list, not every field: this is a
// small "what happened, when, how'd it do" summary row for a minimal UI
// section, not a full trade-detail view (structural_invalidation/
// final_stop/evidence/market_state_at_entry/etc. stay on the wire shape,
// unused here — nothing stops a future, real trade-detail build from
// reading them off StrategyOutcomeWireShape directly).
export interface StrategyOutcomeRow {
  outcomeId: string;
  symbol: string;
  strategyName: string;
  direction: "BUY" | "SELL";
  entryPrice: number;
  exitPrice: number;
  realizedPnl: number;
  realizedR: number;
  exitReason: string;
  exitFilledAt: string;
}

function normalize(wire: StrategyOutcomeWireShape): StrategyOutcomeRow {
  return {
    outcomeId: wire.outcome_id,
    symbol: wire.symbol,
    strategyName: wire.strategy_name,
    direction: wire.direction,
    entryPrice: wire.entry_price,
    exitPrice: wire.exit_price,
    realizedPnl: wire.realized_pnl,
    realizedR: wire.realized_r,
    exitReason: wire.exit_reason,
    exitFilledAt: wire.exit_filled_at,
  };
}

/**
 * Backend read side for the "Recent Closed Trades" section — GET
 * /intelligence/strategy-outcomes (decision #123). Global, not
 * symbol-scoped (the route itself has no `symbol` filter — see its own
 * docstring), so unlike useOpportunities/useOpportunityConflicts this
 * hook takes no `symbol` argument and does not call subscribeSymbol().
 *
 * Deliberately one-shot on mount, NOT re-fetch-on-WebSocket-push like
 * useOpportunities/useIntelligenceState: there is no event published
 * when a row is written to `strategy_outcomes` (no
 * `OutcomeRecorded`-shaped EventType exists anywhere in
 * backend/app/schemas/events/ — confirmed by grep, not assumed), because
 * no live caller exists yet to write one (Execution Engine/Position
 * Monitor aren't built — decision #120). Subscribing to a channel that
 * will never fire would be dead code dressed up as a refresh strategy,
 * not a real one. `refetch()` is exposed for whenever a real trigger
 * (e.g. a future Execution Engine WebSocket event, or a manual action
 * elsewhere in the UI) needs one — deliberately not wired to any new
 * button here, since no established manual-refresh affordance exists
 * yet in the surrounding UI for this to match.
 */
export function useStrategyOutcomes(limit?: number): {
  outcomes: StrategyOutcomeRow[];
  loading: boolean;
  refetch: () => void;
} {
  const [outcomes, setOutcomes] = useState<StrategyOutcomeRow[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    fetchStrategyOutcomes(limit)
      .then((wire) => {
        if (!cancelled) {
          setOutcomes(wire.outcomes.map(normalize));
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useStrategyOutcomes: fetch failed — ${detail}`);
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [limit]);

  useEffect(() => load(), [load]);

  return { outcomes, loading, refetch: load };
}
