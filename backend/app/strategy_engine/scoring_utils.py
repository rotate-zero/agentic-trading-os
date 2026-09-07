"""
Small, genuinely-mechanical helpers shared across strategies' MATCH/SCORE
implementations — extracted after the design review following decisions
#104/#105 found the identical pattern copy-pasted, near-verbatim, across
three files (`orb_strategy.py`, `gap_strategy.py`,
`volume_spike_strategy.py`). Decision #107.

Deliberately NOT a "strategy scoring engine," and deliberately small.
What's here is domain-free arithmetic every strategy's MATCH/SCORE
happens to need in the identical shape:

- a 0-100 clamp,
- the direction-agnostic "how far from Market State's own neutral 50"
  measure every strategy's trend component has used identically since
  ORB,
- the threshold-guard every mirror-around-50 `match_direction()` needs
  identically (decision #99's original finding, independently
  rediscovered and re-fixed verbatim in both `gap_strategy.py` and
  `volume_spike_strategy.py` before this extraction — three copies of
  the same guard was the concrete signal this was overdue).

Deliberately NOT here: a generic weighted-blend/SCORE function. Each
strategy's own weights and its own strategy-specific third component
(breakout strength, gap strength, spike strength) are genuinely bespoke
— forcing them through one shared signature would trade three
straightforward lines of arithmetic for more ceremony than it saves.
Same "what should NOT be abstracted" discipline the review itself
applied — see strategy-engine-design.md §15.
"""
from __future__ import annotations


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """Bound a score to its valid range. Identical in all three strategy
    files before this extraction."""
    return max(lo, min(hi, value))


def trend_magnitude(trend_score: float) -> float:
    """0-100, direction-agnostic distance from Market State's neutral
    50 — the same SCORE trend component every strategy has computed
    identically since ORB (`abs(trend_score - 50.0) * 2.0`)."""
    return abs(trend_score - 50.0) * 2.0


def validate_mirror_threshold(threshold: float, *, param_name: str = "trend_score_threshold") -> None:
    """Every `match_direction()` in this codebase mirrors a BUY
    threshold onto SELL as `100 - threshold` — a threshold at or below
    50 flips onto the wrong side of neutral (decision #99's original
    finding: a threshold of 40 would let a mildly *bullish* trend_score
    of 55 satisfy SELL's confirmation, since 55 <= 100-40). Call at the
    top of any `match_direction()` using that mirror pattern, before
    `threshold` is used for anything else. Raises rather than silently
    producing a backwards-confirmed signal."""
    if threshold <= 50.0:
        raise ValueError(
            f"{param_name} must be > 50.0 for the BUY/SELL mirror-around-"
            f"neutral logic to hold (got {threshold})"
        )
