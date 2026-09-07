"""
VWAP Strategy — seventh and last strategy from trading-intelligence-
architecture.md §8's planned v1 set (ORB, Momentum, First Pullback, Gap,
Reversal, Volume Spike all already built). Built against the real
`base_strategy.py`/`orb_strategy.py` pair (decision #99), delivered
together with `momentum_strategy.py` (decision #113).

Question this answers (§7's "question-based, not indicator-based"): "Has
intraday VWAP-side control transitioned, BEFORE an established trend
exists?" Saqib's own explicit framing, locked after an external design
review found the original two candidate designs each had a real problem
— see "Two rejected designs, and why" below.

--- Two rejected designs, and why ---

**Option A — VWAP-relationship persistence** (`market_state.
vwap_relationship_score` confirming strength + `trend_score` agreeing +
volume, no `LevelInteractionEngine` at all). Rejected: the review's own
words, "this risks being Momentum with a different gating field" —
`vwap_relationship_score` and `trend_score` are plausibly correlated
enough in practice that this would just be `momentum_strategy.py`'s
identity re-parameterized, not a genuinely different trading question.

**Option B, naive form — any two-candle close-vs-vwap cross,
independent of `LevelInteractionEngine`.** Rejected as too primitive:
raw noise (`above/below/above/below` chop) would false-trigger
repeatedly. `LevelInteractionEngine`'s Aura band exists specifically to
absorb exactly this kind of chop before it ever reaches a
"rejected"/"conquered" classification — reinventing a cruder version of
that same hysteresis inside this file would be a second, subtly
different definition of "meaningful move" drifting from the engine's
authoritative one, the same class of risk `level_touch_tracking.py`'s
own module docstring already warns against for First Pullback/Reversal.

**What was built instead — Option B, refined: a genuine `conquered`
resolution (the engine's own authoritative classification, reused
verbatim via `level_touch_tracking.observe_resolution()` — see "Reuses
First Pullback/Reversal's exact mechanism" below), gated to the trend-
NEUTRAL band rather than either "any trend state" or "the opposite of
Reversal's specific condition."**

--- Disjoint from Reversal by construction, not by downstream arbitration ---

Saqib's explicit call, made directly rather than left to "co-firing is
fine, arbitration happens downstream" (which IS the accepted answer for
Momentum/ORB — see `momentum_strategy.py`'s own module docstring): VWAP
and Reversal read the exact same underlying event (a `conquered`
resolution on `level_key`) and would otherwise fire on almost every one
of each other's candles with opposite direction conventions — far more
correlated than the Momentum/ORB case, and worth a real gate rather than
downstream cleanup. `trend_established_side(market_state.trend_score,
trend_score_threshold)` (new in `scoring_utils.py`, decision #113) must
return `None` — trend strictly in the neutral band — for VWAP to
consider firing at all; Reversal requires the exact opposite (a
non-`None` result). The two conditions partition every possible
`trend_score` reading with no gap and no overlap, using ONE shared
constant (`scoring_utils.ESTABLISHED_TREND_SCORE_THRESHOLD`) rather than
two independently-hardcoded 60/40 pairs that could silently drift apart
if either strategy's threshold is ever retuned in a later
`StrategyConfig` version — see `scoring_utils.py`'s own module docstring
and `strategy-engine-design.md` §10 (D11) for the still-open caveat that
this guarantees the DEFAULTS match, not that they stay matched forever.

--- Direction is the ACTUAL resolved side, never a mirror of trend_score ---

Structurally, not just procedurally, different from `reversal_strategy.
py`'s own `match_direction()`: Reversal bets AGAINST whichever trend is
established, so its direction comes from mirroring `trend_score` — it
never even looks at which way the conquering candle actually moved.
VWAP has no established trend to mirror against by the time it's allowed
to fire (the neutral-band gate above guarantees that), so direction here
comes from the conquest's own resolved `zone` instead: `"above"` (price
now sits on the bullish side of VWAP) proposes BUY, `"below"` proposes
SELL. This is the strongest evidence the two strategies are asking
genuinely different questions, not the same one with a relabeled gate —
one computes direction from the trend being broken, the other from the
level actually crossed.

--- Reuses First Pullback/Reversal's exact mechanism — no new touch/cross
detection logic ---

`level_touch_tracking.observe_resolution()` (decisions #107/#108) is
already fully generic over `(symbol, level_key)` — nothing about it is
First-Pullback- or Reversal-specific. This file calls it exactly the
same way `reversal_strategy.py` does: same staleness check against
`get_snapshot()`'s `last_applied_candle_ts` before ever touching the
tracker, same `LevelTouchState` per-symbol memory, same gap-through/
cold-start-unknown-origin handling. Zero new cross-detection code exists
in this file — the review's own explicit instruction ("don't duplicate
the interaction logic inside vwap_strategy.py... strategy-level VWAP
interpretation should remain semantically equivalent to the authoritative
interaction-resolution rule") is satisfied by construction, not by
carefully re-checking a parallel implementation for equivalence.

--- Cadence: fires on a genuine control TRANSITION, not every conquered
event, not a blind once-per-day cap ---

The Aura band already absorbs most raw noise before a `conquered`
resolution is even produced (module docstring above), and
`level_touch_tracking.py`'s own classification rule (`"rejected" if
current_zone == entered_from else "conquered"`) means two CONSECUTIVE
`conquered` resolutions in the engine's own unbroken stream always
alternate zones by construction — there is no direct-repeat case to
guard against there.

There IS a real, reachable repeat case, just a more specific one than
"raw chop": VWAP is a PARTIAL observer of that conquest stream — it only
fires on the conquests that also land in the neutral-trend band, so it
never sees the conquests that happen while `trend_score` is established
(Reversal's window). If price flips zones once or twice WHILE trend is
established, then trend returns to neutral with the zone back where it
was the last time VWAP itself fired, that's a legitimate same-zone
repeat from VWAP's own vantage point even though the underlying engine
never produced two consecutive identical resolutions. `_VWAPState.
last_fired_zone` catches exactly this: a new qualifying `conquered`
resolution into the SAME zone VWAP last spoke for is treated as "nothing
new since I last had something to say," not fired again; only a
resolution into a zone DIFFERENT from VWAP's own last fire — a genuine
change since VWAP was last heard from — fires. Costs a single extra
field, resets on trading-day rollover same as every other per-symbol
state in this codebase.

--- SCORE: `distance_pct`, not `touch_count_today` ---

`get_snapshot()`'s `distance_pct` field, on a resolved (non-`inside_
aura`) zone, is computed from `self._latest_close`/`self._latest_level_
value` — internal engine state tied to the candle actually processed,
NOT `seconds_in_zone`'s wall-clock `datetime.now()` (confirmed by reading
`level_interaction_engine.py`'s `get_snapshot()` directly, same
diligence `reversal_strategy.py`'s own module docstring already applied
to rule `seconds_in_zone` out for §7's backtest-safety invariant) — so
it's safe to use as a genuine, candle-derived "how far did the close
carry past VWAP" transition-strength measure. `touch_count_today` is
logged in `evidence.conditions` for future calibration but deliberately
NOT weighted into SCORE: unlike Reversal (where more prior touches before
a break is straightforwardly stronger evidence — "third time's the
charm"), a high touch count before a VWAP conquest could just as
plausibly mean a choppy, low-conviction session as it could mean a
well-tested level finally giving way — the sign of that relationship
isn't obvious the way it is for Reversal's own MATCH condition
(established trend, conquered against it), and guessing at a direction
for that weight isn't worth doing before real outcome data exists to
check it against. Flagged here rather than silently omitted.

--- Invalidation: same anchor_price / live-level-value fallback as Reversal ---

`anchor_price` (the level's value captured at the start of the resolving
touch) when available; falls back to `features.features.get(level_key)`
(the level's current live value) for the gap-through case, which never
has a captured anchor to read — identical reasoning and identical code
shape to `reversal_strategy.py`'s own PROPOSE section, applied to VWAP's
own direction convention.

--- `level_key` stays a configurable param, defaulting to "vwap" ---

Same "not its own strategy class" precedent §3/First Pullback/Reversal
already establish for SMA 9/20 and the touch-tracking mechanism itself —
this file's specific MATCH logic (neutral-trend gate + resolved-zone
direction) is bespoke to the VWAP question, not a generic "any level"
module the way `level_touch_tracking.py` itself is, but there's no
reason to hardcode the key when the params-driven convention is already
established and costs nothing extra.

--- `allows_waiting` stays `False` for v1 ---

Same reasoning as every other v1 strategy — D5 is explicitly deferred.
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
)
from app.trading_intelligence.level_interaction_engine import get_level_interaction_engine

# --- v1 defaults — all overridable via StrategyConfig.params (§3); a
# threshold change is a new StrategyConfig version, never an edit here. ---

DEFAULT_TIMEFRAME = "1m"  # touch/resolution needs candle-close granularity — not configurable per-instance
DEFAULT_LEVEL_KEY = "vwap"  # module docstring — params-driven, not a separate strategy class
DEFAULT_TREND_SCORE_THRESHOLD = ESTABLISHED_TREND_SCORE_THRESHOLD  # shared with reversal_strategy.py — decision #113, see module docstring
DEFAULT_TARGET_R_MULTIPLE = 2.0
DEFAULT_EXPECTED_HORIZON_MINUTES = 30  # v1 guess, unvalidated — a session-bias event, longer than Volume
# Spike's quick blip, shorter than Gap's implied session-long continuation

# SCORE blend weights (sum to 1.0) — v1 guess, explicitly NOT validated
# against real score distributions yet, same caveat every other
# strategy/scoring module in this codebase states for its own
# calibration constants. touch_count_today deliberately excluded — see
# module docstring's "SCORE: distance_pct, not touch_count_today".
_W_DISTANCE = 0.55
_W_VOLUME = 0.45

# Cap for distance_component's normalization below — a conquest closing
# this many percent beyond the level saturates SCORE's distance
# component at 100. v1 guess, unvalidated — the Aura band widths already
# in use elsewhere in this codebase (e.g. 0.2% in test fixtures) put a
# typical conquest's own distance in a much smaller range than this cap.
DISTANCE_PCT_CAP = 0.5


def default_params() -> dict:
    """v1 StrategyConfig.params — see module docstring for the
    reasoning behind each default."""
    return {
        "level_key": DEFAULT_LEVEL_KEY,
        "trend_score_threshold": DEFAULT_TREND_SCORE_THRESHOLD,
        "target_r_multiple": DEFAULT_TARGET_R_MULTIPLE,
        "expected_horizon_minutes": DEFAULT_EXPECTED_HORIZON_MINUTES,
    }


def match_direction(
    current_zone: str,
    trend_score: float,
    *,
    trend_score_threshold: float,
) -> Literal["BUY", "SELL"] | None:
    """MATCH stage direction confirmation, pure. Deliberately the
    structural OPPOSITE of `reversal_strategy.py`'s own
    `match_direction()` — see module docstring's "Direction is the
    ACTUAL resolved side" section. Direction comes from `current_zone`
    itself, never a mirror of `trend_score`; `trend_score` here only
    gates whether VWAP is even allowed to consider firing (neutral band
    only — Reversal's territory otherwise)."""
    if trend_established_side(trend_score, trend_score_threshold) is not None:
        return None  # an established trend already exists — Reversal's condition, not VWAP's (module docstring)
    if current_zone == "above":
        return "BUY"  # control transitioned bullish
    if current_zone == "below":
        return "SELL"  # control transitioned bearish
    return None  # defensive — observe_resolution() should never hand back anything else on a "conquered" resolution


def score_confidence(
    volume_regime_score: float,
    distance_pct: float | None,
) -> float:
    """SCORE stage, pure — how strongly the pattern matches, given MATCH
    already confirmed a genuine control transition. `distance_pct` is
    `None`-safe (treated as a non-contributing 0.0) — see module
    docstring for why it's still safe/backtest-consistent to read at
    all."""
    volume_component = volume_regime_score  # already 0-100 (Market State's own scale)
    distance_component = clamp(abs(distance_pct or 0.0) / DISTANCE_PCT_CAP * 100.0)

    confidence = _W_DISTANCE * distance_component + _W_VOLUME * volume_component
    return round(clamp(confidence), 2)


def default_config(active_from: datetime, version: str = "vwap_v1") -> StrategyConfig:
    """Seed/testing convenience — constructs the v1 StrategyConfig this
    module was designed against."""
    return StrategyConfig(
        strategy_name="VWAP",
        version=version,
        params=default_params(),
        gate_conditions={"session": "regular"},
        allows_waiting=False,
        active_from=active_from,
        active_to=None,
        rationale=(
            "v1 VWAP: level_key (default vwap) resolved CONQUERED via the "
            "shared level_touch_tracking.py mechanism (decisions #107/#108), "
            "gated to trend_score's NEUTRAL band (scoring_utils."
            "ESTABLISHED_TREND_SCORE_THRESHOLD, shared with reversal_strategy.py "
            "— decision #113) so VWAP and Reversal partition every trend_score "
            "reading with no gap and no overlap. Direction is the actual "
            "resolved zone, never a mirror of trend_score. Fires only on a "
            "genuine control TRANSITION (last_fired_zone dedup), not every "
            "conquered event. See vwap_strategy.py module docstring for the "
            "two rejected designs and why this one was chosen instead."
        ),
    )


@dataclass
class _VWAPState:
    """Private per-symbol memory — mirrors `_ReversalState`'s shape
    (`reversal_strategy.py`), with `fired: bool` replaced by
    `last_fired_zone: str | None` (module docstring's "Cadence" section
    — a control-transition strategy needs to remember WHICH side it last
    fired for, not just whether it has fired today)."""

    trading_day: date | None = None
    last_fired_zone: str | None = None
    touch: LevelTouchState = field(default_factory=LevelTouchState)


class VWAPStrategy(Strategy):
    """See module docstring for the full GATE/MATCH/SCORE/PROPOSE anatomy."""

    name = "VWAP"
    trigger = every_candle(timeframe=DEFAULT_TIMEFRAME)

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._state: dict[str, _VWAPState] = {}

    def _state_for(self, symbol: str) -> _VWAPState:
        state = self._state.get(symbol)
        if state is None:
            state = _VWAPState()
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
        level_key = params.get("level_key", DEFAULT_LEVEL_KEY)

        # --- GATE ---
        if features.timeframe != timeframe:
            return None  # defensive timeframe scope — see reversal_strategy.py precedent

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
            state.last_fired_zone = None

        resolution, current_zone, anchor_price = observe_resolution(state.touch, entry)
        if resolution is None:
            return None  # nothing resolved this candle — still forming, steady, or unclassifiable cold start
        if resolution != "conquered":
            return None  # REJECTED — no control transition, nothing for VWAP to say (module docstring)
        if current_zone == state.last_fired_zone:
            return None  # re-confirmation of the same side, not a transition — module docstring "Cadence"

        # --- MATCH ---
        trend_score_threshold = params.get("trend_score_threshold", DEFAULT_TREND_SCORE_THRESHOLD)
        direction = match_direction(current_zone, market_state.trend_score, trend_score_threshold=trend_score_threshold)
        if direction is None:
            return None

        # --- SCORE ---
        distance_pct = entry.get("distance_pct")
        confidence = score_confidence(market_state.volume_regime_score, distance_pct)

        # --- PROPOSE ---
        target_r = params.get("target_r_multiple", DEFAULT_TARGET_R_MULTIPLE)
        if anchor_price is not None:
            invalidation = anchor_price
        else:
            # Gap-through — no holding entry ever existed to capture an
            # anchor from (module docstring, same as reversal_strategy.py).
            invalidation = features.features.get(level_key)
            if invalidation is None:
                return None  # honest absence — can't propose without any invalidation reference at all
        risk = abs(features.close - invalidation)
        if risk <= 0:
            return None  # honest — degenerate invalidation reference, nothing meaningful to propose against
        target = features.close + target_r * risk if direction == "BUY" else features.close - target_r * risk

        state.last_fired_zone = current_zone

        return Opportunity(
            strategy=self.name,
            version=self.config.version,
            direction=direction,
            confidence=confidence,
            structural_invalidation=invalidation,
            structural_target=target,
            expected_horizon_minutes=params.get("expected_horizon_minutes", DEFAULT_EXPECTED_HORIZON_MINUTES),
            evidence={
                # Literal MATCH-stage values only — never a wholesale
                # FeatureSet dump (strategy-engine-design.md §4/§11 boundary).
                "conditions": {
                    "level_key": level_key,
                    "invalidation_source": "anchor_price" if anchor_price is not None else "live_level_value",
                    "close": features.close,
                    "trend_score": market_state.trend_score,
                    "volume_regime_score": market_state.volume_regime_score,
                    "distance_pct": distance_pct,
                    "touch_count_today": entry["touch_count_today"],  # logged, not scored — module docstring "SCORE"
                },
                "reason": (
                    f"VWAP {direction.lower()}: {level_key} control transitioned to \"{current_zone}\" "
                    f"with trend_score={market_state.trend_score:.1f} (neutral band — no established trend to "
                    f"conflict with Reversal), volume_regime_score={market_state.volume_regime_score:.1f}"
                ),
                "basis": "closed",  # §8 — no PriceSnapshot consumer wired yet;
                # every condition above was read off a settled FeatureSet/get_snapshot() read.
            },
            setup_detected_at=features.candle_ts,  # §7 — never datetime.now()
        )
