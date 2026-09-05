"""
Momentum Strategy — a 5m moving-average crossover, confirmed by Market
State's Trend / Acceleration / Volume-regime dimensions rather than any
raw indicator Momentum recomputes itself.

Direction lock: strategy-engine-design.md §1 (GATE/MATCH/SCORE/PROPOSE
anatomy), §3 (StrategyConfig — family vs. versioned config), §4
(Opportunity/evidence schema), decisions #87-89. Planned strategy set:
trading-intelligence-architecture.md §8.

Question this answers (§7 "Agent Design Philosophy — Question-Based, Not
Indicator-Based"): "Is a trending move accelerating with volume behind
it, worth joining now?" A setup-detector, not a state-builder — it
*consumes* Trend/Acceleration/Volume-regime (built by Market State
Engine, decision #93), it doesn't rebuild them.

Boundary discipline (system-design.md §4.5, §4 of the companion doc):
"Feature Engine measures. Market State interprets. Strategy decides."
Concretely here: `market_state.trend_score` / `acceleration_score` /
`volume_regime_score` are read directly, never re-derived from raw
`sma_20_slope_angle`/`rvol` — Market State already did that
interpretation. The one thing genuinely Strategy's own job is the MA
crossover itself: no Market State dimension is a crossover of two
arbitrary configurable periods, so that comparison happens here, same
as the ORB example reads `features.opening_range_high` directly.

Config-driven per §3: `ma_type` ("sma"|"ema"), `fast_period`,
`slow_period`, and `timeframe` all live on `StrategyConfig.params` —
"5m SMA 9/20" vs. "1m EMA 9/20" is a StrategyConfig version choice, not
a different class. Feature Engine already computes sma_9/sma_20/sma_50
and ema_9/ema_20 on every timeframe it runs (confirmed against
`feature_engine/engine.py` and `core/config.py` directly) — no new
Feature Engine work needed for any of Momentum's v1 parameter choices.

INTEGRATION NOTE — read before wiring this in: `strategy_engine/`
doesn't exist in the repo as of this file's writing; `base_strategy.py`
is being built in a separate, concurrent session. The imports below
follow the direction-locked interface exactly (`Strategy`/
`StrategyConfig`/`Opportunity`/`every_candle` — system-design.md §4.8,
strategy-engine-design.md §3-4) but are NOT verified against the real
`base_strategy.py` yet — reconcile once it lands, particularly:
  - `Strategy.__init__(self, config: StrategyConfig)` / `self.config` —
    assumed here since `evaluate()` needs config-driven params
    somewhere and the ORB example's `self.active_version` implies
    *some* per-instance config access, but no `__init__` is shown in
    the documented ABC.
  - `trigger = every_candle(timeframe=...)` — the documented signature
    shows `every_candle()` with no arguments; a `timeframe` kwarg is
    inferred here (timeframe IS a timing dimension — 5m candles close
    on a different cadence than 1m), not confirmed against real code.
    The explicit `features.timeframe` check in GATE below is a
    defensive fallback either way: harmless no-op if the trigger
    already scopes this, load-bearing if it doesn't.
  - `context: ContextChanged` — the ABC's documented type hint says
    `Context`; the real, already-built schema (decision #92) is
    `ContextChanged` (schemas/events/context.py). Used the real one.
Every GATE/MATCH/SCORE function below (`match_direction`,
`score_confidence`, `ma_key`) is a pure function with zero dependency
on any of the above — testable today regardless of how those questions
resolve.

KNOWN GAPS — found by tracing the real Market State/Feature Engine code
during a MATCH/SCORE review, not fixed here since both live outside
this file's boundary. Flagged, not silently worked around:

  - `market_state.trend_score`/`acceleration_score` are always driven
    by `sma_20_slope_angle` specifically (market_state_engine/scoring.py
    `trend_score()`, decision #93/#83 — hardcoded, not config-driven).
    This strategy's `slow_period` param only changes which MA the
    crossover itself compares; it does NOT change what "trend
    confirmation" measures. A `StrategyConfig` version with
    `slow_period != 20` is comparing its own crossover against a
    trend read anchored to a different period than its "slow" MA —
    a real decoupling, worth knowing before anyone versions one.
  - More serious: `MarketStateEngine._on_features_updated`
    (market_state_engine/engine.py) does not filter by timeframe —
    `_latest_features[symbol]` is one shared slot, overwritten by
    whichever timeframe's `FeaturesUpdated` arrives most recently (1m/
    5m/15m/1h all write to it). Since 1m candles close 5x more often
    than 5m, `market_state.timeframe` will very often be `"1m"` at the
    moment Momentum evaluates a 5m candle close — meaning the
    trend/acceleration confirmation MATCH relies on may silently
    reflect a different timeframe than the crossover it's confirming.
    Deliberately NOT hard-gated on `market_state.timeframe ==
    params["timeframe"]` here the way `features.timeframe` is gated:
    given the race described above, an exact-match gate would make
    Momentum fail to fire almost always, which is worse than the
    current silent behavior. `market_state.timeframe` is now surfaced
    in `evidence["conditions"]` (see PROPOSE below) so this is at
    least inspectable in backtests rather than invisible. This needs a
    real decision — most likely Market State Engine tracking
    latest-features per (symbol, timeframe) instead of one shared slot
    — before Momentum should be trusted live. Raising with Saqib
    rather than picking a fix unilaterally, since it crosses into
    Market State Engine's own territory.
"""
from __future__ import annotations

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

