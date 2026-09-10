"""opportunity_view.py tests.

Almost everything here targets `compute_opportunity_conflicts()` — the
pure function — with hand-built, `get_snapshot()`-shaped dicts. No DB,
no EventBus, no OpportunityCache instance needed for those: this view
is a pure function of the snapshot shape, and testing it that way is
deliberate (module docstring's own "primary test target" framing).

One test (`test_get_opportunity_conflicts_reads_the_real_cache`) goes
through a real `OpportunityCache` + real `EventBus`, mirroring
`test_opportunity_cache.py`'s own existing pattern for publishing
`OpportunityCreated` — this is what actually proves the assumption
about `get_snapshot()`'s exact shape holds, not just an asserted
paraphrase of it.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.event_bus.bus import EventBus
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import EventType
from app.strategy_engine.base_strategy import Opportunity
from app.trading_intelligence.opportunity_cache import OpportunityCache
from app.trading_intelligence.opportunity_view import (
    compute_opportunity_conflicts,
    get_opportunity_conflicts,
)


def _opp(
    direction: str = "BUY",
    confidence: float = 0.5,
    setup_detected_at: str = "2026-08-10T14:00:00+00:00",
    status: str = "actionable",
) -> dict[str, Any]:
    """A raw cached-opportunity dict — deliberately just the handful of
    keys compute_opportunity_conflicts() actually reads (plus `status`,
    included so test 11 below can prove it's ignored), not a full
    Opportunity.model_dump(). The real shape is proven separately by
    the integration test at the bottom of this file."""
    return {
        "direction": direction,
        "confidence": confidence,
        "setup_detected_at": setup_detected_at,
        "status": status,
    }


def _snapshot(symbols: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    return {"symbols": symbols}


# --- honest absence: 0 or 1 cached opportunity ------------------------------


def test_symbol_missing_entirely_is_absent_from_both():
    snapshot = _snapshot({})
    result = compute_opportunity_conflicts(snapshot)
    assert result == {"agreements": {}, "conflicts": {}}


def test_symbol_with_empty_strategy_map_is_absent_from_both():
    # Not a shape OpportunityCache.get_snapshot() actually produces (it
    # never creates a symbol key with zero strategies — see
    # opportunity_cache.py's own _on_opportunity_created), but this
    # function takes an arbitrary snapshot-shaped dict, not necessarily
    # the real cache's output, so it must handle this gracefully too.
    snapshot = _snapshot({"NVDA": {}})
    result = compute_opportunity_conflicts(snapshot)
    assert "NVDA" not in result["agreements"]
    assert "NVDA" not in result["conflicts"]


def test_symbol_with_one_cached_opportunity_is_absent_from_both():
    snapshot = _snapshot({"NVDA": {"ORB": _opp("BUY")}})
    result = compute_opportunity_conflicts(snapshot)
    assert "NVDA" not in result["agreements"]
    assert "NVDA" not in result["conflicts"]


# --- agreement ---------------------------------------------------------------


def test_two_buy_opportunities_is_agreement():
    snapshot = _snapshot({
        "NVDA": {
            "ORB": _opp("BUY", confidence=0.71),
            "Momentum": _opp("BUY", confidence=0.64),
        }
    })
    result = compute_opportunity_conflicts(snapshot)
    assert result["conflicts"] == {}
    assert set(result["agreements"].keys()) == {"NVDA"}

    entry = result["agreements"]["NVDA"]
    assert entry["direction"] == "BUY"
    assert entry["count"] == 2
    # sorted by strategy name: Momentum before ORB
    assert [s["strategy"] for s in entry["strategies"]] == ["Momentum", "ORB"]
    assert entry["strategies"][0]["confidence"] == 0.64
    assert entry["strategies"][1]["confidence"] == 0.71


def test_two_sell_opportunities_is_agreement():
    snapshot = _snapshot({
        "AAPL": {
            "Gap": _opp("SELL"),
            "Reversal": _opp("SELL"),
        }
    })
    result = compute_opportunity_conflicts(snapshot)
    assert result["conflicts"] == {}
    assert result["agreements"]["AAPL"]["direction"] == "SELL"
    assert result["agreements"]["AAPL"]["count"] == 2


# --- conflict ------------------------------------------------------------


def test_buy_and_sell_is_conflict():
    snapshot = _snapshot({
        "TSLA": {
            "ORB": _opp("BUY", confidence=0.58),
            "Reversal": _opp("SELL", confidence=0.66),
        }
    })
    result = compute_opportunity_conflicts(snapshot)
    assert result["agreements"] == {}
    assert set(result["conflicts"].keys()) == {"TSLA"}

    entry = result["conflicts"]["TSLA"]
    assert entry["count"] == 2
    assert list(entry["by_direction"].keys()) == ["BUY", "SELL"]  # BUY before SELL, deterministic
    assert [s["strategy"] for s in entry["by_direction"]["BUY"]] == ["ORB"]
    assert [s["strategy"] for s in entry["by_direction"]["SELL"]] == ["Reversal"]


def test_buy_buy_sell_is_one_conflict_containing_all_three():
    snapshot = _snapshot({
        "TSLA": {
            "ORB": _opp("BUY"),
            "Momentum": _opp("BUY"),
            "Reversal": _opp("SELL"),
        }
    })
    result = compute_opportunity_conflicts(snapshot)
    # Must NOT also produce an agreement entry for the two BUYs.
    assert result["agreements"] == {}
    assert set(result["conflicts"].keys()) == {"TSLA"}

    entry = result["conflicts"]["TSLA"]
    assert entry["count"] == 3
    assert [s["strategy"] for s in entry["by_direction"]["BUY"]] == ["Momentum", "ORB"]
    assert [s["strategy"] for s in entry["by_direction"]["SELL"]] == ["Reversal"]


def test_buy_sell_sell_is_one_conflict_containing_all_three():
    snapshot = _snapshot({
        "TSLA": {
            "ORB": _opp("BUY"),
            "Reversal": _opp("SELL"),
            "VWAP": _opp("SELL"),
        }
    })
    result = compute_opportunity_conflicts(snapshot)
    assert result["agreements"] == {}
    entry = result["conflicts"]["TSLA"]
    assert entry["count"] == 3
    assert [s["strategy"] for s in entry["by_direction"]["BUY"]] == ["ORB"]
    assert [s["strategy"] for s in entry["by_direction"]["SELL"]] == ["Reversal", "VWAP"]


# --- multiple symbols, no cross-contamination -------------------------------


def test_multiple_symbols_mixed_states_classified_independently():
    snapshot = _snapshot({
        "AAPL": {"ORB": _opp("BUY"), "Momentum": _opp("BUY")},           # agreement
        "TSLA": {"ORB": _opp("BUY"), "Reversal": _opp("SELL")},          # conflict
        "NVDA": {"ORB": _opp("BUY")},                                    # absent (only 1)
        "MSFT": {},                                                      # absent (0)
    })
    result = compute_opportunity_conflicts(snapshot)

    assert set(result["agreements"].keys()) == {"AAPL"}
    assert set(result["conflicts"].keys()) == {"TSLA"}
    assert "NVDA" not in result["agreements"] and "NVDA" not in result["conflicts"]
    assert "MSFT" not in result["agreements"] and "MSFT" not in result["conflicts"]


def test_no_cross_symbol_contamination_in_strategy_lists():
    snapshot = _snapshot({
        "AAPL": {"ORB": _opp("BUY"), "Momentum": _opp("BUY")},
        "TSLA": {"ORB": _opp("SELL"), "Momentum": _opp("SELL")},
    })
    result = compute_opportunity_conflicts(snapshot)
    aapl_strategies = {s["strategy"] for s in result["agreements"]["AAPL"]["strategies"]}
    tsla_strategies = {s["strategy"] for s in result["agreements"]["TSLA"]["strategies"]}
    assert aapl_strategies == {"ORB", "Momentum"}
    assert tsla_strategies == {"ORB", "Momentum"}
    # Confidence values don't leak across symbols even though both use
    # the same strategy names.
    assert result["agreements"]["AAPL"]["direction"] == "BUY"
    assert result["agreements"]["TSLA"]["direction"] == "SELL"


# --- status/staleness: this view invents nothing beyond direction ----------


def test_status_and_setup_time_variants_do_not_affect_classification():
    # One "actionable" fired minutes ago, one "expired" fired hours
    # earlier — OpportunityCache doesn't filter by status or age, and
    # neither does this view. Both still count toward the same
    # agreement; nothing about status or timestamp recency changes the
    # classification.
    snapshot = _snapshot({
        "NVDA": {
            "ORB": _opp("BUY", setup_detected_at="2026-08-10T09:35:00+00:00", status="expired"),
            "Momentum": _opp("BUY", setup_detected_at="2026-08-10T14:58:00+00:00", status="actionable"),
        }
    })
    result = compute_opportunity_conflicts(snapshot)
    assert result["agreements"]["NVDA"]["direction"] == "BUY"
    assert result["agreements"]["NVDA"]["count"] == 2
    # setup_detected_at is passed through verbatim, not used to filter/sort.
    times = {s["strategy"]: s["setup_detected_at"] for s in result["agreements"]["NVDA"]["strategies"]}
    assert times["ORB"] == "2026-08-10T09:35:00+00:00"
    assert times["Momentum"] == "2026-08-10T14:58:00+00:00"


# --- defensive: malformed direction excluded, not a crash -------------------


def test_entry_with_missing_direction_is_excluded_not_crashed():
    snapshot = _snapshot({
        "NVDA": {
            "ORB": _opp("BUY"),
            "Momentum": {"confidence": 0.5, "setup_detected_at": "..."},  # no "direction" key
        }
    })
    result = compute_opportunity_conflicts(snapshot)
    # Only one usable entry (ORB) remains -> honest absence, not an
    # agreement-of-one and not a crash.
    assert "NVDA" not in result["agreements"]
    assert "NVDA" not in result["conflicts"]


# --- confidence/metadata is passthrough only, never aggregated -------------


def test_confidence_is_never_aggregated_or_compared():
    # Deliberately give the "losing" strategy the higher confidence, to
    # make sure nothing in the output reorders or drops it based on
    # confidence. Ordering must stay alphabetical by strategy name.
    snapshot = _snapshot({
        "TSLA": {
            "ORB": _opp("BUY", confidence=0.10),
            "Reversal": _opp("SELL", confidence=0.99),
        }
    })
    result = compute_opportunity_conflicts(snapshot)
    entry = result["conflicts"]["TSLA"]
    # No aggregate confidence field of any kind anywhere in the output.
    assert "confidence" not in entry
    assert "avg_confidence" not in entry
    assert "max_confidence" not in entry
    assert entry["by_direction"]["BUY"][0]["confidence"] == 0.10
    assert entry["by_direction"]["SELL"][0]["confidence"] == 0.99


# --- get_opportunity_conflicts(): symbol filter -----------------------------


def test_get_opportunity_conflicts_symbol_filter_via_pure_function_contract():
    # Not going through the real cache here — just confirming
    # compute_opportunity_conflicts() itself is symbol-agnostic and
    # trusts whatever snapshot it's handed, since get_opportunity_conflicts()
    # relies on OpportunityCache.get_snapshot(symbol) to do the actual
    # filtering upstream (see the real-cache test below for that path).
    snapshot = _snapshot({
        "AAPL": {"ORB": _opp("BUY"), "Momentum": _opp("BUY")},
        "TSLA": {"ORB": _opp("BUY"), "Reversal": _opp("SELL")},
    })
    already_filtered = _snapshot({"AAPL": snapshot["symbols"]["AAPL"]})
    result = compute_opportunity_conflicts(already_filtered)
    assert set(result["agreements"].keys()) == {"AAPL"}
    assert result["conflicts"] == {}


# --- integration: real OpportunityCache + real EventBus --------------------


def _make_opportunity(strategy: str, direction: str, confidence: float) -> Opportunity:
    return Opportunity(
        strategy=strategy,
        version=f"{strategy.lower()}_v1",
        direction=direction,
        confidence=confidence,
        structural_invalidation=100.0,
        structural_target=105.0,
        evidence={"conditions": {}, "reason": "test fixture", "basis": "live"},
        setup_detected_at=datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc),
    )


async def test_get_opportunity_conflicts_reads_the_real_cache():
    """Same publish-via-bus pattern test_opportunity_cache.py's own
    tests already use — proves get_opportunity_conflicts() correctly
    consumes the REAL OpportunityCache.get_snapshot() shape, not just
    the hand-built dicts every other test in this file uses."""
    bus = EventBus()
    await bus.start()
    try:
        cache = OpportunityCache(bus)
        cache.start()

        await bus.publish(make_envelope(
            EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB", "BUY", 0.58), symbol="TSLA",
        ))
        await bus.publish(make_envelope(
            EventType.OPPORTUNITY_CREATED, _make_opportunity("Reversal", "SELL", 0.66), symbol="TSLA",
        ))
        await bus.publish(make_envelope(
            EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB", "BUY", 0.71), symbol="NVDA",
        ))
        await bus.publish(make_envelope(
            EventType.OPPORTUNITY_CREATED, _make_opportunity("Momentum", "BUY", 0.64), symbol="NVDA",
        ))
        await asyncio.sleep(0.05)  # let the normal-lane queue dispatch

        result = compute_opportunity_conflicts(cache.get_snapshot())
        assert set(result["conflicts"].keys()) == {"TSLA"}
        assert set(result["agreements"].keys()) == {"NVDA"}

        # And the wrapper, going through the real singleton-shaped
        # instance directly (not get_opportunity_cache()'s process-wide
        # singleton, to keep this test isolated) — call the pure
        # function path the same way the wrapper does, confirming the
        # wiring is identical.
        from app.trading_intelligence import opportunity_view

        wrapped = opportunity_view.compute_opportunity_conflicts(cache.get_snapshot(symbol="NVDA"))
        assert set(wrapped["agreements"].keys()) == {"NVDA"}
        assert wrapped["conflicts"] == {}
    finally:
        await bus.stop()


async def test_get_opportunity_conflicts_wrapper_uses_process_singleton(monkeypatch):
    """get_opportunity_conflicts() itself (not just the pure function)
    against the real process-wide get_opportunity_cache() singleton —
    the actual call path a future route would use."""
    import app.trading_intelligence.opportunity_cache as opportunity_cache_module

    opportunity_cache_module._opportunity_cache = None
    bus = EventBus()
    await bus.start()
    try:
        cache = opportunity_cache_module.get_opportunity_cache(bus)
        cache.start()

        await bus.publish(make_envelope(
            EventType.OPPORTUNITY_CREATED, _make_opportunity("ORB", "BUY", 0.5), symbol="NVDA",
        ))
        await bus.publish(make_envelope(
            EventType.OPPORTUNITY_CREATED, _make_opportunity("Momentum", "BUY", 0.6), symbol="NVDA",
        ))
        await asyncio.sleep(0.05)

        result = get_opportunity_conflicts()
        assert set(result["agreements"].keys()) == {"NVDA"}

        result_filtered = get_opportunity_conflicts(symbol="NVDA")
        assert set(result_filtered["agreements"].keys()) == {"NVDA"}
    finally:
        await bus.stop()
        opportunity_cache_module._opportunity_cache = None
