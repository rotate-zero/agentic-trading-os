import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchBacktestSelectionSummary, type BacktestSelectionSummaryWireShape } from "../services/api-client";

interface SummarySnapshot {
  key: string;
  summary: BacktestSelectionSummaryWireShape | null;
  loading: boolean;
  error: string | null;
}

/** Independent from the capped outcome list; one request per applied selection. */
export function useBacktestSelectionSummary(kind: "run_id" | "sweep_id", id?: string) {
  const key = id ? JSON.stringify([kind, id]) : null;
  const [snapshot, setSnapshot] = useState<SummarySnapshot | null>(null);
  const latestRequest = useRef(0);

  const refetch = useCallback(() => {
    const requestId = ++latestRequest.current;
    if (!key || !id) {
      setSnapshot(null);
      return;
    }
    setSnapshot((previous) => ({
      key,
      summary: previous?.key === key ? previous.summary : null,
      loading: true,
      error: null,
    }));
    const selection = kind === "run_id" ? { runId: id } : { sweepId: id };
    fetchBacktestSelectionSummary(selection)
      .then((summary) => {
        if (requestId !== latestRequest.current) return;
        setSnapshot({ key, summary, loading: false, error: null });
      })
      .catch((error: unknown) => {
        if (requestId !== latestRequest.current) return;
        setSnapshot({
          key, summary: null, loading: false,
          error: error instanceof ApiError ? error.message : String(error),
        });
      });
  }, [key, kind, id]);

  useEffect(() => {
    refetch();
    return () => { latestRequest.current += 1; };
  }, [refetch]);

  const current = key && snapshot?.key === key ? snapshot : null;
  return {
    summary: current?.summary ?? null,
    loading: !!key && (current?.loading ?? true),
    error: current?.error ?? null,
    refetch,
  };
}
