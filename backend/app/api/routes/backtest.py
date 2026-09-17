"""Backtest Runner trigger route (decision #131) — the real, callable
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
synthetic/hand-built — proving the plumbing (real Feature/Level-
Interaction/Market-State/Context/Strategy pipeline, real
`StrategyOutcomeRecord` persistence), never real strategy profitability,
regardless of the outcome. As of decision #135,
`volume_regime_score`/`volatility_regime_score` are no longer
structurally `0.0` in every replay (see `historical_provider_guard.py`
and `fixture_daily_history.py`) — but that only means ORB/Gap/Volume
Spike/Momentum's `volume_regime_score >= 45.0` MATCH gate can now
genuinely be cleared by REAL price/volume action during a replay; it
does not mean the existing named scenarios were built to clear it (see
`scenarios.py`'s own module docstring for the honest, checked answer on
which of the four currently do). `outcomes_recorded == 0` remains an
expected, non-error response for a (strategy, scenario) pair that
genuinely never sets up the conditions a strategy's MATCH stage needs —
not a sign anything is broken, the same honesty `fixture_provider.py`
itself already models for its own data.

**Backtest persistence is namespaced from live state (D18).** The
run-scoped Feature, Level Interaction, and Market State engines resolve
`symbols` through `is_backtest=True`; every live call site explicitly
uses `is_backtest=False`. `daily_levels_state` carries the same namespace
and a composite foreign key prevents a row from being attached to a
symbol of the opposite origin. A caller may therefore replay the exact
same ticker that live trading tracks without either side reading or
mutating the other's rows. Backtest Feature Engines also bypass the live
restart-survival short-circuit at the start of every run: each replay
fetches its fixture daily history and fills the raw-candle cache, so an
identical second run retains real `volume_regime_score` and
`volatility_regime_score` values instead of reverting both to `0.0`.

**Latency.** Each replayed candle costs a real, measured ~1 second of
`EngineBackedReplayStateProducer` engine-settle time (genuine processing
time, not a tunable poll interval) — a 130-candle scenario is roughly a
130 second HTTP round trip. `engine_singleton_guard.py`'s `_RUN_LOCK`
also means concurrent calls to this route serialize within one worker
process, same as any two direct `BacktestRunner` callers would. Neither
is addressed here — see `replay_state_producer.py`/
`engine_singleton_guard.py` for the existing, unmodified behavior this
route simply inherits.

**Two deliberately separate paths.** `POST /backtest/run` below remains
the named, synthetic-fixture regression path and its frontend contract is
unchanged. `POST /backtest/run/ibkr` acquires real IBKR historical OHLCV
first, disconnects the isolated read-only acquisition adapter, then replays
from a run-scoped in-memory provider. Neither path supplies historical
point-in-time fundamentals or news; both keep the existing replay-safe
FixtureBacktestContextProvider calendar behavior and honest absence for
those fields.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.routes import broker, finnhub_data, market_data
from app.backtest_runner.context_provider import FixtureBacktestContextProvider
from app.backtest_runner.fixture_daily_history import build_daily_history_candles
from app.backtest_runner.fixture_provider import FixtureCandleProvider
from app.backtest_runner.ibkr_historical import (
    IBKRHistoricalAcquisitionError,
    acquire_ibkr_replay_data,
)
from app.backtest_runner.runner import BacktestRunner
from app.backtest_runner.scenarios import available_scenarios, load_scenario_candles
from app.core.config import get_settings
from app.core.market_clock import get_market_clock
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
_MAX_IBKR_REPLAY_WINDOW = timedelta(hours=24)


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


def _reject_if_live_data_connected() -> None:
    """Decision #132. This route's own docstring already warned that
    `install_replay_engines()` swapping the process-wide Feature/Level-
    Interaction/MarketState/Context singletons is unsafe to run
    concurrently with live trading — but nothing enforced that until
    now. Since this deployment is a single always-on process (no
    per-request worker isolation — see this route's own docstring), the
    provider modules' existing connection state is the safety signal.
    Finnhub and Polygon expose their status directly; `broker.py` checks
    registry-owned IBKR adapters in either role. The isolated historical
    acquisition adapter is never registered and is therefore not mistaken
    for a live provider."""
    if finnhub_data.is_connected():
        raise HTTPException(
            status_code=409,
            detail=(
                "Refusing to run a backtest: Finnhub is currently connected. "
                "This route temporarily replaces the live Feature/LevelInteraction/"
                "MarketState/Context engine singletons for its full duration, which "
                "would corrupt live state. Disconnect Finnhub first if this is not "
                "a live-trading process."
            ),
        )
    if market_data.is_connected():
        raise HTTPException(
            status_code=409,
            detail=(
                "Refusing to run a backtest: Polygon is currently connected. "
                "This route temporarily replaces the live Feature/LevelInteraction/"
                "MarketState/Context engine singletons for its full duration, which "
                "would corrupt live state. Disconnect Polygon first if this is not "
                "a live-trading process."
            ),
        )
    if broker.is_connected():
        raise HTTPException(
            status_code=409,
            detail=(
                "Refusing to run a backtest: IBKR is currently connected in the live "
                "broker registry. This route temporarily replaces process-wide replay "
                "engines and the historical-provider role. Disconnect IBKR first."
            ),
        )


def _configuration_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "ibkr_backtest_not_configured", "message": message},
    )


def _ibkr_backtest_client_id() -> int:
    settings = get_settings()
    raw = settings.ibkr_backtest_client_id
    if raw is None or not str(raw).strip():
        raise _configuration_error(
            "IBKR_BACKTEST_CLIENT_ID is required for POST /backtest/run/ibkr. "
            "Set an explicit client ID that differs from IBKR_CLIENT_ID."
        )
    try:
        client_id = int(str(raw).strip())
    except ValueError as exc:
        raise _configuration_error(
            "IBKR_BACKTEST_CLIENT_ID must be a non-negative integer distinct from IBKR_CLIENT_ID."
        ) from exc
    if client_id < 0:
        raise _configuration_error("IBKR_BACKTEST_CLIENT_ID must be non-negative.")
    if client_id == settings.ibkr_client_id:
        raise _configuration_error(
            "IBKR_BACKTEST_CLIENT_ID must differ from IBKR_CLIENT_ID; isolated and live "
            "connections cannot share a client ID."
        )
    return client_id


def _validate_ibkr_range(
    symbol: str,
    start: datetime,
    end: datetime,
) -> tuple[str, datetime, datetime]:
    symbol = symbol.strip().upper()
    if not symbol:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_backtest_request", "message": "symbol must not be empty"},
        )
    if start.tzinfo is None or start.utcoffset() is None or end.tzinfo is None or end.utcoffset() is None:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_backtest_request",
                "message": "start and end must be timezone-aware ISO-8601 datetimes",
            },
        )
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    if start >= end:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_backtest_request", "message": "start must be before end"},
        )
    if end - start > _MAX_IBKR_REPLAY_WINDOW:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_backtest_request",
                "message": (
                    "The requested replay window exceeds the v1 maximum of 24 elapsed hours; "
                    "the range is rejected and will not be clamped."
                ),
            },
        )
    return symbol, start, end


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

    **Refuses to run against a live-trading process (decision #132).**
    `engine_singleton_guard.py`'s `install_replay_engines()` (unchanged
    by this task) temporarily replaces the process's real
    Feature/LevelInteraction/MarketState/Context engine singletons with
    this run's fresh ones for the entire duration of the call — already
    documented there as unsafe to run concurrently with live trading in
    the same process. That risk isn't new here, but this route was the
    first thing that made it directly, easily HTTP-reachable rather than
    requiring someone to write Python against internal constructor args.
    As of decision #132, this is enforced rather than merely documented:
    a Finnhub or Polygon connection currently live in this process
    causes a `409` before any engine is touched, not just a warning to
    read here. The IBKR historical replay delivery extends that same
    guard to registry-owned IBKR connections. As of decision #135, the
    same call also temporarily replaces `broker_registry`'s historical-role provider
    (`historical_provider_guard.py`) — this check's existing Polygon gate
    already covers that specifically (`market_data.py`'s own `connect()`
    route is the one that calls `broker_registry.set_historical_provider()`;
    Finnhub only ever claims the streaming role, confirmed by reading
    `finnhub_data.py` directly — its own connection state genuinely has
    no bearing on the historical role, this check just also happens to
    gate on it for the pre-existing engine-singleton reason above). See
    `historical_provider_guard.py`'s own module docstring for the current
    three-provider safety boundary.

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
    _reject_if_live_data_connected()

    strategy = next(
        s for s in default_registry(datetime.now(timezone.utc)) if s.name == strategy_name
    )
    candles = load_scenario_candles(scenario)

    # (symbol, "1d") entry added alongside the replay's own (symbol, "1m")
    # entry, decision #135 — the SAME provider instance now also serves
    # as broker_registry's historical role for FeatureEngine's Daily
    # Levels/ATR/RVOL refresh (BacktestRunner.run() installs it; see
    # historical_provider_guard.py). Anchored to the scenario's own
    # first replayed trading day, not to `symbol` — see
    # fixture_daily_history.py's own docstring for why.
    first_day = get_market_clock().trading_day(candles[0].candle_ts)
    daily_candles = build_daily_history_candles(before=first_day)
    provider = FixtureCandleProvider({(symbol, "1m"): candles, (symbol, "1d"): daily_candles})
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


@router.post("/run/ibkr")
async def run_ibkr_backtest(
    strategy_name: str = Query(..., description="One of the 7 real v1 strategy names."),
    symbol: str = Query(..., description="US stock symbol resolved through IBKR SMART/USD."),
    start: datetime = Query(..., description="Timezone-aware ISO-8601 inclusive start."),
    end: datetime = Query(..., description="Timezone-aware ISO-8601 exclusive end."),
) -> dict[str, Any]:
    """Acquire real IBKR candles, disconnect, then replay synchronously.

    The user interval is exact ``[start, end)`` and is limited to 24
    elapsed hours. Feature Engine's configured 1m premarket and 1d Daily
    Levels/ATR/RVOL lookbacks are acquired in addition to that interval
    and are not subject to the 24-hour cap. At roughly one second per
    primary candle, a regular session takes about 6.5 minutes and a full
    04:00-20:00 extended session can approach 16 minutes.
    """
    _validate_strategy_name(strategy_name)
    symbol, start, end = _validate_ibkr_range(symbol, start, end)
    client_id = _ibkr_backtest_client_id()
    _reject_if_live_data_connected()

    settings = get_settings()
    try:
        dataset = await acquire_ibkr_replay_data(
            symbol=symbol,
            start=start,
            end=end,
            host=settings.ibkr_host,
            port=settings.ibkr_port,
            client_id=client_id,
            daily_lookback_days=settings.daily_levels_lookback_days,
            premarket_lookback_days=settings.feature_engine_premarket_lookback_days,
            market_timezone=settings.market_timezone,
        )
    except IBKRHistoricalAcquisitionError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    # A live provider may have connected during the network acquisition.
    # Re-check after the isolated adapter is gone and before any process-
    # wide replay singleton or database row is touched.
    _reject_if_live_data_connected()

    strategy = next(
        s for s in default_registry(datetime.now(timezone.utc)) if s.name == strategy_name
    )
    runner = BacktestRunner(
        strategy=strategy,
        symbol=symbol,
        market_data_provider=dataset.provider,
        start=start,
        end=end,
        context_provider=FixtureBacktestContextProvider(),
        data_version=dataset.data_version,
        feature_version=_FEATURE_VERSION,
    )
    result = await runner.run()
    return dataclasses.asdict(result)
