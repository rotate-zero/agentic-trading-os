"""Unit tests for the simulated-MVP acceptance command's OWN logic (app/acceptance/simulated_mvp.py).

These do not run the six scenarios (that is the command itself:
`python scripts/simulated_mvp_acceptance.py --database <disposable_db>`, see TESTING.md). They prove that the
guard rails and the PASS/FAIL reporting behave: a populated, unmigrated, wrongly named or unselected database
is refused before anything starts; a failed milestone is named and yields a nonzero exit code; the database
password never reaches the output.
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from app.acceptance import simulated_mvp as mvp

PASSWORD = "s3cret-pw-do-not-print"
HEAD = "0017"
CLEAN = {t: 0 for t in mvp.REQUIRED_TABLES} | {"symbols": 0, "scanner_universe_symbols": 0}


@pytest.fixture
def selected_db(monkeypatch):
    """Point Settings at a disposable-looking database with a recognisable password."""
    from app.core.config import get_settings

    for key, value in {"POSTGRES_DB": "mvp_acceptance_unit", "POSTGRES_PASSWORD": PASSWORD,
                       "POSTGRES_USER": "unit_user", "FINNHUB_API_KEY": "", "POLYGON_API_KEY": "",
                       "EXECUTION_MODE": "simulated"}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def run_main(args, *, inspect=lambda url: (HEAD, dict(CLEAN)), runner=None):
    out = io.StringIO()
    code = mvp.main(args, out=out, inspect=inspect, head=lambda: HEAD, runner=runner or (lambda acceptance: None))
    return code, out.getvalue()


# --- database selection -------------------------------------------------------------------------
def test_selection_requires_an_explicit_matching_disposable_name():
    assert mvp.validate_target_selection("mvp_acceptance", "mvp_acceptance") == []
    assert mvp.validate_target_selection(None, "mvp_acceptance")
    assert any("does not match" in p for p in mvp.validate_target_selection("a_test", "b_test"))
    assert any("does not look disposable" in p for p in mvp.validate_target_selection("trading_workspace", "trading_workspace"))


# --- migrated and empty -------------------------------------------------------------------------
def test_migrated_empty_database_has_no_problems():
    assert mvp.evaluate_database_state(HEAD, HEAD, dict(CLEAN)) == []


def test_unmigrated_or_behind_head_is_a_migration_problem():
    assert [k for k, _ in mvp.evaluate_database_state(None, HEAD, dict(CLEAN))] == ["migration"]
    behind = mvp.evaluate_database_state("0016", HEAD, dict(CLEAN))
    assert [k for k, _ in behind] == ["migration"] and "0016" in behind[0][1]


def test_missing_execution_tables_are_a_migration_problem():
    counts = dict(CLEAN)
    del counts["strategy_outcomes"]
    problems = mvp.evaluate_database_state(HEAD, HEAD, counts)
    assert problems and problems[0][0] == "migration" and "strategy_outcomes" in problems[0][1]


def test_populated_tables_are_named_and_never_cleaned():
    counts = dict(CLEAN) | {"trades": 1, "fills": 1}
    (kind, text), = mvp.evaluate_database_state(HEAD, HEAD, counts)
    assert kind == "empty" and "fills, trades" in text and "never truncates" in text


def test_inspect_database_is_read_only_and_reports_the_real_schema():
    """Against the project's own migrated test database: reads only, whatever it currently holds."""
    from app.core.config import get_settings

    revision, counts = mvp.inspect_database(get_settings().database_url)
    assert revision == mvp.alembic_head_revision()
    assert set(mvp.REQUIRED_TABLES) <= set(counts)
    assert set(counts.values()) <= {0, 1}


# --- settings the scenarios rely on -------------------------------------------------------------
def test_scenario_settings_validation():
    ok = SimpleNamespace(execution_mode="simulated", execution_fixed_notional_usd=1000.0,
                         execution_daily_loss_cap_usd=100.0, execution_max_concurrent_positions=1)
    assert mvp.validate_settings_for_scenarios(ok) == []
    tight = SimpleNamespace(**(vars(ok) | {"execution_daily_loss_cap_usd": 10.0}))
    assert any("daily_loss_cap" in p for p in mvp.validate_settings_for_scenarios(tight))


