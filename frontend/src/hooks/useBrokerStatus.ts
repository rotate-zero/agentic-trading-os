import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  connectBroker,
  disconnectBroker,
  fetchBrokerStatus,
  subscribeBrokerSymbol,
  unsubscribeBrokerSymbol,
} from "../services/api-client";

// Poll interval reasoning, stated explicitly (same discipline
// useContextSnapshot.ts's own POLL_INTERVAL_MS comment models, and the
// same discipline this task's own prompt asked for).
//
// There's no WebSocket event for broker connection state changes —
// confirmed by grep against backend/app/api/websocket/channels.py's
// EVENT_TO_CHANNEL and every EventType in the codebase, same check
// useDataFeedStatus.ts's own comment already documents for Finnhub/
// Polygon. Unlike useContextSnapshot.ts, this poll isn't a safety net
// underneath a real push channel — it's the ONLY source of truth here.
//
// GET /broker/status is a genuinely free read: IBKRAdapter.is_connected()
// is `self._ib.isConnected()`, a local in-memory check with no round
// trip to Gateway (confirmed directly against ibkr_adapter.py) — so a
// short interval costs nothing server-side.
//
// This connection has no auto-reconnect (ibkr_adapter.py's own
// `_on_disconnected()` docstring: auto-reconnect is explicitly Market
// Data Engine's job in a future Phase 4, "not this adapter"). An
// unexpected drop — Gateway restarting, a network blip — means
// /broker/status silently flips to false with no other signal anywhere
// in this app; the only way this panel (or a second popped-out window's
// own independent mount of this same hook) finds out is this poll's
// next tick. That argues for shorter over longer: 10s is tight enough
// to catch an unexpected disconnect during a live session within one
// tick, unlike the 120s useDataFeedStatus.ts accepts for Finnhub/Polygon
// (optional data-quality providers, not the one connection this app's
// own order flow will eventually depend on) or the 60s
// useContextSnapshot.ts accepts underneath its own real push channel.
const POLL_INTERVAL_MS = 10_000;

export interface UseBrokerStatusResult {
  connected: boolean | null; // null = not yet loaded
  statusLoading: boolean;
  statusError: string | null;
  refetchStatus: () => void;

  connecting: boolean;
  connectError: string | null;
  /** Resolves true on success (including the idempotent
   * "already_connected" case), false on a failed attempt — the caller
   * decides how to react, this hook just exposes connectError either
   * way with the backend's own real detail text (e.g. the 502 "Is IB
   * Gateway running?" message). */
  connect: () => Promise<boolean>;

  disconnecting: boolean;
  disconnectError: string | null;
  disconnect: () => Promise<void>;

  // This panel's own record of which symbols IT has successfully asked
  // the backend to subscribe, since the last connect/disconnect/reload
  // — NOT an authoritative read of what IBKR is actually streaming
  // right now. broker.py exposes no GET for that (only /broker/status's
  // plain connected boolean), and this hook doesn't fabricate one.
  // Known, accepted limitation: a symbol subscribed from a different
  // browser tab, or via curl, won't appear here — this list only ever
  // reflects actions taken through this exact hook instance.
  subscribedSymbols: string[];
  subscribing: boolean;
  symbolActionError: string | null;
  subscribe: (symbol: string) => Promise<boolean>;
  unsubscribe: (symbol: string) => Promise<void>;
}

/**
 * Owns GET /broker/status polling plus all four broker action routes
 * (connect/disconnect/subscribe/unsubscribe) for BrokerPanel.tsx — the
 * IBKR analog of useBacktestRun.ts's "trigger an action, track a
 * loading/result state" shape, but for a connection that has to stay
 * continuously monitored rather than a one-shot run.
 */
