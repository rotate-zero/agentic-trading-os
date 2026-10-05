import { useCallback, useEffect, useRef, useState } from "react";
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
 * triggers (this one only after an explicit add/remove or a manual
 * retry, not on a poll timer) and a caller showing just the results table
 * has no reason to pull in universe-editing state at all.
 *
 * Request ordering, mutation safety and error separation (task
 * `scanner-universe-mutation-recovery`; same request-identity pattern as
 * `useScannerState.ts` and the Backtest Results hooks):
 *
 * - Every read (first load, manual `refresh()`, the reconciliation read
 *   after a mutation) takes the next number from one per-hook counter and
 *   its response is applied only if that number is still the latest AND
 *   the hook is still mounted. An older response therefore never touches
 *   the list, `loading` or the read error. Starting an add/remove also
 *   advances the counter, so a read that began before the mutation can
 *   never overwrite the post-mutation reconciliation.
 * - At most one mutation runs per hook instance. A ref flag is set
 *   synchronously before the first `await`, so two clicks / Enter presses
 *   in the same tick cannot both pass; a second call while one is pending
 *   returns `false` without sending anything. (State alone would not do:
 *   `pendingAdd` only changes on the next render.) The guard is released
 *   when the write settles, before the reconciliation read; a mutation
 *   started during that read simply supersedes it.
 * - Remove is optimistic: the row is hidden while the DELETE is in
 *   flight (the confirmed list is not edited). A failed DELETE un-hides
 *   it; a successful one removes it from the confirmed list. The row is
 *   never lost because of a failed write or a later failed read.
 * - Three failure kinds are kept apart. `mutationError` is a rejected
 *   write (add/remove) and is cleared only when the next mutation starts —
 *   a reconciliation or retry read that succeeds does NOT clear it.
 *   `loadError` is a failed read; it is cleared by the next successful
 *   read. `staleAfterWrite` is true when a write succeeded but the read
 *   that should reconcile it failed — the write is NOT reported as failed;
 *   the list shown is the last confirmed one and may be out of date.
 * - A read failure keeps the last confirmed list (`hasLoaded` stays true)
 *   and `refresh()` retries. Initial loading (`!hasLoaded && loading`),
 *   initial failure (`!hasLoaded && loadError`), a genuinely empty
 *   universe (`hasLoaded && no rows && !loadError`) and a populated list
 *   are distinct states.
 * - Unmount (tab switch / panel collapse) invalidates everything in
 *   flight: no state is updated afterwards and no reconciliation read is
 *   started. A DELETE/POST already sent is NOT cancelled and may still
 *   complete on the backend — its outcome is simply not shown.
 */
interface UniverseState {
  confirmed: ScannerUniverseEntryWireShape[];
  hasLoaded: boolean;
  loading: boolean;
  loadError: string | null;
  staleAfterWrite: boolean;
  mutationError: string | null;
  mutation: { kind: "add" | "remove"; symbol: string } | null;
}

const INITIAL: UniverseState = {
  confirmed: [],
  hasLoaded: false,
  loading: true,
  loadError: null,
  staleAfterWrite: false,
  mutationError: null,
  mutation: null,
};

const describe = (err: unknown) => (err instanceof ApiError ? err.message : String(err));

export function useScannerUniverse() {
  const [state, setState] = useState<UniverseState>(INITIAL);
  const latestReadRef = useRef(0);
  const mountedRef = useRef(false);
  const mutatingRef = useRef(false);

  const read = useCallback((afterWrite: boolean) => {
    const readId = ++latestReadRef.current;
    setState((s) => ({ ...s, loading: true }));
    fetchScannerUniverse()
      .then((list) => {
        if (!mountedRef.current || readId !== latestReadRef.current) return;
        setState((s) => ({
          ...s,
          confirmed: list,
          hasLoaded: true,
          loading: false,
          loadError: null,
          staleAfterWrite: false,
        }));
      })
      .catch((err: unknown) => {
        if (!mountedRef.current || readId !== latestReadRef.current) return;
        setState((s) => ({
          ...s,
          loading: false,
          loadError: describe(err),
          staleAfterWrite: afterWrite || s.staleAfterWrite,
        }));
      });
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    read(false);
    return () => {
      mountedRef.current = false;
      latestReadRef.current += 1;
    };
  }, [read]);

  /** Manual retry / refresh. Supersedes any pending read; ignored while a
   * mutation is in flight (its own reconciliation read follows). */
  const refresh = useCallback(() => {
    if (mutatingRef.current) return;
    read(false);
  }, [read]);

  const mutate = useCallback(
    async (kind: "add" | "remove", symbol: string, write: () => Promise<unknown>): Promise<boolean> => {
      if (mutatingRef.current) return false;
      mutatingRef.current = true;
      latestReadRef.current += 1; // pre-mutation reads must not land any more
      setState((s) => ({ ...s, mutation: { kind, symbol }, mutationError: null }));

      let ok = false;
      let failure: string | null = null;
      try {
        await write();
        ok = true;
      } catch (err: unknown) {
        failure = describe(err);
      } finally {
        mutatingRef.current = false;
      }

      // Unmounted meanwhile: the request was already sent and may have
      // completed on the backend, but nothing here may touch state.
      if (!mountedRef.current) return ok;

      setState((s) => ({
        ...s,
        mutation: null,
        mutationError: failure,
        confirmed: ok && kind === "remove" ? s.confirmed.filter((e) => e.symbol !== symbol) : s.confirmed,
      }));
      read(ok);
      return ok;
    },
    [read],
  );

  /** Resolves to true only when the write itself succeeded (also when the
   * following reload fails); false for a rejected write (invalid-format
   * symbol, network error) or when another mutation is already pending. */
  const addSymbol = useCallback(
    (symbol: string) => mutate("add", symbol, () => addScannerUniverseSymbol(symbol)),
    [mutate],
  );

  const removeSymbol = useCallback(
    (symbol: string) => mutate("remove", symbol, () => removeScannerUniverseSymbol(symbol)),
    [mutate],
  );

  const pendingRemove = state.mutation?.kind === "remove" ? state.mutation.symbol : null;
  const symbols =
    pendingRemove === null ? state.confirmed : state.confirmed.filter((s) => s.symbol !== pendingRemove);

  return {
    symbols,
    hasLoaded: state.hasLoaded,
    loading: state.loading,
    loadError: state.loadError,
    staleAfterWrite: state.staleAfterWrite,
    mutationError: state.mutationError,
    pendingAdd: state.mutation?.kind === "add",
    pendingRemove,
    mutating: state.mutation !== null,
    addSymbol,
    removeSymbol,
    refresh,
  };
}
