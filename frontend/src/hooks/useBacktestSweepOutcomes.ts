import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  fetchBacktestRuns,
  fetchStrategyOutcomes,
  type BacktestRunWireShape,
  type StrategyOutcomeWireShape,
} from "../services/api-client";

// Backend read side for BacktestResultsPanel.tsx's sweep_id filter mode
// (decision #163), updated by decision #165 to use the
// route-level sweep filter rather than the original per-run fan-out:
// 1. Resolve every `BacktestRunRecord` belonging to the sweep via
//    `fetchBacktestRuns(limit, undefined, undefined, sweepId)` — already
//    supports `sweepId`, confirmed directly against its own signature.
//    A sweep is capped at `BACKTEST_SWEEP_MAX_PAIRS` (20) pairs
//    server-side (backtest.py's own `_MAX_SWEEP_PAIRS`), so this never
//    needs more than one page — `limit` is passed generously (500, this
//    file's own OUTCOMES_LIMIT-equivalent) rather than omitted, same
//    "don't silently fall back to the route's smaller live-oriented
//    default" reasoning BacktestResultsPanel.tsx's own OUTCOMES_LIMIT
//    comment already gives for the sibling run_id path.
// 2. Fetch every StrategyOutcome in that sweep once with
//    `fetchStrategyOutcomes(limit, true, undefined, sweepId)`. The
//    backend joins through `backtest_run_id` and applies global newest-
//    first ordering plus `limit`, so no client merge or re-sort remains.
// Both requests run together in one Promise.all: exactly two requests per
// load regardless of how many runs belong to the sweep.
//
// Both `runs` (every resolved BacktestRunRecord in the sweep, including
// one whose own run genuinely produced outcomes_recorded=0) and the
// `outcomes` are returned — deliberately NOT collapsed into just the
// outcomes list. A pair that ran cleanly but produced zero
// StrategyOutcome rows (an honest, expected result per the sweep route's
// own docstring — several strategy/scenario combinations are structurally
// unreachable) would otherwise be invisible: absent from `outcomes`, but
// present and accounted for in `runs`. BacktestResultsPanel.tsx renders
// `runs` as a compact per-pair strip above the merged outcome rows for
// exactly this reason — never silently dropping a pair that legitimately
// recorded nothing, per this codebase's own "absent means not-yet, never
// silently worked around" principle.
//
// A malformed (non-UUID) sweepId still reaches a real backend 400 from
// step 1 (fetchBacktestRuns's own existing behavior) and is surfaced as
// this hook's `error`, not swallowed — same posture every other hook in
// this file family already takes. A well-formed but genuinely-unmatched
// sweepId resolves to `runs: []`, `outcomes: []`, `error: null` — honest
// empty, not an error, same distinction useBacktestRuns.ts/
// useBacktestOutcomes.ts already draw for their own equivalent case.
// If either required request fails, the whole hook surfaces that as
// `error` and clears both `runs`/`outcomes` rather than showing a partial
// result. Both datasets are required: outcomes render the trade list,
// while runs preserve visibility for pairs that recorded zero outcomes.
//
// Deliberately one-shot on mount + on `sweepId` change, not a WebSocket
// subscription — same reasoning useBacktestOutcomes.ts/useBacktestRuns.ts
// already give for this exact table family: no relevant event exists in
// `backend/app/schemas/events/`. `refetch()` exposed for a manual
// "Refresh" control, same as those two hooks.
export function useBacktestSweepOutcomes(params?: { sweepId?: string; limit?: number }): {
  runs: BacktestRunWireShape[];
  outcomes: StrategyOutcomeWireShape[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
} {
  const [runs, setRuns] = useState<BacktestRunWireShape[]>([]);
  const [outcomes, setOutcomes] = useState<StrategyOutcomeWireShape[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const sweepId = params?.sweepId;
  const limit = params?.limit ?? 500;

  const load = useCallback(() => {
    if (!sweepId) {
      // Same "nothing to show without a real filter value" posture
      // useBacktestRuns.ts already takes for an unset runId — unlike
      // useBacktestOutcomes.ts's own "no filter = show everything"
      // default, there is no "every sweep merged together" view that
      // would mean anything here.
      setRuns([]);
      setOutcomes([]);
      setLoading(false);
      setError(null);
      return () => {};
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      fetchBacktestRuns(limit, undefined, undefined, sweepId),
      fetchStrategyOutcomes(limit, /* isBacktest */ true, undefined, sweepId),
    ])
      .then(([runsWire, outcomesWire]) => {
        if (cancelled) return;
        setRuns(runsWire.backtest_runs);
        setOutcomes(outcomesWire.outcomes);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useBacktestSweepOutcomes: fetch failed — ${detail}`);
        setRuns([]);
        setOutcomes([]);
        setError(detail);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [sweepId, limit]);

  useEffect(() => load(), [load]);

  return { runs, outcomes, loading, error, refetch: load };
}
