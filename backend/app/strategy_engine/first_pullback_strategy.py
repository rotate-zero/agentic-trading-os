"""
First Pullback Strategy — fourth strategy built against the real
`base_strategy.py`/`orb_strategy.py` pair (decision #99), design-locked
in decision #107 and refined against a design review in decision #108
(both: strategy-engine-design.md §16). Planned strategy set: trading-
intelligence-architecture.md §8 (ORB, Momentum, First Pullback, VWAP,
Gap, Reversal, Volume Spike).

Question this answers (§7's "question-based, not indicator-based"): "Is
this the trend's first pullback to a key reference level today, and did
the level hold?" A setup-detector, same boundary discipline as
`orb_strategy.py`/`gap_strategy.py`: `market_state.trend_score`/
`volume_regime_score` are read directly, never re-derived from raw
slope/RVOL. `LevelInteractionEngine`'s zone/touch/entered_from state is
read the same way — this file answers "does an already-classified
pattern constitute my setup," it does not re-derive what happened at
the level (design review point 2).

--- The one thing genuinely this file's own job ---

Whether an already-resolved touch, in an already-established trend
direction, constitutes a "first pullback continuation" setup. Both the
trend read and the touch-resolution classification are consumed
verbatim from Market State and `LevelInteractionEngine` respectively —
see `level_touch_tracking.py`'s own module docstring for exactly how the
resolution reconstruction stays faithful to the engine's authoritative
definition (gap-through, cold-start-unknown-origin, and the async-worker
staleness gap all handled there / via `last_applied_candle_ts` below,
not reinvented per-strategy).

--- Why `level_key` is a param, not a hardcoded constant ---

Same "not its own strategy class" precedent §3 already sets for SMA
9/20 (`strategy-engine-design.md` §16, design review point 7): a second
`StrategyConfig` version pointed at `"sma_20"` instead of `"vwap"` gives
a second First Pullback variant for free, no new code.

--- MATCH vs. SCORE, kept strict (design review point 4) ---

MATCH is exactly: established trend + this is literally the FIRST touch
of `level_key` today + that touch resolved REJECTED (bounced back) in
the trend's favor. `volume_regime_score`, `touch_count_today` (used
elsewhere for Reversal's SCORE, not applicable here since MATCH already
requires it to be 1), and how far the rejection has already carried
price all belong to SCORE only. This is a deliberate divergence from
`orb_strategy.py`'s `match_direction()`, which DOES use
`volume_regime_score` as a MATCH-stage participation floor — flagged in
strategy-engine-design.md §16 as a considered choice specific to this
strategy (and Reversal), not a retrofit onto ORB/Gap/Volume Spike.

--- "First" is structural, not a quality signal ---

`touch_count_today == 1` gates MATCH itself, unlike Reversal (which
reads it for SCORE only, never gates on it — see `reversal_strategy.py`'s
own module docstring for why). The name of this strategy IS "first
pullback" — a second or third touch is a conceptually different setup
this file deliberately does not also claim, left for a future strategy
family if Saqib ever wants one, not built speculatively here.

--- Invalidation semantics (design review point 5) ---

`structural_invalidation = anchor_price` — the level's own value at the
moment the (now-resolved) touch began, read verbatim from
`level_touch_tracking.py`'s captured state, never re-derived. This is a
STRATEGY-level thesis-invalidation reference ("the pullback's 'held'
thesis is falsified if a later candle CLOSES back through this same
value"), not an executable stop price — translating a thesis boundary
into actual stop/order mechanics is explicitly Trade Planning
Engine/Position Monitor's job downstream (neither built yet), same
separation `Opportunity.structural_invalidation`'s own field comment in
`base_strategy.py` already draws. A single intrabar tick isn't enough to
falsify this thesis on its own — candle-close precision, matching
`LevelInteractionEngine`'s own resolution granularity.

--- Staleness guard (decision #108) ---

`get_snapshot()`'s `last_applied_candle_ts` is checked before this
strategy ever calls into `level_touch_tracking.observe_resolution()` —
a stale entry is skipped entirely (GATE returns `None`), never fed
through the tracker, so a lagging read can't corrupt this strategy's own
zone history with data older than the candle currently being evaluated.
See `level_touch_tracking.py`'s module docstring for why order matters
here.

--- Symbol-keyed state and day rollover ---

`self._state: dict[str, _FirstPullbackState]`, mirroring
`orb_strategy.py`'s `_ORBState` shape. `fired` resets on trading-day
rollover (read off `LevelInteractionEngine`'s own `entry["trading_day"]`,
never a separate `MarketClock` call — no reason to introduce a second
source for the same fact `level_touch_tracking.py` already parses).

--- `allows_waiting` stays `False` for v1 ---

The "touch just started, not yet resolved" moment (`level_touch_tracking`
returning `(None, "inside_aura", None)`) is a natural fit for a
`status="waiting"` Opportunity (§8's ACT/WAIT/ABANDON model) instead of
silently returning `None` until resolution — but D5 (the waiting-value
model itself) is explicitly deferred until a real strategy needs it,
and no strategy has triggered that yet (strategy-engine-design.md §16).
Flagged rather than built speculatively.
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
from app.trading_intelligence.level_interaction_engine import get_level_interaction_engine

# --- v1 defaults — all overridable via StrategyConfig.params (§3); a
# threshold change is a new StrategyConfig version, never an edit here. ---

DEFAULT_TIMEFRAME = "1m"  # touch/resolution needs candle-close granularity — not configurable per-instance
DEFAULT_LEVEL_KEY = "vwap"  # design review point 7 — params-driven, not a separate strategy class
DEFAULT_TREND_SCORE_THRESHOLD = 60.0  # same convention/value as orb_strategy.py
DEFAULT_TARGET_R_MULTIPLE = 2.0

# SCORE blend weights (sum to 1.0) — v1 guess, explicitly NOT validated
# against real score distributions yet, same caveat every other
# strategy/scoring module in this codebase states for its own
# calibration constants.
_W_TREND = 0.40
_W_VOLUME = 0.30
_W_REJECTION_STRENGTH = 0.30

# Cap for rejection_strength_component's normalization below, in
# distance_pct units — a rejection that's already carried price this far
# from the level (as a % of the level's own value) saturates SCORE's
# rejection-strength component at 100. Deliberately much smaller than
# ORB's BREAKOUT_STRENGTH_CAP (0.5, a fraction of a WIDE opening range):
# this is a % distance from a single point level, not a fraction of a
# multi-tick range. v1 guess, unvalidated.
REJECTION_STRENGTH_CAP_PCT = 1.0


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def default_params() -> dict:
    """v1 StrategyConfig.params — see module docstring for the
    reasoning behind each default."""
    return {
        "level_key": DEFAULT_LEVEL_KEY,
        "trend_score_threshold": DEFAULT_TREND_SCORE_THRESHOLD,
        "target_r_multiple": DEFAULT_TARGET_R_MULTIPLE,
    }


def match_direction(
    entered_from: str,
    trend_score: float,
    *,
    trend_score_threshold: float,
) -> Literal["BUY", "SELL"] | None:
    """MATCH stage direction confirmation, pure. Requires the resolved
    touch's own `entered_from` to agree with `trend_score`'s direction —
    price pulling back FROM ABOVE (a dip in an uptrend) confirmed by a
    bullish `trend_score` proposes BUY; FROM BELOW confirmed by a
    bearish `trend_score` proposes SELL. A mismatch (e.g. entered from
    above while `trend_score` reads bearish) is not forced into either
    direction — returns `None`, same "no second, independently-sourced
    price pair to fall out of sync" discipline `orb_strategy.py`'s own
    module docstring establishes.

    `trend_score_threshold` must be > 50.0 — identical guard and
    identical reasoning to `orb_strategy.py`'s `match_direction()`."""
    if trend_score_threshold <= 50.0:
        raise ValueError(
            f"trend_score_threshold must be > 50.0 for the BUY/SELL "
            f"mirror-around-neutral logic to hold (got {trend_score_threshold})"
        )

    if entered_from == "above" and trend_score >= trend_score_threshold:
        return "BUY"
    if entered_from == "below" and trend_score <= (100.0 - trend_score_threshold):
        return "SELL"
    return None


