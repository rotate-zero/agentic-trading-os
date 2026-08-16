import { useEffect, useRef, useState } from "react";
import type { IndicatorPoint } from "../indicators/types";
import { fetchFeatureSeries, subscribeSymbol, ApiError } from "../services/api-client";
import { workspaceSocket, type WireMessage } from "../services/websocket-client";

const BACKFILL_COUNT = 240; // matches useLiveCandles's own BACKFILL_COUNT — an indicator line should cover the same span as the candles it's drawn against
const MAX_POINTS = 500; // matches useLiveCandles's MAX_CANDLES — bounds client memory for a long-running session, same rationale

export interface FeatureEngineSeries {
  // Undefined (not an empty array) distinguishes "Feature Engine has
  // never published this exact key for this symbol/timeframe" from "it
  // has, there just aren't any warmed-up points yet" — both are real,
  // computePriceIndicator (utils/indicators.ts) treats them the same way
  // (fall back to local computation) but the distinction is worth keeping
  // for whoever debugs this next.
  series: Record<string, IndicatorPoint[] | undefined>;
  loading: boolean;
}

/**
 * Chart backfill + live updates for Feature-Engine-computed indicator
 * lines — SMA/EMA/VWAP (confirmed decision #54, Stage 1 of the chart
 * migration: docs/architecture/feature-engine-chart-migration.md). Same
 * backfill-then-live-append shape useLiveCandles.ts already established,
 * deliberately NOT useIntelligenceState.ts's refetch-the-whole-snapshot-
 * on-every-push pattern: that one is fine for a side panel's lightweight
 * "current value" read (no DB I/O per FeatureEngine.get_snapshot()'s own
 * docstring), but GET /intelligence/series does real DB I/O
 * (candle_store/candle_aggregator) — re-fetching the WHOLE series on
 * every single 1m tick would be a real, avoidable DB round-trip every
 * close. Live updates instead APPEND the one new point FeaturesUpdated
 * already carries in its payload directly, no re-fetch at all.
 *
 * Only appends when the incoming FeaturesUpdated's timeframe matches what
 * this hook was asked for — one WS channel carries every timeframe's
 * updates multiplexed together (decision #51: a single 1m close can
 * complete a 5m/15m/1h bucket too, each its own FeaturesUpdated), same
 * client-side filtering useLiveCandles already does for symbol.
 *
 * Calls subscribeSymbol() on mount/change, same as useLiveCandles and
 * useIntelligenceState — Feature Engine only ever computes for a symbol
 * that's actually streaming.
 */
export function useFeatureEngineSeries(symbol: string, timeframe: string): FeatureEngineSeries {
  const [series, setSeries] = useState<Record<string, IndicatorPoint[] | undefined>>({});
  const [loading, setLoading] = useState(true);
  const symbolRef = useRef(symbol);
  const timeframeRef = useRef(timeframe);
  symbolRef.current = symbol;
  timeframeRef.current = timeframe;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setSeries({}); // clear the previous symbol/timeframe's stale data immediately on switch

    subscribeSymbol(symbol).catch((err: unknown) => {
      const detail = err instanceof ApiError ? err.message : String(err);
      console.error(`useFeatureEngineSeries(${symbol}): subscribe failed — ${detail}`);
    });

    fetchFeatureSeries(symbol, timeframe, BACKFILL_COUNT)
      .then((wire) => {
        if (cancelled) return;
        const next: Record<string, IndicatorPoint[]> = {};
        for (const [key, points] of Object.entries(wire.series)) {
          next[key] = points.map((p) => ({ time: Math.floor(new Date(p.candle_ts).getTime() / 1000), value: p.value }));
        }
        setSeries(next);
        setLoading(false);
      })
      .catch((err: unknown) => {
        // A 400 here means this timeframe isn't one Feature Engine computes
        // for at all (e.g. "1d" — see the route's own docstring) — not a
        // transient failure. Either way, series stays {}, and every
        // instance falls back to local computation, same as any other
        // unsupported combo.
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`useFeatureEngineSeries(${symbol}, ${timeframe}): backfill failed — ${detail}`);
        if (!cancelled) setLoading(false);
      });

    const unsubscribe = workspaceSocket.subscribe("features.updated", (msg: WireMessage) => {
      if (msg.symbol !== symbolRef.current) return; // one channel, many symbols — filter client-side
      if (!msg.payload) return;
      const payload = msg.payload as unknown as { timeframe: string; candle_ts: string; features: Record<string, number> };
      if (payload.timeframe !== timeframeRef.current) return; // a different timeframe's bucket completed — not this chart's concern
      const time = Math.floor(new Date(payload.candle_ts).getTime() / 1000);

      setSeries((prev) => {
        const next = { ...prev };
        for (const [key, value] of Object.entries(payload.features)) {
          const existing = next[key] ?? [];
          const appended = [...existing, { time, value }];
          next[key] = appended.length > MAX_POINTS ? appended.slice(appended.length - MAX_POINTS) : appended;
        }
        return next;
      });
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [symbol, timeframe]);

  return { series, loading };
}
