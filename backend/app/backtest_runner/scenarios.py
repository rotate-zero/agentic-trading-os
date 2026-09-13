"""Named fixture scenarios for the Backtest Runner trigger route
(decision #130) — the "a way to select which fixture scenario to run
against" this task's own brief asked for, resolved here as a small,
fixed set of scenarios checked into the repo, loaded via
`fixture_provider.load_fixture_candles_csv()`.

**Why per-strategy scenarios, not one generic session.** Saqib's own
call for this task: build fixture data that actually clears each real
strategy's MATCH condition wherever that's achievable, rather than a
single generic session that would almost never produce a real
`StrategyOutcomeRecord` for any of the 7 strategies. Every scenario
below was verified empirically — replayed through the real
`BacktestRunner` against the real strategy it names, not just reasoned
about from reading the strategy's source — before being checked in.

**The hard ceiling this had to design around, worth restating here since
it isn't visible from any individual scenario file.** `FeatureEngine`'s
`rvol`/`atr_14_pct` (and therefore `MarketState.volume_regime_score`/
`volatility_regime_score`) are populated exclusively from
`self._daily_candle_cache`, itself populated only by
`_maybe_refresh_daily_levels()` calling
`broker_registry.get_historical_provider().get_historical(symbol, "1d",
...)`. `EngineBackedReplayStateProducer` (`replay_state_producer.py`)
constructs a brand-new `FeatureEngine` with no historical provider wired
in at all, and `BacktestRunner` never touches `broker_registry` — so
`volume_regime_score`/`volatility_regime_score` are structurally always
`0.0` for ANY BacktestRunner replay today, for any symbol, regardless of
how the fixture candles are built. Confirmed by direct execution (a
60-candle synthetic uptrend held `volume_regime_score` at `0.00` the
entire replay), not just by reading the code.

Checked against every real strategy's actual MATCH-stage code (not
docstrings):

  - **ORB, Gap, Volume Spike, Momentum** all hard-gate MATCH on
    `volume_regime_score >= threshold` (45.0 by default) — structurally
    unreachable via BacktestRunner today. No amount of candle
    engineering changes this; it needs a fixture daily-history seam
    wired into the replay stack, which is real, separate, follow-on
    work (flagged in decision #130, not attempted here — out of scope
    for "a route + strategy lookup").
  - **First Pullback, Reversal, VWAP** gate MATCH only on `trend_score`
    (established or neutral) and a real `LevelInteractionEngine`
    touch/resolution — neither depends on the daily-candle cache, so
    these three CAN genuinely fire, and each has its own guaranteed-fire
    scenario below.

So `SCENARIOS` intentionally holds one honestly-labeled fallback
(`volume_gated_baseline`) alongside three guaranteed-fire scenarios,
rather than four more fake-precision "shaped" scenarios that would
never fire anyway (a breakout-shaped candle set for ORB is no more
likely to fire than a flat one, given the gate itself is unreachable —
building one would look like an attempt, not an honest acknowledgment).

**Any (strategy_name, scenario) pair is accepted by the route** — this
module doesn't enforce "only run FirstPullback against
first_pullback_vwap_dip." Running, say, Momentum against
`vwap_neutral_conquest` is a perfectly valid call; it will just honestly
report `outcomes_recorded=0`, same as running any of the four
volume-gated strategies against anything. The per-strategy names below
describe what each scenario was BUILT to demonstrate, not a restriction.

**Timing, worth knowing before picking a scenario.** Each replayed
candle costs a real, measured ~1 second of wall-clock time inside
`EngineBackedReplayStateProducer` (genuine per-candle engine settle
time, not a configurable poll interval) — so a 130-candle scenario is a
~130 second HTTP round trip. Scenario lengths below are deliberately
kept to the minimum each one was verified to need (with a modest
buffer), not padded to a full trading session, specifically because of
this cost.
"""
from __future__ import annotations

from pathlib import Path

from app.backtest_runner.fixture_provider import load_fixture_candles_csv
from app.broker_adapters.base import Candle

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

# name -> (csv filename, one-line description shown in the route's 400
# error when an unknown scenario is requested).
_SCENARIO_FILES: dict[str, tuple[str, str]] = {
    "first_pullback_vwap_dip": (
        "first_pullback_vwap_dip.csv",
        "130 candles (~130s to run). Established uptrend + a single "
        "engineered dip into and rejected back out of VWAP's aura band "
        "— verified to fire FirstPullback (BUY, target hit).",
    ),
    "reversal_vwap_break": (
        "reversal_vwap_break.csv",
        "140 candles (~140s to run). Established uptrend + a genuine "
        "break-through of VWAP (conquered, not rejected) — verified to "
        "fire Reversal (SELL, stopped out).",
    ),
    "vwap_neutral_conquest": (
        "vwap_neutral_conquest.csv",
        "140 candles (~140s to run). Flat/choppy session (trend_score "
        "held neutral) with a genuine VWAP conquest partway through — "
        "verified to fire VWAP (SELL, target hit).",
    ),
    "volume_gated_baseline": (
        "volume_gated_baseline.csv",
        "120 candles (~120s to run). Generic moderate-uptrend session, "
        "NOT engineered to trigger any particular strategy. The only "
        "scenario available for ORB/Gap/Volume Spike/Momentum, whose "
        "MATCH conditions hard-gate on volume_regime_score — see this "
        "module's own docstring for why BacktestRunner can never "
        "produce a non-zero value for that today. Verified to run "
        "cleanly (outcomes_recorded=0, no discarded signals, no errors) "
        "against all four.",
    ),
}


def available_scenarios() -> dict[str, str]:
    """name -> description, for building a clear 400 error message."""
    return {name: desc for name, (_, desc) in _SCENARIO_FILES.items()}


def load_scenario_candles(name: str) -> list[Candle]:
    """Raises `KeyError` for an unknown scenario name — callers (the
    route) are expected to validate against `available_scenarios()`
    first and turn that into an honest 400, not let this raise past
    them."""
    filename, _description = _SCENARIO_FILES[name]
    return load_fixture_candles_csv(_FIXTURES_DIR / filename)
