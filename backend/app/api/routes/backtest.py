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

**Latency.** Replay settlement uses exact engine/bus queue completion,
not Market State's live one-second debounce floor, so it no longer costs
roughly one wall-clock second per candle. This remains a synchronous HTTP
route, and `engine_singleton_guard.py`'s `_RUN_LOCK` still serializes
concurrent calls within one worker process. Background jobs, progress,
cancellation, and parallel replay remain separate future work.

**Two deliberately separate paths.** `POST /backtest/run` below remains
the named, synthetic-fixture regression path and its frontend contract is
unchanged. `POST /backtest/run/ibkr` acquires real IBKR historical OHLCV
first, disconnects the isolated read-only acquisition adapter, then replays
from a run-scoped in-memory provider. Neither path supplies historical
point-in-time fundamentals or news; both keep the existing replay-safe
FixtureBacktestContextProvider calendar behavior and honest absence for
those fields.

**A stored-candle path, additive: `POST /backtest/run/stored`.** Replays
the same runner over candles `CandleRecorder` already recorded in
PostgreSQL (non-backtest `Symbol` namespace only), with no IBKR or other
external provider. Recorded 1m/1d warm-up is read in the same worker-owned
snapshot and served from a run-scoped in-memory provider; synthetic daily
history is never substituted. Same two caveats as above — no historical
fundamentals/news — plus: a window with no recorded candles is a
structured 422, and `outcomes_recorded == 0` is a valid result. See
`backtest_runner/stored_history.py`.

**A third path, additive: `POST /backtest/sweep`.** One strategy across
an explicit cross-product of symbols × fixture scenarios, run
sequentially through this exact same `BacktestRunner`/`_RUN_LOCK` path,
sharing one real `sweep_id` instead of each run minting its own ("a
sweep of one," per decision #155's note on the schema). Fixture-only —
deliberately excludes `/run/ibkr`'s real-data path, since a single IBKR
acquisition already costs real minutes and looping that synchronously
inside one request would be impractical. See the route's own docstring
for scope, batch-size bound, and partial-failure handling.

**A fourth path, additive: `POST /backtest/sweep/stored`.** One strategy
across an explicit list of symbols over one shared UTC interval, each symbol
replayed from the candles `CandleRecorder` already recorded in PostgreSQL
(the `/run/stored` acquisition, reused unchanged), sequentially, sharing one
real `sweep_id`. It is the stored-candle counterpart of `/sweep` (which is
fixture-only). See the route's own docstring for the validation, per-symbol
failure and provenance rules, including what a failed symbol does and does not
tell you about records it may have left behind.
"""
from __future__ import annotations

import dataclasses
import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
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
from app.backtest_runner.runner import BacktestRunner, DiscardedSignal
from app.backtest_runner.scenarios import available_scenarios, load_scenario_candles
from app.backtest_runner.stored_history import (
    STORED_DATA_VERSION,
    StoredHistoryError,
    acquire_stored_replay_data,
)
from app.backtest_runner.stored_coverage import acquire_stored_coverage
from app.core.config import get_settings
from app.core.market_clock import get_market_clock
from app.strategy_engine.scheduler import default_registry

router = APIRouter(prefix="/backtest", tags=["backtest"])
logger = logging.getLogger(__name__)

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

# Saqib-approved bound (see this delivery's decision-log entry for the
# reasoning): len(symbols) * len(scenarios) must not exceed this before
# any run starts. Measured ~2.01s per fixture run (130-candle scenario,
# real local Postgres) makes 20 sequential runs ≈ 40s of wall clock for
# the whole synchronous call — a measured fact about THIS environment,
# not a guarantee for any deployment or proxy timeout.
_MAX_SWEEP_PAIRS = 20


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
            "Each is ~120-140 1-minute candles; replay is synchronous but uses exact queue "
            "settlement rather than waiting for the live debounce floor."
        ),
    ),
) -> dict[str, Any]:
    """Trigger one real `BacktestRunner` run against a named fixture
    scenario.

    **This call is fully synchronous.** Replay settlement uses exact
    EventBus and engine-worker queue completion, including Market State's
    replay-only immediate settlement; it does not wait for the live
    one-second debounce floor. v1 still has no background-job, progress,
    cancellation, polling, or webhook infrastructure, and the process-wide
    replay guards still serialize runs. A different deployment's request
    timeout remains outside this route's control.

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


@router.get("/stored-coverage")
async def get_stored_coverage(
    symbol: str = Query(..., description="Recorded ticker to inspect."),
    start: datetime = Query(..., description="Timezone-aware ISO-8601 inclusive start."),
    end: datetime = Query(..., description="Timezone-aware ISO-8601 exclusive end."),
) -> dict[str, Any]:
    """Informational row counts in the exact replay interval and warm-up windows."""
    symbol, start, end = _validate_ibkr_range(symbol, start, end)
    settings = get_settings()
    try:
        coverage = await acquire_stored_coverage(
            symbol=symbol, start=start, end=end,
            daily_lookback_days=settings.daily_levels_lookback_days,
            premarket_lookback_days=settings.feature_engine_premarket_lookback_days,
        )
    except StoredHistoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail={"code": exc.code, "message": exc.message}) from exc
    return coverage.as_dict()


