import { useEffect, useRef, useState } from "react";
import { ApiError, fetchStrategyOutcome, type StrategyOutcomeWireShape } from "../services/api-client";

type Load = {
  outcomeId: string | null;
  outcome: StrategyOutcomeWireShape | null;
  loading: boolean;
  error: string | null;
  notFound: boolean;
};

const idle = (outcomeId: string | null): Load => ({
  outcomeId, outcome: null, loading: outcomeId !== null, error: null, notFound: false,
});

/**
 * Recorded evidence for ONE persisted strategy outcome — GET
 * /intelligence/strategy-outcomes/{outcome_id} (task
 * `recorded-outcome-evidence-detail`). Read-only: one GET per selection or
 * manual `refresh()`, no polling, no retry. `outcomeId === null` makes no
 * request.
 *
 * Only the newest request may write state. A response (success OR failure) for
 * a superseded selection or refresh, or one that completes after the consumer
 * unmounted (the detail was closed), is ignored. A 404 is reported as
 * `notFound`, distinct from a request failure (`error`). A failed refresh keeps
 * the last-loaded outcome of the SAME id (and says so via `error`), but another
 * outcome's data is never shown for the current selection — not even for the
 * one render between a selection change and its effect.
 *
 * The result is the backend's recorded row as-is: nothing is normalised,
 * defaulted or recomputed here, so a null commission or snapshot stays null.
 */
export function useStrategyOutcomeDetail(outcomeId: string | null) {
  const [refreshKey, setRefreshKey] = useState(0);
  const [state, setState] = useState<Load>(() => idle(outcomeId));
  const requestId = useRef(0);

  useEffect(() => {
    const id = ++requestId.current;
    if (outcomeId === null) {
      setState(idle(null));
      return () => { requestId.current += 1; };
    }
    setState((previous) => ({
      outcomeId,
      outcome: previous.outcomeId === outcomeId ? previous.outcome : null,
      loading: true,
      error: null,
      notFound: false,
    }));
    fetchStrategyOutcome(outcomeId)
      .then((outcome) => {
        if (id === requestId.current) {
          setState({ outcomeId, outcome, loading: false, error: null, notFound: false });
        }
      })
      .catch((error: unknown) => {
        if (id !== requestId.current) return;
        if (error instanceof ApiError && error.status === 404) {
          setState({ outcomeId, outcome: null, loading: false, error: null, notFound: true });
          return;
        }
        setState((previous) => ({
          outcomeId,
          outcome: previous.outcomeId === outcomeId ? previous.outcome : null,
          loading: false,
          error: error instanceof Error ? error.message : "Request failed",
          notFound: false,
        }));
      });
    return () => { requestId.current += 1; };
  }, [outcomeId, refreshKey]);

  // The selection can change before its effect runs: never show the previous outcome's data.
  const load: Load = state.outcomeId === outcomeId ? state : idle(outcomeId);
  return {
    ...load,
    refresh: () => {
      requestId.current += 1; // retire the in-flight response before the next effect runs
      setRefreshKey((key) => key + 1);
    },
  };
}
