"""
Unit 5 — regression tests closing four concrete coverage gaps identified
against the original Backtest Runner v1 spec, plus (decision #135) a
fifth section covering the new historical-provider seam's own contract
tests and a real end-to-end regression proof. Test-only: no changes to
`backtest_runner/`, strategy implementations, performance query code,
persistence models, API routes, or the DB schema. Each of the five
sections below is independent; see each section's own docstring for
exactly what it proves.
"""
from __future__ import annotations

import ast
import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from polygon.exceptions import BadResponse
from sqlalchemy import select, text

from app.backtest_runner.context_provider import FixtureBacktestContextProvider
from app.backtest_runner.engine_singleton_guard import install_replay_engines
from app.backtest_runner.fixture_daily_history import build_daily_history_candles
from app.backtest_runner.fixture_provider import FixtureCandleProvider
from app.backtest_runner.historical_provider_guard import install_replay_historical_provider
from app.backtest_runner.runner import BacktestRunner
from app.backtest_runner.scenarios import load_scenario_candles
from app.broker_adapters.base import HistoricalDataUnavailableError
from app.broker_adapters.polygon_provider import PolygonAdapter
from app.core.market_clock import get_market_clock
from app.db.session import SessionLocal
from app.models.daily_levels import DailyLevelState
from app.models.market_data import Symbol
from app.models.market_state import MarketStateHistory
from app.services import broker_registry
from app.strategy_engine.base_strategy import Opportunity, Strategy, StrategyConfig, every_candle
from app.strategy_engine.scheduler import default_registry
from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots

SYMBOL = "ZZUNIT5"


def _db_available() -> bool:
    try:
        session = SessionLocal()
        try:
            session.execute(text("SELECT 1"))
            return True
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        return False


def _clean_test_symbol(ticker: str) -> None:
    session = SessionLocal()
    try:
        session.execute(text("DELETE FROM strategy_outcomes WHERE symbol = :t"), {"t": ticker})
        session.execute(text("DELETE FROM backtests WHERE :t = ANY(symbol_universe)"), {"t": ticker})
        for table in (
            "level_interaction_events",
            "level_interaction_state",
            "daily_levels_state",
            "market_state_history",
            "symbol_fundamentals",
            "scanner_universe_symbols",
            "candles",
        ):
            session.execute(
                text(f"DELETE FROM {table} WHERE symbol_id IN (SELECT id FROM symbols WHERE ticker = :t)"),
                {"t": ticker},
            )
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": ticker})
        session.commit()
    finally:
        session.close()


class _OneShotStubStrategy(Strategy):
    """Same stub precedent as `test_backtest_runner.py`/
    `test_strategy_scheduler.py`'s own `_StubStrategy` — deterministic,
    fires an actionable Opportunity on one specific call, real `Strategy`
    ABC, unmodified calling convention."""

    name = "STUB_ONE_SHOT"
    trigger = every_candle(timeframe="1m")

    def __init__(self, config: StrategyConfig, *, fire_on_call_index: int, opportunity: Opportunity) -> None:
        super().__init__(config)
        self._fire_on_call_index = fire_on_call_index
        self._opportunity = opportunity
        self.calls = 0

    async def evaluate(self, symbol, market_state, features, context) -> Opportunity | None:  # noqa: ANN001
        idx = self.calls
        self.calls += 1
        return self._opportunity if idx == self._fire_on_call_index else None


def _fixture_candles() -> list:
    from app.broker_adapters.base import Candle

    start = datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc)
    return [
        Candle(timeframe="1m", open=100.0, high=100.15, low=99.95, close=100.10, volume=1000, candle_ts=start),
        Candle(timeframe="1m", open=100.10, high=100.25, low=100.05, close=100.20, volume=1000, candle_ts=start + timedelta(minutes=1)),
        Candle(timeframe="1m", open=100.20, high=100.30, low=100.15, close=100.25, volume=1000, candle_ts=start + timedelta(minutes=2)),  # entry fill candle
        Candle(timeframe="1m", open=100.25, high=102.00, low=100.20, close=101.80, volume=1000, candle_ts=start + timedelta(minutes=3)),  # exit candle (target)
    ]