@router.post("/run/stored")
async def run_stored_backtest(
    strategy_name: str = Query(..., description="One of the 7 real v1 strategy names."),
    symbol: str = Query(..., description="Ticker whose candles were already recorded in this database."),
    start: datetime = Query(..., description="Timezone-aware ISO-8601 inclusive start."),
    end: datetime = Query(..., description="Timezone-aware ISO-8601 exclusive end."),
) -> dict[str, Any]:
    """Replay the existing runner over already-recorded PostgreSQL candles.

    The user interval is exact ``[start, end)`` and uses the same
    validation as ``/run/ibkr`` (timezone-aware, ``start < end``, at most
    24 elapsed hours, symbol stripped and upper-cased). Candles come only
    from the non-backtest ``Symbol`` namespace. Configured 1m pre-market and
    1d Daily Levels lookbacks are read as *available* warm-up in addition
    to the interval and are not subject to the 24-hour cap; nothing at or
    after ``end`` is read and no synthetic history is added. The read runs
    in a worker thread that owns its database session and finishes before
    replay; replay then goes through the unchanged runner with a run-scoped
    in-memory provider.

    Provenance: the run's ``data_version`` is ``stored:postgres:candles:1m-1d``.
    Errors are ``{"code", "message"}`` details: ``stored_candles_no_data``
    (422, nothing recorded in the exact interval), ``stored_candles_malformed``
    (422), ``stored_history_unavailable`` (503), plus the shared
    ``invalid_backtest_request`` (422) and the live-provider 409 guard,
    which is checked again after the read and before any replay engine or
    database row of the run is touched.

    Synthetic or sparse stored data proves plumbing only, never profitability;
    ``outcomes_recorded == 0`` is a valid result.
    """
    _validate_strategy_name(strategy_name)
    symbol, start, end = _validate_ibkr_range(symbol, start, end)
    _reject_if_live_data_connected()

    settings = get_settings()
    try:
        dataset = await acquire_stored_replay_data(
            symbol=symbol,
            start=start,
            end=end,
            daily_lookback_days=settings.daily_levels_lookback_days,
            premarket_lookback_days=settings.feature_engine_premarket_lookback_days,
        )
    except StoredHistoryError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    # A live provider may have connected while the read was in flight.
    # Re-check before any process-wide replay singleton or database row of
    # the run is touched.
    _reject_if_live_data_connected()

    strategy = next(
        s for s in default_registry(datetime.now(timezone.utc)) if s.name == strategy_name
    )
    logger.info(
        "stored backtest: symbol=%s primary=%d warmup_1m=%d daily=%d",
        symbol,
        dataset.primary_candle_count,
        dataset.warmup_minute_candle_count,
        dataset.daily_candle_count,
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


# --- POST /backtest/sweep --------------------------------------------------


@dataclass(frozen=True)
class SweepPairResult:
    """One (symbol, scenario) pair's outcome within a sweep. `run_id` is
    `None` only on a genuine per-pair failure — `BacktestRunner.run()`
    only returns its internally-minted `run_id` on a successful return,
    and this route does not thread `run_id` in (only `sweep_id` — see
    `runner.py`'s own docstring for why that's the one small, additive
    change this task asked for). A `BacktestRunRecord` row may still
    exist in the database for a failed attempt (it's written before
    replay starts, per `runner.py`'s own ordering comment) — its
    `run_id` just isn't retrievable from this response without a larger
    change to the runner's return contract than this task's scope."""

    symbol: str
    scenario: str
    run_id: UUID | None
    outcomes_recorded: int | None
    discarded_signals: list[DiscardedSignal]
    error: str | None


@dataclass(frozen=True)
class BacktestSweepResult:
    sweep_id: UUID
    strategy_name: str
    pairs_requested: int
    pairs_succeeded: int
    pairs_failed: int
    runs: list[SweepPairResult] = field(default_factory=list)


def _validate_sweep_symbols(symbols: list[str]) -> list[str]:
    """No real symbol registry exists to validate against — confirmed
    directly: `/backtest/run`'s own `symbol` param is documented as "an
    arbitrary label for this run's outcome rows, not a real ticker
    lookup," and that stays true here. Normalizing (`strip().upper()`,
    matching `/run`'s own convention) and rejecting empty labels after
    normalization is the full extent of what's reasonable to validate."""
    if not symbols:
        raise HTTPException(status_code=422, detail="symbols must contain at least one value")
    normalized = [s.strip().upper() for s in symbols]
    if any(not s for s in normalized):
        raise HTTPException(status_code=422, detail="symbols must not contain empty values")
    return normalized


def _validate_sweep_scenarios(scenarios: list[str]) -> list[str]:
    if not scenarios:
        raise HTTPException(status_code=422, detail="scenarios must contain at least one value")
    for scenario in set(scenarios):
        _validate_scenario(scenario)
    return scenarios


@router.post("/sweep")
async def run_backtest_sweep(
    strategy_name: str = Query(..., description="One of the 7 real v1 strategy names (see default_registry())."),
    symbols: list[str] = Query(
        ...,
        description=(
            "Explicit list of symbol labels to sweep, e.g. ?symbols=AAPL&symbols=MSFT. "
            "No implicit 'all known symbols' expansion — say exactly what to sweep."
        ),
    ),
    scenarios: list[str] = Query(
        ...,
        description=(
            "Explicit list of scenarios.py names to sweep, e.g. ?scenarios=vwap_neutral_conquest. "
            "No implicit 'all scenarios' expansion."
        ),
    ),
) -> dict[str, Any]:
    """Run one strategy across the explicit cross-product of `symbols` ×
    `scenarios`, sequentially, sharing one real `sweep_id` across every
    resulting `backtests` row — the real batch caller `sweep_id` has been
    waiting for since Backtest Runner v1 (decision #128's schema; #155's
    own "a sweep of one" note).

    **Fixture-only, deliberately.** Excludes `/run/ibkr`'s real-data
    path — a single IBKR acquisition already costs real minutes (up to
    ~16 per decision #145); looping that synchronously inside one HTTP
    request would be impractical. Use `/run/ibkr` directly per symbol
    for real-data backtests.

    **Scope, confirmed with Saqib before building.** The cross-product of
    both lists, both explicit and required — no implicit "all known
    symbols" or "all scenarios" expansion, since that could silently
    balloon into a very large sequential run. Requested-pair ordering is
    `symbols` outer / `scenarios` inner (`itertools.product(symbols,
    scenarios)`), preserved exactly in the response's `runs` list — never
    reordered by database or collection behavior — so each result lines
    up with the request that produced it without relying on `run_id`
    lookups.

    **Batch bound.** `len(symbols) * len(scenarios)` must not exceed
    `_MAX_SWEEP_PAIRS` (20); the request is rejected before any run
    starts if it does. Measured ~2.01s per fixture run in this
    environment (real local Postgres, 130-candle scenario) made 20 a
    reasonable synchronous v1 bound — stated as a measured fact about
    this environment, not a guarantee for any deployment or proxy
    timeout. The limit is on requested pairs, not successful runs.

    **Execution model — reuses `/backtest/run`'s own path exactly, one
    pair at a time.** No new locking mechanism: each pair goes through
    the identical `BacktestRunner.run()` → `install_replay_engines()` →
    `engine_singleton_guard._RUN_LOCK` path `/backtest/run` already uses,
    acquiring and releasing that same process-wide lock once per run —
    true parallelism was never on the table, per that lock's own
    docstring on why concurrent runs in one process are unsafe by
    design. A fresh `Strategy` instance is built for every pair (fresh
    `default_registry()` call, same as `/backtest/run` does per request)
    rather than reused across the loop: confirmed directly that all 7
    real strategies hold `self._state: dict[str, ...]` keyed per symbol
    — reusing one instance across pairs would let one pair's state (e.g.
    ORB's opening-range candle count) leak into another pair that
    happens to reuse the same symbol against a different scenario.

    **Live-trading guard, checked once before the sweep starts, not
    per-pair.** Same reasoning `/backtest/run` itself already rests on:
    a single run's own several-second replay never re-checks mid-run
    either, so treating a sequence of those same runs identically is
    consistent, not a new weaker standard invented for sweeps.

    **Partial-failure handling.** Pre-execution validation (strategy
    name, every requested scenario name, batch size, non-empty lists)
    rejects the whole request before any run starts. Once execution
    begins, a genuine per-pair error (as opposed to an honest
    `outcomes_recorded=0`, which is expected and not an error) is
    caught, logged, and recorded against that pair — remaining pairs
    still run, and every already-completed pair's result is still
    returned. This mirrors an existing, real convention in this
    codebase for independent-item batches: `FeatureEngine._worker_loop`
    ("one bad symbol/candle must not stall the other ~100") and
    `websocket/manager.py`'s broadcast ("a dead socket must not break
    the broadcast") both already establish "one item's failure doesn't
    sink the batch" as this project's own precedent — applied here
    rather than invented fresh.
    """
    _validate_strategy_name(strategy_name)
    symbols = _validate_sweep_symbols(symbols)
    scenarios = _validate_sweep_scenarios(scenarios)

    pairs = list(itertools.product(symbols, scenarios))
    if len(pairs) > _MAX_SWEEP_PAIRS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Requested {len(pairs)} (symbol, scenario) pairs "
                f"({len(symbols)} symbols × {len(scenarios)} scenarios), exceeding the "
                f"maximum of {_MAX_SWEEP_PAIRS} per sweep. Split this into multiple "
                f"requests, or request fewer symbols/scenarios."
            ),
        )

    _reject_if_live_data_connected()

    sweep_id = uuid4()
    runs: list[SweepPairResult] = []

    for symbol, scenario in pairs:
        try:
            strategy = next(
                s for s in default_registry(datetime.now(timezone.utc)) if s.name == strategy_name
            )
            candles = load_scenario_candles(scenario)
            first_day = get_market_clock().trading_day(candles[0].candle_ts)
            daily_candles = build_daily_history_candles(before=first_day)
            provider = FixtureCandleProvider({(symbol, "1m"): candles, (symbol, "1d"): daily_candles})
            pair_runner = BacktestRunner(
                strategy=strategy,
                symbol=symbol,
                market_data_provider=provider,
                start=candles[0].candle_ts,
                end=candles[-1].candle_ts,
                context_provider=FixtureBacktestContextProvider(),
                data_version=f"fixture:{scenario}",
                feature_version=_FEATURE_VERSION,
                sweep_id=sweep_id,
            )
            result = await pair_runner.run()
        except Exception as exc:  # noqa: BLE001 — one bad pair must not sink the rest of the sweep
            logger.exception(
                "backtest sweep: pair (symbol=%r, scenario=%r) failed under sweep_id=%s",
                symbol,
                scenario,
                sweep_id,
            )
            runs.append(
                SweepPairResult(
                    symbol=symbol,
                    scenario=scenario,
                    run_id=None,
                    outcomes_recorded=None,
                    discarded_signals=[],
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            runs.append(
                SweepPairResult(
                    symbol=symbol,
                    scenario=scenario,
                    run_id=result.run_id,
                    outcomes_recorded=result.outcomes_recorded,
                    discarded_signals=result.discarded_signals,
                    error=None,
                )
            )

    pairs_failed = sum(1 for r in runs if r.error is not None)
    sweep_result = BacktestSweepResult(
        sweep_id=sweep_id,
        strategy_name=strategy_name,
        pairs_requested=len(pairs),
        pairs_succeeded=len(pairs) - pairs_failed,
        pairs_failed=pairs_failed,
        runs=runs,
    )
    return dataclasses.asdict(sweep_result)


# --- POST /backtest/sweep/stored -------------------------------------------


@dataclass(frozen=True)
class StoredSweepSymbolError:
    """Why one symbol of a stored sweep produced no result.

    ``stage`` is the honest statement of what may have been left behind:

    * ``"before_replay"`` — the failure happened before a ``BacktestRunner``
      was run for this symbol (the live-provider guard, the recorded-candle
      read, or construction). ``BacktestRunner.run()`` is what writes the
      ``backtests`` row, and it was never started, so no run row and no
      outcome row exists for this symbol (asserted by the route tests).
    * ``"during_replay"`` — ``BacktestRunner.run()`` raised. The runner only
      returns its internally-minted ``run_id`` on success, so no ID is
      available here, and it may already have written a ``backtests`` row
      (written before the replay starts) and partial ``strategy_outcomes``
      rows under the shared ``sweep_id``. This response neither reports nor
      denies them; nothing here has verified either way."""

    code: str
    message: str
    stage: str


@dataclass(frozen=True)
class StoredSweepSymbolResult:
    """One symbol's outcome within a stored sweep. On success ``run_id``,
    ``outcomes_recorded`` and ``discarded_signals`` are exactly the fields
    ``POST /backtest/run/stored`` returns (``BacktestRunResult``) and
    ``error`` is ``None``; the three counts describe what the recorded-candle
    read actually found. On failure all of those are ``None``/empty and
    ``error`` explains why. ``outcomes_recorded == 0`` is a success."""

    symbol: str
    run_id: UUID | None
    outcomes_recorded: int | None
    discarded_signals: list[DiscardedSignal]
    primary_candle_count: int | None
    warmup_minute_candle_count: int | None
    daily_candle_count: int | None
    error: StoredSweepSymbolError | None


@dataclass(frozen=True)
class StoredSweepResult:
    sweep_id: UUID
    strategy_name: str
    start: str
    end: str
    data_version: str
    symbols_requested: int
    symbols_succeeded: int
    symbols_failed: int
    runs: list[StoredSweepSymbolResult] = field(default_factory=list)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_stored_sweep_request(
    symbols: list[str],
    start: datetime,
    end: datetime,
) -> tuple[list[str], datetime, datetime]:
    """Validate the WHOLE stored-sweep request before any run starts.

    Reuses ``_validate_ibkr_range`` unchanged for every symbol (strip and
    upper-case, non-empty, tz-aware ``start < end``, at most 24 elapsed hours,
    UTC normalization), so the sweep accepts exactly what ``/run/stored``
    accepts per symbol. Symbols are then de-duplicated keeping first-occurrence
    order: replaying one recorded dataset twice under one sweep would only
    double-count it in the sweep's population. (The fixture ``/sweep``
    deliberately does not de-duplicate — its caller names (symbol, scenario)
    pairs; the web form de-duplicates before sending. Stored replays have no
    second axis, so a repeat is always the same run twice.) The existing
    ``_MAX_SWEEP_PAIRS`` cap is applied to the number of runs that would
    actually execute, i.e. after de-duplication."""
    if not symbols:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_backtest_request", "message": "symbols must contain at least one value"},
        )
    unique: list[str] = []
    seen: set[str] = set()
    start_utc, end_utc = start, end
    for raw in symbols:
        symbol, start_utc, end_utc = _validate_ibkr_range(raw, start, end)
        if symbol not in seen:
            seen.add(symbol)
            unique.append(symbol)
    if len(unique) > _MAX_SWEEP_PAIRS:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "backtest_sweep_too_large",
                "message": (
                    f"Requested {len(unique)} distinct symbols, exceeding the maximum of "
                    f"{_MAX_SWEEP_PAIRS} per sweep. Split this into multiple requests, or request "
                    "fewer symbols."
                ),
            },
        )
    return unique, start_utc, end_utc


