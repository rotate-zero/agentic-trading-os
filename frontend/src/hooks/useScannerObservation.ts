import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchScannerObservation, type ScannerObservationWireShape } from "../services/api-client";

/**
 * Read side for the Scanner panel's "Scheduled observation" section —
 * GET /scanner/observation (task `scanner-observation-status`), the
 * retained snapshot of the optional observation worker, separate from the
 * on-demand `useScannerState`.
 *
 * Manual only: one load when the section's body mounts (expanding it) and
 * one per `refresh()`. No interval, no WebSocket.
 *
 * Request safety (same request-identity pattern as `useScannerState`):
 * - every load takes the next number from one per-hook counter and is applied
 *   only while that number is still the latest, so a newer Refresh
 *   supersedes every older request — success or failure — including a hung
 *   one. Underlying fetches are not cancelled; their results are discarded.
 * - the effect cleanup advances the counter, so unmounting (collapsing the
 *   section, collapsing the panel, switching away) invalidates anything in
 *   flight and a late completion never touches state.
 * - a failed Refresh keeps the last successfully loaded observation on
 *   screen and only sets `error`; a later success clears it. With nothing
 *   loaded yet a failure leaves `data` null plus `error`, so initial
 *   loading, request failure and a backend "unavailable" answer stay
 *   distinct. (A worker whose latest attempt failed is NOT a request
 *   failure: that arrives as a normal response inside `data`.)
 */
interface ObservationState {
  data: ScannerObservationWireShape | null;
  error: string | null;
  loadedAt: Date | null;
  loading: boolean;
}

const INITIAL: ObservationState = { data: null, error: null, loadedAt: null, loading: true };

export function useScannerObservation() {
  const [state, setState] = useState<ObservationState>(INITIAL);
  const latestRequestRef = useRef(0);

  const load = useCallback(() => {
    const requestId = ++latestRequestRef.current;
    setState((prev) => ({ ...prev, loading: true }));

    fetchScannerObservation()
      .then((wire) => {
        if (requestId !== latestRequestRef.current) return;
        setState({ data: wire, error: null, loadedAt: new Date(), loading: false });
      })
      .catch((err: unknown) => {
        if (requestId !== latestRequestRef.current) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        setState((prev) => ({ ...prev, error: detail, loading: false }));
      });
  }, []);

  useEffect(() => {
    load();
    return () => {
      // Unmount: invalidate anything in flight.
      latestRequestRef.current += 1;
    };
  }, [load]);

  return { ...state, refresh: load };
}
