"""
Daily Levels — support/resistance zones from clustering 1D candle open/
close prices. Confirmed decision #59 (direction lock) and
docs/architecture/daily-levels-design.md §1 for the full design history,
including an arithmetic error caught in an early clustering proposal
before it shipped: that proposal tested each candidate point against the
cluster's STALE running average, which does NOT actually reproduce the
worked example it was checked against (100.10/100.20/100.40 at 0.2%
tolerance) — re-deriving the arithmetic directly showed the third point
fails that literal rule. Corrected rule, used here: validate the WHOLE
tentative cluster (old members plus the candidate) against the average it
would produce if the candidate were accepted, requiring every member —
not just the newest — to pass. Re-run against the same example, this
correctly produces the intended 3-point, ~100.23 level.

No bias/relaxation mechanism for a point that fails to join a cluster
(design doc §1.2, resolved explicitly on Saqib's own direct question): a
rejected candidate becomes the seed of the NEXT cluster attempt later in
this same sorted pass — a fair, full shot at a different partner — but
never gets a second, looser test against the cluster that just rejected
it. A point surviving both with no partner anywhere in the lookback
window has no confirming price point in the data, which is the correct
signal to discard it, not a gap to patch.

Pure math only — no I/O, no provider awareness, same purity goal as every
other file in this package (see indicators/__init__.py's module
docstring). engine.py owns fetching the 1D candle history — the first
Feature Engine indicator with a genuine external-provider dependency,
since market.py's own "1d" routing has always gone straight to whichever
provider holds the historical role, never self-recorded (decision #44) —
and hands this function plain (candle_index, price) points, not any
provider-specific Candle type, so this module stays decoupled from
app.broker_adapters entirely.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple


class DailyCandlePoint(NamedTuple):
    """One point contributed by a 1D candle — its open OR its close, not
    both fields at once (a candle contributes two of these). candle_index
    ties a point back to the candle it came from, in chronological order,
    purely so §1.1's same-candle validity check can count DISTINCT
    candles in a cluster without this module needing to know anything
    about when that candle actually was — engine.py assigns the indices
    before calling in."""

    candle_index: int
    price: float


@dataclass(frozen=True)
class ClusteredLevel:
    price: float  # the cluster's average — this level's current price
    strength: int  # total contributing points (opens + closes combined)
    distinct_candle_count: int  # §1.1's validity metric — must be >= min_distinct_candles


def cluster_daily_levels(
    points: list[DailyCandlePoint],
    cluster_pct: float,
    min_distinct_candles: int = 2,
) -> list[ClusteredLevel]:
    """
    The corrected greedy-cluster-consume algorithm (design doc §1):

      1. Sort points by price ascending.
      2. Seed a cluster with the lowest unused point.
      3. Grow it by testing the next unused point, in ascending order:
         would accepting it produce a new cluster average that EVERY
         member (old and new) is still within cluster_pct of? If yes,
         accept and keep growing; if no, STOP growing this cluster — the
         rejected point is left unused and becomes the seed of the next
         cluster attempt later in this same pass (§1.2 — not a retry with
         different rules, just the pass continuing).
      4. A finished cluster is a valid level only if it has >= 2 points
         AND distinct_candle_count >= min_distinct_candles (§1.1) —
         otherwise discarded, same as a true singleton. A same-candle
         open/close pair can still contribute to a larger cluster's
         strength once a second candle's point has also joined it; it
         just can't validate a cluster on its own.

    Deterministic given the same input points and settings — sorted
    order removes any dependence on the original (pre-sort) ordering of
    `points`, and there is no randomness anywhere in this function.

    Deliberately stops growing a cluster at the FIRST point that fails
    the whole-cluster test (a `break`, not a scan past it for a
    later point that might still fit) — matches the sequential
    "grow while adjacent points qualify" shape the algorithm was
    designed and confirmed against, not a global search for the best
    possible grouping. A locally-greedy result, not a globally optimal
    one; accepted as a known characteristic, not something to fix here.
    """
    ordered = sorted(points, key=lambda p: p.price)
    used = [False] * len(ordered)
    levels: list[ClusteredLevel] = []

    for i in range(len(ordered)):
        if used[i]:
            continue
        cluster = [ordered[i]]
        used[i] = True

        for j in range(i + 1, len(ordered)):
            if used[j]:
                continue
            candidate = cluster + [ordered[j]]
            new_avg = sum(p.price for p in candidate) / len(candidate)
            tolerance = new_avg * cluster_pct
            if all(abs(p.price - new_avg) <= tolerance for p in candidate):
                cluster = candidate
                used[j] = True
            else:
                break  # stop growing THIS cluster — j stays unused, becomes a future seed

        distinct_candles = len({p.candle_index for p in cluster})
        if len(cluster) >= 2 and distinct_candles >= min_distinct_candles:
            avg = sum(p.price for p in cluster) / len(cluster)
            levels.append(
                ClusteredLevel(price=round(avg, 6), strength=len(cluster), distinct_candle_count=distinct_candles)
            )

    return levels