def _live_data_connected_message() -> str | None:
    """``_reject_if_live_data_connected`` as a value: the 409 detail if a
    live provider is connected, else ``None``. Lets the per-symbol loop record
    the very same guard text without catching ``HTTPException`` broadly."""
    try:
        _reject_if_live_data_connected()
    except HTTPException as exc:
        return str(exc.detail)
    return None


def _stored_sweep_failure(symbol: str, code: str, message: str, stage: str) -> StoredSweepSymbolResult:
    return StoredSweepSymbolResult(
        symbol=symbol,
        run_id=None,
        outcomes_recorded=None,
        discarded_signals=[],
        primary_candle_count=None,
        warmup_minute_candle_count=None,
        daily_candle_count=None,
        error=StoredSweepSymbolError(code=code, message=message, stage=stage),
    )


async def _run_stored_sweep_symbol(
    *,
    symbol: str,
    strategy_name: str,
    start: datetime,
    end: datetime,
    sweep_id: UUID,
) -> StoredSweepSymbolResult:
    """One symbol of a stored sweep: exactly ``POST /run/stored``'s steps
    (guard, recorded-candle read, guard again immediately before any replay
    engine is installed, run) with the failure turned into a value."""
    settings = get_settings()
    stage = "before_replay"
    try:
        guard = _live_data_connected_message()
        if guard is not None:
            return _stored_sweep_failure(symbol, "live_data_connected", guard, stage)

        dataset = await acquire_stored_replay_data(
            symbol=symbol,
            start=start,
            end=end,
            daily_lookback_days=settings.daily_levels_lookback_days,
            premarket_lookback_days=settings.feature_engine_premarket_lookback_days,
        )

        # A live provider may have connected while the read was in flight.
        # Re-check before any process-wide replay singleton or database row
        # of THIS symbol's run is touched — the same point /run/stored checks.
        guard = _live_data_connected_message()
        if guard is not None:
            return _stored_sweep_failure(symbol, "live_data_connected", guard, stage)

        # Fresh instance per symbol — never shared across the loop (all real
        # strategies keep per-symbol mutable state in `self._state`).
        strategy = next(
            s for s in default_registry(datetime.now(timezone.utc)) if s.name == strategy_name
        )
        logger.info(
            "stored backtest sweep: sweep_id=%s symbol=%s primary=%d warmup_1m=%d daily=%d",
            sweep_id,
            symbol,
            dataset.primary_candle_count,
            dataset.warmup_minute_candle_count,
            dataset.daily_candle_count,
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
            sweep_id=sweep_id,
        )
        stage = "during_replay"
        result = await runner.run()
    except StoredHistoryError as exc:
        return _stored_sweep_failure(symbol, exc.code, exc.message, stage)
    except Exception as exc:  # noqa: BLE001 — one bad symbol must not sink the rest of the sweep
        logger.exception(
            "stored backtest sweep: symbol=%r failed (%s) under sweep_id=%s", symbol, stage, sweep_id
        )
        return _stored_sweep_failure(symbol, "backtest_run_failed", f"{type(exc).__name__}: {exc}", stage)

    return StoredSweepSymbolResult(
        symbol=symbol,
        run_id=result.run_id,
        outcomes_recorded=result.outcomes_recorded,
        discarded_signals=result.discarded_signals,
        primary_candle_count=dataset.primary_candle_count,
        warmup_minute_candle_count=dataset.warmup_minute_candle_count,
        daily_candle_count=dataset.daily_candle_count,
        error=None,
    )