def _make_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_name="STUB_ONE_SHOT", version="stub_v1", gate_conditions={}, params={},
        active_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _make_opportunity(setup_ts: datetime) -> Opportunity:
    return Opportunity(
        strategy="STUB_ONE_SHOT", version="stub_v1", direction="BUY", confidence=0.9,
        structural_invalidation=98.0, structural_target=101.0,
        evidence={"conditions": {}, "reason": "stub fires deterministically", "basis": "closed"},
        setup_detected_at=setup_ts,
    )


# =====================================================================
# 1. Runner-level D17 enforcement — the actual BacktestRunner path, not
#    only gate_and_warmup.check_entry_allowed(). Confirms the specific
#    control flow Unit 4 established: capture_strategy_outcome_snapshots()
#    is called once at the fill candle and once at the exit candle
#    (never at the signal candle), and record_strategy_outcome() is only
#    ever called when BOTH calls return real dicts. Mocked at
#    "app.backtest_runner.runner.capture_strategy_outcome_snapshots" —
#    the name as bound into runner.py's own namespace, not the function's
#    defining module — so this is genuinely testing what the Runner
#    calls, not a coincidentally-matching import path.
#
#    DB-gated: these three run the full engine pipeline (real Postgres
#    writes from FeatureEngine/LevelInteractionEngine/MarketStateEngine),
#    unlike sections 2-4 below, which are DB-free — scoped individually
#    rather than via a module-wide skip, so a missing DB doesn't also
#    skip the tests that don't need one.
# =====================================================================

_needs_db = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


@pytest.fixture()
def _clean_unit5_symbol():
    _clean_test_symbol(SYMBOL)
    yield
    _clean_test_symbol(SYMBOL)


async def _run_with_mocked_snapshots(snapshot_side_effect):
    candles = _fixture_candles()
    strategy = _OneShotStubStrategy(_make_config(), fire_on_call_index=1, opportunity=_make_opportunity(candles[1].candle_ts))
    provider = FixtureCandleProvider.single(SYMBOL, "1m", candles)
    runner = BacktestRunner(
        strategy=strategy, symbol=SYMBOL, market_data_provider=provider,
        start=candles[0].candle_ts, end=candles[-1].candle_ts + timedelta(minutes=1),
        context_provider=FixtureBacktestContextProvider(),
        data_version="fixture-v1", feature_version="feature_engine_v1",
    )
    with patch("app.backtest_runner.runner.capture_strategy_outcome_snapshots", side_effect=snapshot_side_effect) as capture_mock, patch(
        "app.backtest_runner.runner.record_strategy_outcome"
    ) as record_mock:
        result = await runner.run()
    return result, capture_mock, record_mock


@_needs_db
async def test_runner_discards_signal_when_fill_time_snapshot_is_none(_clean_unit5_symbol):
    """Fill-time capture (the call at entry_fill.entry_candle_index)
    returns None → the signal is discarded, a DiscardedSignal is
    recorded, record_strategy_outcome() is never called. No exit-time
    capture happens either, since the position was voided before ever
    reaching that check."""
    result, capture_mock, record_mock = await _run_with_mocked_snapshots(
        [StrategyOutcomeSnapshots(market_state=None, context=None)]
    )

    assert capture_mock.call_count == 1  # only the fill-time call — position was voided before the exit check
    record_mock.assert_not_called()
    assert result.outcomes_recorded == 0
    assert len(result.discarded_signals) == 1
    assert "D17" in result.discarded_signals[0].reason
    assert "entry_filled_at" in result.discarded_signals[0].reason


@_needs_db
async def test_runner_discards_signal_when_exit_time_snapshot_is_none(_clean_unit5_symbol):
    """Fill-time capture succeeds (real-looking dicts); exit-time capture
    returns None → the outcome is NOT persisted, no fabricated `{}` is
    substituted for the missing snapshot, and no StrategyOutcome reaches
    record_strategy_outcome()."""
    real_entry_snapshots = StrategyOutcomeSnapshots(market_state={"trend_score": 55.0}, context={"calendar": {"session": "open"}})
    result, capture_mock, record_mock = await _run_with_mocked_snapshots(
        [real_entry_snapshots, StrategyOutcomeSnapshots(market_state=None, context=None)]
    )

    assert capture_mock.call_count == 2  # fill-time AND exit-time both ran
    record_mock.assert_not_called()
    assert result.outcomes_recorded == 0
    assert len(result.discarded_signals) == 1
    assert "D17" in result.discarded_signals[0].reason
    assert "exit_filled_at" in result.discarded_signals[0].reason


