"""Migration 0018 (selection journal + candidate acceptances) on a scratch PostgreSQL database.

A separate database is created and dropped per module so the shared schema is never downgraded.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

BACKEND = Path(__file__).resolve().parents[1]
DB = f"decision_audit_mig_{uuid.uuid4().hex[:8]}"
NOW = datetime(2026, 1, 5, 15, tzinfo=timezone.utc)
CHECKS = {"ck_selection_attempts_mode", "ck_selection_attempts_result", "ck_selection_attempts_selected_candidate",
          "ck_selection_attempts_candidate_id", "ck_selection_attempts_policy_version",
          "ck_selection_attempts_schema_version", "ck_selection_attempts_evidence_object",
          "ck_candidate_acceptances_mode", "ck_candidate_acceptances_non_shadow",
          "ck_candidate_acceptances_candidate_id"}


def _admin():
    s = get_settings()
    conn = psycopg2.connect(host=s.postgres_host, port=s.postgres_port, user=s.postgres_user,
                            password=s.postgres_password, dbname="postgres")
    conn.autocommit = True
    return conn


def alembic(*args):
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND, env={**os.environ, "POSTGRES_DB": DB},
                          capture_output=True, text=True)


@pytest.fixture(scope="module")
def scratch():
    conn = _admin()
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{DB}"')
    s = get_settings()
    engine = create_engine(
        f"postgresql+psycopg2://{s.postgres_user}:{s.postgres_password}@{s.postgres_host}:{s.postgres_port}/{DB}",
        future=True)
    try:
        yield sessionmaker(bind=engine, future=True)
    finally:
        engine.dispose()
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
        conn.close()


def version(sessions):
    with sessions() as s:
        return s.execute(text("SELECT version_num FROM alembic_version")).scalar()


def tables(sessions):
    with sessions() as s:
        return {r[0] for r in s.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}


def insert_attempt(s, sid, *, shadow=False, result="selected", cand="c1", mode="simulated"):
    s.execute(text(
        "INSERT INTO selection_attempts (selection_id, created_at, execution_mode, shadow, policy_version, result, "
        "selected_candidate_id, schema_version, evidence) VALUES (:i, :t, :m, :sh, 'p', :r, :c, 1, '{}'::jsonb)"),
        {"i": sid, "t": NOW, "m": mode, "sh": shadow, "r": result, "c": cand})


def insert_trade(s):
    return s.execute(text(
        "INSERT INTO trades (trade_id, execution_mode, execution_venue, strategy_name, strategy_version, direction, "
        "symbol, decision, status) VALUES (gen_random_uuid(), 'simulated', 'simulated', 'MIG', 'v1', 'BUY', 'ZZ', "
        "'approved', 'open') RETURNING trade_id")).scalar()


def test_0018_upgrade_from_prior_head_preserves_legacy_trades(scratch):
    up = alembic("upgrade", "0017")
    assert up.returncode == 0, up.stderr
    assert version(scratch) == "0017" and not {"selection_attempts", "candidate_acceptances"} & tables(scratch)
    with scratch.begin() as s:
        legacy = insert_trade(s)
    head = alembic("upgrade", "0018")
    assert head.returncode == 0, head.stderr
    assert version(scratch) == "0018"
    assert {"selection_attempts", "candidate_acceptances"} <= tables(scratch)
    with scratch() as s:  # legacy trade untouched, no claim invented
        assert s.execute(text("SELECT count(*) FROM trades WHERE trade_id = :t"), {"t": legacy}).scalar() == 1
        assert s.execute(text("SELECT count(*) FROM candidate_acceptances")).scalar() == 0
        names = {r[0] for r in s.execute(text(
            "SELECT conname FROM pg_constraint WHERE conrelid IN ('selection_attempts'::regclass, "
            "'candidate_acceptances'::regclass)"))}
    assert CHECKS <= names
    assert {"pk_candidate_acceptances", "uq_candidate_acceptances_trade_id", "uq_selection_attempts_acceptance_target",
            "fk_candidate_acceptances_selected_attempt"} <= names
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert script.get_revision("0018").down_revision == "0017"
    assert len(script.get_heads()) == 1  # one linear history


def test_constraints_and_immutability_at_the_database(scratch):
    ok, shadow, abstained = (uuid.uuid4() for _ in range(3))
    with scratch.begin() as s:
        insert_attempt(s, ok)
        insert_attempt(s, shadow, shadow=True)
        insert_attempt(s, abstained, result="abstained", cand=None)
        trade = insert_trade(s)
    claim = ("INSERT INTO candidate_acceptances (execution_mode, candidate_id, trade_id, selection_id) "
             "VALUES (:m, :c, :t, :s)")
    for sel, cand, mode in [(shadow, "c1", "simulated"), (abstained, "c1", "simulated"), (ok, "other", "simulated"),
                            (ok, "c1", "paper"), (uuid.uuid4(), "c1", "simulated")]:
        with pytest.raises(DBAPIError):
            with scratch.begin() as s:
                s.execute(text(claim), {"m": mode, "c": cand, "t": trade, "s": sel})
    with scratch.begin() as s:
        s.execute(text(claim), {"m": "simulated", "c": "c1", "t": trade, "s": ok})
    with pytest.raises(DBAPIError):  # same mode/candidate again, other trade
        with scratch.begin() as s:
            s.execute(text(claim), {"m": "simulated", "c": "c1", "t": insert_trade(s), "s": ok})
    with pytest.raises(DBAPIError):  # same trade, second candidate
        with scratch.begin() as s:
            insert_attempt(s, uuid.uuid4(), cand="c2")
            s.execute(text(claim), {"m": "simulated", "c": "c2", "t": trade, "s": ok})
    for sql in ("UPDATE selection_attempts SET policy_version = 'x'", "UPDATE candidate_acceptances SET created_at = now()"):
        with pytest.raises(DBAPIError, match="append-only"):
            with scratch.begin() as s:
                s.execute(text(sql))
    with pytest.raises(DBAPIError):  # a claim row cannot be deleted out from under... but attempts are FK-protected
        with scratch.begin() as s:
            s.execute(text("DELETE FROM selection_attempts WHERE selection_id = :i"), {"i": ok})
    with pytest.raises(DBAPIError):
        with scratch.begin() as s:
            s.execute(text("DELETE FROM trades WHERE trade_id = :t"), {"t": trade})


def test_downgrade_refuses_with_evidence_then_succeeds_when_empty(scratch):
    refused = alembic("downgrade", "0017")
    assert refused.returncode != 0 and "Cannot downgrade 0018" in (refused.stderr + refused.stdout)
    assert version(scratch) == "0018" and {"selection_attempts", "candidate_acceptances"} <= tables(scratch)
    with scratch.begin() as s:  # operator removes the audit evidence deliberately (deletes are not blocked)
        s.execute(text("DELETE FROM candidate_acceptances"))
        s.execute(text("DELETE FROM selection_attempts"))
    down = alembic("downgrade", "0017")
    assert down.returncode == 0, down.stderr
    assert version(scratch) == "0017" and not {"selection_attempts", "candidate_acceptances"} & tables(scratch)
    with scratch() as s:
        assert s.execute(text("SELECT count(*) FROM pg_proc WHERE proname = 'decision_audit_refuse_update'")).scalar() == 0
        assert s.execute(text("SELECT count(*) FROM trades")).scalar() >= 1  # legacy trades preserved
    again = alembic("upgrade", "0018")
    assert again.returncode == 0 and version(scratch) == "0018"


def test_empty_database_upgrades_through_0018():
    conn = _admin()
    name = f"{DB}_empty"
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        env = {**os.environ, "POSTGRES_DB": name}
        run = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "0018"], cwd=BACKEND, env=env,
                             capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        s = get_settings()
        engine = create_engine(
            f"postgresql+psycopg2://{s.postgres_user}:{s.postgres_password}@{s.postgres_host}:{s.postgres_port}/{name}")
        with engine.connect() as c:
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0018"
            assert c.execute(text("SELECT count(*) FROM selection_attempts")).scalar() == 0
        engine.dispose()
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.close()
