"""
ORB (Opening Range Breakout) Strategy — the first concrete strategy
built against the real `base_strategy.py` (decision #99). Previously
illustrated only as a sketch in strategy-engine-design.md §1; this is
the actual, tested implementation, with several corrections against
that sketch documented below.

Direction lock: strategy-engine-design.md §1 (GATE/MATCH/SCORE/PROPOSE
anatomy), §3 (StrategyConfig), §4 (Opportunity/evidence schema),
decisions #87-89, #98 (Market State / Context `get_snapshot()`), #99
(base_strategy.py, FeatureSet OHLC). Planned strategy set:
trading-intelligence-architecture.md §8.

Question this answers (§7's "question-based, not indicator-based"):
"Is this breakout of the opening range likely to continue?" A
setup-detector: it *consumes* Trend/Volume-regime (Market State Engine,
decision #93), it doesn't rebuild them. The one thing genuinely ORB's
own job is the breakout test itself against a range no other engine
tracks — the opening range belongs to ORB alone, deliberately not
published to `FeatureSet` (Saqib's call — see "Opening range state"
below).

Boundary discipline (system-design.md §4.5): "Feature Engine measures.
Market State interprets. Strategy decides." `market_state.trend_score`/
`volume_regime_score` are read directly here, never re-derived from raw
slope/RVOL — Market State already did that interpretation.

--- Corrections against strategy-engine-design.md §1's illustrative ORB sketch ---

1. TRIGGER: the sketch used `trigger = after_time("09:30", until="09:45")`
   — a single time-boxed window. That can't be right for a real breakout
   strategy: the range needs to be OBSERVED during 09:30-09:45 (every 1m
   candle in that window has to be seen to compute a true high/low), and
   the breakout itself typically happens AFTER 09:45, once the range is
   already fixed. A trigger that only fires during formation would never
   see the breakout at all. Corrected here to `every_candle(timeframe="1m")`
   — evaluate() runs on every 1m candle all session; the GATE step below
   decides internally whether a given candle is still forming the range
   or is a breakout candidate, using MarketClock-derived time, not the
   trigger. This is Stage 1's own call (see base_strategy.py's docstring
   for the general reasoning); the sketch was explicitly "illustrative,
   not final code."

2. OPENING RANGE HIGH/LOW NEEDS TRUE WICKS, NOT CLOSES: the sketch read
   `features.opening_range_high` as if Feature Engine already published
   it. It didn't — `FeatureSet` carried only `close` (decision #99's own
   FeatureSet schema gap, strategy-engine-design.md §8). A true opening
   range is the highest HIGH and lowest LOW printed during the window;
   approximating it from closes alone would systematically understate
   the range on either side. Decision #99 added `open`/`high`/`low`/
   `volume` to `FeatureSet` (1m only) specifically so this strategy could
   be built correctly rather than against an approximation.

--- Opening range state: private to this strategy, not published ---

Saqib's call: the running opening-range accumulator lives entirely
inside `ORBStrategy`'s own memory, keyed by symbol (§8 already
establishes strategies may hold small internal per-symbol state — the
same pattern the discarded vwap_strategy.py draft used for a "reclaim"
event note). It is NEVER written to `FeatureSet` or anywhere else — no
other engine or strategy can see it.

Known, accepted limitation of that choice (flagged explicitly per §11's
"decisions before code" / "unverified assumptions flagged" discipline,
not glossed over): this state is in-memory only, with no persistence
and no replay-from-history path. If the process restarts intraday
AFTER 09:45 on a given symbol, ORB has no way to reconstruct that day's
opening range — Strategy Engine only consumes `MarketStateChanged`/
`FeaturesUpdated`/`ContextChanged`, never raw candle history directly —
and simply won't fire for that symbol for the remainder of that trading
day (GATE returns None indefinitely — an honest gap, not a fabricated
range). Acceptable for v1; would need Strategy-level state persistence
(no such mechanism exists anywhere in Strategy Engine today) to fix.

--- Symbol-keyed state and day rollover ---

`self._state: dict[str, _ORBState]`, one entry per symbol this instance
has evaluated. Reset detection uses `MarketClock.trading_day()` off
`features.candle_ts` (never wall-clock `datetime.now()` — §7), the same
"track the trading_day one has, compare, reset on change" shape
`level_interaction_engine.py` already established for its own daily
touch counters.

--- Fires once per direction per day ---

Once ORB proposes a BUY (or SELL) for a symbol on a given day, it won't
propose the same direction again that day even if price stays beyond
the range for many subsequent candles — `_ORBState.fired_directions`
tracks this. Undocumented anywhere as a locked rule; this file's own
deliberate choice, made explicit here rather than silently baked in,
so a duplicate-Opportunity flood isn't mistaken for a bug. A reversal
(price breaking the OPPOSITE side after already firing one direction)
is still allowed to fire once of its own.

--- Reconciling this morning's concurrent-session findings on the discarded orphans ---

While this file was being built, a separate session (assigned Momentum,
per Saqib's own instruction) independently reviewed the discarded
`momentum_strategy.py`/`vwap_strategy.py` in place — not told they were
about to be discarded, since Saqib's own answer to "should the parallel
session wait for base_strategy.py" was "start now, reconcile later."
This is that reconciliation. Two findings carried forward here rather
than lost when those files were discarded:

1. **Threshold validation guard** — applied directly, see
   `match_direction()`'s own docstring below. Both discarded files had
   the identical BUY/SELL-mirror-around-50 vulnerability; ORB has the
   exact same shape of threshold and would have had the exact same bug.

2. **PROPOSE-stage sanity guard — found NOT to apply here, and why.**
   Both discarded files' review found a real bug: `direction` was
   decided from `market_state`'s own score, but the target/invalidation
   math used a locally-read price pair (`close`/`slow_ma` for Momentum,
   `close`/`vwap` for VWAP) that isn't guaranteed to agree with that
   direction, given the timeframe race in finding 3 below — risking a
   structurally backwards Opportunity. ORB doesn't have this exposure:
   `match_direction()` above decides direction from `close` itself
   against `or_high`/`or_low` — the SAME value PROPOSE then uses for
   `risk`/`target` — not a separate derived quantity that could
   disagree. There is no second, independently-sourced price pair for
   ORB's own direction to fall out of sync with.

3. **`MarketStateEngine`'s shared-slot-per-symbol race — real, found
   independently by both reviews, NOT fixed here, ORB's own exposure
   noted.** `MarketStateEngine._on_features_updated` keeps one
   `_latest_features[symbol]` slot regardless of timeframe — 1m, 5m,
   15m, and 1h `FeaturesUpdated` all overwrite it, whichever arrives
   most recently. `market_state.trend_score`/`volume_regime_score`
   read here could, in principle, reflect whatever timeframe's close
   happened to land right before this candle's evaluate() call, not
   necessarily "the market state as of this 1m close." Materially
   lower risk for ORB than it was flagged for Momentum's 5m default:
   1m `FeaturesUpdated` fires 5x more often than 5m and far more than
   15m/1h, so it's the dominant writer of the shared slot most of the
   time when ORB (always 1m) is the one reading it — but not zero-risk,
   since a 5m/15m/1h close landing in the same debounce window can
   still transiently win. This is Market State Engine's own territory
   to fix (most likely: per-`(symbol, timeframe)` tracking instead of
   one shared slot), raised with Saqib rather than patched unilaterally
   here, same restraint both reviews already showed.
"""
from dataclasses import dataclass, field
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

