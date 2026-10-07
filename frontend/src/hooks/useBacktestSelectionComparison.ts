import { useBacktestSelectionSummary } from "./useBacktestSelectionSummary";
import type { BacktestSelectionSummaryWireShape } from "../services/api-client";
import type { ComparedSelection } from "../components/backtest-results/selectionComparison";

/**
 * Read side of `BacktestSelectionComparison.tsx` (task
 * `backtest-selection-comparison`). It owns no request logic of its own:
 * each side is one `useBacktestSelectionSummary` — the hook the Performance
 * summary card already uses over `fetchBacktestSelectionSummary` — so the
 * complete-population endpoint is the only data source and nothing is ever
 * computed from the capped outcome list.
 *
 * Independence: sides A and B are two separate hook instances with separate
 * request counters, so a slow, failed or superseded request on one side can
 * never delay, replace or clear the other. Within a side the inherited
 * guarantees apply: every load (selection change or manual `refetch`)
 * takes the next number from that side's counter and only the latest may
 * write state, so an older response arriving last — including after a
 * newer selection or Refresh — is discarded; the counter also advances on
 * unmount, so a completion after the section is collapsed touches nothing;
 * and a response keyed to a previous selection is never shown for the
 * current one (the snapshot's key must equal the applied selection's).
 *
 * Status per side:
 *   idle     no selection applied (no request)
 *   loading  first load for this selection, nothing to show yet
 *   error    the request failed (HTTP/network) — never shown as "unknown"
 *   unknown  the request succeeded and `selection_found` is false
 *   known    the selection exists; with `outcomeCount === 0` it is a KNOWN
 *            zero-outcome selection, distinct from unknown
 * `refreshing` is true while a reload runs over an already shown summary of
 * the same selection. This hook never reads or writes workspace state.
 */
export type SideStatus = "idle" | "loading" | "error" | "unknown" | "known";

export interface SideResult {
  selection: ComparedSelection | null;
  status: SideStatus;
  summary: BacktestSelectionSummaryWireShape | null;
  refreshing: boolean;
  error: string | null;
  refetch: () => void;
}

function useSide(selection: ComparedSelection | null): SideResult {
  const { summary, loading, error, refetch } = useBacktestSelectionSummary(
    selection?.kind ?? "run_id",
    selection?.id,
  );
  let status: SideStatus;
  if (!selection) status = "idle";
  else if (error) status = "error";
  else if (summary) status = summary.selection_found ? "known" : "unknown";
  else status = "loading";
  return {
    selection,
    status,
    summary: status === "known" || status === "unknown" ? summary : null,
    refreshing: loading && (status === "known" || status === "unknown"),
    error: selection ? error : null,
    refetch,
  };
}

export function useBacktestSelectionComparison(
  a: ComparedSelection | null,
  b: ComparedSelection | null,
): { a: SideResult; b: SideResult } {
  return { a: useSide(a), b: useSide(b) };
}
