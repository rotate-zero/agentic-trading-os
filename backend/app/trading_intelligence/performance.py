"""
Performance Intelligence's write path — decision #120,
strategy-engine-design.md §5/§11. Pairs with
app/schemas/performance.py (the `StrategyOutcome`/`BacktestRun`
Pydantic contracts) and app/models/trading_intelligence.py (the
`StrategyOutcomeRecord`/`BacktestRunRecord` ORM tables).

This module has no real caller wired into it — Execution Engine and
Position Monitor don't exist yet. Its only caller today is this task's
own test suite (test_performance_intelligence.py), which constructs a
synthetic `StrategyOutcome` directly — same "prove the contract, don't
fabricate the caller" precedent app/trading_intelligence/state_snapshot.py
(decision #98) already established for the read side. Do NOT wire this
into any live pipeline as part of this or a future task without a real
Execution Engine/Position Monitor to drive it.
"""
from __future__ import annotations

from app.db.session import SessionLocal
from app.models.trading_intelligence import StrategyOutcomeRecord
from app.schemas.performance import StrategyOutcome


def record_strategy_outcome(outcome: StrategyOutcome) -> None:
    """Persists one closed-trade `StrategyOutcome` to `strategy_outcomes`.

    **Deliberately does NOT follow `MarketStateEngine._persist`'s
    catch-log-rollback-and-swallow pattern**
    (app/market_state_engine/engine.py) for the `entry_qty == exit_qty`
    invariant below, even though that function is this project's only
    other example of a write-time assertion guarding a persisted row.
    That pattern is the right posture for an unattended background
    worker that must never crash a live, debounced pipeline over one
    bad write. It is the wrong posture here: §5/§11 (decision #120)
    require this invariant to raise, not silently vanish into a log
    line, because (a) this function has no live caller yet to protect
    from crashing, and (b) a future caller that DOES exist (Execution
    Engine/Position Monitor) needs to learn synchronously that its own
    accounting is inconsistent, not have that fact swallowed on its
    behalf. Do not "fix" this back to the soft-fail pattern by copying
    `MarketStateEngine._persist` — the divergence here is intentional,
    not an oversight.

    The `entry_qty == exit_qty` check runs BEFORE `SessionLocal()` is
    even opened — no session, no transaction, and no partial write are
    ever attempted for an inconsistent outcome; the check is not caught
    by anything downstream. A genuine DB-layer error during the write
    itself (e.g. `backtest_run_id` referencing a `backtests` row that
    doesn't exist) is rolled back, then re-raised — not swallowed, same
    reasoning as above.
    """
    if outcome.entry_qty != outcome.exit_qty:
        raise ValueError(
            f"StrategyOutcome invariant violated: entry_qty ({outcome.entry_qty}) != "
            f"exit_qty ({outcome.exit_qty}) for outcome_id={outcome.outcome_id} — refusing to "
            "persist. A closed trade's StrategyOutcome must record equal entry and exit "
            "quantity (§5/§11)."
        )

    row = StrategyOutcomeRecord(
        outcome_id=outcome.outcome_id,
        opportunity_id=outcome.opportunity_id,
        schema_version=outcome.schema_version,
        strategy_name=outcome.strategy_name,
        strategy_version=outcome.strategy_version,
        symbol=outcome.symbol,
        origin=outcome.origin,
        is_backtest=outcome.is_backtest,
        backtest_run_id=outcome.backtest_run_id,
        trading_day=outcome.trading_day,
        setup_detected_at=outcome.setup_detected_at,
        signal_confirmed_at=outcome.signal_confirmed_at,
        decided_at=outcome.decided_at,
        entry_filled_at=outcome.entry_filled_at,
        exit_filled_at=outcome.exit_filled_at,
        holding_seconds=outcome.holding_seconds,
        direction=outcome.direction,
        entry_price=outcome.entry_price,
        entry_qty=outcome.entry_qty,
        exit_price=outcome.exit_price,
        exit_qty=outcome.exit_qty,
        commission_total=outcome.commission_total,
        slippage_entry=outcome.slippage_entry,
        realized_pnl=outcome.realized_pnl,
        realized_r=outcome.realized_r,
        exit_reason=outcome.exit_reason,
        structural_invalidation=outcome.structural_invalidation,
        structural_target=outcome.structural_target,
        final_stop=outcome.final_stop,
        final_target=outcome.final_target,
        confidence_at_signal=outcome.confidence_at_signal,
        evidence=outcome.evidence,
        market_state_at_entry=outcome.market_state_at_entry,
        context_at_entry=outcome.context_at_entry,
        market_state_at_exit=outcome.market_state_at_exit,
        context_at_exit=outcome.context_at_exit,
        feature_snapshot_id=outcome.feature_snapshot_id,
    )

    session = SessionLocal()
    try:
        session.add(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
