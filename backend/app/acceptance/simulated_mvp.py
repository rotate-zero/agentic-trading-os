"""Simulated-MVP acceptance: one command, six scenarios, PASS/FAIL.

WHAT THIS PROVES
    That the already-built downstream simulated execution lifecycle works end to end against a real
    PostgreSQL database, driven through the REAL FastAPI lifespan (`app.main`), from seeded
    `OpportunityCreated` events (S1-S5) and one real StrategyScheduler/Gap signal (S6):

        OpportunityCreated -> AuthorizerStub -> ExecutionEngine -> SimulatedVenue -> Portfolio State
        -> Position Monitor (stop / target / simulated EOD) -> ExecutionEngine exit -> close fill
        -> OutcomeRecorder -> strategy_outcomes

WHAT THIS DOES NOT PROVE
    Candle acquisition, FeatureEngine calculations, strategy profitability, opportunity ranking,
    live-feed coverage or real broker execution. The S1-S5 opportunities and all feature, market-state,
    price and clock values are CONTROLLED INPUTS; nothing here proves that live data would produce the
    S6 setup or these prices.

HOW IT STAYS HONEST
    * No ledger or outcome row is ever inserted by this module. Every row it reads was written by the
      application's own workers (the authorizer, execution engine, Portfolio State, OutcomeRecorder).
    * The only things substituted are controlled inputs (including S6 FeaturesUpdated/MarketStateChanged),
      wall clock, and the venue/monitor/exit-ledger *clock*. The components are production classes.
    * The database must be an explicitly selected, disposable, migrated and EMPTY PostgreSQL database.
      The module never truncates, deletes or cleans anything; a populated database is refused.

EXIT CODES (see `main`)
    0  every milestone passed
    1  a scenario milestone failed (the failed milestone is named in the output)
    2  a precondition failed or the command was misused; no application worker was started
    3  the overall watchdog expired (a hang is reported as a failure, never left running)

The database password is never printed: the target is shown as host:port/database/user only, and every
line of output is scrubbed of the configured password as a second line of defence.
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import math
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Sequence

SCOPE_STATEMENT = (
    "Scope: seeded opportunities (S1-S5) and a real StrategyScheduler/Gap opportunity (S6) reach the "
    "SIMULATED authorizer -> execution -> SimulatedVenue -> Portfolio State -> Position Monitor -> "
    "OutcomeRecorder against PostgreSQL. Controlled feature, market-state, context, price and clock inputs "
    "do NOT prove candle acquisition, FeatureEngine calculations, live-feed coverage, strategy profitability, "
    "ranking or real broker execution."
)

# A database name must contain one of these to be accepted as disposable. This is a guard against
# pointing the command at a working database by accident, not a security boundary.
DISPOSABLE_NAME_MARKERS = ("acceptance", "disposable", "scratch", "test")

# Tables whose presence proves the execution/outcome migrations were applied.
REQUIRED_TABLES = (
    "trades", "trade_reservations", "orders", "fills", "positions", "exit_requests",
    "position_fill_receipts", "portfolio_state_cursor", "strategy_outcomes",
)

# Migration 0004 itself seeds the default six-symbol scanner universe into `symbols` and
# `scanner_universe_symbols`. A freshly migrated database therefore is NOT row-free in those two tables;
# they count as empty only while they hold nothing beyond exactly that seed. Every other table must be
# row-free.
MIGRATION_SEEDED_TICKERS = ("AAPL", "MSFT", "NVDA", "AMD", "TSLA", "SPY")

STRATEGY_NAME = "SIMULATED_MVP_ACCEPTANCE"
STRATEGY_VERSION = "acceptance_v1"

# --- controlled inputs -------------------------------------------------------------------------
# 2026-09-16 and 2026-09-17 are consecutive NYSE regular-session days (Wed/Thu, EDT: close = 20:00Z).
SESSION_DAY_1 = datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)         # 10:00 ET
SESSION_CLOSE_DAY_1 = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)   # 16:00 ET
SESSION_DAY_2 = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)
SESSION_DAY_3 = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)

ENTRY_PRICE = 100.0
STOP_LEVEL = 95.0       # structural_invalidation of every seeded opportunity
TARGET_LEVEL = 120.0    # structural_target of every seeded opportunity
TARGET_TICK = 121.0     # observed price that breaches the target
STOP_TICK = 94.0        # observed price that breaches the stop
EOD_TICK = 101.0

SYMBOL_TARGET = "ZZACCT"    # scenario 1 entry, closed by target in scenario 2
SYMBOL_STOP = "ZZACCS"      # scenario 2 entry, closed by stop
SYMBOL_EOD = "ZZACCE"       # scenario 3
SYMBOL_RESTART = "ZZACCR"   # scenario 4
SYMBOL_REJECTED = "ZZACCX"  # scenario 5 negative control (no reference price -> rejected)
SYMBOL_GAP_ABSENT = "ZZACGN"  # scenario 6 negative control: no gap feature keys
SYMBOL_GAP = "ZZACGP"         # scenario 6 real Gap opportunity
GAP_STRATEGY = "Gap"
GAP_VERSION = "gap_v1"
GAP_TARGET = 110.0             # close 100, regular_open/stop 95, default target_r_multiple=2


# =================================================================================================
# Reporting
# =================================================================================================
class AcceptanceFailure(Exception):
    """A named milestone did not hold. Carries enough to print `FAIL <id> <title>: <detail>`."""

    def __init__(self, milestone_id: str, title: str, detail: str = "") -> None:
        super().__init__(f"{milestone_id} {title}: {detail}")
        self.milestone_id = milestone_id
        self.title = title
        self.detail = detail


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every non-empty secret in `text` with `***`."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


@dataclass
class Report:
    """Line-oriented PASS/FAIL printer. Never prints a configured secret."""

    out: Any = None
    secrets: tuple[str, ...] = ()
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    current: str = "-"  # the milestone being worked on, for the watchdog message

    def __post_init__(self) -> None:
        if self.out is None:
            self.out = sys.stdout

    def line(self, text: str = "") -> None:
        print(redact(text, self.secrets), file=self.out, flush=True)

    def section(self, title: str) -> None:
        self.line()
        self.line(title)

    def ok(self, milestone_id: str, title: str, detail: str = "") -> None:
        self.passed.append(milestone_id)
        self.line(f"  PASS  {milestone_id:<6} {title}" + (f"  [{detail}]" if detail else ""))

    def fail(self, milestone_id: str, title: str, detail: str = "") -> None:
        self.failed.append(milestone_id)
        self.line(f"  FAIL  {milestone_id:<6} {title}" + (f"\n          {detail}" if detail else ""))


class Checker:
    """Numbers milestones per scenario and raises `AcceptanceFailure` on the first one that does not hold."""

    def __init__(self, report: Report, *, wait_timeout: float) -> None:
        self.report = report
        self.wait_timeout = wait_timeout
        self.scenario = "P"
        self._n = 0

    def begin(self, scenario: str, title: str | None = None) -> None:
        self.scenario, self._n = scenario, 0
        if title:
            self.report.section(title)

    def _next(self) -> str:
        self._n += 1
        return f"{self.scenario}.{self._n}"

    def check(self, title: str, condition: bool, detail: Callable[[], str] | str = "", *, note: str = "") -> None:
        mid = self._next()
        self.report.current = f"{mid} {title}"
        if not condition:
            text = detail() if callable(detail) else detail
            self.report.fail(mid, title, text)
            raise AcceptanceFailure(mid, title, text)
        self.report.ok(mid, title, note)

    def wait(self, title: str, predicate: Callable[[], bool], describe: Callable[[], Any] | None = None,
             *, timeout: float | None = None, interval: float = 0.02, note: str = "") -> None:
        """Poll for an OBSERVED milestone; fail with what was last seen if it never arrives in time.

        Never a fixed sleep standing in for a milestone: the loop ends the moment the predicate holds.
        """
        mid = self._next()
        self.report.current = f"{mid} {title}"
        limit = self.wait_timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        last_error: str | None = None
        while True:
            try:
                if predicate():
                    self.report.ok(mid, title, note)
                    return
                last_error = None
            except AcceptanceFailure:
                raise
            except Exception as exc:  # a predicate that cannot yet evaluate is "not yet", reported on timeout
                last_error = f"{type(exc).__name__}: {exc}"
            if time.monotonic() >= deadline:
                seen = describe() if describe is not None else "n/a"
                detail = f"timed out after {limit:g}s; last observed: {seen}" + (
                    f"; last predicate error: {last_error}" if last_error else "")
                self.report.fail(mid, title, detail)
                raise AcceptanceFailure(mid, title, detail)
            time.sleep(interval)


# =================================================================================================
# Preconditions (pure logic first, so it is unit-testable without a database)
# =================================================================================================
def validate_target_selection(requested: str | None, configured: str) -> list[str]:
    """Problems with how the database was selected. Empty list == acceptable."""
    problems: list[str] = []
    if not requested:
        problems.append("no database selected: pass --database NAME (and point POSTGRES_* at it)")
        return problems
    if requested != configured:
        problems.append(
            f"--database {requested!r} does not match the configured POSTGRES_DB {configured!r}; "
            "select the disposable database explicitly in both places")
    lowered = requested.lower()
    if not any(marker in lowered for marker in DISPOSABLE_NAME_MARKERS):
        problems.append(
            f"database name {requested!r} does not look disposable (it must contain one of "
            f"{', '.join(DISPOSABLE_NAME_MARKERS)}); refusing to run against it")
    return problems


def validate_settings_for_scenarios(settings: Any) -> list[str]:
    """The scenarios assume the authorizer limits can admit one seeded position at a time."""
    problems: list[str] = []
    qty = math.floor(settings.execution_fixed_notional_usd / ENTRY_PRICE)
    if settings.execution_mode != "simulated":
        problems.append(f"execution_mode must be 'simulated', got {settings.execution_mode!r}")
    if qty < 1:
        problems.append("execution_fixed_notional_usd is too small to buy one share at the seeded price")
    elif qty * (ENTRY_PRICE - STOP_LEVEL) > settings.execution_daily_loss_cap_usd:
        problems.append(
            "execution_daily_loss_cap_usd is below one seeded position's stop-out loss "
            f"({qty * (ENTRY_PRICE - STOP_LEVEL):.2f}); the authorizer would reject the seeded entries")
    if settings.execution_max_concurrent_positions < 1:
        problems.append("execution_max_concurrent_positions must be >= 1")
    return problems


def evaluate_database_state(
    current_revision: str | None, head_revision: str, table_counts: dict[str, int]
) -> list[tuple[str, str]]:
    """Problems with a database, as `(kind, message)` where kind is "migration" or "empty".

    `table_counts` maps each public base table to 1 if it holds any non-seed row, else 0 (for the two
    migration-seeded reference tables only rows BEYOND the migration's own seed count). Empty list ==
    migrated and empty.
    """
    problems: list[tuple[str, str]] = []
    if current_revision is None:
        problems.append(("migration", "alembic_version is missing or empty: the database is not migrated "
                         "(run `alembic upgrade head` against it first)"))
    elif current_revision != head_revision:
        problems.append(("migration", f"database is at migration {current_revision!r} but head is "
                         f"{head_revision!r}; run `alembic upgrade head` against it first"))
    missing = [t for t in REQUIRED_TABLES if t not in table_counts]
    if missing:
        problems.append(("migration", f"required tables are missing: {', '.join(missing)}"))
    populated = sorted(t for t, n in table_counts.items() if n)
    if populated:
        problems.append((
            "empty",
            "the database is not empty (tables holding data: " + ", ".join(populated) + "). This command never "
            "truncates or cleans a database: create a fresh disposable database, run `alembic upgrade head`, "
            "and rerun"))
    return problems


def alembic_head_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend = Path(__file__).resolve().parents[2]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected exactly one alembic head, found {heads}")
    return heads[0]


def inspect_database(database_url: str) -> tuple[str | None, dict[str, int]]:
    """READ-ONLY: the current alembic revision and, per public base table, whether it holds any non-seed
    row (0/1). Enforced read-only by the server (`postgresql_readonly`)."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import NullPool

    engine = create_engine(database_url, poolclass=NullPool, future=True)
    try:
        with engine.connect().execution_options(postgresql_readonly=True) as conn:
            tables = [r[0] for r in conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"))]
            revision = None
            if "alembic_version" in tables:
                rows = conn.execute(text("SELECT version_num FROM alembic_version")).all()
                revision = rows[0][0] if len(rows) == 1 else None
            quote = engine.dialect.identifier_preparer.quote
            seed = ", ".join(f"'{t}'" for t in MIGRATION_SEEDED_TICKERS)
            counts: dict[str, int] = {}
            for name in tables:
                if name == "alembic_version":
                    continue
                if name == "symbols":
                    sql = f"SELECT EXISTS (SELECT 1 FROM symbols WHERE ticker NOT IN ({seed}) OR is_backtest)"
                elif name == "scanner_universe_symbols":
                    sql = ("SELECT EXISTS (SELECT 1 FROM scanner_universe_symbols u "
                           "LEFT JOIN symbols s ON s.id = u.symbol_id "
                           f"WHERE s.ticker IS NULL OR s.ticker NOT IN ({seed}))")
                else:
                    sql = f"SELECT EXISTS (SELECT 1 FROM {quote(name)})"
                counts[name] = int(bool(conn.execute(text(sql)).scalar()))
            conn.rollback()
        return revision, counts
    finally:
        engine.dispose()


def describe_target(settings: Any) -> str:
    """host:port/database as user — never the password."""
    return f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db} as {settings.postgres_user}"


# =================================================================================================
# Controlled environment
# =================================================================================================
class DeterministicClock:
    """The single wall clock for the whole run. Callable, so it plugs in wherever a `wall_clock`/`clock`
    callable is accepted; `.now` is advanced explicitly by the scenarios."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _LogRing(logging.Handler):
    """Keeps the last WARNING+ records for failure diagnostics; prints nothing."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: deque[str] = deque(maxlen=40)

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.levelname} {record.name}: {record.getMessage()}")


