"""
Volume Spike Strategy — is this candle's volume burst large enough,
relative to this symbol's own recent bars, to treat as a real directional
signal rather than noise? Third strategy built against the real
`base_strategy.py`/`orb_strategy.py` pair (decision #99); delivered
together with `gap_strategy.py` (decision #104/#105). Planned strategy
set: trading-intelligence-architecture.md §8 (ORB, Momentum, First
Pullback, VWAP, Gap, Reversal, Volume Spike).

Question this answers (§7's "question-based, not indicator-based"): "Did
this candle just print unusually heavy volume, and if so, which way did
the market actually move on it?" A setup-detector: it *consumes*
Trend/Volume-regime (Market State Engine, decision #93), it doesn't
rebuild them. The one thing genuinely this strategy's own job — a
per-candle spike test against this symbol's own recent volume — is
something no other engine tracks (see "Why this needed new private
state" below), same category of "belongs to this strategy alone" as
ORB's opening range.

Boundary discipline (system-design.md §4.5): "Feature Engine measures.
Market State interprets. Strategy decides." `market_state.trend_score`/
`volume_regime_score` are read directly here, never re-derived from raw
slope/RVOL — Market State already did that interpretation.

--- Correction against base_strategy.py's own illustrative trigger name ---

`base_strategy.py`'s `on_event()` factory docstring names `"VolumeSpike"`
as its own example (`on_event(event_name)  # on_event("VolumeSpike")`) —
naturally the first thing checked before writing this file (`grep -rn
"VolumeSpike" backend/app/` returned nothing outside `strategy_engine/`
itself). No such event exists anywhere on the bus, and no engine
publishes one — it was always illustrative naming for the factory
function, not a promise that a `VolumeSpikeDetected` event type exists
in `schemas/events/`. Using `on_event("VolumeSpike")` here would silently
imply a publisher that doesn't exist — the same class of gap decision
#99 already corrected for ORB's trigger (`after_time` → `every_candle`,
because the sketch's trigger couldn't actually see what it needed to
see). Corrected the same way: `trigger = every_candle(timeframe="1m")` —
evaluate() runs on every 1m candle, and this file's own GATE/MATCH does
the actual spike-detection math no other engine provides. If a future
`VolumeSpikeDetected` event type is ever built upstream (Feature Engine
or a new dedicated detector), this strategy could migrate its trigger to
`on_event(...)` without changing any of the GATE/MATCH/SCORE/PROPOSE
logic below — the trigger only ever answered "when does evaluate() get
called," never "what does the pattern test."

--- Why this needed new private state, and why that's still in-bounds ---

Feature Engine publishes `volume` (this candle's own, decision #99) and
`rvol` (today's cumulative session volume vs. a multi-day historical
average, time-of-day-normalized — `indicators/rvol.py`, decision #71).
Neither answers this strategy's actual question. `rvol` measures whether
TODAY, in aggregate, is a busier day than normal for this symbol — a
day-level statistic, already exactly what Market State's own
`volume_regime_score` interprets (decision #93) and this file reads
directly rather than re-deriving. A SPIKE is a different, narrower claim:
did THIS ONE CANDLE print far more volume than this symbol's own last
handful of candles, a single-bar anomaly `rvol`'s own smoothing would
dilute into invisibility (`indicators/rvol.py`'s own docstring: an
intraday per-minute volume PROFILE "needs much more history than a
handful of daily totals to build" and is explicitly out of that module's
scope). No engine anywhere in this codebase computes a rolling per-candle
volume baseline — so, same precedent as `orb_strategy.py`'s own opening-
range accumulator (a value ONLY that strategy needed, tracked entirely in
its own private per-symbol memory, Saqib's explicit call there), this
file keeps a small rolling window of each symbol's last `lookback_bars`
1m volumes purely for its own spike-ratio test. Never published to
`FeatureSet` or anywhere else — no other engine or strategy can see it,
identical visibility rule to ORB's own range state.

**v1 simple moving average, not spike-robust — flagged the same way
`rvol` itself is flagged as "a proxy... not scanner-grade precision."**
Past spikes remain in the rolling window and pull the baseline up for
later candles, rather than being excluded as outliers. A real, deliberate
scope choice for v1, not an oversight: outlier-robust baselining (a
trimmed mean, a median, or decaying weights) is a real, separate
refinement if live data shows plain-mean contamination is a problem in
practice — not assumed as a defect before any real data exists to
justify the extra complexity.

--- Honest warm-up, same shape as ORB's `or_formed` gate ---

`orb_strategy.py` won't test a breakout until `or_minutes` of formation
candles were actually observed (`_ORBState.candles_seen`), rather than
fabricating a range from a single late candle. This file applies the
identical discipline to its own baseline: `len(state.volumes) <
lookback_bars` returns `None` — still building an honest baseline,
nothing yet to compare against — even though a numeric (but
statistically meaningless, 1-or-2-sample) "average" could technically be
computed earlier. Each candle observed during warm-up is still pushed
into the window, so the baseline is exactly `lookback_bars` samples deep
the moment testing starts, not padded with fewer.

**The current candle's own volume is never compared against a baseline
that already includes itself.** `baseline = mean(state.volumes)` is
computed from the PRIOR `lookback_bars` candles, and only pushed into the
window (evicting the oldest, `deque(maxlen=lookback_bars)`) after that
read — comparing a candle against a baseline that already contains its
own value would mechanically dampen every ratio and make the threshold's
real-world meaning depend on `lookback_bars` in a way it shouldn't.

--- Direction comes from the spike candle's own bar, not a second source ---

MATCH's direction test (`candle_close > candle_open` for BUY, `<` for
SELL) reads the SAME 1m candle whose volume just qualified as a spike —
never a separately-sourced price pair. This gives Volume Spike the exact
same immunity `orb_strategy.py`'s own docstring identifies for ORB (and
this file's sibling `gap_strategy.py` shares for its own reasons): the
discarded momentum_/vwap_strategy.py review's PROPOSE-stage contradiction
bug (direction from one source, target math from another, capable of
disagreeing) has no foothold here, because PROPOSE's `structural_
invalidation` below (`features.low`/`features.high`) is that same
candle's own wick — not a second, independently-derived quantity.

--- Cooldown, not a fired-once-per-day flag ---

`orb_strategy.py`'s opening range is a single, fixed-for-the-day
reference — a fired-once-per-direction rule makes sense against a level
that doesn't move. `gap_strategy.py`'s gap is likewise one fixed event
per day, so it fires at most once, period. A volume spike is neither: a
genuinely busy session can produce several independent, legitimate spike
candles hours apart, and this strategy should be able to catch each one
— a once-per-day cap would silently discard real signal a slow-moving
level-based strategy doesn't have to worry about losing. What it does
need is a floor against re-firing on the immediate next candle or two of
the SAME still-elevated move (the spike candle's own volume, now sitting
inside `state.volumes`, doesn't retroactively change, but a strongly
trending move often keeps printing above-average volume for several
bars in a row) — `_VolumeSpikeState.last_fired_ts` plus
`cooldown_minutes` (a single per-symbol timer, not per-direction like
ORB's `fired_directions` set) enforces that floor. Single-timer rather
than per-direction: two opposite-direction fires only seconds apart on
the SAME underlying volatile stretch are far more likely to be noise
chasing one event than a genuine, independent reversal — unlike ORB's
fixed range, where a real opposite-side breakout is a meaningfully
distinct, second event worth its own signal.

--- Session scope ---

`gate_conditions={"session": "regular"}` (declared in `default_config()`
below, enforced solely by `StrategyScheduler`/`gate_conditions.py` —
decisions #117/#118, D16/#119), same reasoning as `gap_strategy.py`:
pre-market/after-hours volume is thin by construction (decision #94's
own IEX-coverage findings for the live tape aside, even the
historical/backfill volume any of these sessions print is a fraction of
regular-hours turnover), so a "3x the recent baseline" test means
something different, and less reliably, outside regular hours. Kept
simple for v1 rather than inventing a session-specific threshold scheme.
This file itself no longer checks session directly; see D16/decision
#119 for why the prior inline `MarketClock.is_regular_session()` call
here was removed as redundant.

--- Absolute volume floor and body/displacement check (decision #111) ---

The design review's §3 found two real, concrete MATCH-stage gaps, both
closed here.

**No absolute floor.** `volume_ratio` is purely relative to this
symbol's own trailing baseline — on an illiquid symbol, or during a
quiet stretch, that baseline can shrink small enough that a genuinely
tiny order looks like a "3x spike." `volume_regime_score` doesn't
protect against this either, since it's a day-level, per-symbol-
normalized read, not an absolute-liquidity check. `min_absolute_volume`
(v1 default 500 shares, unvalidated) requires real, absolute size before
the ratio test even runs.

**No displacement/body check.** MATCH previously only checked
`candle_close != candle_open` — a candle with huge volume and a
razor-thin body (open and close a cent apart) passed as a directional
signal, even though a huge-volume, tiny-body bar is closer to a
classic indecision/climax signature than a confident directional print.
`min_body_ratio` (v1 default 0.3, unvalidated) requires the candle's net
body to span at least that fraction of its own high-low range —
computable from the same OHLC already on this candle, no new data
needed. Zero-range risk doesn't apply: `candle_close != candle_open`
(already checked first) guarantees `high > low` for any valid bar
(`high >= max(open, close) > min(open, close) >= low`), so the division
is always safe by construction.

Deliberately NOT addressed here, both flagged by the review as needing
real outcome data rather than a guessed fix: whether a high-volume
directional candle actually means continuation, as opposed to
exhaustion/climax (trend_score can't distinguish "healthy early trend"
from "already-extended trend"); and whether sizing the stop off the same
spike candle's own wick can produce an impractically wide risk/target,
since spike candles are often unusually wide-range by construction. Both
are real, open questions this change does not attempt to resolve.

--- `expected_horizon_minutes` (decision #111) ---

The design review's §5 found this strategy's implicit time horizon —
almost certainly much shorter than Gap's, since a spike's whole premise
is fast, near-term follow-through — was being lost the moment
`evaluate()` returned. `DEFAULT_EXPECTED_HORIZON_MINUTES = 15` is a v1
guess (a handful of 1m/5m candles), unvalidated against real outcome
data. See `base_strategy.py`'s `Opportunity` docstring.

--- MarketStateEngine race: not a caveat here, unlike ORB/Momentum/VWAP ---

Same note as `gap_strategy.py`'s own module docstring: decision #103
(built after ORB/Momentum/VWAP each had to flag it) closed
`MarketStateEngine`'s shared-slot-per-symbol race. This file's default
timeframe is `"1m"`, and `_compute()` now always reads the `(symbol,
"1m")` slot specifically — `market_state.trend_score`/
`volume_regime_score` read here are guaranteed to reflect the exact 1m
close this strategy is evaluating, not merely probabilistically likely
to.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
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

DEFAULT_TIMEFRAME = "1m"  # a per-candle spike test is meaningless at any coarser aggregation — not configurable per-instance
DEFAULT_LOOKBACK_BARS = 20  # v1 guess, unvalidated — how many prior 1m candles form the rolling baseline
DEFAULT_SPIKE_RATIO_THRESHOLD = 3.0  # v1 guess, unvalidated — this candle's volume vs. the rolling baseline
DEFAULT_TREND_SCORE_THRESHOLD = 60.0  # same convention/value as orb_strategy.py's DEFAULT_TREND_SCORE_THRESHOLD
DEFAULT_VOLUME_REGIME_THRESHOLD = 45.0  # same participation floor as orb_strategy.py (~rvol 1.35)
DEFAULT_TARGET_R_MULTIPLE = 2.0
DEFAULT_COOLDOWN_MINUTES = 5  # v1 guess, unvalidated — see module docstring's "Cooldown" section
DEFAULT_MIN_ABSOLUTE_VOLUME = 500  # v1 guess, unvalidated — decision #111, design review §3.
# Shares in this one candle, independent of the ratio — protects against a
# thin-baseline/illiquid-symbol false positive the ratio alone can't catch.
DEFAULT_MIN_BODY_RATIO = 0.3  # v1 guess, unvalidated — decision #111, design review §3.
# Net body as a fraction of the candle's own high-low range — a huge-volume,
# razor-thin-body candle is closer to indecision than conviction.
DEFAULT_EXPECTED_HORIZON_MINUTES = 15  # v1 guess, unvalidated — decision #111. A fast,
# near-term follow-through thesis, not modeled against real outcome data yet. See
# base_strategy.py's Opportunity docstring.

# SCORE blend weights (sum to 1.0) — v1 guess, explicitly NOT validated
# against real score distributions yet, same caveat orb_strategy.py states
# for its own calibration constants. Spike weighted higher than Trend/
# Volume here (unlike orb_strategy.py's even 0.35/0.30/0.35 split) because
# the spike itself is this strategy's whole reason for existing, not a
# shared component every strategy weighs symmetrically.
_W_TREND = 0.30
_W_VOLUME = 0.25
_W_SPIKE = 0.45

# Cap for spike_strength_fraction's normalization below — a candle
# printing this many multiples OVER the threshold itself saturates
# SCORE's spike component at 100. v1 guess, unvalidated.
SPIKE_STRENGTH_CAP = 2.0


def default_params() -> dict:
    """v1 StrategyConfig.params — see module docstring for the
    reasoning behind each default."""
    return {
        "lookback_bars": DEFAULT_LOOKBACK_BARS,
        "spike_ratio_threshold": DEFAULT_SPIKE_RATIO_THRESHOLD,
        "trend_score_threshold": DEFAULT_TREND_SCORE_THRESHOLD,
        "volume_regime_threshold": DEFAULT_VOLUME_REGIME_THRESHOLD,
        "target_r_multiple": DEFAULT_TARGET_R_MULTIPLE,
        "cooldown_minutes": DEFAULT_COOLDOWN_MINUTES,
        "min_absolute_volume": DEFAULT_MIN_ABSOLUTE_VOLUME,
        "min_body_ratio": DEFAULT_MIN_BODY_RATIO,
        "expected_horizon_minutes": DEFAULT_EXPECTED_HORIZON_MINUTES,
    }


def match_direction(
    volume_ratio: float,
    candle_open: float,
    candle_high: float,
    candle_low: float,
    candle_close: float,
    candle_volume: int,
    trend_score: float,
    volume_regime_score: float,
    *,
    spike_ratio_threshold: float,
    trend_score_threshold: float,
    volume_regime_threshold: float,
    min_absolute_volume: int,
    min_body_ratio: float,
) -> Literal["BUY", "SELL"] | None:
    """MATCH stage, pure. Direction-symmetric on purpose, same shape
    orb_strategy.py's/gap_strategy.py's match_direction() already
    established: SELL is BUY's mirror image around each dimension's
    neutral 50, not a separately hand-tuned rule set. Returns None on
    "not enough absolute volume to trust the ratio", "not actually a
    spike", "no participation", "a doji with no net direction", "too
    little real displacement for the volume to represent conviction", or
    any confirming condition failing — never a partial/weak signal; that
    nuance belongs to SCORE, not MATCH.

    `trend_score_threshold` must be > 50.0 — same guard, same reasoning,
    as orb_strategy.py's/gap_strategy.py's own match_direction() (decision
    #99's fix, applied via the shared
    `scoring_utils.validate_mirror_threshold()`, decision #107/#111,
    rather than risking a third rediscovery of the identical bug): SELL
    mirrors the threshold as `100 - threshold`, so a threshold at or
    below 50 flips onto the wrong side of neutral."""
    validate_mirror_threshold(trend_score_threshold)

    if candle_volume < min_absolute_volume:
        return None  # too little real volume in absolute terms for the ratio to mean anything — module docstring

    if volume_ratio < spike_ratio_threshold:
        return None  # not a spike by this strategy's own definition

    if volume_regime_score < volume_regime_threshold:
        return None  # participation floor — direction-agnostic, checked once

    if candle_close == candle_open:
        return None  # doji — a spike with no net direction of its own, nothing to match

    # Safe by construction: candle_close != candle_open (just checked)
    # guarantees high > low for any valid bar (high >= max(open, close) >
    # min(open, close) >= low) — module docstring.
    body_ratio = abs(candle_close - candle_open) / (candle_high - candle_low)
    if body_ratio < min_body_ratio:
        return None  # large volume, but too little of the bar's own range was net displacement — module docstring

    if candle_close > candle_open:
        if trend_score >= trend_score_threshold:
            return "BUY"
        return None
    if trend_score <= (100.0 - trend_score_threshold):
        return "SELL"
    return None


def score_confidence(
    trend_score: float,
    volume_regime_score: float,
    spike_strength: float,
) -> float:
    """SCORE stage, pure — how strongly the pattern matches, given MATCH
    already confirmed direction and that this candle qualifies as a
    spike. `spike_strength` is the pre-normalized, pre-clamped excess
    from `spike_strength_fraction()` below — kept as a separate pure
    function so it's independently testable against just the raw ratio,
    no score inputs involved."""
    trend_component = trend_magnitude(trend_score)  # 0-100, direction-agnostic magnitude
    volume_component = volume_regime_score  # already 0-100 (Market State's own scale)
    spike_component = clamp(spike_strength / SPIKE_STRENGTH_CAP * 100.0)

    confidence = (
        _W_TREND * trend_component
        + _W_VOLUME * volume_component
        + _W_SPIKE * spike_component
    )
    return round(clamp(confidence), 2)


def spike_strength_fraction(volume_ratio: float, spike_ratio_threshold: float) -> float:
    """How far this candle's volume sits ABOVE the spike threshold
    itself, as a fraction of that threshold — a candle at 2x the
    threshold multiple is much stronger evidence than one that barely
    cleared it. Pure function of the ratio and threshold — no score
    inputs — so this is testable independent of score_confidence()'s
    blend weights. `spike_ratio_threshold <= 0.0` returns 0.0 rather than
    dividing by zero — an honest "no signal from this component," not an
    error; a StrategyConfig with a non-positive threshold is a
    misconfiguration this function chooses not to crash on."""
    if spike_ratio_threshold <= 0.0:
        return 0.0
    return max(0.0, (volume_ratio - spike_ratio_threshold) / spike_ratio_threshold)


def default_config(active_from, version: str = "volume_spike_v1") -> StrategyConfig:
    """Seed/testing convenience — constructs the v1 StrategyConfig this
    module was designed against. `active_from` is caller-supplied (real
    wall-clock time at the point a human promotes this version — config
    activation bookkeeping, not part of evaluate()'s live/backtest-
    identical timestamp derivation, so this is exempt from §7's "never
    datetime.now() inside evaluate()" rule)."""
    return StrategyConfig(
        strategy_name="Volume Spike",
        version=version,
        params=default_params(),
        gate_conditions={"session": "regular"},  # enforced solely by
        # StrategyScheduler via gate_conditions.py (decisions #117/#118) —
        # this file no longer checks session itself (D16, decision #119).
        allows_waiting=False,
        active_from=active_from,
        active_to=None,
        rationale=(
            "v1 Volume Spike: a 1m candle printing >= 3x its own symbol's "
            "trailing 20-bar average volume (min 500 shares absolute, min 30% "
            "body-to-range ratio — decision #111 additions), confirmed by that "
            "candle's own bar direction and Market State's trend_score/"
            "volume_regime_score. Rolling baseline and thresholds are v1 "
            "defaults, unvalidated against real score distributions — see "
            "volume_spike_strategy.py module docstring."
        ),
    )


@dataclass
class _VolumeSpikeState:
    """Private per-symbol memory — see module docstring's "Why this
    needed new private state" and "Cooldown, not a fired-once-per-day
    flag" sections. `volumes` is bounded (`deque(maxlen=lookback_bars)`)
    so the oldest sample is evicted automatically as new ones arrive —
    no separate trimming logic needed."""

    trading_day: date
    volumes: deque[float]
    last_fired_ts: datetime | None = None


class VolumeSpikeStrategy(Strategy):
    """See module docstring for the full GATE/MATCH/SCORE/PROPOSE anatomy."""

    name = "Volume Spike"
    trigger = every_candle(timeframe=DEFAULT_TIMEFRAME)

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._state: dict[str, _VolumeSpikeState] = {}

    def _state_for(self, symbol: str, trading_day: date, lookback_bars: int) -> _VolumeSpikeState:
        state = self._state.get(symbol)
        if state is None or state.trading_day != trading_day:
            # First time seeing this symbol, or a new day — reset. A new
            # day gets a fresh baseline (yesterday's power-hour volume
            # shouldn't set today's pre-open expectations) and a fresh
            # cooldown, same "track the trading_day one has, compare,
            # reset on change" shape orb_strategy.py's own _state_for uses.
            state = _VolumeSpikeState(trading_day=trading_day, volumes=deque(maxlen=lookback_bars))
            self._state[symbol] = state
        return state

    async def evaluate(
        self,
        symbol: str,
        market_state: MarketState,
        features: FeatureSet,
        context: ContextChanged,  # noqa: ARG002 — not used by v1 MATCH logic;
        # accepted for interface conformance, same as orb_strategy.py/
        # gap_strategy.py. Available for a future gate_conditions extension
        # without touching this signature again.
    ) -> Opportunity | None:
        params = self.config.params
        timeframe = DEFAULT_TIMEFRAME

        # --- GATE ---
        if features.timeframe != timeframe:
            return None  # defensive timeframe scope — see module docstring
        clock = get_market_clock()
        if features.open is None or features.high is None or features.low is None or features.volume is None:
            return None  # honest absence — pre-decision #99 FeatureSet, or an aggregated
            # timeframe's FeatureSet slipping through despite the check above; either way,
            # can't test a per-candle spike or derive a structural stop without real OHLCV.

        lookback_bars = params.get("lookback_bars", DEFAULT_LOOKBACK_BARS)
        trading_day = clock.trading_day(features.candle_ts)
        state = self._state_for(symbol, trading_day, lookback_bars)

        if len(state.volumes) < lookback_bars:
            # Still building an honest baseline — module docstring's
            # "Honest warm-up" section. Push this candle in for a future
            # comparison; there's nothing honest to compare IT against yet.
            state.volumes.append(features.volume)
            return None

        baseline = sum(state.volumes) / len(state.volumes)
        # This candle joins the window for the NEXT candle's baseline —
        # never its own. See module docstring: comparing a candle against
        # a baseline that already contains itself would mechanically
        # dampen every ratio.
        state.volumes.append(features.volume)

        if baseline <= 0:
            return None  # honest — can't compute a ratio against a zero baseline
        volume_ratio = features.volume / baseline

        cooldown_minutes = params.get("cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)
        if state.last_fired_ts is not None and features.candle_ts < state.last_fired_ts + timedelta(minutes=cooldown_minutes):
            return None  # cooldown — module docstring "Cooldown, not a fired-once-per-day flag"

        # --- MATCH ---
        spike_ratio_threshold = params.get("spike_ratio_threshold", DEFAULT_SPIKE_RATIO_THRESHOLD)
        direction = match_direction(
            volume_ratio, features.open, features.high, features.low, features.close, features.volume,
            market_state.trend_score, market_state.volume_regime_score,
            spike_ratio_threshold=spike_ratio_threshold,
            trend_score_threshold=params.get("trend_score_threshold", DEFAULT_TREND_SCORE_THRESHOLD),
            volume_regime_threshold=params.get("volume_regime_threshold", DEFAULT_VOLUME_REGIME_THRESHOLD),
            min_absolute_volume=params.get("min_absolute_volume", DEFAULT_MIN_ABSOLUTE_VOLUME),
            min_body_ratio=params.get("min_body_ratio", DEFAULT_MIN_BODY_RATIO),
        )
        if direction is None:
            return None

        # --- SCORE ---
        strength = spike_strength_fraction(volume_ratio, spike_ratio_threshold)
        confidence = score_confidence(market_state.trend_score, market_state.volume_regime_score, strength)

        # --- PROPOSE ---
        target_r = params.get("target_r_multiple", DEFAULT_TARGET_R_MULTIPLE)
        invalidation = features.low if direction == "BUY" else features.high
        # the thesis IS this specific candle's own move — a reversal back through
        # its own wick falsifies the pattern itself, same "structural, not
        # arbitrary" reasoning orb_strategy.py's own invalidation comment uses.
        risk = abs(features.close - invalidation)
        target = features.close + target_r * risk if direction == "BUY" else features.close - target_r * risk

        state.last_fired_ts = features.candle_ts

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
                    "volume": features.volume,
                    "baseline_volume": round(baseline, 2),
                    "volume_ratio": round(volume_ratio, 4),
                    "open": features.open,
                    "close": features.close,
                    "trend_score": market_state.trend_score,
                    "volume_regime_score": market_state.volume_regime_score,
                },
                "reason": (
                    f"Volume Spike {direction.lower()}: {volume_ratio:.2f}x the trailing "
                    f"{lookback_bars}-bar average volume ({features.volume:,} vs. "
                    f"{baseline:,.0f}), trend_score={market_state.trend_score:.1f}, "
                    f"volume_regime_score={market_state.volume_regime_score:.1f}"
                ),
                "basis": "closed",  # §8 — no PriceSnapshot consumer wired yet;
                # every condition above was read off a settled FeatureSet.
            },
            setup_detected_at=features.candle_ts,  # §7 — never datetime.now()
        )