# --- v1 defaults — all overridable via StrategyConfig.params (§3); a
# threshold change is a new StrategyConfig version, never an edit here. ---

DEFAULT_TIMEFRAME = "5m"
DEFAULT_MA_TYPE: Literal["sma", "ema"] = "sma"
DEFAULT_FAST_PERIOD = 9
DEFAULT_SLOW_PERIOD = 20
DEFAULT_TREND_SCORE_THRESHOLD = 60.0
DEFAULT_ACCELERATION_SCORE_THRESHOLD = 55.0
DEFAULT_VOLUME_REGIME_THRESHOLD = 45.0  # ~rvol 1.35 (volume_regime_score's own
# ceiling is rvol=3.0 -> 100, per market_state_engine/scoring.py)
DEFAULT_TARGET_R_MULTIPLE = 2.0

# Saturation point for |regression_{period}_slope_norm| in the SCORE
# blend below. v1 guess, explicitly NOT validated against real score
# distributions yet — same caveat market_state_engine/scoring.py states
# for its own calibration constants (module docstring there).
REGRESSION_SLOPE_NORM_CAP = 0.5

# SCORE blend weights (sum to 1.0) — trend/acceleration weighted
# highest since they're Market State's own directional read; volume
# next; continuation (regression slope) weighted lowest since it's a
# secondary confirmation, not a gate.
_W_TREND = 0.35
_W_ACCELERATION = 0.25
_W_VOLUME = 0.25
_W_CONTINUATION = 0.15


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def default_params() -> dict:
    """v1 StrategyConfig.params — see module docstring for the
    reasoning behind each default."""
    return {
        "timeframe": DEFAULT_TIMEFRAME,
        "ma_type": DEFAULT_MA_TYPE,
        "fast_period": DEFAULT_FAST_PERIOD,
        "slow_period": DEFAULT_SLOW_PERIOD,
        "trend_score_threshold": DEFAULT_TREND_SCORE_THRESHOLD,
        "acceleration_score_threshold": DEFAULT_ACCELERATION_SCORE_THRESHOLD,
        "volume_regime_threshold": DEFAULT_VOLUME_REGIME_THRESHOLD,
        "target_r_multiple": DEFAULT_TARGET_R_MULTIPLE,
    }


def ma_key(ma_type: str, period: int) -> str:
    """Feature Engine's own key convention (`f"sma_{period}"` /
    `f"ema_{period}"` — engine.py) — not reinvented here."""
    return f"{ma_type}_{period}"


