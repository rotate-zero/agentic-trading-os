import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, StoredBacktestError, triggerStoredBacktest, type BacktestRunResultWireShape } from "../services/api-client";

export type StoredBacktestRunStatus = "idle" | "running" | "done" | "error";

export interface StoredBacktestRunError {
  message: string;
  status: number;
  /** Backend's own stable code (e.g. "stored_candles_no_data"), or null
   * for the plain-string 409 shape / an unparseable response — see
   * StoredBacktestError in api-client.ts. BacktestPanel.tsx's classifier
   * falls back safely when this is null or unrecognized. */
  code: string | null;
}

/**
 * Owns one POST /backtest/run/stored call end to end — sibling to
 * useIbkrBacktestRun.ts with the same status machine, elapsed timer and
 * duplicate-submit guard, but its own request/error surface (StoredBacktestError
 * codes such as "stored_candles_no_data"). Deliberately not a shared
 * abstraction with the other run hooks, same call those hooks already made.
 * The request is synchronous: a database read followed by replay, with no
 * progress signal, so only elapsed time is reported.
 */
export function useStoredBacktestRun() {
  const [status, setStatus] = useState<StoredBacktestRunStatus>("idle");
  const [result, setResult] = useState<BacktestRunResultWireShape | null>(null);
  const [error, setError] = useState<StoredBacktestRunError | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const timerRef = useRef<number | null>(null);
  const statusRef = useRef<StoredBacktestRunStatus>("idle");

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
        const res = await triggerStoredBacktest(strategyName, symbol, startIso, endIso);
        setResult(res);
        statusRef.current = "done";
        setStatus("done");
      } catch (err: unknown) {
        if (err instanceof StoredBacktestError) {
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
