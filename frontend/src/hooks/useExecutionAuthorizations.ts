import { useEffect, useRef, useState } from "react";
import {
  fetchExecutionAuthorizations,
  type ExecutionAuthorizationWireShape,
} from "../services/api-client";

export type AuthorizationDecisionFilter = "all" | "approved" | "rejected";

type Load = {
  filter: AuthorizationDecisionFilter;
  rows: ExecutionAuthorizationWireShape[] | null;
  loading: boolean;
  error: string | null;
};

export function useExecutionAuthorizations(filter: AuthorizationDecisionFilter) {
  const [refreshKey, setRefreshKey] = useState(0);
  const [state, setState] = useState<Load>({ filter, rows: null, loading: true, error: null });
  const requestId = useRef(0);

  useEffect(() => {
    const id = ++requestId.current;
    setState((previous) => ({
      filter,
      rows: previous.filter === filter ? previous.rows : null,
      loading: true,
      error: null,
    }));
    fetchExecutionAuthorizations({ decision: filter === "all" ? undefined : filter })
      .then((data) => {
        if (id === requestId.current) {
          setState({ filter, rows: data.authorizations, loading: false, error: null });
        }
      })
      .catch((error: unknown) => {
        if (id === requestId.current) {
          setState((previous) => ({
            filter,
            rows: previous.filter === filter ? previous.rows : null,
            loading: false,
            error: error instanceof Error ? error.message : "Request failed",
          }));
        }
      });
    return () => { requestId.current += 1; };
  }, [filter, refreshKey]);

  // The filter can change before its effect starts. Hide the previous
  // filter's rows immediately on that intervening render.
  const load: Load = state.filter === filter
    ? state
    : { filter, rows: null, loading: true, error: null };
  return {
    ...load,
    invalidate: () => { requestId.current += 1; },
    refresh: () => {
      requestId.current += 1; // retire the prior response before React runs the next effect
      setRefreshKey((key) => key + 1);
    },
  };
}
