// Pure presentation helpers for the "Candidate observation" section
// (task `candidate-observation-status-ui`). No React and no I/O so they can be
// executed directly (this repo has no frontend test framework), in the same
// posture as the other pure formatters in ExecutionLifecyclePanel.tsx.
//
// Wording rule: say what is true in plain language. An unconfigured freshness
// policy is a missing setting, not a feed outage; an eligible candidate is not
// a selected or approved trade; confidence is descriptive, not a win rate.

export function humanizeCode(code: string): string {
  return code.replace(/[_:]+/g, " ").trim();
}

/** "2026-10-09T14:01:02Z" -> "2026-10-09 14:01:02 UTC"; unparseable -> "—". */
export function formatUtc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toISOString().replace("T", " ").replace(/(?:\.\d+)?Z$/, " UTC");
}

/** Whole-second duration: 30 -> "30s", 125 -> "2m 05s", 3720 -> "1h 02m". null -> "unknown". */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return "unknown";
  const total = Math.round(seconds);
  if (total < 60) return `${total}s`;
  if (total < 3600) return `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, "0")}s`;
  return `${Math.floor(total / 3600)}h ${String(Math.floor((total % 3600) / 60)).padStart(2, "0")}m`;
}

const REASON_TEXT: Record<string, string> = {
  freshness_policy_unconfigured: "Freshness policy is not configured for this timeframe, so it cannot be eligible.",
  expired: "Older than the configured maximum age.",
  source_age_unavailable: "Source age cannot be determined (as-of is earlier than the candle close).",
  not_actionable: "The strategy did not mark this opportunity actionable.",
  no_opportunity: "The strategy found no opportunity on this candle.",
  gated: "The strategy was gated off for this candle.",
  error: "The strategy evaluation failed.",
  opportunity: "Opportunity was not eligible.",
};

const INVALIDATION_TEXT: Record<string, string> = {
  prerequisite_unavailable:
    "Withdrawn: a newer candle's inputs were unavailable, so this earlier candidate can no longer be confirmed.",
};

/** One plain-language line for an eligibility reason code (unknown codes are shown as words, never hidden). */
export function describeReason(code: string): string {
  if (code in REASON_TEXT) return REASON_TEXT[code];
  if (code.startsWith("invalidated:")) {
    const inner = code.slice("invalidated:".length);
    return INVALIDATION_TEXT[inner] ?? `Withdrawn: ${humanizeCode(inner)}.`;
  }
  return `${humanizeCode(code)}.`;
}

export type DispositionTone = "bull" | "bear" | "signal" | "muted";

export interface DispositionView {
  label: string;
  tone: DispositionTone;
}

/** Eligible is deliberately NOT labelled "approved", "selected" or "trade". */
export function describeEligibility(eligible: boolean, disposition: string): DispositionView {
  if (eligible) return { label: "Eligible (observation only)", tone: "signal" };
  switch (disposition) {
    case "opportunity":
      return { label: "Not eligible", tone: "muted" };
    case "no_opportunity":
      return { label: "No opportunity", tone: "muted" };
    case "gated":
      return { label: "Gated", tone: "muted" };
    case "error":
      return { label: "Evaluation error", tone: "bear" };
    default:
      return { label: `Not eligible (${humanizeCode(disposition)})`, tone: "muted" };
  }
}

/** Explanation of why the whole section is unavailable (never an empty universe). */
export function describeUnavailable(reason: string | null, reasonText: string | null): string {
  if (reasonText) return reasonText;
  switch (reason) {
    case "reader_not_installed":
      return "No candidate observation reader is installed in this backend process.";
    case "reader_not_started":
      return "The candidate observation reader has not started, so it has observed nothing.";
    case "reader_stopped":
      return "The candidate observation reader is stopped, so its last state is not reported as current.";
    default:
      return "Candidate observation is unavailable.";
  }
}

export function describeFreshness(configured: boolean): string {
  return configured
    ? "Freshness policy configured: candidates older than the maximum age for their timeframe are ineligible."
    : "Freshness policy is not configured, so no candidate can be eligible yet. This is a missing setting, not a data-feed outage.";
}

/** "Strategy confidence 0.70 (descriptive, not a win probability)". null -> null. */
export function describeConfidence(confidence: number | null | undefined): string | null {
  if (confidence === null || confidence === undefined || !Number.isFinite(confidence)) return null;
  return `Strategy confidence ${confidence.toFixed(2)} (descriptive, not a win probability)`;
}

export function describeTruncation(returned: number, total: number, noun: string): string | null {
  return returned < total ? `Showing ${returned} of ${total} ${noun}; the list is not complete.` : null;
}

/** "gated 1 · opportunity 2" from a count map, in key order. */
export function formatCountMap(counts: Record<string, number>): string {
  const entries = Object.entries(counts).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return entries.length === 0 ? "none" : entries.map(([key, value]) => `${humanizeCode(key)} ${value}`).join(" · ");
}
