import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  fetchExecutionTradeDetail,
  type ExecutionTradeDetailWireShape,
} from "../services/api-client";

type Load = {
  tradeId: string | null;
  detail: ExecutionTradeDetailWireShape | null;
  loading: boolean;
  error: string | null;
  notFound: boolean;
};

const idle = (tradeId: string | null): Load => ({
  tradeId, detail: null, loading: tradeId !== null, error: null, notFound: false,
});

/**
 * Lifecycle detail for one recorded authorization. Read-only: one GET per
 * selection or manual refresh, no polling, no retry. `tradeId === null` makes
 * no request. Only the newest request may write state: a response for a
 * superseded selection or refresh, or one that completes after the consumer
 * unmounted (section collapsed), is ignored. A 404 is reported as `notFound`,
 * distinct from a request failure (`error`), and a failed refresh keeps the
 * last-loaded detail of the SAME trade (never another trade's).
 */
export function useExecutionTradeDetail(tradeId: string | null) {
  const [refreshKey, setRefreshKey] = useState(0);
  const [state, setState] = useState<Load>(() => idle(tradeId));
  const requestId = useRef(0);

  useEffect(() => {
    const id = ++requestId.current;
    if (tradeId === null) {
      setState(idle(null));
      return () => { requestId.current += 1; };
    }
    setState((previous) => ({
      tradeId,
      detail: previous.tradeId === tradeId ? previous.detail : null,
      loading: true,
      error: null,
      notFound: false,
    }));
    fetchExecutionTradeDetail(tradeId)
      .then((detail) => {
        if (id === requestId.current) {
          setState({ tradeId, detail, loading: false, error: null, notFound: false });
        }
      })
      .catch((error: unknown) => {
        if (id !== requestId.current) return;
        if (error instanceof ApiError && error.status === 404) {
          setState({ tradeId, detail: null, loading: false, error: null, notFound: true });
          return;
        }
        setState((previous) => ({
          tradeId,
          detail: previous.tradeId === tradeId ? previous.detail : null,
          loading: false,
          error: error instanceof Error ? error.message : "Request failed",
          notFound: false,
        }));
      });
    return () => { requestId.current += 1; };
  }, [tradeId, refreshKey]);

  // The selection can change before its effect runs: never show the previous trade's data.
  const load: Load = state.tradeId === tradeId ? state : idle(tradeId);
  return {
    ...load,
    refresh: () => {
      requestId.current += 1; // retire the in-flight response before the next effect runs
      setRefreshKey((key) => key + 1);
    },
  };
}
