import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  fetchMarketStateSnapshot,
  type MarketStateCompositeWireShape,
  type MarketStateSymbolWireShape,
} from "../services/api-client";
import { workspaceSocket, type WireMessage } from "../services/websocket-client";

// Cross-symbol composite envelopes on this channel use this literal
// sentinel as `envelope.symbol` — never null/absent, unlike
// ContextChanged's own market-wide shape (decision #96). Confirmed
// directly against `app/market_state_engine/engine.py`'s
// `_CROSS_SYMBOL_SENTINEL` and `app/api/websocket/channels.py`'s own
// `EVENT_TO_CHANNEL` comment for `MARKET_STATE_CHANGED` before writing
// this hook's subscription below — not assumed from ContextChanged's
// convention.
const CROSS_SYMBOL_SENTINEL = "__MARKET__";

// Normalized, display-ready shapes — same camelCase-flattening split
// useContextSnapshot.ts's own Calendar/Fundamentals/News types
// establish for their own wire shapes.
export interface MarketStateScores {
  timeframe: string;
  candleTs: string;
  trendScore: number;
  volatilityRegimeScore: number;
  volumeRegimeScore: number;
  vwapRelationshipScore: number;
  // null on a symbol's first-ever recompute — no prior trend_score yet
  // to derive a rate of change from (decision #93). Normal, expected
  // absence, not an error — render it as such, never as a fabricated 0.
  accelerationScore: number | null;
}

export interface CrossSymbolMarketState {
  timeframe: string;
  candleTs: string;
  spyDirectionScore: number;
  qqqDirectionScore: number;
  iwmDirectionScore: number;
  trendAlignmentScore: number;
  riskOnScore: number;
  qqqLeadershipScore: number;
  iwmConfirmationScore: number;
}

function normalizeSymbolState(wire: MarketStateSymbolWireShape): MarketStateScores {
  return {
    timeframe: wire.timeframe,
    candleTs: wire.candle_ts,
    trendScore: wire.trend_score,
    volatilityRegimeScore: wire.volatility_regime_score,
    volumeRegimeScore: wire.volume_regime_score,
    vwapRelationshipScore: wire.vwap_relationship_score,
    accelerationScore: wire.acceleration_score,
  };
}

function normalizeMarket(wire: MarketStateCompositeWireShape): CrossSymbolMarketState {
  return {
    timeframe: wire.timeframe,
    candleTs: wire.candle_ts,
    spyDirectionScore: wire.spy_direction_score,
    qqqDirectionScore: wire.qqq_direction_score,
    iwmDirectionScore: wire.iwm_direction_score,
    trendAlignmentScore: wire.trend_alignment_score,
    riskOnScore: wire.risk_on_score,
    qqqLeadershipScore: wire.qqq_leadership_score,
    iwmConfirmationScore: wire.iwm_confirmation_score,
  };
}

// WebSocket-push reasoning, stated explicitly (same discipline
// useContextSnapshot.ts's own POLL_INTERVAL_MS comment used for its
// choice, rather than defaulting silently).
//
// AS OF this task, `EVENT_TO_CHANNEL` (backend/app/api/websocket/
// channels.py) routes `EventType.MARKET_STATE_CHANGED` to WebSocket
// channel "intelligence.market-state" (temp id
// `market-state-changed-websocket-channel`) — the gap this hook's own
// comment used to describe. useContextSnapshot.ts is the direct
// template for wiring push onto an existing fetch-based hook (decision
// #126), but its own resolution of "does the poll stay as a fallback"
// does NOT carry over here — a genuine difference in this engine's own
// update cadence, not a default:
//
// Context genuinely changes on a slow cadence (session boundaries a
// handful of times a day, plus a 15-minute per-symbol timer), so a
// dropped/reconnecting WebSocket session could otherwise go up to 15
// real minutes with no self-correcting signal — decision #126 kept
// Context's poll specifically to bound that worst case. Market State
// Engine recomputes on a materially faster cadence: DebounceScheduler's
// own ~1s floor / ~10s ceiling per symbol, ~1s floor / ~4s ceiling for
// the SPY/QQQ/IWM cross-symbol composite (market_state_engine/engine.py's
// own module docstring, decision #91 §4 / #97). That worst-case
// reconnect gap is at most ~10 seconds here, not 15 minutes — the same
// "fires often enough that a dropped/reconnecting WebSocket self-heals
// quickly" situation useOpportunities.ts's own WS-only design (no
// fallback poll at all) already rests on for `OpportunityCreated`, and
// this engine's own cadence is at least as tight as that one. Retaining
// the previous 5-second poll alongside push would mean routinely paying
// for a request whose answer is already in hand from the more recent
// push, and it re-introduces exactly the "unnecessary requests against
// an engine that already tells you the instant it changes" cost this
// upgrade exists to remove — so the poll is fully replaced, matching
// useOpportunities.ts's resolution of this exact question rather than
// useContextSnapshot.ts's.