# --- v1 defaults — all overridable via StrategyConfig.params (§3); a
# threshold change is a new StrategyConfig version, never an edit here. ---

DEFAULT_TIMEFRAME = "1m"  # opening range formation needs 1m granularity — not configurable per-instance
DEFAULT_OR_MINUTES = 15  # Saqib's choice — configurable per StrategyConfig version
DEFAULT_TREND_SCORE_THRESHOLD = 60.0  # same convention/value as momentum_strategy.py's DEFAULT_TREND_SCORE_THRESHOLD
DEFAULT_VOLUME_REGIME_THRESHOLD = 45.0  # same participation floor as momentum_strategy.py (~rvol 1.35)
DEFAULT_TARGET_R_MULTIPLE = 2.0

# SCORE blend weights (sum to 1.0) — v1 guess, explicitly NOT validated
# against real score distributions yet, same caveat every other
# strategy/scoring module in this codebase states for its own
# calibration constants.
_W_TREND = 0.35
_W_VOLUME = 0.30
_W_BREAKOUT_STRENGTH = 0.35

# Cap for breakout_strength_component's normalization below — a
# breakout clearing the range by this fraction of the range's own width
# saturates SCORE's breakout component at 100. v1 guess, unvalidated.
BREAKOUT_STRENGTH_CAP = 0.5


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def default_params() -> dict:
    """v1 StrategyConfig.params — see module docstring for the
    reasoning behind each default."""
    return {
        "or_minutes": DEFAULT_OR_MINUTES,
        "trend_score_threshold": DEFAULT_TREND_SCORE_THRESHOLD,
        "volume_regime_threshold": DEFAULT_VOLUME_REGIME_THRESHOLD,
        "target_r_multiple": DEFAULT_TARGET_R_MULTIPLE,
    }


