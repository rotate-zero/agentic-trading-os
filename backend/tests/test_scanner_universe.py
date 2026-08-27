"""
Universe management DB tests — real Postgres, same convention
tests/test_feature_engine.py's own DB-integrated tests already use
(SessionLocal directly, no mocking, a distinctively-prefixed test
ticker + explicit cleanup so this never touches the real persisted
universe or collides with other tests' data).

Already run once by hand against a real, freshly-migrated Postgres 16
instance (all of migrations 0001-0004 applied) before this was written
up as a repeatable test — this file turns that manual pass into
something that runs on every future `pytest`.
"""
from __future__ import annotations

from sqlalchemy import text

from app.db.session import SessionLocal
from app.scanner.universe import (
    DbUniverseProvider,
    add_symbol_to_universe,
    is_valid_ticker_format,
    list_universe_symbols,
    remove_symbol_from_universe,
)

_TEST_TICKER = "ZZTST"  # valid ticker FORMAT (5 uppercase letters) but not a
# real assigned symbol — needs to pass is_valid_ticker_format itself,
# unlike test_feature_engine.py's __TESTFEATR1__ convention, since this
# module's add path enforces format validation that those other tables
# don't have.


def _clean_test_symbol() -> None:
    """FK-safe cleanup order, same shape test_feature_engine.py's own
    _clean_atr_symbol uses — scanner_universe_symbols references
    symbols.id with no ON DELETE CASCADE, so it goes first."""
    session = SessionLocal()
    try:
        symbol_id = session.execute(text("SELECT id FROM symbols WHERE ticker = :t"), {"t": _TEST_TICKER}).scalar()
        if symbol_id is not None:
            session.execute(text("DELETE FROM scanner_universe_symbols WHERE symbol_id = :sid"), {"sid": symbol_id})
        session.execute(text("DELETE FROM symbols WHERE ticker = :t"), {"t": _TEST_TICKER})
        session.commit()
    finally:
        session.close()


def test_is_valid_ticker_format_accepts_real_shapes_rejects_malformed():
    assert is_valid_ticker_format("NVDA") is True
    assert is_valid_ticker_format("BRK.B") is True
    assert is_valid_ticker_format("A") is True
    assert is_valid_ticker_format("nvda") is False  # lowercase
    assert is_valid_ticker_format("TOOLONG1") is False  # >5 letters
    assert is_valid_ticker_format("") is False
    assert is_valid_ticker_format("123") is False


def test_add_list_remove_round_trip_against_real_db():
    _clean_test_symbol()
    try:
        before = {s["symbol"] for s in list_universe_symbols(SessionLocal)}
        assert _TEST_TICKER not in before

        added = add_symbol_to_universe(SessionLocal, _TEST_TICKER.lower())  # lowercase in, uppercase out
        assert added == _TEST_TICKER

        after_add = {s["symbol"] for s in list_universe_symbols(SessionLocal)}
        assert _TEST_TICKER in after_add

        assert _TEST_TICKER in DbUniverseProvider(SessionLocal).get_core_universe()

        removed = remove_symbol_from_universe(SessionLocal, _TEST_TICKER)
        assert removed is True

        after_remove = {s["symbol"] for s in list_universe_symbols(SessionLocal)}
        assert _TEST_TICKER not in after_remove
    finally:
        _clean_test_symbol()


def test_add_is_idempotent_no_duplicate_row():
    _clean_test_symbol()
    try:
        add_symbol_to_universe(SessionLocal, _TEST_TICKER)
        add_symbol_to_universe(SessionLocal, _TEST_TICKER)  # second call, same symbol

        matches = [s for s in list_universe_symbols(SessionLocal) if s["symbol"] == _TEST_TICKER]
        assert len(matches) == 1
    finally:
        _clean_test_symbol()


def test_add_invalid_ticker_raises_and_touches_nothing():
    import pytest

    with pytest.raises(ValueError):
        add_symbol_to_universe(SessionLocal, "definitelynotavalidticker")

    assert "DEFINITELYNOTAVALIDTICKER" not in {s["symbol"] for s in list_universe_symbols(SessionLocal)}


def test_remove_nonexistent_symbol_returns_false_not_an_error():
    _clean_test_symbol()  # ensure it's genuinely absent first
    assert remove_symbol_from_universe(SessionLocal, _TEST_TICKER) is False
