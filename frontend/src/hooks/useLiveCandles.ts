import { useEffect, useRef, useState } from "react";
import type { Candle } from "../types/market";
import { fetchCandles, subscribeSymbol, toCandle, ApiError, type CandleWireShape } from "../services/api-client";
import { workspaceSocket, type WireMessage } from "../services/websocket-client";

const BACKFILL_COUNT = 240; // matches mocks/candles.ts's default, so chart density doesn't visibly change
const MAX_CANDLES = 500; // bounds client memory for a long-running session — matches backend's rolling buffer scale

/**
 * Replaces `generateMockCandles(symbol)` (mocks/candles.ts) as the
 * 1-minute candle source. Same shape in, same shape out (Candle[]) —
 * nothing downstream (resampleCandles, computeIndicator, ChartWidget)
 * needs to change, exactly as mocks/candles.ts's own comment promised.
 *
 * Two things happen on mount/symbol change: (1) POST /market/subscribe
 * so the backend actually starts streaming this symbol — real providers
 * need an explicit subscription, unlike the old dev CandlePublisher,
 * which streamed every mock ticker with no subscribe step; (2) GET
 * /market/candles for the initial backfill, since a chart needs "the
 * last 240 candles" on load, not just a live tail starting from nothing.
 *
 * Error handling is deliberately minimal: failures log to the console
 * rather than surfacing in the UI. This codebase has no established
 * error-display pattern yet (every previous data source was a
 * synchronous mock generator that couldn't fail) — building one is a
 * separate, explicitly-scoped piece of work, not something to improvise
 * here as a side effect of the data-source swap.
 */
export function useLiveCandles(symbol: string): Candle[] {
  const [candles, setCandles] = useState<Candle[]>([]);
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;

  useEffect(() => {
    let cancelled = false;
    setCandles([]); // clear the previous symbol's stale data immediately on switch

    subscribeSymbol(symbol).catch((err: unknown) => {
      const detail = err instanceof ApiError ? err.message : String(err);
      console.error(`useLiveCandles(${symbol}): subscribe failed — ${detail}`);
    });

    fetchCandles(symbol, BACKFILL_COUNT)
      .then((initial) => {
        if (!cancelled) setCandles(initial);
      })
      .catch((err: unknown) => {
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useLiveCandles(${symbol}): backfill failed — ${detail}`);
      });

    const unsubscribe = workspaceSocket.subscribe("market.candle", (msg: WireMessage) => {
      if (msg.symbol !== symbolRef.current) return; // one channel, many symbols — filter client-side
      if (!msg.payload) return;
      // msg.payload is typed as Record<string, unknown> at the generic
      // WireMessage envelope level (any channel could carry any shape),
      // but on market.candle specifically it's always a CandleClosed
      // dump per the backend's EVENT_TO_CHANNEL mapping — the `as
      // unknown as` two-step is TypeScript's idiom for "trust the
      // channel contract, not something the type system can verify
      // structurally from a plain Record."
      const candle = toCandle(msg.payload as unknown as CandleWireShape);
      setCandles((prev) => {
        const next = [...prev, candle];
        return next.length > MAX_CANDLES ? next.slice(next.length - MAX_CANDLES) : next;
      });
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [symbol]);

  return candles;
}
