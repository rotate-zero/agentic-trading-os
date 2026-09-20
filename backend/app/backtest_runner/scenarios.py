"""Named fixture scenarios for the Backtest Runner trigger route
(decision #131) — the "a way to select which fixture scenario to run
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

**The hard ceiling this used to design around — resolved by decision
#135, restated here for history since it explains why the scenarios
below look the way they do.** `FeatureEngine`'s `rvol`/`atr_14_pct` (and
therefore `MarketState.volume_regime_score`/`volatility_regime_score`)
are populated exclusively from `self._daily_candle_cache`, itself
populated only by `_maybe_refresh_daily_levels()` calling
`broker_registry.get_historical_provider().get_historical(symbol, "1d",
...)`. Before decision #135, `BacktestRunner` never touched
`broker_registry` at all, so these two scores were structurally always
`0.0` for ANY BacktestRunner replay, for any symbol, regardless of how
the fixture candles were built — confirmed by direct execution at the
time (a 60-candle synthetic uptrend held `volume_regime_score` at `0.00`
the entire replay). Decision #135 closed that specific gap
(`historical_provider_guard.py`/`fixture_daily_history.py`): a real
synthetic daily-candle history is now installed as `broker_registry`'s
historical role for every replay, so these two scores are no longer
structurally zero.

**What that does, and does NOT, mean for the four volume-gated
strategies — checked by direct execution against decision #135's own
seam, not assumed.**

  - **ORB, Gap, Volume Spike, Momentum** all hard-gate MATCH on
    `volume_regime_score >= threshold` (45.0 by default). That gate can
    now genuinely be cleared — it is no longer the structural ceiling it
    was. Checked directly, one strategy at a time, against the existing
    `volume_gated_baseline` scenario with decision #135's seam active:
    **Momentum now genuinely fires** (`outcomes_recorded=1`) — an
    unintended side effect of the specific daily-history numbers
    `fixture_daily_history.py` happens to generate, not something
    engineered to make it fire, and not something to rely on (a future
    change to that generator's numbers could un-fire it just as
    accidentally). **ORB, Gap, and Volume Spike still return
    `outcomes_recorded=0`** against this scenario — not because of the
    volume gate anymore, but because `volume_gated_baseline`'s 1m candle
    shape was never built to satisfy any of their OTHER MATCH conditions
    (no real opening-range breakout shape for ORB, no overnight gap for
    Gap, no spike-shaped volume burst for Volume Spike — all
    orthogonal to `volume_regime_score`).
  - **Building new, deliberately-shaped scenarios to guarantee ORB/Gap/
    Volume Spike each fire is explicitly out of scope for decision
    #135** — flagged there as real, separate, follow-on work (which
    strategy gets which scenario, what magnitude of price/volume shape
    to target, whether shared or per-strategy) with its own judgment
    calls, same as it was flagged out of scope for decision #131 before
    it.
  - **First Pullback, Reversal, VWAP** gate MATCH only on `trend_score`
    (established or neutral) and a real `LevelInteractionEngine`
    touch/resolution — neither depends on the daily-candle cache, so
    these three could always genuinely fire, and each has its own
    guaranteed-fire scenario below, unaffected by decision #135 either
    way.

So `SCENARIOS` still holds one honestly-labeled fallback
(`volume_gated_baseline`) alongside three guaranteed-fire scenarios —
"fallback" now means "not purpose-built to guarantee a fire for any
particular strategy," not "structurally can never fire," which was the
accurate label before decision #135 and would now be misleading.

**Any (strategy_name, scenario) pair is accepted by the route** — this
module doesn't enforce "only run FirstPullback against
first_pullback_vwap_dip." Running, say, Momentum against
`vwap_neutral_conquest` is a perfectly valid call; it will just honestly
report `outcomes_recorded=0`, same as running any of the four
volume-gated strategies against anything. The per-strategy names below
describe what each scenario was BUILT to demonstrate, not a restriction.

**Timing, worth knowing before picking a scenario.** Replay settlement
uses exact engine/bus queue completion and does not pay Market State's
live one-second debounce floor per candle. Scenario lengths below remain
deliberately small and reviewable; the HTTP route is still synchronous.
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
        "scenario available for ORB/Gap/Volume Spike/Momentum. As of "
        "decision #135, volume_regime_score is no longer structurally "
        "zero — checked directly against all four: Momentum now "
        "genuinely fires here (an unintended side effect of the daily-"
        "history fixture's numbers, not something engineered), ORB/Gap/"
        "Volume Spike still return outcomes_recorded=0, for reasons "
        "unrelated to the volume gate — see this module's own docstring.",
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