def match_direction(
    close: float,
    or_high: float,
    or_low: float,
    trend_score: float,
    volume_regime_score: float,
    *,
    trend_score_threshold: float,
    volume_regime_threshold: float,
) -> Literal["BUY", "SELL"] | None:
    """MATCH stage, pure. Direction-symmetric on purpose, same shape
    momentum_strategy.py's match_direction() already established: SELL
    is BUY's mirror image around each dimension's neutral 50, not a
    separately hand-tuned rule set. Returns None on no-breakout or any
    confirming condition failing — never a partial/weak signal; that
    nuance belongs to SCORE, not MATCH.

    `trend_score_threshold` must be > 50.0 — the parallel Momentum/VWAP
    review (this morning's concurrent-session drop, reconciled into
    decision #99 rather than lost when their underlying files were
    discarded) found this exact failure mode in both: SELL mirrors the
    threshold as `100 - threshold`, so a threshold at or below 50 flips
    onto the wrong side of neutral (threshold=40 would let a mildly
    *bullish* trend_score of 55 satisfy SELL's confirmation, since
    55 <= 100-40). Raises here rather than silently producing a
    backwards-confirmed signal, applying that review's fix directly
    rather than rediscovering it independently."""
    if trend_score_threshold <= 50.0:
        raise ValueError(
            f"trend_score_threshold must be > 50.0 for the BUY/SELL "
            f"mirror-around-neutral logic to hold (got {trend_score_threshold})"
        )

    if volume_regime_score < volume_regime_threshold:
        return None  # participation floor — direction-agnostic, checked once

    if close > or_high:
        if trend_score >= trend_score_threshold:
            return "BUY"
        return None
    if close < or_low:
        if trend_score <= (100.0 - trend_score_threshold):
            return "SELL"
        return None
    return None  # inside the range: no breakout, nothing to match


def score_confidence(
    trend_score: float,
    volume_regime_score: float,
    breakout_strength: float,
) -> float:
    """SCORE stage, pure — how strongly the pattern matches, given
    MATCH already confirmed direction and breakout side.
    `breakout_strength` is the pre-normalized, pre-clamped fraction from
    `breakout_strength_fraction()` below — kept as a separate pure
    function so it's independently testable against just the four raw
    prices, no score inputs involved."""
    trend_component = abs(trend_score - 50.0) * 2.0  # 0-100, direction-agnostic magnitude
    volume_component = volume_regime_score  # already 0-100 (Market State's own scale)
    breakout_component = _clamp(breakout_strength / BREAKOUT_STRENGTH_CAP * 100.0)

    confidence = (
        _W_TREND * trend_component
        + _W_VOLUME * volume_component
        + _W_BREAKOUT_STRENGTH * breakout_component
    )
    return round(_clamp(confidence), 2)


