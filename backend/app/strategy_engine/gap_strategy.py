"""
Gap Strategy — is today's opening gap holding (continuation), not filling
(reversal)? Second and third strategies built against the real
`base_strategy.py`/`orb_strategy.py` pair (decision #99); this file and
`volume_spike_strategy.py` are delivered together (decision #104/#105).
Planned strategy set: trading-intelligence-architecture.md §8 (ORB,
Momentum, First Pullback, VWAP, Gap, Reversal, Volume Spike).

Question this answers (§7's "question-based, not indicator-based"): "Has
today's opening gap held, or has it already been given back?" A
setup-detector, same boundary discipline as `orb_strategy.py`: Gap%
itself is Feature Engine's own number (`indicators/gap.py`, decisions
#67/#68), read straight off `FeaturesUpdated` per trading-intelligence-
architecture.md §5's explicit ruling ("`GapProvider`... cut from Context
Engine entirely... Strategy reads Gap%/level proximity straight from
`FeaturesUpdated`") — this file does not re-derive gap math, only reads
`features.features["gap_pct"]`/`["gap_dollars"]`, exactly as that
boundary decision anticipated. `market_state.trend_score`/
`volume_regime_score` are read directly too, never re-derived from raw
slope/RVOL — Market State already did that interpretation.

--- Reconstructing `regular_open` without a second piece of state ---

`gap_pct`/`gap_dollars` are Feature Engine's own frozen-at-the-open
values (`FeatureEngine._update_gap`) — but neither `FeatureSet` nor
`MarketState` publishes the regular-session open price itself as its own
key. Rather than track a second, redundant piece of per-symbol state
inside this strategy for a value Feature Engine already effectively
carries, `regular_open` is reconstructed algebraically from two values
already on every `FeatureSet` once a gap exists: `gap_dollars` is defined
(`indicators/gap.py`) as `round(regular_open - pdc, 6)`, so
`regular_open = pdc + gap_dollars` exactly, up to that same 6-decimal
rounding — immaterial for any realistically-priced equity. This is a
one-line algebraic read of two already-published numbers, not a
re-implementation of the gap indicator's own logic (which also needs
`_update_gap`'s stateful "freeze on the first regular-session candle,
backfill on restart" behavior — genuinely orchestration work, correctly
staying in Feature Engine, not duplicated here).

--- Why `regular_open`, not `pdc` itself, is the invalidation level ---

Two candidate theses were possible here: (a) "the gap hasn't fully
filled back to yesterday's close (`pdc`)", or (b) "price is still on the
correct side of today's own opening print (`regular_open`)". (b) was
chosen: it's the more sensitive, ORB-shaped test — ORB's own MATCH tests
`close` against a level that meaningfully separates "still confirming
the pattern" from "no longer confirming it" (`or_high`/`or_low`); `pdc`
as a test would only fire the MOMENT the gap fully closes, by which
point the continuation thesis has already been dead for a while. Using
`regular_open` (a real, structural level — the price where buyers/
sellers actually transacted at the open) means the SAME level this
file's own MATCH stage tests direction against is also PROPOSE's
invalidation, exactly the "no second, independently-sourced price pair
to fall out of sync" property `orb_strategy.py`'s own module docstring
identifies as why ORB was never exposed to the discarded momentum_/
vwap_strategy.py review's PROPOSE-stage contradiction bug (see that
finding, reconciled into decision #99). Gap has the identical property
for the identical reason: direction comes from `gap_pct`'s own sign,
doubly confirmed by `close` vs. `regular_open` — the same pair PROPOSE
then uses for `risk`/`target` — never a separate derived quantity.

--- One fire per symbol per day, not a set of directions ---

`orb_strategy.py` tracks `fired_directions: set[...]` because a genuine
reversal (breaking the OPPOSITE side of a fixed range) is a real, second
event ORB should still catch. Gap has no equivalent: `gap_pct`'s sign is
frozen for the whole day by `_update_gap` itself (decision #67/#68) —
there is only ever ONE possible direction for a given symbol on a given
day, never a second, opposite one to detect. `_GapState.fired` is
therefore a plain `bool`, a deliberate simplification against ORB's
precedent, not an oversight.

--- What was deliberately NOT added: a minimum-elapsed-time gate ---

ORB requires `or_minutes` of formation before it will test anything, an
honest "not enough data yet" gate. A parallel instinct here would be
"wait N minutes after the open before trusting `volume_regime_score`."
Not added: `volume_regime_score` is Market State's OWN interpretation,
already computed and published as a settled `float` on every
`MarketStateChanged` (schemas/events/market_state.py — never partially
null except `acceleration_score`'s documented first-recompute case) —
trusting it at whatever value it reports respects the same "Market State
interprets, Strategy decides" boundary `orb_strategy.py`'s own docstring
states, rather than this file second-guessing Market State Engine's own
freshness by imposing an extra, unvalidated timing rule on top of it.
If real data later shows the first few regular-session candles produce
unreliable `volume_regime_score` reads, that's a Market State Engine
tuning question, not something to patch around here.

--- Session scope ---

Gated to `MarketClock.is_regular_session()` — Gap's continuation-or-not
question only means something once the regular-session open print
exists to test against. `_update_gap` (Feature Engine) leaves `gap_pct`
frozen and present in `features.features` for the REST of the day,
including power hour and (per `_update_gap`'s own code, which applies no
session filter after the initial freeze) after-hours — without this
gate, a stale gap read late in the day would keep re-testing the same
frozen level well past the point "gap continuation" is a meaningful
day-trading pattern.

--- MarketStateEngine race: not a caveat here, unlike ORB/Momentum/VWAP ---

`orb_strategy.py` and the reconciled Momentum/VWAP review (decision #99)
each had to flag `MarketStateEngine`'s shared-`_latest_features[symbol]`-
slot race as a real, unfixed exposure. Decision #103 (built after all
three) closed it — `_latest_features` is now keyed `(symbol, timeframe)`,
and `_compute()` always reads the `(symbol, "1m")` slot specifically,
never "whichever timeframe arrived most recently." This file's default
timeframe is `"1m"`, same as VWAP's — the `market_state.trend_score`/
`volume_regime_score` values read here are now GUARANTEED to reflect the
1m close this strategy is evaluating, not merely "usually" so because 1m
happens to be the dominant writer. Noted explicitly because three prior
strategy files each had to reason carefully about this exposure; this
one doesn't need to.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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

DEFAULT_TIMEFRAME = "1m"  # gap continuation is read from the earliest regular-session candles onward — not configurable per-instance
DEFAULT_MIN_GAP_PCT = 2.0  # v1 guess, unvalidated — a gap smaller than this isn't treated as its own distinct pattern
DEFAULT_TREND_SCORE_THRESHOLD = 60.0  # same convention/value as orb_strategy.py's DEFAULT_TREND_SCORE_THRESHOLD
DEFAULT_VOLUME_REGIME_THRESHOLD = 45.0  # same participation floor as orb_strategy.py (~rvol 1.35)
DEFAULT_TARGET_R_MULTIPLE = 2.0

# SCORE blend weights (sum to 1.0) — v1 guess, explicitly NOT validated
# against real score distributions yet, same caveat orb_strategy.py
# states for its own calibration constants.
_W_TREND = 0.35
_W_VOLUME = 0.30
_W_GAP = 0.35

# Cap for gap_strength_fraction's normalization below — a gap this large
# (percent) saturates SCORE's gap component at 100. v1 guess, unvalidated.
GAP_STRENGTH_CAP_PCT = 10.0


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def default_params() -> dict:
    """v1 StrategyConfig.params — see module docstring for the
    reasoning behind each default."""
    return {
        "min_gap_pct": DEFAULT_MIN_GAP_PCT,
        "trend_score_threshold": DEFAULT_TREND_SCORE_THRESHOLD,
        "volume_regime_threshold": DEFAULT_VOLUME_REGIME_THRESHOLD,
        "target_r_multiple": DEFAULT_TARGET_R_MULTIPLE,
    }


def match_direction(
    close: float,
    regular_open: float,
    gap_pct: float,
    trend_score: float,
    volume_regime_score: float,
    *,
    min_gap_pct: float,
    trend_score_threshold: float,
    volume_regime_threshold: float,
) -> Literal["BUY", "SELL"] | None:
    """MATCH stage, pure. Direction-symmetric on purpose, same shape
    orb_strategy.py's match_direction() already established: SELL is
    BUY's mirror image around each dimension's neutral 50, not a
    separately hand-tuned rule set. Returns None on "gap too small to
    care about", "already given back the open print", or any confirming
    condition failing — never a partial/weak signal; that nuance belongs
    to SCORE, not MATCH.

    `trend_score_threshold` must be > 50.0 — same guard, same reasoning,
    as orb_strategy.py's own match_direction() (decision #99's fix,
    applied directly here rather than risking rediscovering the identical
    bug a third time): SELL mirrors the threshold as `100 - threshold`,
    so a threshold at or below 50 flips onto the wrong side of neutral."""
    if trend_score_threshold <= 50.0:
        raise ValueError(
            f"trend_score_threshold must be > 50.0 for the BUY/SELL "
            f"mirror-around-neutral logic to hold (got {trend_score_threshold})"
        )

    if abs(gap_pct) < min_gap_pct:
        return None  # not a large enough gap to trade as its own distinct pattern

    if volume_regime_score < volume_regime_threshold:
        return None  # participation floor — direction-agnostic, checked once

    if gap_pct > 0:
        if close <= regular_open:
            return None  # gapped up but already given back the open print — not holding
        if trend_score >= trend_score_threshold:
            return "BUY"
        return None
    if gap_pct < 0:
        if close >= regular_open:
            return None  # gapped down but already reclaimed the open print — not holding
        if trend_score <= (100.0 - trend_score_threshold):
            return "SELL"
        return None
    return None  # gap_pct == 0.0 exactly — unreachable given min_gap_pct > 0 in practice, honest no-op regardless


def score_confidence(
    trend_score: float,
    volume_regime_score: float,
    gap_strength: float,
) -> float:
    """SCORE stage, pure — how strongly the pattern matches, given MATCH
    already confirmed direction and that the gap is still holding.
    `gap_strength` is the pre-normalized, pre-clamped magnitude from
    `gap_strength_fraction()` below — kept as a separate pure function so
    it's independently testable against just the raw gap_pct, no score
    inputs involved."""
    trend_component = abs(trend_score - 50.0) * 2.0  # 0-100, direction-agnostic magnitude
    volume_component = volume_regime_score  # already 0-100 (Market State's own scale)
    gap_component = _clamp(gap_strength / GAP_STRENGTH_CAP_PCT * 100.0)

    confidence = (
        _W_TREND * trend_component
        + _W_VOLUME * volume_component
        + _W_GAP * gap_component
    )
    return round(_clamp(confidence), 2)


def gap_strength_fraction(gap_pct: float) -> float:
    """How large today's gap is, in absolute percent — pure function of
    the one raw Feature Engine value, no score inputs, so this is
    testable independent of score_confidence()'s blend weights."""
    return abs(gap_pct)