@_needs_db
async def test_runner_persists_outcome_when_both_snapshots_present(_clean_unit5_symbol):
    """The retained successful path, through the SAME mocking seam as the
    two failure-path tests above — confirms the mock target genuinely
    matches what the Runner calls (this isn't just "mocking something
    unrelated and nothing breaks"): valid snapshots at both instants →
    record_strategy_outcome() is called exactly once, with the mocked
    dicts flowing straight through to market_state_at_entry/
    context_at_entry/market_state_at_exit/context_at_exit unchanged."""
    entry_snapshots = StrategyOutcomeSnapshots(market_state={"trend_score": 55.0}, context={"calendar": {"session": "open"}})
    exit_snapshots = StrategyOutcomeSnapshots(market_state={"trend_score": 61.0}, context={"calendar": {"session": "open"}})
    result, capture_mock, record_mock = await _run_with_mocked_snapshots([entry_snapshots, exit_snapshots])

    assert capture_mock.call_count == 2
    record_mock.assert_called_once()
    outcome = record_mock.call_args[0][0]
    assert outcome.market_state_at_entry == entry_snapshots.market_state
    assert outcome.context_at_entry == entry_snapshots.context
    assert outcome.market_state_at_exit == exit_snapshots.market_state
    assert outcome.context_at_exit == exit_snapshots.context
    assert result.outcomes_recorded == 1
    assert result.discarded_signals == []


# =====================================================================
# 2. engine_singleton_guard serialization — proves actual mutual
#    exclusion (no overlap in critical-section occupancy), not merely an
#    observed call order that could arise by scheduling coincidence.
#    DB-free: install_replay_engines() only assigns the given objects to
#    module-level globals, so plain sentinel objects are sufficient —
#    nothing here needs real engine behavior.
# =====================================================================


async def test_engine_singleton_guard_serializes_concurrent_installs():
    events: list[tuple[str, str, float]] = []

    async def run(name: str, hold_seconds: float) -> None:
        async with install_replay_engines(
            feature_engine=object(), level_interaction_engine=object(),
            market_state_engine=object(), context_engine=object(),
        ):
            events.append((name, "enter", time.monotonic()))
            await asyncio.sleep(hold_seconds)
            events.append((name, "exit", time.monotonic()))

    await asyncio.gather(run("A", 0.2), run("B", 0.05))

    intervals = {name: {} for name in ("A", "B")}
    for name, kind, t in events:
        intervals[name][kind] = t

    a_enter, a_exit = intervals["A"]["enter"], intervals["A"]["exit"]
    b_enter, b_exit = intervals["B"]["enter"], intervals["B"]["exit"]

    # Mutual exclusion, checked directly: the two [enter, exit] intervals
    # must not overlap, regardless of which one happened to acquire the
    # lock first (asyncio.gather's start order isn't a hard guarantee).
    no_overlap = (a_exit <= b_enter) or (b_exit <= a_enter)
    assert no_overlap, f"critical sections overlapped: A=[{a_enter}, {a_exit}] B=[{b_enter}, {b_exit}]"


async def test_engine_singleton_guard_restores_prior_value_even_on_exception():
    """Retained from Unit 2 — still the right place for it now that this
    file is the dedicated home for engine_singleton_guard's own
    contract tests."""
    import app.market_state_engine.engine as market_state_engine_module

    sentinel_prev = market_state_engine_module._market_state_engine

    with pytest.raises(RuntimeError, match="boom"):
        async with install_replay_engines(
            feature_engine=object(), level_interaction_engine=object(),
            market_state_engine=object(), context_engine=object(),
        ):
            raise RuntimeError("boom")

    assert market_state_engine_module._market_state_engine is sentinel_prev


# =====================================================================
# 3. Polygon NOT_AUTHORIZED propagation through the real BacktestRunner
#    path — reuses test_polygon_provider.py's own established mocking
#    seam (adapter._client.get_aggs monkeypatched to raise the real
#    captured NOT_AUTHORIZED response body) rather than inventing a
#    second abstraction. Confirms the Runner neither swallows the
#    exception, nor downgrades to fixture data, nor produces an empty
#    "successful" run.
#
#    DB-free: BacktestRunner.run() calls get_historical() as its very
#    first action, before writing BacktestRunRecord or starting any
#    engine — so this exception fires before any DB interaction occurs.
# =====================================================================

