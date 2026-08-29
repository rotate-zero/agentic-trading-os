import { useEffect, useRef, useState } from "react";
import {
  fetchIntelligenceState,
  subscribeSymbol,
  ApiError,
  type DailyLevelWireShape,
  type FeatureSlopeWireShape,
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
  // SMA/EMA slope family (confirmed decision #85) — present only on
  // "sma"/"ema" entries once slope has warmed up (2*period-1 closes for
  // SMA, more for EMA — see sma_slope()/ema_slope()'s own docstrings).
  // Deliberately never carries its own `levelInteraction` — these four
  // values are excluded from Level Interaction tracking entirely (same
  // decision, LevelInteractionEngine._process_one).
  slope?: FeatureSlopeWireShape;
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
function normalize(wire: IntelligenceStateWireShape): { timeframes: FeatureTimeframe[]; dailyLevels: DailyLevelWireShape[] } {
  const timeframes = Object.entries(wire.timeframes).map(([timeframe, tf]) => {
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
              slope: unit.slope,
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
          slope: node.slope,
        }))
        // Numeric sort for SMA/EMA-style periods ("9", "20", ...); for a
        // non-numeric family (Camarilla's "pp"/"r1"-"r4"/"s1"-"s4" — the
        // decision #66 grouping fix on the backend), Number(period) is
        // NaN for both sides and `NaN - NaN` is itself NaN, which V8
        // treats as "equal" but isn't guaranteed to be by spec across
        // engines — falling back to 0 explicitly keeps the backend's own
        // dict-insertion order (pp, r1-r4, s1-s4 — camarilla.py's own
        // return order) rather than leaving it to an unspecified
        // comparator result.
        .sort((a, b) => {
          const an = Number(a.period);
          const bn = Number(b.period);
          return Number.isFinite(an) && Number.isFinite(bn) ? an - bn : 0;
        });
      return { key: unitKey, entries };
    });
    units.sort((a, b) => a.key.localeCompare(b.key));
    return { timeframe, close: tf.close, units };
  });
  return { timeframes, dailyLevels: wire.daily_levels };
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
export function useIntelligenceState(
  symbol: string,
  dailyLevelsLookbackDays?: number | null
): { timeframes: FeatureTimeframe[]; dailyLevels: DailyLevelWireShape[]; loading: boolean } {
  const [timeframes, setTimeframes] = useState<FeatureTimeframe[]>([]);
  const [dailyLevels, setDailyLevels] = useState<DailyLevelWireShape[]>([]);
  const [loading, setLoading] = useState(true);
  const symbolRef = useRef(symbol);
  symbolRef.current = symbol;
  const lookbackRef = useRef(dailyLevelsLookbackDays);
  lookbackRef.current = dailyLevelsLookbackDays;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setTimeframes([]); // clear the previous symbol's stale data immediately on switch
    setDailyLevels([]);

    subscribeSymbol(symbol).catch((err: unknown) => {
      const detail = err instanceof ApiError ? err.message : String(err);
      console.error(`useIntelligenceState(${symbol}): subscribe failed — ${detail}`);
    });

    const load = () => {
      fetchIntelligenceState(symbolRef.current, lookbackRef.current)
        .then((wire) => {
          if (!cancelled && wire.symbol === symbolRef.current) {
            const normalized = normalize(wire);
            setTimeframes(normalized.timeframes);
            setDailyLevels(normalized.dailyLevels);
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
    // dailyLevelsLookbackDays is intentionally in this array (not just
    // symbol) — changing the lookback selector should trigger an
    // immediate refetch with the new value, not wait for the next
    // WebSocket-driven update. lookbackRef exists so `load()` inside the
    // WebSocket handler always reads the CURRENT value without needing
    // onUpdate itself to be recreated on every lookback change.
  }, [symbol, dailyLevelsLookbackDays]);

  return { timeframes, dailyLevels, loading };
}
