import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchWorldView, type WorldViewPerformanceWireShape, type WorldViewPortfolioWireShape } from "../services/api-client";

/**
 * Backend read side for the World View composite facade — GET
 * /intelligence/world-view (decision #150), zero frontend representation
 * until this task (confirmed by grep across frontend/src/ before
 * starting).
 *
 * World View's own architectural purpose (trading-intelligence-
 * architecture.md §15) is "one summary instead of several separate
 * queries" for something like a debug view or a future automated-
 * reasoning layer — not a replacement for Market State's/Context's own
 * detailed, filterable, already-live-updating panels
 * (useMarketState.ts/useContextSnapshot.ts). This hook therefore does
 * NOT normalize or expose `market_state`/`context` at all, even though
 * the route returns them — a caller that wants those should call
 * useMarketState/useContextSnapshot directly, the real owners of that
 * data; duplicating their normalization here, for fields this task's own
 * UI never renders, would be dead code. Only `performance` (the one
 * field with genuinely new shape — both live and backtest populations,
 * always, side by side, all-time; nothing else in this codebase shows
 * both populations at once) and `portfolio` (the restored execution
 * snapshot when available) are exposed.
 *
 * Always calls `fetchWorldView()` with no `symbol` — `performance`/
 * `portfolio` are system-wide/unscoped regardless of `symbol` (only
 * `market_state`/`context` would be affected, and this hook doesn't
 * expose either — see fetchWorldView's own comment in api-client.ts). A
 * future per-symbol consumer of `market_state`/`context` should extend
 * this hook deliberately rather than have `symbol` silently do nothing
 * here today.
 *
 * Deliberately one-shot on mount, NOT polled and NOT WebSocket-driven —
 * same reasoning usePerformanceAnalytics.ts already established for
 * `strategy_outcomes` itself, and directly applicable here since
 * `performance` is composed from that exact table via the same two
 * query functions (`get_win_rate_by_hour()`/
 * `get_expectancy_by_session_type()`, composite.py's own
 * `_read_performance()`): no event is published when a `strategy_outcomes`
 * row is written, and
 * World View has no event subscription, cache, or WebSocket channel of
 * its own by design — confirmed directly against decision #150's own
 * text and `composite.py`'s module docstring ("No persistence, cache,
 * scheduler, or WebSocket channel added"). There is consequently nothing
 * that would ever change on its own for this hook to poll or subscribe
 * to; `refetch()` is exposed as the manual trigger.
 *
 * Distinguishes a caller-visible `error` from genuinely empty data, the
 * same deliberate deviation usePerformanceAnalytics.ts already
 * established over useContextSnapshot/useMarketState/useStrategyOutcomes
 * (all three collapse a fetch failure into `console.error` plus an
 * empty/default result): collapsing the two here would mean a real
 * backend failure renders identically to "no StrategyOutcome data
 * exists yet," which is exactly the distinction an honest empty state
 * needs to preserve.
 */
export interface UseWorldViewResult {
  performance: WorldViewPerformanceWireShape | null;
  portfolio: WorldViewPortfolioWireShape | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useWorldView(): UseWorldViewResult {
  const [performance, setPerformance] = useState<WorldViewPerformanceWireShape | null>(null);
  const [portfolio, setPortfolio] = useState<WorldViewPortfolioWireShape | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  const load = useCallback(() => {
    const currentRequest = ++requestId.current;
    setLoading(true);
    setError(null);

    fetchWorldView()
      .then((snapshot) => {
        if (currentRequest !== requestId.current) return;
        setPerformance(snapshot.performance);
        setPortfolio(snapshot.portfolio);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (currentRequest !== requestId.current) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useWorldView: fetch failed — ${detail}`);
        // Cleared rather than left stale — same reasoning
        // usePerformanceAnalytics.ts's own load() already documents: a
        // failed refetch shouldn't silently keep showing a previous
        // success's numbers under a new error state.
        setPerformance(null);
        setPortfolio(null);
        setError(detail);
        setLoading(false);
      });

    return () => { requestId.current++; };
  }, []);

  useEffect(() => load(), [load]);

  return { performance, portfolio, loading, error, refetch: load };
}
