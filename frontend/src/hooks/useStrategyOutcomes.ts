import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchStrategyOutcomes, type StrategyOutcomeWireShape } from "../services/api-client";

// The normalized, display-ready shape the "Recent Closed Trades" section
// actually renders — same camelCase-flattening split useOpportunities.ts's
// own Opportunity type establishes. Deliberately a SUBSET of
// StrategyOutcomeWireShape's full field list, not every field: this is a
// small "what happened, when, how'd it do" summary row for a minimal UI
// section, not a full trade-detail view (structural_invalidation/
// final_stop/evidence/the four snapshots/etc. stay on the wire shape,
// unused here — nothing stops a future, real trade-detail build from
// reading them off StrategyOutcomeWireShape directly).
//
// Decision #186: `executionMode`/`executionVenue` are carried so the row
// can state what kind of execution produced it (never assumed from the
// section's own label), and `snapshotMissingReasons` is carried so a row
// whose entry/exit snapshot was unavailable can say so honestly instead
// of looking complete. It is `null` when the backend sent none.
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
  executionMode: StrategyOutcomeWireShape["execution_mode"];
  executionVenue: string;
  snapshotMissingReasons: Record<string, string> | null;
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
    executionMode: wire.execution_mode,
    executionVenue: wire.execution_venue,
    snapshotMissingReasons: wire.snapshot_missing_reasons ?? null,
  };
}

function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

/**
 * Backend read side for the "Recent Closed Trades" section — GET
 * /intelligence/strategy-outcomes (decision #123), updated for decision
 * #186. Global, not symbol-scoped (the route itself has no `symbol`
 * filter — see its own docstring), so unlike useOpportunities/
 * useOpportunityConflicts this hook takes no `symbol` argument and does
 * not call subscribeSymbol().
 *
 * Passes `isBacktest: false` to `fetchStrategyOutcomes()` explicitly
 * (decision #130) rather than omitting it and relying on the route's
 * own matching default — a deliberate divergence from
 * `usePerformanceAnalytics.ts` (which omits `isBacktest` and relies on
 * the backend default for the same effective result). This is a strict
 * either/or selector, never blended with backtest rows, and its intent
 * should be visible at this call site rather than inherited silently
 * from a backend default a future change could alter. No `isBacktest`
 * toggle is exposed on this hook's own signature — a backtest viewer is
 * `useBacktestOutcomes.ts`'s job. Since decision #186 the non-backtest
 * rows are SIMULATED execution outcomes written by the simulated
 * `OutcomeRecorder` (`executionMode: "simulated"`) — never real-money
 * trades; the UI labels them so.
 *
 * Fetch policy: one request on mount (and when `limit` changes) plus an
 * explicit, user-triggered `refetch()` (the section's Refresh button).
 * Deliberately NOT polled and NOT WebSocket-driven: no `OutcomeRecorded`-
 * shaped EventType exists in backend/app/schemas/events/ (the recorder
 * writes to Postgres and publishes nothing for this table), so a
 * subscription would never fire. That is a fact about the event catalog,
 * not about whether a writer exists — one does, as of decision #186.
 *
 * Result semantics (why the returned fields look the way they do):
 * - `error` is separate from an empty result. A failed request is never
 *   rendered as "no closed trades" — an empty `outcomes` with `error ===
 *   null` and `hasLoaded === true` is the only genuine empty state.
 * - A failed request NEVER erases rows already loaded: `outcomes` keeps
 *   the last successful result, `error` carries the failure, and
 *   `lastLoadedAt` tells the UI how old those rows are. A later success
 *   replaces the rows and clears `error`.
 * - Only the newest request may write state. Every load takes a
 *   monotonically increasing request id; a response (success OR failure)
 *   whose id is no longer the latest is dropped, so a slow older request
 *   can neither overwrite a newer refresh's rows nor resurrect a stale
 *   error. Unmount/`limit`-change cleanup also invalidates any in-flight
 *   request. (The previous version returned a cancel closure from
 *   `load()` that `refetch()` callers could never reach, so a manual
 *   refetch was not protected.)
 * - `loading` is true while ANY request is in flight, including a
 *   refresh over already-loaded rows; it does not imply the rows are
 *   absent — check `hasLoaded`/`outcomes.length` for that.
 */
export function useStrategyOutcomes(limit?: number): {
  outcomes: StrategyOutcomeRow[];
  loading: boolean;
  error: string | null;
  hasLoaded: boolean;
  lastLoadedAt: number | null;
  refetch: () => void;
} {
  const [outcomes, setOutcomes] = useState<StrategyOutcomeRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState<number | null>(null);
  const latestRequestId = useRef(0);

  const load = useCallback(() => {
    const requestId = ++latestRequestId.current;
    setLoading(true);
    fetchStrategyOutcomes(limit, /* isBacktest */ false)
      .then((wire) => {
        if (requestId !== latestRequestId.current) return;
        setOutcomes(wire.outcomes.map(normalize));
        setError(null);
        setLastLoadedAt(Date.now());
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (requestId !== latestRequestId.current) return;
        const detail = describeError(err);
        console.error(`useStrategyOutcomes: fetch failed — ${detail}`);
        setError(detail);
        setLoading(false);
      });
  }, [limit]);

  useEffect(() => {
    load();
    return () => {
      // Invalidate whatever is in flight: unmount, or `limit` changed and
      // a fresh load() is about to supersede it.
      latestRequestId.current++;
    };
  }, [load]);

  return { outcomes, loading, error, hasLoaded: lastLoadedAt !== null, lastLoadedAt, refetch: load };
}
