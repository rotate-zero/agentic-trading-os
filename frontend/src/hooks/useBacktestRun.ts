import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, triggerBacktest, type BacktestRunResultWireShape } from "../services/api-client";

export type BacktestRunStatus = "idle" | "running" | "done" | "error";

/**
 * Owns one POST /backtest/run (decision #130) call end to end —
 * BacktestPanel.tsx's only piece of non-presentational logic.
 *
 * Unlike every other hook in this codebase (useScannerState's 15s poll,
 * useContextSnapshot's WS-primary + safety-net poll, ...), this isn't a
 * "keep some server state fresh" hook — there's nothing to poll or
 * subscribe to. It's a one-shot synchronous action whose duration depends
 * on workload and environment (replay no longer pays a one-second debounce
 * floor per candle as of decision #157), so the hook exposes elapsed time
 * honestly rather than hiding the request behind a generic spinner. `run()`
 * starts a plain 1s `setInterval` the instant the call is fired and
 * derives `elapsedSeconds` from a real `Date.now()` delta each tick
 * (not just an incrementing counter — a slow browser tab throttling
 * timers wouldn't silently understate the true elapsed time this way),
 * so the panel can render a live "Nm Ns elapsed" instead of a generic
 * spinner that gives no sense of whether this is normal.
 *
 * `run()` is a no-op if a run is already in flight — belt-and-suspenders
 * alongside BacktestPanel.tsx disabling its own submit control while
 * `status === "running"`, since this route's own
 * `engine_singleton_guard.py` would serialize a genuine double-call on
 * the backend anyway (see that route's docstring); this guard just
 * avoids firing a second, wasted HTTP request from this tab
 * that would end up blocked behind the first regardless.
 */
export function useBacktestRun() {
  const [status, setStatus] = useState<BacktestRunStatus>("idle");
  const [result, setResult] = useState<BacktestRunResultWireShape | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const timerRef = useRef<number | null>(null);
  const statusRef = useRef<BacktestRunStatus>("idle");

  const stopTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // Unmounting mid-run (panel collapsed, tab navigated away) shouldn't
  // leave a dangling interval — the in-flight fetch itself can't be
  // cancelled (no AbortController wired here, nothing else in this
  // codebase's fetch wrappers uses one either), but the timer at least
  // stops ticking against state nobody will ever read again.
  useEffect(() => stopTimer, [stopTimer]);

  const run = useCallback(
    async (strategyName: string, symbol: string, scenario: string) => {
      if (statusRef.current === "running") return;
      statusRef.current = "running";
      setStatus("running");
      setError(null);
      setResult(null);
      setElapsedSeconds(0);

      const startedAtMs = Date.now();
      stopTimer();
      timerRef.current = window.setInterval(() => {
        setElapsedSeconds(Math.floor((Date.now() - startedAtMs) / 1000));
      }, 1000);

      try {
        const res = await triggerBacktest(strategyName, symbol, scenario);
        setResult(res);
        statusRef.current = "done";
        setStatus("done");
      } catch (err: unknown) {
        const detail = err instanceof ApiError ? err.message : String(err);
        setError(detail);
        statusRef.current = "error";
        setStatus("error");
      } finally {
        stopTimer();
      }
    },
    [stopTimer],
  );

  return { status, result, error, elapsedSeconds, run };
}
