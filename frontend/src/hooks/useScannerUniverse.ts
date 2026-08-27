import { useCallback, useEffect, useState } from "react";
import {
  fetchScannerUniverse,
  addScannerUniverseSymbol,
  removeScannerUniverseSymbol,
  ApiError,
  type ScannerUniverseEntryWireShape,
} from "../services/api-client";

/**
 * Backend read/write side for the Scanner panel's universe editor —
 * GET/POST/DELETE /scanner/universe. Separate from useScannerState (the
 * ranked-results side) on purpose: the two refresh on different
 * triggers (this one only after an explicit add/remove, not on a poll
 * timer) and a caller showing just the results table has no reason to
 * pull in universe-editing state at all.
 */
export function useScannerUniverse() {
  const [symbols, setSymbols] = useState<ScannerUniverseEntryWireShape[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingAdd, setPendingAdd] = useState(false);

  const refresh = useCallback(() => {
    setLoading(true);
    fetchScannerUniverse()
      .then((list) => {
        setSymbols(list);
        setError(null);
      })
      .catch((err: unknown) => setError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  /** Resolves to true on success, false on a rejected (invalid-format)
   * symbol — the caller decides how to surface that, this hook just
   * exposes `error` with the backend's own explanation either way. */
  const addSymbol = useCallback(
    async (symbol: string): Promise<boolean> => {
      setPendingAdd(true);
      setError(null);
      try {
        await addScannerUniverseSymbol(symbol);
        refresh();
        return true;
      } catch (err: unknown) {
        setError(err instanceof ApiError ? err.message : String(err));
        return false;
      } finally {
        setPendingAdd(false);
      }
    },
    [refresh],
  );

  const removeSymbol = useCallback(
    async (symbol: string): Promise<void> => {
      // Optimistic — a universe list is low-stakes enough that waiting
      // on a round trip before the row disappears would just feel
      // laggy; refresh() below reconciles with the server regardless.
      setSymbols((prev) => prev.filter((s) => s.symbol !== symbol));
      try {
        await removeScannerUniverseSymbol(symbol);
      } catch (err: unknown) {
        setError(err instanceof ApiError ? err.message : String(err));
      } finally {
        refresh();
      }
    },
    [refresh],
  );

  return { symbols, loading, error, pendingAdd, addSymbol, removeSymbol, refresh };
}
