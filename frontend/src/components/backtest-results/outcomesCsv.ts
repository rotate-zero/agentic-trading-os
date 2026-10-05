import type { StrategyOutcomeWireShape } from "../../services/api-client";

// Pure CSV export helpers for BacktestResultsPanel.tsx ("Download loaded
// rows"). Everything here is deterministic and DOM-free except
// `downloadCsvFile`, which owns the browser download resources.
//
// Contract (task `backtest-results-csv-export`):
//  - one header row with stable, snake_case column names that equal the
//    wire field names (`OUTCOME_CSV_COLUMNS` below is the single source of
//    truth; order is part of the contract);
//  - one record per outcome, in the order given (the panel passes its
//    displayed order);
//  - numbers are written with `String(value)` (JS shortest round-trip,
//    never rounded, never given a "+" sign); `null`/`undefined` and
//    non-finite numbers are empty cells;
//  - text is written verbatim except for formula protection (below);
//  - timestamps that already are ISO-8601 strings are written verbatim
//    (keeps the backend's own precision and offset); any other
//    parseable timestamp is converted with `toISOString()`; an unparseable
//    one is written verbatim;
//  - records end with CRLF (RFC 4180); fields containing a comma, quote,
//    CR or LF are double-quoted with embedded quotes doubled, and embedded
//    line breaks are preserved inside the quotes.

type CellKind = "text" | "timestamp" | "number";

interface CsvColumn {
  header: keyof StrategyOutcomeWireShape;
  kind: CellKind;
}

// Requested core fields first, then the remaining scalar fields the
// panel's own full-record view already shows. The JSON blobs (evidence,
// market state / context snapshots, snapshot_missing_reasons) are
// deliberately not exported: they are not scalar and do not belong in a
// flat spreadsheet row.
export const OUTCOME_CSV_COLUMNS: readonly CsvColumn[] = [
  { header: "outcome_id", kind: "text" },
  { header: "backtest_run_id", kind: "text" },
  { header: "symbol", kind: "text" },
  { header: "strategy_name", kind: "text" },
  { header: "strategy_version", kind: "text" },
  { header: "direction", kind: "text" },
  { header: "entry_filled_at", kind: "timestamp" },
  { header: "exit_filled_at", kind: "timestamp" },
  { header: "entry_price", kind: "number" },
  { header: "exit_price", kind: "number" },
  { header: "realized_r", kind: "number" },
  { header: "exit_reason", kind: "text" },
  { header: "realized_pnl", kind: "number" },
  { header: "entry_qty", kind: "number" },
  { header: "exit_qty", kind: "number" },
  { header: "holding_seconds", kind: "number" },
  { header: "commission_total", kind: "number" },
  { header: "slippage_entry", kind: "number" },
  { header: "final_stop", kind: "number" },
  { header: "final_target", kind: "number" },
  { header: "structural_invalidation", kind: "number" },
  { header: "structural_target", kind: "number" },
  { header: "confidence_at_signal", kind: "number" },
  { header: "trading_day", kind: "text" },
  { header: "opportunity_id", kind: "text" },
  { header: "origin", kind: "text" },
];

const ISO_8601 = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?)?$/;

// A spreadsheet may evaluate a text cell whose first character is one of
// these as a formula (CSV/formula injection). Only TEXT cells are
// protected, by a leading apostrophe that spreadsheets display as a
// literal-text marker; numeric cells come exclusively from real numbers
// (`typeof value === "number"`), so a legitimate negative value such as
// -1.25 is never touched.
const FORMULA_LEADERS = new Set(["=", "+", "-", "@", "\t", "\r"]);

export function protectFormulaText(text: string): string {
  return text.length > 0 && FORMULA_LEADERS.has(text[0]) ? `'${text}` : text;
}

export function escapeCsvField(field: string): string {
  return /[",\r\n]/.test(field) ? `"${field.replace(/"/g, '""')}"` : field;
}

function textCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  return escapeCsvField(protectFormulaText(String(value)));
}

function timestampCell(value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  const raw = String(value);
  if (ISO_8601.test(raw)) return escapeCsvField(raw);
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? textCell(raw) : parsed.toISOString();
}

function numberCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
  // Not expected from the wire contract; keep it as protected text rather
  // than silently reinterpreting it as a number.
  return textCell(value);
}

export function outcomesToCsv(outcomes: readonly StrategyOutcomeWireShape[]): string {
  const lines = [OUTCOME_CSV_COLUMNS.map((c) => c.header).join(",")];
  for (const outcome of outcomes) {
    lines.push(
      OUTCOME_CSV_COLUMNS.map((c) => {
        const value = outcome[c.header];
        if (c.kind === "number") return numberCell(value);
        if (c.kind === "timestamp") return timestampCell(value);
        return textCell(value);
      }).join(","),
    );
  }
  return lines.join("\r\n") + "\r\n";
}

export type ExportMode = "run" | "sweep";

// `backtest-outcomes-<run|sweep>-<id>.csv`. The identifier is reduced to
// [A-Za-z0-9._-] (anything else becomes "_"), leading dots/underscores are
// dropped and the result is capped at 64 characters, so the name is safe
// on every filesystem and can never carry a path separator. An unfiltered
// run view (no applied run_id) is named `...-run-unfiltered.csv`.
export function outcomesCsvFilename(mode: ExportMode, appliedId: string | undefined): string {
  const cleaned = (appliedId ?? "")
    .replace(/[^A-Za-z0-9._-]/g, "_")
    .replace(/^[._]+/, "")
    .slice(0, 64);
  return `backtest-outcomes-${mode}-${cleaned === "" ? "unfiltered" : cleaned}.csv`;
}

// How long the object URL stays valid after the click. Revoking
// synchronously can cancel the download in some browsers; a short delay
// is enough because the browser has already started reading the blob.
export const OBJECT_URL_RELEASE_DELAY_MS = 10_000;

const UTF8_BOM = "\uFEFF"; // lets Excel detect UTF-8 for non-ASCII text; harmless to other readers

// Creates a Blob, a temporary object URL and a detached-then-removed
// anchor, clicks it, removes the anchor immediately and revokes the URL
// after OBJECT_URL_RELEASE_DELAY_MS. Every resource it creates is released
// even if the click throws.
export function downloadCsvFile(filename: string, csv: string): void {
  const url = URL.createObjectURL(new Blob([UTF8_BOM, csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), OBJECT_URL_RELEASE_DELAY_MS);
  }
}
