"""
Session % Change / Session $ Change — pure math only: current price vs.
the previous regular-session close (`pdc`, already computed by
FeatureEngine._update_previous_day). Continuously updates through
pre-market, regular session, and after-hours alike — it's just "current
price vs. yesterday's close," recomputed on every candle close the same
way vwap/pdc/pmh/pml are, with no state of its own (confirmed decisions
#67/#68, docs/architecture/feature-engine-indicator-expansion.md §2).

Deliberately distinct from Gap (gap.py): both reference the SAME `pdc`,
but this one tracks `close` continuously while Gap freezes at today's
regular-session open and never updates again that day.
"""
from __future__ import annotations


def session_change(close: float, pdc: float | None) -> dict[str, float]:
    """Returns {} when `pdc` isn't available yet (no prior trading day in
    the configured lookback window — a fresh symbol/deployment) — an
    honest gap, not an error, same convention `pdc` itself already
    carries via _update_previous_day's own `state["values"] is None`
    handling."""
    if pdc is None:
        return {}
    return {
        "session_pct_change": round((close - pdc) / pdc * 100, 6),
        "session_dollar_change": round(close - pdc, 6),
    }
