import { useCallback, useEffect, useState } from "react";
import { ApiError, fetchStrategyOutcomes, type StrategyOutcomeWireShape } from "../services/api-client";

/**
 * Backend read side for the new "Backtest Results" panel
 * (`BacktestResultsPanel.tsx`) — GET /intelligence/strategy-outcomes
 * (decision #123), with `is_backtest` fixed `true` by this hook's own
 * default rather than exposed as a caller-toggleable option. This is
 * deliberately a NEW, separate hook from `useStrategyOutcomes.ts`, not
 * an extension of it — that hook backs "Recent Closed Trades" and is
 * pinned to `isBacktest: false` by design (see its own comment); adding
 * a toggle there would blur two hooks with opposite, fixed intents into
 * one confusing one. `strategy_outcomes` has zero real LIVE rows today
 * but real, persisted BACKTEST rows as of decision #128 — so unlike
 * `useStrategyOutcomes`, defaulting this hook to the live-only value
 * would make it default to an always-empty view of the one thing this
 * panel exists to show.
 *
 * `backtestRunId` and `limit` are reactive params (changing either
 * re-fetches, same dependency-array shape `usePerformanceAnalytics.ts`
 * already uses for its own filter params) — `backtestRunId` in
 * particular is expected to change often, since the panel's free-text
 * `run_id` field writes directly into it. `is_backtest=true` is never
 * varied alongside it: this hook has no way to ask for
 * `backtest_run_id` + `is_backtest=false` (the real, enforced 400 the
 * backend route defines), so that combination can't be reached through
 * this hook's own call shape — only a malformed (non-UUID)
 * `backtestRunId` string can still produce a 400 from the backend, and
 * that's handled below like any other fetch failure.
 *
 * Exposes a caller-visible `error` state distinct from empty data —
 * same deliberate deviation from `useStrategyOutcomes.ts`'s
 * console-error-and-swallow shape that `usePerformanceAnalytics.ts`
 * already established, for the same reason: a real backend 400/500
 * must never render identically to "this table/run genuinely has no
 * rows," since this panel's whole job is showing exactly what's in the
 * table. `outcomes` stays the full, un-narrowed
 * `StrategyOutcomeWireShape` (unlike `useStrategyOutcomes.ts`'s
 * `StrategyOutcomeRow` subset) — this panel's own expand-in-place detail
 * view needs `evidence`/`market_state_at_entry`/`context_at_entry`/etc.,
 * which that narrower type deliberately drops.
 *
 * Deliberately one-shot on mount + on reactive-param change, not a
 * WebSocket subscription: no `OutcomeRecorded`-shaped event exists
 * anywhere in `backend/app/schemas/events/` (confirmed by grep, not
 * assumed) since no live writer exists yet — same reasoning
 * `useStrategyOutcomes.ts`/`usePerformanceAnalytics.ts` already
 * document for this exact table. `refetch()` is exposed for a manual
 * "Refresh" action in the panel.
 *
 * Decision #134 update: this table's own writer, Backtest Runner v1, was
 * triggered from a separate, unlinked panel (`BacktestPanel.tsx`) when
 * this comment was originally written — that's now closed. A finished
 * run's `run_id` is published to shared `WorkspaceContext` state
 * (`lastBacktestRunId`) and `BacktestResultsPanel.tsx` defaults its
 * `backtestRunId` param to it, so this hook's own `[limit, backtestRunId]`
 * dependency array (already reactive, unchanged by that task) now
 * re-fires this fetch automatically the moment a run the panel is
 * auto-following finishes — a real refetch, not merely a UI hint, since
 * `POST /backtest/run` is synchronous and only resolves after its own
 * `StrategyOutcome` rows are already written. `refetch()`/manual
 * "Refresh" remain meaningful for anything outside that path: a run
 * triggered from a different browser tab or by another operator, or
 * while the results panel's filter is pinned to a different run_id the
 * person picked manually (see that panel's own auto/manual mode
 * comment) — this hook itself has no way to distinguish those cases,
 * that logic lives entirely in the panel above it.
 *
 * `enabled` (decision #163, default `true`):
 * BacktestResultsPanel.tsx now has a second, independent filter mode
 * (sweep_id, via useBacktestSweepOutcomes.ts) that this hook knows
 * nothing about. Both hooks are called unconditionally from that panel
 * (React's own rules of hooks — a component can't call a hook
 * conditionally), so `enabled: false` is this hook's own explicit way to
 * skip its real network call while the OTHER filter mode is the one
 * currently showing, rather than always issuing its own "everything"
 * fetch in the background whether or not anything will ever render it.
 * `loading` still resolves to `false` in this state (never left stuck at
 * its initial `true`) — a disabled hook isn't "still loading," it's
 * simply not asked to do anything right now.
 */
export function useBacktestOutcomes(params?: { limit?: number; backtestRunId?: string; enabled?: boolean }): {
  outcomes: StrategyOutcomeWireShape[];
  loading: boolean;
  error: string | null;
  refetch: () => void;
} {
  const [outcomes, setOutcomes] = useState<StrategyOutcomeWireShape[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const limit = params?.limit;
  const backtestRunId = params?.backtestRunId;
  const enabled = params?.enabled ?? true;

  const load = useCallback(() => {
    if (!enabled) {
      setLoading(false);
      return () => {};
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    fetchStrategyOutcomes(limit, /* isBacktest */ true, backtestRunId)
      .then((wire) => {
        if (cancelled) return;
        setOutcomes(wire.outcomes);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useBacktestOutcomes: fetch failed — ${detail}`);
        // Cleared rather than left stale, same reasoning
        // usePerformanceAnalytics.ts gives for its own failure path — a
        // failed refetch (e.g. a mistyped run_id) shouldn't leave a
        // previous successful result on screen underneath a new error.
        setOutcomes([]);
        setError(detail);
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [limit, backtestRunId, enabled]);

  useEffect(() => load(), [load]);

  return { outcomes, loading, error, refetch: load };
}