export interface UseMarketStateResult {
  // null: either no `symbol` was requested, or this process hasn't
  // computed a MarketState for the requested symbol yet — the latter is
  // an honest "not yet computed" absence (get_snapshot()'s own
  // docstring), never a fabricated zero/neutral score. Callers that
  // pass a symbol are responsible for rendering the not-yet-computed
  // case explicitly rather than treating null as an error.
  symbolState: MarketStateScores | null;
  // null until SPY/QQQ/IWM have all reported at least one trend_score
  // (_compute_cross_symbol's own docstring) — render this honestly too,
  // regardless of whether a `symbol` was requested.
  market: CrossSymbolMarketState | null;
  loading: boolean;
  refetch: () => void;
}

/**
 * Backend read side for Market State Engine — GET
 * /intelligence/market-state (confirmed decision #98), unsurfaced
 * anywhere in the UI until this task. `symbol` is optional: omit it to
 * fetch only the market-wide cross-symbol composite (this hook still
 * fetches "symbols" under the hood since the route always returns both,
 * but a caller that omits `symbol` gets `symbolState: null` back and is
 * expected to ignore it, the same way useContextSnapshot.ts's callers
 * that omit `symbol` ignore its returned `fundamentals`/`news`); pass a
 * ticker to also get that symbol's own per-symbol scores.
 *
 * WebSocket-primary, no poll — see the comment above for the full
 * reasoning on why this hook fully replaces its previous poll with push
 * rather than keeping one as a fallback the way useContextSnapshot.ts
 * does for its own (much slower) engine.
 *
 * Subscribes to the "intelligence.market-state" channel (mirroring
 * useContextSnapshot.ts's subscribe/handle/unsubscribe pattern, itself
 * modeled on useOpportunities.ts) as an INVALIDATION signal, not a
 * second normalization path: a relevant push just calls this hook's own
 * `load()`, the same REST fetch that's already the source of truth,
 * rather than hand-parsing `WireMessage.payload` into
 * `MarketStateScores`/`CrossSymbolMarketState` a second time.
 * `MarketStateChanged`'s two envelope shapes (confirmed directly against
 * `channels.py`, not assumed from ContextChanged's convention) route as:
 * `symbol === CROSS_SYMBOL_SENTINEL` ("__MARKET__", never null/absent
 * here) -> reload; `symbol === this hook's own symbol argument` -> that
 * symbol's per-symbol scores changed -> reload; any other symbol ->
 * ignored, via `symbolRef` so a push arriving after a symbol switch
 * can't act on a stale closed-over symbol.
 *
 * Uses a `mountedRef` guard rather than a single per-effect `cancelled`
 * closure, for the same reason useContextSnapshot.ts does: `load()`
 * here fires repeatedly within one effect run (once on mount/symbol-
 * change, then again on every relevant WebSocket push), so one
 * closed-over flag from the first call wouldn't cover later pushes.
 */
export function useMarketState(symbol?: string): UseMarketStateResult {
  const [symbolState, setSymbolState] = useState<MarketStateScores | null>(null);
  const [market, setMarket] = useState<CrossSymbolMarketState | null>(null);
  const [loading, setLoading] = useState(true);
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const load = useCallback(() => {
    fetchMarketStateSnapshot(symbolRef.current)
      .then((wire) => {
        if (!mountedRef.current) return;

        const sym = symbolRef.current;
        const symbolWire = sym ? wire.symbols[sym] : undefined;
        setSymbolState(symbolWire ? normalizeSymbolState(symbolWire) : null);
        setMarket(wire.market ? normalizeMarket(wire.market) : null);
        setLoading(false);
      })
      .catch((err: unknown) => {
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useMarketState(${symbolRef.current ?? "market-wide"}): fetch failed — ${detail}`);
        if (mountedRef.current) setLoading(false);
      });
  }, []);

  useEffect(() => {
    setLoading(true);
    // Symbol change clears the previous ticker's per-symbol scores
    // immediately, same "don't show stale data mid-switch" convention
    // useContextSnapshot.ts uses for fundamentals/news. `market` isn't
    // symbol-scoped, so it's deliberately NOT cleared here — same
    // reasoning that file's own `calendar` isn't cleared either.
    setSymbolState(null);

    load();

    // WebSocket-primary trigger — invalidation signal only, see this
    // hook's own docstring above for why `load()` (a real refetch)
    // rather than merging `msg.payload` in piecemeal.
    const onUpdate = (msg: WireMessage) => {
      if (msg.symbol === CROSS_SYMBOL_SENTINEL) {
        load(); // cross-symbol composite changed
      } else if (msg.symbol === symbolRef.current) {
        load(); // this hook's own symbol's per-symbol scores changed
      }
      // else: a different symbol's MarketState changed — not this hook
      // instance's concern, ignore rather than merge it in.
    };
    const unsubscribe = workspaceSocket.subscribe("intelligence.market-state", onUpdate);

    return () => {
      unsubscribe();
    };
  }, [symbol, load]);

  return { symbolState, market, loading, refetch: load };
}