# --- reporting ----------------------------------------------------------------------------------
def test_redact_and_report_never_print_a_secret():
    assert mvp.redact(f"dsn password={PASSWORD}!", [PASSWORD, ""]) == "dsn password=***!"
    out = io.StringIO()
    report = mvp.Report(out=out, secrets=(PASSWORD,))
    report.fail("S1.1", "title", f"connection failed for {PASSWORD}")
    assert PASSWORD not in out.getvalue() and "FAIL" in out.getvalue()


def test_check_failure_names_the_milestone():
    out = io.StringIO()
    checker = mvp.Checker(mvp.Report(out=out), wait_timeout=0.05)
    checker.begin("S2")
    checker.check("first holds", True)
    with pytest.raises(mvp.AcceptanceFailure) as failure:
        checker.check("second does not", False, "evidence")
    assert failure.value.milestone_id == "S2.2" and "FAIL  S2.2" in out.getvalue()


def test_wait_is_bounded_and_reports_what_was_last_observed():
    out = io.StringIO()
    checker = mvp.Checker(mvp.Report(out=out), wait_timeout=0.05)
    checker.begin("S3")
    with pytest.raises(mvp.AcceptanceFailure) as failure:
        checker.wait("never happens", lambda: False, lambda: "state=submitted")
    assert failure.value.milestone_id == "S3.1"
    assert "timed out" in failure.value.detail and "state=submitted" in failure.value.detail


def test_wait_returns_as_soon_as_the_milestone_is_observed():
    checker = mvp.Checker(mvp.Report(out=io.StringIO()), wait_timeout=5)
    checker.begin("S3")
    calls = iter([False, False, True])
    checker.wait("arrives", lambda: next(calls))


# --- exit codes ---------------------------------------------------------------------------------
def test_exit_2_when_no_database_is_selected(selected_db):
    code, text = run_main([])
    assert code == 2 and "P.1" in text and "nothing was started" in text


def test_exit_2_when_database_name_is_not_disposable(selected_db, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("POSTGRES_DB", "trading_workspace")
    get_settings.cache_clear()
    code, text = run_main(["--database", "trading_workspace"])
    assert code == 2 and "does not look disposable" in text


def test_exit_2_and_no_scenario_when_database_is_populated(selected_db):
    ran = []
    code, text = run_main(["--database", "mvp_acceptance_unit"],
                          inspect=lambda url: (HEAD, dict(CLEAN) | {"trades": 1}),
                          runner=lambda acceptance: ran.append(True))
    assert code == 2 and ran == [] and "P.4" in text and "trades" in text


def test_exit_2_when_database_is_not_migrated(selected_db):
    ran = []
    code, text = run_main(["--database", "mvp_acceptance_unit"],
                          inspect=lambda url: (None, {}), runner=lambda acceptance: ran.append(True))
    assert code == 2 and ran == [] and "P.3" in text


def test_exit_2_when_database_is_unreachable_and_password_is_not_printed(selected_db):
    def unreachable(url):
        raise RuntimeError(f"could not connect using {url}")

    code, text = run_main(["--database", "mvp_acceptance_unit"], inspect=unreachable)
    assert code == 2 and "P.3" in text and PASSWORD not in text


def test_exit_1_names_the_failed_milestone(selected_db):
    def failing(acceptance):
        acceptance.c.begin("S2", "Scenario 2")
        acceptance.c.check("close order submitted", False, f"saw nothing near {PASSWORD}")

    code, text = run_main(["--database", "mvp_acceptance_unit"], runner=failing)
    assert code == 1
    assert "RESULT: FAIL — failed milestone S2.1: close order submitted" in text
    assert PASSWORD not in text


def test_exit_1_on_an_unexpected_crash_and_names_the_milestone_in_progress(selected_db):
    def crashing(acceptance):
        acceptance.c.begin("S4", "Scenario 4")
        acceptance.report.current = "S4.7 restart"
        raise ValueError("boom")

    code, text = run_main(["--database", "mvp_acceptance_unit"], runner=crashing)
    assert code == 1 and "S4.7 restart" in text and "boom" in text


def test_exit_0_prints_pass_and_the_scope_limits(selected_db):
    code, text = run_main(["--database", "mvp_acceptance_unit"])
    assert code == 0 and "RESULT: PASS" in text
    assert "real StrategyScheduler/Gap opportunity (S6)" in text
    assert "do NOT prove candle acquisition, FeatureEngine calculations, live-feed coverage" in text
    assert PASSWORD not in text and "unit_user" in text
