"""
Gap % / Gap $ — pure math only: today's regular-session open vs. the
previous regular-session close (`pdc`). engine.py owns capturing WHICH
price counts as "today's regular open" and freezing it for the rest of
the day (FeatureEngine._update_gap's own docstring) — a genuinely
stateful, once-per-day concern, same split every other file in this
package keeps (pure math here, orchestration there — see
previous_day.py/camarilla.py/premarket.py's own docstrings). Confirmed
decisions #67/#68, docs/architecture/feature-engine-indicator-expansion.md §3.

Deliberately distinct from Session % Change (session_change.py): both
reference the SAME `pdc`, but this one freezes at today's regular-session
open instead of tracking `close` continuously.
"""
from __future__ import annotations


def gap(regular_open: float | None, pdc: float | None) -> dict[str, float]:
    """Returns {} when either input isn't available yet. `regular_open`
    is None before today's regular session has started — pre-market
    FeatureSets never carry gap by design (session_change.py's
    session_pct_change IS already defined at that point, since it only
    needs `pdc` — an intentional asymmetry, not a bug, called out in the
    design doc). `pdc` is None on a fresh symbol/deployment with no prior
    trading day yet."""
    if regular_open is None or pdc is None:
        return {}
    return {
        "gap_pct": round((regular_open - pdc) / pdc * 100, 6),
        "gap_dollars": round(regular_open - pdc, 6),
    }
