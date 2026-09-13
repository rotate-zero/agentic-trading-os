"""Backtest Runner trigger route (decision #130) — the real, callable
HTTP entry point Backtest Runner v1 (decisions #120-#129) never had.
Before this route, `BacktestRunner` could only be constructed by writing
Python directly against its internal constructor args (`test_backtest_
runner.py`'s own fixture-construction helpers were the only working
example anywhere in the codebase). This route closes that gap: pick one
of the 7 real, live strategy definitions (`strategy_engine.scheduler.
default_registry()` — the same registry the live Scheduler itself
builds from, reused rather than re-derived) and one of a small, fixed
set of named fixture scenarios (`backtest_runner/scenarios.py`), and get
back the real `BacktestRunResult` produced by an unmodified engine
pipeline replay.

**What a 200 from this route proves, and does not prove — read before
treating any response as a trading signal.** Every scenario here is
either synthetic/hand-built (proving the plumbing: real Feature/Level-
Interaction/Market-State/Context/Strategy pipeline, real
`StrategyOutcomeRecord` persistence) or, for four of the seven
strategies, honestly incapable of ever firing at all today — see
`scenarios.py`'s own module docstring for exactly why
(`volume_regime_score`/`volatility_regime_score` are structurally always
`0.0` in any BacktestRunner replay, which ORB/Gap/Volume Spike/Momentum
all hard-gate MATCH on). `outcomes_recorded == 0` is therefore an
entirely expected, non-error response for many (strategy, scenario)
pairs, not a sign anything is broken — the same honesty
`fixture_provider.py` itself already models for its own data. Nothing
here indicates real strategy profitability regardless of the outcome.

**Latency.** Each replayed candle costs a real, measured ~1 second of
`EngineBackedReplayStateProducer` engine-settle time (genuine processing
time, not a tunable poll interval) — a 130-candle scenario is roughly a
130 second HTTP round trip. `engine_singleton_guard.py`'s `_RUN_LOCK`
also means concurrent calls to this route serialize within one worker
process, same as any two direct `BacktestRunner` callers would. Neither
is addressed here — see `replay_state_producer.py`/
`engine_singleton_guard.py` for the existing, unmodified behavior this
route simply inherits.

**Scope boundary, restated from this task's own brief.** This route
does not touch, and its own scenarios do not attempt to simulate,
sourcing real minute-level historical data — that remains the same
separate, larger, still-open prerequisite `fixture_provider.py`'s
module docstring already describes. Nor does it add any Performance
Analytics UI for inspecting results beyond the fields returned directly
below: a caller wanting the raw persisted rows can already query the
existing, unmodified `/intelligence/strategy-outcomes` route separately.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.backtest_runner.context_provider import FixtureBacktestContextProvider
from app.backtest_runner.fixture_provider import FixtureCandleProvider
from app.backtest_runner.runner import BacktestRunner
from app.backtest_runner.scenarios import available_scenarios, load_scenario_candles
from app.strategy_engine.scheduler import default_registry

router = APIRouter(prefix="/backtest", tags=["backtest"])

# Not versioned anywhere else in this codebase yet (runner.py's own
# module docstring: "Feature Engine has no versioning scheme of its own
# yet, so this module doesn't invent a silent default a future real-data
# run could forget to override") — BacktestRunner therefore takes
# data_version/feature_version from its caller with no default of its
# own, and this route is that caller. feature_version mirrors
# test_backtest_runner.py's own value, the only other place in the
# codebase that has ever had to pick one.
_FEATURE_VERSION = "feature_engine_v1"


def _validate_strategy_name(strategy_name: str) -> None:
    valid_names = sorted({s.name for s in default_registry(datetime.now(timezone.utc))})
    if strategy_name not in valid_names:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy_name {strategy_name!r}. Valid values: {valid_names}",
        )


def _validate_scenario(scenario: str) -> None:
    valid_scenarios = available_scenarios()
    if scenario not in valid_scenarios:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario {scenario!r}. Valid values: {valid_scenarios}",
        )


@router.post("/run")
async def run_backtest(
    strategy_name: str = Query(..., description="One of the 7 real v1 strategy names (see default_registry())."),
    symbol: str = Query(..., description="Arbitrary label for this run's outcome rows — not a real ticker lookup."),
    scenario: str = Query(
        ...,
        description=(
            "One of scenarios.py's named fixture scenarios — no default, must be explicit. "
            "Each is ~120-140 1-minute candles; see this route's own docstring for why that "
            "means a ~120-140 SECOND synchronous HTTP response, not milliseconds."
        ),
    ),
) -> dict[str, Any]:
    """Trigger one real `BacktestRunner` run against a named fixture
    scenario.

    **This call is fully synchronous and blocks for roughly real time,
    not request-processing time.** `EngineBackedReplayStateProducer`
    costs a measured, genuine ~1 second of engine-settle time per
    replayed candle (confirmed by direct timing, not estimated) — so
    this endpoint does not return until (scenario candle count) seconds
    have actually elapsed. A 130-candle scenario is a ~130 second HTTP
    round trip; all of this task's scenarios are 120-140 candles
    (~2-2.5 minutes), deliberately kept to the minimum each needed
    rather than padded to a full session, specifically because of this
    cost. This is a deliberate v1 trade-off, not an oversight: v1 has no
    background-job, polling, or webhook infrastructure, and none is
    added here — verified against this deployment's own stack (a single
    uvicorn worker, `Dockerfile`, no reverse proxy, no
    `--timeout-keep-alive` override, `docker-compose.yml`) to confirm
    nothing already in place here would truncate a multi-minute
    synchronous request; a different deployment (a proxy or load
    balancer with its own request timeout in front of this service)
    could behave differently, which this route has no way to know or
    control.

    **Do not call this against a live-trading process.**
    `engine_singleton_guard.py`'s `install_replay_engines()` (unchanged
    by this task) temporarily replaces the process's real
    Feature/LevelInteraction/MarketState/Context engine singletons with
    this run's fresh ones for the entire duration of the call — already
    documented there as unsafe to run concurrently with live trading in
    the same process. That risk isn't new here, but this route is the
    first thing that makes it directly, easily HTTP-reachable rather
    than requiring someone to write Python against internal constructor
    args, so it's worth restating plainly rather than leaving it
    findable only by reading that module.

    See this module's own docstring for what a response does and does
    not prove (several (strategy, scenario) pairs are expected to
    honestly return `outcomes_recorded=0`, not an error).

    Returns `BacktestRunResult`'s real fields verbatim
    (`run_id`/`sweep_id`/`outcomes_recorded`/`discarded_signals`) — no
    Performance Analytics wrapping, per this task's own scope boundary.
    """
    symbol = symbol.strip().upper()
    _validate_strategy_name(strategy_name)
    _validate_scenario(scenario)

    strategy = next(
        s for s in default_registry(datetime.now(timezone.utc)) if s.name == strategy_name
    )
    candles = load_scenario_candles(scenario)

    provider = FixtureCandleProvider.single(symbol, "1m", candles)
    runner = BacktestRunner(
        strategy=strategy,
        symbol=symbol,
        market_data_provider=provider,
        start=candles[0].candle_ts,
        end=candles[-1].candle_ts,
        context_provider=FixtureBacktestContextProvider(),
        data_version=f"fixture:{scenario}",
        feature_version=_FEATURE_VERSION,
    )
    result = await runner.run()
    return dataclasses.asdict(result)