def match_direction(
    fast_ma: float,
    slow_ma: float,
    trend_score: float,
    acceleration_score: float,
    volume_regime_score: float,
    *,
    trend_score_threshold: float,
    acceleration_score_threshold: float,
    volume_regime_threshold: float,
) -> Literal["BUY", "SELL"] | None:
    """MATCH stage, pure. Direction-symmetric on purpose: SELL is BUY's
    mirror image around each dimension's neutral 50, not a separately
    hand-tuned rule set. Returns None on no-crossover or any
    confirming condition failing — never a partial/weak signal; that
    nuance belongs to SCORE, not MATCH.

    Both thresholds must be > 50.0. The SELL branch mirrors each one as
    `100 - threshold`; a threshold at or below 50 flips that mirror
    onto the wrong side of neutral (e.g. threshold=40 would let a
    mildly *bullish* trend_score of 55 satisfy SELL's confirmation,
    since 55 <= 100-40). Found while reviewing this function in
    isolation — nothing upstream (`StrategyConfig`, `default_params()`)
    currently enforces it, so a misconfigured version would silently
    produce backwards-confirmed signals rather than erroring. Raises
    here instead, since MATCH is the one place both threshold params
    are guaranteed to pass through regardless of caller."""
    if trend_score_threshold <= 50.0 or acceleration_score_threshold <= 50.0:
        raise ValueError(
            "trend_score_threshold and acceleration_score_threshold must be "
            "> 50.0 for the BUY/SELL mirror-around-neutral logic to hold "
            f"(got trend={trend_score_threshold}, "
            f"acceleration={acceleration_score_threshold})"
        )
    if volume_regime_score < volume_regime_threshold:
        return None  # participation floor — direction-agnostic, checked once

    if fast_ma > slow_ma:
        if (
            trend_score >= trend_score_threshold
            and acceleration_score >= acceleration_score_threshold
        ):
            return "BUY"
        return None
    if fast_ma < slow_ma:
        if (
            trend_score <= (100.0 - trend_score_threshold)
            and acceleration_score <= (100.0 - acceleration_score_threshold)
        ):
            return "SELL"
        return None
    return None  # fast == slow: no crossover, nothing to match


def score_confidence(
    trend_score: float,
    acceleration_score: float,
    volume_regime_score: float,
    regression_slope_norm: float | None,
) -> float:
    """SCORE stage, pure — how strongly the pattern matches, given
    MATCH already confirmed direction. Distance-from-neutral (50) on
    Trend/Acceleration is intentionally direction-agnostic here:
    match_direction() already verified the sign is correct, so only
    magnitude matters for confidence."""
    trend_component = abs(trend_score - 50.0) * 2.0  # 0-100
    acceleration_component = abs(acceleration_score - 50.0) * 2.0  # 0-100
    volume_component = volume_regime_score  # already 0-100 (Market State's own scale)

    if regression_slope_norm is None:
        # Honest-absence, not a fabricated read: neutral midpoint
        # contribution rather than a stealth 0 or 100 (this codebase's
        # "honest state over fabricated state" principle, applied to a
        # scoring input rather than a stored field).
        continuation_component = 50.0
    else:
        continuation_component = _clamp(
            abs(regression_slope_norm) / REGRESSION_SLOPE_NORM_CAP * 100.0
        )

    confidence = (
        _W_TREND * trend_component
        + _W_ACCELERATION * acceleration_component
        + _W_VOLUME * volume_component
        + _W_CONTINUATION * continuation_component
    )
    return round(_clamp(confidence), 2)


def default_config(active_from, version: str = "momentum_v1") -> StrategyConfig:
    """Seed/testing convenience — constructs the v1 StrategyConfig this
    module was designed against. `active_from` is caller-supplied
    (real wall-clock time at the point a human promotes this version —
    config activation bookkeeping, not part of evaluate()'s
    live/backtest-identical timestamp derivation, so this is exempt
    from §7's "never datetime.now() inside evaluate()" rule)."""
    return StrategyConfig(
        strategy_name="Momentum",
        version=version,
        params=default_params(),
        gate_conditions={"session": "regular"},
        allows_waiting=False,
        active_from=active_from,
        active_to=None,
        rationale=(
            "v1 Momentum: 5m MA crossover (config default SMA 9/20), confirmed by "
            "Market State's trend_score/acceleration_score/volume_regime_score. "
            "Thresholds are v1 defaults, unvalidated against real score "
            "distributions — see momentum_strategy.py module docstring."
        ),
    )


