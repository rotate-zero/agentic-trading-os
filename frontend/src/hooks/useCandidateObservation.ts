import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchCandidateObservation, type CandidateObservationWireShape } from "../services/api-client";

/**
 * Read side for the Execution panel's "Candidate observation" section —
 * GET /intelligence/candidate-observation (task `candidate-observation-status-ui`),
 * the C2 observation-only candidate snapshot. Not selection, not authorization.
 *
 * Manual only: one load when the section body mounts (expanding it) and one
 * per `refresh()`. No interval, no WebSocket.
 *
 * Request safety (same request-identity pattern as `useScannerObservation`):
 * - every load takes the next number from one per-hook counter and is applied
 *   only while it is still the latest, so a newer Refresh supersedes every
 *   older request — success or failure — including a hung one. Fetches are
 *   not cancelled; their results are discarded. Refresh stays usable while a
 *   request is pending.
 * - the effect cleanup advances the counter, so collapsing the section (or
 *   the panel) invalidates anything in flight and a late completion never
 *   touches state.
 * - a failed Refresh keeps the last successfully loaded snapshot and only
 *   sets `error`; a later success clears it. With nothing loaded a failure
 *   leaves `data` null plus `error`, so initial loading, request failure and
 *   a backend `status: "unavailable"` answer (which arrives as normal `data`)
 *   stay distinct.
 */
interface CandidateObservationState {
  data: CandidateObservationWireShape | null;
  error: string | null;
  loadedAt: Date | null;
  loading: boolean;
}

const INITIAL: CandidateObservationState = { data: null, error: null, loadedAt: null, loading: true };

export function useCandidateObservation() {
  const [state, setState] = useState<CandidateObservationState>(INITIAL);
  const latestRequestRef = useRef(0);

  const load = useCallback(() => {
    const requestId = ++latestRequestRef.current;
    setState((prev) => ({ ...prev, loading: true }));

    fetchCandidateObservation()
      .then((wire) => {
        if (requestId !== latestRequestRef.current) return;
        setState({ data: wire, error: null, loadedAt: new Date(), loading: false });
      })
      .catch((err: unknown) => {
        if (requestId !== latestRequestRef.current) return;
        const detail = err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Request failed";
        setState((prev) => ({ ...prev, error: detail, loading: false }));
      });
  }, []);

  useEffect(() => {
    load();
    return () => {
      // Unmount (section or panel collapsed): invalidate anything in flight.
      latestRequestRef.current += 1;
    };
  }, [load]);

  return { ...state, refresh: load };
}
