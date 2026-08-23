import type { HudLineConfig, HudVariableKey } from "../types/workspace";

/**
 * Catalog of the on-chart HUD box's live variables. Each entry maps a
 * HudVariableKey onto the exact Feature Engine key(s) that back it
 * (useHudFeatures.ts's job to actually fetch/parse those) plus a short
 * label and a formatter for turning the raw number into the compact
 * string that appears in the box.
 *
 * Key mapping, verified against the live repo (backend/app/feature_engine)
 * rather than assumed:
 *   gap_pct            <- gap.py's "gap_pct" (indicators/gap.py)
 *   gap_dollars         <- gap.py's "gap_dollars"
 *   session_pct_change  <- session_change.py's "session_pct_change"
 *   session_dollar_change <- session_change.py's "session_dollar_change"
 *   atr                 <- atr.py's "atr_{period}", period fixed at 14
 *                          (core/config.py's feature_engine_atr_period) —
 *                          "ATR[5]" as originally asked for isn't
 *                          computed anywhere in the system; Saqib chose
 *                          ATR(14) (already live) over adding a second
 *                          backend period or a frontend-only approximation
 *                          when this was flagged in chat.
 *   atr_pct             <- atr.py's "atr_{period}_pct" — offered as a
 *                          second variable alongside atr, not used by any
 *                          default line, but available for a custom line
 *   rvol                <- rvol.py's "rvol" (confirmed decision #71)
 *   session_volume      <- engine.py's _update_vwap "session_volume",
 *                          regular session only (empty pre/post-market —
 *                          same scoping VWAP itself already has, not a
 *                          restriction invented for the HUD)
 */
export interface HudVariableDef {
  label: string;
  format: (value: number) => string;
}

function formatPct(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatDollarSigned(value: number): string {
  const sign = value >= 0 ? "+$" : "-$";
  return `${sign}${Math.abs(value).toFixed(2)}`;
}

function formatDollarPlain(value: number): string {
  return `$${value.toFixed(2)}`;
}

function formatMultiplier(value: number): string {
  return `${value.toFixed(2)}x`;
}

// Abbreviates large share counts the way most trading platforms display
// cumulative daily volume — full precision would be both harder to read
// at a glance and wider than a compact HUD line should be.
function formatShareCount(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return Math.round(value).toString();
}

export const HUD_VARIABLES: Record<HudVariableKey, HudVariableDef> = {
  gap_pct: { label: "GAP", format: formatPct },
  gap_dollars: { label: "GAP", format: formatDollarSigned },
  session_pct_change: { label: "DAY", format: formatPct },
  session_dollar_change: { label: "P/L", format: formatDollarSigned },
  atr: { label: "ATR", format: formatDollarPlain },
  atr_pct: { label: "ATR", format: formatPct },
  rvol: { label: "RVOL", format: formatMultiplier },
  session_volume: { label: "VOL", format: formatShareCount },
};

// "LABEL value" when the value is known; "LABEL —" (honest, not a
// fabricated 0) when it isn't yet — same "empty means not-yet, not zero"
// convention the backend itself uses (e.g. gap.py/atr.py returning {}
// before their inputs are available).
export function formatHudVariable(key: HudVariableKey, value: number | undefined): string {
  const def = HUD_VARIABLES[key];
  return `${def.label} ${value === undefined ? "—" : def.format(value)}`;
}

// Renders one HUD line's segments (literal text + variables, in order)
// into the final display string. A disabled line renders as "" and the
// caller (HudBox.tsx) skips it entirely — kept as a pure function here,
// separate from the segment-array shape itself (types/workspace.ts) and
// from the live-value lookup (useHudFeatures.ts), so each piece is
// independently testable in spirit even though there's no test runner
// wired up on the frontend yet (same note SubWindow.tsx's own comments
// make elsewhere in this codebase).
export function resolveHudLine(line: HudLineConfig, values: Partial<Record<HudVariableKey, number>>): string {
  if (!line.enabled) return "";
  return line.segments
    .map((seg) => (seg.kind === "text" ? seg.value : formatHudVariable(seg.variable, values[seg.variable])))
    .join("");
}

// Hex + 0-100 opacity -> rgba(...) string, same "hex picker + separate
// opacity control" split ColorField-adjacent pickers elsewhere in this
// app could grow later but don't have yet — VolumeAvgLineConfig/
// DailyLevelsConfig's colors are all opaque hex with no alpha channel,
// so this is new, self-contained math rather than a shared helper.
export function hexWithOpacity(hex: string, opacityPct: number): string {
  const clean = hex.replace("#", "");
  const full = clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean;
  const r = parseInt(full.slice(0, 2), 16) || 0;
  const g = parseInt(full.slice(2, 4), 16) || 0;
  const b = parseInt(full.slice(4, 6), 16) || 0;
  const a = Math.max(0, Math.min(100, opacityPct)) / 100;
  return `rgba(${r}, ${g}, ${b}, ${a})`;
}