class MomentumStrategy(Strategy):
    """See module docstring for the full GATE/MATCH/SCORE/PROPOSE anatomy."""

    name = "Momentum"
    trigger = every_candle(timeframe=DEFAULT_TIMEFRAME)

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    async def evaluate(
        self,
        market_state: MarketState,
        features: FeatureSet,
        context: ContextChanged,  # noqa: ARG002 — not used by v1 MATCH logic;
        # accepted for interface conformance. Available for a future
        # gate_conditions extension (VIX regime, earnings-day exclusion, etc.)
        # without touching this signature again.
    ) -> Opportunity | None:
        params = self.config.params
        timeframe = params.get("timeframe", DEFAULT_TIMEFRAME)

        # --- GATE ---
        if features.timeframe != timeframe:
            return None  # defensive timeframe scope — see module docstring
        ma_type = params.get("ma_type", DEFAULT_MA_TYPE)
        fast_period = params.get("fast_period", DEFAULT_FAST_PERIOD)
        slow_period = params.get("slow_period", DEFAULT_SLOW_PERIOD)
        fast_key = ma_key(ma_type, fast_period)
        slow_key = ma_key(ma_type, slow_period)
        fast_ma = features.features.get(fast_key)
        slow_ma = features.features.get(slow_key)
        if fast_ma is None or slow_ma is None:
            return None  # indicator not warmed up yet — honest absence
        if market_state.acceleration_score is None:
            return None  # symbol's first-ever recompute — no rate to confirm yet

        # --- MATCH ---
        direction = match_direction(
            fast_ma,
            slow_ma,
            market_state.trend_score,
            market_state.acceleration_score,
            market_state.volume_regime_score,
            trend_score_threshold=params.get(
                "trend_score_threshold", DEFAULT_TREND_SCORE_THRESHOLD
            ),
            acceleration_score_threshold=params.get(
                "acceleration_score_threshold", DEFAULT_ACCELERATION_SCORE_THRESHOLD
            ),
            volume_regime_threshold=params.get(
                "volume_regime_threshold", DEFAULT_VOLUME_REGIME_THRESHOLD
            ),
        )
        if direction is None:
            return None

        # --- SCORE ---
        regression_key = f"regression_{fast_period}_slope_norm"
        regression_slope_norm = features.features.get(regression_key)
        confidence = score_confidence(
            market_state.trend_score,
            market_state.acceleration_score,
            market_state.volume_regime_score,
            regression_slope_norm,
        )

        # --- PROPOSE ---
        close = features.close
        target_r = params.get("target_r_multiple", DEFAULT_TARGET_R_MULTIPLE)
        invalidation = slow_ma  # the thesis IS the crossover holding above/below
        # the slow MA — a close back through it falsifies the pattern itself,
        # same "structural, not arbitrary" reasoning as the ORB example's
        # opening_range_low invalidation (strategy-engine-design.md §1).
        # PROPOSE-stage sanity guard, found while reviewing this math: the
        # crossover (fast_ma vs. slow_ma) and `close`'s own position
        # relative to slow_ma are not the same comparison — fast_ma is a
        # 9-bar average and can sit above slow_ma on a bar where the raw
        # close has pulled back below it. If that happens on a BUY,
        # `close - slow_ma` goes negative and both formulas below invert:
        # target lands *below* entry and invalidation (slow_ma) sits
        # *above* entry — a structurally backwards Opportunity. The
        # module docstring itself already frames this thesis as "holding
        # above/below the slow MA," so a close on the wrong side means
        # the thesis is already falsified at the moment of signal, not a
        # weaker version of it — honest absence (None), not a malformed
        # PROPOSE, same as every other guard in this function.
        if direction == "BUY" and close <= slow_ma:
            return None
        if direction == "SELL" and close >= slow_ma:
            return None

        if direction == "BUY":
            target = close + target_r * (close - slow_ma)
        else:
            target = close - target_r * (slow_ma - close)

        return Opportunity(
            strategy=self.name,
            version=self.config.version,
            direction=direction,
            confidence=confidence,
            structural_invalidation=invalidation,
            structural_target=target,
            evidence={
                # Literal MATCH-stage values only — never a wholesale FeatureSet
                # dump (strategy-engine-design.md §4/§11 boundary).
                "conditions": {
                    "ma_type": ma_type,
                    "fast_period": fast_period,
                    "slow_period": slow_period,
                    "fast_ma": fast_ma,
                    "slow_ma": slow_ma,
                    "trend_score": market_state.trend_score,
                    "acceleration_score": market_state.acceleration_score,
                    "volume_regime_score": market_state.volume_regime_score,
                    "regression_slope_norm": regression_slope_norm,
                    # Surfaced per the module docstring's "KNOWN GAPS" note:
                    # this may legitimately differ from `features.timeframe`
                    # (Market State Engine's `_latest_features` isn't
                    # timeframe-scoped) — recorded here so it's inspectable
                    # in backtests/evidence rather than invisible.
                    "market_state_timeframe": market_state.timeframe,
                },
                "reason": (
                    f"{ma_type.upper()} {fast_period}/{slow_period} crossover "
                    f"{direction.lower()}, trend_score={market_state.trend_score:.1f}, "
                    f"acceleration_score={market_state.acceleration_score:.1f}, "
                    f"volume_regime_score={market_state.volume_regime_score:.1f}"
                ),
                "basis": "closed",  # §8 — no PriceSnapshot consumer wired yet;
                # every condition above was read off a settled FeatureSet.
            },
            setup_detected_at=features.candle_ts,  # §7 — never datetime.now()
        )
