import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchBacktestRuns, type BacktestRunWireShape } from "../services/api-client";

/**
 * Backend read side for `RecentBacktestRuns.tsx` (task
 * `backtest-run-history`) — the recent-runs LIST read of
 * GET /intelligence/backtest-runs (decision #136): no `run_id`, no
 * `sweep_id`, no strategy filter, an explicit `limit`. A deliberately
 * separate hook from `useBacktestRuns.ts`, whose contract (one `run_id` ->
 * one run's metadata, skipped when no `run_id`) is unchanged; the two read
 * the same route at different granularity and share nothing but
 * `fetchBacktestRuns`.
 *
 * `runs` keeps the server's returned order verbatim (the route is
 * newest-first by `created_at`, ties broken deterministically — decision
 * #136 / task `outcome-read-limit-ordering`); this hook never re-sorts,
 * filters or de-duplicates. It reads run SETTINGS only; it never derives
 * profitability, outcome counts or any other statistic from them.
 *
 * Request ordering: every load — the effect-driven one and a manual
 * `refetch()` alike — takes the next number from one per-hook counter, and
 * a response is applied only while its number is still the latest, so a
 * later request (including a Refresh pressed while an earlier one is still
 * pending) supersedes every earlier one even when the earlier response
 * arrives last. The counter is also advanced on unmount, so a completion
 * after unmount touches no state. Underlying fetches are not cancelled; a
 * superseded response is simply discarded.
 *
 * `refreshKey` is an opaque value the caller changes when the world is
 * known to have moved on (the panel passes the workspace's
 * `lastBacktestRunId`/`lastBacktestSweepId`). A change re-runs the effect
 * and therefore starts a fresh, superseding load. This is NOT polling: no
 * timer, no WebSocket; the first value only triggers the mount load.
 *
 * State on a reload: the previous list stays visible with `loading: true`
 * (a refresh is not an empty state). On failure `runs` is cleared and
 * `error` is set — a failed history read never looks like "no runs", and
 * never leaves a previous list on screen under a new error. The error is
 * history-local: nothing outside this hook reads it, so it cannot block
 * viewing an already selected run.
 */
export const RECENT_RUNS_LIMIT = 50;

interface RecentRunsSnapshot {
  runs: BacktestRunWireShape[];
  error: string | null;
  loading: boolean;
}

const INITIAL: RecentRunsSnapshot = { runs: [], error: null, loading: true };

export function useRecentBacktestRuns(params?: { refreshKey?: string }): {
  runs: BacktestRunWireShape[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
} {
  const refreshKey = params?.refreshKey ?? "";
  const [snapshot, setSnapshot] = useState<RecentRunsSnapshot>(INITIAL);
  const latestRequestRef = useRef(0);

  const load = useCallback(() => {
    const requestId = ++latestRequestRef.current;
    setSnapshot((prev) => ({ runs: prev.runs, error: null, loading: true }));

    fetchBacktestRuns(RECENT_RUNS_LIMIT)
      .then((wire) => {
        if (requestId !== latestRequestRef.current) return;
        setSnapshot({ runs: wire.backtest_runs, error: null, loading: false });
      })
      .catch((err: unknown) => {
        if (requestId !== latestRequestRef.current) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useRecentBacktestRuns: fetch failed — ${detail}`);
        setSnapshot({ runs: [], error: detail, loading: false });
      });
  }, []);

  useEffect(() => {
    load();
    return () => {
      // refreshKey change or unmount: invalidate anything in flight.
      latestRequestRef.current += 1;
    };
  }, [load, refreshKey]);

  return { runs: snapshot.runs, loading: snapshot.loading, error: snapshot.error, refetch: load };
}
