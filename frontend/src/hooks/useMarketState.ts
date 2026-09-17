import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  fetchMarketStateSnapshot,
  type MarketStateCompositeWireShape,
  type MarketStateSymbolWireShape,
} from "../services/api-client";

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

// Poll interval reasoning, stated explicitly (same discipline
// useContextSnapshot.ts/useStrategyOutcomes.ts's own docstrings use for
// their own choices, rather than defaulting to a number silently).
//
// Fetch + poll, NOT WebSocket-push: at the time this hook was written, no
// `EventType.MARKET_STATE_CHANGED` entry existed in `EVENT_TO_CHANNEL`
// (backend/app/api/websocket/channels.py, confirmed by reading that file
// directly). A parallel session (temp id
// `market-state-changed-websocket-channel`) landed the missing routing
// entry — channel `intelligence.market-state` — after this hook was
// already built and tested; this hook does NOT yet consume it. This was
// the same shape useContextSnapshot.ts itself originally shipped with
// under decision #125, before decision #126 wired CONTEXT_CHANGED's own
// missing routing entry. A push-based upgrade is now unblocked and would
// be a natural, immediate follow-up (this task's own decision-log entry
// notes it), not something this delivery retroactively blocks on.
//
// The interval itself is deliberately NOT copied from
// useContextSnapshot.ts's 60_000 — that number was chosen relative to
// Context's own much slower cadence (session boundaries a handful of
// times a day, plus a 15-minute Fundamentals/News timer per symbol).
// Market State Engine recomputes on a materially faster cadence:
// DebounceScheduler's own ~1s floor / ~10s ceiling per symbol, ~1s
// floor / ~4s ceiling for the SPY/QQQ/IWM cross-symbol composite
// (market_state_engine/engine.py's own module docstring, decision #91
// §4 / #97 — `_MIN_INTERVAL_SECONDS`/`_MAX_INTERVAL_SECONDS`/
// `_CROSS_SYMBOL_MAX_INTERVAL_SECONDS`). Copying Context's 60s here
// would show state up to a full minute stale most of the time — far
// looser than this engine's own update cadence, the opposite problem
// 60s solves for Context (where it's tighter than that engine's 15-
// minute cadence). `get_snapshot()` is the same synchronous, in-memory,
// zero-I/O read `ContextEngine.get_snapshot()` is (confirmed directly
// against market_state_engine/engine.py's own docstring), so a tight
// poll still costs effectively nothing server-side. 5 seconds keeps
// this comfortably within one full per-symbol recompute cycle (and just
// over one cross-symbol cycle) without polling meaningfully faster than
// the underlying data can actually change.
const POLL_INTERVAL_MS = 5_000;

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
 * Fetch + poll only, no WebSocket — see POLL_INTERVAL_MS's own comment
 * above for the full reasoning, including why the interval is much
 * tighter than useContextSnapshot.ts's.
 *
 * Uses a `mountedRef` guard rather than a single per-effect `cancelled`
 * closure, for the same reason useContextSnapshot.ts does: `load()`
 * here fires repeatedly within one effect run (once on mount/symbol-
 * change, then again every poll tick), so one closed-over flag from the
 * first call wouldn't cover later ticks.
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
    const interval = setInterval(load, POLL_INTERVAL_MS);

    return () => {
      clearInterval(interval);
    };
  }, [symbol, load]);

  return { symbolState, market, loading, refetch: load };
}
