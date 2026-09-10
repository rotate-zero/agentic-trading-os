"""
Performance Intelligence read-side query-layer tests — decision #122,
strategy-engine-design.md §5. Exercises `performance_queries.py`'s two
real GROUP BY queries against real Postgres, same posture as
test_performance_intelligence.py (decision #120): skipped as a whole,
not failed, if a real local Postgres isn't reachable.

Every synthetic outcome here is written via the real
`record_strategy_outcome()` write path (decision #120) — never a raw
SQL INSERT that bypasses its `entry_qty == exit_qty` invariant, same
"prove the contract, don't fabricate the caller" precedent
test_performance_intelligence.py itself already established.

**NULL realized_r — checked, not assumed inapplicable.**
`StrategyOutcomeRecord.realized_r` is `Numeric(10, 4), nullable=False`
(app/models/trading_intelligence.py) and `StrategyOutcome.realized_r`
is `float` with no default and no `| None`
(app/schemas/performance.py) — confirmed directly before writing this
file. Every row `record_strategy_outcome()` can possibly write has a
real, non-NULL `realized_r`; there is no code path that produces a NULL
population for either metric to define a policy for, so no test here
exercises a NULL-realized_r case.

**Timezone ground truth used throughout this file.** All synthetic
`entry_filled_at` values fall in September 2026, which is
Eastern Daylight Time (UTC-4) — verified directly against a real
Postgres `AT TIME ZONE 'America/New_York'` conversion before writing
these fixtures, not assumed from a UTC-offset table. `14:00Z` -> 10am
ET, `19:20Z` -> 3:20pm ET, etc.; each test spells out its own
UTC->ET arithmetic inline.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import text

from app.db.session import SessionLocal
from app.schemas.performance import StrategyOutcome
from app.trading_intelligence.performance import record_strategy_outcome
from app.trading_intelligence.performance_queries import (
    get_expectancy_by_session_type,
    get_win_rate_by_hour,
)

_STRATEGY_PREFIX = "TEST_PERF_QUERIES"


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


pytestmark = pytest.mark.skipif(not _db_available(), reason="real Postgres not reachable")


def _clean_test_rows() -> None:
    session = SessionLocal()
    try:
        session.execute(
            text("DELETE FROM strategy_outcomes WHERE strategy_name LIKE :p"),
            {"p": f"{_STRATEGY_PREFIX}%"},
        )
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _cleanup():
    _clean_test_rows()
    yield
    _clean_test_rows()


def _make_outcome(
    *,
    strategy_name: str,
    strategy_version: str = "v1",
    entry_filled_at: datetime,
    realized_r: float,
    is_backtest: bool = False,
    session_type: str | None = "regular",
) -> StrategyOutcome:
    """Minimal, realistic synthetic outcome. `context_at_entry` carries
    `session_type` only when one is given — `None` means "simulate a
    row whose context dict never had that key," exercising the honest-
    None grouping path, not an omission."""
    context_at_entry = {"session_type": session_type} if session_type is not None else {}
    return StrategyOutcome(
        outcome_id=uuid.uuid4(),
        opportunity_id=uuid.uuid4(),
        schema_version=1,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        symbol="AAPL",
        origin="auto",
        is_backtest=is_backtest,
        backtest_run_id=None,
        trading_day=entry_filled_at.date(),
        setup_detected_at=entry_filled_at,
        signal_confirmed_at=entry_filled_at,
        decided_at=entry_filled_at,
        entry_filled_at=entry_filled_at,
        exit_filled_at=entry_filled_at,
        holding_seconds=60,
        direction="BUY",
        entry_price=100.0,
        entry_qty=1,
        exit_price=100.0 + realized_r,
        exit_qty=1,
        commission_total=0.0,
        slippage_entry=0.0,
        realized_pnl=realized_r,
        realized_r=realized_r,
        exit_reason="target",
        structural_invalidation=99.0,
        structural_target=102.0,
        final_stop=99.0,
        final_target=102.0,
        confidence_at_signal=0.5,
        evidence={},
        market_state_at_entry={},
        context_at_entry=context_at_entry,
        market_state_at_exit={},
        context_at_exit={},
        feature_snapshot_id=None,
    )


def _utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 10, hour, minute, tzinfo=timezone.utc)


# --- 1. Empty table -----------------------------------------------------

def test_empty_table_returns_empty_list_for_both_queries():
    assert get_win_rate_by_hour(strategy_name=f"{_STRATEGY_PREFIX}_NOTHING_HERE") == []
    assert get_expectancy_by_session_type(strategy_name=f"{_STRATEGY_PREFIX}_NOTHING_HERE") == []


# --- 2. Win rate by hour --------------------------------------------------

def test_win_rate_by_hour_groups_correctly_and_computes_exact_win_rate():
    strategy = f"{_STRATEGY_PREFIX}_WINRATE"
    # 14:00Z and 14:45Z -> both 10am ET (hour_et=10): one win, one loss.
    record_strategy_outcome(_make_outcome(strategy_name=strategy, entry_filled_at=_utc(14, 0), realized_r=2.0))
    record_strategy_outcome(_make_outcome(strategy_name=strategy, entry_filled_at=_utc(14, 45), realized_r=-1.0))
    # 19:20Z -> 3:20pm ET (hour_et=15): breakeven, NOT a win.
    record_strategy_outcome(_make_outcome(strategy_name=strategy, entry_filled_at=_utc(19, 20), realized_r=0.0))

    results = get_win_rate_by_hour(strategy_name=strategy)

    assert [r.hour_et for r in results] == [10, 15]  # deterministic ascending order
    hour_10, hour_15 = results
    assert hour_10.total_trades == 2
    assert hour_10.win_count == 1
    assert hour_10.win_rate == pytest.approx(0.5)
    assert hour_15.total_trades == 1
    assert hour_15.win_count == 0  # realized_r == 0 is explicitly not a win
    assert hour_15.win_rate == pytest.approx(0.0)


# --- 3. Expectancy by session_type ----------------------------------------

def test_expectancy_by_session_type_groups_correctly_and_computes_exact_average():
    strategy = f"{_STRATEGY_PREFIX}_EXPECTANCY"
    record_strategy_outcome(
        _make_outcome(strategy_name=strategy, entry_filled_at=_utc(13, 0), realized_r=1.0, session_type="regular")
    )
    record_strategy_outcome(
        _make_outcome(strategy_name=strategy, entry_filled_at=_utc(13, 30), realized_r=3.0, session_type="regular")
    )
    record_strategy_outcome(
        _make_outcome(
            strategy_name=strategy, entry_filled_at=_utc(11, 0), realized_r=-2.0, session_type="pre_market"
        )
    )

    results = get_expectancy_by_session_type(strategy_name=strategy)

    by_type = {r.session_type: r for r in results}
    assert set(by_type) == {"pre_market", "regular"}
    assert [r.session_type for r in results] == ["pre_market", "regular"]  # alphabetical, deterministic
    assert by_type["regular"].trade_count == 2
    assert by_type["regular"].expectancy_r == pytest.approx(2.0)  # (1.0 + 3.0) / 2
    assert by_type["pre_market"].trade_count == 1
    assert by_type["pre_market"].expectancy_r == pytest.approx(-2.0)


def test_expectancy_by_session_type_groups_missing_key_as_honest_none():
    strategy = f"{_STRATEGY_PREFIX}_NOKEY"
    record_strategy_outcome(
        _make_outcome(strategy_name=strategy, entry_filled_at=_utc(13, 0), realized_r=4.0, session_type=None)
    )

    results = get_expectancy_by_session_type(strategy_name=strategy)

    assert len(results) == 1
    assert results[0].session_type is None
    assert results[0].trade_count == 1
    assert results[0].expectancy_r == pytest.approx(4.0)


# --- 4. Backtest isolation --------------------------------------------------

def test_backtest_isolation_never_blends_live_and_backtest():
    strategy = f"{_STRATEGY_PREFIX}_BACKTEST"
    # Live: two outcomes at hour_et=12, one win one loss.
    record_strategy_outcome(
        _make_outcome(strategy_name=strategy, entry_filled_at=_utc(16, 0), realized_r=1.0, is_backtest=False)
    )
    record_strategy_outcome(
        _make_outcome(strategy_name=strategy, entry_filled_at=_utc(16, 15), realized_r=-1.0, is_backtest=False)
    )
    # Backtest: three outcomes at the same hour_et=12, all wins — deliberately
    # different counts/results from the live set so any accidental blending
    # is detectable via total_trades and win_rate both.
    for r in (5.0, 6.0, 7.0):
        record_strategy_outcome(
            _make_outcome(strategy_name=strategy, entry_filled_at=_utc(16, 30), realized_r=r, is_backtest=True)
        )

    live_results = get_win_rate_by_hour(strategy_name=strategy)  # default is_backtest=False
    assert len(live_results) == 1
    assert live_results[0].total_trades == 2
    assert live_results[0].win_count == 1
    assert live_results[0].win_rate == pytest.approx(0.5)

    backtest_results = get_win_rate_by_hour(strategy_name=strategy, is_backtest=True)
    assert len(backtest_results) == 1
    assert backtest_results[0].total_trades == 3
    assert backtest_results[0].win_count == 3
    assert backtest_results[0].win_rate == pytest.approx(1.0)

    # Same isolation holds for the other query.
    live_expectancy = get_expectancy_by_session_type(strategy_name=strategy)
    assert live_expectancy[0].trade_count == 2
    assert live_expectancy[0].expectancy_r == pytest.approx(0.0)  # (1.0 + -1.0) / 2

    backtest_expectancy = get_expectancy_by_session_type(strategy_name=strategy, is_backtest=True)
    assert backtest_expectancy[0].trade_count == 3
    assert backtest_expectancy[0].expectancy_r == pytest.approx(6.0)  # (5+6+7)/3


# --- 5. Strategy-version isolation ------------------------------------------

def test_strategy_version_isolation_does_not_blend_versions():
    strategy = f"{_STRATEGY_PREFIX}_VERSIONS"
    record_strategy_outcome(
        _make_outcome(strategy_name=strategy, strategy_version="v1", entry_filled_at=_utc(14, 0), realized_r=1.0)
    )
    record_strategy_outcome(
        _make_outcome(strategy_name=strategy, strategy_version="v1", entry_filled_at=_utc(14, 10), realized_r=3.0)
    )
    record_strategy_outcome(
        _make_outcome(strategy_name=strategy, strategy_version="v2", entry_filled_at=_utc(14, 0), realized_r=-9.0)
    )

    v1_results = get_win_rate_by_hour(strategy_name=strategy, strategy_version="v1")
    assert len(v1_results) == 1
    assert v1_results[0].total_trades == 2  # not 3 — v2's row excluded
    assert v1_results[0].win_count == 2

    v2_results = get_win_rate_by_hour(strategy_name=strategy, strategy_version="v2")
    assert len(v2_results) == 1
    assert v2_results[0].total_trades == 1
    assert v2_results[0].win_count == 0

    # Unfiltered by version: both versions' trades appear, at the same hour bucket.
    unfiltered = get_win_rate_by_hour(strategy_name=strategy)
    assert len(unfiltered) == 1
    assert unfiltered[0].total_trades == 3


# --- 6. Strategy-name filter -------------------------------------------------

def test_strategy_name_filter_excludes_other_strategies():
    strategy_a = f"{_STRATEGY_PREFIX}_ALPHA"
    strategy_b = f"{_STRATEGY_PREFIX}_BETA"
    record_strategy_outcome(_make_outcome(strategy_name=strategy_a, entry_filled_at=_utc(14, 0), realized_r=1.0))
    record_strategy_outcome(_make_outcome(strategy_name=strategy_b, entry_filled_at=_utc(14, 0), realized_r=-1.0))

    alpha_results = get_win_rate_by_hour(strategy_name=strategy_a)
    assert len(alpha_results) == 1
    assert alpha_results[0].total_trades == 1
    assert alpha_results[0].win_count == 1

    beta_results = get_win_rate_by_hour(strategy_name=strategy_b)
    assert len(beta_results) == 1
    assert beta_results[0].total_trades == 1
    assert beta_results[0].win_count == 0


# --- 7. Invalid version-only filter -----------------------------------------

def test_strategy_version_without_strategy_name_raises_value_error():
    with pytest.raises(ValueError, match="strategy_name"):
        get_win_rate_by_hour(strategy_version="v1")

    with pytest.raises(ValueError, match="strategy_name"):
        get_expectancy_by_session_type(strategy_version="v1")
