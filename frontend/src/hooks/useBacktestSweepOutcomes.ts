import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  fetchBacktestRuns,
  fetchStrategyOutcomes,
  type BacktestRunWireShape,
  type StrategyOutcomeWireShape,
} from "../services/api-client";

// Backend read side for BacktestResultsPanel.tsx's new sweep_id filter
// mode (decision #163). Confirmed directly: no
// backend route filters `strategy_outcomes` by `sweep_id` — that table's
// only filters (GET /intelligence/strategy-outcomes, decision #123/#130)
// are `is_backtest`/`backtest_run_id`; `sweep_id` lives only on
// `backtests` (GET /intelligence/backtest-runs, decision #136, which DOES
// already support a real `sweep_id` filter — checked directly). Adding a
// `sweep_id` filter to GET /strategy-outcomes directly would be the
// smaller, more natural backend change, but this delivery's own explicit
// file boundary excludes everything under `backend/` — flagged here as a
// real, worth-doing follow-up, not built.
//
// This hook is the frontend-only way to get the same practical result:
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
// 2. Fetch that resolved run's own StrategyOutcome rows for EVERY run in
//    parallel (`Promise.all`, one `fetchStrategyOutcomes(limit, true,
//    runId)` call per resolved run_id) and merge them into one list,
//    re-sorted by `exit_filled_at` descending — the same ordering
//    GET /strategy-outcomes itself already guarantees per-run, restored
//    here across the merge since concatenating already-sorted arrays
//    doesn't keep that guarantee.
//
// Both `runs` (every resolved BacktestRunRecord in the sweep, including
// one whose own run genuinely produced outcomes_recorded=0) and the
// merged `outcomes` are returned — deliberately NOT collapsed into just
// the merged outcomes list. A pair that ran cleanly but produced zero
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
// Once ANY resolved run's own outcomes fetch fails, the whole hook
// surfaces that as `error` and clears both `runs`/`outcomes` rather than
// showing a partially-merged result silently missing one run's rows —
// a merged view has no honest way to show "this one run's own outcomes
// count is unknown" inline the way a per-pair error string could in
// BacktestPanel.tsx's own sweep RESULTS view right after a sweep finishes
// (see SweepResultsView there); surfacing a hard error here and letting
// the person retry is the safer default for a browse-only view with no
// per-row error slot to put a partial failure in.
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

    fetchBacktestRuns(limit, undefined, undefined, sweepId)
      .then(async (runsWire) => {
        if (cancelled) return;
        const resolvedRuns = runsWire.backtest_runs;
        const outcomesByRun = await Promise.all(
          resolvedRuns.map((r) => fetchStrategyOutcomes(limit, /* isBacktest */ true, r.run_id)),
        );
        if (cancelled) return;
        const merged = outcomesByRun
          .flatMap((w) => w.outcomes)
          .sort((a, b) => (a.exit_filled_at < b.exit_filled_at ? 1 : a.exit_filled_at > b.exit_filled_at ? -1 : 0));
        setRuns(resolvedRuns);
        setOutcomes(merged);
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
