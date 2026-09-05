"""
VWAP Strategy — is price holding session VWAP as a level (support
tested from above, resistance tested from below), confirmed by Market
State's Trend and Volume-regime dimensions?

Direction lock: strategy-engine-design.md §1/§3/§4, decisions #87-89.
Planned strategy set: trading-intelligence-architecture.md §8.

Deliberately distinct from Momentum (this file): Momentum answers "is a
trending move accelerating," VWAP answers "is the level itself holding
right now." Both can legitimately fire together on the same symbol —
that's confluence, and it's Opportunity Engine's/Decision Engine's job
to weigh multiple agreeing Opportunities (trading-intelligence-
architecture.md §9-10), never this strategy's own job to know Momentum
exists. Keeping evaluate() reading only `(market_state, features,
context)` — never another strategy's output — is what keeps this
testable in isolation and keeps Strategy Engine's own stated boundary
intact: "Nothing decides 'this is a Momentum setup, not VWAP.' Every
eligible strategy's detector runs independently."

Boundary discipline: `market_state.vwap_relationship_score` (Market
State Engine, decision #93) is `(close - vwap) / vwap`, already
normalized to 0-100 (50 = at VWAP) — read directly here, never
re-derived from raw `close`/`vwap`. What Market State's score does NOT
capture is a *band* read (avoiding both "still right on the line,
noise" and "already extended, different strategy's job") — that band
judgment is this strategy's own MATCH-stage interpretation, same
division of labor Momentum uses for its MA crossover.

v1 scope, stated plainly: this tests *position relative to VWAP*
(has price established itself just above/below the level, with
trend/volume backing), not a *reclaim event* (just-crossed, as of this
candle). A true event-based reclaim needs per-symbol memory of the
prior candle's side — real, legitimate state (`strategy-engine-
design.md` §8 already establishes strategies may hold small internal
per-symbol state, for the pending-Opportunity case), but a bigger step
than this v1 needs. Flagged here rather than silently built or
silently skipped — revisit if the position-based read proves too
noisy in practice.

INTEGRATION NOTE — same caveats as momentum_strategy.py's module
docstring: `strategy_engine/` doesn't exist in the repo yet;
`Strategy.__init__(self, config)`, `every_candle(timeframe=...)`, and
`context: ContextChanged` (vs. the ABC's documented `Context` type
hint) are all inferred against the locked spec, not verified against
the real `base_strategy.py`. `match_direction`/`score_confidence`/
`_band_quality` below have zero dependency on any of that — testable
today regardless.

KNOWN GAPS — found tracing the real Feature/Market State Engine code
during review, same discipline as momentum_strategy.py's own "KNOWN
GAPS" section:

  - `market_state`'s shared-slot-per-symbol race (momentum_strategy.py's
    docstring has the full explanation) applies here too, but is
    LESS severe by default: VWAP's own default `timeframe` is `"1m"`,
    and 1m `FeaturesUpdated` fires on every close — the highest
    frequency of any timeframe — so it's the dominant writer of
    `_latest_features[symbol]` most of the time, not a rare visitor the
    way it is for Momentum's 5m default. Still not zero-risk (any 5m/
    15m/1h close landing inside the same debounce window can briefly
    overwrite it), and would become the SAME severity as Momentum's if
    this strategy's `timeframe` param were ever reconfigured away from
    1m. `market_state.timeframe` is now in `evidence["conditions"]`
    for the same inspectability reason.
  - More consequential, specific to this file: PROPOSE's target uses
    `atr_14`, and `atr_14` is Wilder ATR over the last 14 COMPLETE
    DAILY bars (feature_engine/indicators/atr.py, decisions #67/#68) —
    a session-level statistic frozen once per day, deliberately the
    same "reads identically regardless of chart timeframe" shape as
    VWAP itself (system-design.md §4.5). That means `target = close +
    atr_target_multiplier * atr_14` sizes a 1-MINUTE-chart level-hold
    target off a DAILY range measure. `atr_target_multiplier=2.0`
    against a $2 daily ATR asks a quick 1m VWAP-hold play to capture
    2x the stock's whole typical DAY's range before exit — not
    obviously the right unit for this strategy's own timeframe, but
    picking a replacement (a smaller multiplier against the same
    daily ATR vs. some intraday-range measure that doesn't currently
    exist in Feature Engine vs. something else) is a real strategy-
    design call, not a bug fix — raising it rather than picking one.
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

# --- v1 defaults — all overridable via StrategyConfig.params (§3). ---

DEFAULT_TIMEFRAME = "1m"  # fastest reaction to a VWAP test, per Saqib's call —
# VWAP itself is timeframe-agnostic (system-design.md §4.5: "a session-level
# statistic that should read identically regardless of chart timeframe"), so
# 1m is a responsiveness choice here, not a data-availability constraint.

# vwap_relationship_score band for a BUY signal: must be clearly above the
# line (not sitting at the noisy 50 midpoint) but not yet "extended" (that's
# a different, not-yet-built strategy's job). SELL is this band's mirror
# image around 50 (100-high .. 100-low). v1 guess, unvalidated against real
# score distributions — same caveat as every band/threshold in this file.
DEFAULT_VWAP_SCORE_LOW = 52.0
DEFAULT_VWAP_SCORE_HIGH = 65.0
DEFAULT_TREND_SCORE_THRESHOLD = 55.0
DEFAULT_VOLUME_REGIME_THRESHOLD = 40.0  # ~rvol 1.2
DEFAULT_ATR_TARGET_MULTIPLIER = 2.0

# SCORE blend weights (sum to 1.0).
_W_BAND = 0.40
_W_TREND = 0.35
_W_VOLUME = 0.25


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def default_params() -> dict:
    return {
        "timeframe": DEFAULT_TIMEFRAME,
        "vwap_score_low": DEFAULT_VWAP_SCORE_LOW,
        "vwap_score_high": DEFAULT_VWAP_SCORE_HIGH,
        "trend_score_threshold": DEFAULT_TREND_SCORE_THRESHOLD,
        "volume_regime_threshold": DEFAULT_VOLUME_REGIME_THRESHOLD,
        "atr_target_multiplier": DEFAULT_ATR_TARGET_MULTIPLIER,
    }


def match_direction(
    vwap_relationship_score: float,
    trend_score: float,
    volume_regime_score: float,
    *,
    vwap_score_low: float,
    vwap_score_high: float,
    trend_score_threshold: float,
    volume_regime_threshold: float,
) -> Literal["BUY", "SELL"] | None:
    """MATCH stage, pure. BUY band is [vwap_score_low, vwap_score_high];
    SELL band is its mirror image around 50. Direction-agnostic volume
    floor checked once, same shape as momentum_strategy.match_direction.

    `trend_score_threshold` must be > 50.0 (SELL mirrors it as
    `100 - threshold`, same failure mode momentum_strategy.match_direction
    guards against) and `vwap_score_low`/`vwap_score_high` must satisfy
    `50 < vwap_score_low < vwap_score_high <= 100` — the BUY band is
    documented as "clearly above the line"; a band that dips to 50 or
    below, or is inverted/zero-width, silently stops meaning that."""
    if trend_score_threshold <= 50.0:
        raise ValueError(
            f"trend_score_threshold must be > 50.0 (got {trend_score_threshold})"
        )
    if not (50.0 < vwap_score_low < vwap_score_high <= 100.0):
        raise ValueError(
            "vwap_score_low/vwap_score_high must satisfy "
            f"50 < low < high <= 100 (got low={vwap_score_low}, "
            f"high={vwap_score_high})"
        )
    if volume_regime_score < volume_regime_threshold:
        return None

    if vwap_score_low <= vwap_relationship_score <= vwap_score_high:
        if trend_score >= trend_score_threshold:
            return "BUY"
        return None

    sell_low = 100.0 - vwap_score_high
    sell_high = 100.0 - vwap_score_low
    if sell_low <= vwap_relationship_score <= sell_high:
        if trend_score <= (100.0 - trend_score_threshold):
            return "SELL"
        return None

    return None


def _band_quality(score: float, low: float, high: float) -> float:
    """Triangular quality peaking at the band's own midpoint, tapering
    to 0 at its edges. v1 judgment call: a read right at the band
    center is the "cleanest" level-hold; near either edge it's either
    barely-reclaimed (still could fail) or already drifting toward
    extended (arguably a different strategy's setup) — not derived
    from real distributions yet, flagged same as every other
    calibration constant in this file."""
    mid = (low + high) / 2.0
    half_width = (high - low) / 2.0
    if half_width <= 0:
        return 100.0
    distance = abs(score - mid)
    return _clamp(100.0 * (1.0 - distance / half_width))


def score_confidence(
    vwap_relationship_score: float,
    trend_score: float,
    volume_regime_score: float,
    *,
    vwap_score_low: float,
    vwap_score_high: float,
    direction: Literal["BUY", "SELL"],
) -> float:
    """SCORE stage, pure — given MATCH already confirmed direction and
    band membership."""
    if direction == "BUY":
        band_low, band_high = vwap_score_low, vwap_score_high
    else:
        band_low, band_high = 100.0 - vwap_score_high, 100.0 - vwap_score_low

    band_component = _band_quality(vwap_relationship_score, band_low, band_high)
    trend_component = abs(trend_score - 50.0) * 2.0  # direction already confirmed by MATCH
    volume_component = volume_regime_score  # already 0-100

    confidence = _W_BAND * band_component + _W_TREND * trend_component + _W_VOLUME * volume_component
    return round(_clamp(confidence), 2)


def default_config(active_from, version: str = "vwap_v1") -> StrategyConfig:
    """Seed/testing convenience — see momentum_strategy.default_config's
    docstring for why `active_from` being caller-supplied wall-clock
    time here doesn't conflict with §7's evaluate()-only constraint."""
    return StrategyConfig(
        strategy_name="VWAP",
        version=version,
        params=default_params(),
        gate_conditions={"session": "regular"},
        allows_waiting=False,
        active_from=active_from,
        active_to=None,
        rationale=(
            "v1 VWAP: 1m position-relative-to-VWAP read (vwap_relationship_score "
            "band, not a reclaim event — see module docstring), confirmed by "
            "trend_score/volume_regime_score. Thresholds are v1 defaults, "
            "unvalidated against real score distributions."
        ),
    )


class VWAPStrategy(Strategy):
    """See module docstring for the full GATE/MATCH/SCORE/PROPOSE anatomy
    and the explicit v1-scope note (position read, not reclaim event)."""

    name = "VWAP"
    trigger = every_candle(timeframe=DEFAULT_TIMEFRAME)

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    async def evaluate(
        self,
        market_state: MarketState,
        features: FeatureSet,
        context: ContextChanged,  # noqa: ARG002 — see momentum_strategy.py's
        # identical note; accepted for interface conformance, unused by v1 MATCH.
    ) -> Opportunity | None:
        params = self.config.params
        timeframe = params.get("timeframe", DEFAULT_TIMEFRAME)

        # --- GATE ---
        if features.timeframe != timeframe:
            return None  # defensive timeframe scope — see module docstring
        vwap = features.features.get("vwap")
        atr = features.features.get("atr_14")
        if vwap is None or vwap == 0.0:
            return None  # no session VWAP yet (or the vwap==0 degenerate case
            # scoring.py itself guards against) — honest absence, not a guess
        if atr is None:
            return None  # needed for PROPOSE's target projection

        vwap_score = market_state.vwap_relationship_score

        # --- MATCH ---
        direction = match_direction(
            vwap_score,
            market_state.trend_score,
            market_state.volume_regime_score,
            vwap_score_low=params.get("vwap_score_low", DEFAULT_VWAP_SCORE_LOW),
            vwap_score_high=params.get("vwap_score_high", DEFAULT_VWAP_SCORE_HIGH),
            trend_score_threshold=params.get(
                "trend_score_threshold", DEFAULT_TREND_SCORE_THRESHOLD
            ),
            volume_regime_threshold=params.get(
                "volume_regime_threshold", DEFAULT_VOLUME_REGIME_THRESHOLD
            ),
        )
        if direction is None:
            return None

        # --- SCORE ---
        confidence = score_confidence(
            vwap_score,
            market_state.trend_score,
            market_state.volume_regime_score,
            vwap_score_low=params.get("vwap_score_low", DEFAULT_VWAP_SCORE_LOW),
            vwap_score_high=params.get("vwap_score_high", DEFAULT_VWAP_SCORE_HIGH),
            direction=direction,
        )

        # --- PROPOSE ---
        close = features.close
        target_mult = params.get("atr_target_multiplier", DEFAULT_ATR_TARGET_MULTIPLIER)
        invalidation = vwap  # the level itself IS the thesis — a close back
        # through VWAP falsifies it, same "structural, not arbitrary" shape
        # Momentum uses for its own MA-based invalidation.
        # PROPOSE-stage sanity guard, same reasoning as
        # momentum_strategy.py's identical check: `direction` came from
        # `market_state.vwap_relationship_score`, which per this file's
        # own "KNOWN GAPS" note may not always share the exact same
        # close/vwap pair as the LOCAL `features.close`/`vwap` used for
        # the math below. If local `close` doesn't actually confirm the
        # direction against local `vwap`, the strategy's own stated
        # thesis ("holding VWAP as a level") is already false for the
        # data this Opportunity would be built from — honest absence,
        # not a malformed PROPOSE.
        if direction == "BUY" and close <= vwap:
            return None
        if direction == "SELL" and close >= vwap:
            return None

        if direction == "BUY":
            target = close + target_mult * atr
        else:
            target = close - target_mult * atr

        return Opportunity(
            strategy=self.name,
            version=self.config.version,
            direction=direction,
            confidence=confidence,
            structural_invalidation=invalidation,
            structural_target=target,
            evidence={
                "conditions": {
                    "vwap": vwap,
                    "close": close,
                    "vwap_relationship_score": vwap_score,
                    "trend_score": market_state.trend_score,
                    "volume_regime_score": market_state.volume_regime_score,
                    "atr_14": atr,
                    # Surfaced per this file's "KNOWN GAPS" note — may
                    # legitimately differ from `features.timeframe`.
                    "market_state_timeframe": market_state.timeframe,
                },
                "reason": (
                    f"VWAP {direction.lower()} — vwap_relationship_score={vwap_score:.1f}, "
                    f"trend_score={market_state.trend_score:.1f}, "
                    f"volume_regime_score={market_state.volume_regime_score:.1f}"
                ),
                "basis": "closed",  # §8 — no PriceSnapshot consumer wired yet
            },
            setup_detected_at=features.candle_ts,  # §7 — never datetime.now()
        )
