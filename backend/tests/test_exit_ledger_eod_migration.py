"""Migration 0016 (simulated EOD exit state) on a scratch PostgreSQL database.

A separate database is created and dropped per test module so the shared
`trading_workspace` schema and its data are never downgraded.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

BACKEND = Path(__file__).resolve().parents[1]


def _head_revision() -> str:
    """The current Alembic head, so later migrations do not break this 0016 module's 'at head' checks."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


HEAD = _head_revision()
UTC = timezone.utc
DB = f"eod_migration_{uuid.uuid4().hex[:8]}"
OPENED = datetime(2026, 9, 16, 14, 0, tzinfo=UTC)
FLATTEN = datetime(2026, 9, 16, 19, 59, tzinfo=UTC)
CLOSE = datetime(2026, 9, 16, 20, 0, tzinfo=UTC)


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


def seed_position(sessions, suffix):
    from app.models.execution_ledger import Position, Trade

    with sessions.begin() as session:
        trade = Trade(execution_mode="simulated", execution_venue="simulated", strategy_name=f"MIG_{suffix}",
                      strategy_version="v1", direction="BUY", symbol="ZZMIG", decision="approved", status="open")
        session.add(trade)
        session.flush()
        position = Position(trade_id=trade.trade_id, execution_mode="simulated", execution_venue="simulated",
                            symbol="ZZMIG", side="BUY", qty=5, avg_price=100, opened_at=OPENED, status="open")
        session.add(position)
        session.flush()
        return trade.trade_id, position.position_id


