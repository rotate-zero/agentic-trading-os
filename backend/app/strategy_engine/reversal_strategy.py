"""
Reversal Strategy — fifth strategy built against the real
`base_strategy.py`/`orb_strategy.py` pair (decision #99), design-locked
in decision #107 and refined against a design review in decision #108
(both: strategy-engine-design.md §16). Planned strategy set: trading-
intelligence-architecture.md §8 (ORB, Momentum, First Pullback, VWAP,
Gap, Reversal, Volume Spike).

Question this answers (§7's "question-based, not indicator-based"): "Has
an established trend's key reference level just been conquered against
it?" Same boundary discipline and same shared touch-resolution mechanism
as `first_pullback_strategy.py` — see that file's module docstring and
`level_touch_tracking.py`'s own docstring for the full reasoning behind
how resolution reconstruction, staleness handling, and the
`LevelInteractionEngine`/Strategy boundary are kept faithful; not
repeated here.

--- First Pullback vs. Reversal: same mechanism, opposite confirming
outcome, deliberately different touch-count gating (design review
point 6) ---

First Pullback confirms REJECTED (continuation); Reversal confirms
CONQUERED (against the established direction). First Pullback restricts
MATCH to `touch_count_today == 1`; Reversal deliberately does NOT — a
reversal is often the second or third test that finally breaks, not the
first, so `touch_count_today` is read for SCORE (a break after several
prior holds is stronger evidence than a break on the very first test)
but never gates MATCH.

--- MATCH vs. SCORE, kept strict (design review point 4) ---

MATCH is exactly: an established trend (per `trend_score`, still
reading its OLD, about-to-be-broken direction at the moment of
resolution) + the level just resolved CONQUERED. `volume_regime_score`
and `touch_count_today` belong to SCORE only, same deliberate divergence
from `orb_strategy.py`'s own MATCH-stage volume floor that
`first_pullback_strategy.py` already documents.

--- Direction is the mirror of the trend being broken ---

An established uptrend (`trend_score >= threshold`) conquered downward
proposes SELL, not BUY — the reversal bets AGAINST the trend that just
broke, not with it.

--- Gap-through has no `anchor_price` — live level value used instead ---

`level_touch_tracking.py`'s gap-through case (a steady zone jumping
straight to the opposite steady zone, `inside_aura` never observed in
between) never has a `holding` entry to read an anchor from — unlike
First Pullback, Reversal's MATCH condition (CONQUERED) genuinely
includes gap-throughs, so this file needs a real invalidation reference
even when `anchor_price` comes back `None`. Falls back to
`features.features.get(level_key)` — the level's own CURRENT value,
read directly off the same `FeaturesUpdated` this candle's `evaluate()`
call already carries — an honest, still-structural (not arbitrary)
reference, just the live value rather than a historical touch-start
snapshot. Flagged explicitly rather than silently degrading: this
fallback path is less precise than a real anchor and only ever
exercised on gap-throughs, expected to be rare.

--- Fires at most once per symbol per day ---

Same simplification `gap_strategy.py`'s `_GapState.fired` already makes
(a plain `bool`, not a set of directions): once a reversal has fired
against an established trend, that trend (by definition) is no longer
what `trend_score` will keep reading going forward, so there is no
"opposite-direction reversal" for THIS strategy to still catch the same
day the way ORB's `fired_directions` set watches for a genuine second,
opposite breakout of a fixed range. A different symbol/level_key
reversing again later the same day is still a fresh `_ReversalState`
reset by trading-day rollover, unaffected.

--- `allows_waiting` stays `False` for v1 ---

Same reasoning as `first_pullback_strategy.py` — D5 (the waiting-value
model) is explicitly deferred, First Pullback already flagged as its
natural first trigger; not duplicated here.

--- `DEFAULT_TREND_SCORE_THRESHOLD` promoted to a shared constant (decision #113) ---

Previously this file's own module-level `60.0`. Now reads
`scoring_utils.ESTABLISHED_TREND_SCORE_THRESHOLD` — `vwap_strategy.py`
needs the identical number for the complementary condition (VWAP fires
only when trend is NOT established; Reversal fires only when it IS), and
two independently-hardcoded 60.0/40.0 pairs would let the strategies'
firing conditions silently drift apart the moment either one's threshold
is retuned in a future `StrategyConfig` version. `match_direction()`
below now calls the shared `scoring_utils.trend_established_side()`
rather than its own inline `>=`/`<=` branching — behavior unchanged
(same two comparisons, same guard), just no longer a private copy of a
classification `vwap_strategy.py` also needs verbatim. See
`scoring_utils.py`'s own module docstring and `strategy-engine-design.md`
§10 (D11) for the still-open caveat: nothing structurally stops the two
`StrategyConfig.params` from being overridden to different values later
— only the DEFAULT is guaranteed to match.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from app.schemas.events.context import ContextChanged
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState
from app.strategy_engine.base_strategy import (
    Opportunity,
    Strategy,
    StrategyConfig,
    every_candle,
)
from app.strategy_engine.level_touch_tracking import LevelTouchState, observe_resolution
from app.strategy_engine.scoring_utils import (
    ESTABLISHED_TREND_SCORE_THRESHOLD,
    clamp,
    trend_established_side,
    trend_magnitude,
)
from app.trading_intelligence.level_interaction_engine import get_level_interaction_engine

# --- v1 defaults — all overridable via StrategyConfig.params (§3); a
# threshold change is a new StrategyConfig version, never an edit here. ---

DEFAULT_TIMEFRAME = "1m"  # touch/resolution needs candle-close granularity — not configurable per-instance
DEFAULT_LEVEL_KEY = "vwap"  # design review point 7 — params-driven, not a separate strategy class
DEFAULT_TREND_SCORE_THRESHOLD = ESTABLISHED_TREND_SCORE_THRESHOLD  # shared with vwap_strategy.py — decision #113, see module docstring
DEFAULT_TARGET_R_MULTIPLE = 2.0

# SCORE blend weights (sum to 1.0) — v1 guess, explicitly NOT validated
# against real score distributions yet, same caveat every other
# strategy/scoring module in this codebase states for its own
# calibration constants.
_W_BROKEN_TREND_STRENGTH = 0.40
_W_VOLUME = 0.30
_W_TOUCH_COUNT = 0.30

# Cap for touch_count_component's normalization below — a conquest
# after this many prior touches saturates SCORE's touch-count component
# at 100 ("third time's the charm" reasoning made concrete, not
# unbounded — a level tested 20 times isn't 20x more significant than
# one tested 5 times). v1 guess, unvalidated.
TOUCH_COUNT_CAP = 4


def default_params() -> dict:
    """v1 StrategyConfig.params — see module docstring for the
    reasoning behind each default."""
    return {
        "level_key": DEFAULT_LEVEL_KEY,
        "trend_score_threshold": DEFAULT_TREND_SCORE_THRESHOLD,
        "target_r_multiple": DEFAULT_TARGET_R_MULTIPLE,
    }


def match_direction(
    trend_score: float,
    *,
    trend_score_threshold: float,
) -> Literal["BUY", "SELL"] | None:
    """MATCH stage direction confirmation, pure. Direction is the MIRROR
    of the trend being broken — an established uptrend conquered
    downward proposes SELL, an established downtrend conquered upward
    proposes BUY (module docstring). Only reads `trend_score`: by the
    time this is called, the caller has already confirmed the touch
    resolved CONQUERED — the direction of the break itself is implicit
    in which side `trend_score` currently establishes as "the trend
    being broken," not in which side the price move went (unlike
    `first_pullback_strategy.py`, which cross-checks `entered_from`
    against `trend_score` for a rejection).

    `trend_score_threshold` must be > 50.0 — identical guard and
    identical reasoning to `orb_strategy.py`'s `match_direction()`. Now
    delegates the classification itself to the shared
    `scoring_utils.trend_established_side()` (decision #113, which also
    carries the `validate_mirror_threshold()` guard internally) rather
    than its own inline `>=`/`<=` branching — behavior unchanged, just no
    longer a private copy of a classification `vwap_strategy.py` also
    needs verbatim for the opposite condition."""
    side = trend_established_side(trend_score, trend_score_threshold)
    if side == "bullish":
        return "SELL"  # established uptrend just conquered downward — bet against it
    if side == "bearish":
        return "BUY"  # established downtrend just conquered upward — bet against it
    return None  # no established trend to reverse


def score_confidence(
    trend_score: float,
    volume_regime_score: float,
    touch_count_today: int,
) -> float:
    """SCORE stage, pure — how strongly the pattern matches, given MATCH
    already confirmed an established trend was just conquered.
    `touch_count_today` (the resolving touch's own count, already
    engine-provided) rewards a level that held multiple times before
    finally breaking — module docstring's "third time's the charm"
    reasoning."""
    broken_trend_component = trend_magnitude(trend_score)  # 0-100, direction-agnostic magnitude
    volume_component = volume_regime_score  # already 0-100 (Market State's own scale)
    touch_count_component = clamp((touch_count_today - 1) / (TOUCH_COUNT_CAP - 1) * 100.0) if TOUCH_COUNT_CAP > 1 else 0.0

    confidence = (
        _W_BROKEN_TREND_STRENGTH * broken_trend_component
        + _W_VOLUME * volume_component
        + _W_TOUCH_COUNT * touch_count_component
    )
    return round(clamp(confidence), 2)


def default_config(active_from: datetime, version: str = "reversal_v1") -> StrategyConfig:
    """Seed/testing convenience — constructs the v1 StrategyConfig this
    module was designed against."""
    return StrategyConfig(
        strategy_name="Reversal",
        version=version,
        params=default_params(),
        gate_conditions={"session": "regular"},
        allows_waiting=False,
        active_from=active_from,
        active_to=None,
        rationale=(
            "v1 Reversal: established trend's level_key (default vwap) "
            "conquered against it, any touch count (not gated to the "
            "first touch — see module docstring). LevelInteractionEngine's "
            "touch/zone state read via get_snapshot() polling, not a live "
            "event — see reversal_strategy.py module docstring and "
            "strategy-engine-design.md §16 (decisions #107/#108)."
        ),
    )


@dataclass
class _ReversalState:
    """Private per-symbol memory — mirrors `_FirstPullbackState`'s
    shape in `first_pullback_strategy.py`. `fired` is a plain `bool`,
    not a set of directions — see module docstring for why ORB's
    `fired_directions` precedent doesn't apply here."""

    trading_day: date | None = None
    fired: bool = False
    touch: LevelTouchState = field(default_factory=LevelTouchState)


