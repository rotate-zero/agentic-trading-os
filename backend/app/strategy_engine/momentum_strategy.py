"""
Momentum Strategy — sixth strategy built against the real
`base_strategy.py`/`orb_strategy.py` pair (decision #99), delivered
together with `vwap_strategy.py` (decision #113). Planned strategy set:
trading-intelligence-architecture.md §8 (ORB, Momentum, First Pullback,
VWAP, Gap, Reversal, Volume Spike) — the sixth of seven, closing out all
but VWAP.

Question this answers (§7's "question-based, not indicator-based"): "Is
an existing directional move accelerating, right now, regardless of time
of day or any specific price level?" Deliberately NOT a time-boxed or
level-based question — that's ORB's job (a specific breakout of a
specific morning range). Momentum reads the market's own rate of change
and doesn't care whether today's opening range ever formed one.

Boundary discipline (system-design.md §4.5): "Feature Engine measures.
Market State interprets. Strategy decides." `market_state.trend_score`/
`acceleration_score`/`volume_regime_score` are read directly here, never
re-derived from raw slope/RVOL — Market State already did that
interpretation.

--- Genuinely distinct from ORB, co-firing accepted by design ---

Momentum and ORB can both fire on the same accelerating breakout candle
— confirmed acceptable directly by Saqib and by the external design
review this file's design went through before any code was written (same
"design note first, then code" practice as First Pullback/Reversal,
decisions #107/#108): nothing arbitrates "this is Momentum, not ORB" at
the strategy level, and nothing should — `base_strategy.py`'s own §0
framing already treats strategy competition as downstream (Opportunity/
Decision Engine's job, not this file's). ORB's question is "did a
specific structural level just break." Momentum's is "is the market
already moving, and gaining force, independent of any level at all" — a
sustained trend that's stopped accelerating (steady, not fresh) will NOT
match here even if ORB or Reversal would still find it interesting; a
fresh acceleration phase with no completed opening-range breakout WILL
match here even though ORB has nothing to say about it. Different
questions, not a re-parameterization of the same one.

--- `acceleration_score`'s actual formula, verified against
`market_state_engine/scoring.py` directly rather than assumed from the
field's name ---

`acceleration_score(trend_score_now, trend_score_prev, elapsed_seconds)`
is the RATE OF CHANGE of `trend_score` itself — `(trend_score_now -
trend_score_prev) / elapsed_seconds`, rescaled so a full 0-100
`trend_score` swing over ~60 seconds saturates it, 50 = holding steady.
Two consequences this design leans on directly rather than guessing at:

1. **No volume is baked in** — confirmed by reading the function body,
   not inferred from the name. `volume_regime_score` in this file's own
   MATCH is genuinely independent evidence, not a second read of
   something `acceleration_score` already folded in.
2. **`acceleration_score` and `trend_score` are NOT independent
   evidence** — acceleration is trend's own derivative. A trend that's
   strong but no longer changing (a mature, steady move) reads near
   neutral (50) on acceleration; this is Momentum's own intended
   identity (catch the accelerating phase, not a flat mature trend —
   that's other strategies' territory), not an accident of the formula,
   but it does mean `trend_score`'s role in MATCH below is deliberately
   a LIGHTER confirmation than Reversal/VWAP's "established trend"
   question, not the same threshold reused — see "Why trend's threshold
   here is NOT `scoring_utils.ESTABLISHED_TREND_SCORE_THRESHOLD`" below.

**Known, accepted noise risk, explicitly not resolved by empirical
testing before this build (Saqib's own call):** `market_state_engine/
engine.py`'s per-symbol `DebounceScheduler` recomputes on roughly a ~1s
floor / ~10s ceiling cadence. With `elapsed_seconds` that small,
ordinary candle-to-candle `trend_score` jitter can saturate
`acceleration_score` without a real regime shift underneath it. Saqib's
explicit instruction: choose a sensible threshold from the formula's own
semantics now, do not spend a session on empirical distribution
analysis first — `acceleration_score_threshold` below is flagged
unvalidated same as every other v1 calibration constant in this
codebase, expected to be retuned once real live distributions exist
(Stage 2, decision #112/D10, once unblocked by this build).

--- Why trend's threshold here is NOT `scoring_utils.ESTABLISHED_TREND_SCORE_THRESHOLD` ---

Reversal and VWAP (decision #113) share ONE authoritative "established
trend" threshold because they ask complementary questions about the same
specific claim (trend_score has committed to one side or the other).
Momentum's trend check asks something looser: "is the broader trend at
least leaning the same way the acceleration is pointing," a much weaker
claim than "established." Requiring the same 60/40 bar here would filter
out exactly the earliest, most valuable part of a fresh move — the
moment `trend_score` is still crossing through the low-to-mid 50s while
`acceleration_score` is already screaming, which is the whole reason
this strategy exists. `DEFAULT_TREND_CONTEXT_THRESHOLD` (55.0, own
params key `trend_context_threshold` — deliberately NOT named
`trend_score_threshold`, so it's never confused with Reversal/VWAP's
different question at the config level) is a distinct, lighter,
independently-tunable number. Still mirror-guarded (>50.0) via the same
`scoring_utils.validate_mirror_threshold()` every other strategy uses.

--- Hierarchy encoded in both MATCH and SCORE, not just prose ---

Per the design review: acceleration is the PRIMARY condition (why this
candle, not some other one), trend is CONTEXT (confirms the move is
directional, not a blip), volume is PARTICIPATION (confirms real size
behind it, direction-agnostic floor, same MATCH-stage-volume-floor
convention `orb_strategy.py` already established — a deliberate,
documented divergence from First Pullback/Reversal's SCORE-only volume,
same open question neither of those files resolved either). SCORE below
weights acceleration highest (0.45), trend next (0.25), volume last
(0.30) — encoding the same hierarchy numerically, not just gating on it.

--- Invalidation: private rolling swing lookback, not ATR, not LevelInteractionEngine ---

The design review preferred a structural swing failure ("the structure
supporting the momentum thesis broke") over an ATR-multiple distance
("price moved X against me") — a real distinction, not just a style
preference: ATR says nothing about whether the move's own recent
structure is still intact. `FeatureSet` has no swing-high/low field —
nothing publishes one — so this needed new private state, same category
as ORB's opening range and Volume Spike's rolling volume baseline
(`_MomentumState.recent_highs`/`recent_lows`, `deque(maxlen=lookback_bars)`).
Deliberately NOT a fractal/pivot-point detector — a plain rolling N-bar
high/low is enough for "has the recent structure broken," and building
anything fancier before a concrete gap appears would violate this
project's own "defer generality" discipline (`strategy-engine-design.md`
§11). Also deliberately NOT an ATR fallback for the warm-up period the
review floated as an "otherwise": this file instead just doesn't fire
yet during warm-up, honest-absence, exactly Volume Spike's own precedent
("nothing honest to compare against yet" — `volume_spike_strategy.py`'s
own module docstring) — simpler, one fewer code path, and the warm-up
window is only the first `lookback_bars` minutes of each trading day per
symbol, small enough that "don't fire yet" is a fully acceptable v1
answer rather than a real gap needing a fallback measure.

Same ordering discipline `volume_spike_strategy.py`'s own baseline uses:
the swing reference is read from the window BEFORE this candle's own
high/low join it — an accelerating candle can't manufacture its own
invalidation reference by setting a fresh extreme the very moment it's
being evaluated.

--- `structural_target` — required by the current `Opportunity` schema,
not a strong claim ---

The design review's own recommendation was "don't force a
strategy-specific projection; trade planning decides target, risk,
sizing downstream." `Opportunity.structural_target: float` is currently
a REQUIRED field on `base_strategy.py` (not `Optional`) — changing that
schema is a bigger, cross-cutting change touching all 6 other built
strategies and is out of scope for this file. Reconciled by supplying
the same mechanical R-multiple target every other v1 strategy already
uses (`close + target_r_multiple * risk`) — schema-completeness
placeholder, not a claim that this number is meaningful trading advice;
Trade Planning Engine (unbuilt) is expected to override or ignore it
once it exists, same as every other strategy's own target already is.

--- Cadence: cooldown, not once-per-day, not full "episode awareness" ---

Momentum's whole point is catching multiple genuinely independent
acceleration phases across a session — `gap_strategy.py`'s once-per-day
model would defeat that, and ORB's `fired_directions` set doesn't fit a
strategy with no fixed range to break twice. Reuses
`volume_spike_strategy.py`'s exact precedent instead: a single
per-symbol `cooldown_minutes` timer (v1 default 5, same value), not
gated to a fixed count or a specific direction. The design review raised
a further refinement — recognizing "still the same episode" rather than
treating every cooldown-cleared candle as a brand-new signal — explicitly
NOT built here, per the review's own conclusion: "acceptable for v1,"
deferred until real live behavior shows the plain cooldown is
insufficient, same "defer generality until a concrete gap appears"
discipline as the swing-lookback choice above.

--- Session scope ---

`gate_conditions={"session": "regular"}` (declared in `default_config()`
below, enforced solely by `StrategyScheduler`/`gate_conditions.py` —
decisions #117/#118, D16/#119), same as `gap_strategy.py`/
`volume_spike_strategy.py` — thin, unreliable trend/volume readings
outside regular hours, and nothing else in this file's own trigger
(`every_candle`, all session) would otherwise bound it. This file itself
no longer checks session directly; see D16/decision #119 for why the
prior inline `MarketClock.is_regular_session()` call here was removed as
redundant.

--- `allows_waiting` stays `False` for v1 ---

Same reasoning as every other v1 strategy — D5 (the waiting-value model)
is explicitly deferred, no strategy has hit a real need for it yet.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

from app.core.market_clock import get_market_clock
from app.schemas.events.context import ContextChanged
from app.schemas.events.features import FeatureSet
from app.schemas.events.market_state import MarketState
from app.strategy_engine.base_strategy import (
    Opportunity,
    Strategy,
    StrategyConfig,
    every_candle,
)
from app.strategy_engine.scoring_utils import clamp, trend_magnitude, validate_mirror_threshold

# --- v1 defaults — all overridable via StrategyConfig.params (§3); a
# threshold change is a new StrategyConfig version, never an edit here. ---

DEFAULT_TIMEFRAME = "1m"  # acceleration/swing tracking needs candle-close granularity — not configurable per-instance
DEFAULT_LOOKBACK_BARS = 10  # rolling swing-reference window — v1 guess, unvalidated, same category as
# volume_spike_strategy.py's own DEFAULT_LOOKBACK_BARS (a different window, same "own private rolling
# state" pattern, not the same deque)
DEFAULT_ACCELERATION_SCORE_THRESHOLD = 65.0  # primary condition — v1 guess from the formula's own
# semantics (module docstring), not empirically tuned; expected to move once Stage 2 produces
# real live acceleration_score distributions to check it against
DEFAULT_TREND_CONTEXT_THRESHOLD = 55.0  # deliberately lighter than scoring_utils.ESTABLISHED_TREND_SCORE_THRESHOLD —
# see module docstring's "Why trend's threshold here is NOT ESTABLISHED_TREND_SCORE_THRESHOLD"
DEFAULT_VOLUME_REGIME_THRESHOLD = 45.0  # same participation floor as orb_strategy.py (~rvol 1.35) —
# one of the few values carried over unchanged from the discarded draft's reviewed logic (decision #99)
DEFAULT_COOLDOWN_MINUTES = 5  # same value and shape as volume_spike_strategy.py's own DEFAULT_COOLDOWN_MINUTES
DEFAULT_TARGET_R_MULTIPLE = 2.0
DEFAULT_EXPECTED_HORIZON_MINUTES = 20  # v1 guess, unvalidated — shorter than ORB's 45 (a fixed morning
# structure) since Momentum is chasing an already-moving continuation, not a session-long thesis

# SCORE blend weights (sum to 1.0) — v1 guess, explicitly NOT validated
# against real score distributions yet, same caveat every other
# strategy/scoring module in this codebase states for its own
# calibration constants. Acceleration weighted highest, matching the
# MATCH-stage hierarchy (module docstring's "Hierarchy encoded in both
# MATCH and SCORE").
_W_ACCELERATION = 0.45
_W_TREND = 0.25
_W_VOLUME = 0.30


def default_params() -> dict:
    """v1 StrategyConfig.params — see module docstring for the
    reasoning behind each default."""
    return {
        "lookback_bars": DEFAULT_LOOKBACK_BARS,
        "acceleration_score_threshold": DEFAULT_ACCELERATION_SCORE_THRESHOLD,
        "trend_context_threshold": DEFAULT_TREND_CONTEXT_THRESHOLD,
        "volume_regime_threshold": DEFAULT_VOLUME_REGIME_THRESHOLD,
        "cooldown_minutes": DEFAULT_COOLDOWN_MINUTES,
        "target_r_multiple": DEFAULT_TARGET_R_MULTIPLE,
        "expected_horizon_minutes": DEFAULT_EXPECTED_HORIZON_MINUTES,
    }


def match_direction(
    trend_score: float,
    acceleration_score: float,
    volume_regime_score: float,
    *,
    acceleration_score_threshold: float,
    trend_context_threshold: float,
    volume_regime_threshold: float,
) -> Literal["BUY", "SELL"] | None:
    """MATCH stage, pure. Direction-symmetric on purpose, same shape
    every other `match_direction()` in this codebase uses: SELL is BUY's
    mirror image around each dimension's neutral 50. Acceleration is the
    PRIMARY gate (module docstring's hierarchy); trend confirms direction
    agreement at a deliberately LIGHTER bar than an "established trend"
    question; volume is a direction-agnostic participation floor, checked
    first — same MATCH-stage-volume-floor convention `orb_strategy.py`
    already established.

    Both thresholds must be > 50.0 — `validate_mirror_threshold()`,
    identical guard to every other mirror-around-50 `match_direction()`
    in this codebase."""
    validate_mirror_threshold(acceleration_score_threshold, param_name="acceleration_score_threshold")
    validate_mirror_threshold(trend_context_threshold, param_name="trend_context_threshold")

    if volume_regime_score < volume_regime_threshold:
        return None  # participation floor — direction-agnostic, checked once

    if acceleration_score >= acceleration_score_threshold:
        if trend_score >= trend_context_threshold:
            return "BUY"
        return None  # accelerating up, but the broader trend doesn't even lean that way yet — not a match
    if acceleration_score <= (100.0 - acceleration_score_threshold):
        if trend_score <= (100.0 - trend_context_threshold):
            return "SELL"
        return None
    return None  # not accelerating enough in either direction — nothing to match


def score_confidence(
    trend_score: float,
    acceleration_score: float,
    volume_regime_score: float,
) -> float:
    """SCORE stage, pure — how strongly the pattern matches, given MATCH
    already confirmed direction. `trend_magnitude()` is reused verbatim
    for `acceleration_score` (deliberate, not a coincidence): both
    dimensions share the identical 0-100/50-neutral shape, so the same
    direction-agnostic "distance from neutral" measure applies without
    re-deriving it."""
    acceleration_component = trend_magnitude(acceleration_score)  # 0-100, direction-agnostic
    trend_component = trend_magnitude(trend_score)  # 0-100, direction-agnostic
    volume_component = volume_regime_score  # already 0-100 (Market State's own scale)

    confidence = (
        _W_ACCELERATION * acceleration_component
        + _W_TREND * trend_component
        + _W_VOLUME * volume_component
    )
    return round(clamp(confidence), 2)


def default_config(active_from: datetime, version: str = "momentum_v1") -> StrategyConfig:
    """Seed/testing convenience — constructs the v1 StrategyConfig this
    module was designed against."""
    return StrategyConfig(
        strategy_name="Momentum",
        version=version,
        params=default_params(),
        gate_conditions={"session": "regular"},
        allows_waiting=False,
        active_from=active_from,
        active_to=None,
        rationale=(
            "v1 Momentum: acceleration_score is the primary MATCH condition "
            "(directional trend actively speeding up), trend_score a lighter "
            "context confirmation (deliberately NOT scoring_utils."
            "ESTABLISHED_TREND_SCORE_THRESHOLD — see momentum_strategy.py "
            "module docstring), volume_regime_score a participation floor. "
            "Invalidation is a private rolling swing low/high, not ATR, not "
            "LevelInteractionEngine. Cooldown-based cadence (volume_spike_"
            "strategy.py precedent), not once-per-day. All thresholds are "
            "v1 defaults, unvalidated against real score distributions."
        ),
    )


@dataclass
class _MomentumState:
    """Private per-symbol memory — mirrors `_VolumeSpikeState`'s shape
    (`volume_spike_strategy.py`). Two bounded deques instead of one:
    `recent_highs`/`recent_lows` are the rolling swing-reference window
    (module docstring's invalidation section), `last_fired_ts` is the
    cooldown timer (same field, same purpose, same name as
    `_VolumeSpikeState`'s own)."""

    trading_day: date
    recent_highs: deque[float]
    recent_lows: deque[float]
    last_fired_ts: datetime | None = None


class MomentumStrategy(Strategy):
    """See module docstring for the full GATE/MATCH/SCORE/PROPOSE anatomy."""

    name = "Momentum"
    trigger = every_candle(timeframe=DEFAULT_TIMEFRAME)

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._state: dict[str, _MomentumState] = {}

    def _state_for(self, symbol: str, trading_day: date, lookback_bars: int) -> _MomentumState:
        state = self._state.get(symbol)
        if state is None or state.trading_day != trading_day:
            # First time seeing this symbol, or a new day — reset. A new
            # day gets a fresh swing window (yesterday's power-hour range
            # shouldn't set today's invalidation reference) and a fresh
            # cooldown, same "track the trading_day one has, compare,
            # reset on change" shape orb_strategy.py's/volume_spike_
            # strategy.py's own _state_for already use.
            state = _MomentumState(
                trading_day=trading_day,
                recent_highs=deque(maxlen=lookback_bars),
                recent_lows=deque(maxlen=lookback_bars),
            )
            self._state[symbol] = state
        return state

    async def evaluate(
        self,
        symbol: str,
        market_state: MarketState,
        features: FeatureSet,
        context: ContextChanged,  # noqa: ARG002 — not used by v1 MATCH logic;
        # accepted for interface conformance, same as every other v1 strategy.
    ) -> Opportunity | None:
        params = self.config.params
        timeframe = DEFAULT_TIMEFRAME

        # --- GATE ---
        if features.timeframe != timeframe:
            return None  # defensive timeframe scope — see module docstring
        if features.high is None or features.low is None:
            return None  # honest absence — pre-decision #99 FeatureSet, or an aggregated
            # timeframe's FeatureSet slipping through despite the check above; either way,
            # can't accumulate a swing reference without real wicks (same reasoning
            # orb_strategy.py's own opening-range check already documents).

        clock = get_market_clock()

        if market_state.acceleration_score is None:
            return None  # honest absence — this symbol's first-ever Market State recompute (decision #93)

        trading_day = clock.trading_day(features.candle_ts)
        lookback_bars = params.get("lookback_bars", DEFAULT_LOOKBACK_BARS)
        state = self._state_for(symbol, trading_day, lookback_bars)

        if len(state.recent_highs) < lookback_bars:
            # Still building an honest swing reference — module
            # docstring's "Invalidation" section. Push this candle in for
            # a future reference; there's nothing honest to invalidate
            # against yet.
            state.recent_highs.append(features.high)
            state.recent_lows.append(features.low)
            return None

        swing_high = max(state.recent_highs)
        swing_low = min(state.recent_lows)
        # This candle joins the window for the NEXT candle's swing
        # reference — never its own (module docstring: an accelerating
        # candle can't manufacture its own invalidation reference).
        state.recent_highs.append(features.high)
        state.recent_lows.append(features.low)

        cooldown_minutes = params.get("cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)
        if state.last_fired_ts is not None and features.candle_ts < state.last_fired_ts + timedelta(minutes=cooldown_minutes):
            return None  # cooldown, not a fired-once-per-day flag — module docstring "Cadence"

        # --- MATCH ---
        direction = match_direction(
            market_state.trend_score,
            market_state.acceleration_score,
            market_state.volume_regime_score,
            acceleration_score_threshold=params.get("acceleration_score_threshold", DEFAULT_ACCELERATION_SCORE_THRESHOLD),
            trend_context_threshold=params.get("trend_context_threshold", DEFAULT_TREND_CONTEXT_THRESHOLD),
            volume_regime_threshold=params.get("volume_regime_threshold", DEFAULT_VOLUME_REGIME_THRESHOLD),
        )
        if direction is None:
            return None

        # --- SCORE ---
        confidence = score_confidence(market_state.trend_score, market_state.acceleration_score, market_state.volume_regime_score)

        # --- PROPOSE ---
        invalidation = swing_low if direction == "BUY" else swing_high
        # the thesis is the recent structure holding — a close back
        # through the recent swing falsifies it, same "structural, not
        # arbitrary" reasoning orb_strategy.py's own invalidation comment
        # uses, applied to a rolling reference instead of a fixed range.
        risk = abs(features.close - invalidation)
        if risk <= 0:
            return None  # honest — a degenerate swing reference (e.g. this candle's own
            # close already at or beyond it) leaves nothing meaningful to invalidate against

        target_r = params.get("target_r_multiple", DEFAULT_TARGET_R_MULTIPLE)
        target = features.close + target_r * risk if direction == "BUY" else features.close - target_r * risk

        state.last_fired_ts = features.candle_ts

        return Opportunity(
            strategy=self.name,
            version=self.config.version,
            direction=direction,
            confidence=confidence,
            structural_invalidation=invalidation,
            structural_target=target,  # schema-required placeholder — module docstring "structural_target" section
            expected_horizon_minutes=params.get("expected_horizon_minutes", DEFAULT_EXPECTED_HORIZON_MINUTES),
            evidence={
                # Literal MATCH-stage values only — never a wholesale
                # FeatureSet dump (strategy-engine-design.md §4/§11 boundary).
                "conditions": {
                    "close": features.close,
                    "trend_score": market_state.trend_score,
                    "acceleration_score": market_state.acceleration_score,
                    "volume_regime_score": market_state.volume_regime_score,
                    "swing_high": swing_high,
                    "swing_low": swing_low,
                    "lookback_bars": lookback_bars,
                },
                "reason": (
                    f"Momentum {direction.lower()}: acceleration_score={market_state.acceleration_score:.1f} "
                    f"(threshold {params.get('acceleration_score_threshold', DEFAULT_ACCELERATION_SCORE_THRESHOLD):.1f}), "
                    f"trend_score={market_state.trend_score:.1f}, volume_regime_score={market_state.volume_regime_score:.1f}, "
                    f"invalidation at {invalidation:.2f} ({lookback_bars}-bar swing)"
                ),
                "basis": "closed",  # §8 — no PriceSnapshot consumer wired yet;
                # every condition above was read off a settled FeatureSet/MarketState.
            },
            setup_detected_at=features.candle_ts,  # §7 — never datetime.now()
        )