export function useBrokerStatus(): UseBrokerStatusResult {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  const [disconnecting, setDisconnecting] = useState(false);
  const [disconnectError, setDisconnectError] = useState<string | null>(null);

  const [subscribedSymbols, setSubscribedSymbols] = useState<string[]>([]);
  const [subscribing, setSubscribing] = useState(false);
  const [symbolActionError, setSymbolActionError] = useState<string | null>(null);

  // Tracks the last polled value so a poll-detected true -> false
  // transition (an external/unexpected drop, not caused by this hook's
  // own disconnect()) can clear subscribedSymbols too — a disconnected
  // adapter has zero subscriptions, no matter what disconnected it.
  const prevConnectedRef = useRef<boolean | null>(null);

  const refetchStatus = useCallback(() => {
    fetchBrokerStatus()
      .then((wire) => {
        if (prevConnectedRef.current === true && wire.connected === false) {
          setSubscribedSymbols([]);
        }
        prevConnectedRef.current = wire.connected;
        setConnected(wire.connected);
        setStatusError(null);
      })
      .catch((err: unknown) => {
        const detail = err instanceof ApiError ? err.message : String(err);
        // A failed status read is "no fresh read," not "disconnected" —
        // same honest-state convention useDataFeedStatus.ts's own
        // Promise.allSettled handling uses: the previous `connected`
        // reading is left in place rather than fabricated.
        setStatusError(detail);
      })
      .finally(() => setStatusLoading(false));
  }, []);

  useEffect(() => {
    refetchStatus();
    const interval = setInterval(refetchStatus, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refetchStatus]);

  const connect = useCallback(async (): Promise<boolean> => {
    setConnecting(true);
    setConnectError(null);
    try {
      const res = await connectBroker();
      // A genuine new connection always starts with zero subscriptions
      // (broker.py's connect() constructs a fresh IBKRAdapter() in that
      // branch); "already_connected" means nothing changed backend-side,
      // so this panel's own existing subscribedSymbols record stays valid.
      if (res.status === "connected") {
        setSubscribedSymbols([]);
      }
      refetchStatus();
      return true;
    } catch (err: unknown) {
      const detail = err instanceof ApiError ? err.message : String(err);
      setConnectError(detail);
      return false;
    } finally {
      setConnecting(false);
    }
  }, [refetchStatus]);

  const disconnect = useCallback(async (): Promise<void> => {
    setDisconnecting(true);
    setDisconnectError(null);
    try {
      await disconnectBroker();
      setSubscribedSymbols([]);
    } catch (err: unknown) {
      const detail = err instanceof ApiError ? err.message : String(err);
      setDisconnectError(detail);
    } finally {
      setDisconnecting(false);
      refetchStatus();
    }
  }, [refetchStatus]);

  const subscribe = useCallback(async (symbol: string): Promise<boolean> => {
    setSubscribing(true);
    setSymbolActionError(null);
    try {
      const res = await subscribeBrokerSymbol(symbol);
      setSubscribedSymbols((prev) => (prev.includes(res.symbol) ? prev : [...prev, res.symbol]));
      return true;
    } catch (err: unknown) {
      const detail = err instanceof ApiError ? err.message : String(err);
      setSymbolActionError(detail);
      return false;
    } finally {
      setSubscribing(false);
    }
  }, []);

  const unsubscribe = useCallback(async (symbol: string): Promise<void> => {
    // Optimistic — same low-stakes reasoning useScannerUniverse.ts's own
    // removeSymbol() already gives for its identical pattern: waiting on
    // a round trip before the row disappears would just feel laggy.
    setSubscribedSymbols((prev) => prev.filter((s) => s !== symbol));
    try {
      await unsubscribeBrokerSymbol(symbol);
    } catch (err: unknown) {
      const detail = err instanceof ApiError ? err.message : String(err);
      setSymbolActionError(detail);
    }
  }, []);

  return {
    connected,
    statusLoading,
    statusError,
    refetchStatus,
    connecting,
    connectError,
    connect,
    disconnecting,
    disconnectError,
    disconnect,
    subscribedSymbols,
    subscribing,
    symbolActionError,
    subscribe,
    unsubscribe,
  };
}
