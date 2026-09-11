"""
Unit 4 test — real Postgres, DB-gated (same convention as the rest of
this drop). Proves the core claim of the whole task: one fixture-driven
run, through the real engine pipeline and a real (stubbed) Strategy,
produces a real `BacktestRunRecord` and a real
`StrategyOutcomeRecord(is_backtest=True)` resolving to it — captured at
`entry_filled_at`/`exit_filled_at`, never at the signal candle.

Uses a stub `Strategy`, not one of the 7 real ones — same precedent
`test_strategy_scheduler.py`'s own `_StubStrategy` already established
for testing orchestration against the real engines without needing a
real strategy's actual MATCH thresholds (trend_score/volume_regime_score
crossing points, computed by MarketStateEngine's real scoring functions)
to fire reproducibly from hand-built candles. Each real strategy's own
test file already proves its GATE/MATCH/SCORE logic; nothing here
re-tests that. What's under test here is entirely `runner.py`'s own
orchestration — fill timing, snapshot-capture instant, persistence.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.backtest_runner.context_provider import FixtureBacktestContextProvider
from app.backtest_runner.fixture_provider import FixtureCandleProvider
from app.backtest_runner.runner import BacktestRunner
from app.broker_adapters.base import Candle
from app.db.session import SessionLocal
from app.strategy_engine.base_strategy import Opportunity, Strategy, StrategyConfig, every_candle

SYMBOL = "ZZUNIT4"


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


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


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


@pytest.fixture(autouse=True)
def _clean_before_and_after():
    _clean_test_symbol(SYMBOL)
    yield
    _clean_test_symbol(SYMBOL)


class _OneShotStubStrategy(Strategy):
    """Returns a single actionable `Opportunity` on a specific 0-indexed
    call number (i.e. a specific replayed candle), `None` every other
    call — deterministic, so the resulting entry/exit candle indices are
    exactly known ahead of writing the test's assertions. Same
    `Strategy` ABC, same calling convention `record_strategy_outcome()`'s
    real caller uses — nothing about `evaluate()`'s signature or contract
    is bent for this stub, matching `test_strategy_scheduler.py`'s own
    `_StubStrategy` precedent."""

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


def _fixture_candles() -> list[Candle]:
    """4 candles: index 0 warm-up, index 1 is the signal candle (stub
    fires here), index 2 is the entry fill candle (next open after the
    signal), index 3 clears the target via its high — a short,
    deliberately hand-verifiable trade, not aiming for realism."""
    start = datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc)  # 09:30 ET, real regular session
    return [
        Candle(timeframe="1m", open=100.0, high=100.15, low=99.95, close=100.10, volume=1000, candle_ts=start),
        Candle(timeframe="1m", open=100.10, high=100.25, low=100.05, close=100.20, volume=1000, candle_ts=start + timedelta(minutes=1)),
        Candle(timeframe="1m", open=100.20, high=100.30, low=100.15, close=100.25, volume=1000, candle_ts=start + timedelta(minutes=2)),  # entry fills at this open (100.20)
        Candle(timeframe="1m", open=100.25, high=102.00, low=100.20, close=101.80, volume=1000, candle_ts=start + timedelta(minutes=3)),  # high clears target (101.0)
    ]


def _make_config() -> StrategyConfig:
    return StrategyConfig(
        strategy_name="STUB_ONE_SHOT",
        version="stub_v1",
        gate_conditions={},  # no session restriction — keeps this test decoupled from gate_conditions.py's own logic, already tested elsewhere
        params={},
        active_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


async def test_full_fixture_run_records_real_outcome_at_correct_instants():
    candles = _fixture_candles()
    opportunity = Opportunity(
        strategy="STUB_ONE_SHOT",
        version="stub_v1",
        direction="BUY",
        confidence=0.9,
        structural_invalidation=98.0,
        structural_target=101.0,
        evidence={"conditions": {}, "reason": "stub fires deterministically for Unit 4's own test", "basis": "closed"},
        setup_detected_at=candles[1].candle_ts,
    )
    strategy = _OneShotStubStrategy(_make_config(), fire_on_call_index=1, opportunity=opportunity)
    provider = FixtureCandleProvider.single(SYMBOL, "1m", candles)

    runner = BacktestRunner(
        strategy=strategy,
        symbol=SYMBOL,
        market_data_provider=provider,
        start=candles[0].candle_ts,
        end=candles[-1].candle_ts + timedelta(minutes=1),
        context_provider=FixtureBacktestContextProvider(),
        data_version="fixture-v1",
        feature_version="feature_engine_v1",
    )

    result = await runner.run()

    assert result.outcomes_recorded == 1
    assert result.discarded_signals == []
    assert strategy.calls == 4  # evaluate() called on EVERY candle — see runner.py's own module docstring

    session = SessionLocal()
    try:
        run_row = session.execute(text("SELECT run_id, strategy_name, data_version, feature_version FROM backtests WHERE run_id = :rid"), {"rid": str(result.run_id)}).mappings().one()
        assert run_row["strategy_name"] == "STUB_ONE_SHOT"
        assert run_row["data_version"] == "fixture-v1"
        assert run_row["feature_version"] == "feature_engine_v1"

        outcome_row = session.execute(
            text(
                "SELECT symbol, is_backtest, backtest_run_id, entry_filled_at, exit_filled_at, exit_reason, "
                "entry_price, exit_price, realized_r, market_state_at_entry, context_at_entry, "
                "market_state_at_exit, context_at_exit FROM strategy_outcomes WHERE symbol = :sym"
            ),
            {"sym": SYMBOL},
        ).mappings().one()

        assert outcome_row["is_backtest"] is True
        assert str(outcome_row["backtest_run_id"]) == str(result.run_id)
        assert outcome_row["exit_reason"] == "target"
        assert float(outcome_row["entry_price"]) == pytest.approx(100.20)  # candles[2].open
        assert float(outcome_row["exit_price"]) == pytest.approx(101.0)  # structural_target
        assert float(outcome_row["realized_r"]) == pytest.approx((101.0 - 100.20) / (100.20 - 98.0), abs=1e-4)

        # Captured at the FILL/EXIT candles, not the signal candle — the
        # whole reason this module's design changed during planning.
        assert outcome_row["entry_filled_at"].replace(tzinfo=timezone.utc) == candles[2].candle_ts
        assert outcome_row["exit_filled_at"].replace(tzinfo=timezone.utc) == candles[3].candle_ts
        assert outcome_row["market_state_at_entry"] is not None
        assert outcome_row["context_at_entry"] is not None
        assert outcome_row["market_state_at_exit"] is not None
        assert outcome_row["context_at_exit"] is not None
        assert "calendar" in outcome_row["context_at_entry"]
    finally:
        session.close()


async def test_is_backtest_isolation_reuses_existing_invariant():
    """A live-query pattern (is_backtest=False) must never see this
    backtest's row — reusing the SAME isolation `performance_queries.py`
    already enforces and is already tested for, not rebuilding that
    invariant from scratch here."""
    candles = _fixture_candles()
    opportunity = Opportunity(
        strategy="STUB_ONE_SHOT",
        version="stub_v1",
        direction="BUY",
        confidence=0.9,
        structural_invalidation=98.0,
        structural_target=101.0,
        evidence={"conditions": {}, "reason": "stub", "basis": "closed"},
        setup_detected_at=candles[1].candle_ts,
    )
    strategy = _OneShotStubStrategy(_make_config(), fire_on_call_index=1, opportunity=opportunity)
    provider = FixtureCandleProvider.single(SYMBOL, "1m", candles)
    runner = BacktestRunner(
        strategy=strategy,
        symbol=SYMBOL,
        market_data_provider=provider,
        start=candles[0].candle_ts,
        end=candles[-1].candle_ts + timedelta(minutes=1),
        context_provider=FixtureBacktestContextProvider(),
        data_version="fixture-v1",
        feature_version="feature_engine_v1",
    )
    await runner.run()

    session = SessionLocal()
    try:
        live_rows = session.execute(
            text("SELECT COUNT(*) FROM strategy_outcomes WHERE symbol = :sym AND is_backtest = FALSE"), {"sym": SYMBOL}
        ).scalar_one()
        assert live_rows == 0
    finally:
        session.close()


async def test_empty_candle_range_raises_rather_than_silently_no_ops():
    provider = FixtureCandleProvider.single(SYMBOL, "1m", [])
    strategy = _OneShotStubStrategy(
        _make_config(),
        fire_on_call_index=0,
        opportunity=Opportunity(
            strategy="STUB_ONE_SHOT", version="stub_v1", direction="BUY", confidence=0.5,
            structural_invalidation=1.0, structural_target=2.0,
            evidence={"conditions": {}, "reason": "unused", "basis": "closed"},
            setup_detected_at=datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc),
        ),
    )
    runner = BacktestRunner(
        strategy=strategy,
        symbol=SYMBOL,
        market_data_provider=provider,
        start=datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc),
        end=datetime(2026, 1, 28, 14, 40, tzinfo=timezone.utc),
        context_provider=FixtureBacktestContextProvider(),
        data_version="fixture-v1",
        feature_version="feature_engine_v1",
    )
    with pytest.raises(ValueError, match="zero candles"):
        await runner.run()
