import { useCallback, useEffect, useRef, useState } from "react";
import { fetchStoredCoverage, type StoredCoverageWireShape } from "../services/api-client";

type PreviewState = {
  key: string;
  status: "loading" | "done" | "error";
  result: StoredCoverageWireShape | null;
  error: string | null;
};

/** One explicit preview at a time; edits and unmounts retire older responses. */
export function useStoredCoverage(symbol: string, startLocal: string, endLocal: string) {
  const key = JSON.stringify([symbol, startLocal, endLocal]);
  const currentKey = useRef(key);
  currentKey.current = key;
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const [state, setState] = useState<PreviewState | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      requestId.current += 1;
      controller.current?.abort();
    };
  }, []);

  useEffect(() => {
    requestId.current += 1;
    controller.current?.abort();
    controller.current = null;
    setState(null);
  }, [key]);

  const check = useCallback(async (symbolValue: string, startIso: string, endIso: string) => {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const id = ++requestId.current;
    const requestKey = currentKey.current;
    setState({ key: requestKey, status: "loading", result: null, error: null });
    try {
      const result = await fetchStoredCoverage(symbolValue, startIso, endIso, abort.signal);
      if (mounted.current && id === requestId.current && requestKey === currentKey.current) {
        setState({ key: requestKey, status: "done", result, error: null });
      }
    } catch (error) {
      if (mounted.current && id === requestId.current && requestKey === currentKey.current) {
        setState({ key: requestKey, status: "error", result: null, error: String(error) });
      }
    }
  }, []);

  return { state: state?.key === key ? state : null, check };
}
