import { useEffect, useRef, useState } from "react";
import type { Candle } from "../types/market";
import type { Timeframe } from "../types/workspace";
import { fetchCandles, subscribeSymbol, toCandle, ApiError, type CandleWireShape } from "../services/api-client";
import { workspaceSocket, type WireMessage } from "../services/websocket-client";

const BACKFILL_COUNT = 240; // matches mocks/candles.ts's default, so chart density doesn't visibly change
const MAX_CANDLES = 500; // bounds client memory for a long-running session — matches backend's rolling buffer scale

/**
 * Replaces `generateMockCandles(symbol)` (mocks/candles.ts) as the candle
 * source, now for any timeframe — not just 1-minute. Same shape in, same
 * shape out (Candle[]).
 *
 * Two things happen on mount/symbol/timeframe change: (1) POST
 * /market/subscribe so the backend actually starts streaming this symbol
 * — real providers need an explicit subscription, unlike the old dev
 * CandlePublisher, which streamed every mock ticker with no subscribe
 * step; (2) GET /market/candles?timeframe=... for the initial backfill,
 * requesting the ACTUAL selected timeframe now rather than always "1m" —
 * this is the fix for the chart never reflecting anything but a 1m-
 * resampled view (see confirmed-decisions.md). A timeframe switch re-runs
 * both, including the subscribe call even though the symbol didn't
 * change — a harmless redundant call (subscribing to an already-
 * subscribed symbol is idempotent), traded for not needing a second
 * effect just to avoid it.
 *
 * Live updates: every push on the "market.candle" channel is a freshly-
 * CLOSED 1m candle (TickIngestBridge's fixed bucket size) — never a
 * complete bar for any other timeframe. For a "1m" view this can be
 * appended directly, same as before. For anything coarser, appending it
 * would inject a spurious 1-minute-wide candle into what should be a 5m/
 * 15m/1h/1d series — instead, a new 1m close re-pulls the real backend-
 * aggregated backfill (candle_aggregator.py owns the actual bucketing;
 * duplicating that session-aware logic here would be two implementations
 * to keep in sync, for data that only changes once a minute regardless of
 * which approach is used).
 *
 * Error handling is deliberately minimal: failures log to the console
 * rather than surfacing in the UI. This codebase has no established
 * error-display pattern yet (every previous data source was a
 * synchronous mock generator that couldn't fail) — building one is a
 * separate, explicitly-scoped piece of work, not something to improvise
 * here as a side effect of the data-source swap.
 */
export function useLiveCandles(symbol: string, timeframe: Timeframe = "1m"): Candle[] {
  const [candles, setCandles] = useState<Candle[]>([]);
  const symbolRef = useRef(symbol);
  const timeframeRef = useRef(timeframe);
  symbolRef.current = symbol;
  timeframeRef.current = timeframe;

  useEffect(() => {
    let cancelled = false;
    setCandles([]); // clear the previous symbol/timeframe's stale data immediately on switch

    subscribeSymbol(symbol).catch((err: unknown) => {
      const detail = err instanceof ApiError ? err.message : String(err);
      console.error(`useLiveCandles(${symbol}): subscribe failed — ${detail}`);
    });

    fetchCandles(symbol, BACKFILL_COUNT, timeframe)
      .then((initial) => {
        if (!cancelled) setCandles(initial);
      })
      .catch((err: unknown) => {
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useLiveCandles(${symbol}, ${timeframe}): backfill failed — ${detail}`);
      });

    const unsubscribe = workspaceSocket.subscribe("market.candle", (msg: WireMessage) => {
      if (msg.symbol !== symbolRef.current) return; // one channel, many symbols — filter client-side
      if (!msg.payload) return;

      if (timeframeRef.current === "1m") {
        // msg.payload is typed as Record<string, unknown> at the generic
        // WireMessage envelope level (any channel could carry any
        // shape), but on market.candle specifically it's always a
        // CandleClosed dump per the backend's EVENT_TO_CHANNEL mapping —
        // the `as unknown as` two-step is TypeScript's idiom for "trust
        // the channel contract, not something the type system can verify
        // structurally from a plain Record."
        const candle = toCandle(msg.payload as unknown as CandleWireShape);
        setCandles((prev) => {
          const next = [...prev, candle];
          return next.length > MAX_CANDLES ? next.slice(next.length - MAX_CANDLES) : next;
        });
        return;
      }

      fetchCandles(symbolRef.current, BACKFILL_COUNT, timeframeRef.current)
        .then((refreshed) => {
          if (!cancelled) setCandles(refreshed);
        })
        .catch((err: unknown) => {
          const detail = err instanceof ApiError ? err.message : String(err);
          console.error(`useLiveCandles(${symbolRef.current}, ${timeframeRef.current}): live refresh failed — ${detail}`);
        });
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [symbol, timeframe]);

  return candles;
}