@router.post("/sweep/stored")
async def run_stored_backtest_sweep(
    strategy_name: str = Query(..., description="One of the 7 real v1 strategy names."),
    symbols: list[str] = Query(
        ...,
        description=(
            "Explicit list of tickers whose candles were already recorded in this database, e.g. "
            "?symbols=AAPL&symbols=MSFT. No implicit 'all recorded symbols' expansion."
        ),
    ),
    start: datetime = Query(..., description="Timezone-aware ISO-8601 inclusive start, shared by every symbol."),
    end: datetime = Query(..., description="Timezone-aware ISO-8601 exclusive end, shared by every symbol."),
) -> dict[str, Any]:
    """Run one strategy over each of ``symbols`` using candles already
    recorded in PostgreSQL, over one shared exact ``[start, end)`` interval,
    sequentially, under one real ``sweep_id``.

    **Validation is all-or-nothing and happens first.** Unknown
    ``strategy_name`` (400), then every symbol and the interval through the
    unchanged ``/run/stored`` rules (422 ``invalid_backtest_request``: blank
    symbol, naive datetime, ``start >= end``, more than 24 elapsed hours),
    then symbols are normalized (strip, upper-case) and de-duplicated in
    first-occurrence order, then the existing ``_MAX_SWEEP_PAIRS`` (20) cap on
    the distinct symbols (400 ``backtest_sweep_too_large``), then the
    connected-provider guard (409). Nothing is read or written until all of
    that passes.

    **Execution.** Each symbol is executed in request order, one at a time,
    through the same steps as ``/run/stored``: guard, one worker-owned
    read-only snapshot of that symbol's recorded candles
    (``acquire_stored_replay_data`` — live namespace only, exact interval,
    configured warm-up lookbacks, nothing at or after ``end``, no daily bar the
    replay could not legitimately see, no synthetic history), the guard again
    immediately before the replay engines are installed, then a fresh
    ``Strategy`` instance and a ``BacktestRunner`` carrying the shared
    ``sweep_id``. Each run takes and releases ``engine_singleton_guard``'s
    process-wide lock on its own, exactly like ``/sweep``; another request's
    replay can therefore run between two symbols of one sweep. Nothing is
    written to the source candles and no external provider is contacted.

    **Per-symbol results, never a lost sweep.** A symbol that cannot be
    replayed (no recorded candles in the interval, malformed stored values,
    database unavailable, a live provider connecting mid-sweep, or an
    exception inside the runner) is recorded in ``runs`` as
    ``{"error": {"code", "message", "stage"}}`` with ``run_id: null`` — no ID
    is ever invented — and the remaining symbols still run. Completed runs are
    never undone. ``outcomes_recorded == 0`` is a success, not a failure. The
    response is ``200`` whenever the request passed validation, even if every
    symbol failed; ``symbols_succeeded`` says how many produced a run.

    **Provenance limits, stated plainly.** (1) Each symbol is read in its own
    snapshot, taken just before its own replay: the sweep is not one
    consistent snapshot across symbols. (2) A failure with ``stage ==
    "before_replay"`` left no run or outcome row. A failure with ``stage ==
    "during_replay"`` may have left a ``backtests`` row and partial outcomes
    under the shared ``sweep_id`` whose ``run_id`` this response cannot give
    (the runner returns it only on success); those rows would count in
    sweep-level reads. (3) Historical fundamentals and news are not stored, so
    they stay absent; daily-derived scores use only recorded ``1d`` history
    and are at their no-history value where none is recorded
    (``daily_candle_count`` shows how much each symbol had). (4) Synthetic or
    sparse data proves plumbing, never profitability.
    """
    _validate_strategy_name(strategy_name)
    symbols, start, end = _validate_stored_sweep_request(symbols, start, end)

    guard = _live_data_connected_message()
    if guard is not None:
        raise HTTPException(status_code=409, detail=guard)

    sweep_id = uuid4()
    runs: list[StoredSweepSymbolResult] = []
    for symbol in symbols:
        runs.append(
            await _run_stored_sweep_symbol(
                symbol=symbol,
                strategy_name=strategy_name,
                start=start,
                end=end,
                sweep_id=sweep_id,
            )
        )

    symbols_failed = sum(1 for r in runs if r.error is not None)
    sweep_result = StoredSweepResult(
        sweep_id=sweep_id,
        strategy_name=strategy_name,
        start=_iso_utc(start),
        end=_iso_utc(end),
        data_version=STORED_DATA_VERSION,
        symbols_requested=len(symbols),
        symbols_succeeded=len(symbols) - symbols_failed,
        symbols_failed=symbols_failed,
        runs=runs,
    )
    return dataclasses.asdict(sweep_result)