class ReversalStrategy(Strategy):
    """See module docstring for the full GATE/MATCH/SCORE/PROPOSE anatomy."""

    name = "Reversal"
    trigger = every_candle(timeframe=DEFAULT_TIMEFRAME)

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._state: dict[str, _ReversalState] = {}

    def _state_for(self, symbol: str) -> _ReversalState:
        state = self._state.get(symbol)
        if state is None:
            state = _ReversalState()
            self._state[symbol] = state
        return state

    async def evaluate(
        self,
        symbol: str,
        market_state: MarketState,
        features: FeatureSet,
        context: ContextChanged,  # noqa: ARG002 — not used by v1 MATCH logic;
        # accepted for interface conformance, same as orb_strategy.py.
    ) -> Opportunity | None:
        params = self.config.params
        timeframe = DEFAULT_TIMEFRAME
        level_key = params.get("level_key", DEFAULT_LEVEL_KEY)

        # --- GATE ---
        if features.timeframe != timeframe:
            return None  # defensive timeframe scope — see orb_strategy.py precedent

        snapshot = get_level_interaction_engine().get_snapshot(symbol)
        entry = snapshot.get(symbol, {}).get(timeframe, {}).get(level_key)
        if entry is None:
            return None  # honest absence — level not tracked yet (e.g. indicator still warming up)

        last_applied_str = entry.get("last_applied_candle_ts")
        if last_applied_str is None:
            return None  # engine has never finished processing this (symbol, timeframe) at all
        last_applied_ts = datetime.fromisoformat(last_applied_str)
        if last_applied_ts.tzinfo is None:
            last_applied_ts = last_applied_ts.replace(tzinfo=features.candle_ts.tzinfo)
        if last_applied_ts < features.candle_ts:
            return None  # not caught up to this candle yet (decision #108) — skip, don't feed the tracker a stale read

        state = self._state_for(symbol)
        trading_day = date.fromisoformat(entry["trading_day"])
        if state.trading_day != trading_day:
            state.trading_day = trading_day
            state.fired = False

        resolution, _current_zone, anchor_price = observe_resolution(state.touch, entry)
        if resolution is None:
            return None  # nothing resolved this candle — still forming, steady, or unclassifiable cold start

        if state.fired:
            return None  # already proposed today — see module docstring
        if resolution != "conquered":
            return None  # REJECTED — the level held, trend intact, no reversal signal (not an error)

        # --- MATCH ---
        trend_score_threshold = params.get("trend_score_threshold", DEFAULT_TREND_SCORE_THRESHOLD)
        direction = match_direction(market_state.trend_score, trend_score_threshold=trend_score_threshold)
        if direction is None:
            return None

        # --- SCORE ---
        confidence = score_confidence(market_state.trend_score, market_state.volume_regime_score, entry["touch_count_today"])

        # --- PROPOSE ---
        target_r = params.get("target_r_multiple", DEFAULT_TARGET_R_MULTIPLE)
        if anchor_price is not None:
            invalidation = anchor_price
        else:
            # Gap-through — no holding entry ever existed to capture an
            # anchor from (module docstring). Fall back to the level's
            # current live value, read straight off this candle's own
            # FeatureSet — still structural, just less precise than a
            # real pre-conquest anchor.
            invalidation = features.features.get(level_key)
            if invalidation is None:
                return None  # honest absence — can't propose without any invalidation reference at all
        risk = abs(features.close - invalidation)
        target = features.close + target_r * risk if direction == "BUY" else features.close - target_r * risk

        state.fired = True

        return Opportunity(
            strategy=self.name,
            version=self.config.version,
            direction=direction,
            confidence=confidence,
            structural_invalidation=invalidation,
            structural_target=target,
            evidence={
                # Literal MATCH-stage values only — never a wholesale
                # FeatureSet dump (strategy-engine-design.md §4/§11 boundary).
                "conditions": {
                    "level_key": level_key,
                    "invalidation_source": "anchor_price" if anchor_price is not None else "live_level_value",
                    "close": features.close,
                    "trend_score": market_state.trend_score,
                    "volume_regime_score": market_state.volume_regime_score,
                    "touch_count_today": entry["touch_count_today"],
                },
                "reason": (
                    f"Reversal {direction.lower()}: {level_key} conquered against an established "
                    f"trend (trend_score={market_state.trend_score:.1f}) on touch #{entry['touch_count_today']} "
                    f"today, volume_regime_score={market_state.volume_regime_score:.1f}"
                ),
                "basis": "closed",  # §8 — no PriceSnapshot consumer wired yet;
                # every condition above was read off a settled FeatureSet/get_snapshot() read.
            },
            setup_detected_at=features.candle_ts,  # §7 — never datetime.now()
        )