def reset_singletons() -> None:
    """Mirror of backend/tests/conftest.py's `_reset_app_singletons` body. Each `TestClient` block owns a
    fresh event loop and every cached singleton's asyncio primitives are bound to the loop that first
    touched them, so a new "process" (lifespan) needs them cleared. Keep in sync with conftest.py."""
    import app.api.routes.finnhub_data as finnhub_data_module
    import app.api.routes.market_data as market_data_module
    import app.api.websocket.channels as channels_module
    import app.api.websocket.manager as manager_module
    import app.context_engine.engine as context_engine_module
    import app.context_engine.fundamentals_refresh as fundamentals_refresh_module
    import app.event_bus.bus as bus_module
    import app.execution_engine.engine as execution_engine_module
    import app.feature_engine.engine as feature_engine_module
    import app.governor.engine as governor_engine_module
    import app.market_state_engine.engine as market_state_engine_module
    import app.services.live_tick_relay as live_tick_relay_module
    import app.strategy_engine.scheduler as strategy_scheduler_module
    import app.trading_intelligence.level_interaction_engine as level_interaction_engine_module
    import app.trading_intelligence.opportunity_cache as opportunity_cache_module
    from app.services import broker_registry

    bus_module._event_bus = None
    manager_module._manager = None
    channels_module._gateway = None
    feature_engine_module._feature_engine = None
    level_interaction_engine_module._level_interaction_engine = None
    live_tick_relay_module._live_tick_relay = None
    context_engine_module._context_engine = None
    fundamentals_refresh_module._fundamentals_refresh_jobs = None
    market_state_engine_module._market_state_engine = None
    strategy_scheduler_module._strategy_scheduler = None
    opportunity_cache_module._opportunity_cache = None
    governor_engine_module._authorizer_stub = None
    execution_engine_module._execution_engine = None
    broker_registry.clear_all()
    market_data_module._provider = None
    finnhub_data_module._provider = None


