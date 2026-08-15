import { useEffect, useRef, useState } from "react";
import {
  fetchIntelligenceState,
  subscribeSymbol,
  ApiError,
  type FeatureValueNodeWireShape,
  type IntelligenceStateWireShape,
  type LevelInteractionWireShape,
} from "../services/api-client";
import { workspaceSocket, type WireMessage } from "../services/websocket-client";

export interface FeatureUnitEntry {
  period: string | null; // null for PDH/PDL/VWAP-style single-value units — see FeatureUnitWireShape
  value: number;
  candleTs: string;
  levelInteraction?: LevelInteractionWireShape;
}

export interface FeatureUnit {
  key: string; // "sma", "ema", "pdh", ... — whatever Feature Engine actually publishes, not a fixed list
  entries: FeatureUnitEntry[]; // sorted by numeric period when present
}

export interface FeatureTimeframe {
  timeframe: string;
  close: number;
  units: FeatureUnit[];
}

function isValueNode(
  x: FeatureValueNodeWireShape | Record<string, FeatureValueNodeWireShape>
): x is FeatureValueNodeWireShape {
  return typeof (x as FeatureValueNodeWireShape).value === "number";
}

// Reshapes the backend's raw wire response (a union per unit — see
// FeatureUnitWireShape's own comment in api-client.ts) into one
// consistent, always-array shape so the component doesn't need to
// type-narrow the union itself. Pure and separately testable in spirit,
// even though there's no test runner wired up on the frontend yet.
function normalize(wire: IntelligenceStateWireShape): FeatureTimeframe[] {
  return Object.entries(wire.timeframes).map(([timeframe, tf]) => {
    const units: FeatureUnit[] = Object.entries(tf.units).map(([unitKey, unit]) => {
      if (isValueNode(unit)) {
        return {
          key: unitKey,
          entries: [
            {
              period: null,
              value: unit.value,
              candleTs: unit.candle_ts,
              levelInteraction: unit.level_interaction,
            },
          ],
        };
      }
      const entries = Object.entries(unit)
        .map(([period, node]) => ({
          period,
          value: node.value,
          candleTs: node.candle_ts,
          levelInteraction: node.level_interaction,
        }))
        .sort((a, b) => Number(a.period) - Number(b.period));
      return { key: unitKey, entries };
    });
    units.sort((a, b) => a.key.localeCompare(b.key));
    return { timeframe, close: tf.close, units };
  });
}

/**
 * Backend read side for the Feature Engine panel — GET /intelligence/state
 * (confirmed decisions #47/#48) for the initial snapshot; live updates via
 * the "features.updated"/"intelligence.level" WebSocket channels, both
 * newly wired to the Gateway in decision #47.
 *
 * Live updates re-fetch the full snapshot rather than merging the WS
 * push in piecemeal — the same choice useLiveCandles.ts already made for
 * non-1m timeframes, and for the same reason: GET /intelligence/state is
 * a cheap, in-memory-only read on the backend (no DB I/O — see
 * FeatureEngine.get_snapshot()'s own docstring), so re-fetching costs
 * nothing, and it avoids maintaining the merge logic in two places
 * (Python in the route, TypeScript here) that could quietly drift apart.
 *
 * Also calls subscribeSymbol() on mount/change, same as useLiveCandles —
 * Feature Engine only ever computes for a symbol that's actually
 * streaming; typing a symbol into this panel that nothing else is
 * showing needs to start that stream itself, not assume it already
 * exists elsewhere.
 */
export function useIntelligenceState(symbol: string): { timeframes: FeatureTimeframe[]; loading: boolean } {
  const [timeframes, setTimeframes] = useState<FeatureTimeframe[]>([]);
  const [loading, setLoading] = useState(true);
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setTimeframes([]); // clear the previous symbol's stale data immediately on switch

    subscribeSymbol(symbol).catch((err: unknown) => {
      const detail = err instanceof ApiError ? err.message : String(err);
      console.error(`useIntelligenceState(${symbol}): subscribe failed — ${detail}`);
    });

    const load = () => {
      fetchIntelligenceState(symbolRef.current)
        .then((wire) => {
          if (!cancelled && wire.symbol === symbolRef.current) {
            setTimeframes(normalize(wire));
            setLoading(false);
          }
        })
        .catch((err: unknown) => {
          const detail = err instanceof ApiError ? err.message : String(err);
          console.error(`useIntelligenceState(${symbolRef.current}): fetch failed — ${detail}`);
          if (!cancelled) setLoading(false);
        });
    };

    load();

    const onUpdate = (msg: WireMessage) => {
      if (msg.symbol !== symbolRef.current) return; // one channel, many symbols — filter client-side
      load();
    };
    const unsubFeatures = workspaceSocket.subscribe("features.updated", onUpdate);
    const unsubLevels = workspaceSocket.subscribe("intelligence.level", onUpdate);

    return () => {
      cancelled = true;
      unsubFeatures();
      unsubLevels();
    };
  }, [symbol]);

  return { timeframes, loading };
}
