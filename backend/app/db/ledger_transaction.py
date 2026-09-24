"""Owned transactions for decision/order persistence, in portfolio lock order."""
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


@contextmanager
def ledger_transaction(session_factory, error_type):
    try:
        with session_factory() as session:
            if session.in_transaction():
                raise error_type("ledger adapter requires a fresh owned transaction")
            with session.begin():
                session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                session.execute(text("SET LOCAL synchronous_commit = on"))
                session.execute(text("LOCK TABLE trades, orders, trade_reservations IN SHARE ROW EXCLUSIVE MODE"))
                yield session
    except error_type:
        raise
    except (SQLAlchemyError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
        raise error_type(f"ledger transaction failed: {exc}") from exc
