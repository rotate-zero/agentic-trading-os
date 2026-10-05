import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
   * "already_connected" case), false on a failed attempt or when the call
   * is refused because a connect/disconnect is already pending (nothing is
   * sent and connectError is untouched in that case) — the caller
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
  /** Resolves true only when the backend accepted the symbol AND it was
   * recorded in `subscribedSymbols`. False for a rejection, a refused
   * (duplicate/incompatible) call, or a completion made obsolete by a
   * confirmed disconnect/new connection/unmount. */
  subscribe: (symbol: string) => Promise<boolean>;
  unsubscribe: (symbol: string) => Promise<void>;

  /** Symbol whose unsubscribe is in flight. Its row is hidden from
   * `subscribedSymbols` meanwhile (optimistic) and comes back if the
   * request fails. */
  unsubscribingSymbol: string | null;
  /** True while ANY of connect / disconnect / subscribe / unsubscribe is
   * pending — the panel uses it to disable the controls the hook would
   * refuse anyway. */
  mutating: boolean;
}

/**
 * Owns GET /broker/status polling plus all four broker action routes
 * (connect/disconnect/subscribe/unsubscribe) for BrokerPanel.tsx — the
 * IBKR analog of useBacktestRun.ts's "trigger an action, track a
 * loading/result state" shape, but for a connection that has to stay
 * continuously monitored rather than a one-shot run.
 *
 * Request safety (task `broker-panel-request-safety`):
 *
 * - Status reads carry an identity. Every refetchStatus() takes the next
 *   number from one counter and its outcome — success, error AND the end
 *   of `statusLoading` — is applied only if that number is still the
 *   latest and the hook is mounted. connect()/disconnect() also advance
 *   the counter when they START, so a read begun before the action can
 *   never overwrite the action's result or its follow-up read.
 * - Mutations are guarded by refs set synchronously before the first
 *   `await` (state would only change on the next render, so two Enter
 *   presses in one tick would both pass). connect/disconnect are mutually
 *   exclusive and non-repeatable; subscribe/unsubscribe allow one symbol
 *   action at a time and are refused while a connect/disconnect is
 *   pending. A refused call sends nothing. connect/disconnect are NOT
 *   refused during a pending symbol action: they supersede it.
 * - Superseding = the list reset. A confirmed disconnect, a confirmed new
 *   connection ("connected") or a poll-detected true -> false drop clears
 *   subscribedSymbols AND detaches any pending symbol action (its token is
 *   dropped), so that action's late success/failure touches no state. A
 *   failed disconnect supersedes nothing.
 * - Unsubscribe never edits the confirmed list up front: the row is only
 *   hidden while the request is in flight. Failure un-hides it (original
 *   position); success removes it.
 * - Unmount sets a flag, clears the interval, and every completion checks
 *   it: no state update and no follow-up status read after unmount. Requests
 *   already sent are not cancelled.
 */
interface SymbolOp {
  kind: "subscribe" | "unsubscribe";
  symbol: string;
}

