"""C2: Scheduler completed-evaluation batches (StrategyEvaluationCompleted).

Pure tier: fake bus, fake ContextEngine, stub strategies, injected clocks — no
database. Covers every disposition, error isolation, "untriggered is not
evaluated", prerequisite availability/coherence, source-interval derivation
(including session-trailing buckets), distinct timestamps, identical
re-evaluation, and that the legacy OpportunityCreated stream is unchanged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.market_clock import MarketClock
from app.event_bus.events import make_envelope
from app.schemas.events.envelope import CRITICAL_EVENT_TYPES, EventType
from app.schemas.events.strategy_evaluation import StrategyEvaluationCompleted
from app.strategy_engine.base_strategy import Opportunity, ScheduleTrigger, StrategyConfig
from app.strategy_engine.scheduler import StrategyScheduler
from app.trading_intelligence.candidate_batch_wire import (
    SourceIntervalUnavailable,
    batch_to_payload,
    derive_source_interval,
    opportunity_content_from,
    payload_to_batch,
)
from app.trading_intelligence.candidate_contract import CandidateContractError
from tests.test_strategy_scheduler import (
    _TS,
    _FakeBus,
    _features_updated_envelope,
    _install_fake_context,
    _make_opportunity,
    _market_state_changed_envelope,
    _StubStrategy,
    _stub_with_gate_conditions,
)

ET = ZoneInfo("America/New_York")
COMPLETED = datetime(2026, 8, 10, 14, 1, 3, tzinfo=timezone.utc)


def batches(bus: _FakeBus) -> list:
    return [e for e in bus.published if e.event_type == EventType.STRATEGY_EVALUATION_COMPLETED]


def parsed(bus: _FakeBus, index: int = -1) -> StrategyEvaluationCompleted:
    return StrategyEvaluationCompleted.model_validate(batches(bus)[index].payload)


def make_scheduler(bus, strategies, now=lambda: COMPLETED, **kw) -> StrategyScheduler:
    return StrategyScheduler(bus, strategies=strategies, now=now, **kw)


async def trigger(scheduler: StrategyScheduler, symbol: str = "AAPL", ts: datetime = _TS, **ms) -> None:
    await scheduler._on_features_updated(_features_updated_envelope(symbol, candle_ts=ts))
    await scheduler._on_market_state_changed(_market_state_changed_envelope(symbol, candle_ts=ts, **ms))


# --- vocabulary ---------------------------------------------------------------


def test_event_type_is_on_the_normal_lane():
    assert EventType.STRATEGY_EVALUATION_COMPLETED.value == "StrategyEvaluationCompleted"
    assert EventType.STRATEGY_EVALUATION_COMPLETED not in CRITICAL_EVENT_TYPES
    envelope = make_envelope(
        EventType.STRATEGY_EVALUATION_COMPLETED,
        StrategyEvaluationCompleted(
            timeframe="1m", mode="simulated", source_candle_ts=_TS, source_interval_start=_TS,
            source_interval_close=_TS + timedelta(minutes=1), completed_at=COMPLETED,
            unavailable_prerequisites=[{"name": "features", "reason": "features_unavailable"}],
        ),
        symbol="AAPL",
    )
    assert envelope.is_critical is False


# --- dispositions -------------------------------------------------------------


async def test_every_disposition_kind_in_one_batch_published_after_the_pass(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    gated = _stub_with_gate_conditions("Gated", {"session": "regular"})
    outside = datetime(2026, 8, 10, 7, 0, tzinfo=ET)  # pre-market: Gated must be skipped
    strategies = [
        _StubStrategy("Fires", result=_make_opportunity("Fires")),
        _StubStrategy("Nothing"),
        _StubStrategy("Boom", raises=True),
        gated,
    ]
    scheduler = make_scheduler(bus, strategies)
    await trigger(scheduler, ts=outside)

    batch = parsed(bus)
    by_name = {d.strategy: d for d in batch.dispositions}
    assert {n: d.kind for n, d in by_name.items()} == {
        "Fires": "opportunity", "Nothing": "no_opportunity", "Boom": "error", "Gated": "gated",
    }
    assert by_name["Boom"].reason == "evaluate_failed"          # machine code, never exception text
    assert "deliberately" not in batches(bus)[0].model_dump_json()
    assert by_name["Gated"].reason == "gate_conditions_not_satisfied"
    assert by_name["Fires"].opportunity.direction == "long"      # BUY -> long
    assert by_name["Fires"].opportunity.evidence == {"conditions": {}, "reason": "stub", "basis": "live"}
    assert batch.unavailable_prerequisites == []
    # Exactly one batch, and it is the LAST event of the pass.
    assert len(batches(bus)) == 1
    assert bus.published[-1].event_type == EventType.STRATEGY_EVALUATION_COMPLETED
    # Strategies other than the failing one still finished (legacy isolation).
    assert [e.payload["strategy"] for e in bus.opportunity_events] == ["Fires"]


async def test_sell_maps_to_short_and_status_and_horizon_are_carried(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    opp = _make_opportunity("S", direction="SELL").model_copy(
        update={"status": "waiting", "expected_horizon_minutes": 45}
    )
    await trigger(make_scheduler(bus, [_StubStrategy("S", result=opp)]))
    content = parsed(bus).dispositions[0].opportunity
    assert (content.direction, content.status, content.expected_horizon_minutes) == ("short", "waiting", 45)


async def test_gate_check_exception_is_an_error_disposition_and_others_finish(monkeypatch):
    _install_fake_context(monkeypatch)
    import app.strategy_engine.scheduler as scheduler_module

    def explode(*_a, **_k):
        raise RuntimeError("gate bug")

    bus = _FakeBus()
    gated = _stub_with_gate_conditions("Gated", {"session": "regular"})
    ungated = _StubStrategy("Ungated", result=_make_opportunity("Ungated"))
    scheduler = make_scheduler(bus, [gated, ungated])
    # Only the gated strategy consults the gate; make that one explode.
    monkeypatch.setattr(
        scheduler_module, "gate_conditions_satisfied",
        lambda gate, ts: explode() if gate else True,
    )
    await trigger(scheduler)
    kinds = {d.strategy: (d.kind, d.reason) for d in parsed(bus).dispositions}
    assert kinds == {"Gated": ("error", "gate_check_failed"), "Ungated": ("opportunity", None)}


async def test_untriggered_strategy_never_appears_as_evaluated(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    five = _StubStrategy("FiveMinute", trigger=ScheduleTrigger(kind="every_candle", timeframe="5m"))
    event_driven = _StubStrategy("OnEvent", trigger=ScheduleTrigger(kind="on_event", event_name="X"))
    one = _StubStrategy("OneMinute")
    await trigger(make_scheduler(bus, [five, event_driven, one]))
    assert [d.strategy for d in parsed(bus).dispositions] == ["OneMinute"]
    assert five.calls == [] and event_driven.calls == []


async def test_opportunity_with_invalid_contents_is_an_error_disposition_but_legacy_event_still_published(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    bad = _make_opportunity("Bad").model_copy(update={"evidence": {"when": datetime(2026, 1, 1)}})  # not plain JSON
    wrong_version = _make_opportunity("Mismatch").model_copy(update={"version": "other_v9"})
    await trigger(make_scheduler(bus, [_StubStrategy("Bad", result=bad), _StubStrategy("Mismatch", result=wrong_version)]))
    kinds = {d.strategy: (d.kind, d.reason) for d in parsed(bus).dispositions}
    assert kinds == {"Bad": ("error", "opportunity_content_invalid"), "Mismatch": ("error", "opportunity_content_invalid")}
    assert len(bus.opportunity_events) == 2  # existing consumers unaffected


async def test_legacy_opportunity_created_payloads_are_unchanged(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    opp = _make_opportunity("A")
    await trigger(make_scheduler(bus, [_StubStrategy("A", result=opp)]))
    assert bus.opportunity_events[0].payload == opp.model_dump(mode="json")
    assert bus.opportunity_events[0].symbol == "AAPL"


async def test_batch_for_candle_where_no_strategy_is_watching_is_not_published(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    scheduler = make_scheduler(bus, [_StubStrategy("Five", trigger=ScheduleTrigger(kind="every_candle", timeframe="5m"))])
    await trigger(scheduler)
    assert bus.published == []


# --- prerequisites: availability and coherence ------------------------------------


async def test_missing_features_publishes_an_unavailable_batch_and_evaluates_nothing(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    scheduler = make_scheduler(bus, [stub])
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))
    batch = parsed(bus)
    assert batch.dispositions == []
    assert [(p.name, p.reason) for p in batch.unavailable_prerequisites] == [("features", "features_unavailable")]
    assert stub.calls == [] and bus.opportunity_events == []


async def test_missing_context_publishes_an_unavailable_batch(monkeypatch):
    _install_fake_context(monkeypatch, present=False)
    bus = _FakeBus()
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    await trigger(make_scheduler(bus, [stub]))
    assert [(p.name, p.reason) for p in parsed(bus).unavailable_prerequisites] == [("context", "context_unavailable")]
    assert stub.calls == [] and bus.opportunity_events == []


async def test_features_for_a_newer_candle_are_never_mixed_with_an_older_market_state(monkeypatch):
    """The real ordering hazard: the debounced MarketStateChanged for candle N
    arrives after FeaturesUpdated for N+1 replaced the single-slot cache."""
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    scheduler = make_scheduler(bus, [stub])
    newer = _TS + timedelta(minutes=1)
    await scheduler._on_features_updated(_features_updated_envelope("AAPL", candle_ts=newer))
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL", candle_ts=_TS))
    batch = parsed(bus)
    assert batch.source_candle_ts == _TS
    assert [(p.name, p.reason) for p in batch.unavailable_prerequisites] == [("features", "candle_ts_mismatch")]
    assert stub.calls == [] and bus.opportunity_events == []


async def test_timeframe_mismatch_between_cached_features_and_state_is_unavailable(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    scheduler = make_scheduler(bus, [stub])
    await trigger(scheduler)
    # Corrupt the cache slot so its own timeframe disagrees with its key.
    cached = scheduler._latest_features[("AAPL", "1m")]
    scheduler._latest_features[("AAPL", "1m")] = cached.model_copy(update={"timeframe": "5m"})
    bus.published.clear()
    stub.calls.clear()
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))
    assert [(p.name, p.reason) for p in parsed(bus).unavailable_prerequisites] == [("features", "timeframe_mismatch")]
    assert stub.calls == []


async def test_naive_and_aware_equal_instants_are_coherent(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    stub = _StubStrategy("A")
    scheduler = make_scheduler(bus, [stub])
    await scheduler._on_features_updated(_features_updated_envelope("AAPL", candle_ts=_TS.replace(tzinfo=None)))
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))
    assert len(stub.calls) == 1 and parsed(bus).unavailable_prerequisites == []


async def test_both_prerequisites_reported_together_sorted_by_the_contract(monkeypatch):
    _install_fake_context(monkeypatch, present=False)
    bus = _FakeBus()
    await make_scheduler(bus, [_StubStrategy("A")])._on_market_state_changed(_market_state_changed_envelope("AAPL"))
    assert [p.name for p in parsed(bus).unavailable_prerequisites] == ["context", "features"]


# --- source interval and the three distinct timestamps --------------------------------


async def test_batch_timestamps_are_distinct_facts(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    await trigger(make_scheduler(bus, [_StubStrategy("A")]))
    batch = parsed(bus)
    assert batch.mode == "simulated" and batch.timeframe == "1m"
    assert batch.source_candle_ts == _TS
    assert batch.source_interval_start == _TS
    assert batch.source_interval_close == _TS + timedelta(minutes=1)
    assert batch.completed_at == COMPLETED
    envelope = batches(bus)[0]
    assert envelope.symbol == "AAPL"
    assert len({batch.source_candle_ts, batch.source_interval_close, batch.completed_at}) == 3


async def test_no_interval_means_no_batch_and_unchanged_legacy_behavior(monkeypatch, caplog):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    closed = datetime(2026, 8, 9, 14, 0, tzinfo=timezone.utc)  # a Sunday
    with caplog.at_level(logging.WARNING):
        await trigger(make_scheduler(bus, [stub]), ts=closed)
    assert batches(bus) == []
    assert len(bus.opportunity_events) == 1 and len(stub.calls) == 1
    assert "no_session_bounds" in caplog.text


def test_interval_for_1m_and_5m_and_aligned_hour():
    clock = MarketClock()
    open_ = datetime(2026, 8, 10, 9, 30, tzinfo=ET)
    assert derive_source_interval("1m", open_, clock) == (
        open_.astimezone(timezone.utc), (open_ + timedelta(minutes=1)).astimezone(timezone.utc))
    assert derive_source_interval("5m", open_ + timedelta(minutes=5), clock)[1] == (
        open_ + timedelta(minutes=10)).astimezone(timezone.utc)
    # 1h buckets are anchored at the session start (9:30), not on the clock hour.
    assert derive_source_interval("1h", open_, clock)[1] == (open_ + timedelta(minutes=60)).astimezone(timezone.utc)


def test_session_trailing_hour_bucket_closes_at_the_session_close_not_a_full_hour_later():
    clock = MarketClock()
    trailing_open = datetime(2026, 8, 10, 15, 30, tzinfo=ET)  # regular 9:30 + 6h = 15:30; 30-minute stub
    start, close = derive_source_interval("1h", trailing_open, clock)
    assert start == trailing_open.astimezone(timezone.utc)
    assert close == datetime(2026, 8, 10, 16, 0, tzinfo=ET).astimezone(timezone.utc)
    assert close - start == timedelta(minutes=30)


def test_half_day_session_close_bounds_the_interval():
    clock = MarketClock()
    last_1m = datetime(2026, 11, 27, 12, 59, tzinfo=ET)  # day after Thanksgiving, closes 13:00 ET
    assert derive_source_interval("1m", last_1m, clock)[1] == datetime(2026, 11, 27, 13, 0, tzinfo=ET).astimezone(timezone.utc)
    stub_hour = datetime(2026, 11, 27, 12, 30, tzinfo=ET)  # buckets 9:30, 10:30, 11:30, 12:30
    assert derive_source_interval("1h", stub_hour, clock)[1] == datetime(2026, 11, 27, 13, 0, tzinfo=ET).astimezone(timezone.utc)


def test_premarket_and_after_hours_use_their_own_session_bounds():
    clock = MarketClock()
    pm = datetime(2026, 8, 10, 9, 29, tzinfo=ET)
    assert derive_source_interval("1m", pm, clock)[1] == datetime(2026, 8, 10, 9, 30, tzinfo=ET).astimezone(timezone.utc)
    ah_hour_stub = datetime(2026, 8, 10, 19, 0, tzinfo=ET)  # after-hours 16:00-20:00 -> 16,17,18,19 buckets
    assert derive_source_interval("1h", ah_hour_stub, clock)[1] == datetime(2026, 8, 10, 20, 0, tzinfo=ET).astimezone(timezone.utc)


@pytest.mark.parametrize(
    ("timeframe", "ts", "reason"),
    [
        ("1d", datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc), "unsupported_timeframe"),
        ("1m", datetime(2026, 8, 9, 14, 0, tzinfo=timezone.utc), "no_session_bounds"),
        ("5m", datetime(2026, 8, 10, 9, 32, tzinfo=ET), "candle_not_bucket_aligned"),
        ("1m", datetime(2029, 8, 10, 14, 0, tzinfo=timezone.utc), "market_calendar_unverified"),
    ],
)
def test_interval_unavailable_reasons(timeframe, ts, reason):
    with pytest.raises(SourceIntervalUnavailable) as caught:
        derive_source_interval(timeframe, ts, MarketClock())
    assert caught.value.reason == reason


# --- identical re-evaluation, conflicting re-evaluation -----------------------------------


async def test_identical_reevaluation_republishes_the_original_batch_completion_time(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    times = iter([COMPLETED, COMPLETED + timedelta(seconds=30), COMPLETED + timedelta(seconds=60)])
    scheduler = make_scheduler(bus, [_StubStrategy("A", result=_make_opportunity("A"))], now=lambda: next(times))
    await trigger(scheduler)
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))
    first, second = parsed(bus, 0), parsed(bus, 1)
    assert first == second and second.completed_at == COMPLETED  # not recomputed to look new


async def test_changed_reevaluation_gets_a_new_completion_time_and_is_not_relabelled(monkeypatch):
    _install_fake_context(monkeypatch)
    bus = _FakeBus()
    stub = _StubStrategy("A", result=_make_opportunity("A"))
    times = iter([COMPLETED, COMPLETED + timedelta(seconds=30)])
    scheduler = make_scheduler(bus, [stub], now=lambda: next(times))
    await trigger(scheduler)
    stub._result = _make_opportunity("A").model_copy(update={"confidence": 0.1})
    await scheduler._on_market_state_changed(_market_state_changed_envelope("AAPL"))
    first, second = parsed(bus, 0), parsed(bus, 1)
    assert second.completed_at == COMPLETED + timedelta(seconds=30)
    assert first.dispositions[0].opportunity.confidence != second.dispositions[0].opportunity.confidence


async def test_publishing_failure_does_not_disturb_evaluation_or_legacy_events(monkeypatch, caplog):
    _install_fake_context(monkeypatch)

    class FlakyBus(_FakeBus):
        async def publish(self, envelope):
            if envelope.event_type == EventType.STRATEGY_EVALUATION_COMPLETED:
                raise RuntimeError("bus refused")
            await super().publish(envelope)

    bus = FlakyBus()
    with caplog.at_level(logging.ERROR):
        await trigger(make_scheduler(bus, [_StubStrategy("A", result=_make_opportunity("A"))]))
    assert len(bus.opportunity_events) == 1
    assert "failed to publish StrategyEvaluationCompleted" in caplog.text


# --- wire round trip -------------------------------------------------------------------


def test_wire_round_trip_preserves_the_c1_batch():
    from tests.test_opportunity_candidate_contract import make_batch, opportunity_disposition

    batch = make_batch(dispositions=[opportunity_disposition("orb", "1", "short"), opportunity_disposition("vwap", "2")])
    again = payload_to_batch("AAPL", batch_to_payload(batch))
    assert again == batch


def test_payload_the_contract_refuses_raises_candidate_error():
    payload = StrategyEvaluationCompleted(
        timeframe="1m", mode="simulated", source_candle_ts=_TS, source_interval_start=_TS,
        source_interval_close=_TS + timedelta(minutes=1), completed_at=COMPLETED,
    )  # neither dispositions nor unavailable prerequisites
    with pytest.raises(CandidateContractError):
        payload_to_batch("AAPL", payload)


def test_opportunity_content_refuses_unknown_direction():
    opp = Opportunity.model_construct(**{**_make_opportunity("A").model_dump(), "direction": "HOLD"})
    with pytest.raises(CandidateContractError):
        opportunity_content_from(opp, "A", "stub_v1")
