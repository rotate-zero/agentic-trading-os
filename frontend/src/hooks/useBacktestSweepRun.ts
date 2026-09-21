import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, triggerBacktestSweep, type BacktestSweepResultWireShape } from "../services/api-client";

export type BacktestSweepRunStatus = "idle" | "running" | "done" | "error";

/**
 * Sibling to useBacktestRun.ts/useIbkrBacktestRun.ts — owns one
 * POST /backtest/sweep call end to end, mirroring those two hooks' own
 * status-machine/live-elapsed-timer shape almost exactly (idle/running/
 * done/error, a plain 1s setInterval deriving elapsedSeconds from a real
 * Date.now() delta rather than an incrementing counter, so a throttled
 * background tab doesn't silently understate true elapsed time). Kept as
 * its own file rather than folded into useBacktestRun.ts, same reasoning
 * useIbkrBacktestRun.ts's own docstring already gives for staying
 * separate: the request shape (strategy_name + symbols[] + scenarios[],
 * not strategy_name/symbol/scenario) and the result shape
 * (BacktestSweepResultWireShape — many pairs, one shared sweep_id, no
 * single run_id) are different enough from either existing hook that a
 * shared abstraction would need its own mode branching, defeating the
 * point of keeping each hook small and legible.
 *
 * No incremental/partial result during a run — confirmed directly against
 * the backend route: it returns one complete BacktestSweepResult only
 * once every requested pair has finished, sequentially, with no progress
 * event of any kind. `elapsedSeconds` is therefore this hook's only
 * signal of "how long has this been running," same honest limitation
 * useIbkrBacktestRun.ts already lives with for its own single-run,
 * no-progress-signal request.
 *
 * Plain ApiError, not a dedicated error subclass the way
 * useIbkrBacktestRun.ts needed IbkrBacktestError for — confirmed directly
 * against the sweep route: every error it raises (unknown strategy_name/
 * scenario, empty symbols/scenarios, batch-size exceeded, the live-data
 * guard) is a plain string `detail`, the same shape useBacktestRun.ts's
 * own triggerBacktest already handles with the shared ApiError.
 */
export function useBacktestSweepRun() {
  const [status, setStatus] = useState<BacktestSweepRunStatus>("idle");
  const [result, setResult] = useState<BacktestSweepResultWireShape | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const timerRef = useRef<number | null>(null);
  const statusRef = useRef<BacktestSweepRunStatus>("idle");

  const stopTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => stopTimer, [stopTimer]);

  const run = useCallback(
    async (strategyName: string, symbols: string[], scenarios: string[]) => {
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
        const res = await triggerBacktestSweep(strategyName, symbols, scenarios);
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