def score_confidence(
    trend_score: float,
    volume_regime_score: float,
    resolution_distance_pct: float,
) -> float:
    """SCORE stage, pure — how strongly the pattern matches, given MATCH
    already confirmed direction and rejection. `resolution_distance_pct`
    is the resolving candle's own `distance_pct` (from `get_snapshot()`,
    already signed relative to the live level — see
    `rejection_strength_fraction()` below for the direction-aware
    normalization), not re-derived here."""
    trend_component = abs(trend_score - 50.0) * 2.0  # 0-100, direction-agnostic magnitude
    volume_component = volume_regime_score  # already 0-100 (Market State's own scale)
    rejection_component = _clamp(resolution_distance_pct / REJECTION_STRENGTH_CAP_PCT * 100.0)

    confidence = (
        _W_TREND * trend_component
        + _W_VOLUME * volume_component
        + _W_REJECTION_STRENGTH * rejection_component
    )
    return round(_clamp(confidence), 2)


def rejection_strength_fraction(distance_pct: float, direction: Literal["BUY", "SELL"]) -> float:
    """Normalizes the resolving candle's signed `distance_pct` (positive
    = above the level, negative = below) into a direction-agnostic
    "how far has the rejection already carried price" magnitude, clamped
    at zero for the (rare, but possible) case where a resolution candle's
    `distance_pct` still points the wrong way at read time. Pure
    function of already-engine-computed values — no score inputs — same
    "independently testable" split `orb_strategy.py`'s own
    `breakout_strength_fraction()` keeps."""
    return max(0.0, distance_pct if direction == "BUY" else -distance_pct)


