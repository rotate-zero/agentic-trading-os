"""
Unit 5 — regression tests closing four concrete coverage gaps identified
against the original Backtest Runner v1 spec. Test-only: no changes to
`backtest_runner/`, strategy implementations, performance query code,
persistence models, API routes, or the DB schema. Each of the four
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
from sqlalchemy import text

from app.backtest_runner.context_provider import FixtureBacktestContextProvider
from app.backtest_runner.engine_singleton_guard import install_replay_engines
from app.backtest_runner.fixture_provider import FixtureCandleProvider
from app.backtest_runner.runner import BacktestRunner
from app.broker_adapters.base import HistoricalDataUnavailableError
from app.broker_adapters.polygon_provider import PolygonAdapter
from app.db.session import SessionLocal
from app.strategy_engine.base_strategy import Opportunity, Strategy, StrategyConfig, every_candle
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
