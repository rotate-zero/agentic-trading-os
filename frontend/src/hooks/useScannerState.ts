import { useCallback, useEffect, useRef, useState } from "react";
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
 *
 * Request ordering, polling and query association (task
 * `scanner-results-request-safety`; same request-identity pattern the
 * Backtest Results hooks use, see `useBacktestOutcomes.ts`):
 *
 * - Every load — the effect-driven first one, each automatic poll and a
 *   manual `refresh()` alike — takes the next number from one per-hook
 *   request counter, and its response is applied only if that number is
 *   still the latest. A newer request therefore supersedes every older
 *   one, including a hung request replaced by Refresh; an older success or
 *   failure never touches results, skipped, universe, error, loading or
 *   lastUpdated. Underlying fetches are not cancelled: a superseded
 *   request still completes and its response is discarded.
 * - The counter is also advanced by the effect cleanup, so a symbols
 *   override change and unmount (including the panel collapsing or
 *   switching tab, which unmounts the Results tab) invalidate everything
 *   in flight. The cleanup also clears the polling interval.
 * - Polling keeps its 15 s `setInterval`, but a tick that finds the newest
 *   request still pending is skipped, so polls never stack behind a slow
 *   backend. Manual `refresh()` is never skipped or disabled: it always
 *   starts a newer request, which is how a hung request is recovered from.
 * - Settled state is stored together with the request key of the effective
 *   query it was fetched for and returned only while that key is still the
 *   current one — including in the render between an override change and
 *   the effect that starts its first fetch — so rows, skipped symbols,
 *   universe, errors and timestamps from a previous override never appear
 *   under a new one; that render reports `loading` with nothing else.
 *   The key distinguishes an omitted `symbols` (persisted universe) from
 *   an explicit override, exactly as `fetchScannerState` does, and is
 *   built from the array's contents, so a new array with identical
 *   contents is the same query and neither refetches nor restarts polling.
 * - For the SAME query, a refresh keeps the rows, skipped symbols,
 *   universe, last-success timestamp and any earlier error on screen until
 *   the newer result settles. A failed request keeps the last successful
 *   rows and `lastUpdated` and only sets `error`; a later success clears
 *   it. With no successful result yet for the query, a failure leaves
 *   empty rows plus `error`, so initial loading (`loading`, no data), a
 *   genuine empty result (`!loading`, no error, no rows) and a request
 *   failure (`error`) stay distinct states.
 */
interface ScannerSnapshot {
  key: string;
  results: ScannerResultWireShape[];
  skipped: string[];
  universe: string[];
  error: string | null;
  lastUpdated: Date | null;
  loading: boolean;
}

const NO_RESULTS: ScannerResultWireShape[] = [];
const NO_SYMBOLS: string[] = [];

export function useScannerState(symbols?: string[]) {
  // Identifies the effective query this hook is responsible for. `undefined`
  // (persisted universe) and any explicit array — including an empty one —
  // stay distinct, exactly as they are handed to `fetchScannerState`.
  const requestKey = symbols === undefined ? "omitted" : JSON.stringify(symbols);

  const [snapshot, setSnapshot] = useState<ScannerSnapshot | null>(null);
  const latestRequestRef = useRef(0);
  // True while the newest request has not settled. Read by the polling tick
  // only; manual refresh ignores it.
  const pendingRef = useRef(false);

  const load = useCallback(() => {
    const requestId = ++latestRequestRef.current;
    pendingRef.current = true;

    setSnapshot((prev) =>
      prev?.key === requestKey
        ? { ...prev, loading: true }
        : { key: requestKey, results: NO_RESULTS, skipped: NO_SYMBOLS, universe: NO_SYMBOLS, error: null, lastUpdated: null, loading: true },
    );

    fetchScannerState(symbols)
      .then((wire) => {
        if (requestId !== latestRequestRef.current) return;
        pendingRef.current = false;
        setSnapshot({
          key: requestKey,
          results: wire.results,
          skipped: wire.skipped,
          universe: wire.universe,
          error: null,
          lastUpdated: new Date(),
          loading: false,
        });
      })
      .catch((err: unknown) => {
        if (requestId !== latestRequestRef.current) return;
        pendingRef.current = false;
        const detail = err instanceof ApiError ? err.message : String(err);
        // Same query: keep the last successful rows and timestamp, only
        // surface the failure. Nothing to keep for a new query.
        setSnapshot((prev) =>
          prev?.key === requestKey
            ? { ...prev, error: detail, loading: false }
            : { key: requestKey, results: NO_RESULTS, skipped: NO_SYMBOLS, universe: NO_SYMBOLS, error: detail, lastUpdated: null, loading: false },
        );
      });
    // requestKey (not symbols itself) is the real dependency — a new
    // array reference with the same contents shouldn't invalidate this
    // callback and reset the poll interval below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestKey]);

  useEffect(() => {
    load();
    const interval = setInterval(() => {
      // Skip automatic polls while the newest request is still pending.
      if (!pendingRef.current) load();
    }, POLL_INTERVAL_MS);
    return () => {
      // Override change or unmount: stop polling and invalidate anything
      // in flight so a late response can't touch state.
      clearInterval(interval);
      latestRequestRef.current += 1;
      pendingRef.current = false;
    };
  }, [load]);

  const current = snapshot?.key === requestKey ? snapshot : null;
  return {
    results: current?.results ?? NO_RESULTS,
    skipped: current?.skipped ?? NO_SYMBOLS,
    universe: current?.universe ?? NO_SYMBOLS,
    loading: current?.loading ?? true,
    error: current?.error ?? null,
    lastUpdated: current?.lastUpdated ?? null,
    refresh: load,
  };
}

export type { ScannerResultWireShape };
