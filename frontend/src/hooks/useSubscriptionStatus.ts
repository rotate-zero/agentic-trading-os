import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchSubscriptionStatus, type SubscriptionStatusWireShape } from "../services/api-client";

/**
 * Read side for the Broker panel's "Subscription diagnostics" section —
 * GET /market/subscription-status (task `provider-subscription-diagnostics`):
 * the current streaming provider's LOCALLY TRACKED subscription requests.
 * Not an authoritative read of what the provider is streaming, and not
 * related to `useBrokerStatus`'s own panel-local `subscribedSymbols`.
 *
 * Manual only: one load when the section's body mounts (expanding it) and
 * one per `refresh()`. No interval, no WebSocket, and no refresh triggered by
 * the panel's connect/subscribe/unsubscribe actions.
 *
 * Request safety (same request-identity pattern as `useScannerObservation`):
 * - every load takes the next number from one per-hook counter and is applied
 *   only while that number is still the latest, so a newer Refresh
 *   supersedes every older request — success or failure — including a hung
 *   one. Underlying fetches are not cancelled; their results are discarded.
 * - the effect cleanup advances the counter, so unmounting (collapsing the
 *   section or the panel) invalidates anything in flight and a late
 *   completion never touches state.
 * - a failed Refresh keeps the last successfully loaded reading on screen and
 *   only sets `error`; a later success clears it. With nothing loaded yet a
 *   failure leaves `data` null plus `error`, so initial loading, a request
 *   failure and a backend "unavailable" answer stay distinct.
 */
interface SubscriptionStatusState {
  data: SubscriptionStatusWireShape | null;
  error: string | null;
  loadedAt: Date | null;
  loading: boolean;
}

const INITIAL: SubscriptionStatusState = { data: null, error: null, loadedAt: null, loading: true };

export function useSubscriptionStatus() {
  const [state, setState] = useState<SubscriptionStatusState>(INITIAL);
  const latestRequestRef = useRef(0);

  const load = useCallback(() => {
    const requestId = ++latestRequestRef.current;
    setState((prev) => ({ ...prev, loading: true }));

    fetchSubscriptionStatus()
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