export function useBrokerStatus(): UseBrokerStatusResult {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState<string | null>(null);

  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState<string | null>(null);

  const [disconnecting, setDisconnecting] = useState(false);
  const [disconnectError, setDisconnectError] = useState<string | null>(null);

  // Confirmed local record. subscribedSymbols (returned) is this minus the
  // row whose unsubscribe is pending.
  const [confirmedSymbols, setConfirmedSymbols] = useState<string[]>([]);
  const [subscribing, setSubscribing] = useState(false);
  const [unsubscribingSymbol, setUnsubscribingSymbol] = useState<string | null>(null);
  const [symbolActionError, setSymbolActionError] = useState<string | null>(null);

  // Tracks the last polled value so a poll-detected true -> false
  // transition (an external/unexpected drop, not caused by this hook's
  // own disconnect()) can clear subscribedSymbols too — a disconnected
  // adapter has zero subscriptions, no matter what disconnected it.
  const prevConnectedRef = useRef<boolean | null>(null);

  const mountedRef = useRef(true);
  const statusSeqRef = useRef(0);
  const connectionOpRef = useRef<"connect" | "disconnect" | null>(null);
  const symbolOpRef = useRef<SymbolOp | null>(null);

  // Clears the local record and detaches a pending symbol action: that
  // action's completion compares its token against symbolOpRef and, no
  // longer matching, applies nothing.
  const resetSubscriptions = useCallback(() => {
    symbolOpRef.current = null;
    setSubscribing(false);
    setUnsubscribingSymbol(null);
    setConfirmedSymbols([]);
  }, []);

  const refetchStatus = useCallback(() => {
    if (!mountedRef.current) return;
    const id = ++statusSeqRef.current;
    const isCurrent = () => mountedRef.current && id === statusSeqRef.current;
    fetchBrokerStatus().then(
      (wire) => {
        if (!isCurrent()) return;
        if (prevConnectedRef.current === true && wire.connected === false) {
          resetSubscriptions();
        }
        prevConnectedRef.current = wire.connected;
        setConnected(wire.connected);
        setStatusError(null);
        setStatusLoading(false);
      },
      (err: unknown) => {
        if (!isCurrent()) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        // A failed status read is "no fresh read," not "disconnected" —
        // same honest-state convention useDataFeedStatus.ts's own
        // Promise.allSettled handling uses: the previous `connected`
        // reading is left in place rather than fabricated.
        setStatusError(detail);
        setStatusLoading(false);
      },
    );
  }, [resetSubscriptions]);

  useEffect(() => {
    mountedRef.current = true;
    refetchStatus();
    const interval = setInterval(refetchStatus, POLL_INTERVAL_MS);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [refetchStatus]);

  const connect = useCallback(async (): Promise<boolean> => {
    if (connectionOpRef.current !== null) return false;
    connectionOpRef.current = "connect";
    statusSeqRef.current++; // reads begun before this action can no longer land
    setConnecting(true);
    setConnectError(null);
    try {
      const res = await connectBroker();
      if (!mountedRef.current) return true;
      // A genuine new connection always starts with zero subscriptions
      // (broker.py's connect() constructs a fresh IBKRAdapter() in that
      // branch); "already_connected" means nothing changed backend-side,
      // so this panel's own existing subscribedSymbols record stays valid.
      if (res.status === "connected") {
        resetSubscriptions();
      }
      return true;
    } catch (err: unknown) {
      if (mountedRef.current) {
        setConnectError(err instanceof ApiError ? err.message : String(err));
      }
      return false;
    } finally {
      connectionOpRef.current = null;
      if (mountedRef.current) {
        setConnecting(false);
        refetchStatus(); // also after a failure: the start of the action invalidated in-flight reads
      }
    }
  }, [refetchStatus, resetSubscriptions]);

  const disconnect = useCallback(async (): Promise<void> => {
    if (connectionOpRef.current !== null) return;
    connectionOpRef.current = "disconnect";
    statusSeqRef.current++;
    setDisconnecting(true);
    setDisconnectError(null);
    try {
      await disconnectBroker();
      if (!mountedRef.current) return;
      resetSubscriptions(); // confirmed disconnect
    } catch (err: unknown) {
      if (mountedRef.current) {
        setDisconnectError(err instanceof ApiError ? err.message : String(err));
      }
    } finally {
      connectionOpRef.current = null;
      if (mountedRef.current) {
        setDisconnecting(false);
        refetchStatus();
      }
    }
  }, [refetchStatus, resetSubscriptions]);

  const subscribe = useCallback(async (symbol: string): Promise<boolean> => {
    if (connectionOpRef.current !== null || symbolOpRef.current !== null) return false;
    const op: SymbolOp = { kind: "subscribe", symbol };
    symbolOpRef.current = op;
    setSubscribing(true);
    setSymbolActionError(null);
    try {
      const res = await subscribeBrokerSymbol(symbol);
      if (!mountedRef.current || symbolOpRef.current !== op) return false;
      setConfirmedSymbols((prev) => (prev.includes(res.symbol) ? prev : [...prev, res.symbol]));
      return true;
    } catch (err: unknown) {
      if (mountedRef.current && symbolOpRef.current === op) {
        setSymbolActionError(err instanceof ApiError ? err.message : String(err));
      }
      return false;
    } finally {
      if (mountedRef.current && symbolOpRef.current === op) {
        symbolOpRef.current = null;
        setSubscribing(false);
      }
    }
  }, []);

  const unsubscribe = useCallback(async (symbol: string): Promise<void> => {
    if (connectionOpRef.current !== null || symbolOpRef.current !== null) return;
    const op: SymbolOp = { kind: "unsubscribe", symbol };
    symbolOpRef.current = op;
    // Optimistic only in what is SHOWN: the confirmed list is not edited
    // until the backend confirms, so a failure just un-hides the row —
    // same low-stakes "don't wait on a round trip before the row
    // disappears" reasoning useScannerUniverse.ts's removeSymbol() gives.
    setUnsubscribingSymbol(symbol);
    setSymbolActionError(null);
    try {
      await unsubscribeBrokerSymbol(symbol);
      if (!mountedRef.current || symbolOpRef.current !== op) return;
      setConfirmedSymbols((prev) => prev.filter((s) => s !== symbol));
    } catch (err: unknown) {
      if (mountedRef.current && symbolOpRef.current === op) {
        setSymbolActionError(err instanceof ApiError ? err.message : String(err));
      }
    } finally {
      if (mountedRef.current && symbolOpRef.current === op) {
        symbolOpRef.current = null;
        setUnsubscribingSymbol(null);
      }
    }
  }, []);

  const subscribedSymbols = useMemo(
    () => (unsubscribingSymbol === null ? confirmedSymbols : confirmedSymbols.filter((s) => s !== unsubscribingSymbol)),
    [confirmedSymbols, unsubscribingSymbol],
  );
  const mutating = connecting || disconnecting || subscribing || unsubscribingSymbol !== null;

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
    unsubscribingSymbol,
    mutating,
  };
}
