import { useCallback, useEffect, useState } from "react";
import { ApiError, fetchBacktestRuns, type BacktestRunWireShape } from "../services/api-client";

/**
 * Backend read side for `BacktestResultsPanel.tsx`'s new run-metadata card
 * (decision #139 — surfaces GET /intelligence/backtest-runs, decision
 * #136, in the frontend for the first time). Deliberately a NEW, separate
 * hook from `useBacktestOutcomes.ts`, not an extension of it — that hook
 * reads `strategy_outcomes` (one row per closed trade a run produced);
 * this one reads `backtests` (one row per run's own settings), a
 * different table at a different granularity, related only via
 * `run_id`/`backtest_run_id`. Naming mirrors that hook's own
 * (`useBacktestOutcomes`/`useBacktestRuns`) for the same reason
 * `useStrategyOutcomes`/`useBacktestOutcomes` already pair up.
 *
 * `runId` is the ONLY param this hook exposes, even though
 * `fetchBacktestRuns` also supports `strategyName`/`sweepId`/`limit` —
 * this hook has exactly one real caller today (`BacktestResultsPanel.tsx`,
 * `BacktestResultsBody`), and that caller only ever has a run_id to
 * resolve against (the exact gap decision #136's own docstring named:
 * "a caller with only a run_id... can now resolve that run's own
 * metadata"). Exposing unused filter params here would be building ahead
 * of an actual need.
 *
 * Deliberately skips the fetch entirely when `runId` is undefined —
 * unlike `useBacktestOutcomes.ts` (which still fetches "everything" with
 * no `backtestRunId`), there is no single run's metadata to show when no
 * run_id filter is applied; issuing a request in that state would return
 * up to 50 arbitrary recent runs with no correct way to pick "the" one
 * for a metadata card keyed to one run. `backtestRun` resets to `null`
 * (not left stale) the instant `runId` itself changes or clears, same
 * "never render a mismatched previous result" reasoning
 * `useBacktestOutcomes.ts`'s own `setOutcomes([])`-on-error already uses.
 *
 * `run_id` is `BacktestRunRecord`'s own primary key (confirmed against
 * the ORM model) — `backtest_runs[0] ?? null` is safe, never an
 * arbitrary pick from a genuine list of more than one.
 *
 * Exposes a caller-visible `error` state distinct from "no run found for
 * this run_id" (`backtestRun === null` with `error === null` once
 * `loading` is `false`) — same real-400-must-never-look-like-honest-empty
 * reasoning `useBacktestOutcomes.ts`/`usePerformanceAnalytics.ts` already
 * establish for this exact concern. A malformed (non-UUID) `runId` string
 * — reachable here since this panel's run_id filter is free-text — still
 * produces a real backend 400, surfaced as `error`, not conflated with a
 * genuinely-unmatched, well-formed run_id.
 *
 * Deliberately one-shot on mount + on `runId` change, not a WebSocket
 * subscription — same reasoning `useBacktestOutcomes.ts` already gives
 * for this exact table's sibling: no `BacktestRunRecorded`-shaped event
 * exists anywhere in `backend/app/schemas/events/` (confirmed by grep).
 * `refetch()` exposed for symmetry with `useBacktestOutcomes.ts`, though
 * this panel doesn't wire a dedicated manual-refresh control to it today
 * — a run's own metadata (unlike its outcomes) never changes after
 * `POST /backtest/run` first writes it, so there's no "just-finished run"
 * staleness case to refresh away; kept for interface symmetry and in
 * case a future caller needs it, not dead code speculatively expanding
 * this hook's own scope.
 */
export function useBacktestRuns(params?: { runId?: string }): {
  backtestRun: BacktestRunWireShape | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
} {
  const [backtestRun, setBacktestRun] = useState<BacktestRunWireShape | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runId = params?.runId;

  const load = useCallback(() => {
    if (!runId) {
      setBacktestRun(null);
      setLoading(false);
      setError(null);
      return () => {};
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchBacktestRuns(/* limit */ undefined, runId)
      .then((wire) => {
        if (cancelled) return;
        setBacktestRun(wire.backtest_runs[0] ?? null);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useBacktestRuns: fetch failed — ${detail}`);
        setBacktestRun(null);
        setError(detail);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [runId]);

  useEffect(() => load(), [load]);

  return { backtestRun, loading, error, refetch: load };
}