def apply_safe_environment() -> None:
    """Disable external providers and pin simulated execution. Must run BEFORE `get_settings()`.

    An explicit empty string (not `delenv`) because pydantic-settings also reads a `.env` file as a
    fallback source; only a SET value takes priority over it (see tests/conftest.py).
    """
    os.environ["FINNHUB_API_KEY"] = ""
    os.environ["POLYGON_API_KEY"] = ""
    os.environ["EXECUTION_MODE"] = "simulated"
    from app.core.config import get_settings

    get_settings.cache_clear()


# =================================================================================================
# The acceptance run
# =================================================================================================
@dataclass
class Evidence:
    """What a scenario learned about one trade; later scenarios (outcomes) re-check these exact rows."""

    symbol: str
    trade_id: Any = None
    entry_order_id: str | None = None
    position_id: Any = None
    close_order_id: str | None = None
    exit_reason: str | None = None
    entry_price: float = ENTRY_PRICE
    exit_price: float | None = None
    qty: int | None = None


class Acceptance:
    def __init__(self, report: Report, checker: Checker, settings: Any, log_ring: _LogRing) -> None:
        self.report = report
        self.c = checker
        self.settings = settings
        self.log_ring = log_ring
        self.clock = DeterministicClock(SESSION_DAY_1)
        self.api_reads: dict[str, int] = {}
        self.venues: list[Any] = []
        self.recorders: list[Any] = []
        self.client: Any = None
        self.events: list[Any] = []
        self.evidence: dict[str, Evidence] = {}
        self.restart_marker: tuple[Evidence, Any] | None = None
        # SimulatedVenue is in-memory only. A FRESH one after a restart reports qty 0 for an open ledger
        # position and startup reconciliation then fails closed (`reconciliation_blocked`, by design; see
        # tests/test_simulated_eod_integration.py::test_real_lifespan_eod_and_fresh_venue_restart_block).
        # To prove restoration the restart therefore RETAINS the previous venue's book, standing in for a
        # durable venue. This is a stated limitation, not a claim about a real broker.
        self.retain_venue = False
        self.d: SimpleNamespace = SimpleNamespace()

    # --- lazy imports (nothing below may import app.db.session before preconditions pass) ------------
    def _import(self) -> None:
        import app.broker_adapters.simulated_venue as venue_module
        import app.execution_engine.exit_ledger as exit_module
        import app.governor.engine as governor_module
        import app.position_monitor.engine as monitor_module
        import app.strategy_engine.scheduler as scheduler_module
        import app.trading_intelligence.outcome_recorder as recorder_module
        from app.context_engine.engine import get_context_engine
        from app.core.market_clock import MarketClock
        from app.db.session import SessionLocal
        from app.event_bus.bus import get_event_bus
        from app.main import app as fastapi_app
        from app.models.execution_ledger import ExitRequest, Fill, Order, Position, Trade
        from app.models.trading_intelligence import StrategyOutcomeRecord
        from app.schemas.events.envelope import EventEnvelope, EventType
        from app.schemas.events.features import FeatureSet
        from app.schemas.events.market_data import PriceUpdated
        from app.schemas.events.market_state import MarketState
        from app.services import broker_registry
        from app.strategy_engine.base_strategy import Opportunity
        from app.strategy_engine.gap_strategy import GapStrategy, default_config as gap_default_config
        from app.trading_intelligence.state_snapshot import StrategyOutcomeSnapshots

        self.d = SimpleNamespace(
            venue_module=venue_module, exit_module=exit_module, governor_module=governor_module,
            monitor_module=monitor_module, recorder_module=recorder_module, scheduler_module=scheduler_module,
            get_context_engine=get_context_engine, GapStrategy=GapStrategy, gap_default_config=gap_default_config,
            MarketClock=MarketClock,
            SessionLocal=SessionLocal, get_event_bus=get_event_bus, app=fastapi_app, Fill=Fill,
            ExitRequest=ExitRequest, Order=Order, Position=Position, Trade=Trade,
            Outcome=StrategyOutcomeRecord, EventEnvelope=EventEnvelope, EventType=EventType,
            PriceUpdated=PriceUpdated, FeatureSet=FeatureSet, MarketState=MarketState,
            broker_registry=broker_registry, Opportunity=Opportunity,
            StrategyOutcomeSnapshots=StrategyOutcomeSnapshots,
        )

    # --- controlled inputs ------------------------------------------------------------------------
    @contextlib.contextmanager
    def controlled_inputs(self):
        """Patch ONLY inputs and clocks: the session check consults the deterministic clock, the entry
        snapshot capture returns a fixed snapshot, and the venue / monitor / exit ledger receive the
        deterministic clock (monitor auto-pulse off so EOD is driven explicitly). Production classes are
        otherwise untouched. All patches are undone on exit."""
        d, outer = self.d, self
        original_market_clock = (d.governor_module.get_market_clock, d.venue_module.get_market_clock)
        original_venue = d.venue_module.SimulatedVenue
        original_monitor = d.monitor_module.PositionMonitor
        original_ledger = d.exit_module.PostgresExitLedger
        original_recorder = d.recorder_module.OutcomeRecorder
        original_capture = d.governor_module.capture_strategy_outcome_snapshots
        clock = self.clock

        class AcceptanceMarketClock(d.MarketClock):
            """Answers every session/day question for the deterministic clock. The authorizer passes the
            REAL `datetime.now()` explicitly, so ignoring the argument is what makes the run independent of
            when the command is executed."""

            def is_regular_session(self, ts=None):
                return super().is_regular_session(clock())

            def trading_day(self, ts=None):
                return super().trading_day(clock())

        market_clock = AcceptanceMarketClock()

        def make_venue(*, event_bus=None, **kwargs):
            if outer.retain_venue and outer.venues:
                venue = outer.venues[-1]
                venue._event_bus = event_bus  # same retained book, new process bus
                return venue
            venue = original_venue(event_bus=event_bus, **kwargs)
            outer.venues.append(venue)
            return venue

        class AcceptanceMonitor(original_monitor):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, wall_clock=clock, pulse_interval_seconds=None, **kwargs)

        class AcceptanceRecorder(original_recorder):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                outer.recorders.append(self)

        # Both the authorizer (rule 1: regular session) and the venue's own session guard obtain their
        # MarketClock through `get_market_clock`; hand both the deterministic one.
        d.governor_module.get_market_clock = lambda: market_clock
        d.venue_module.get_market_clock = lambda: market_clock
        d.governor_module.capture_strategy_outcome_snapshots = lambda symbol: d.StrategyOutcomeSnapshots(
            market_state={"trend_score": 1.0}, context={"news": []})
        d.venue_module.SimulatedVenue = make_venue
        d.monitor_module.PositionMonitor = AcceptanceMonitor
        d.exit_module.PostgresExitLedger = lambda sessions: original_ledger(sessions, clock=clock)
        d.recorder_module.OutcomeRecorder = AcceptanceRecorder
        try:
            yield
        finally:
            d.governor_module.get_market_clock, d.venue_module.get_market_clock = original_market_clock
            d.governor_module.capture_strategy_outcome_snapshots = original_capture
            d.venue_module.SimulatedVenue = original_venue
            d.monitor_module.PositionMonitor = original_monitor
            d.exit_module.PostgresExitLedger = original_ledger
            d.recorder_module.OutcomeRecorder = original_recorder

    # --- one real "process": the real lifespan ------------------------------------------------------
    @contextlib.contextmanager
    def lifespan(self):
        """Enter the REAL `app.main` lifespan. `with TestClient` guarantees the lifespan shutdown runs on
        every exit path, including a failed milestone."""
        from fastapi.testclient import TestClient

        reset_singletons()
        self.events = []
        try:
            with TestClient(self.d.app) as client:
                self.client = client
                self.d.get_event_bus().subscribe_all(lambda env: self.events.append(env))
                try:
                    yield client
                finally:
                    self.client = None
        finally:
            reset_singletons()

    # --- public API reads ---------------------------------------------------------------------------
    def api(self, path: str, **params: Any) -> dict:
        response = self.client.get(path, params=params or None)
        self.api_reads[path] = self.api_reads.get(path, 0) + 1
        if response.status_code != 200:
            raise AcceptanceFailure(self.c.scenario, f"GET {path}", f"HTTP {response.status_code}")
        return response.json()

    # --- inputs -------------------------------------------------------------------------------------
    def publish(self, envelope: Any) -> None:
        self.client.portal.call(self.d.get_event_bus().publish, envelope)

    def send_price(self, symbol: str, price: float) -> None:
        d = self.d
        self.publish(d.EventEnvelope(
            event_type=d.EventType.PRICE_UPDATED, symbol=symbol,
            payload=d.PriceUpdated(price=price, size=100, exchange_ts=self.clock.now).model_dump(mode="json")))

    def send_opportunity(self, symbol: str) -> None:
        d = self.d
        payload = d.Opportunity(
            strategy=STRATEGY_NAME, version=STRATEGY_VERSION, direction="BUY", confidence=0.8,
            structural_invalidation=STOP_LEVEL, structural_target=TARGET_LEVEL,
            evidence={"conditions": {}, "reason": "simulated MVP acceptance seed", "basis": "live"},
            status="actionable", setup_detected_at=self.clock.now).model_dump(mode="json")
        self.publish(d.EventEnvelope(event_type=d.EventType.OPPORTUNITY_CREATED, symbol=symbol, payload=payload))

    def send_gap_inputs(self, symbol: str, *, setup_present: bool) -> None:
        """Feed the installed scheduler through normal context/feature/market-state contracts.

        Gap's own evaluate() decides whether a proposal exists. This method never constructs or
        publishes an OpportunityCreated event.
        """
        d = self.d
        self.client.portal.call(d.get_context_engine().evaluate_for_symbol, symbol)
        features = {"pdc": 92.0, "gap_dollars": 3.0, "gap_pct": 100.0 * 3.0 / 92.0,
                    "regular_open": STOP_LEVEL} if setup_present else {}
        feature_set = d.FeatureSet(timeframe="1m", candle_ts=self.clock.now, close=ENTRY_PRICE,
                                   features=features)
        self.publish(d.EventEnvelope(event_type=d.EventType.FEATURES_UPDATED, symbol=symbol,
                                     payload=feature_set.model_dump(mode="json")))
        self.settle()  # scheduler's feature cache and other FeaturesUpdated subscribers have processed it
        market_state = d.MarketState(timeframe="1m", candle_ts=self.clock.now,
                                     trend_score=70.0, volatility_regime_score=50.0,
                                     volume_regime_score=60.0, vwap_relationship_score=50.0)
        self.publish(d.EventEnvelope(event_type=d.EventType.MARKET_STATE_CHANGED, symbol=symbol,
                                     payload=market_state.model_dump(mode="json")))

    def venue_tick(self, symbol: str, price: float) -> None:
        venue = self.d.broker_registry.get_execution_venue()
        if venue is None:
            raise AcceptanceFailure(self.c.scenario, "venue tick", "no execution venue is registered")
        self.client.portal.call(venue.ingest_tick, symbol, price, self.clock.now)

    def settle(self, timeout: float = 10.0) -> None:
        """Bounded drain of the in-flight queues, so a following NEGATIVE assertion ("still exactly one
        close order") is made only after the work that could have violated it has finished. Mirrors
        tests/test_simulated_eod_integration.py::settle_lifespan."""
        import asyncio

        from app.execution_engine.engine import get_execution_engine

        bus = self.d.get_event_bus()
        monitor = self.d.app.state.position_monitor
        engine = get_execution_engine()
        portfolio = self.d.app.state.world_view_portfolio_reader

        async def drain():
            queues = (bus._normal_queue, monitor._queue, engine._queue, bus._critical_queue, portfolio._queue)
            while True:
                for queue in queues:
                    await queue.join()
                if not any(q._unfinished_tasks for q in queues):
                    return

        async def bounded():
            await asyncio.wait_for(drain(), timeout)

        self.client.portal.call(bounded)

    # --- ledger reads (rows written by the application's own workers) ------------------------------
    def trade(self, symbol: str, strategy_name: str = STRATEGY_NAME):
        d = self.d
        with d.SessionLocal() as s:
            return s.query(d.Trade).filter(d.Trade.strategy_name == strategy_name, d.Trade.symbol == symbol) \
                .order_by(d.Trade.created_at).first()

    def orders(self, trade_id) -> list:
        d = self.d
        with d.SessionLocal() as s:
            return s.query(d.Order).filter(d.Order.trade_id == trade_id).order_by(d.Order.id).all()

    def fills(self, trade_id) -> list:
        d = self.d
        with d.SessionLocal() as s:
            ids = [o.client_order_id for o in s.query(d.Order).filter(d.Order.trade_id == trade_id)]
            return s.query(d.Fill).filter(d.Fill.client_order_id.in_(ids)).order_by(d.Fill.ledger_seq).all()

    def position(self, trade_id):
        d = self.d
        with d.SessionLocal() as s:
            return s.query(d.Position).filter(d.Position.trade_id == trade_id).first()

    def exit_request(self, position_id):
        d = self.d
        with d.SessionLocal() as s:
            return s.get(d.ExitRequest, position_id)

    def outcomes(self, trade_id=None, strategy_name: str | None = STRATEGY_NAME) -> list:
        d = self.d
        with d.SessionLocal() as s:
            query = s.query(d.Outcome)
            if strategy_name is not None:
                query = query.filter(d.Outcome.strategy_name == strategy_name)
            if trade_id is not None:
                query = query.filter(d.Outcome.opportunity_id == trade_id)
            return query.all()

    # --- composite milestones --------------------------------------------------------------------
    def portfolio(self) -> dict | None:
        return self.api("/intelligence/portfolio-state")["portfolio"]

    def portfolio_has(self, symbol: str) -> bool:
        snapshot = self.portfolio()
        return snapshot is not None and any(p["symbol"] == symbol and p["qty"] > 0 for p in snapshot["positions"])

    def expect_startup_ready(self) -> None:
        body = self.api("/health/execution-startup")
        self.c.check("execution startup reports ready (GET /health/execution-startup)",
                     body["status"] == "ready", lambda: f"startup status: {body}", note=f"status={body['status']}")
        monitor = self.api("/intelligence/exit-intents")
        self.c.check("Position Monitor running (GET /intelligence/exit-intents)",
                     monitor["monitor_status"] == "running", lambda: f"monitor: {monitor['monitor_status']}")

    def enter_position(self, symbol: str, *, label: str, strategy_name: str = STRATEGY_NAME,
                       opportunity_input: Callable[[str], None] | None = None,
                       target_level: float = TARGET_LEVEL) -> Evidence:
        """Drive a seeded or scheduler-generated opportunity to an open position."""
        c = self.c
        ev = self.evidence.setdefault(symbol, Evidence(symbol=symbol))
        self.send_price(symbol, ENTRY_PRICE)        # reference price for the authorizer
        (opportunity_input or self.send_opportunity)(symbol)

        def entry_state():
            trade = self.trade(symbol, strategy_name)
            order = None if trade is None else next(iter(self.orders(trade.trade_id)), None)
            return (None if trade is None else trade.decision, None if order is None else order.status)

        c.wait(f"{label}: authorizer persists an approved decision and the entry order is submitted",
               lambda: entry_state() == ("approved", "submitted"), entry_state)
        trade = self.trade(symbol, strategy_name)
        order = self.orders(trade.trade_id)[0]
        expected_qty = math.floor(self.settings.execution_fixed_notional_usd / ENTRY_PRICE)
        c.check(f"{label}: persisted approval is simulated, auto, sized by the authorizer limits",
                (trade.decision, trade.execution_mode, trade.execution_venue, trade.origin, trade.status)
                == ("approved", "simulated", "simulated", "auto", "open")
                and order.client_order_id == f"{trade.trade_id}:entry" and order.qty == expected_qty
                and (order.side, order.position_effect) == ("BUY", "open"),
                lambda: (f"trade={trade.decision}/{trade.execution_mode}/{trade.origin}/{trade.status} "
                         f"order={order.client_order_id} qty={order.qty}"),
                note=f"qty={order.qty}")
        ev.trade_id, ev.entry_order_id, ev.qty = trade.trade_id, order.client_order_id, order.qty

        self.venue_tick(symbol, ENTRY_PRICE)         # the venue fills the accepted market order

        def open_state():
            pos = self.position(trade.trade_id)
            return (None if pos is None else pos.status, len(self.fills(trade.trade_id)), self.portfolio_has(symbol))

        c.wait(f"{label}: SimulatedVenue fill is applied; position open and visible in Portfolio State",
               lambda: open_state() == ("open", 1, True), open_state)
        pos = self.position(trade.trade_id)
        fill = self.fills(trade.trade_id)[0]
        c.check(f"{label}: ledger holds one entry fill and an open position with the structural stop/target",
                (float(fill.price), fill.qty) == (ENTRY_PRICE, ev.qty) and pos.qty == ev.qty
                and (float(pos.avg_price), float(pos.stop), float(pos.target)) == (ENTRY_PRICE, STOP_LEVEL, target_level)
                and self.orders(trade.trade_id)[0].status == "filled",
                lambda: (f"fill={fill.price}x{fill.qty} position={pos.status}/{pos.qty}/{pos.avg_price}/"
                         f"{pos.stop}/{pos.target}"))
        ev.position_id = pos.position_id
        return ev

    def expect_public_open_position(self, ev: Evidence) -> None:
        """Public API views agree with the ledger for an open position."""
        orders = self.api("/intelligence/execution-orders", symbol=ev.symbol)["orders"]
        fills = self.api("/intelligence/execution-fills", symbol=ev.symbol)["fills"]
        positions = self.api("/intelligence/execution-positions", symbol=ev.symbol)["positions"]
        portfolio = self.portfolio()
        self.c.check(
            "public APIs agree: orders, fills, positions and portfolio-state show the open position",
            [o["client_order_id"] for o in orders] == [ev.entry_order_id] and orders[0]["status"] == "filled"
            and len(fills) == 1 and fills[0]["qty"] == ev.qty
            and Decimal(fills[0]["price"]) == Decimal(str(ENTRY_PRICE))
            and len(positions) == 1 and positions[0]["status"] == "open"
            and positions[0]["position_id"] == str(ev.position_id)
            and portfolio is not None and portfolio["open_position_count"] == 1
            and [p["position_id"] for p in portfolio["positions"]] == [str(ev.position_id)],
            lambda: f"orders={orders} fills={fills} positions={positions} portfolio={portfolio}",
            note="execution-orders, execution-fills, execution-positions, portfolio-state")

    def protective_close(self, ev: Evidence, *, reason: str, level: float, trigger_tick: float, fill_price: float,
                         label: str) -> None:
        """A stop/target observation -> durable exit request -> close order -> closing fill -> flat."""
        c = self.c
        trade_id, symbol = ev.trade_id, ev.symbol
        self.send_price(symbol, trigger_tick)

        def exit_state():
            request = self.exit_request(ev.position_id)
            closes = [o.status for o in self.orders(trade_id) if o.position_effect == "close"]
            intents = self.api("/intelligence/exit-intents", symbol=symbol)["exit_intents"]
            return request is not None, closes, len(intents)

        c.wait(f"{label}: {reason} observation becomes a durable exit request, intent listed, close order submitted",
               lambda: exit_state() == (True, ["submitted"], 1), exit_state)
        request = self.exit_request(ev.position_id)
        close = next(o for o in self.orders(trade_id) if o.position_effect == "close")
        api_requests = self.api("/intelligence/execution-exit-requests", symbol=symbol)["exit_requests"]
        c.check(f"{label}: exit request and close order carry the {reason} reason; execution-exit-requests agrees",
                request.exit_reason == reason and float(request.trigger_price) == level
                and close.client_order_id == f"{trade_id}:exit:1" and close.exit_reason == reason
                and (close.side, close.qty, close.position_id) == ("SELL", ev.qty, ev.position_id)
                and len(api_requests) == 1 and api_requests[0]["position_id"] == str(ev.position_id)
                and api_requests[0]["exit_reason"] == reason,
                lambda: (f"request={request.exit_reason}@{request.trigger_price} "
                         f"close={close.client_order_id}/{close.exit_reason} api={api_requests}"))
        ev.close_order_id, ev.exit_reason = close.client_order_id, reason
        self.finish_close(ev, fill_price=fill_price, label=label)

    def finish_close(self, ev: Evidence, *, fill_price: float, label: str) -> None:
        c = self.c
        trade_id, symbol = ev.trade_id, ev.symbol
        self.venue_tick(symbol, fill_price)

        def close_state():
            pos = self.position(trade_id)
            closed_event = any(e.event_type == self.d.EventType.POSITION_CLOSED and e.symbol == symbol
                               for e in list(self.events))
            return (None if pos is None else pos.status), closed_event

        c.wait(f"{label}: closing fill applied, position closed, PositionClosed published",
               lambda: close_state() == ("closed", True), close_state)
        self.settle()
        pos, fills = self.position(trade_id), self.fills(trade_id)
        closes = [o for o in self.orders(trade_id) if o.position_effect == "close"]
        expected_pnl = (fill_price - ev.entry_price) * ev.qty
        c.check(f"{label}: exactly one closing fill; position flat with the ledger-derived realized P&L",
                len(fills) == 2 and len(closes) == 1 and closes[0].status == "filled"
                and float(fills[1].price) == fill_price and fills[1].qty == ev.qty
                and pos.qty == 0 and float(pos.realized_pnl) == expected_pnl,
                lambda: (f"fills={[(float(f.price), f.qty) for f in fills]} closes={[o.status for o in closes]} "
                         f"pnl={pos.realized_pnl} expected={expected_pnl}"),
                note=f"realized_pnl={expected_pnl:+.2f}")
        ev.exit_price = fill_price
        portfolio = self.portfolio()
        positions = self.api("/intelligence/execution-positions", symbol=symbol)["positions"]
        c.check("public APIs agree: portfolio-state is flat and execution-positions shows the position closed",
                portfolio is not None and portfolio["positions"] == [] and portfolio["open_position_count"] == 0
                and len(positions) == 1 and positions[0]["status"] == "closed"
                and Decimal(positions[0]["realized_pnl"]) == Decimal(str(expected_pnl)),
                lambda: f"portfolio={portfolio} positions={positions}")

    # --- scenarios ---------------------------------------------------------------------------------
    def scenario_1(self) -> None:
        c = self.c
        c.begin("S1", "Scenario 1 — seeded OpportunityCreated -> authorizer -> execution -> "
                      "SimulatedVenue -> Portfolio State")
        self.expect_startup_ready()
        flat = self.portfolio()
        c.check("Portfolio State starts flat (GET /intelligence/portfolio-state)",
                flat is not None and flat["positions"] == [] and flat["open_position_count"] == 0, lambda: f"{flat}")
        ev = self.enter_position(SYMBOL_TARGET, label="S1")
        self.expect_public_open_position(ev)

    def scenario_2(self) -> None:
        c = self.c
        c.begin("S2", "Scenario 2 — separate positions close through target and stop observations")
        target = self.evidence[SYMBOL_TARGET]
        self.protective_close(target, reason="target", level=TARGET_LEVEL, trigger_tick=TARGET_TICK,
                              fill_price=TARGET_TICK, label="target")
        stop = self.enter_position(SYMBOL_STOP, label="stop")
        self.protective_close(stop, reason="stop", level=STOP_LEVEL, trigger_tick=STOP_TICK,
                              fill_price=STOP_TICK, label="stop")
        c.check("the two closed positions are separate trades with separate positions",
                target.trade_id != stop.trade_id and target.position_id != stop.position_id
                and {target.exit_reason, stop.exit_reason} == {"target", "stop"},
                lambda: f"{target} {stop}")

    def scenario_3(self) -> None:
        c = self.c
        c.begin("S3", "Scenario 3 — simulated EOD window closes a position, deterministic clock")
        ev = self.enter_position(SYMBOL_EOD, label="eod")
        lead = self.settings.execution_eod_flatten_lead_seconds
        flatten = SESSION_CLOSE_DAY_1 - timedelta(seconds=lead)
        self.clock.now = flatten + timedelta(seconds=min(5, lead // 2))
        c.check("deterministic clock advanced into the EOD placement window",
                flatten <= self.clock.now < SESSION_CLOSE_DAY_1, lambda: f"clock={self.clock.now.isoformat()}",
                note=f"clock={self.clock.now.strftime('%H:%M:%SZ')}, window starts {flatten.strftime('%H:%M:%SZ')}")
        self.send_price(SYMBOL_EOD, EOD_TICK)
        self.settle()    # the monitor has replayed the tick before the pulse evaluates the EOD window
        monitor = self.d.app.state.position_monitor
        c.check("Position Monitor accepts an explicit EOD pulse", bool(monitor.enqueue_pulse()), "pulse not accepted")

        def eod_state():
            request = self.exit_request(ev.position_id)
            closes = [(o.status, o.exit_reason) for o in self.orders(ev.trade_id) if o.position_effect == "close"]
            return (None if request is None else request.exit_reason), closes

        c.wait("EOD pulse becomes a durable eod_flatten request and one submitted close order",
               lambda: eod_state() == ("eod_flatten", [("submitted", "eod_flatten")]), eod_state)
        request = self.exit_request(ev.position_id)
        api = self.api("/intelligence/execution-exit-requests", symbol=SYMBOL_EOD)["exit_requests"]
        c.check("durable EOD window is [close - lead, close) and execution-exit-requests agrees",
                request.eod_flatten_at == flatten and request.eod_close_at == SESSION_CLOSE_DAY_1
                and len(api) == 1 and api[0]["exit_reason"] == "eod_flatten" and api[0]["eod_flatten_at"] is not None,
                lambda: f"request window={request.eod_flatten_at}..{request.eod_close_at} api={api}")
        ev.close_order_id = f"{ev.trade_id}:exit:1"
        ev.exit_reason = "eod_flatten"
        self.finish_close(ev, fill_price=EOD_TICK, label="eod")

    def scenario_4_before_restart(self) -> None:
        c = self.c
        c.begin("S4", "Scenario 4 — restart the real lifespan with an open position; restoration, then protective closure")
        # Next regular-session day, so the EOD clock position of scenario 3 does not leak into this one.
        self.clock.now = SESSION_DAY_2
        c.check("deterministic clock moved to the next regular session day",
                self.clock.now == SESSION_DAY_2, note=f"clock={self.clock.now.isoformat()}")
        ev = self.enter_position(SYMBOL_RESTART, label="before restart")
        self.restart_marker = (ev, self.venues[-1])
        # The lifespan exits right after this method returns (process 1 ends), and a fresh one starts.

    def scenario_4_after_restart(self) -> None:
        c = self.c
        assert self.restart_marker is not None
        ev, first_venue = self.restart_marker
        self.expect_startup_ready()
        c.check("restart rebuilt every worker; only the venue's order book is retained (stand-in for a durable venue)",
                self.venues[-1] is first_venue and len(self.venues) == 1 and first_venue.is_connected(),
                lambda: f"venues={len(self.venues)} connected={first_venue.is_connected()}",
                note="a fresh SimulatedVenue would fail closed: reconciliation_blocked")

        def restored_state():
            snapshot = self.portfolio()
            return None if snapshot is None else [(p["position_id"], p["qty"], p["stop"]) for p in snapshot["positions"]]

        c.wait("Portfolio State restores the open position from the ledger (same position id, qty, stop)",
               lambda: restored_state() == [(str(ev.position_id), ev.qty, f"{STOP_LEVEL:.6f}")], restored_state)
        positions = self.api("/intelligence/execution-positions", symbol=SYMBOL_RESTART)["positions"]
        self.settle()
        c.check("restart did not duplicate the entry: still one order, one fill, one open position",
                len(self.orders(ev.trade_id)) == 1 and len(self.fills(ev.trade_id)) == 1
                and len(positions) == 1 and positions[0]["status"] == "open",
                lambda: (f"orders={len(self.orders(ev.trade_id))} fills={len(self.fills(ev.trade_id))} "
                         f"api={positions}"))
        self.protective_close(ev, reason="stop", level=STOP_LEVEL, trigger_tick=STOP_TICK,
                              fill_price=STOP_TICK, label="after restart")

    # --- scenario 5 -------------------------------------------------------------------------------
    def check_outcomes(self, *, phase: str) -> dict:
        """Exactly one linked, ledger-consistent outcome per eligible closed trade; none for ineligible."""
        c, d = self.c, self.d
        closed = [ev for ev in self.evidence.values() if ev.exit_price is not None]
        status = self.api("/intelligence/execution-outcome-status", limit=100)
        listed = self.api("/intelligence/strategy-outcomes", limit=500)["outcomes"]
        rows = {ev.symbol: self.outcomes(ev.trade_id) for ev in closed}
        with d.SessionLocal() as s:
            trades = {ev.symbol: s.get(d.Trade, ev.trade_id) for ev in closed}
            positions = {ev.symbol: s.get(d.Position, ev.position_id) for ev in closed}
        total = len(self.outcomes())
        c.check(f"{phase}: exactly one strategy outcome per closed trade, none extra "
                f"({len(closed)} closed trades, {total} outcomes)",
                total == len(closed) and all(len(r) == 1 for r in rows.values()),
                lambda: f"outcomes per trade={ {k: len(v) for k, v in rows.items()} } total={total}")
        problems = []
        for ev in closed:
            outcome, trade, position = rows[ev.symbol][0], trades[ev.symbol], positions[ev.symbol]
            if not (trade.outcome_id == outcome.outcome_id and trade.outcome_status == "recorded"):
                problems.append(f"{ev.symbol}: trade not linked/recorded ({trade.outcome_id}/{trade.outcome_status})")
            if not (outcome.is_backtest is False and outcome.backtest_run_id is None
                    and (outcome.execution_mode, outcome.execution_venue, outcome.origin)
                    == ("simulated", "simulated", "auto")):
                problems.append(f"{ev.symbol}: outcome is not a simulated, non-backtest, auto outcome")
            if outcome.exit_reason != ev.exit_reason:
                problems.append(f"{ev.symbol}: exit_reason {outcome.exit_reason!r} != ledger {ev.exit_reason!r}")
            if (float(outcome.entry_price), float(outcome.exit_price)) != (ev.entry_price, ev.exit_price):
                problems.append(f"{ev.symbol}: prices {outcome.entry_price}/{outcome.exit_price} != ledger")
            if abs(float(outcome.realized_pnl) - float(position.realized_pnl)) > 1e-6:
                problems.append(f"{ev.symbol}: realized_pnl {outcome.realized_pnl} != position {position.realized_pnl}")
        c.check(f"{phase}: each outcome is linked to its trade and agrees with the ledger fills and position",
                not problems, lambda: "; ".join(problems))
        c.check(f"{phase}: strategy-outcomes lists each closed trade once; "
                "execution-outcome-status counts them all recorded",
                all(sum(1 for o in listed if o["opportunity_id"] == str(ev.trade_id)) == 1 for ev in closed)
                and all(o["is_backtest"] is False for o in listed if o["opportunity_id"] in
                        {str(ev.trade_id) for ev in closed})
                and status["counts"]["recorded"] == len(closed)
                and sum(v for k, v in status["counts"].items() if k != "recorded") == 0,
                lambda: f"counts={status['counts']} listed={len(listed)}")
        return {sym: [o.outcome_id for o in r] for sym, r in rows.items()}

    def scenario_5_first(self) -> dict:
        c, d = self.c, self.d
        c.begin("S5", "Scenario 5 — every eligible closed trade receives exactly one linked outcome, "
                      "including after restart")
        # Negative control: an opportunity the authorizer must REJECT (no reference price) is not eligible.
        self.send_opportunity(SYMBOL_REJECTED)
        c.wait("negative control: authorizer persists a rejected decision for an opportunity with no reference price",
               lambda: (t := self.trade(SYMBOL_REJECTED)) is not None and t.decision == "rejected",
               lambda: getattr(self.trade(SYMBOL_REJECTED), "decision", None))
        rejected = self.trade(SYMBOL_REJECTED)
        c.check("rejected trade has no order, no position and no outcome link",
                not self.orders(rejected.trade_id) and self.position(rejected.trade_id) is None
                and rejected.outcome_id is None and not self.outcomes(rejected.trade_id),
                lambda: f"orders={len(self.orders(rejected.trade_id))} outcome={rejected.outcome_id}")
        closed = [ev for ev in self.evidence.values() if ev.exit_price is not None]

        def recorded():
            with d.SessionLocal() as s:
                return sorted(s.get(d.Trade, ev.trade_id).outcome_status or "pending" for ev in closed)

        c.wait(f"running OutcomeRecorder records all {len(closed)} closed trades (scenarios 1-4)",
               lambda: recorded() == ["recorded"] * len(closed),
               lambda: (recorded(), list(self.log_ring.records)[-5:]))
        return self.check_outcomes(phase="same lifespan")

    def scenario_5_after_restart(self, before: dict) -> None:
        import asyncio

        c = self.c
        self.expect_startup_ready()
        recorder = self.recorders[-1]
        c.wait("restarted OutcomeRecorder is running (startup scan finished)",
               lambda: recorder._worker is not None and recorder._sweeper is not None,
               lambda: (recorder._worker, recorder._sweeper))

        async def sweep_and_drain():
            # One more sweep over the ledger, drained: an already-recorded trade must be skipped, not re-recorded.
            await recorder.scan()
            await asyncio.wait_for(recorder._queue.join(), 10.0)

        self.client.portal.call(sweep_and_drain)
        c.check("an explicit recorder sweep after restart is drained", recorder._queue.empty(),
                lambda: f"queue size {recorder._queue.qsize()}")
        after = self.check_outcomes(phase="after restart")
        c.check("the same outcome rows survive the restart (no row replaced or duplicated)",
                after == before, lambda: f"before={before} after={after}")
        flat = self.portfolio()
        c.check("restarted Portfolio State is flat; nothing re-opened or re-closed",
                flat is not None and flat["positions"] == [] and flat["open_position_count"] == 0, lambda: f"{flat}")

    # --- scenario 6 -------------------------------------------------------------------------------
    @contextlib.contextmanager
    def gap_only_registry(self):
        """Use the scheduler's existing registry seam to isolate one registered production strategy."""
        d = self.d
        original = d.scheduler_module.default_registry
        d.scheduler_module.default_registry = lambda active_from: [
            d.GapStrategy(d.gap_default_config(active_from))
        ]
        try:
            yield
        finally:
            d.scheduler_module.default_registry = original

    def gap_events(self, symbol: str) -> list[Any]:
        return [e for e in list(self.events)
                if e.event_type == self.d.EventType.OPPORTUNITY_CREATED and e.symbol == symbol]

    def scenario_6(self) -> None:
        c, d = self.c, self.d
        c.begin("S6", "Scenario 6 — real Gap strategy through StrategyScheduler, simulated execution and outcome")
        self.clock.now = SESSION_DAY_3  # fresh trading day: S4's stop must not consume S6's daily risk cap
        c.check("controlled clock is the next regular-session day, with a fresh daily loss budget",
                self.clock.now == SESSION_DAY_3, note=self.clock.now.isoformat())
        self.expect_startup_ready()
        scheduler = d.scheduler_module.get_strategy_scheduler()
        strategies = scheduler._strategies
        c.check("lifespan-installed scheduler has only the real registered Gap v1 and its default configuration",
                len(strategies) == 1 and isinstance(strategies[0], d.GapStrategy)
                and strategies[0].config == d.gap_default_config(strategies[0].config.active_from),
                lambda: f"registered={[(s.name, s.config.version) for s in strategies]}")

        # No gap keys: the selected strategy's own GATE returns None. The drain is the processing
        # barrier; a sleep would not establish that scheduler/authorizer work had finished.
        self.send_price(SYMBOL_GAP_ABSENT, ENTRY_PRICE)
        self.send_gap_inputs(SYMBOL_GAP_ABSENT, setup_present=False)
        self.settle()
        absent_orders = self.api("/intelligence/execution-orders", symbol=SYMBOL_GAP_ABSENT)["orders"]
        absent_positions = self.api("/intelligence/execution-positions", symbol=SYMBOL_GAP_ABSENT)["positions"]
        c.check("absent Gap setup produces no scheduler opportunity, authorized trade or entry order",
                not self.gap_events(SYMBOL_GAP_ABSENT)
                and self.trade(SYMBOL_GAP_ABSENT, GAP_STRATEGY) is None
                and not absent_orders and not absent_positions,
                lambda: f"opportunities={len(self.gap_events(SYMBOL_GAP_ABSENT))} "
                        f"trade={self.trade(SYMBOL_GAP_ABSENT, GAP_STRATEGY)} "
                        f"orders={len(absent_orders)} positions={len(absent_positions)}")

        def publish_gap_inputs(symbol: str) -> None:
            self.send_gap_inputs(symbol, setup_present=True)
            c.wait("Gap evaluate() causes StrategyScheduler to publish OpportunityCreated",
                   lambda: len(self.gap_events(symbol)) == 1,
                   lambda: f"events={len(self.gap_events(symbol))}")
            opportunity = d.Opportunity.model_validate(self.gap_events(symbol)[0].payload)
            conditions = opportunity.evidence.get("conditions", {})
            c.check("scheduler opportunity carries Gap v1, default-config setup evidence and structural levels",
                    opportunity.strategy == GAP_STRATEGY and opportunity.version == GAP_VERSION
                    and opportunity.direction == "BUY" and opportunity.status == "actionable"
                    and (opportunity.structural_invalidation, opportunity.structural_target)
                    == (STOP_LEVEL, GAP_TARGET)
                    and opportunity.setup_detected_at == self.clock.now
                    and conditions.get("pdc") == 92.0 and conditions.get("gap_dollars") == 3.0
                    and math.isclose(conditions.get("gap_pct", 0), 100.0 * 3.0 / 92.0)
                    and conditions.get("regular_open") == STOP_LEVEL
                    and conditions.get("close") == ENTRY_PRICE
                    and conditions.get("trend_score") == 70.0
                    and conditions.get("volume_regime_score") == 60.0,
                    lambda: f"opportunity={opportunity.model_dump(mode='json')}")

        ev = self.enter_position(SYMBOL_GAP, label="Gap", strategy_name=GAP_STRATEGY,
                                 opportunity_input=publish_gap_inputs, target_level=GAP_TARGET)
        self.expect_public_open_position(ev)
        trade = self.trade(SYMBOL_GAP, GAP_STRATEGY)
        c.check("approved trade retains Gap strategy/version and the same structural thesis",
                trade.strategy_version == GAP_VERSION
                and (trade.thesis.get("structural_invalidation"), trade.thesis.get("structural_target"))
                == (STOP_LEVEL, GAP_TARGET),
                lambda: f"version={trade.strategy_version} thesis={trade.thesis}")
        self.protective_close(ev, reason="target", level=GAP_TARGET, trigger_tick=111.0,
                              fill_price=111.0, label="Gap target")

        def recorded() -> bool:
            with d.SessionLocal() as s:
                row = s.get(d.Trade, ev.trade_id)
                return row.outcome_status == "recorded" and row.outcome_id is not None

        c.wait("OutcomeRecorder records and links the scheduler-originated trade", recorded)
        self.settle()
        rows = self.outcomes(ev.trade_id, strategy_name=None)
        trade = self.trade(SYMBOL_GAP, GAP_STRATEGY)
        c.check("exactly one linked simulated Gap v1 outcome preserves strategy and structural attribution",
                len(rows) == 1 and trade.outcome_id == rows[0].outcome_id
                and rows[0].strategy_name == GAP_STRATEGY and rows[0].strategy_version == GAP_VERSION
                and rows[0].execution_mode == "simulated" and rows[0].is_backtest is False
                and rows[0].exit_reason == "target"
                and (float(rows[0].structural_invalidation), float(rows[0].structural_target))
                == (STOP_LEVEL, GAP_TARGET),
                lambda: f"outcomes={len(rows)} trade_link={trade.outcome_id}"
                        + (f" outcome={rows[0].outcome_id}" if rows else ""))

    # --- driver ------------------------------------------------------------------------------------
    def run(self) -> None:
        self._import()
        with self.controlled_inputs():
            with self.lifespan():                      # process 1
                self.scenario_1()
                self.scenario_2()
                self.scenario_3()
                self.scenario_4_before_restart()
            self.retain_venue = True                   # see __init__: stand-in for a durable venue
            with self.lifespan():                      # process 2: restart with an open position
                self.scenario_4_after_restart()
                before = self.scenario_5_first()
            self.retain_venue = False                  # the book is flat now: a fresh venue reconciles cleanly
            with self.lifespan():                      # process 3: another restart
                self.scenario_5_after_restart(before)
            with self.gap_only_registry():
                with self.lifespan():                  # process 4: isolated real Gap strategy
                    self.scenario_6()


# =================================================================================================
# CLI
# =================================================================================================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simulated_mvp_acceptance",
        description="Prove the downstream simulated execution lifecycle against a disposable, migrated, "
                    "EMPTY PostgreSQL database. " + SCOPE_STATEMENT,
        epilog="Select the database with POSTGRES_HOST/PORT/DB/USER/PASSWORD (environment or backend/.env) "
               "and repeat its name in --database. The command never truncates or cleans a database.")
    parser.add_argument("--database", required=False, metavar="NAME",
                        help="name of the disposable database (must equal POSTGRES_DB and contain "
                             "'acceptance', 'disposable', 'scratch' or 'test')")
    parser.add_argument("--timeout", type=float, default=20.0, metavar="SECONDS",
                        help="bound for each observed-milestone wait (default 20)")
    parser.add_argument("--watchdog", type=float, default=300.0, metavar="SECONDS",
                        help="overall time limit; a hang is reported as a failure (default 300)")
    return parser


def _start_watchdog(seconds: float, report: Report) -> threading.Timer:
    def expire() -> None:
        report.line()
        report.line(f"FAIL  watchdog: no completion within {seconds:.0f}s (last milestone: {report.current})")
        report.line("RESULT: FAIL (watchdog expired)")
        os._exit(3)

    timer = threading.Timer(seconds, expire)
    timer.daemon = True
    timer.start()
    return timer


def main(argv: Sequence[str] | None = None, *, out: Any = None,
         inspect: Callable[[str], tuple[str | None, dict[str, int]]] | None = None,
         head: Callable[[], str] | None = None,
         runner: Callable[[Acceptance], None] | None = None) -> int:
    """Run the acceptance. `inspect`/`head`/`runner` are injection seams for the unit tests only."""
    args = build_parser().parse_args(argv)
    report = Report(out=out)
    report.line("Simulated MVP acceptance")
    report.line(SCOPE_STATEMENT)

    # --- preconditions: nothing below starts an application worker ---
    apply_safe_environment()
    from app.core.config import get_settings

    settings = get_settings()
    report.secrets = (settings.postgres_password,)
    report.line(f"Target database: {describe_target(settings)} (password not shown)")
    checker = Checker(report, wait_timeout=args.timeout)
    checker.begin("P", "Preconditions (read-only; no application worker is started)")

    problems = validate_target_selection(args.database, settings.postgres_db)
    if problems:
        for text in problems:
            report.fail("P.1", "disposable database explicitly selected", text)
        report.line()
        report.line("RESULT: FAIL (precondition P.1: database selection) — nothing was started or modified")
        return 2
    report.ok("P.1", "disposable database explicitly selected", f"database={settings.postgres_db}")

    problems = validate_settings_for_scenarios(settings)
    if problems:
        for text in problems:
            report.fail("P.2", "simulated execution settings admit the seeded scenarios", text)
        report.line("RESULT: FAIL (precondition P.2: settings) — nothing was started or modified")
        return 2
    report.ok("P.2", "external providers disabled; simulated execution only; limits admit the seeded entries",
              "FINNHUB/POLYGON keys blanked, execution_mode=simulated")

    try:
        head_revision = (head or alembic_head_revision)()
        revision, counts = (inspect or inspect_database)(settings.database_url)
    except Exception as exc:  # unreachable database, bad credentials, ...
        report.fail("P.3", "database reachable and migrated", f"{type(exc).__name__}: {exc}")
        report.line("RESULT: FAIL (precondition P.3: database connection) — nothing was started or modified")
        return 2
    problems = evaluate_database_state(revision, head_revision, counts)
    migration_problems = [text for kind, text in problems if kind == "migration"]
    empty_problems = [text for kind, text in problems if kind == "empty"]
    if migration_problems:
        for text in migration_problems:
            report.fail("P.3", "database migrated to head", text)
        report.line("RESULT: FAIL (precondition P.3: database not migrated) — nothing was started or modified")
        return 2
    report.ok("P.3", "database migrated to head", f"revision={revision}")
    if empty_problems:
        for text in empty_problems:
            report.fail("P.4", "database is empty before any worker starts", text)
        report.line("RESULT: FAIL (precondition P.4: database not empty) — nothing was started or modified")
        return 2
    report.ok("P.4", "database is empty before any worker starts",
              f"{len(counts)} tables checked; only the migration's own 6-symbol scanner seed present")

    # --- scenarios ---
    ring = _LogRing()
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    root_logger.addHandler(ring)  # pre-empts logging.basicConfig(): application logs stay off the console
    root_logger.setLevel(logging.INFO)
    watchdog = _start_watchdog(args.watchdog, report)
    acceptance = Acceptance(report, checker, settings, ring)
    started = time.monotonic()
    exit_code = 0
    try:
        (runner or Acceptance.run)(acceptance)
    except AcceptanceFailure as failure:
        exit_code = 1
        report.line()
        report.line(f"RESULT: FAIL — failed milestone {failure.milestone_id}: {failure.title}")
        if failure.detail:
            report.line(f"  detail: {failure.detail}")
        if ring.records:
            report.line("  recent application warnings/errors (most recent last):")
            for record in list(ring.records)[-8:]:
                report.line(f"    {record}")
    except Exception as exc:  # an unexpected crash is also a failure, with the milestone in progress named
        exit_code = 1
        report.line()
        report.line(f"RESULT: FAIL — unexpected {type(exc).__name__} during milestone {report.current}: {exc}")
    finally:
        watchdog.cancel()
        root_logger.removeHandler(ring)
        root_logger.setLevel(previous_level)
    if exit_code == 0:
        report.line()
        reads = sum(acceptance.api_reads.values())
        report.line(f"RESULT: PASS — {len(report.passed)} milestones passed in {time.monotonic() - started:.1f}s; "
                    f"{reads} public API reads across {len(acceptance.api_reads)} routes")
    report.line(SCOPE_STATEMENT)
    report.line("The database is left as evidence and is not cleaned. Recreate a fresh migrated database before rerunning.")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