_REAL_NOT_AUTHORIZED_BODY = (
    '{"status":"NOT_AUTHORIZED","request_id":"f73a9e39364524f66ccb08499ea385b3",'
    '"message":"Your plan doesn\'t include this data timeframe. '
    "Please upgrade your plan at https://polygon.io/pricing\"}"
)


async def test_backtest_runner_propagates_polygon_not_authorized_uncaught():
    adapter = PolygonAdapter(api_key="test-key-not-real")

    def raise_not_authorized(**kwargs):
        raise BadResponse(_REAL_NOT_AUTHORIZED_BODY)

    adapter._client.get_aggs = raise_not_authorized

    strategy = _OneShotStubStrategy(
        _make_config(), fire_on_call_index=0,
        opportunity=_make_opportunity(datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc)),
    )
    runner = BacktestRunner(
        strategy=strategy, symbol="AAPL", market_data_provider=adapter,
        start=datetime.now(timezone.utc) - timedelta(hours=1), end=datetime.now(timezone.utc),
        context_provider=FixtureBacktestContextProvider(),
        data_version="polygon-live", feature_version="feature_engine_v1",
    )

    with pytest.raises(HistoricalDataUnavailableError) as exc_info:
        await runner.run()

    # Genuinely the same exception PolygonAdapter itself raises — not a
    # different type the Runner wrapped or reinterpreted.
    assert exc_info.value.provider == "Polygon"
    assert strategy.calls == 0  # never even reached candle replay — no silent partial/empty run


# =====================================================================
# 4. No backtest-specific branches in the 7 real strategy files — AST-
#    based, so comments/docstrings mentioning "backtest" (there are
#    several, legitimately — e.g. "this strategy is backtest-safe by
#    construction") never produce a false positive. Checks only real
#    Python identifiers: import targets and `if`-condition expressions.
# =====================================================================

_STRATEGY_ENGINE_DIR = Path(__file__).parent.parent / "app" / "strategy_engine"
_STRATEGY_FILES = [
    "orb_strategy.py",
    "gap_strategy.py",
    "volume_spike_strategy.py",
    "momentum_strategy.py",
    "vwap_strategy.py",
    "first_pullback_strategy.py",
    "reversal_strategy.py",
]


