import { useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import { useBacktestSelectionComparison, type SideResult } from "../../hooks/useBacktestSelectionComparison";
import type { BacktestSelectionSummaryGroupWireShape } from "../../services/api-client";
import {
  alignGroups,
  formatMeanR,
  formatMeanRDiff,
  formatWinRate,
  formatWinRateDiff,
  parseSelectionId,
  sameSelection,
  selectionTotals,
  type AlignedGroup,
  type ComparedSelection,
  type SelectionKind,
} from "./selectionComparison";

type Side = "a" | "b";
const SIDES: Side[] = ["a", "b"];

interface Draft {
  kind: SelectionKind;
  input: string;
  error: string | null;
}
type Drafts = Record<Side, Draft>;
type Applied = Record<Side, ComparedSelection | null>;

const BUTTON =
  "rounded border border-base-border px-1.5 py-0.5 font-mono text-[10px] text-text-muted enabled:hover:border-signal enabled:hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50";

function SelectorRow({
  side, draft, appliedSelection, onChange, onApply, onRefresh,
}: {
  side: Side;
  draft: Draft;
  appliedSelection: ComparedSelection | null;
  onChange: (patch: Partial<Draft>) => void;
  onApply: () => void;
  onRefresh: () => void;
}) {
  const label = side.toUpperCase();
  return (
    <div className="flex flex-col gap-1" data-testid={`compare-selector-${side}`}>
      <div className="flex gap-1">
        <span className="w-4 shrink-0 pt-1 font-mono text-[10px] font-semibold text-text-primary">{label}</span>
        <select
          aria-label={`Selection ${label} type`}
          value={draft.kind}
          onChange={(e) => onChange({ kind: e.target.value as SelectionKind, error: null })}
          className="rounded border border-base-border bg-base-bg px-1 py-1 font-mono text-[10px] text-text-primary focus:border-signal focus:outline-none"
        >
          <option value="run_id">run_id</option>
          <option value="sweep_id">sweep_id</option>
        </select>
        <input
          aria-label={`Selection ${label} ID`}
          value={draft.input}
          onChange={(e) => onChange({ input: e.target.value, error: null })}
          onKeyDown={(e) => e.key === "Enter" && onApply()}
          placeholder={`paste a ${draft.kind}`}
          className="min-w-0 flex-1 rounded border border-base-border bg-base-bg px-1.5 py-1 font-mono text-[10px] text-text-primary placeholder:text-text-muted focus:border-signal focus:outline-none"
        />
        <button type="button" onClick={onApply} className={BUTTON}>Apply {label}</button>
        {/* Never disabled while a request is pending: a hung request must stay
            recoverable, and each press supersedes the older one. */}
        <button type="button" onClick={onRefresh} disabled={!appliedSelection} className={BUTTON}>Refresh {label}</button>
      </div>
      {draft.error && <p role="alert" className="pl-5 font-mono text-[9px] text-bear">{draft.error}</p>}
    </div>
  );
}

function statusText(result: SideResult): string {
  switch (result.status) {
    case "idle": return "No selection applied.";
    case "loading": return "Loading…";
    case "error": return `Request failed: ${result.error}`;
    case "unknown": return "Unknown — no recorded run or sweep with this ID.";
    case "known": {
      const outcomes = selectionTotals(result.summary?.groups ?? []).outcomeCount;
      const base = outcomes === 0 ? "Recorded, zero outcomes." : "Recorded.";
      return result.refreshing ? `${base} Refreshing…` : base;
    }
  }
}

function OverviewTable({ results }: { results: Record<Side, SideResult> }) {
  const totals = SIDES.map((s) => (results[s].status === "known" ? selectionTotals(results[s].summary?.groups ?? []) : null));
  const rows: { label: string; cell: (side: number) => string }[] = [
    { label: "Selection", cell: (i) => (results[SIDES[i]].selection ? `${results[SIDES[i]].selection!.kind} ${results[SIDES[i]].selection!.id}` : "—") },
    { label: "State", cell: (i) => statusText(results[SIDES[i]]) },
    { label: "Runs", cell: (i) => (totals[i] ? String(totals[i]!.runCount) : "—") },
    { label: "Outcomes", cell: (i) => (totals[i] ? String(totals[i]!.outcomeCount) : "—") },
    { label: "Wins", cell: (i) => (totals[i] ? String(totals[i]!.wins) : "—") },
    { label: "Losses", cell: (i) => (totals[i] ? String(totals[i]!.losses) : "—") },
    { label: "Breakevens", cell: (i) => (totals[i] ? String(totals[i]!.breakevens) : "—") },
  ];
  return (
    <table className="w-full table-fixed border-collapse font-mono text-[10px]" data-testid="compare-overview">
      <thead>
        <tr className="text-left text-text-muted">
          <th className="w-16 py-0.5 font-normal"> </th>
          <th className="py-0.5 pr-1 font-semibold text-text-primary">A</th>
          <th className="py-0.5 font-semibold text-text-primary">B</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label} className="align-top border-t border-base-border">
            <td className="py-0.5 text-text-muted">{row.label}</td>
            <td className="break-all py-0.5 pr-1 text-text-primary">{row.cell(0)}</td>
            <td className="break-all py-0.5 text-text-primary">{row.cell(1)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Provenance({ group }: { group: BacktestSelectionSummaryGroupWireShape }) {
  return (
    <>
      <div className="font-semibold text-text-primary">{group.strategy_name} · {group.strategy_version}</div>
      <div className="break-all text-text-muted">Config: {group.config_hash}</div>
      <div className="break-all text-text-muted">Data: {group.data_version} · Features: {group.feature_version}</div>
    </>
  );
}

function AlignedGroupCard({ pair }: { pair: AlignedGroup }) {
  const { a, b } = pair;
  const rows: { label: string; a: string; b: string; diff?: string; diffLabel?: string }[] = [
    { label: "Runs", a: String(a.run_count), b: String(b.run_count) },
    { label: "Outcomes", a: String(a.total_outcomes), b: String(b.total_outcomes) },
    { label: "Wins", a: String(a.wins), b: String(b.wins) },
    { label: "Losses", a: String(a.losses), b: String(b.losses) },
    { label: "Breakevens", a: String(a.breakevens), b: String(b.breakevens) },
    { label: "Win rate", a: formatWinRate(a.win_rate), b: formatWinRate(b.win_rate), diff: formatWinRateDiff(pair.winRateDiffPp), diffLabel: "B − A, percentage points" },
    { label: "Mean realized R", a: formatMeanR(a.mean_realized_r), b: formatMeanR(b.mean_realized_r), diff: formatMeanRDiff(pair.meanRDiff), diffLabel: "B − A, R" },
  ];
  return (
    <div className="rounded border border-base-border p-1.5 font-mono text-[10px]" data-testid="compare-aligned-group">
      <Provenance group={a} />
      <table className="mt-1 w-full table-fixed border-collapse">
        <thead>
          <tr className="text-left text-text-muted">
            <th className="w-20 py-0.5 font-normal"> </th>
            <th className="py-0.5 font-semibold text-text-primary">A</th>
            <th className="py-0.5 font-semibold text-text-primary">B</th>
            <th className="py-0.5 font-semibold text-text-primary">B − A</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label} className="border-t border-base-border align-top">
              <td className="py-0.5 text-text-muted">{row.label}</td>
              <td className="py-0.5 text-text-primary">{row.a}</td>
              <td className="py-0.5 text-text-primary">{row.b}</td>
              <td className="py-0.5 text-text-primary" title={row.diffLabel}>{row.diff ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {(pair.winRateDiffPp === null || pair.meanRDiff === null) && (
        <p className="mt-1 text-text-muted">A difference is shown only when both sides have that metric; a missing metric has no difference.</p>
      )}
    </div>
  );
}

function SingleGroupCard({ group }: { group: BacktestSelectionSummaryGroupWireShape }) {
  return (
    <div className="rounded border border-base-border p-1.5 font-mono text-[10px]" data-testid="compare-single-group">
      <Provenance group={group} />
      <div className="mt-1 text-text-primary">{group.run_count} run(s) · {group.total_outcomes} outcome(s)</div>
      {group.total_outcomes === 0 ? (
        <p className="text-text-muted">No outcomes recorded for this group. Win rate and mean R are unavailable.</p>
      ) : (
        <div className="text-text-muted">
          <div>Wins: {group.wins} · Losses: {group.losses} · Breakevens: {group.breakevens}</div>
          <div>Win rate: {formatWinRate(group.win_rate)} · Mean realized R: {formatMeanR(group.mean_realized_r)}</div>
        </div>
      )}
    </div>
  );
}

function GroupList({ title, note, groups, testId }: {
  title: string; note?: string; groups: BacktestSelectionSummaryGroupWireShape[]; testId: string;
}) {
  if (groups.length === 0) return null;
  return (
    <div className="flex flex-col gap-1" data-testid={testId}>
      <h4 className="font-mono text-[10px] font-semibold text-text-primary">{title}</h4>
      {note && <p className="font-mono text-[9px] text-text-muted">{note}</p>}
      {groups.map((g) => <SingleGroupCard key={JSON.stringify([g.strategy_name, g.strategy_version, g.config_hash, g.data_version, g.feature_version])} group={g} />)}
    </div>
  );
}

function ComparisonResults({ results }: { results: Record<Side, SideResult> }) {
  const a = results.a;
  const b = results.b;
  const bothKnown = a.status === "known" && b.status === "known";
  const aGroups = a.summary?.groups ?? [];
  const bGroups = b.summary?.groups ?? [];

  let body: ReactNode = null;
  if (bothKnown) {
    const { aligned, unmatchedA, unmatchedB } = alignGroups(aGroups, bGroups);
    body = (
      <>
        <div className="flex flex-col gap-1" data-testid="compare-aligned">
          <h4 className="font-mono text-[10px] font-semibold text-text-primary">Matching provenance groups</h4>
          {aligned.length === 0 ? (
            <p className="font-mono text-[9px] text-text-muted">
              No group has an identical strategy, version, configuration, data and feature version on both sides, so nothing is aligned.
            </p>
          ) : (
            aligned.map((pair) => <AlignedGroupCard key={pair.key} pair={pair} />)
          )}
        </div>
        <GroupList testId="compare-unmatched-a" title="Only in A" note="No group in B has this exact provenance, so it is not compared." groups={unmatchedA} />
        <GroupList testId="compare-unmatched-b" title="Only in B" note="No group in A has this exact provenance, so it is not compared." groups={unmatchedB} />
      </>
    );
  } else if (a.status !== "idle" || b.status !== "idle") {
    body = (
      <>
        <p className="font-mono text-[9px] text-text-muted" data-testid="compare-incomplete">
          Groups are aligned only when both selections are loaded and recorded. Each side's own groups are shown without comparison.
        </p>
        {a.status === "known" && <GroupList testId="compare-groups-a" title="Selection A groups" groups={aGroups} />}
        {b.status === "known" && <GroupList testId="compare-groups-b" title="Selection B groups" groups={bGroups} />}
      </>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <OverviewTable results={results} />
      {body}
    </div>
  );
}

// The body (and therefore both summary requests) exists only while the
// section is expanded: collapsing unmounts it, which invalidates anything in
// flight, and expanding re-requests the applied selections. Drafts and the
// applied selections live in the always-mounted wrapper so collapsing does
// not lose what the person entered.
function ComparisonBody({
  drafts, setDrafts, applied, setApplied,
}: {
  drafts: Drafts;
  setDrafts: Dispatch<SetStateAction<Drafts>>;
  applied: Applied;
  setApplied: Dispatch<SetStateAction<Applied>>;
}) {
  const results = useBacktestSelectionComparison(applied.a, applied.b);

  const patchDraft = (side: Side, patch: Partial<Draft>) =>
    setDrafts((previous) => ({ ...previous, [side]: { ...previous[side], ...patch } }));

  const apply = (side: Side) => {
    const draft = drafts[side];
    const parsed = parseSelectionId(draft.input, draft.kind);
    if (!parsed.ok) {
      patchDraft(side, { error: parsed.message });
      return;
    }
    const next: ComparedSelection = { kind: draft.kind, id: parsed.id };
    patchDraft(side, { input: parsed.id, error: null });
    if (sameSelection(applied[side], next)) results[side].refetch(); // re-applying the same selection reloads it
    else setApplied((previous) => ({ ...previous, [side]: next }));
  };

  return (
    <div className="flex max-h-[28rem] flex-col gap-2 overflow-y-auto border-t border-base-border p-2">
      <div className="flex flex-col gap-1.5">
        {SIDES.map((side) => (
          <SelectorRow
            key={side}
            side={side}
            draft={drafts[side]}
            appliedSelection={applied[side]}
            onChange={(patch) => patchDraft(side, patch)}
            onApply={() => apply(side)}
            onRefresh={() => results[side].refetch()}
          />
        ))}
      </div>
      <p className="font-mono text-[9px] leading-snug text-text-muted">
        Each side is read from the complete-population summary for its run or sweep — not from the capped outcome list.
        Selecting here does not change the results view, Follow latest or the workspace's latest run/sweep.
      </p>
      <ComparisonResults results={results} />
      <p className="font-mono text-[9px] leading-snug text-text-muted" data-testid="compare-disclaimer">
        Differences are descriptive (B − A). They do not establish statistical significance or profitability, and no
        strategy is ranked, scored or recommended.
      </p>
    </div>
  );
}

/**
 * Collapsible "Compare selections" section (task
 * `backtest-selection-comparison`): two independent selectors, A and B, each
 * a run_id or a sweep_id, loaded through the existing complete-population
 * summary endpoint and shown side by side with provenance-aligned,
 * descriptive B − A differences. Self-contained: it takes no props, reads
 * nothing from and writes nothing to the workspace, and leaves the panel's
 * selected results, Follow latest, history browser, summary card and CSV
 * export untouched.
 */
export function BacktestSelectionComparison() {
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState<Drafts>({
    a: { kind: "run_id", input: "", error: null },
    b: { kind: "run_id", input: "", error: null },
  });
  const [applied, setApplied] = useState<Applied>({ a: null, b: null });

  return (
    <div className="shrink-0 border-b border-base-border" data-testid="compare-selections-section">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 px-2 py-1 text-left font-mono text-[10px] uppercase tracking-wide text-text-muted hover:text-text-primary"
      >
        <span>{open ? "▾" : "▸"}</span>
        <span>Compare selections</span>
      </button>
      {open && <ComparisonBody drafts={drafts} setDrafts={setDrafts} applied={applied} setApplied={setApplied} />}
    </div>
  );
}