def default_config(active_from, version: str = "gap_v1") -> StrategyConfig:
    """Seed/testing convenience — constructs the v1 StrategyConfig this
    module was designed against. `active_from` is caller-supplied (real
    wall-clock time at the point a human promotes this version — config
    activation bookkeeping, not part of evaluate()'s live/backtest-
    identical timestamp derivation, so this is exempt from §7's "never
    datetime.now() inside evaluate()" rule)."""
    return StrategyConfig(
        strategy_name="Gap",
        version=version,
        params=default_params(),
        gate_conditions={"session": "regular"},  # mirrors what evaluate() already
        # enforces itself via MarketClock.is_regular_session() — declarative
        # defense-in-depth, not yet consumed by any Scheduler (base_strategy.py's
        # own docstring: the Scheduler that would read this doesn't exist yet).
        allows_waiting=False,
        active_from=active_from,
        active_to=None,
        rationale=(
            "v1 Gap: opening-gap continuation, confirmed by Market State's "
            "trend_score/volume_regime_score against the reconstructed regular-"
            "session open. Minimum 2.0% gap size and thresholds are v1 defaults, "
            "unvalidated against real score distributions — see gap_strategy.py "
            "module docstring."
        ),
    )


@dataclass
class _GapState:
    """Private per-symbol memory — see module docstring's "One fire per
    symbol per day, not a set of directions" section for why `fired` is
    a plain bool rather than orb_strategy.py's `fired_directions: set`."""

    trading_day: date
    fired: bool = False


