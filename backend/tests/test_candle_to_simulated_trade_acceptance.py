"""Unit tests for the candle-to-simulated-trade acceptance command's OWN logic
(app/acceptance/candle_to_simulated_trade.py).

These do not run the scenarios (that is the command itself:
`python scripts/candle_to_simulated_trade_acceptance.py --database <disposable_db>`, see TESTING.md). They prove
that (1) the supplied candle fixtures are deterministic and, through the PRODUCTION indicator, scoring and Gap
MATCH functions, select exactly the intended setup (present for the gap symbol, absent for the contrast symbol);
(2) the guard rails refuse an unselected, unmigrated, wrongly named or populated database before anything starts;
(3) a failed milestone yields a nonzero exit code and the password never reaches the output; and (4) the module
contains no code path that publishes FeaturesUpdated, MarketStateChanged or OpportunityCreated.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from app.acceptance import candle_to_simulated_trade as cts
from app.acceptance import simulated_mvp as mvp

PASSWORD = "s3cret-pw-do-not-print"
HEAD = "0017"
CLEAN = {t: 0 for t in mvp.REQUIRED_TABLES} | {"symbols": 0, "scanner_universe_symbols": 0}


# --- fixture contracts, evaluated by the PRODUCTION functions -------------------------------------
def _calculated_inputs(symbol: str) -> dict:
    """What the real producers will compute from the supplied candles, via the same pure functions."""
    from app.backtest_runner.fixture_daily_history import build_daily_history_candles
    from app.feature_engine.indicators.rvol import rvol
    from app.feature_engine.indicators.sma import sma_slope
    from app.market_state_engine import scoring

    closes = [c["close"] for c in cts.prior_session_candles()] + [cts.session_candle(symbol)["close"]]
    angle = sma_slope(closes, 20)["sma_20_slope_angle"]
    history = build_daily_history_candles(cts.SESSION_OPEN.date())
    avg_volume = sum(c.volume for c in history[-cts.RVOL_LOOKBACK_DAYS:]) / cts.RVOL_LOOKBACK_DAYS
    relative = rvol(cts.DAY_VOLUME, avg_volume, 1, cts.SESSION_MINUTES)["rvol"]
    dollars, pct, regular_open = cts.expected_gap(symbol)
    return {"trend": scoring.trend_score(angle), "volume": scoring.volume_regime_score(relative),
            "gap_pct": pct, "regular_open": regular_open, "close": cts.DAY_CLOSE}


def test_supplied_candles_are_deterministic_and_well_formed():
    assert cts.prior_session_candles() == cts.prior_session_candles()
    warmup = cts.prior_session_candles()
    assert len(warmup) == cts.WARMUP_CANDLES >= 2 * 20 - 1
    assert warmup[-1]["close"] == cts.PRIOR_CLOSE
    stamps = [c["candle_ts"] for c in warmup]
    assert stamps == sorted(set(stamps)) and all(c["timeframe"] == "1m" for c in warmup)
    assert all(c["low"] <= min(c["open"], c["close"]) and c["high"] >= max(c["open"], c["close"]) for c in warmup)
    for symbol in (cts.SYMBOL_GAP, cts.SYMBOL_NO_GAP):
        candle = cts.session_candle(symbol)
        assert candle["low"] <= min(candle["open"], candle["close"]) and candle["high"] >= candle["close"]
        assert candle["candle_ts"] == cts.SESSION_OPEN


def test_the_two_session_candles_differ_only_in_the_opening_print():
    gap, no_gap = cts.session_candle(cts.SYMBOL_GAP), cts.session_candle(cts.SYMBOL_NO_GAP)
    assert gap["close"] == no_gap["close"] and gap["volume"] == no_gap["volume"] and gap["high"] == no_gap["high"]
    assert gap["open"] != no_gap["open"]


def test_trading_days_are_consecutive_regular_sessions_open_at_0930_et():
    from app.core.market_clock import get_market_clock

    clock = get_market_clock()
    assert clock.trading_day(cts.PRIOR_SESSION_OPEN) < clock.trading_day(cts.SESSION_OPEN)
    assert clock.is_regular_session(cts.PRIOR_SESSION_OPEN) and clock.is_regular_session(cts.SESSION_OPEN)
    assert clock.minutes_since_open(cts.SESSION_OPEN) == 0
    last_warmup = cts.prior_session_candles()[-1]["candle_ts"]
    assert clock.is_regular_session(last_warmup) and clock.trading_day(last_warmup) == clock.trading_day(cts.PRIOR_SESSION_OPEN)


def test_gap_symbol_clears_every_gap_v1_default_gate_and_proposes_the_expected_levels():
    from app.strategy_engine.gap_strategy import default_params, match_direction

    params = default_params()
    inputs = _calculated_inputs(cts.SYMBOL_GAP)
    assert (params["min_gap_pct"], params["trend_score_threshold"], params["volume_regime_threshold"]) == (
        cts.DEFAULT_MIN_GAP_PCT, cts.TREND_THRESHOLD, cts.VOLUME_THRESHOLD)   # the fixtures track production defaults
    assert inputs["gap_pct"] >= cts.DEFAULT_MIN_GAP_PCT and inputs["trend"] >= cts.TREND_THRESHOLD
    assert inputs["volume"] >= cts.VOLUME_THRESHOLD
    direction = match_direction(
        inputs["close"], inputs["regular_open"], inputs["gap_pct"], inputs["trend"], inputs["volume"], 0,
        min_gap_pct=params["min_gap_pct"], trend_score_threshold=params["trend_score_threshold"],
        volume_regime_threshold=params["volume_regime_threshold"],
        max_minutes_since_open=params["max_minutes_since_open"])
    assert direction == "BUY"
    risk = inputs["close"] - inputs["regular_open"]
    assert (inputs["regular_open"], inputs["close"] + params["target_r_multiple"] * risk) == (mvp.STOP_LEVEL, mvp.GAP_TARGET)


def test_contrast_symbol_fails_only_the_gap_condition():
    from app.strategy_engine.gap_strategy import default_params, match_direction

    params = default_params()
    inputs = _calculated_inputs(cts.SYMBOL_NO_GAP)
    assert inputs["trend"] >= cts.TREND_THRESHOLD and inputs["volume"] >= cts.VOLUME_THRESHOLD   # same regime
    assert 0 < inputs["gap_pct"] < cts.DEFAULT_MIN_GAP_PCT
    kwargs = dict(min_gap_pct=params["min_gap_pct"], trend_score_threshold=params["trend_score_threshold"],
                  volume_regime_threshold=params["volume_regime_threshold"],
                  max_minutes_since_open=params["max_minutes_since_open"])
    assert match_direction(inputs["close"], inputs["regular_open"], inputs["gap_pct"], inputs["trend"],
                           inputs["volume"], 0, **kwargs) is None
    # ... and it WOULD match if only the gap were large enough, so the contrast isolates the selected setup
    assert match_direction(inputs["close"], inputs["regular_open"], 3.0, inputs["trend"], inputs["volume"], 0,
                           **kwargs) == "BUY"


def test_settings_validation_matches_the_prices_this_scenario_uses():
    from types import SimpleNamespace

    ok = SimpleNamespace(execution_mode="simulated", execution_fixed_notional_usd=1000.0,
                         execution_daily_loss_cap_usd=100.0, execution_max_concurrent_positions=1)
    assert mvp.validate_settings_for_scenarios(ok) == []
    assert cts.DAY_CLOSE == mvp.ENTRY_PRICE and cts.GAP_OPEN == mvp.STOP_LEVEL   # the validation's price assumptions


def test_no_code_path_publishes_upstream_events():
    source = Path(cts.__file__).read_text()
    published = re.findall(r"make_envelope\(\s*[\w.]*?EventType\.([A-Z_]+)", source) \
        + re.findall(r"EventEnvelope\(\s*event_type=[\w.]*?EventType\.([A-Z_]+)", source)
    assert published == ["CANDLE_CLOSED"]
    for forbidden in ("FEATURES_UPDATED", "MARKET_STATE_CHANGED", "OPPORTUNITY_CREATED"):
        assert not re.search(rf"(make_envelope|EventEnvelope)\([^)]*{forbidden}", source, re.S)
    assert "FeatureSet(" not in source and "MarketState(" not in source and "Opportunity(" not in source


def test_provenance_and_scope_statements_state_the_limits():
    joined = " ".join(cts.PROVENANCE)
    assert "SUPPLIED" in joined and "CALCULATED" in joined and "pdc" in joined and "gap_pct" in joined
    assert "NOT tick acquisition, real-feed coverage, profitability or broker execution" in cts.SCOPE_STATEMENT
    assert "synthetic candle-to-trade path" in cts.SCOPE_STATEMENT


# --- guard rails (command-level) ------------------------------------------------------------------
@pytest.fixture
def selected_db(monkeypatch):
    from app.core.config import get_settings

    for key, value in {"POSTGRES_DB": "candle_acceptance_unit", "POSTGRES_PASSWORD": PASSWORD,
                       "POSTGRES_USER": "unit_user", "FINNHUB_API_KEY": "", "POLYGON_API_KEY": "",
                       "EXECUTION_MODE": "simulated"}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def run_main(args, *, inspect=lambda url: (HEAD, dict(CLEAN)), runner=None):
    out = io.StringIO()
    code = cts.main(args, out=out, inspect=inspect, head=lambda: HEAD, runner=runner or (lambda acceptance: None))
    return code, out.getvalue()


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
    code, text = run_main(["--database", "candle_acceptance_unit"],
                          inspect=lambda url: (HEAD, dict(CLEAN) | {"strategy_outcomes": 1}),
                          runner=lambda acceptance: ran.append(True))
    assert code == 2 and ran == [] and "P.4" in text and "strategy_outcomes" in text


def test_exit_2_when_database_is_not_migrated(selected_db):
    ran = []
    code, text = run_main(["--database", "candle_acceptance_unit"],
                          inspect=lambda url: (None, {}), runner=lambda acceptance: ran.append(True))
    assert code == 2 and ran == [] and "P.3" in text


def test_exit_2_when_database_is_unreachable_and_password_is_not_printed(selected_db):
    def unreachable(url):
        raise RuntimeError(f"could not connect using {url}")

    code, text = run_main(["--database", "candle_acceptance_unit"], inspect=unreachable)
    assert code == 2 and "P.3" in text and PASSWORD not in text


def test_exit_1_names_the_failed_milestone_and_hides_the_password(selected_db):
    def failing(acceptance):
        acceptance.c.begin("C1", "Scenario C1")
        acceptance.c.check("FeatureEngine publishes gap features", False, f"saw nothing near {PASSWORD}")

    code, text = run_main(["--database", "candle_acceptance_unit"], runner=failing)
    assert code == 1
    assert "RESULT: FAIL — failed milestone C1.1: FeatureEngine publishes gap features" in text
    assert PASSWORD not in text


def test_exit_1_on_an_unexpected_crash_and_names_the_milestone_in_progress(selected_db):
    def crashing(acceptance):
        acceptance.c.begin("C2", "Scenario C2")
        acceptance.report.current = "C2.3 contrast barrier"
        raise ValueError("boom")

    code, text = run_main(["--database", "candle_acceptance_unit"], runner=crashing)
    assert code == 1 and "C2.3 contrast barrier" in text and "boom" in text


def test_exit_0_prints_pass_provenance_and_the_scope_limits(selected_db):
    code, text = run_main(["--database", "candle_acceptance_unit"])
    assert code == 0 and "RESULT: PASS" in text
    assert "Inputs SUPPLIED" in text and "Outputs CALCULATED" in text
    assert "NOT tick acquisition, real-feed coverage, profitability or broker execution" in text
    assert PASSWORD not in text and "unit_user" in text