def default_config(active_from: datetime, version: str = "first_pullback_v1") -> StrategyConfig:
    """Seed/testing convenience — constructs the v1 StrategyConfig this
    module was designed against."""
    return StrategyConfig(
        strategy_name="FirstPullback",
        version=version,
        params=default_params(),
        gate_conditions={"session": "regular"},
        allows_waiting=False,
        active_from=active_from,
        active_to=None,
        rationale=(
            "v1 First Pullback: established trend + the first touch of "
            "level_key (default vwap) today, resolved as a rejection in "
            "the trend's favor. LevelInteractionEngine's touch/zone state "
            "read via get_snapshot() polling, not a live event — see "
            "first_pullback_strategy.py module docstring and "
            "strategy-engine-design.md §16 (decisions #107/#108)."
        ),
    )


@dataclass
class _FirstPullbackState:
    """Private per-symbol memory — mirrors `orb_strategy.py`'s
    `_ORBState` shape. `touch` is this strategy's own
    `LevelTouchState` for `level_key`; `fired` is a belt-and-suspenders
    explicit guard against re-proposing the same day's already-resolved
    first touch — `level_touch_tracking.observe_resolution()` itself
    only ever returns a non-`None` resolution once per touch, so this
    shouldn't be load-bearing in practice, but makes the "fires at most
    once per symbol per day" rule explicit rather than an accidental
    emergent property, same as Gap's own `_GapState.fired`."""

    trading_day: date | None = None
    fired: bool = False
    touch: LevelTouchState = field(default_factory=LevelTouchState)


class FirstPullbackStrategy(Strategy):
    """See module docstring for the full GATE/MATCH/SCORE/PROPOSE anatomy."""

    name = "FirstPullback"
    trigger = every_candle(timeframe=DEFAULT_TIMEFRAME)

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._state: dict[str, _FirstPullbackState] = {}

    def _state_for(self, symbol: str) -> _FirstPullbackState:
        state = self._state.get(symbol)
        if state is None:
            state = _FirstPullbackState()
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

        if entry["touch_count_today"] != 1:
            return None  # not the first touch of the day — a different, not-yet-built strategy family
        if state.fired:
            return None  # already proposed today (belt-and-suspenders — see _FirstPullbackState docstring)
        if resolution != "rejected":
            return None  # CONQUERED — the pullback failed, no continuation signal (not an error, just a non-match)
        if anchor_price is None:
            return None  # shouldn't happen for a "rejected" resolution (only gap-throughs lack an anchor,
            # and gap-throughs are always "conquered" per level_touch_tracking.py) — defensive, honest no-op

        entered_from = _current_zone
        # For a "rejected" resolution, `current_zone == entered_from` BY
        # DEFINITION (level_touch_tracking.py's own classify rule) — using
        # `_current_zone` directly here rather than threading a third
        # return value through observe_resolution() for a value that's
        # already implied once resolution == "rejected".

        # --- MATCH ---
        trend_score_threshold = params.get("trend_score_threshold", DEFAULT_TREND_SCORE_THRESHOLD)
        direction = match_direction(entered_from, market_state.trend_score, trend_score_threshold=trend_score_threshold)
        if direction is None:
            return None

        # --- SCORE ---
        strength = rejection_strength_fraction(entry["distance_pct"], direction)
        confidence = score_confidence(market_state.trend_score, market_state.volume_regime_score, strength)

        # --- PROPOSE ---
        target_r = params.get("target_r_multiple", DEFAULT_TARGET_R_MULTIPLE)
        invalidation = anchor_price
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
                    "entered_from": entered_from,
                    "anchor_price": anchor_price,
                    "close": features.close,
                    "trend_score": market_state.trend_score,
                    "volume_regime_score": market_state.volume_regime_score,
                    "rejection_strength_pct": round(strength, 4),
                },
                "reason": (
                    f"First Pullback {direction.lower()}: first touch of {level_key}={anchor_price:.2f} "
                    f"today, rejected from {entered_from}, trend_score={market_state.trend_score:.1f}, "
                    f"volume_regime_score={market_state.volume_regime_score:.1f}"
                ),
                "basis": "closed",  # §8 — no PriceSnapshot consumer wired yet;
                # every condition above was read off a settled FeatureSet/get_snapshot() read.
            },
            setup_detected_at=features.candle_ts,  # §7 — never datetime.now()
        )