def test_upgrade_preserves_legacy_rows_and_downgrade_without_evidence_round_trips(scratch):
    up = alembic("upgrade", "0015")
    assert up.returncode == 0, up.stderr
    # a legacy stop request + close order exists BEFORE 0016; seed at 0015 using raw SQL (the ORM models describe the 0016 shape)
    trade_id, position_id = None, None
    with scratch.begin() as session:
        trade_id = session.execute(text(
            "INSERT INTO trades (trade_id, execution_mode, execution_venue, strategy_name, strategy_version, "
            "direction, symbol, decision, status) VALUES (gen_random_uuid(), 'simulated','simulated','MIG_A','v1',"
            "'BUY','ZZMIG','approved','open') RETURNING trade_id")).scalar()
        position_id = session.execute(text(
            "INSERT INTO positions (position_id, trade_id, execution_mode, execution_venue, symbol, side, qty, "
            "avg_price, opened_at, status, exit_attempt) VALUES (gen_random_uuid(), :t,'simulated','simulated',"
            "'ZZMIG','BUY',5,100,:o,'open',1) RETURNING position_id"), {"t": trade_id, "o": OPENED}).scalar()
        session.execute(text(
            "INSERT INTO exit_requests (position_id, exit_reason, trigger_price, trigger_ts) "
            "VALUES (:p,'stop',89,:o)"), {"p": position_id, "o": OPENED})
        session.execute(text(
            "INSERT INTO orders (client_order_id, trade_id, position_id, execution_mode, execution_venue, symbol, "
            "side, position_effect, qty, order_type, status, exit_reason) VALUES (:c,:t,:p,'simulated','simulated',"
            "'ZZMIG','SELL','close',5,'market','approved','stop')"),
            {"c": f"{trade_id}:exit:1", "t": trade_id, "p": position_id})

    head = alembic("upgrade", "head")
    assert head.returncode == 0, head.stderr
    assert current(scratch) == HEAD
    with scratch() as session:
        legacy = session.execute(text(
            "SELECT exit_reason, eod_flatten_at, eod_close_at, eod_expired_at, fallback_reason, "
            "fallback_trigger_price, fallback_trigger_ts FROM exit_requests")).one()
        assert legacy == ("stop", None, None, None, None, None, None)
        marker = session.execute(text("SELECT exit_dispatch_started_at FROM orders")).scalar()
        assert marker is None

    # a legacy-only database has no EOD evidence: downgrade succeeds and keeps the legacy rows
    down = alembic("downgrade", "0015")
    assert down.returncode == 0, down.stderr
    assert current(scratch) == "0015"
    with scratch() as session:
        assert session.execute(text("SELECT count(*) FROM exit_requests")).scalar() == 1
        assert session.execute(text("SELECT count(*) FROM orders")).scalar() == 1
        cols = {r[0] for r in session.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name IN ('exit_requests','orders')"))}
        assert not cols & {"eod_flatten_at", "fallback_reason", "exit_dispatch_started_at"}
        # the 0015 CHECK is back: EOD is not representable again
        with pytest.raises(Exception):
            session.execute(text("INSERT INTO exit_requests (position_id, exit_reason, trigger_price, trigger_ts) "
                                 "SELECT gen_random_uuid(), 'eod_flatten', 1, now()"))
    assert alembic("upgrade", "head").returncode == 0 and current(scratch) == HEAD


def _evidence_refuses_downgrade(scratch, mutate_sql, params=None):
    with scratch.begin() as session:
        session.execute(text(mutate_sql), params or {})
    refused = alembic("downgrade", "0015")
    assert refused.returncode != 0
    assert "Cannot downgrade 0016" in refused.stderr
    assert current(scratch) == HEAD  # nothing was dropped


def test_downgrade_refuses_to_discard_eod_fallback_or_dispatch_evidence(scratch):
    assert alembic("upgrade", "head").returncode == 0
    # 1) an EOD request
    _, pid = seed_position(scratch, "B")
    _evidence_refuses_downgrade(
        scratch,
        "INSERT INTO exit_requests (position_id, exit_reason, trigger_price, trigger_ts, eod_flatten_at, eod_close_at) "
        "VALUES (:p,'eod_flatten',101,:o,:f,:c)", {"p": pid, "o": OPENED, "f": FLATTEN, "c": CLOSE})
    # 2) fallback + expiry on it
    _evidence_refuses_downgrade(
        scratch,
        "UPDATE exit_requests SET fallback_reason='stop', fallback_trigger_price=89, fallback_trigger_ts=:o, "
        "eod_expired_at=:c WHERE position_id=:p", {"p": pid, "o": OPENED, "c": CLOSE})
    with scratch.begin() as session:
        session.execute(text("DELETE FROM exit_requests WHERE position_id=:p"), {"p": pid})
    assert alembic("downgrade", "0015").returncode == 0  # evidence removed: allowed again
    assert alembic("upgrade", "head").returncode == 0

    # 3) a dispatch marker on a close order, with the request itself a plain stop
    trade_id, pid2 = seed_position(scratch, "C")
    with scratch.begin() as session:
        session.execute(text("INSERT INTO exit_requests (position_id, exit_reason, trigger_price, trigger_ts) "
                             "VALUES (:p,'stop',89,:o)"), {"p": pid2, "o": OPENED})
        session.execute(text(
            "INSERT INTO orders (client_order_id, trade_id, position_id, execution_mode, execution_venue, symbol, "
            "side, position_effect, qty, order_type, status, exit_reason) VALUES (:c,:t,:p,'simulated','simulated',"
            "'ZZMIG','SELL','close',5,'market','approved','stop')"), {"c": f"{trade_id}:exit:1", "t": trade_id, "p": pid2})
    _evidence_refuses_downgrade(scratch, "UPDATE orders SET exit_dispatch_started_at=:o WHERE position_id=:p",
                                {"p": pid2, "o": OPENED + timedelta(hours=1)})


def test_head_constraints_and_triggers_exist_and_the_marker_is_close_only(scratch):
    assert alembic("upgrade", "head").returncode == 0
    with scratch() as session:
        names = {r[0] for r in session.execute(text(
            "SELECT conname FROM pg_constraint WHERE conname LIKE 'ck_exit_requests_%' OR conname LIKE 'ck_orders_%'"))}
        assert {"ck_exit_requests_eod_group", "ck_exit_requests_fallback_group", "ck_orders_dispatch_marker_close_only",
                "ck_orders_unsent_expiry_has_no_marker"} <= names
        triggers = {r[0] for r in session.execute(text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"))}
        assert {"trg_exit_requests_immutable", "trg_orders_dispatch_marker_immutable"} <= triggers
