"""
level_touch_tracking.observe_resolution() tests — pure, no DB, no event
loop. Every scenario in `level_touch_tracking.py`'s own module docstring
gets a dedicated test: normal reject/conquer, gap-through (both
directions), cold-start-unknown-origin (both flavors), day rollover, and
the "entry missing / no transition" no-ops.
"""
from __future__ import annotations

from app.strategy_engine.level_touch_tracking import LevelTouchState, observe_resolution


def _entry(zone: str, trading_day: str = "2026-08-10", holding: dict | None = None) -> dict:
    entry: dict = {"zone": zone, "trading_day": trading_day, "touch_count_today": 1}
    if holding is not None:
        entry["holding"] = holding
    return entry


def test_missing_entry_returns_all_none_and_does_not_touch_state():
    state = LevelTouchState()
    resolution, zone, anchor = observe_resolution(state, None)
    assert (resolution, zone, anchor) == (None, None, None)
    assert state == LevelTouchState()  # completely untouched


def test_first_observation_steady_zone_records_last_zone_no_resolution():
    state = LevelTouchState()
    resolution, zone, anchor = observe_resolution(state, _entry("above"))
    assert (resolution, zone, anchor) == (None, "above", None)
    assert state.last_zone == "above"
    assert state.entered_from is None


def test_touch_starts_records_entered_from_and_anchor_no_resolution_yet():
    state = LevelTouchState(last_zone="below", trading_day=None)
    # Prime trading_day via a first call so the second call is same-day.
    observe_resolution(state, _entry("below", trading_day="2026-08-10"))
    resolution, zone, anchor = observe_resolution(
        state, _entry("inside_aura", trading_day="2026-08-10", holding={"anchor_price": 100.0, "entered_from": "below"})
    )
    assert (resolution, zone, anchor) == (None, "inside_aura", None)
    assert state.entered_from == "below"
    assert state.anchor_price == 100.0


def test_normal_rejection_resolves_back_out_the_side_it_entered():
    state = LevelTouchState()
    observe_resolution(state, _entry("below"))  # steady
    observe_resolution(state, _entry("inside_aura", holding={"anchor_price": 100.0, "entered_from": "below"}))  # touch starts
    resolution, zone, anchor = observe_resolution(state, _entry("below"))  # bounces back to the side it came from
    assert resolution == "rejected"
    assert zone == "below"
    assert anchor == 100.0
    # consumed — a further steady candle doesn't re-report anything
    resolution2, _, anchor2 = observe_resolution(state, _entry("below"))
    assert resolution2 is None
    assert anchor2 is None
    assert state.entered_from is None
    assert state.anchor_price is None


def test_normal_conquest_resolves_out_the_opposite_side():
    state = LevelTouchState()
    observe_resolution(state, _entry("below"))
    observe_resolution(state, _entry("inside_aura", holding={"anchor_price": 100.0, "entered_from": "below"}))
    resolution, zone, anchor = observe_resolution(state, _entry("above"))  # breaks through to the opposite side
    assert resolution == "conquered"
    assert zone == "above"
    assert anchor == 100.0


def test_gap_through_below_to_above_is_unconditionally_conquered_with_no_anchor():
    state = LevelTouchState()
    observe_resolution(state, _entry("below"))
    resolution, zone, anchor = observe_resolution(state, _entry("above"))  # jumped straight over, inside_aura never seen
    assert resolution == "conquered"
    assert zone == "above"
    assert anchor is None  # no holding entry ever existed for this transition


def test_gap_through_above_to_below_is_also_unconditionally_conquered():
    state = LevelTouchState()
    observe_resolution(state, _entry("above"))
    resolution, zone, anchor = observe_resolution(state, _entry("below"))
    assert resolution == "conquered"
    assert zone == "below"
    assert anchor is None


def test_cold_start_process_first_observation_already_inside_aura_with_known_engine_origin():
    """This PROCESS never saw the touch begin, but the ENGINE's own
    record already has a valid entered_from — bootstrapped, not
    discarded, since the engine's value is authoritative regardless of
    when this process started watching."""
    state = LevelTouchState()
    resolution, zone, anchor = observe_resolution(
        state, _entry("inside_aura", holding={"anchor_price": 50.0, "entered_from": "above"})
    )
    assert (resolution, zone, anchor) == (None, "inside_aura", None)
    assert state.entered_from == "above"
    assert state.anchor_price == 50.0
    # resolves normally from here since entered_from was successfully bootstrapped
    resolution2, _, anchor2 = observe_resolution(state, _entry("above"))
    assert resolution2 == "rejected"
    assert anchor2 == 50.0


def test_cold_start_unknown_origin_even_to_the_engine_never_classified():
    """entered_from is None even in the engine's own record (its own
    first-ever observation of this level was already inside the aura,
    decision #46) — must never guess a direction."""
    state = LevelTouchState()
    observe_resolution(state, _entry("inside_aura", holding={"anchor_price": 50.0, "entered_from": None}))
    resolution, zone, anchor = observe_resolution(state, _entry("above"))
    assert resolution is None  # honestly unclassifiable, not a guessed "rejected" or "conquered"
    assert zone == "above"
    assert anchor is None


def test_no_transition_steady_state_reports_nothing():
    state = LevelTouchState()
    observe_resolution(state, _entry("above"))
    resolution, zone, anchor = observe_resolution(state, _entry("above"))
    assert (resolution, zone, anchor) == (None, "above", None)


def test_no_transition_while_still_holding_reports_nothing():
    state = LevelTouchState()
    observe_resolution(state, _entry("below"))
    observe_resolution(state, _entry("inside_aura", holding={"anchor_price": 10.0, "entered_from": "below"}))
    resolution, zone, anchor = observe_resolution(state, _entry("inside_aura", holding={"anchor_price": 10.0, "entered_from": "below"}))
    assert (resolution, zone, anchor) == (None, "inside_aura", None)
    # still watching — nothing consumed
    assert state.entered_from == "below"
    assert state.anchor_price == 10.0


def test_day_rollover_resets_last_zone_entered_from_and_anchor():
    state = LevelTouchState()
    observe_resolution(state, _entry("below", trading_day="2026-08-10"))
    observe_resolution(
        state, _entry("inside_aura", trading_day="2026-08-10", holding={"anchor_price": 10.0, "entered_from": "below"})
    )
    # New trading day arrives mid-"watch" — yesterday's in-progress touch
    # is not a meaningful comparison point for today.
    resolution, zone, anchor = observe_resolution(state, _entry("above", trading_day="2026-08-11"))
    assert resolution is None  # first observation of the new day — nothing to resolve against
    assert zone == "above"
    assert anchor is None
    assert state.trading_day.isoformat() == "2026-08-11"
    assert state.entered_from is None


def test_day_rollover_then_gap_through_next_day_still_detected_correctly():
    state = LevelTouchState()
    observe_resolution(state, _entry("below", trading_day="2026-08-10"))
    observe_resolution(state, _entry("above", trading_day="2026-08-11"))  # day 2's first observation, steady, no resolution
    resolution, zone, anchor = observe_resolution(state, _entry("below", trading_day="2026-08-11"))  # real gap-through, same day 2
    assert resolution == "conquered"
    assert zone == "below"
    assert anchor is None
