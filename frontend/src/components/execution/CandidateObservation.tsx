import { useState } from "react";
import { useCandidateObservation } from "../../hooks/useCandidateObservation";
import type {
  CandidateObservationRowWireShape,
  CandidateObservationSnapshotWireShape,
  CandidateObservationUnavailableInputWireShape,
} from "../../services/api-client";
import {
  describeConfidence,
  describeEligibility,
  describeFreshness,
  describeReason,
  describeTruncation,
  describeUnavailable,
  formatAge,
  formatCountMap,
  formatUtc,
  humanizeCode,
  type DispositionTone,
} from "./candidateObservationView";

// "Candidate observation" section of the Execution panel (task
// `candidate-observation-status-ui`): a read-only view of the C2 observation
// snapshot from GET /intelligence/candidate-observation. Visually separate on
// purpose (dashed accent border, "Observation only" tag): these are completed
// strategy evaluations, NOT recorded authorizations, orders or fills, and an
// eligible candidate is not a selected or approved trade. Loaded when the
// section is expanded and on manual Refresh; not a live feed. No action buttons.

const TONE_CLASS: Record<DispositionTone, string> = {
  bull: "text-bull",
  bear: "text-bear",
  signal: "text-signal",
  muted: "text-text-muted",
};

function CandidateRow({ row }: { row: CandidateObservationRowWireShape }) {
  const view = describeEligibility(row.eligible, row.disposition);
  const confidence = row.opportunity ? describeConfidence(row.opportunity.confidence) : null;
  const reasonLines = row.reasons.length > 0 ? row.reasons : row.eligible ? [] : [row.disposition];
  return (
    <div className="border-b border-base-border px-2 py-1.5 font-mono text-[10px] last:border-b-0" data-testid="candidate-observation-row">
      <div className="flex flex-wrap items-center justify-between gap-1">
        <span className="text-text-primary">
          {row.symbol} · {row.strategy} v{row.strategy_version} · {row.timeframe}
          {row.direction ? ` · ${row.direction}` : ""}
        </span>
        <span className={TONE_CLASS[view.tone]}>{view.label}</span>
      </div>
      {row.disposition_reason && (
        <div className="break-words text-text-muted">Strategy reported: {humanizeCode(row.disposition_reason)}</div>
      )}
      {reasonLines.map((code) => (
        <div key={code} className="break-words text-text-muted">{describeReason(code)}</div>
      ))}
      {row.opportunity && (
        <div className="text-text-muted">
          Opportunity {humanizeCode(row.opportunity.status)}
          {row.opportunity.expected_horizon_minutes !== null ? ` · horizon ${row.opportunity.expected_horizon_minutes} min` : ""}
        </div>
      )}
      {confidence && <div className="text-text-muted">{confidence}</div>}
      <div className="text-text-muted">
        Candle <time dateTime={row.source_candle_ts}>{formatUtc(row.source_candle_ts)}</time>
        {" · closed "}
        <time dateTime={row.source_interval_close}>{formatUtc(row.source_interval_close)}</time>
      </div>
      <div className="text-text-muted">
        Source age {formatAge(row.age_seconds)}
        {row.max_age_seconds !== null ? ` of max ${formatAge(row.max_age_seconds)}` : " (no maximum configured)"}
        {" · received "}
        <time dateTime={row.received_at}>{formatUtc(row.received_at)}</time>
      </div>
      <div className="break-all text-text-muted">
        Evaluation {row.evaluation_id}
        {row.candidate_id ? ` · Candidate ${row.candidate_id}` : ""}
      </div>
    </div>
  );
}

function UnavailableInputRow({ item }: { item: CandidateObservationUnavailableInputWireShape }) {
  return (
    <div className="border-b border-base-border px-2 py-1 font-mono text-[10px] last:border-b-0" data-testid="candidate-observation-unavailable-input">
      <span className="text-text-primary">{item.symbol} · {item.timeframe}</span>
      <span className="text-text-muted">
        {" · candle "}{formatUtc(item.source_candle_ts)}{" — "}
        {item.prerequisites.map((p) => `${humanizeCode(p.name ?? "")}: ${humanizeCode(p.reason ?? "")}`).join("; ")}
      </span>
    </div>
  );
}

