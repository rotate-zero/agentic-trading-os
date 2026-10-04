import { useCallback, useEffect, useRef, useState } from "react";
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
 *
 * Current-filter consistency (task `backtest-results-refresh-recovery`):
 * the settled `backtestRun`/`error` are stored with the `runId` they were
 * fetched for and returned only while that is still the current `runId`.
 * In the render between a `runId` change and the effect that starts its
 * fetch, the hook therefore reports `loading` with `backtestRun === null`
 * and `error === null` — never the previous run's metadata or error, and
 * never a premature "no run found" before any request for the current
 * `runId` has even started. A per-hook request counter, advanced on every
 * load, on `runId` change and on unmount, makes a newer request supersede
 * every older one, so a late older response (success or failure) is
 * discarded. Underlying fetches are not cancelled.
 */
interface RunSnapshot {
  key: string;
  backtestRun: BacktestRunWireShape | null;
  error: string | null;
  loading: boolean;
}

export function useBacktestRuns(params?: { runId?: string }): {
  backtestRun: BacktestRunWireShape | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
} {
  const runId = params?.runId;
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null);
  const latestRequestRef = useRef(0);

  const load = useCallback(() => {
    const requestId = ++latestRequestRef.current;
    if (!runId) {
      setSnapshot(null);
      return;
    }

    setSnapshot((prev) => ({
      key: runId,
      backtestRun: prev?.key === runId ? prev.backtestRun : null,
      error: null,
      loading: true,
    }));

    fetchBacktestRuns(/* limit */ undefined, runId)
      .then((wire) => {
        if (requestId !== latestRequestRef.current) return;
        setSnapshot({ key: runId, backtestRun: wire.backtest_runs[0] ?? null, error: null, loading: false });
      })
      .catch((err: unknown) => {
        if (requestId !== latestRequestRef.current) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useBacktestRuns: fetch failed — ${detail}`);
        setSnapshot({ key: runId, backtestRun: null, error: detail, loading: false });
      });
  }, [runId]);

  useEffect(() => {
    load();
    return () => {
      // runId change or unmount: invalidate anything in flight.
      latestRequestRef.current += 1;
    };
  }, [load]);

  const current = runId && snapshot?.key === runId ? snapshot : null;
  return {
    backtestRun: current?.backtestRun ?? null,
    loading: !!runId && (current?.loading ?? true),
    error: current?.error ?? null,
    refetch: load,
  };
}
