"""
Monthly partition auto-creation for the `candles` table (see
app/models/market_data.py and alembic/versions/0001_initial_symbols_and_
candles.py) — closes the TODO that first migration left explicitly open:
"automate future-partition creation... before this becomes a real
operational gap — i.e. before Market Data Engine is actually writing
candles". CandleRecorder (app/services/candle_recorder.py) is that
trigger arriving.

`candles` is RANGE-partitioned by candle_ts with no default/catch-all
partition (deliberately, per that same migration — an unbounded catch-all
partition defeats the point of range partitioning for query pruning).
Every INSERT therefore needs its target month's partition to already
exist, or Postgres rejects it outright with "no partition of relation
candles found for row". The original migration only seeded July/August
2026 — this app is still running past that today, so without this,
writes would start failing the moment September arrives. Called before
every write (see ensure_month_partition's own docstring) rather than run
as a separate scheduled job, since a scheduled job is one more process to
keep alive and monitor for what's a genuinely cheap, idempotent check.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

# Tracks which (year, month) partitions this PROCESS has already confirmed
# exist — CREATE TABLE IF NOT EXISTS is already idempotent and cheap on its
# own, but this avoids a round-trip DDL statement on every single candle
# write once a given month is known-good. Process-lifetime only, not
# persisted: a fresh process re-checks once per month it touches, which is
# correct and harmless either way (IF NOT EXISTS covers it regardless).
_ensured_this_process: set[tuple[int, int]] = set()


def _partition_name(year: int, month: int) -> str:
    return f"candles_y{year:04d}m{month:02d}"


def _partition_bounds(year: int, month: int) -> tuple[str, str]:
    # Explicit +00 (UTC) offset, not a bare date — matching the original
    # migration's own note: a bare date literal is interpreted using the
    # DB session's local TimeZone setting at the moment the DDL runs,
    # which would make the exact same call produce a different physical
    # partition boundary depending on where/when it's invoked.
    start = f"{year:04d}-{month:02d}-01 00:00:00+00"
    if month == 12:
        end = f"{year + 1:04d}-01-01 00:00:00+00"
    else:
        end = f"{year:04d}-{month + 1:02d}-01 00:00:00+00"
    return start, end


def _months_to_ensure(for_date: date) -> list[tuple[int, int]]:
    # This month AND next — so a write landing right at a month boundary
    # is never the first thing to discover the next partition doesn't
    # exist yet. Since this runs on every candle close, "next month" ends
    # up created well before the boundary is ever actually crossed in
    # practice, not just-in-time at 00:00:00 on the day itself.
    year, month = for_date.year, for_date.month
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return [(year, month), (next_year, next_month)]


def ensure_month_partition(session: Session, for_date: date) -> None:
    """Idempotent — safe to call before every write."""
    for year, month in _months_to_ensure(for_date):
        if (year, month) in _ensured_this_process:
            continue
        start, end = _partition_bounds(year, month)
        session.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {_partition_name(year, month)} "
                f"PARTITION OF candles FOR VALUES FROM ('{start}') TO ('{end}')"
            )
        )
        session.commit()
        _ensured_this_process.add((year, month))
