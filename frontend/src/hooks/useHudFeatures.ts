import { useIntelligenceState } from "./useIntelligenceState";
import type { HudVariableKey } from "../types/workspace";

/**
 * Flat lookup of the specific Feature Engine values the on-chart HUD box
 * (types/workspace.ts's HudConfig) needs — gap %/$, session %/$ change,
 * ATR(14) raw + %, RVOL, session volume — for one sub-window's own
 * symbol+timeframe.
 *
 * Deliberately its OWN hook rather than reusing useFeatureEngineLevels.ts
 * unmodified: that hook intentionally excludes periodic units (any entry
 * with a non-null period, e.g. atr's "14") because it's scoped to
 * HorizontalLevelType matching, where a periodic key could never map onto
 * a level anyway (see its own docstring). The HUD needs atr_14's raw
 * value specifically (period "14"), so this hook reads periodic and
 * non-periodic units alike. Both still build on the same underlying
 * useIntelligenceState (decision #47's zero-DB-I/O snapshot) — no new
 * REST/WS plumbing, same reasoning useFeatureEngineLevels.ts already
 * established.
 *
 * Key-by-key mapping (verified against engine.py/indicators/*.py, not
 * assumed — see utils/hud.ts's own header comment for the full trace):
 *   gap_pct, gap_dollars                <- non-periodic units, same key names
 *   session_pct_change, session_dollar_change <- non-periodic, same key names
 *   atr_14_pct                          <- non-periodic unit "atr_14_pct" -> HudVariableKey "atr_pct"
 *   atr_14 (raw)                        <- unit "atr", period "14" -> HudVariableKey "atr"
 *   rvol                                <- non-periodic unit "rvol"
 *   session_volume                      <- non-periodic unit "session_volume"
 *     (absent outside regular session — _update_vwap's own scoping, not
 *     a restriction invented here; the HUD line simply renders "—" for it)
 */
export function useHudFeatures(symbol: string, timeframe: string): Partial<Record<HudVariableKey, number>> {
  const { timeframes } = useIntelligenceState(symbol);
  const tf = timeframes.find((t) => t.timeframe === timeframe);
  if (!tf) return {};

  const values: Partial<Record<HudVariableKey, number>> = {};
  for (const unit of tf.units) {
    if (unit.key === "atr") {
      // Takes whatever single period is actually published rather than
      // hardcoding "14" — feature_engine_atr_period (core/config.py) is
      // one global setting with exactly one entry per publish, so this
      // stays correct even if that setting is ever changed, without
      // this hook needing to know its value.
      const entry = unit.entries[0];
      if (entry) values.atr = entry.value;
      continue;
    }
    if (unit.entries.length !== 1 || unit.entries[0].period !== null) continue;
    const value = unit.entries[0].value;
    switch (unit.key) {
      case "gap_pct":
        values.gap_pct = value;
        break;
      case "gap_dollars":
        values.gap_dollars = value;
        break;
      case "session_pct_change":
        values.session_pct_change = value;
        break;
      case "session_dollar_change":
        values.session_dollar_change = value;
        break;
      case "atr_14_pct":
        values.atr_pct = value;
        break;
      case "rvol":
        values.rvol = value;
        break;
      case "session_volume":
        values.session_volume = value;
        break;
      default:
        break; // every other non-periodic unit (pdh, vwap, ...) isn't a HUD variable
    }
  }
  return values;
}
