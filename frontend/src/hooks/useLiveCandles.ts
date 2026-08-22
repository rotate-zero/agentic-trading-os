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
 * liveTick (decision #72) — optional, only meaningful while timeframe is
 * "1m" (SubWindowMenu's Tick toggle enforces that pairing; this hook
 * defensively no-ops the subscription otherwise rather than trusting the
 * caller). When on, also listens on "market.tick.snapshot" for throttled
 * PriceSnapshot updates to the CURRENTLY FORMING bar and upserts it into
 * `candles` via `_upsertLast`. This is why the market.candle handler below
 * ALSO went through `_upsertLast` rather than a blind append: once a
 * provisional snapshot bar can already occupy the array's last slot, the
 * real CandleClosed for that same minute must replace it, not duplicate
 * it. A symbol/timeframe not actually in LiveTickRelay's small active set
 * (see live_tick_relay.py) simply never receives snapshot pushes — the
 * bar then only ever updates on real 1m close, same as liveTick===false.
 *
 * Error handling is deliberately minimal: failures log to the console
 * rather than surfacing in the UI. This codebase has no established
 * error-display pattern yet (every previous data source was a
 * synchronous mock generator that couldn't fail) — building one is a
 * separate, explicitly-scoped piece of work, not something to improvise
 * here as a side effect of the data-source swap.
 */
function _upsertLast(prev: Candle[], candle: Candle): Candle[] {
  if (prev.length === 0) {
    // No backfill/anchor bar yet — nothing to revise in place, and
    // appending a bare provisional bar with no prior context isn't worth
    // the edge cases (e.g. it briefly being the ONLY bar on the chart).
    // The next real backfill or CandleClosed establishes the anchor.
    return prev;
  }
  const last = prev[prev.length - 1];
  if (candle.time === last.time) {
    const next = prev.slice();
    next[next.length - 1] = candle;
    return next;
  }
  if (candle.time > last.time) {
    const next = [...prev, candle];
    return next.length > MAX_CANDLES ? next.slice(next.length - MAX_CANDLES) : next;
  }
  return prev; // stale/out-of-order for a minute already closed and moved past — ignore
}

export function useLiveCandles(symbol: string, timeframe: Timeframe = "1m", liveTick = false): Candle[] {
  const [candles, setCandles] = useState<Candle[]>([]);
  const symbolRef = useRef(symbol);
  const timeframeRef = useRef(timeframe);
  const liveTickRef = useRef(liveTick);
  symbolRef.current = symbol;
  timeframeRef.current = timeframe;
  liveTickRef.current = liveTick;

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

    const unsubscribeCandle = workspaceSocket.subscribe("market.candle", (msg: WireMessage) => {
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
        setCandles((prev) => _upsertLast(prev, candle));
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

    // Decision #72 — only ever subscribed while liveTick is on; a no-op
    // unsubscribe otherwise so the cleanup below stays unconditional.
    const unsubscribeTick = liveTick
      ? workspaceSocket.subscribe("market.tick.snapshot", (msg: WireMessage) => {
          if (msg.symbol !== symbolRef.current) return;
          if (timeframeRef.current !== "1m") return; // tick fluidity only ever describes the 1m forming bar
          if (!msg.payload) return;
          const snapshot = toCandle(msg.payload as unknown as CandleWireShape);
          setCandles((prev) => _upsertLast(prev, snapshot));
        })
      : () => {};

    return () => {
      cancelled = true;
      unsubscribeCandle();
      unsubscribeTick();
    };
  }, [symbol, timeframe, liveTick]);

  return candles;
}
