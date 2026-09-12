import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  fetchExpectancyBySessionType,
  fetchWinRateByHour,
  type HourlyWinRateWireShape,
  type PerformanceAnalyticsFilters,
  type SessionTypeExpectancyWireShape,
} from "../services/api-client";

/**
 * Backend read side for the "Strategy Performance" section — GET
 * /intelligence/win-rate-by-hour + GET /intelligence/expectancy-by-
 * session-type (decision #127). Global, not symbol-scoped (neither
 * route has a `symbol` filter), so — like useStrategyOutcomes — this
 * hook takes no `symbol` argument.
 *
 * One combined hook, not two, deliberately: both routes back a single
 * UI section ("Strategy Performance") with one loading/error state, and
 * `usePerformanceAnalytics` was this task's own suggested name for
 * exactly that combined responsibility — no existing hook already owns
 * it (checked directly against frontend/src/hooks/ before adding this
 * file). The two underlying fetches still hit two separate endpoints
 * and are still exposed as two separate wrapper functions in
 * api-client.ts, matching the query layer's own two-functions
 * granularity — this hook is the one place they're joined for display.
 *
 * Deliberately one-shot on mount, NOT polled and NOT WebSocket-driven —
 * same reasoning useStrategyOutcomes.ts already established for
 * `strategy_outcomes` itself: no event is published when a row is
 * written to that table (no live Execution Engine exists yet), and
 * these two routes are aggregate `GROUP BY` reads over that same table,
 * so there is equally no event to react to here. Subscribing to a
 * channel that never fires would be dead code dressed up as a refresh
 * strategy. `refetch()` is exposed for the same "whenever a real
 * trigger eventually exists" reason useStrategyOutcomes.ts's own
 * `refetch()` is.
 *
 * Unlike useStrategyOutcomes/useOpportunityConflicts/useContextSnapshot
 * — none of which expose a caller-visible error state, all three
 * collapse a fetch failure into `console.error` + an empty/default
 * result — this hook DOES distinguish `error` from genuinely empty
 * data. That's a deliberate, first-of-its-kind deviation from those
 * hooks' exact shape, not an oversight: collapsing the two here would
 * mean a backend outage renders identically to "no real
 * StrategyOutcome data exists yet," which is exactly the honest-empty-
 * state guarantee this section is supposed to uphold (a real API
 * failure is not "no data yet"). `winRateByHour`/`sessionExpectancy`
 * are only ever populated on a genuinely successful response; a failed
 * fetch clears them to `[]` and sets `error` instead, so the UI can
 * render an explicit error state rather than a silently-empty one.
 */
export function usePerformanceAnalytics(filters?: PerformanceAnalyticsFilters): {
  winRateByHour: HourlyWinRateWireShape[];
  sessionExpectancy: SessionTypeExpectancyWireShape[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
} {
  const [winRateByHour, setWinRateByHour] = useState<HourlyWinRateWireShape[]>([]);
  const [sessionExpectancy, setSessionExpectancy] = useState<SessionTypeExpectancyWireShape[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const strategyName = filters?.strategyName;
  const strategyVersion = filters?.strategyVersion;
  const isBacktest = filters?.isBacktest;

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const activeFilters: PerformanceAnalyticsFilters = { strategyName, strategyVersion, isBacktest };

    Promise.all([fetchWinRateByHour(activeFilters), fetchExpectancyBySessionType(activeFilters)])
      .then(([winRate, expectancy]) => {
        if (cancelled) return;
        setWinRateByHour(winRate.hourly_win_rates);
        setSessionExpectancy(expectancy.session_expectancy);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`usePerformanceAnalytics: fetch failed — ${detail}`);
        // Cleared rather than left stale — a failed refetch shouldn't
        // silently keep showing a previous success's numbers under a
        // new error state.
        setWinRateByHour([]);
        setSessionExpectancy([]);
        setError(detail);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [strategyName, strategyVersion, isBacktest]);

  useEffect(() => load(), [load]);

  return { winRateByHour, sessionExpectancy, loading, error, refetch: load };
}
