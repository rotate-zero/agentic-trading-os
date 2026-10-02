"""Migration 0017: one non-backtest strategy outcome per opportunity (real PostgreSQL).

A scratch database is created and dropped for this module so the shared
`trading_workspace` schema and data are never downgraded. Every test starts from
a known state: head, downgraded to 0016, with `strategy_outcomes`/`backtests`
emptied.

Covered: the unique simulated case, duplicate rejection, the backtest exemption,
the model/migration index agreement, an upgrade that preserves existing rows, an
upgrade that refuses (changing nothing) when duplicates already exist, and a
downgrade that removes only this index.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

BACKEND = Path(__file__).resolve().parents[1]
DB = f"outcome_unique_{uuid.uuid4().hex[:8]}"
INDEX = "uq_strategy_outcomes_non_backtest_opportunity"


def _admin():
    s = get_settings()
    conn = psycopg2.connect(host=s.postgres_host, port=s.postgres_port, user=s.postgres_user,
                            password=s.postgres_password, dbname="postgres")
    conn.autocommit = True
    return conn


def alembic(*args):
    env = {**os.environ, "POSTGRES_DB": DB}
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env=env,
                          capture_output=True, text=True)


@pytest.fixture(scope="module")
def scratch():
    conn = _admin()
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{DB}"')
    s = get_settings()
    url = f"postgresql+psycopg2://{s.postgres_user}:{s.postgres_password}@{s.postgres_host}:{s.postgres_port}/{DB}"
    engine = create_engine(url, future=True)
    try:
        yield sessionmaker(bind=engine, future=True)
    finally:
        engine.dispose()
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
        conn.close()


def current(sessions):
    with sessions() as session:
        return session.execute(text("SELECT version_num FROM alembic_version")).scalar()


@pytest.fixture()
def db(scratch):
    """Head, then 0016, with both tables empty: every test chooses its own starting point."""
    assert alembic("upgrade", "head").returncode == 0
    assert alembic("downgrade", "0016").returncode == 0
    assert current(scratch) == "0016"
    with scratch.begin() as session:
        session.execute(text("DELETE FROM strategy_outcomes"))
        session.execute(text("DELETE FROM backtests"))
    return scratch


def upgrade_to_head(sessions):
    result = alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert current(sessions) == "0017"


def new_run(session) -> uuid.UUID:
    run_id = uuid.uuid4()
    session.execute(text(
        "INSERT INTO backtests (run_id, sweep_id, strategy_name, strategy_version, config_hash, "
        "symbol_universe, date_range_start, date_range_end, data_version, feature_version, is_holdout) "
        "VALUES (:r, :s, 'MIG', 'v1', 'h', ARRAY['ZZMIG'], '2026-09-01', '2026-09-02', 'd1', 'f1', false)"),
        {"r": run_id, "s": uuid.uuid4()})
    return run_id


def add_outcome(session, opportunity_id, *, mode="simulated", run_id=None, venue=None):
    """Insert one valid strategy_outcomes row. `mode` is simulated | paper | backtest."""
    is_backtest = mode == "backtest"
    venue = venue or ("simulated" if mode in ("simulated", "backtest") else "ibkr")
    session.execute(text(
        "INSERT INTO strategy_outcomes (opportunity_id, schema_version, strategy_name, strategy_version, "
        "symbol, origin, is_backtest, backtest_run_id, execution_mode, execution_venue, trading_day, "
        "setup_detected_at, entry_filled_at, exit_filled_at, holding_seconds, direction, entry_price, "
        "entry_qty, exit_price, exit_qty, realized_pnl, realized_r, exit_reason, structural_invalidation, "
        "structural_target, final_stop, final_target, confidence_at_signal, evidence, market_state_at_entry, "
        "context_at_entry, market_state_at_exit, context_at_exit) "
        "VALUES (:o, 1, 'MIG', 'v1', 'ZZMIG', 'auto', :b, :run, :m, :v, '2026-09-16', "
        "'2026-09-16 14:00+00', '2026-09-16 14:05+00', '2026-09-16 15:05+00', 3600, 'BUY', 100, 5, 102, 5, "
        "10, 1, 'target', 98, 104, 98, 104, 0.6, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb)"),
        {"o": opportunity_id, "b": is_backtest, "run": run_id, "m": mode, "v": venue})


def rows(sessions):
    with sessions() as session:
        return [r[0] for r in session.execute(text(
            "SELECT row_to_json(t)::text FROM strategy_outcomes t ORDER BY outcome_id"))]


def index_names(sessions, table=None):
    sql = "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
    if table:
        sql += " AND tablename = :t"
    with sessions() as session:
        return {r[0] for r in session.execute(text(sql), {"t": table})}


def test_model_and_database_declare_the_same_partial_unique_index(db):
    from app.models.trading_intelligence import StrategyOutcomeRecord

    declared = {i.name: i for i in StrategyOutcomeRecord.__table__.indexes}[INDEX]
    assert declared.unique is True
    assert [c.name for c in declared.columns] == ["opportunity_id"]
    assert str(declared.dialect_options["postgresql"]["where"]) == "is_backtest IS FALSE"

    assert INDEX not in index_names(db)  # 0016: not yet
    upgrade_to_head(db)
    with db() as session:
        definition = session.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": INDEX}).scalar()
    assert definition.startswith("CREATE UNIQUE INDEX")
    assert "ON public.strategy_outcomes" in definition
    assert "(opportunity_id)" in definition
    assert "WHERE (is_backtest IS FALSE)" in definition


def test_one_non_backtest_outcome_per_opportunity_and_duplicates_are_rejected(db):
    upgrade_to_head(db)
    opp, other = uuid.uuid4(), uuid.uuid4()
    with db.begin() as session:
        add_outcome(session, opp)
        add_outcome(session, other)  # a different opportunity is fine
    before = rows(db)
    assert len(before) == 2

    for mode in ("simulated", "paper"):  # the guard is is_backtest IS FALSE, not mode = 'simulated'
        with pytest.raises(IntegrityError) as excinfo:
            with db.begin() as session:
                add_outcome(session, opp, mode=mode)
        assert INDEX in str(excinfo.value)
    assert rows(db) == before  # nothing was added, nothing changed


def test_backtest_outcomes_still_share_an_opportunity_id_across_runs(db):
    upgrade_to_head(db)
    opp = uuid.uuid4()
    with db.begin() as session:
        run_a, run_b = new_run(session), new_run(session)
        add_outcome(session, opp, mode="backtest", run_id=run_a)
        add_outcome(session, opp, mode="backtest", run_id=run_b)
        add_outcome(session, opp, mode="simulated")  # a non-backtest row may share an ID with backtest rows
    assert len(rows(db)) == 3
    with pytest.raises(IntegrityError):  # ...but only one non-backtest row per ID
        with db.begin() as session:
            add_outcome(session, opp, mode="simulated")
    assert len(rows(db)) == 3


def test_upgrade_preserves_every_existing_row(db):
    opp = uuid.uuid4()
    with db.begin() as session:
        add_outcome(session, uuid.uuid4())
        add_outcome(session, uuid.uuid4())
        run_a, run_b = new_run(session), new_run(session)
        add_outcome(session, opp, mode="backtest", run_id=run_a)
        add_outcome(session, opp, mode="backtest", run_id=run_b)  # backtest duplicates are legal and must survive
        add_outcome(session, opp)  # simulated row sharing the backtest rows' ID
    before = rows(db)
    assert len(before) == 5

    upgrade_to_head(db)
    assert rows(db) == before
    assert INDEX in index_names(db, "strategy_outcomes")


def test_upgrade_refuses_existing_duplicates_without_changing_anything(db):
    dup, dup2, clean = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with db.begin() as session:
        add_outcome(session, dup)
        add_outcome(session, dup)
        add_outcome(session, dup2)
        add_outcome(session, dup2)
        add_outcome(session, dup2, mode="paper")
        add_outcome(session, clean)
        run_id = new_run(session)
        add_outcome(session, dup, mode="backtest", run_id=run_id)  # not a duplicate for this guard
    before = rows(db)
    assert len(before) == 7

    refused = alembic("upgrade", "head")
    assert refused.returncode != 0
    assert "Cannot upgrade 0017" in refused.stderr
    assert "2 non-backtest opportunity_id value(s)" in refused.stderr
    assert str(dup) in refused.stderr and str(dup2) in refused.stderr
    assert str(clean) not in refused.stderr
    assert "No row was changed or deleted" in refused.stderr

    assert current(db) == "0016"  # the revision did not advance
    assert INDEX not in index_names(db)  # and no index was created
    assert rows(db) == before  # no row deleted, merged or chosen

    # An operator resolves the duplicates deliberately; the same upgrade then succeeds.
    with db.begin() as session:
        session.execute(text(
            "DELETE FROM strategy_outcomes WHERE is_backtest IS FALSE AND opportunity_id IN (:a, :b)"),
            {"a": dup, "b": dup2})
    upgrade_to_head(db)
    assert INDEX in index_names(db, "strategy_outcomes")


def test_downgrade_removes_only_this_index_and_keeps_rows(db):
    upgrade_to_head(db)
    opp = uuid.uuid4()
    with db.begin() as session:
        add_outcome(session, opp)
        add_outcome(session, uuid.uuid4())
        add_outcome(session, opp, mode="backtest", run_id=new_run(session))
    before_rows, before_indexes = rows(db), index_names(db)
    with db() as session:
        before_constraints = {r[0] for r in session.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'strategy_outcomes'::regclass"))}
    assert INDEX in before_indexes

    assert alembic("downgrade", "0016").returncode == 0
    assert current(db) == "0016"
    assert index_names(db) == before_indexes - {INDEX}  # nothing else, on any table, was touched
    with db() as session:
        after_constraints = {r[0] for r in session.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'strategy_outcomes'::regclass"))}
    assert after_constraints == before_constraints
    assert rows(db) == before_rows

    with db.begin() as session:  # the guard is genuinely gone
        add_outcome(session, opp)
    assert len(rows(db)) == len(before_rows) + 1
