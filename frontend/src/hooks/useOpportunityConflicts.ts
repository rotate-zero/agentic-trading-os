import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  fetchOpportunityConflicts,
  subscribeSymbol,
  type OpportunityAgreementWireShape,
  type OpportunityConflictWireShape,
} from "../services/api-client";
import { workspaceSocket, type WireMessage } from "../services/websocket-client";

/**
 * Backend read side for a symbol's opportunity-agreement/conflict
 * status — GET /intelligence/opportunity-conflicts (decision #123),
 * filtered server-side to `symbol`. Same re-fetch-on-push choice
 * useOpportunities.ts already made for the identical underlying data
 * source (`OpportunityCache`, via `opportunity_view.py` — decision
 * #121): live updates via the same "opportunity.new" WebSocket channel
 * (EventType.OPPORTUNITY_CREATED), since this view is entirely derived
 * from the same cache useOpportunities already watches. Also calls
 * subscribeSymbol() on mount/change, same as useOpportunities — harmless
 * if a sibling useOpportunities(symbol) call already subscribed this
 * symbol (subscribeSymbol is idempotent server-side), and keeps this
 * hook correct on its own if it's ever used without that sibling.
 *
 * Returns at most one of `agreement`/`conflict` — a symbol with 0 or 1
 * currently-cached opportunity has neither (honest absence, matching
 * get_opportunity_conflicts()'s own "mutually exclusive, never both, and
 * never a fabricated third state" contract — see that function's
 * docstring). Never both populated at once.
 */
export function useOpportunityConflicts(symbol: string): {
  agreement: OpportunityAgreementWireShape | null;
  conflict: OpportunityConflictWireShape | null;
  loading: boolean;
} {
  const [agreement, setAgreement] = useState<OpportunityAgreementWireShape | null>(null);
  const [conflict, setConflict] = useState<OpportunityConflictWireShape | null>(null);
  const [loading, setLoading] = useState(true);
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setAgreement(null);
    setConflict(null);

    subscribeSymbol(symbol).catch((err: unknown) => {
      const detail = err instanceof ApiError ? err.message : String(err);
      console.error(`useOpportunityConflicts(${symbol}): subscribe failed — ${detail}`);
    });

    const load = () => {
      fetchOpportunityConflicts(symbolRef.current)
        .then((wire) => {
          if (!cancelled) {
            setAgreement(wire.agreements[symbolRef.current] ?? null);
            setConflict(wire.conflicts[symbolRef.current] ?? null);
            setLoading(false);
          }
        })
        .catch((err: unknown) => {
          const detail = err instanceof ApiError ? err.message : String(err);
          console.error(`useOpportunityConflicts(${symbolRef.current}): fetch failed — ${detail}`);
          if (!cancelled) setLoading(false);
        });
    };

    load();

    const onUpdate = (msg: WireMessage) => {
      if (msg.symbol !== symbolRef.current) return;
      load();
    };
    const unsubOpportunities = workspaceSocket.subscribe("opportunity.new", onUpdate);

    return () => {
      cancelled = true;
      unsubOpportunities();
    };
  }, [symbol]);

  return { agreement, conflict, loading };
}