function SnapshotView({ snapshot }: { snapshot: CandidateObservationSnapshotWireShape }) {
  const { counts, diagnostics } = snapshot;
  const candidateNote = describeTruncation(snapshot.candidates_truncation.returned, snapshot.candidates_truncation.total, "evaluations");
  const inputNote = describeTruncation(snapshot.unavailable_inputs_truncation.returned, snapshot.unavailable_inputs_truncation.total, "unavailable inputs");
  const problems = diagnostics.recent_problems;
  return (
    <>
      <p
        className={`px-2 pb-1.5 font-mono text-[10px] ${snapshot.freshness.configured ? "text-text-muted" : "text-signal"}`}
        data-testid="candidate-observation-freshness"
      >
        {describeFreshness(snapshot.freshness.configured)}
      </p>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 px-2 pb-1.5 font-mono text-[10px]" data-testid="candidate-observation-counts">
        <span className="text-text-primary">Completed evaluations {counts.evaluations}</span>
        <span className="text-signal">Eligible {counts.eligible}</span>
        <span className="text-text-muted">Ineligible {counts.ineligible}</span>
      </div>
      {counts.evaluations > 0 && (
        <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted">By disposition: {formatCountMap(counts.by_disposition)}</p>
      )}
      <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted">
        As of <time dateTime={snapshot.as_of}>{formatUtc(snapshot.as_of)}</time> · through delivery #{snapshot.arrival_sequence} ·
        {" "}resets {snapshot.reset.count}
        {snapshot.reset.boundary ? `, admitting candles from ${formatUtc(snapshot.reset.boundary)}` : ""}
      </p>

      {counts.evaluations === 0 && (
        <p className="px-2 pb-2 font-mono text-[10px] text-text-muted" data-testid="candidate-observation-empty">
          No completed strategy evaluations observed since the last reset. The reader is running; it simply has not received any yet.
        </p>
      )}
      {candidateNote && <p className="px-2 pb-1.5 font-mono text-[10px] text-signal">{candidateNote} Eligible rows are listed first.</p>}
      {snapshot.candidates.length > 0 && (
        <div className="max-h-96 overflow-y-auto border-t border-base-border">
          {snapshot.candidates.map((row) => (
            <CandidateRow key={row.evaluation_id + (row.candidate_id ?? "")} row={row} />
          ))}
        </div>
      )}

      {counts.unavailable_inputs > 0 && (
        <div className="border-t border-base-border">
          <p className="px-2 pt-1.5 font-mono text-[10px] text-text-primary">Unavailable inputs ({counts.unavailable_inputs})</p>
          <p className="px-2 pb-1 font-mono text-[10px] text-text-muted">
            A candle whose inputs were incomplete produced no evaluation, and earlier candidates for that symbol and timeframe were withdrawn.
          </p>
          {inputNote && <p className="px-2 pb-1 font-mono text-[10px] text-signal">{inputNote}</p>}
          <div className="max-h-32 overflow-y-auto">
            {snapshot.unavailable_inputs.map((item) => (
              <UnavailableInputRow key={`${item.symbol}|${item.timeframe}`} item={item} />
            ))}
          </div>
        </div>
      )}

      <details className="border-t border-base-border px-2 py-1 font-mono text-[10px]" data-testid="candidate-observation-diagnostics">
        <summary className="cursor-pointer text-text-muted">Diagnostics ({diagnostics.deliveries} deliveries)</summary>
        <div className="pt-1 text-text-muted">Outcomes: {formatCountMap(diagnostics.by_status)}</div>
        <div className="text-text-muted">Reasons: {formatCountMap(diagnostics.by_reason)}</div>
        <div className="text-text-muted">Resets: {formatCountMap(diagnostics.resets)}</div>
        {problems.length > 0 && (
          <div className="pt-1">
            <div className="text-text-primary">Recent problems (newest last)</div>
            {problems.map((problem) => (
              <div key={problem.arrival_sequence} className="break-words text-text-muted">
                #{problem.arrival_sequence} {humanizeCode(problem.status)}
                {problem.reason ? ` — ${humanizeCode(problem.reason)}` : ""}
                {problem.symbol ? ` · ${problem.symbol}` : ""}
                {problem.timeframe ? ` ${problem.timeframe}` : ""}
                {problem.source_candle_ts ? ` · candle ${formatUtc(problem.source_candle_ts)}` : ""}
              </div>
            ))}
          </div>
        )}
      </details>
    </>
  );
}

function CandidateObservationBody() {
  const { data, error, loadedAt, loading, refresh } = useCandidateObservation();
  return (
    <div className="border-t border-dashed border-signal/40 bg-base-bg/40">
      <div className="flex items-center justify-between gap-1 px-2 py-1">
        <span className="rounded border border-signal/40 px-1 font-mono text-[10px] text-signal">Observation only</span>
        <button onClick={refresh} className="rounded px-1 py-0.5 font-mono text-[10px] text-signal hover:bg-base-bg">
          Refresh
        </button>
      </div>
      <p className="px-2 pb-1.5 font-mono text-[10px] text-text-muted">
        Completed strategy evaluations the system has observed. Nothing here is selected, approved, planned or ordered — see Recorded
        authorizations and the order sections below for that. Eligible means only that a candidate passed the freshness and
        actionability checks. Loaded on expansion and Refresh, not a live feed.
      </p>
      {loading && <p className="px-2 pb-2 font-mono text-[10px] text-text-muted">Loading candidate observation…</p>}
      {error && (
        <p className="px-2 pb-2 font-mono text-[10px] text-bear">
          Could not fetch candidate observation: {error}{data !== null && " Showing the last loaded result."}
        </p>
      )}
      {data && data.status === "unavailable" && (
        <div className="px-2 pb-2 font-mono text-[10px]" data-testid="candidate-observation-unavailable">
          <p className="text-signal">Candidate observation is unavailable.</p>
          <p className="text-text-muted">{describeUnavailable(data.reason, data.reason_text)}</p>
          <p className="text-text-muted">No candidates are shown because nothing is being observed — this is not an empty universe.</p>
        </div>
      )}
      {data && data.status === "available" && data.snapshot && (
        <>
          <p className="px-2 pb-1 font-mono text-[10px] text-text-muted">
            Reader {data.reader.status ?? "unknown"} · mode {data.reader.execution_mode ?? "unknown"}
            {loadedAt && <> · loaded <time dateTime={loadedAt.toISOString()}>{formatUtc(loadedAt.toISOString())}</time></>}
          </p>
          <SnapshotView snapshot={data.snapshot} />
        </>
      )}
      {data && data.status === "available" && !data.snapshot && (
        <p className="px-2 pb-2 font-mono text-[10px] text-bear">The response was available but carried no snapshot.</p>
      )}
    </div>
  );
}

export function CandidateObservation() {
  const [expanded, setExpanded] = useState(false);
  return (
    <section className="border-b border-base-border" aria-label="Candidate observation">
      <button
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between px-2 py-1.5 text-left font-mono text-[11px] font-semibold text-text-primary hover:bg-base-bg"
      >
        <span>Candidate observation</span><span className="text-text-muted">{expanded ? "▾" : "▸"}</span>
      </button>
      {expanded && <CandidateObservationBody />}
    </section>
  );
}
