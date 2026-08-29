"""
ActivityScorer — Core Tier composite activity score, pure function only.
docs/architecture/scanner-design.md §2 (formula), §8 (config), §9 (why
spread tightness isn't a fourth input yet, and the current build-now
decision this module implements). Consumes FeatureSet.features exactly as
Feature Engine already publishes it — computes nothing new itself, same
"nothing downstream recomputes an indicator" rule system-design.md §4.5
states for Strategy Engine, extended here to the Scanner.

v1 = ACTIVITY + ATR-normalized |gap_pct| + ATR-normalized |session_pct_change|
— the two scan types Saqib chose to build and test the pipeline against
first (unusual volume, and volatility-relative move), on current APIs,
ahead of the IBKR subscription. No raw (non-normalized) gap or session
scan, and no spread tightness — both deliberately out of v1, not
overlooked (§2, §9).

ACTIVITY is `rvol` during regular session OR `premarket_volume_ratio`
during pre-market (docs/architecture/premarket-accumulator-design.md
§6) — NOT two independent inputs. The two are mutually exclusive by
session (Feature Engine never publishes both for the same FeatureSet:
`rvol` needs regular session, `premarket_volume_ratio` needs
`Session.PRE_MARKET`), so treating them as one shared conceptual slot
with two possible sources is more honest than bolting on a nominal 4th
input that could never actually coexist with the first — same "3
conceptual dimensions: activity, gap, session-change" shape whether it's
before or after the open, not a growing pile of independent weights.

ATR normalization: dividing the two %-based inputs by atr_14_pct expresses
"is this move large FOR THIS STOCK'S normal volatility" rather than
comparing raw percentages across a universe that mixes low-ATR and
high-ATR names — a low-ATR stock's small-looking raw move can still be a
real breakout; a high-ATR stock's large-looking raw move can still be
business as usual for it. Falls back to the raw (un-normalized) value only
when atr_14_pct itself isn't available yet (cold start, Feature Engine
needs a full daily-candle lookback before ATR exists at all) — better
than discarding an already-real rvol/gap/session-change reading entirely
just because ATR hasn't warmed up yet. It's ATR_PCT's absence that
triggers the fallback, never the numerator being zero.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.schemas.events.features import FeatureSet


@dataclass(frozen=True)
class ActivityScore:
    """
    `inputs_available` (0-3: activity [rvol or premarket_volume_ratio],
    gap, session_change) is deliberately NOT folded into `score` itself
    — it's exposed so a caller can tell "genuinely low activity" (score
    near 0, inputs_available == 3) apart from "not enough data yet to
    say anything" (score near 0, inputs_available == 0). The two are
    indistinguishable as a bare float, and conflating them would be
    exactly the kind of looks-real-but-isn't reading scanner-design.md's
    own §2 already warns against for the Feature Engine side of this.
    Promotion logic (not yet built — §5) should treat inputs_available
    == 0 as "exclude from this scan cycle's ranking," not as a real
    score of 0.0.
    """
    symbol: str
    score: float
    inputs_available: int


def score_symbol(
    symbol: str,
    feature_set: FeatureSet,
    *,
    weight_rvol: float,
    weight_gap: float,
    weight_session_change: float,
    weight_premarket_volume_ratio: float = 0.0,
) -> ActivityScore:
    """
    One symbol, one already-fetched FeatureSet in hand (the "1m" timeframe
    row — the only one rvol/gap_pct/session_pct_change/atr_14_pct/
    premarket_volume_ratio are ever computed under, per
    feature_engine/engine.py's SUPPORTED_TIMEFRAME). No I/O, no
    knowledge of ranking, universe, or promotion — trivially testable
    against hand-built FeatureSet fixtures, same style indicators/atr.py's
    own unit tests already use.

    A feature simply absent from `feature_set.features` (cold start, or
    the underlying indicator itself returned {} — same honest-gap
    convention every indicator in this package follows) is skipped
    entirely rather than treated as a real zero: rvol=0.0 would mean "we
    measured genuinely zero relative volume," which is a real, meaningful
    reading a missing key is not.

    `weight_premarket_volume_ratio` defaults to 0.0 (not required by
    callers that predate its existence — scripts/test_scanner_pipeline.py
    among them) — matching Settings' own default, inert until Saqib has
    actually looked at real values.
    """
    features = feature_set.features
    atr_pct = features.get("atr_14_pct")

    inputs_available = 0
    total = 0.0

    if "rvol" in features:
        total += weight_rvol * features["rvol"]
        inputs_available += 1
    elif "premarket_volume_ratio" in features:
        # elif, not a second `if` — these never both exist for the same
        # FeatureSet (session-gated, mutually exclusive), but `elif`
        # makes that mutual exclusivity structural rather than merely
        # assumed, so a future bug in either indicator's session gating
        # can't silently double-count this slot.
        total += weight_premarket_volume_ratio * features["premarket_volume_ratio"]
        inputs_available += 1

    if "gap_pct" in features:
        gap = abs(features["gap_pct"])
        total += weight_gap * (gap / atr_pct if atr_pct else gap)
        inputs_available += 1

    if "session_pct_change" in features:
        change = abs(features["session_pct_change"])
        total += weight_session_change * (change / atr_pct if atr_pct else change)
        inputs_available += 1

    return ActivityScore(symbol=symbol, score=round(total, 6), inputs_available=inputs_available)
