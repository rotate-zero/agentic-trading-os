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

--- `ESTABLISHED_TREND_SCORE_THRESHOLD` / `trend_established_side()`
added (decision #113) ---

Promoted out of `reversal_strategy.py`'s own module-level
`DEFAULT_TREND_SCORE_THRESHOLD` the moment a second strategy
(`vwap_strategy.py`) needed the exact same number for the opposite
purpose: Reversal fires only WITH an established trend, VWAP fires only
WITHOUT one, and the two are only genuine complements — no gap, no
overlap — if both read one authoritative value rather than two
independently-configurable `StrategyConfig.params` entries that happen
to start out equal. Saqib's own explicit call: "avoid creating a second
hardcoded 60/40 pair."

Still genuinely two separate configs underneath (`StrategyConfig` stays
per-strategy and versioned, §3 — this module doesn't change that), so
this only guarantees the DEFAULTS match, not that they stay matched
forever: nothing here stops Reversal's or VWAP's `StrategyConfig.params`
from being independently overridden to different values in a later
version. Flagged explicitly in both strategies' own docstrings — see
`strategy-engine-design.md` §10, D11.
"""
from __future__ import annotations

from typing import Literal

# trend_score >= this = established bullish; <= (100 - this) = established
# bearish; strictly between = neutral / no established trend. Single source
# of truth for "what counts as an established trend" across every strategy
# that needs that specific reading (see module docstring above).
ESTABLISHED_TREND_SCORE_THRESHOLD = 60.0


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


def trend_established_side(
    trend_score: float,
    threshold: float = ESTABLISHED_TREND_SCORE_THRESHOLD,
) -> Literal["bullish", "bearish"] | None:
    """Classifies `trend_score` against the shared established-trend
    threshold (decision #113). Returns `"bullish"` if `trend_score >=
    threshold`, `"bearish"` if `trend_score <= (100 - threshold)`, `None`
    for the neutral band in between — the exact classification
    `reversal_strategy.py`'s own `match_direction()` computed inline
    before this extraction, now also `vwap_strategy.py`'s own gate for
    the opposite condition (fires only when this returns `None`).

    Calls `validate_mirror_threshold()` first — `threshold` must be
    > 50.0 for the two branches to stay non-overlapping and jointly
    exhaustive-minus-the-neutral-band, same guard every mirror-around-50
    `match_direction()` in this codebase already needs."""
    validate_mirror_threshold(threshold)
    if trend_score >= threshold:
        return "bullish"
    if trend_score <= (100.0 - threshold):
        return "bearish"
    return None
