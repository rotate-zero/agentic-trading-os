"""
Unit 3 tests — pure, DB-free. `fill_simulator.py` tested directly against
synthetic candle sequences (no engines, no DB). `gate_and_warmup.py`'s
`check_entry_allowed()` tested with `capture_market_state_snapshot`/
`capture_context_snapshot` mocked (unittest.mock.patch) — this module's
own logic (branching, reason strings) is what's under test here, not the
real capture functions themselves (those are exercised for real in
Unit 2's `test_replay_state_producer.py`, DB-gated).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.backtest_runner.fill_simulator import (
    InsufficientReplayDataError,
    compute_realized_pnl,
    compute_realized_r,
    regular_session_close_utc,
    simulate_entry,
    simulate_exit,
)
from app.backtest_runner.gate_and_warmup import check_entry_allowed
from app.broker_adapters.base import Candle
from app.core.market_clock import MarketClock
from app.strategy_engine.base_strategy import Opportunity, StrategyConfig

_CLOCK = MarketClock()
_REGULAR_DAY_OPEN = datetime(2026, 1, 28, 14, 30, tzinfo=timezone.utc)  # 09:30 ET, real Wednesday, not a holiday/half-day


def _candle(ts: datetime, o: float, h: float, l: float, c: float, v: int = 1000) -> Candle:
    return Candle(timeframe="1m", open=o, high=h, low=l, close=c, volume=v, candle_ts=ts)


def _minute_candles(n: int, start: datetime = _REGULAR_DAY_OPEN, price: float = 100.0, drift: float = 0.0) -> list[Candle]:
    candles = []
    for i in range(n):
        o = price
        c = round(price + drift, 4)
        candles.append(_candle(start + timedelta(minutes=i), o, max(o, c) + 0.05, min(o, c) - 0.05, c))
        price = c
    return candles


def _opportunity(direction: str = "BUY", target: float = 105.0, invalidation: float = 98.0, status: str = "actionable") -> Opportunity:
    return Opportunity(
        strategy="TEST",
        version="test_v1",
        direction=direction,
        confidence=0.8,
        structural_invalidation=invalidation,
        structural_target=target,
        evidence={"conditions": {}, "reason": "test", "basis": "closed"},
        status=status,
        setup_detected_at=_REGULAR_DAY_OPEN,
    )


# --- simulate_entry -----------------------------------------------------


def test_simulate_entry_fills_at_next_candle_open():
    candles = _minute_candles(5, price=100.0, drift=0.5)
    fill = simulate_entry(_opportunity(), candles, signal_index=1)
    assert fill is not None
    assert fill.entry_price == candles[2].open
    assert fill.entry_ts == candles[2].candle_ts
    assert fill.entry_candle_index == 2


def test_simulate_entry_returns_none_for_non_actionable_status():
    candles = _minute_candles(5)
    for status in ("potential", "waiting", "expired"):
        assert simulate_entry(_opportunity(status=status), candles, signal_index=0) is None


def test_simulate_entry_returns_none_when_no_next_candle():
    candles = _minute_candles(3)
    assert simulate_entry(_opportunity(), candles, signal_index=2) is None  # signal on the LAST candle — nothing to fill against


# --- simulate_exit: target / stop / tie-break ----------------------------


def test_simulate_exit_hits_target():
    entry_ts = _REGULAR_DAY_OPEN + timedelta(minutes=1)
    candles = [
        _candle(_REGULAR_DAY_OPEN, 100, 100.5, 99.5, 100),
        _candle(entry_ts, 100, 100.5, 99.5, 100.2),  # entry fill candle (index 1)
        _candle(entry_ts + timedelta(minutes=1), 100.2, 102, 100.1, 101.8),  # neither touched
        _candle(entry_ts + timedelta(minutes=2), 101.8, 106, 101.5, 105.5),  # target (105) touched via high
    ]
    opp = _opportunity(direction="BUY", target=105.0, invalidation=98.0)
    entry_fill = simulate_entry(opp, candles, signal_index=0)
    assert entry_fill.entry_candle_index == 1

    exit_fill = simulate_exit(opp, entry_fill, candles, clock=_CLOCK)
    assert exit_fill.exit_reason == "target"
    assert exit_fill.exit_price == 105.0
    assert exit_fill.exit_candle_index == 3


def test_simulate_exit_hits_stop():
    entry_ts = _REGULAR_DAY_OPEN + timedelta(minutes=1)
    candles = [
        _candle(_REGULAR_DAY_OPEN, 100, 100.5, 99.5, 100),
        _candle(entry_ts, 100, 100.5, 99.5, 100.2),
        _candle(entry_ts + timedelta(minutes=1), 100.2, 100.4, 97.5, 98.0),  # stop (98) touched via low
    ]
    opp = _opportunity(direction="BUY", target=105.0, invalidation=98.0)
    entry_fill = simulate_entry(opp, candles, signal_index=0)

    exit_fill = simulate_exit(opp, entry_fill, candles, clock=_CLOCK)
    assert exit_fill.exit_reason == "stop"
    assert exit_fill.exit_price == 98.0


def test_simulate_exit_same_candle_both_touched_stop_wins():
    entry_ts = _REGULAR_DAY_OPEN + timedelta(minutes=1)
    candles = [
        _candle(_REGULAR_DAY_OPEN, 100, 100.5, 99.5, 100),
        _candle(entry_ts, 100, 100.5, 99.5, 100.2),
        # This candle's range spans BOTH target (105) and stop (98) —
        # a huge, deliberately contrived bar to force the tie.
        _candle(entry_ts + timedelta(minutes=1), 100.2, 106, 97, 101),
    ]
    opp = _opportunity(direction="BUY", target=105.0, invalidation=98.0)
    entry_fill = simulate_entry(opp, candles, signal_index=0)

    exit_fill = simulate_exit(opp, entry_fill, candles, clock=_CLOCK)
    assert exit_fill.exit_reason == "stop"  # conservative v1 convention, confirmed by Saqib


def test_simulate_exit_sell_direction_target_and_stop_are_mirrored():
    entry_ts = _REGULAR_DAY_OPEN + timedelta(minutes=1)
    candles = [
        _candle(_REGULAR_DAY_OPEN, 100, 100.5, 99.5, 100),
        _candle(entry_ts, 100, 100.5, 99.5, 100.2),
        _candle(entry_ts + timedelta(minutes=1), 100.2, 100.3, 94.5, 95.0),  # SELL target (95) touched via LOW
    ]
    opp = _opportunity(direction="SELL", target=95.0, invalidation=103.0)
    entry_fill = simulate_entry(opp, candles, signal_index=0)

    exit_fill = simulate_exit(opp, entry_fill, candles, clock=_CLOCK)
    assert exit_fill.exit_reason == "target"
    assert exit_fill.exit_price == 95.0


# --- simulate_exit: eod_flatten vs. InsufficientReplayDataError ----------


def test_simulate_exit_eod_flatten_at_real_session_close():
    entry_ts = _REGULAR_DAY_OPEN + timedelta(minutes=1)
    close_ts = regular_session_close_utc(_CLOCK, _CLOCK.trading_day(entry_ts))
    candles = [
        _candle(_REGULAR_DAY_OPEN, 100, 100.5, 99.5, 100),
        _candle(entry_ts, 100, 100.5, 99.5, 100.2),  # entry fill, index 1 — never touches target/stop
        _candle(close_ts - timedelta(minutes=1), 100.2, 100.3, 100.1, 100.25),
        _candle(close_ts, 100.25, 100.3, 100.2, 100.28),  # exactly at real session close
    ]
    opp = _opportunity(direction="BUY", target=999.0, invalidation=1.0)  # unreachable either way
    entry_fill = simulate_entry(opp, candles, signal_index=0)

    exit_fill = simulate_exit(opp, entry_fill, candles, clock=_CLOCK)
    assert exit_fill.exit_reason == "eod_flatten"
    assert exit_fill.exit_ts == close_ts
    assert exit_fill.exit_price == candles[3].close


def test_simulate_exit_raises_insufficient_data_when_fixture_ends_before_close():
    """The real distinguishing case: replay data simply doesn't reach the
    real session close. Must NOT be silently reported as eod_flatten."""
    entry_ts = _REGULAR_DAY_OPEN + timedelta(minutes=1)
    candles = [
        _candle(_REGULAR_DAY_OPEN, 100, 100.5, 99.5, 100),
        _candle(entry_ts, 100, 100.5, 99.5, 100.2),
        _candle(entry_ts + timedelta(minutes=1), 100.2, 100.3, 100.1, 100.25),  # fixture stops here — still mid-morning
    ]
    opp = _opportunity(direction="BUY", target=999.0, invalidation=1.0)
    entry_fill = simulate_entry(opp, candles, signal_index=0)

    with pytest.raises(InsufficientReplayDataError, match="doesn't extend far enough"):
        simulate_exit(opp, entry_fill, candles, clock=_CLOCK)


def test_simulate_exit_raises_insufficient_data_when_next_day_starts_immediately():
    """Crossing into the next trading day with zero same-day candles after
    entry is the same honest-absence condition, reached via a different
    path through the loop (last_same_day_index stays None)."""
    entry_ts = _REGULAR_DAY_OPEN + timedelta(minutes=1)
    next_day_open = entry_ts + timedelta(days=1)
    candles = [
        _candle(_REGULAR_DAY_OPEN, 100, 100.5, 99.5, 100),
        _candle(entry_ts, 100, 100.5, 99.5, 100.2),
        _candle(next_day_open, 101, 101.5, 100.5, 101.2),  # next day, immediately
    ]
    opp = _opportunity(direction="BUY", target=999.0, invalidation=1.0)
    entry_fill = simulate_entry(opp, candles, signal_index=0)

    with pytest.raises(InsufficientReplayDataError, match="no candle after the entry fill"):
        simulate_exit(opp, entry_fill, candles, clock=_CLOCK)


# --- realized_r / realized_pnl -------------------------------------------


def test_compute_realized_r_and_pnl_buy():
    opp = _opportunity(direction="BUY", target=110.0, invalidation=95.0)
    from app.backtest_runner.fill_simulator import EntryFill, ExitFill

    entry_fill = EntryFill(entry_price=100.0, entry_ts=_REGULAR_DAY_OPEN, entry_candle_index=1)
    exit_fill = ExitFill(exit_price=110.0, exit_ts=_REGULAR_DAY_OPEN, exit_reason="target", exit_candle_index=5)

    # risk = |100 - 95| = 5; pnl = 110 - 100 = 10; R = 10/5 = 2.0
    assert compute_realized_r(opp, entry_fill, exit_fill) == pytest.approx(2.0)
    assert compute_realized_pnl(opp, entry_fill, exit_fill) == pytest.approx(10.0)


def test_compute_realized_r_and_pnl_sell():
    opp = _opportunity(direction="SELL", target=90.0, invalidation=105.0)
    from app.backtest_runner.fill_simulator import EntryFill, ExitFill

    entry_fill = EntryFill(entry_price=100.0, entry_ts=_REGULAR_DAY_OPEN, entry_candle_index=1)
    exit_fill = ExitFill(exit_price=92.0, exit_ts=_REGULAR_DAY_OPEN, exit_reason="target", exit_candle_index=5)

    # risk = |100 - 105| = 5; pnl = 100 - 92 = 8; R = 8/5 = 1.6
    assert compute_realized_r(opp, entry_fill, exit_fill) == pytest.approx(1.6)
    assert compute_realized_pnl(opp, entry_fill, exit_fill) == pytest.approx(8.0)


def test_compute_realized_r_raises_on_zero_planned_risk():
    opp = _opportunity(direction="BUY", target=110.0, invalidation=100.0)
    from app.backtest_runner.fill_simulator import EntryFill, ExitFill

    entry_fill = EntryFill(entry_price=100.0, entry_ts=_REGULAR_DAY_OPEN, entry_candle_index=1)  # entry == invalidation
    exit_fill = ExitFill(exit_price=105.0, exit_ts=_REGULAR_DAY_OPEN, exit_reason="target", exit_candle_index=5)

    with pytest.raises(ValueError, match="zero planned risk"):
        compute_realized_r(opp, entry_fill, exit_fill)


# --- check_entry_allowed (gate_and_warmup) --------------------------------


def _config(gate_conditions: dict | None = None) -> StrategyConfig:
    return StrategyConfig(
        strategy_name="TEST",
        version="test_v1",
        gate_conditions=gate_conditions or {},
        params={},
        active_from=_REGULAR_DAY_OPEN,
    )


def test_check_entry_allowed_fails_gate_conditions_before_touching_snapshots():
    config = _config(gate_conditions={"session": "regular"})
    off_hours_ts = _REGULAR_DAY_OPEN.replace(hour=2)  # 2am ET — not a regular session under any circumstance

    with patch("app.backtest_runner.gate_and_warmup.capture_market_state_snapshot") as ms_mock, patch(
        "app.backtest_runner.gate_and_warmup.capture_context_snapshot"
    ) as ctx_mock:
        result = check_entry_allowed(config, off_hours_ts, "AAPL")

    assert result.allowed is False
    assert "gate_conditions" in result.reason
    ms_mock.assert_not_called()
    ctx_mock.assert_not_called()


def test_check_entry_allowed_d17_blocks_when_either_snapshot_missing():
    config = _config()
    with patch("app.backtest_runner.gate_and_warmup.capture_market_state_snapshot", return_value=None), patch(
        "app.backtest_runner.gate_and_warmup.capture_context_snapshot", return_value={"calendar": {}}
    ):
        result = check_entry_allowed(config, _REGULAR_DAY_OPEN, "AAPL")

    assert result.allowed is False
    assert "D17" in result.reason
    assert "market_state" in result.reason


def test_check_entry_allowed_true_when_gate_and_both_snapshots_present():
    config = _config()
    ms = {"trend_score": 60.0}
    ctx = {"calendar": {"session": "open"}}
    with patch("app.backtest_runner.gate_and_warmup.capture_market_state_snapshot", return_value=ms), patch(
        "app.backtest_runner.gate_and_warmup.capture_context_snapshot", return_value=ctx
    ):
        result = check_entry_allowed(config, _REGULAR_DAY_OPEN, "AAPL")

    assert result.allowed is True
    assert result.reason == "ok"
    assert result.market_state_snapshot == ms
    assert result.context_snapshot == ctx