def breakout_strength_fraction(close: float, or_high: float, or_low: float, direction: Literal["BUY", "SELL"]) -> float:
    """How far beyond the range edge `close` sits, as a fraction of the
    range's own width. A breakout that clears the range by half its own
    width is stronger evidence than a breakout by one tick. Pure
    function of the four raw prices — no score inputs — so this is
    testable independent of score_confidence()'s blend weights.
    Zero-width range (or_high == or_low) returns 0.0 rather than
    dividing by zero — an honest "no signal from this component," not
    an error; a genuinely flat opening range is rare but not
    impossible on a low-volume symbol."""
    range_width = or_high - or_low
    if range_width <= 0.0:
        return 0.0
    if direction == "BUY":
        return max(0.0, (close - or_high) / range_width)
    return max(0.0, (or_low - close) / range_width)


def default_config(active_from, version: str = "orb_v1") -> StrategyConfig:
    """Seed/testing convenience — constructs the v1 StrategyConfig this
    module was designed against. `active_from` is caller-supplied (real
    wall-clock time at the point a human promotes this version — config
    activation bookkeeping, not part of evaluate()'s live/backtest-
    identical timestamp derivation, so this is exempt from §7's "never
    datetime.now() inside evaluate()" rule)."""
    return StrategyConfig(
        strategy_name="ORB",
        version=version,
        params=default_params(),
        gate_conditions={"session": "regular"},
        allows_waiting=False,
        active_from=active_from,
        active_to=None,
        rationale=(
            "v1 ORB: 15-minute opening range breakout (config default), confirmed by "
            "Market State's trend_score/volume_regime_score. Opening range is tracked "
            "privately inside this strategy, never published. Thresholds are v1 "
            "defaults, unvalidated against real score distributions — see "
            "orb_strategy.py module docstring."
        ),
    )


@dataclass
class _ORBState:
    """Private per-symbol memory — see module docstring's "Opening range
    state" section for why this exists and its accepted limitation.

    `candles_seen` (not just `or_high is not None`) is what actually
    gates `or_formed` below — tracking count, not just presence, matters
    because a gap in the candle stream during formation (a dropped/
    missed CandleClosed, or a process that starts watching a symbol
    partway through the window) would otherwise silently freeze an
    INCOMPLETE range as if it were the real one, with no signal that
    anything was wrong. Honest state over fabricated state (§11):
    `or_formed` only ever becomes True once every expected minute of
    the window was actually observed."""

    trading_day: date
    or_high: float | None = None
    or_low: float | None = None
    or_formed: bool = False
    candles_seen: int = 0
    fired_directions: set[Literal["BUY", "SELL"]] = field(default_factory=set)


