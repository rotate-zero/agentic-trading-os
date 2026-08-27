import { useCallback, useEffect, useState } from "react";
import { fetchScannerState, ApiError, type ScannerResultWireShape } from "../services/api-client";

const POLL_INTERVAL_MS = 15_000;

/**
 * Backend read side for the Scanner panel — GET /scanner/state, v1
 * on-demand (docs/architecture/scanner-design.md §5/§10 — no
 * MarketActivityScanner or ScanCadenceSchedule exists yet, so there's no
 * "scanner.ranking" WebSocket channel to subscribe to the way
 * useIntelligenceState subscribes to "features.updated"). Polls instead —
 * a plain setInterval, same as any other "nothing pushes this yet"
 * screen would need. Revisit once a real ScannerRankingUpdated event
 * exists; this hook's return shape wouldn't need to change, just how it
 * gets refreshed.
 */
export function useScannerState(symbols?: string[]) {
  const [results, setResults] = useState<ScannerResultWireShape[]>([]);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [universe, setUniverse] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const symbolsKey = symbols?.join(",");

  const refresh = useCallback(() => {
    setLoading(true);
    fetchScannerState(symbols)
      .then((wire) => {
        setResults(wire.results);
        setSkipped(wire.skipped);
        setUniverse(wire.universe);
        setError(null);
        setLastUpdated(new Date());
      })
      .catch((err: unknown) => {
        const detail = err instanceof ApiError ? err.message : String(err);
        setError(detail);
      })
      .finally(() => setLoading(false));
    // symbolsKey (not symbols itself) is the real dependency — a new
    // array reference with the same contents shouldn't invalidate this
    // callback and reset the poll interval below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolsKey]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refresh]);

  return { results, skipped, universe, loading, error, lastUpdated, refresh };
}

export type { ScannerResultWireShape };
