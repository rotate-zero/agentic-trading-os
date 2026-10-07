import type { BacktestSelectionSummaryGroupWireShape } from "../../services/api-client";

/**
 * Pure helpers for the "Compare selections" section (task
 * `backtest-selection-comparison`). Nothing here fetches, reads React state
 * or touches the workspace; every function is a deterministic transform of
 * the complete-population summaries `GET /intelligence/backtest-selection-summary`
 * returned. No metric is ever derived from the capped outcome list.
 */

export type SelectionKind = "run_id" | "sweep_id";

export interface ComparedSelection {
  kind: SelectionKind;
  id: string;
}

// Canonical 8-4-4-4-12 hexadecimal UUID text. The repository had no earlier
// client-side UUID check (the other run_id/sweep_id inputs trim and let the
// backend's 400 surface as a request failure), so this is deliberately
// narrow: it only stops an obviously malformed entry from being sent. The
// backend stays the authority; a value this accepts can still be unknown or
// rejected there, and both are shown as a request failure / unknown selection.
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type ParsedSelectionId = { ok: true; id: string } | { ok: false; message: string };

/** Trim, require a canonical UUID, and lower-case it so equal IDs compare equal. */
export function parseSelectionId(raw: string, kind: SelectionKind): ParsedSelectionId {
  const trimmed = raw.trim();
  if (trimmed === "") return { ok: false, message: `Enter a ${kind}.` };
  if (!UUID_PATTERN.test(trimmed)) {
    return { ok: false, message: `${kind} must be a UUID (8-4-4-4-12 hexadecimal characters).` };
  }
  return { ok: true, id: trimmed.toLowerCase() };
}

export function sameSelection(a: ComparedSelection | null, b: ComparedSelection | null): boolean {
  return a !== null && b !== null && a.kind === b.kind && a.id === b.id;
}

/** The full provenance key: strategy, version, configuration, data and feature version. */
export function groupKey(group: BacktestSelectionSummaryGroupWireShape): string {
  return JSON.stringify([
    group.strategy_name, group.strategy_version, group.config_hash, group.data_version, group.feature_version,
  ]);
}

export interface AlignedGroup {
  key: string;
  a: BacktestSelectionSummaryGroupWireShape;
  b: BacktestSelectionSummaryGroupWireShape;
  /** (B − A) × 100, percentage points; null when either win rate is null. */
  winRateDiffPp: number | null;
  /** B − A in R; null when either mean is null. */
  meanRDiff: number | null;
}

export interface GroupAlignment {
  aligned: AlignedGroup[];
  unmatchedA: BacktestSelectionSummaryGroupWireShape[];
  unmatchedB: BacktestSelectionSummaryGroupWireShape[];
}

/**
 * Align groups ONLY when every provenance field matches (identical key). A
 * partial match (same strategy, different configuration, say) is never
 * paired. The backend groups by exactly these five columns so a key is
 * unique per side; if a response ever repeated one, pairing would be a
 * guess, so such groups are left unmatched rather than merged or picked.
 * Order: aligned and unmatched-A keep A's server order, unmatched-B keeps B's.
 */
export function alignGroups(
  aGroups: BacktestSelectionSummaryGroupWireShape[],
  bGroups: BacktestSelectionSummaryGroupWireShape[],
): GroupAlignment {
  const count = (groups: BacktestSelectionSummaryGroupWireShape[]) => {
    const counts = new Map<string, number>();
    for (const g of groups) counts.set(groupKey(g), (counts.get(groupKey(g)) ?? 0) + 1);
    return counts;
  };
  const aCounts = count(aGroups);
  const bCounts = count(bGroups);
  const bByKey = new Map(bGroups.map((g) => [groupKey(g), g] as const));
  const isPair = (key: string) => aCounts.get(key) === 1 && bCounts.get(key) === 1;

  const aligned: AlignedGroup[] = [];
  const unmatchedA: BacktestSelectionSummaryGroupWireShape[] = [];
  for (const a of aGroups) {
    const key = groupKey(a);
    const b = isPair(key) ? bByKey.get(key) : undefined;
    if (!b) { unmatchedA.push(a); continue; }
    aligned.push({
      key, a, b,
      winRateDiffPp: a.win_rate === null || b.win_rate === null ? null : (b.win_rate - a.win_rate) * 100,
      meanRDiff: a.mean_realized_r === null || b.mean_realized_r === null ? null : b.mean_realized_r - a.mean_realized_r,
    });
  }
  const unmatchedB = bGroups.filter((g) => !isPair(groupKey(g)));
  return { aligned, unmatchedA, unmatchedB };
}

export interface SelectionTotals {
  runCount: number;
  outcomeCount: number;
  wins: number;
  losses: number;
  breakevens: number;
}

/** Exact integer sums over the groups — counts only; rates and means are never pooled. */
export function selectionTotals(groups: BacktestSelectionSummaryGroupWireShape[]): SelectionTotals {
  const totals: SelectionTotals = { runCount: 0, outcomeCount: 0, wins: 0, losses: 0, breakevens: 0 };
  for (const g of groups) {
    totals.runCount += g.run_count;
    totals.outcomeCount += g.total_outcomes;
    totals.wins += g.wins;
    totals.losses += g.losses;
    totals.breakevens += g.breakevens;
  }
  return totals;
}

export const formatWinRate = (rate: number | null): string => (rate === null ? "—" : `${(rate * 100).toFixed(2)}%`);
export const formatMeanR = (mean: number | null): string => (mean === null ? "—" : `${mean.toFixed(4)} R`);

function signed(value: number, digits: number, unit: string): string {
  const rounded = Number(value.toFixed(digits));
  if (rounded === 0) return `${(0).toFixed(digits)} ${unit}`; // never "-0.00"
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded).toFixed(digits)} ${unit}`;
}

/** "B − A" win-rate difference in percentage points; no difference exists when a rate is null. */
export const formatWinRateDiff = (pp: number | null): string => (pp === null ? "—" : signed(pp, 2, "pp"));
/** "B − A" mean-realized-R difference; no difference exists when a mean is null. */
export const formatMeanRDiff = (diff: number | null): string => (diff === null ? "—" : signed(diff, 4, "R"));