class ORBStrategy(Strategy):
    """See module docstring for the full GATE/MATCH/SCORE/PROPOSE anatomy."""

    name = "ORB"
    trigger = every_candle(timeframe=DEFAULT_TIMEFRAME)

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._state: dict[str, _ORBState] = {}

    def _state_for(self, symbol: str, trading_day: date) -> _ORBState:
        state = self._state.get(symbol)
        if state is None or state.trading_day != trading_day:
            # First time seeing this symbol, or a new day — reset.
            # "Missed formation window entirely" (state is None but
            # trading_day already past 09:30+or_minutes) is handled by
            # the caller's GATE check below, not here: this method's
            # only job is producing a correctly-dated blank slate.
            state = _ORBState(trading_day=trading_day)
            self._state[symbol] = state
        return state

    async def evaluate(
        self,
        symbol: str,
        market_state: MarketState,
        features: FeatureSet,
        context: ContextChanged,  # noqa: ARG002 — not used by v1 MATCH logic;
        # accepted for interface conformance. Available for a future
        # gate_conditions extension (earnings-day exclusion, etc.)
        # without touching this signature again.
    ) -> Opportunity | None:
        params = self.config.params
        timeframe = DEFAULT_TIMEFRAME

        # --- GATE ---
        if features.timeframe != timeframe:
            return None  # defensive timeframe scope — see module docstring
        if features.high is None or features.low is None:
            return None  # honest absence — pre-decision #99 FeatureSet, or an
            # aggregated timeframe's FeatureSet slipping through despite the
            # check above; either way, can't accumulate/test a range without
            # real wicks (see module docstring's correction #2)

        clock = get_market_clock()
        trading_day = clock.trading_day(features.candle_ts)
        or_minutes = params.get("or_minutes", DEFAULT_OR_MINUTES)
        state = self._state_for(symbol, trading_day)
        minutes = clock.minutes_since_open(features.candle_ts)

        if minutes < or_minutes:
            # Still forming — accumulate this candle's wick into the
            # running range, never evaluate a breakout yet.
            state.or_high = features.high if state.or_high is None else max(state.or_high, features.high)
            state.or_low = features.low if state.or_low is None else min(state.or_low, features.low)
            state.candles_seen += 1
            return None

        if not state.or_formed:
            # First candle at/past the window boundary. If fewer than
            # `or_minutes` formation candles were actually observed —
            # a gap in the stream, or this process only started
            # watching this symbol partway through the window — there
            # is nothing honest to test against (see _ORBState's own
            # docstring and module docstring's "known, accepted
            # limitation").
            if state.candles_seen < or_minutes or state.or_high is None or state.or_low is None:
                return None
            state.or_formed = True

        # --- MATCH ---
        direction = match_direction(
            features.close,
            state.or_high,
            state.or_low,
            market_state.trend_score,
            market_state.volume_regime_score,
            trend_score_threshold=params.get("trend_score_threshold", DEFAULT_TREND_SCORE_THRESHOLD),
            volume_regime_threshold=params.get("volume_regime_threshold", DEFAULT_VOLUME_REGIME_THRESHOLD),
        )
        if direction is None:
            return None
        if direction in state.fired_directions:
            return None  # already proposed this direction today — see module docstring

        # --- SCORE ---
        strength = breakout_strength_fraction(features.close, state.or_high, state.or_low, direction)
        confidence = score_confidence(market_state.trend_score, market_state.volume_regime_score, strength)

        # --- PROPOSE ---
        target_r = params.get("target_r_multiple", DEFAULT_TARGET_R_MULTIPLE)
        invalidation = state.or_low if direction == "BUY" else state.or_high
        # the thesis IS the breakout holding beyond the range — a close
        # back inside it falsifies the pattern itself, same "structural,
        # not arbitrary" reasoning momentum_strategy.py's own invalidation
        # comment uses for its slow-MA invalidation.
        risk = abs(features.close - invalidation)
        target = features.close + target_r * risk if direction == "BUY" else features.close - target_r * risk

        state.fired_directions.add(direction)

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
                    "or_minutes": or_minutes,
                    "or_high": state.or_high,
                    "or_low": state.or_low,
                    "close": features.close,
                    "trend_score": market_state.trend_score,
                    "volume_regime_score": market_state.volume_regime_score,
                    "breakout_strength": round(strength, 4),
                },
                "reason": (
                    f"ORB {direction.lower()} breakout of "
                    f"[{state.or_low:.2f}, {state.or_high:.2f}] "
                    f"({or_minutes}m range), trend_score={market_state.trend_score:.1f}, "
                    f"volume_regime_score={market_state.volume_regime_score:.1f}"
                ),
                "basis": "closed",  # §8 — no PriceSnapshot consumer wired yet;
                # every condition above was read off a settled FeatureSet.
            },
            setup_detected_at=features.candle_ts,  # §7 — never datetime.now()
        )
