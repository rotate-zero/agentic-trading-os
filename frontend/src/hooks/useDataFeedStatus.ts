import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, connectFinnhub, fetchFinnhubStatus, fetchMarketDataStatus } from "../services/api-client";

// Poll interval reasoning, stated explicitly (same discipline
// useContextSnapshot.ts's own POLL_INTERVAL_MS comment uses).
//
// No push path exists for this at all — grepped
// backend/app/api/websocket/channels.py's EVENT_TO_CHANNEL directly:
// there is no EventType for a provider connecting/disconnecting, so unlike
// useContextSnapshot.ts (WebSocket-primary, poll as a safety net) this hook
// is pure poll, all the time, by necessity rather than choice.
//
// Genuinely slower-changing than Context, though: Context's fastest cadence
// is a 15-minute per-symbol Fundamentals/News timer plus a handful of
// session-boundary changes a day (context_engine/engine.py's own docstring).
// This data changes at most three times in a normal session — connect once
// at startup, and then only again on a rare unexpected drop (confirmed via
// finnhub_provider.py: no auto-reconnect on a closed WebSocket yet, that's
// deferred to a future Phase 4 ConnectionManager) or a deliberate manual
// disconnect. Also, this badge isn't a live session's primary way to notice
// an outage — ticks visibly stop flowing on the chart itself, immediately,
// which is a faster and more obvious signal than any poll interval here
// could be. So this is a corroborating, glanceable summary, not a
// time-critical alarm, and can afford to be slower than Context's 60s
// rather than faster. Both underlying routes are cheap, synchronous,
// zero-I/O boolean/registry checks (confirmed directly in
// finnhub_data.py's/market_data.py's own `status()` handlers), so the
// interval below is chosen for "don't make someone stare at a stale badge
// for too long after a real drop," not for server load — 2 minutes is
// short enough that a stale reading is a minor annoyance, not a trap, and
// long enough to stay meaningfully looser than Context's own cadence.
const POLL_INTERVAL_MS = 120_000;

export interface UseDataFeedStatusResult {
  finnhubConnected: boolean | null;
  polygonConnected: boolean | null;
  polygonRole: "historical+streaming" | "historical" | null;
  loading: boolean;
  /** True while a reconnectFinnhub() call is in flight. */
  reconnecting: boolean;
  /** Set only when the most recent reconnectFinnhub() call failed; cleared on the next attempt. */
  reconnectError: string | null;
  refetch: () => void;
  /**
   * POST /finnhub/connect — see connectFinnhub()'s own doc comment in
   * api-client.ts for why this exists only for Finnhub, not Polygon.
   * Always followed by an immediate refetch() (success or failure) so the
   * badge reflects the real post-attempt state rather than an assumed one.
   */
  reconnectFinnhub: () => void;
}

/**
 * Backend read side for the two auto-connect-on-startup market data
 * providers — GET /finnhub/status + GET /market-data/status, unsurfaced
 * anywhere in the UI until this task. Deliberately two separate fetches
 * rather than one combined endpoint — no such combined route exists on the
 * backend (each provider owns its own router/module-level `_provider`, per
 * finnhub_data.py's/market_data.py's own docstrings), and inventing one here
 * would be scope beyond a read-only frontend indicator.
 *
 * Pure-poll, no WebSocket — see POLL_INTERVAL_MS's own comment above for why
 * no push path exists for this and why the interval is deliberately looser
 * than useContextSnapshot.ts's.
 *
 * Uses a `mountedRef` guard (useContextSnapshot.ts's pattern, not
 * useOpportunities.ts's single-closure `cancelled` flag) since `load()` here
 * also fires repeatedly — every poll tick, plus a manual `refetch()` after
 * every `reconnectFinnhub()` attempt — within a single effect run.
 */
export function useDataFeedStatus(): UseDataFeedStatusResult {
  const [finnhubConnected, setFinnhubConnected] = useState<boolean | null>(null);
  const [polygonConnected, setPolygonConnected] = useState<boolean | null>(null);
  const [polygonRole, setPolygonRole] = useState<"historical+streaming" | "historical" | null>(null);
  const [loading, setLoading] = useState(true);
  const [reconnecting, setReconnecting] = useState(false);
  const [reconnectError, setReconnectError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const load = useCallback(() => {
    // Independent providers, independent failure domains — Promise.allSettled
    // rather than Promise.all so one provider's route erroring (e.g. a
    // transient network blip) doesn't blank out the other provider's
    // perfectly good reading.
    Promise.allSettled([fetchFinnhubStatus(), fetchMarketDataStatus()]).then(([finnhubResult, polygonResult]) => {
      if (!mountedRef.current) return;

      if (finnhubResult.status === "fulfilled") {
        setFinnhubConnected(finnhubResult.value.connected);
      } else {
        const detail = finnhubResult.reason instanceof ApiError ? finnhubResult.reason.message : String(finnhubResult.reason);
        console.error(`useDataFeedStatus: GET /finnhub/status failed — ${detail}`);
        // Leave the previous reading in place rather than flipping to a
        // fabricated "disconnected" on a transient fetch failure — same
        // "honest absence over fabricated state" posture this project uses
        // elsewhere, applied to "no fresh reading" rather than "no data."
      }

      if (polygonResult.status === "fulfilled") {
        setPolygonConnected(polygonResult.value.connected);
        setPolygonRole(polygonResult.value.role);
      } else {
        const detail = polygonResult.reason instanceof ApiError ? polygonResult.reason.message : String(polygonResult.reason);
        console.error(`useDataFeedStatus: GET /market-data/status failed — ${detail}`);
      }

      setLoading(false);
    });
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [load]);

  const reconnectFinnhub = useCallback(() => {
    setReconnecting(true);
    setReconnectError(null);
    connectFinnhub()
      .catch((err: unknown) => {
        const detail = err instanceof ApiError ? err.message : String(err);
        if (mountedRef.current) setReconnectError(detail);
      })
      .finally(() => {
        if (!mountedRef.current) return;
        setReconnecting(false);
        load(); // reflect the real post-attempt state, whether it succeeded or not
      });
  }, [load]);

  return {
    finnhubConnected,
    polygonConnected,
    polygonRole,
    loading,
    reconnecting,
    reconnectError,
    refetch: load,
    reconnectFinnhub,
  };
}
