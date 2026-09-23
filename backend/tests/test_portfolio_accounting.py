"""Pure arithmetic tests: no persistence fake is involved."""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from uuid import UUID, uuid4

import pytest

from app.portfolio_state.accounting import LedgerFill, apply_fill

TS = datetime(2026, 1, 5, 15, tzinfo=timezone.utc)
TRADE = UUID("00000000-0000-0000-0000-000000000001")


def fill(seq=1, **overrides):
    values = dict(ledger_seq=seq, venue_fill_id=f"f{seq}", client_order_id="entry", trade_id=TRADE,
                  execution_mode="simulated", execution_venue="simulated", symbol="AAPL",
                  side="BUY", position_effect="open", qty=10, price=D("100"), venue_ts=TS,
                  stop=D("90"))
    values.update(overrides)
    return LedgerFill(**values)


@pytest.mark.parametrize("side,exit_side,first,last", [
    ("BUY", "SELL", "110", "95"), ("SELL", "BUY", "90", "105"),
])
def test_long_and_short_partial_reductions_keep_profit_loss_and_fees_separate(side, exit_side, first, last):
    identity = uuid4()
    p = apply_fill(None, fill(side=side, commission=D("1")), new_position_id=identity).position
    partial = apply_fill(p, fill(2, side=exit_side, position_effect="close", qty=4, price=D(first), commission=D("0.4")))
    assert partial.realized_delta == D("40")
    assert partial.position.qty == 6
    assert partial.position.avg_price == D("100")
    closed = apply_fill(partial.position, fill(3, side=exit_side, position_effect="close", qty=6, price=D(last), commission=D("0.6"))).position
    assert closed.position_id == identity
    assert closed.qty == 0 and closed.status == "closed"
    assert closed.realized_profit == D("40") and closed.realized_loss == D("30")
    assert closed.realized_pnl == D("10") and closed.fees == D("2")
    assert closed.exit_price == (D(first) * 4 + D(last) * 6) / 10
    assert p.qty == 10 and p.realized_pnl == 0  # input remains unchanged


def test_weighted_add_after_reduction_and_later_reopen_use_correct_cost_and_identity():
    p = apply_fill(None, fill(qty=4), new_position_id=uuid4()).position
    added = apply_fill(p, fill(2, qty=6, price=D("110"))).position
    assert added.avg_price == D("106") and added.position_id == p.position_id
    reduced = apply_fill(added, fill(3, side="SELL", position_effect="close", qty=5, price=D("116"))).position
    added_again = apply_fill(reduced, fill(4, qty=5, price=D("96"))).position
    assert added_again.avg_price == D("101") and added_again.realized_profit == 50
    closed = apply_fill(added_again, fill(5, side="SELL", position_effect="close", price=D("101"))).position
    assert closed.fees is None and closed.unknown_fee_count == 5
    reopened = apply_fill(None, fill(6), new_position_id=uuid4()).position
    assert reopened.position_id != closed.position_id
    with pytest.raises(ValueError, match="closed positions"):
        apply_fill(closed, fill(6))


@pytest.mark.parametrize("changes", [
    {"qty": 0}, {"qty": -1}, {"qty": 1.5}, {"qty": True}, {"price": D("NaN")},
    {"price": D("Infinity")}, {"price": D("0")}, {"commission": D("NaN")},
    {"venue_ts": TS.replace(tzinfo=None)}, {"execution_mode": "unknown"},
    {"execution_mode": "paper"}, {"ledger_seq": 0}, {"venue_fill_id": ""},
    {"side": "LONG"}, {"position_effect": "reverse"},
])
def test_invalid_fill_rejected_before_arithmetic(changes):
    with pytest.raises(ValueError):
        apply_fill(None, fill(**changes), new_position_id=uuid4())


@pytest.mark.parametrize("changes", [
    {"side": "SELL"},
    {"position_effect": "close", "side": "BUY"},
    {"position_effect": "close", "side": "SELL", "qty": 11},
    {"trade_id": uuid4()}, {"symbol": "MSFT"},
    {"execution_mode": "backtest"},
])
def test_invalid_add_or_close_cannot_reverse_or_mix_positions(changes):
    p = apply_fill(None, fill(), new_position_id=uuid4()).position
    with pytest.raises(ValueError):
        apply_fill(p, fill(2, **changes))
    assert p.qty == 10 and p.realized_pnl == 0


def test_close_without_position_rejected_and_stop_move_does_not_change_cost():
    with pytest.raises(ValueError, match="no open position"):
        apply_fill(None, fill(position_effect="close", side="SELL"))
    p = apply_fill(None, fill(), new_position_id=uuid4()).position
    moved = replace(p, stop=D("102"))
    result = apply_fill(moved, fill(2, side="SELL", position_effect="close", price=D("105")))
    assert result.realized_delta == 50
    assert result.position.avg_price == 100 and result.position.stop == 102