class GapStrategy(Strategy):
    """See module docstring for the full GATE/MATCH/SCORE/PROPOSE anatomy."""

    name = "Gap"
    trigger = every_candle(timeframe=DEFAULT_TIMEFRAME)

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._state: dict[str, _GapState] = {}

    def _state_for(self, symbol: str, trading_day: date) -> _GapState:
        state = self._state.get(symbol)
        if state is None or state.trading_day != trading_day:
            state = _GapState(trading_day=trading_day)
            self._state[symbol] = state
        return state

    async def evaluate(
        self,
        symbol: str,
        market_state: MarketState,
        features: FeatureSet,
        context: ContextChanged,  # noqa: ARG002 — not used by v1 MATCH logic;
        # accepted for interface conformance, same as orb_strategy.py. Available
        # for a future gate_conditions extension (e.g. excluding earnings-day
        # gaps, which "news"/"calendar" providers could flag) without touching
        # this signature again.
    ) -> Opportunity | None:
        params = self.config.params
        timeframe = DEFAULT_TIMEFRAME

        # --- GATE ---
        if features.timeframe != timeframe:
            return None  # defensive timeframe scope — see module docstring
        clock = get_market_clock()
        if not clock.is_regular_session(features.candle_ts):
            return None  # gap continuation is a regular-session concept — module docstring "Session scope"

        gap_pct = features.features.get("gap_pct")
        gap_dollars = features.features.get("gap_dollars")
        pdc = features.features.get("pdc")
        if gap_pct is None or gap_dollars is None or pdc is None:
            return None  # honest absence — no gap established yet today, or no prior trading day

        trading_day = clock.trading_day(features.candle_ts)
        state = self._state_for(symbol, trading_day)
        if state.fired:
            return None  # already answered this question for today — module docstring

        regular_open = pdc + gap_dollars  # module docstring's "Reconstructing regular_open" section

        # --- MATCH ---
        direction = match_direction(
            features.close, regular_open, gap_pct,
            market_state.trend_score, market_state.volume_regime_score,
            min_gap_pct=params.get("min_gap_pct", DEFAULT_MIN_GAP_PCT),
            trend_score_threshold=params.get("trend_score_threshold", DEFAULT_TREND_SCORE_THRESHOLD),
            volume_regime_threshold=params.get("volume_regime_threshold", DEFAULT_VOLUME_REGIME_THRESHOLD),
        )
        if direction is None:
            return None

        # --- SCORE ---
        strength = gap_strength_fraction(gap_pct)
        confidence = score_confidence(market_state.trend_score, market_state.volume_regime_score, strength)

        # --- PROPOSE ---
        target_r = params.get("target_r_multiple", DEFAULT_TARGET_R_MULTIPLE)
        invalidation = regular_open
        # the thesis IS the gap holding beyond the open print — a close back
        # through it falsifies the pattern itself, same "structural, not
        # arbitrary" reasoning orb_strategy.py's own invalidation comment uses.
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
                    "gap_pct": gap_pct,
                    "gap_dollars": gap_dollars,
                    "regular_open": round(regular_open, 6),
                    "pdc": pdc,
                    "close": features.close,
                    "trend_score": market_state.trend_score,
                    "volume_regime_score": market_state.volume_regime_score,
                },
                "reason": (
                    f"Gap {direction.lower()} continuation: {gap_pct:+.2f}% gap holding "
                    f"{'above' if direction == 'BUY' else 'below'} regular open {regular_open:.2f}, "
                    f"trend_score={market_state.trend_score:.1f}, "
                    f"volume_regime_score={market_state.volume_regime_score:.1f}"
                ),
                "basis": "closed",  # §8 — no PriceSnapshot consumer wired yet;
                # every condition above was read off a settled FeatureSet.
            },
            setup_detected_at=features.candle_ts,  # §7 — never datetime.now()
        )
