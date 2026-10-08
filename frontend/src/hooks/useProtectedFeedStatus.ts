import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchProtectedFeedStatus, type ProtectedFeedStatusWireShape } from "../services/api-client";

/**
 * Read side for the Broker panel's "Protected feed" section —
 * GET /market/protected-feed-status (task `protected-feed-reconciliation-status`):
 * what the protected-symbol subscription owner last attempted. Request
 * evidence only; not a read of what the provider is streaming.
 *
 * Manual only: one load when the section's body mounts (expanding it) and one
 * per `refresh()`. No interval, no WebSocket, no refresh triggered by the
 * panel's other actions.
 *
 * Request safety (same request-identity pattern as `useSubscriptionStatus`):
 * - every load takes the next number from one per-hook counter and is applied
 *   only while that number is still the latest, so a newer Refresh supersedes
 *   every older request — success or failure — including a hung one.
 * - the effect cleanup advances the counter, so unmounting invalidates anything
 *   in flight and a late completion never touches state.
 * - a failed Refresh keeps the last successfully loaded reading (and its load
 *   time) on screen and only sets `error`; a later success clears it. With
 *   nothing loaded yet a failure leaves `data` null plus `error`, so initial
 *   loading, an HTTP failure and a backend/worker state stay distinct.
 */
interface ProtectedFeedStatusState {
  data: ProtectedFeedStatusWireShape | null;
  error: string | null;
  loadedAt: Date | null;
  loading: boolean;
}

const INITIAL: ProtectedFeedStatusState = { data: null, error: null, loadedAt: null, loading: true };

export function useProtectedFeedStatus() {
  const [state, setState] = useState<ProtectedFeedStatusState>(INITIAL);
  const latestRequestRef = useRef(0);

  const load = useCallback(() => {
    const requestId = ++latestRequestRef.current;
    setState((prev) => ({ ...prev, loading: true }));

    fetchProtectedFeedStatus()
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
