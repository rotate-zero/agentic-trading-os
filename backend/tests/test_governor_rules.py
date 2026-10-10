"""
Pure, DB-free tests for app/governor/rules.py — AGENTS.md testing
philosophy ("pure/DB-free tests for pure functions"). No EventBus, no
Postgres, no fake ports: every input is a plain dataclass built directly
in each test. Covers this task's owned AC #3, #4, #16 (via config.py's
own validators, tested separately in test_governor_config.py), and #17
(the daily-loss gate's full case table).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.trade_planning.plan import ReferenceObservation
from app.trade_planning.planner import plan_entry

from app.governor.ports import OpenExposure, PortfolioSnapshot
from app.governor.rules import (
    AuthorizationContext,
    LimitsSnapshot,
    OpportunityView,
    evaluate_authorization,
)

_NOW = datetime(2026, 9, 22, 15, 0, tzinfo=timezone.utc)
_TRADING_DAY = date(2026, 9, 22)


def _opportunity(
    *,
    direction: str = "BUY",
    status: str = "actionable",
    structural_invalidation: float = 98.0,
    structural_target: float = 110.0,
    confidence: float = 0.8,
) -> OpportunityView:
    return OpportunityView(
        strategy="TEST",
        version="test_v1",
        direction=direction,  # type: ignore[arg-type]
        confidence=confidence,
        structural_invalidation=structural_invalidation,
        structural_target=structural_target,
        status=status,  # type: ignore[arg-type]
        setup_detected_at=_NOW,
    )


def _portfolio(*, realized_pnl_today: float = 0.0, exposures: tuple[OpenExposure, ...] = ()) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        execution_mode="simulated",
        trading_day=_TRADING_DAY,
        as_of=_NOW,
        realized_pnl_today=realized_pnl_today,
        exposures=exposures,
    )


def _limits(
    *, max_concurrent_positions: int = 1, fixed_notional_usd: float = 1000.0, daily_loss_cap_usd: float = 100.0
) -> LimitsSnapshot:
    return LimitsSnapshot(
        max_concurrent_positions=max_concurrent_positions,
        fixed_notional_usd=fixed_notional_usd,
        daily_loss_cap_usd=daily_loss_cap_usd,
    )


def _ctx(
    *,
    symbol: str = "AAPL",
    opportunity: OpportunityView | None = None,
    execution_mode: str | None = "simulated",
    is_regular_session: bool = True,
    market_state_snapshot_present: bool = True,
    context_snapshot_present: bool = True,
    portfolio: PortfolioSnapshot | None = None,
    reference_price: float | None = 100.0,
    fixed_notional_usd: float = 1000.0,
) -> AuthorizationContext:
    return AuthorizationContext(
        symbol=symbol,
        opportunity=opportunity or _opportunity(),
        execution_mode=execution_mode,
        is_regular_session=is_regular_session,
        market_state_snapshot_present=market_state_snapshot_present,
        context_snapshot_present=context_snapshot_present,
        portfolio=portfolio or _portfolio(),
        plan=plan_entry(symbol, opportunity or _opportunity(),
                        None if reference_price is None else ReferenceObservation(reference_price, _NOW, _NOW),
                        fixed_notional_usd, _NOW),
        now=_NOW,
    )


# --- rule 0: execution_mode gate (AC #3, #4) --------------------------------


@pytest.mark.parametrize("mode", ["paper", "live", "backtest", "bogus", None, ""])
def test_rejects_any_non_simulated_execution_mode(mode: str | None) -> None:
    result = evaluate_authorization(_ctx(execution_mode=mode), _limits())
    assert result.decision == "rejected"
    assert result.reasons == ["execution_mode_not_permitted"]


def test_approves_when_execution_mode_is_simulated_and_all_other_rules_pass() -> None:
    result = evaluate_authorization(_ctx(), _limits())
    assert result.decision == "approved"
    assert result.qty == 10  # floor(1000 / 100)
    assert result.reference_price == 100.0
    assert result.reasons == []


# --- rule 1: regular session -------------------------------------------------


def test_rejects_outside_regular_session() -> None:
    result = evaluate_authorization(_ctx(is_regular_session=False), _limits())
    assert result.decision == "rejected"
    assert result.reasons == ["outside_regular_session"]


# --- rule 2: actionable only --------------------------------------------------


@pytest.mark.parametrize("status", ["potential", "waiting", "expired"])
def test_rejects_non_actionable_opportunity(status: str) -> None:
    result = evaluate_authorization(_ctx(opportunity=_opportunity(status=status)), _limits())
    assert result.decision == "rejected"
    assert result.reasons == ["not_actionable"]


# --- rule 3: pre-trade snapshot gate ------------------------------------------


def test_rejects_missing_market_state_snapshot() -> None:
    result = evaluate_authorization(_ctx(market_state_snapshot_present=False), _limits())
    assert result.decision == "rejected"
    assert result.reasons == ["snapshot_unavailable:market_state"]


def test_rejects_missing_context_snapshot() -> None:
    result = evaluate_authorization(_ctx(context_snapshot_present=False), _limits())
    assert result.decision == "rejected"
    assert result.reasons == ["snapshot_unavailable:context"]


# --- rule 4: slots / duplicates -----------------------------------------------


def test_rejects_symbol_already_busy() -> None:
    busy = OpenExposure(
        symbol="AAPL", direction="BUY", qty=5, avg_entry_price=99.0, stop=95.0, mark=100.0, unrealized_pnl=5.0,
        is_in_flight=False,
    )
    result = evaluate_authorization(
        _ctx(symbol="AAPL", portfolio=_portfolio(exposures=(busy,))), _limits(max_concurrent_positions=5)
    )
    assert result.decision == "rejected"
    assert result.reasons == ["symbol_busy"]


def test_rejects_at_max_concurrent_positions() -> None:
    other = OpenExposure(
        symbol="MSFT", direction="BUY", qty=5, avg_entry_price=99.0, stop=95.0, mark=100.0, unrealized_pnl=5.0,
        is_in_flight=False,
    )
    result = evaluate_authorization(
        _ctx(symbol="AAPL", portfolio=_portfolio(exposures=(other,))), _limits(max_concurrent_positions=1)
    )
    assert result.decision == "rejected"
    assert result.reasons == ["max_concurrent_positions"]


def test_approves_with_raised_max_concurrent_positions() -> None:
    other = OpenExposure(
        symbol="MSFT", direction="BUY", qty=5, avg_entry_price=99.0, stop=95.0, mark=100.0, unrealized_pnl=5.0,
        is_in_flight=False,
    )
    result = evaluate_authorization(
        _ctx(symbol="AAPL", portfolio=_portfolio(exposures=(other,))), _limits(max_concurrent_positions=2)
    )
    assert result.decision == "approved"


def test_in_flight_entry_counts_toward_max_concurrent_positions() -> None:
    inflight = OpenExposure(
        symbol="MSFT", direction="BUY", qty=5, avg_entry_price=99.0, stop=95.0, mark=None, unrealized_pnl=None,
        is_in_flight=True,
    )
    result = evaluate_authorization(
        _ctx(symbol="AAPL", portfolio=_portfolio(exposures=(inflight,))), _limits(max_concurrent_positions=1)
    )
    assert result.decision == "rejected"
    assert result.reasons == ["max_concurrent_positions"]


# --- rule 5: reference price, stop geometry, sizing ---------------------------


def test_rejects_no_reference_price() -> None:
    result = evaluate_authorization(_ctx(reference_price=None), _limits())
    assert result.decision == "rejected"
    assert result.reasons == ["no_reference_price"]


def test_rejects_invalid_stop_geometry_for_long() -> None:
    # BUY requires stop BELOW reference price — this stop is above it.
    opp = _opportunity(direction="BUY", structural_invalidation=105.0)
    result = evaluate_authorization(_ctx(opportunity=opp, reference_price=100.0), _limits())
    assert result.decision == "rejected"
    assert result.reasons == ["invalid_stop_geometry"]


def test_rejects_invalid_stop_geometry_for_short() -> None:
    # SELL requires stop ABOVE reference price — this stop is below it.
    opp = _opportunity(direction="SELL", structural_invalidation=95.0, structural_target=80.0)
    result = evaluate_authorization(_ctx(opportunity=opp, reference_price=100.0), _limits())
    assert result.decision == "rejected"
    assert result.reasons == ["invalid_stop_geometry"]


def test_approves_valid_short_geometry() -> None:
    opp = _opportunity(direction="SELL", structural_invalidation=105.0, structural_target=80.0)
    result = evaluate_authorization(_ctx(opportunity=opp, reference_price=100.0), _limits())
    assert result.decision == "approved"


def test_rejects_notional_below_one_share() -> None:
    result = evaluate_authorization(
        _ctx(reference_price=2000.0), _limits(fixed_notional_usd=1000.0)
    )
    assert result.decision == "rejected"
    assert result.reasons == ["notional_below_one_share"]


# --- rule 6: daily-loss gate (I15) — the full AC #17 case table --------------


def test_daily_loss_gate_realized_loss_only() -> None:
    # $90 realized loss today, no open exposure, small candidate risk —
    # stays under the $100 cap.
    portfolio = _portfolio(realized_pnl_today=-90.0)
    opp = _opportunity(structural_invalidation=99.5)  # candidate risk: 10 * 0.5 = 5
    result = evaluate_authorization(_ctx(opportunity=opp, portfolio=portfolio), _limits())
    assert result.decision == "approved"


def test_daily_loss_gate_realized_loss_at_cap_rejects() -> None:
    portfolio = _portfolio(realized_pnl_today=-100.0)
    result = evaluate_authorization(_ctx(portfolio=portfolio), _limits())
    assert result.decision == "rejected"
    assert result.reasons == ["daily_loss_cap_reached"]


def test_daily_loss_gate_unrealized_loss_on_open_position() -> None:
    # A tight stop (loss_if_stopped = 1 * 5 = 5) but a much deeper actual
    # unrealized loss (-45) — the gate must count the ACTUAL unrealized
    # loss (max(5, 45) = 45), not just the stop-distance figure, while
    # still approving since 45 + candidate's 5 = 50 stays under the cap.
    exposure = OpenExposure(
        symbol="MSFT", direction="BUY", qty=1, avg_entry_price=100.0, stop=95.0, mark=55.0, unrealized_pnl=-45.0,
        is_in_flight=False,
    )
    portfolio = _portfolio(exposures=(exposure,))
    opp = _opportunity(structural_invalidation=99.5)  # candidate risk: 10 * 0.5 = 5; 45 + 5 = 50 < 100
    result = evaluate_authorization(_ctx(opportunity=opp, portfolio=portfolio), _limits(max_concurrent_positions=5))
    assert result.decision == "approved"


def test_daily_loss_gate_in_flight_entry_counted() -> None:
    inflight = OpenExposure(
        symbol="MSFT", direction="BUY", qty=10, avg_entry_price=100.0, stop=90.0, mark=100.0, unrealized_pnl=0.0,
        is_in_flight=True,
    )
    # loss_if_stopped = 10 * |100-90| = 100 -> already at the cap on its own
    portfolio = _portfolio(exposures=(inflight,))
    result = evaluate_authorization(_ctx(portfolio=portfolio), _limits(max_concurrent_positions=5))
    assert result.decision == "rejected"
    assert result.reasons == ["daily_loss_cap_reached"]


def test_daily_loss_gate_gap_through_stop_uses_unrealized_not_stop_distance() -> None:
    # loss_if_stopped = 10 * |100-90| = 100, but unrealized loss (a gap
    # through the stop) is WORSE: -150. max(100, 150) = 150 > cap.
    exposure = OpenExposure(
        symbol="MSFT", direction="BUY", qty=10, avg_entry_price=100.0, stop=90.0, mark=85.0, unrealized_pnl=-150.0,
        is_in_flight=False,
    )
    portfolio = _portfolio(exposures=(exposure,))
    result = evaluate_authorization(_ctx(portfolio=portfolio), _limits(max_concurrent_positions=5))
    assert result.decision == "rejected"
    assert result.reasons == ["daily_loss_cap_reached"]


def test_daily_loss_gate_missing_mark_is_unknown_and_rejects() -> None:
    exposure = OpenExposure(
        symbol="MSFT", direction="BUY", qty=10, avg_entry_price=100.0, stop=90.0, mark=None, unrealized_pnl=None,
        is_in_flight=False,
    )
    portfolio = _portfolio(exposures=(exposure,))
    result = evaluate_authorization(_ctx(portfolio=portfolio), _limits(max_concurrent_positions=5))
    assert result.decision == "rejected"
    assert result.reasons == ["loss_exposure_unknown"]


def test_daily_loss_gate_missing_stop_is_unknown_and_rejects() -> None:
    exposure = OpenExposure(
        symbol="MSFT", direction="BUY", qty=10, avg_entry_price=100.0, stop=None, mark=100.0, unrealized_pnl=0.0,
        is_in_flight=False,
    )
    portfolio = _portfolio(exposures=(exposure,))
    result = evaluate_authorization(_ctx(portfolio=portfolio), _limits(max_concurrent_positions=5))
    assert result.decision == "rejected"
    assert result.reasons == ["loss_exposure_unknown"]


def test_daily_loss_gate_candidates_own_stop_out_loss_alone_breaches_cap() -> None:
    # No existing exposure at all. Candidate: qty 10, |entry-stop| = 20 -> 200 > 100.
    opp = _opportunity(structural_invalidation=80.0)  # reference_price 100 -> risk 20/share
    result = evaluate_authorization(_ctx(opportunity=opp), _limits())
    assert result.decision == "rejected"
    assert result.reasons == ["projected_loss_exceeds_daily_cap"]


# P2: plans are inputs; only plans that reach rule 6 become audit proposals.
@pytest.mark.parametrize("changes,reason", [
    ({"execution_mode": "paper"}, "execution_mode_not_permitted"),
    ({"is_regular_session": False}, "outside_regular_session"),
    ({"opportunity": _opportunity(status="waiting")}, "not_actionable"),
    ({"market_state_snapshot_present": False}, "snapshot_unavailable:market_state"),
    ({"context_snapshot_present": False}, "snapshot_unavailable:context"),
    ({"portfolio": _portfolio(exposures=(OpenExposure("AAPL", "BUY", 1, 100., 99., 100., 0., False),))}, "symbol_busy"),
    ({"portfolio": _portfolio(exposures=(OpenExposure("MSFT", "BUY", 1, 100., 99., 100., 0., False),))}, "max_concurrent_positions"),
])
def test_earlier_rules_win_over_planning_refusal(changes, reason):
    result = evaluate_authorization(_ctx(reference_price=None, **changes), _limits())
    assert result.reasons == [reason]
    assert result.plan is None
    assert result.qty is result.reference_price is None


@pytest.mark.parametrize("price,stop,target,reason", [
    (0., 99., 110., "no_reference_price"),
    (float("nan"), 99., 110., "no_reference_price"),
    (100., 0., 110., "invalid_stop_geometry"),
    (100., 100., 90., "invalid_stop_geometry"),
    (1001., 99., 90., "notional_below_one_share"),
    (100., 99., 100., "invalid_target_geometry"),
    (100., 99., float("inf"), "invalid_target_geometry"),
])
def test_planning_refusal_precedes_risk_and_has_no_plan(price, stop, target, reason):
    result = evaluate_authorization(_ctx(reference_price=price,
        opportunity=_opportunity(structural_invalidation=stop, structural_target=target),
        portfolio=_portfolio(realized_pnl_today=-1000.)), _limits())
    assert result.reasons == [reason]
    assert result.plan is None


@pytest.mark.parametrize("cap,decision,reason", [
    (0.1, "approved", []),
    (0.09999999999999999, "rejected", ["projected_loss_exceeds_daily_cap"]),
])
def test_decimal_candidate_exact_cap_and_just_over(cap, decision, reason):
    # Legacy float risk 10 * (1.1 - 1.09) == 0.10000000000000009
    # falsely rejected equality. Canonical decimal arithmetic corrects it.
    ctx = _ctx(reference_price=1.1, fixed_notional_usd=11.,
               opportunity=_opportunity(structural_invalidation=1.09, structural_target=1.2))
    result = evaluate_authorization(ctx, _limits(fixed_notional_usd=11., daily_loss_cap_usd=cap))
    assert result.decision == decision
    assert result.reasons == reason
    assert result.plan is ctx.plan


@pytest.mark.parametrize("realized,cap,reason", [
    (-0.1, 0.6, []),
    (-0.1000000000000001, 0.6, ["projected_loss_exceeds_daily_cap"]),
    (-0.1, 0.5, ["daily_loss_cap_reached"]),
    (0.2, 0.5, []),  # realized profit does not offset exposure risk
])
def test_decimal_existing_exposure_combines_stop_unrealized_and_realized(realized, cap, reason):
    exposures = (
        OpenExposure("MSFT", "BUY", 10, 1.1, 1.09, 1.1, 0., True),  # exact .1
        OpenExposure("TSLA", "SELL", 1, 1., 1.1, 1.3, -.3, False),  # max(.1,.3)
    )
    ctx = _ctx(reference_price=1.1, fixed_notional_usd=11.,
               opportunity=_opportunity(structural_invalidation=1.09, structural_target=1.2),
               portfolio=_portfolio(realized_pnl_today=realized, exposures=exposures))
    result = evaluate_authorization(ctx, _limits(max_concurrent_positions=3, fixed_notional_usd=11., daily_loss_cap_usd=cap))
    assert result.reasons == reason
    assert result.plan is ctx.plan


def test_risk_gate_consumes_plan_risk_without_recomputing_quantity_or_risk():
    from dataclasses import replace
    from decimal import Decimal
    ctx = _ctx()
    # An intentionally distinct test value proves delegation. Production
    # plans are built by plan_entry; rules do not derive another risk value.
    ctx = replace(ctx, plan=replace(ctx.plan, planned_risk_usd=Decimal("100.00000000000001")))
    assert evaluate_authorization(ctx, _limits()).reasons == ["projected_loss_exceeds_daily_cap"]
