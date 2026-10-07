import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  StoredBacktestError,
  triggerStoredBacktestSweep,
  type StoredSweepResultWireShape,
} from "../services/api-client";

export type StoredBacktestSweepRunStatus = "idle" | "running" | "done" | "error";

export interface StoredBacktestSweepRunError {
  message: string;
  status: number;
  /** Backend's stable code ("invalid_backtest_request", "backtest_sweep_too_large"),
   * or null for the plain-string 409 / an unparseable or network failure. */
  code: string | null;
}

/** The exact request that was sent, kept with the result or error it produced
 * so the display never changes if the form is edited afterwards. */
export interface StoredSweepSubmission {
  strategyName: string;
  symbols: string[];
  startIso: string;
  endIso: string;
  /** The Eastern wall-clock strings the person entered, for display only. */
  startLocal: string;
  endLocal: string;
}

/**
 * Owns one POST /backtest/sweep/stored call end to end — sibling to
 * useStoredBacktestRun.ts / useBacktestSweepRun.ts with the same status
 * machine and elapsed timer, but its own request/result shape. The
 * duplicate-submit guard is a synchronous ref (`statusRef`), set before any
 * state update, so two calls in the same tick send exactly one request.
 * `submitted` is set synchronously at submission and replaced only by the next
 * accepted submission. The request is one synchronous call with no per-symbol
 * progress signal, so only elapsed time is reported.
 */
export function useStoredBacktestSweepRun() {
  const [status, setStatus] = useState<StoredBacktestSweepRunStatus>("idle");
  const [result, setResult] = useState<StoredSweepResultWireShape | null>(null);
  const [error, setError] = useState<StoredBacktestSweepRunError | null>(null);
  const [submitted, setSubmitted] = useState<StoredSweepSubmission | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const timerRef = useRef<number | null>(null);
  const statusRef = useRef<StoredBacktestSweepRunStatus>("idle");

  const stopTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => stopTimer, [stopTimer]);

  const run = useCallback(
    async (submission: StoredSweepSubmission) => {
      if (statusRef.current === "running") return;
      statusRef.current = "running";
      setStatus("running");
      setSubmitted({ ...submission, symbols: [...submission.symbols] });
      setError(null);
      setResult(null);
      setElapsedSeconds(0);

      const startedAtMs = Date.now();
      stopTimer();
      timerRef.current = window.setInterval(() => {
        setElapsedSeconds(Math.floor((Date.now() - startedAtMs) / 1000));
      }, 1000);

      try {
        const res = await triggerStoredBacktestSweep(
          submission.strategyName,
          submission.symbols,
          submission.startIso,
          submission.endIso,
        );
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

  return { status, result, error, submitted, elapsedSeconds, run };
}
