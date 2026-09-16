import { useEffect, useState } from "react";
import { ApiError, fetchBacktestRuns, type BacktestRunWireShape } from "../services/api-client";

/**
 * Backend read side for `BacktestResultsPanel.tsx`'s new run-metadata
 * banner — GET /intelligence/backtest-runs (decision #136), narrowed to
 * exactly one run via its `run_id` param. Sibling to
 * `useBacktestOutcomes.ts` (that hook shows what a run *produced*; this
 * one resolves the run's *own* metadata: strategy_version, config_hash,
 * date range, data_version, feature_version, walk_forward_fold,
 * is_holdout, sweep_id, created_at) — same pairing `strategy-engine-
 * design.md` §7's decision #136 as-built note already describes.
 *
 * `runId` is expected to be `BacktestResultsBody`'s own `appliedRunId` —
 * the exact same value already driving `useBacktestOutcomes`'s
 * `backtestRunId` param, so this hook stays in lockstep with whichever
 * run that panel is currently filtered to, without any new shared state.
 * When `runId` is `undefined` or empty (no filter applied — the panel is
 * showing "everything"), this hook makes no request at all: a run's own
 * metadata is only meaningful for one specific run, and fetching against
 * an unfiltered view would mean an arbitrary, meaningless `limit=1` row.
 *
 * Because `run_id` is an exact match on `backtests`' own primary key, at
 * most one row can ever come back — `run: null` with no error is the
 * honest "no run metadata found for this run_id" case (a syntactically
 * valid UUID that just doesn't match any row, e.g. from another
 * environment's data); a malformed (non-UUID) `runId` — reachable here
 * since the panel's `run_id` field is free text — surfaces as a distinct
 * `error`, mirroring `useBacktestOutcomes.ts`'s own error/empty split for
 * the exact same reason: a real 400 must never render identically to a
 * genuinely absent row.
 *
 * No `refetch` exposed, unlike `useBacktestOutcomes.ts`. A `backtests`
 * row is written once, before any `StrategyOutcome` row for that run
 * exists (`BacktestRunner.run()`'s own FK-ordering requirement), and
 * never updated afterward — there is nothing for a manual refresh to
 * pick up that a `runId` change wouldn't already trigger.
 */
export function useBacktestRunMeta(runId?: string): {
  run: BacktestRunWireShape | null;
  loading: boolean;
  error: string | null;
} {
  const [run, setRun] = useState<BacktestRunWireShape | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) {
      // No run selected — nothing to resolve, and no stale prior result
      // should linger once the filter is cleared.
      setRun(null);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchBacktestRuns(1, runId)
      .then((wire) => {
        if (cancelled) return;
        setRun(wire.backtest_runs[0] ?? null);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useBacktestRunMeta: fetch failed — ${detail}`);
        setRun(null);
        setError(detail);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [runId]);

  return { run, loading, error };
}
