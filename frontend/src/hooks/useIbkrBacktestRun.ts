import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, IbkrBacktestError, triggerIbkrBacktest, type BacktestRunResultWireShape } from "../services/api-client";

export type IbkrBacktestRunStatus = "idle" | "running" | "done" | "error";

export interface IbkrBacktestRunError {
  message: string;
  status: number;
  /** Backend's own stable code (e.g. "ibkr_historical_timeout"), or null
   * for the plain-string 409 shape / an unparseable response — see
   * IbkrBacktestError in api-client.ts. BacktestPanel.tsx's classifier
   * falls back safely when this is null or unrecognized. */
  code: string | null;
}

/**
 * Sibling to useBacktestRun.ts, deliberately NOT a shared abstraction
 * between the two (per this task's own design call) — owns one
 * POST /backtest/run/ibkr call end to end, mirroring that hook's own
 * status-machine/live-elapsed-timer shape almost exactly, because the
 * request/response/error surface differs enough to make a real difference:
 *
 * - Request shape: strategy_name/symbol/start/end (two timezone-aware ISO
 *   instants), not strategy_name/symbol/scenario.
 * - Error surface: IbkrBacktestError's `code` field (409/422/503/400/502/504
 *   with several distinct machine-readable codes — see api-client.ts's own
 *   comment block above triggerIbkrBacktest), not useBacktestRun.ts's plain
 *   ApiError.message.
 * - Execution: external IBKR acquisition plus synchronous replay, with no
 *   progress event stream. Replay uses decision #157's exact settlement;
 *   acquisition can still vary independently. Keeping this separate avoids
 *   mixing that request/error lifecycle into the fixture hook.
 *
 * The interval-based live-elapsed-timer mechanics below are intentionally
 * near-identical to useBacktestRun.ts's own — a small, obvious duplication,
 * not factored into a shared "request state" hook, since the two hooks'
 * request/response/error shapes and docstrings are genuinely different
 * enough that a shared abstraction would need its own mode branching to
 * paper over that, defeating the point of keeping this small and legible.
 */
export function useIbkrBacktestRun() {
  const [status, setStatus] = useState<IbkrBacktestRunStatus>("idle");
  const [result, setResult] = useState<BacktestRunResultWireShape | null>(null);
  const [error, setError] = useState<IbkrBacktestRunError | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const timerRef = useRef<number | null>(null);
  const statusRef = useRef<IbkrBacktestRunStatus>("idle");

  const stopTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => stopTimer, [stopTimer]);

  const run = useCallback(
    async (strategyName: string, symbol: string, startIso: string, endIso: string) => {
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
        const res = await triggerIbkrBacktest(strategyName, symbol, startIso, endIso);
        setResult(res);
        statusRef.current = "done";
        setStatus("done");
      } catch (err: unknown) {
        if (err instanceof IbkrBacktestError) {
          setError({ message: err.message, status: err.status, code: err.code });
        } else if (err instanceof ApiError) {
          setError({ message: err.message, status: err.status, code: null });
        } else {
          setError({ message: String(err), status: 0, code: null });
        }
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
