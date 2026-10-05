import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, fetchPortfolioState, type PortfolioStateWireShape } from "../services/api-client";

/**
 * Read side for GET /intelligence/portfolio-state (`live-portfolio-details`):
 * the running simulated Portfolio State's detailed snapshot.
 *
 * Loads when `enabled` becomes true (the section is expanded) and on manual
 * `refresh()`. Deliberately not polled and not WebSocket-driven: the route is
 * a one-shot read of an in-memory cache and no channel announces changes.
 * Collapsing discards any pending request and clears the data, so a later
 * expansion never shows numbers from an earlier snapshot as current.
 *
 * Request safety (same convention as useWorldView/useScannerState):
 *  - one counter; a response is applied only if it is still the latest request;
 *  - cleanup (collapse, unmount, newer request) invalidates older ones, so a
 *    late success or failure changes nothing and never ends `loading` early;
 *  - a failure clears the previous data (an error is never shown over old
 *    numbers) and is kept apart from `portfolio === null` (unavailable).
 *
 * `loaded` distinguishes "not read yet" from "read: unavailable".
 */
export interface UsePortfolioStateResult {
  portfolio: PortfolioStateWireShape | null;
  loaded: boolean;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function usePortfolioState(enabled: boolean): UsePortfolioStateResult {
  const [portfolio, setPortfolio] = useState<PortfolioStateWireShape | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);
  const mounted = useRef(false);

  const load = useCallback(() => {
    if (!mounted.current) return;
    const current = ++requestId.current;
    setLoading(true);
    setError(null);
    fetchPortfolioState()
      .then((response) => {
        if (current !== requestId.current) return;
        setPortfolio(response.portfolio);
        setLoaded(true);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (current !== requestId.current) return;
        const detail = err instanceof ApiError ? err.message : String(err);
        console.error(`usePortfolioState: fetch failed — ${detail}`);
        setPortfolio(null);
        setLoaded(false);
        setError(detail);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestId.current++;
    };
  }, []);

  useEffect(() => {
    if (!enabled) {
      requestId.current++; // discard anything still in flight
      setPortfolio(null);
      setLoaded(false);
      setLoading(false);
      setError(null);
      return;
    }
    load();
    return () => {
      requestId.current++;
    };
  }, [enabled, load]);

  return { portfolio, loaded, loading, error, refresh: load };
}
