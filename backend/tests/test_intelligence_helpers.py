"""
Pure-function tests for app/api/routes/intelligence.py's _parse_level_key —
deliberately its own file, not folded into test_intelligence_routes.py,
since that whole module is skipped without a reachable Postgres instance
(see its own docstring) while this function does no I/O at all and
shouldn't need a database running to be checked.

Covers the fix for the display quirk flagged (not fixed) in decision #56:
Camarilla's nine keys share the "cam_" prefix the original digit-suffix
rule was built for, but their suffixes ("pp", "r1", ...) aren't numeric,
so every one of them fell through to the flat "no period" path instead of
grouping under one "camarilla" family the way SMA/EMA group under "sma"/
"ema". Decision #66 fixes it.
"""
from __future__ import annotations

from app.api.routes.intelligence import _parse_level_key


def test_sma_and_ema_style_keys_split_on_numeric_suffix():
    assert _parse_level_key("sma_9") == ("sma", "9")
    assert _parse_level_key("sma_20") == ("sma", "20")
    assert _parse_level_key("ema_9") == ("ema", "9")


def test_single_value_units_have_no_period():
    assert _parse_level_key("vwap") == ("vwap", None)
    assert _parse_level_key("pdh") == ("pdh", None)
    assert _parse_level_key("pdl") == ("pdl", None)
    assert _parse_level_key("pdc") == ("pdc", None)
    assert _parse_level_key("pmh") == ("pmh", None)
    assert _parse_level_key("pml") == ("pml", None)
    assert _parse_level_key("vpoc") == ("vpoc", None)


def test_all_nine_camarilla_keys_group_under_one_family():
    # indicators/camarilla.py's exact nine keys, engine.py's f"cam_{k}"
    # prefixing applied.
    expected = {
        "cam_pp": ("camarilla", "pp"),
        "cam_r1": ("camarilla", "r1"),
        "cam_r2": ("camarilla", "r2"),
        "cam_r3": ("camarilla", "r3"),
        "cam_r4": ("camarilla", "r4"),
        "cam_s1": ("camarilla", "s1"),
        "cam_s2": ("camarilla", "s2"),
        "cam_s3": ("camarilla", "s3"),
        "cam_s4": ("camarilla", "s4"),
    }
    for level_key, want in expected.items():
        assert _parse_level_key(level_key) == want


def test_camarilla_prefix_check_runs_before_the_generic_digit_rule():
    # Without the "cam_" special-case running FIRST, "cam_r1" would still
    # fail the generic digit-suffix check on its own (r1 isn't a digit) —
    # this test exists to pin the ordering/precedence, not just the
    # end result, so a future refactor that reorders the two checks can't
    # silently regress this while still passing the test above.
    unit, period = _parse_level_key("cam_r1")
    assert unit == "camarilla"
    assert period == "r1"