def _condition_identifiers(node: ast.expr) -> set[str]:
    """Every Name/Attribute identifier referenced anywhere inside an
    `if` condition expression (walks the whole subtree, so `if a and
    is_backtest:` is caught just as much as a bare `if is_backtest:`)."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    return names


@pytest.mark.parametrize("filename", _STRATEGY_FILES)
def test_strategy_file_does_not_import_backtest_runner(filename: str):
    tree = ast.parse((_STRATEGY_ENGINE_DIR / filename).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None) or ""
            imported_names = " ".join(alias.name for alias in node.names)
            combined = f"{module} {imported_names}".lower()
            assert "backtest" not in combined, (
                f"{filename}: imports something backtest-related ({module!r}, {imported_names!r}) — "
                "strategies must stay fully decoupled from Backtest Runner (real GATE/MATCH/SCORE/PROPOSE "
                "logic is imported and called by the harness, never the other way around)."
            )


@pytest.mark.parametrize("filename", _STRATEGY_FILES)
def test_strategy_file_has_no_conditional_branch_on_backtest_identifiers(filename: str):
    tree = ast.parse((_STRATEGY_ENGINE_DIR / filename).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            offending = {n for n in _condition_identifiers(node.test) if "backtest" in n.lower()}
            assert not offending, (
                f"{filename}, line {node.lineno}: conditional branch references {offending} — "
                "no strategy file may branch on backtest-related state (e.g. `if is_backtest:`, "
                "`if backtesting:`). Docstrings/comments mentioning 'backtest' are fine; this check "
                "only inspects real identifiers in import statements and if-conditions."
            )


# =====================================================================
# 5. historical_provider_guard.py's own contract tests (decision #135) —
#    same two shapes section 2 above already established for
#    engine_singleton_guard.py, applied to the new broker_registry
#    historical-role seam — plus a real end-to-end regression proof that
#    a `volume_gated_baseline` replay now produces genuinely non-zero
#    `volume_regime_score`/`volatility_regime_score` throughout, where
#    every row was exactly `0.00` before this decision (confirmed via
#    direct execution against a real Postgres during this decision's own
#    investigation, not reasoned from source — see `historical_provider_
#    guard.py`'s and `fixture_daily_history.py`'s own module docstrings).
#
#    The first two tests below are DB-free, same reasoning as section 2:
#    install_replay_historical_provider() only touches broker_registry's
#    in-memory global, so plain sentinel objects are sufficient. The
#    third is DB-gated — it runs a real replay and inspects real
#    persisted `market_state_history` rows.
# =====================================================================


async def test_historical_provider_guard_serializes_concurrent_installs():
    events: list[tuple[str, str, float]] = []

    async def run(name: str, hold_seconds: float) -> None:
        async with install_replay_historical_provider(object()):
            events.append((name, "enter", time.monotonic()))
            await asyncio.sleep(hold_seconds)
            events.append((name, "exit", time.monotonic()))

    await asyncio.gather(run("A", 0.2), run("B", 0.05))

    intervals = {name: {} for name in ("A", "B")}
    for name, kind, t in events:
        intervals[name][kind] = t

    a_enter, a_exit = intervals["A"]["enter"], intervals["A"]["exit"]
    b_enter, b_exit = intervals["B"]["enter"], intervals["B"]["exit"]

    no_overlap = (a_exit <= b_enter) or (b_exit <= a_enter)
    assert no_overlap, f"critical sections overlapped: A=[{a_enter}, {a_exit}] B=[{b_enter}, {b_exit}]"


async def test_historical_provider_guard_restores_prior_value_even_on_exception():
    """Same shape as test_engine_singleton_guard_restores_prior_value_
    even_on_exception (section 2 above), applied to
    historical_provider_guard.py's own broker_registry seam. Uses a real
    non-None sentinel as the "prior value" specifically to exercise the
    `else: broker_registry.set_historical_provider(prev)` branch of the
    restore, not just the `prev is None` branch (see the next test for
    that one) — a real code path this module has that
    engine_singleton_guard.py's own unconditional-reassignment restore
    never needed."""
    sentinel_prev = object()
    broker_registry.set_historical_provider(sentinel_prev)

    with pytest.raises(RuntimeError, match="boom"):
        async with install_replay_historical_provider(object()):
            raise RuntimeError("boom")

    assert broker_registry.get_historical_provider() is sentinel_prev


async def test_historical_provider_guard_restores_none_when_nothing_installed_before():
    """The other restore branch — `prev is None` →
    `broker_registry.clear_historical_provider()`, not a
    `set_historical_provider(None)` call (that distinction matters:
    `broker_registry.py`'s own `clear_historical_provider()` is the real,
    documented way to represent "nothing installed," confirmed by
    reading `broker_registry.py` directly before writing this seam)."""
    assert broker_registry.get_historical_provider() is None  # autouse _reset_app_singletons guarantees this
    async with install_replay_historical_provider(object()):
        assert broker_registry.get_historical_provider() is not None
    assert broker_registry.get_historical_provider() is None


DAILY_HISTORY_SYMBOL = "ZZUNIT5B"


@pytest.fixture()
def _clean_daily_history_symbol():
    _clean_test_symbol(DAILY_HISTORY_SYMBOL)
    yield
    _clean_test_symbol(DAILY_HISTORY_SYMBOL)


@_needs_db
async def test_backtest_runner_namespace_preserves_live_levels_and_repeat_runs_keep_nonzero_regime_scores(
    _clean_daily_history_symbol,
):
    """The real end-to-end proof decisions #135 and D18 require. Before
    it, `scenarios.py`'s own module docstring could state directly (and
    this same check, run against the pre-#135 code during that
    decision's investigation, confirmed it by direct execution): every
    `market_state_history` row from ANY BacktestRunner replay had
    `volume_regime_score == 0.0` and `volatility_regime_score == 0.0`,
    structurally, for any symbol. This asserts that's no longer true for
    every replayed candle on two consecutive runs of the existing `volume_gated_baseline`
    scenario — not just the last one, which alone wouldn't catch a bug
    where the daily-candle cache populates late, is dropped partway through
    a run, or is skipped because the prior replay left a checkpoint. It also
    proves a colliding live Daily Levels row remains unchanged.

    Strategy choice (Reversal) is arbitrary and deliberately NOT one of
    the four volume-gated strategies — `MarketStateEngine` persists
    `market_state_history` as part of the engine pipeline itself,
    independent of which strategy is being evaluated against it, so any
    real `Strategy` proves this."""
    candles = load_scenario_candles("volume_gated_baseline")
    first_day = get_market_clock().trading_day(candles[0].candle_ts)
    daily_candles = build_daily_history_candles(before=first_day)
    provider = FixtureCandleProvider(
        {
            (DAILY_HISTORY_SYMBOL, "1m"): candles,
            (DAILY_HISTORY_SYMBOL, "1d"): daily_candles,
        }
    )

    # A real live checkpoint with the exact caller-supplied ticker must
    # survive both replays byte-for-byte at the field level.
    session = SessionLocal()
    try:
        live_symbol = Symbol(ticker=DAILY_HISTORY_SYMBOL, is_backtest=False)
        session.add(live_symbol)
        session.flush()
        session.add(
            DailyLevelState(
                symbol_id=live_symbol.id,
                is_backtest=False,
                level_id=f"{DAILY_HISTORY_SYMBOL}-LIVE-SENTINEL",
                price=987.654321,
                strength=7,
                distinct_candle_count=9,
                status="active",
                first_seen_day=first_day,
                last_confirmed_day=first_day,
            )
        )
        session.commit()
    finally:
        session.close()

    for _ in range(2):
        strategy = next(s for s in default_registry(datetime.now(timezone.utc)) if s.name == "Reversal")
        runner = BacktestRunner(
            strategy=strategy,
            symbol=DAILY_HISTORY_SYMBOL,
            market_data_provider=provider,
            start=candles[0].candle_ts,
            end=candles[-1].candle_ts,
            context_provider=FixtureBacktestContextProvider(),
            data_version="decision-135-regression",
            feature_version="feature_engine_v1",
        )
        await runner.run()

    session = SessionLocal()
    try:
        rows = (
            session.query(MarketStateHistory)
            .join(Symbol, Symbol.id == MarketStateHistory.symbol_id)
            .filter(Symbol.ticker == DAILY_HISTORY_SYMBOL, Symbol.is_backtest.is_(True))
            .order_by(MarketStateHistory.candle_ts)
            .all()
        )
        namespaces = {
            row.is_backtest for row in session.execute(select(Symbol).where(Symbol.ticker == DAILY_HISTORY_SYMBOL)).scalars()
        }
        live_level = session.execute(
            select(DailyLevelState).where(
                DailyLevelState.level_id == f"{DAILY_HISTORY_SYMBOL}-LIVE-SENTINEL",
                DailyLevelState.is_backtest.is_(False),
            )
        ).scalar_one()
    finally:
        session.close()

    assert namespaces == {False, True}
    assert (
        float(live_level.price),
        live_level.strength,
        live_level.distinct_candle_count,
        live_level.status,
        live_level.first_seen_day,
        live_level.last_confirmed_day,
        live_level.archived_day,
    ) == (987.654321, 7, 9, "active", first_day, first_day, None)

    # NOT `len(rows) == len(candles)` — checked directly against the
    # UNMODIFIED baseline (no decision #135 seam at all, plain
    # FixtureCandleProvider.single()): the real, pre-existing
    # MarketStateEngine/ReplayStateProducer pipeline already persists one
    # fewer `market_state_history` row than replayed candles for this
    # exact scenario — the final candle's state update never lands
    # before `producer.stop()`. Confirmed by direct execution against
    # `main`, not this delivery's own code — a genuine pre-existing gap,
    # unrelated to and not introduced by decision #135, out of scope to
    # fix here (would mean touching `MarketStateEngine`/
    # `ReplayStateProducer`, not the historical-provider seam). A
    # non-trivial floor (rather than no count check at all) still
    # catches a real regression where the seam breaks early and stops
    # producing state altogether.
    assert len(rows) >= 2 * (len(candles) - 1), (
        f"expected close to one market_state_history row per replayed candle on each of two runs "
        f"({len(candles)} candles each), got only {len(rows)} — more missing than the one-per-run "
        f"pre-existing, unrelated final-candle gap accounts for"
    )
    assert all(float(r.volume_regime_score) != 0.0 for r in rows), (
        "volume_regime_score was 0.0 for at least one replayed candle — the exact "
        "decision #135 regression this test exists to catch."
    )
    assert all(float(r.volatility_regime_score) != 0.0 for r in rows), (
        "volatility_regime_score was 0.0 for at least one replayed candle — the exact "
        "decision #135 regression this test exists to catch."
    )
