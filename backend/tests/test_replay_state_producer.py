"""
Unit 2 tests — real Postgres, DB-gated (same convention
`test_strategy_scheduler.py`'s own real-engine integration tests use).
Covers: EngineBackedReplayStateProducer settles real Feature/MarketState/
Context state to the replayed candle_ts (never wall-clock), D17
availability via the real capture functions once engines are installed
via engine_singleton_guard, singleton install/restore correctness, and
ReplaySettleTimeout firing honestly rather than returning stale state.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.backtest_runner.context_provider import FixtureBacktestContextProvider
from app.backtest_runner.engine_singleton_guard import install_replay_engines
from app.backtest_runner.replay_state_producer import EngineBackedReplayStateProducer, ReplaySettleTimeout
from app.broker_adapters.base import Candle
from app.db.session import SessionLocal
from app.trading_intelligence.state_snapshot import capture_context_snapshot, capture_market_state_snapshot

SYMBOL = "ZZTEST"  # deliberately not a real ticker — never collides with real data


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
    """Deletes every row this producer's full engine pipeline could have
    written for `ticker`, in FK-safe order, before deleting the `symbols`
    row itself. Wider than `test_strategy_scheduler.py`'s own
    `_clean_test_symbol` (which only touches `market_state_history`):
    that precedent never wires `LevelInteractionEngine`, so it never
    needed to — this producer does (D9's First Pullback/Reversal
    dependency), so `level_interaction_state`/`level_interaction_events`
    need cleanup too, confirmed by checking every FK referencing
    `symbols.id` directly rather than guessing which tables apply."""
    session = SessionLocal()
    try:
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


pytestmark = pytest.mark.skipif(not _db_available(), reason="Postgres not reachable at the configured DATABASE settings")


def _fixture_candles(n: int, start: datetime) -> list[Candle]:
    price = 50.0
    candles = []
    for i in range(n):
        o, c = price, price + 0.10
        candles.append(
            Candle(
                timeframe="1m",
                open=round(o, 2),
                high=round(c + 0.02, 2),
                low=round(o - 0.02, 2),
                close=round(c, 2),
                volume=1000 + i,
                candle_ts=start + timedelta(minutes=i),
            )
        )
        price = c
    return candles


@pytest.fixture(autouse=True)
def _clean_before_and_after():
    _clean_test_symbol(SYMBOL)
    yield
    _clean_test_symbol(SYMBOL)


async def test_advance_to_settles_state_to_the_replayed_candle_ts_not_wall_clock():
    """The core Unit 2 claim: candle_ts is the only source of historical
    truth. Real wall-clock 'now' at test-run time is irrelevant here by
    construction — every returned timestamp must equal the REPLAYED
    candle's own candle_ts, for every candle, including ones whose
    candle_ts is nowhere near the actual test execution time."""
    start = datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc)  # real Wednesday, real FOMC date
    candles = _fixture_candles(3, start)

    producer = EngineBackedReplayStateProducer(context_provider=FixtureBacktestContextProvider())
    await producer.start()
    try:
        for candle in candles:
            state = await producer.advance_to(SYMBOL, candle)
            assert state.candle_ts == candle.candle_ts
            assert state.features.candle_ts == candle.candle_ts
            assert state.market_state.candle_ts == candle.candle_ts
            assert state.symbol == SYMBOL
            # Real calendar facts for the REPLAYED date, not test wall-clock time.
            assert state.context.providers["calendar"]["fed_day"] is True
            assert state.context.providers["calendar"]["trading_day"] == "2026-01-28"
    finally:
        await producer.stop()


async def test_d17_snapshots_available_once_engines_installed_as_singleton():
    """The real D17 caller (state_snapshot.py's two capture functions)
    resolves through the process-wide singleton getters — this confirms
    they return None BEFORE engine_singleton_guard installs this
    producer's engines, and real (non-fabricated) dicts AFTER, proving
    the guard is actually necessary and actually sufficient, not just
    plausible."""
    start = datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc)
    candle = _fixture_candles(1, start)[0]

    producer = EngineBackedReplayStateProducer(context_provider=FixtureBacktestContextProvider())
    await producer.start()
    try:
        # Before installation: the real capture functions must not
        # magically resolve to this producer's not-yet-installed engines.
        assert capture_market_state_snapshot(SYMBOL) is None
        assert capture_context_snapshot(SYMBOL) is None

        async with install_replay_engines(
            feature_engine=producer.feature_engine,
            level_interaction_engine=producer.level_interaction_engine,
            market_state_engine=producer.market_state_engine,
            context_engine=producer.context_engine,
        ):
            await producer.advance_to(SYMBOL, candle)

            ms_snapshot = capture_market_state_snapshot(SYMBOL)
            ctx_snapshot = capture_context_snapshot(SYMBOL)
            assert ms_snapshot is not None
            assert ctx_snapshot is not None
            assert "calendar" in ctx_snapshot
            # Honest absence, not fabrication: fundamentals/news were
            # never wired (FixtureBacktestContextProvider's whole point)
            # — they must be genuinely absent from the dict, not present
            # with a placeholder/None value standing in.
            assert "fundamentals" not in ctx_snapshot
            assert "news" not in ctx_snapshot

        # After the `async with` exits: singleton restored, real capture
        # functions must go back to None (or whatever they were before —
        # here, None), never keep pointing at the torn-down producer.
        assert capture_market_state_snapshot(SYMBOL) is None
    finally:
        await producer.stop()


async def test_engine_singleton_guard_restores_prior_value_even_on_exception():
    import app.market_state_engine.engine as market_state_engine_module

    sentinel_prev = market_state_engine_module._market_state_engine
    producer = EngineBackedReplayStateProducer(context_provider=FixtureBacktestContextProvider())

    with pytest.raises(RuntimeError, match="boom"):
        async with install_replay_engines(
            feature_engine=producer.feature_engine,
            level_interaction_engine=producer.level_interaction_engine,
            market_state_engine=producer.market_state_engine,
            context_engine=producer.context_engine,
        ):
            assert market_state_engine_module._market_state_engine is producer.market_state_engine
            raise RuntimeError("boom")

    assert market_state_engine_module._market_state_engine is sentinel_prev


async def test_replay_settle_timeout_raised_honestly_not_stale_state_returned():
    """A producer configured with an impossibly short settle timeout must
    raise ReplaySettleTimeout, never silently hand back state from a
    prior candle as if it were current."""
    start = datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc)
    candles = _fixture_candles(2, start)

    producer = EngineBackedReplayStateProducer(
        context_provider=FixtureBacktestContextProvider(),
        settle_timeout_seconds=0.001,  # shorter than MarketStateEngine's real 1.0s debounce floor
        settle_poll_interval_seconds=0.0005,
    )
    await producer.start()
    try:
        await producer.advance_to(SYMBOL, candles[0])  # first trigger runs synchronously — should succeed
        with pytest.raises(ReplaySettleTimeout):
            # second candle within the same process hits the real 1.0s
            # debounce floor — 0.001s is nowhere near enough to clear it
            await producer.advance_to(SYMBOL, candles[1])
    finally:
        await producer.stop()
