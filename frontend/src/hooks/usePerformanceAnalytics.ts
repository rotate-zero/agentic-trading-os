import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  fetchExpectancyBySessionType,
  fetchWinRateByHour,
  type HourlyWinRateWireShape,
  type PerformanceAnalyticsFilters,
  type SessionTypeExpectancyWireShape,
} from "../services/api-client";

// Identity of a "population": the exact (isBacktest, strategyName,
// strategyVersion) triple a request was made for. Every stored result and
// every stored failure is tagged with the key it was fetched for, and the
// hook only ever RETURNS a result/failure whose key equals the key of the
// filters passed on the current render (see the hook docstring).
function populationKey(filters: PerformanceAnalyticsFilters | undefined): string {
  return JSON.stringify([filters?.isBacktest ?? null, filters?.strategyName ?? null, filters?.strategyVersion ?? null]);
}

interface LoadedResult {
  key: string;
  winRateByHour: HourlyWinRateWireShape[];
  sessionExpectancy: SessionTypeExpectancyWireShape[];
  loadedAt: number;
}

interface LoadFailure {
  key: string;
  message: string;
}

/**
 * Backend read side for the "Strategy Performance" section — GET
 * /intelligence/win-rate-by-hour + GET /intelligence/expectancy-by-
 * session-type (decision #127). Global, not symbol-scoped (neither
 * route has a `symbol` filter), so — like useStrategyOutcomes — this
 * hook takes no `symbol` argument.
 *
 * One combined hook, not two, deliberately: both routes back a single
 * UI section ("Strategy Performance") with one loading/error state.
 * The two underlying fetches still hit two separate endpoints and are
 * still exposed as two separate wrapper functions in api-client.ts;
 * this hook is the one place they're joined for display.
 *
 * Fetch policy: one request pair on mount, one whenever the population
 * (`isBacktest` / `strategyName` / `strategyVersion`) changes, plus an
 * explicit, user-triggered `refetch()` (the section's Refresh button).
 * Deliberately NOT polled and NOT WebSocket-driven: the
 * `OutcomeRecorder` (decision #186) and the Backtest Runner write
 * `strategy_outcomes` straight to Postgres and neither publishes an
 * event for that table, so a subscription would never fire. (That is a
 * fact about the event catalog, not about whether a writer exists — the
 * simulated OutcomeRecorder writes non-backtest rows as of #186.)
 *
 * Result semantics (why the returned fields look the way they do):
 * - Loading, genuinely empty, and failed are three different states.
 *   `error` is only ever set by a failed request, never by empty data;
 *   an empty array pair with `error === null`, `loading === false` and
 *   `hasLoaded === true` is the only genuine "no data" state.
 * - Population isolation. Results and failures are tagged with the
 *   population they were fetched for, and only a result/failure for
 *   the population passed on THIS render is returned. Rows fetched for
 *   one population/strategy are therefore never returned while another
 *   is selected — not for one frame, and not while the new request is
 *   in flight (`loading` is true and the arrays are empty until the new
 *   population's own result arrives). The isolation is derived at
 *   render time rather than cleared in an effect, so there is no
 *   commit in which a stale population's rows sit beneath a new label.
 * - Only the newest request may write state. Every load takes a
 *   monotonically increasing request id (same pattern as
 *   useStrategyOutcomes.ts); a response — success OR failure — whose id
 *   is no longer the latest is dropped, so a slow older request can
 *   neither overwrite a newer request's result nor resurrect a stale
 *   error, whether the older request came from a filter change or from
 *   a repeated Refresh. Filter-change and unmount cleanup also
 *   invalidate any in-flight request. (The previous version's cancel
 *   closure was unreachable from `refetch()` callers, so a manual
 *   refetch was unprotected.)
 * - Refresh over the SAME population keeps that population's previous
 *   rows on screen while the new request is in flight (`loading` is
 *   true, `hasLoaded` stays true) — they still match the label.
 * - A failed request DOES clear the rows and sets `error`. This is a
 *   deliberate choice inherited from the original hook and differs
 *   from useStrategyOutcomes.ts (which keeps rows on a failed
 *   refresh): a failure must not leave a previous success's numbers
 *   sitting beneath an error state. A later success clears `error`.
 */
export function usePerformanceAnalytics(filters?: PerformanceAnalyticsFilters): {
  winRateByHour: HourlyWinRateWireShape[];
  sessionExpectancy: SessionTypeExpectancyWireShape[];
  loading: boolean;
  error: string | null;
  hasLoaded: boolean;
  lastLoadedAt: number | null;
  refetch: () => void;
} {
  const [result, setResult] = useState<LoadedResult | null>(null);
  const [failure, setFailure] = useState<LoadFailure | null>(null);
  // True while the newest request is in flight. Combined at render time
  // with "no result/failure for this population yet" to derive `loading`,
  // so the very first render after a population change already reads as
  // loading (before the effect below has even run).
  const [inFlight, setInFlight] = useState(true);
  const latestRequestId = useRef(0);

  const strategyName = filters?.strategyName;
  const strategyVersion = filters?.strategyVersion;
  const isBacktest = filters?.isBacktest;
  const key = populationKey({ strategyName, strategyVersion, isBacktest });

  const load = useCallback(() => {
    const requestId = ++latestRequestId.current;
    const activeFilters: PerformanceAnalyticsFilters = { strategyName, strategyVersion, isBacktest };
    const requestKey = populationKey(activeFilters);
    setInFlight(true);

    Promise.all([fetchWinRateByHour(activeFilters), fetchExpectancyBySessionType(activeFilters)])
      .then(([winRate, expectancy]) => {
        if (requestId !== latestRequestId.current) return;
        setResult({
          key: requestKey,
          winRateByHour: winRate.hourly_win_rates,
          sessionExpectancy: expectancy.session_expectancy,
          loadedAt: Date.now(),
        });
        setFailure(null);
        setInFlight(false);
      })
      .catch((err: unknown) => {
        if (requestId !== latestRequestId.current) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`usePerformanceAnalytics: fetch failed — ${detail}`);
        // Cleared rather than left stale — a failed request shouldn't
        // silently keep showing a previous success's numbers under an
        // error state.
        setResult(null);
        setFailure({ key: requestKey, message: detail });
        setInFlight(false);
      });
  }, [strategyName, strategyVersion, isBacktest]);

  useEffect(() => {
    load();
    return () => {
      // Invalidate whatever is in flight: unmount, or the population
      // changed and a fresh load() is about to supersede it.
      latestRequestId.current++;
    };
  }, [load]);

  // Render-time population isolation: anything not fetched for THIS
  // population is invisible to the caller.
  const currentResult = result !== null && result.key === key ? result : null;
  const currentFailure = failure !== null && failure.key === key ? failure : null;
  const loading = inFlight || (currentResult === null && currentFailure === null);

  return {
    winRateByHour: currentResult?.winRateByHour ?? EMPTY_WIN_RATES,
    sessionExpectancy: currentResult?.sessionExpectancy ?? EMPTY_EXPECTANCY,
    loading,
    error: currentFailure?.message ?? null,
    hasLoaded: currentResult !== null,
    lastLoadedAt: currentResult?.loadedAt ?? null,
    refetch: load,
  };
}

// Stable empty references so a not-yet-loaded population doesn't hand
// callers a new [] every render.
const EMPTY_WIN_RATES: HourlyWinRateWireShape[] = [];
const EMPTY_EXPECTANCY: SessionTypeExpectancyWireShape[] = [];
