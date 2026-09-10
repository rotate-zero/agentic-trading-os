"""
Performance Intelligence's read-side query layer over `strategy_outcomes`
— decision #122, strategy-engine-design.md §5. Pairs with
app/trading_intelligence/performance.py (decision #120's write path,
`record_strategy_outcome()`) and the persistence layer it writes to
(app/models/trading_intelligence.py's `StrategyOutcomeRecord`,
app/schemas/performance.py's `StrategyOutcome`) — neither of which this
module modifies.

**Why a new sibling module, not a section inside performance.py.**
`performance.py`'s docstring, its one function, and its test file
(test_performance_intelligence.py) are all scoped to exactly one job:
assert the `entry_qty == exit_qty` invariant, then persist. That's a
write-time-correctness concern; this module's job is the opposite kind
of thing — read-only aggregation over rows already durably written,
with no invariant to enforce and no session lifecycle tied to a single
row. This codebase already has a working precedent for keeping that
split as two files rather than one: `state_snapshot.py` sits one layer
above `MarketStateEngine`/`ContextEngine` rather than living inside
either (decision #98), and `opportunity_view.py` sits one layer above
`opportunity_cache.py` rather than being folded into it (decision
#121). This module follows the same shape one layer above
`strategy_outcomes` itself. `performance.py` and
test_performance_intelligence.py are untouched by this build — zero
risk to the write path, zero collision surface with anything else
touching this table.

**Two real queries, exactly two.** §5's own text: "'Rank,' 'expectancy
by regime,' 'win rate by time-of-day' — every one of these is a
`GROUP BY` over this table, computed on demand, never a value stored on
the strategy itself." This module builds the two of those three that
have an unambiguous grouping key available on `StrategyOutcomeRecord`
without inventing a new bucketing scheme:

  - `get_win_rate_by_hour()` — groups by `entry_filled_at`, converted to
    the project's canonical market timezone (`Settings.market_timezone`,
    `"America/New_York"` by default — the same setting
    `core/market_clock.py`'s own singleton reads, confirmed by grep
    before writing this: `get_market_clock()` constructs
    `MarketClock(get_settings().market_timezone)`, not a hardcoded
    literal). Bucketing by raw stored UTC hour would technically be
    "unambiguous" but would silently misrepresent what "time-of-day"
    means to a reader — 14:00 UTC on a summer trading day is 10am ET,
    not 2pm. Reading the zone from settings, rather than hardcoding
    `'America/New_York'` inline, keeps this module consistent with the
    one place elsewhere in this codebase that already makes this exact
    ET-conversion decision, rather than introducing a second, divergent
    source of truth for "which zone counts as market time." Postgres's
    `AT TIME ZONE` handles the EST/EDT transition via its own tzdata —
    no custom DST logic here. Output field is named `hour_et`
    (0-23) specifically so a caller can never mistake it for a UTC
    hour.

  - `get_expectancy_by_session_type()` — groups by
    `context_at_entry->>'session_type'` (real PostgreSQL JSONB `->>`
    extraction, exercised against a real Postgres in this module's own
    test file, not assumed from reading the code). §5's own field
    comment for `context_at_entry` names three candidate regime
    dimensions verbatim: "gap day?, session type, VIX regime."
    `session_type` is the one chosen for v1, for two concrete reasons
    checked directly rather than picked arbitrarily: (1) decision #117
    already confirmed by grep that no `vix` field exists anywhere in
    this codebase to back a VIX-regime grouping — there is nothing real
    to group by yet; (2) `gap_day` is a boolean flag, not a genuine
    multi-valued regime dimension the way `session_type` (e.g.
    "regular"/"pre_market"/"power_hour") actually is. **This is
    Performance Intelligence's first concrete regime dimension, not
    "regime" solved generally** — there is deliberately no
    `regime_dimension` parameter or generic JSONB-key abstraction here;
    adding one now would silently imply a generalized regime framework
    that was never asked for and has exactly one real instance to
    generalize from. A future second regime dimension is a new,
    separately-considered function, not a parameter added to this one.

**Explicitly out of scope: "parameter sensitivity."** `StrategyConfig
.params`'s shape varies per strategy (§3) — there is no single column
or JSON key to group by without first designing a per-strategy params
schema, which is a real, separate design question this task does not
resolve. No function, stub, or narrowed version of this exists here.

**Metric definitions, stated exactly, not left implicit.** Both queries
use `realized_r`, never `realized_pnl` — §5's own reasoning: R is
comparable across position sizes, raw PnL isn't, and mixing them into
one aggregate would be a real correctness bug invisible from reading
any single row. `realized_r` is `NOT NULL` at both the ORM
(`Numeric(10, 4), nullable=False`) and Pydantic (`realized_r: float`,
no default, no `| None`) layers — checked directly before writing this
module, not assumed — so there is no NULL-population question for
either metric to resolve; every row `record_strategy_outcome()` ever
writes has a real `realized_r`.
  - **Expectancy** (`get_expectancy_by_session_type`) = `AVG(realized_r)`
    per group — real Postgres `AVG`, computed in SQL.
  - **Win rate** (`get_win_rate_by_hour`) = `win_count / total_trades`,
    where `win_count` is `COUNT(*) FILTER (WHERE realized_r > 0)` —
    real Postgres `FILTER`, computed in SQL. `realized_r <= 0` is
    explicitly NOT a win; a breakeven trade (`realized_r == 0`) still
    counts toward `total_trades` but not `win_count`. The division
    itself happens in Python, deliberately: `COUNT(*)` returns
    `bigint` in Postgres, and `bigint / bigint` is truncating integer
    division there — `COUNT(*) FILTER (...) / COUNT(*)` as a single SQL
    expression would silently collapse every real ratio to `0` or `1`.
    Both integers are already fully reduced, one row per hour bucket,
    by the real `GROUP BY` before this division ever runs — this is
    arithmetic on an aggregate result, not "fetching all rows and
    aggregating in Python."

**Live/backtest isolation is a hard invariant, enforced by construction
— never a blend.** `is_backtest: bool = False` on both functions is a
strict selector, not an additive "include": `False` (the default)
filters `WHERE is_backtest = false` and returns live rows only; `True`
filters `WHERE is_backtest = true` and returns backtest rows only.
There is no way to call either function and get a result mixing both
populations — §5/§7's "never blended with live in a live query" is a
hard invariant, not a style preference, and it's enforced in the SQL
`WHERE` clause itself, not left to caller discipline.

**Version filtering never silently blends strategies.** Both functions
accept optional `strategy_name`/`strategy_version` filters. Passing
`strategy_version` without `strategy_name` raises `ValueError` —
checked directly, there is no existing filtering abstraction elsewhere
in this codebase to reuse instead (grepped for any shared
strategy_name+strategy_version validation helper; found none). A
version string alone isn't guaranteed unique across different
strategies (§3's versioning is per-strategy, not global), so an
unguarded version-only filter risks silently combining two unrelated
strategies' outcomes into one aggregate — exactly the kind of blending
§3's immutable-version discipline exists to prevent.

**Empty-result semantics.** No matching rows -> `[]`. Never `None`,
never a fabricated zero-filled row, never a NULL-populated aggregate.
`strategy_outcomes` has zero real rows in production today (§5's own
framing: "prove the contract now, real callers arrive later," same
precedent as decisions #98/#120) — an empty result is the normal,
expected case this module is built to handle correctly from day one,
not an edge case discovered later.

**Query implementation: real SQL-level `GROUP BY`, not fetch-then-
aggregate-in-Python.** §5 itself frames every one of these queries as
"a `GROUP BY` over this table, computed on demand" — this module takes
that literally. Each public function is a thin wrapper: a private
`_build_*_query()` builds a SQLAlchemy `Select` (pure, no session) and
the public function opens its own `SessionLocal()`, executes it,
shapes the result rows into a small local, frozen dataclass, and
closes the session — same session-lifecycle convention
`record_strategy_outcome()` already uses in this same package. No
caching, no persisted query results — §5's own framing is "computed on
demand"; persisting these results would silently reintroduce the
"stored rank" anti-pattern §5 opens by explicitly rejecting.

**Return shapes are local dataclasses, not new Pydantic schemas.**
`app/schemas/performance.py` is out of scope for this task (owned by
the persistence contract, not this read-side view) — `HourlyWinRate`/
`SessionTypeExpectancy` below are this module's own, matching the
"small dataclass bundle" precedent `state_snapshot.py`'s
`StrategyOutcomeSnapshots` already set, not a new cross-module schema.

**Not built here, deliberately:** no API route (a future task, would
risk colliding with the sibling parallel track's own route work on
`routes/intelligence.py`), no caching layer, no change to
`StrategyOutcomeRecord`'s schema, no "parameter sensitivity" in any
form.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Integer, Select, cast, func, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.trading_intelligence import StrategyOutcomeRecord

__all__ = [
    "HourlyWinRate",
    "SessionTypeExpectancy",
    "get_win_rate_by_hour",
    "get_expectancy_by_session_type",
]


@dataclass(frozen=True)
class HourlyWinRate:
    """One row per Eastern-time hour-of-day bucket with at least one
    matching outcome. `hour_et` is 0-23, converted from the stored UTC
    `entry_filled_at` via `Settings.market_timezone` — never a raw UTC
    hour. `win_rate` is `win_count / total_trades`; `realized_r <= 0`
    (including exactly 0) never counts toward `win_count`."""

    hour_et: int
    total_trades: int
    win_count: int
    win_rate: float


@dataclass(frozen=True)
class SessionTypeExpectancy:
    """One row per `context_at_entry->>'session_type'` value with at
    least one matching outcome. `session_type` is `None` only for the
    group of outcomes whose `context_at_entry` dict had no
    `"session_type"` key at write time — an honest, real group, not a
    dropped/excluded one. `expectancy_r` is `AVG(realized_r)` for the
    group."""

    session_type: str | None
    trade_count: int
    expectancy_r: float


def _validate_strategy_filters(strategy_name: str | None, strategy_version: str | None) -> None:
    """§3's immutable-version discipline: `strategy_version` only means
    something scoped to a specific `strategy_name` (two different
    strategies could share a version label). Raises before any query is
    built or a session is even opened — same "guard before doing any
    work" posture `record_strategy_outcome()` already uses for its own
    write-time invariant."""
    if strategy_version is not None and strategy_name is None:
        raise ValueError(
            "strategy_version filter requires strategy_name — a version string alone is not "
            "guaranteed unique across different strategies (§3's per-strategy versioning), so "
            "filtering on strategy_version without strategy_name risks silently blending two "
            "unrelated strategies' outcomes into one aggregate."
        )


def _common_filters(
    *,
    strategy_name: str | None,
    strategy_version: str | None,
    is_backtest: bool,
):
    """Shared WHERE-clause fragments for both queries below. `is_backtest`
    is always applied — a strict selector, never an optional filter —
    so neither query can be called in a way that blends live and
    backtest rows (§5/§7's hard invariant, enforced here rather than
    left to caller discipline)."""
    filters = [StrategyOutcomeRecord.is_backtest.is_(is_backtest)]
    if strategy_name is not None:
        filters.append(StrategyOutcomeRecord.strategy_name == strategy_name)
    if strategy_version is not None:
        filters.append(StrategyOutcomeRecord.strategy_version == strategy_version)
    return filters


def _build_win_rate_by_hour_query(
    *,
    strategy_name: str | None,
    strategy_version: str | None,
    is_backtest: bool,
) -> Select:
    tz_name = get_settings().market_timezone
    hour_et = cast(
        func.extract("hour", StrategyOutcomeRecord.entry_filled_at.op("AT TIME ZONE")(tz_name)),
        Integer,
    )
    filters = _common_filters(strategy_name=strategy_name, strategy_version=strategy_version, is_backtest=is_backtest)
    return (
        select(
            hour_et.label("hour_et"),
            func.count().label("total_trades"),
            func.count().filter(StrategyOutcomeRecord.realized_r > 0).label("win_count"),
        )
        .where(*filters)
        .group_by(hour_et)
        .order_by(hour_et.asc())
    )


def get_win_rate_by_hour(
    *,
    strategy_name: str | None = None,
    strategy_version: str | None = None,
    is_backtest: bool = False,
) -> list[HourlyWinRate]:
    """Win rate grouped by `entry_filled_at`'s Eastern-time hour
    (`hour_et`, 0-23). `is_backtest=False` (default) returns live rows
    only; `True` returns backtest rows only — never both. Raises
    `ValueError` if `strategy_version` is given without `strategy_name`.
    Returns `[]`, never a fabricated row, when nothing matches."""
    _validate_strategy_filters(strategy_name, strategy_version)
    stmt = _build_win_rate_by_hour_query(
        strategy_name=strategy_name, strategy_version=strategy_version, is_backtest=is_backtest
    )
    session = SessionLocal()
    try:
        rows = session.execute(stmt).all()
    finally:
        session.close()
    return [
        HourlyWinRate(
            hour_et=int(row.hour_et),
            total_trades=int(row.total_trades),
            win_count=int(row.win_count),
            # Both operands already real SQL aggregates, reduced to one row per
            # bucket — plain float division here, not bigint/bigint (would
            # truncate to 0 or 1 if done inside the SQL itself).
            win_rate=row.win_count / row.total_trades,
        )
        for row in rows
    ]


def _build_expectancy_by_session_type_query(
    *,
    strategy_name: str | None,
    strategy_version: str | None,
    is_backtest: bool,
) -> Select:
    session_type_expr = StrategyOutcomeRecord.context_at_entry["session_type"].astext
    filters = _common_filters(strategy_name=strategy_name, strategy_version=strategy_version, is_backtest=is_backtest)
    return (
        select(
            session_type_expr.label("session_type"),
            func.count().label("trade_count"),
            func.avg(StrategyOutcomeRecord.realized_r).label("expectancy_r"),
        )
        .where(*filters)
        .group_by(session_type_expr)
        .order_by(session_type_expr.asc())
    )


def get_expectancy_by_session_type(
    *,
    strategy_name: str | None = None,
    strategy_version: str | None = None,
    is_backtest: bool = False,
) -> list[SessionTypeExpectancy]:
    """Expectancy (`AVG(realized_r)`) grouped by
    `context_at_entry->>'session_type'` — Performance Intelligence's
    first concrete "expectancy by regime" query (§5), not a general
    regime framework. `is_backtest=False` (default) returns live rows
    only; `True` returns backtest rows only — never both. Raises
    `ValueError` if `strategy_version` is given without `strategy_name`.
    Returns `[]`, never a fabricated row, when nothing matches."""
    _validate_strategy_filters(strategy_name, strategy_version)
    stmt = _build_expectancy_by_session_type_query(
        strategy_name=strategy_name, strategy_version=strategy_version, is_backtest=is_backtest
    )
    session = SessionLocal()
    try:
        rows = session.execute(stmt).all()
    finally:
        session.close()
    return [
        SessionTypeExpectancy(
            session_type=row.session_type,
            trade_count=int(row.trade_count),
            expectancy_r=float(row.expectancy_r),
        )
        for row in rows
    ]
