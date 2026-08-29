"""
Pure-function tests for app/api/routes/intelligence.py's _parse_level_key
and _parse_slope_key — deliberately their own file, not folded into
test_intelligence_routes.py, since that whole module is skipped without a
reachable Postgres instance (see its own docstring) while these functions
do no I/O at all and shouldn't need a database running to be checked.

Covers the fix for the display quirk flagged (not fixed) in decision #56:
Camarilla's nine keys share the "cam_" prefix the original digit-suffix
rule was built for, but their suffixes ("pp", "r1", ...) aren't numeric,
so every one of them fell through to the flat "no period" path instead of
grouping under one "camarilla" family the way SMA/EMA group under "sma"/
"ema". Decision #66 fixes it.

Also covers _parse_slope_key (confirmed decision #85, a fix to decision
#83's own delivery gap): sma_slope()/ema_slope()'s four published keys
per period (`_slope`, `_r2`, `_slope_pct`, `_slope_angle`) had the
identical class of grouping bug as Camarilla's own, pre-#66 — each fell
through to its own flat "no period" unit instead of nesting under the
owning sma_{period}/ema_{period} entry.
"""
from __future__ import annotations

from app.api.routes.intelligence import _parse_level_key, _parse_slope_key


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


# --- _parse_slope_key (confirmed decision #85) -------------------------


def test_slope_family_keys_split_unit_period_and_field():
    # sma_slope()/ema_slope() (indicators/sma.py, indicators/ema.py,
    # confirmed decision #83) publish exactly these four suffixes per
    # period — this pins all four, not just the three the original bug
    # report named ("slope", "slope_angle", "r2"): "slope_pct" has the
    # identical grouping gap and was missing from the report entirely.
    assert _parse_slope_key("sma_9_slope") == ("sma", "9", "slope")
    assert _parse_slope_key("sma_9_r2") == ("sma", "9", "r2")
    assert _parse_slope_key("sma_9_slope_pct") == ("sma", "9", "slope_pct")
    assert _parse_slope_key("sma_9_slope_angle") == ("sma", "9", "slope_angle")
    assert _parse_slope_key("ema_20_slope") == ("ema", "20", "slope")
    assert _parse_slope_key("ema_20_r2") == ("ema", "20", "r2")
    assert _parse_slope_key("ema_20_slope_pct") == ("ema", "20", "slope_pct")
    assert _parse_slope_key("ema_20_slope_angle") == ("ema", "20", "slope_angle")


def test_slope_key_extraction_is_exact_suffix_not_prefix_confusion():
    # "_slope" reads like a prefix of "_slope_pct"/"_slope_angle", but
    # str.endswith is an exact suffix match — this pins that
    # "sma_9_slope_angle" resolves its field as "slope_angle" whole,
    # never mistakenly as "slope" plus a dropped "_angle" remainder.
    assert _parse_slope_key("sma_9_slope_angle") == ("sma", "9", "slope_angle")
    assert _parse_slope_key("sma_9_slope_pct") == ("sma", "9", "slope_pct")


def test_base_sma_ema_keys_are_not_slope_keys():
    # sma_9/ema_20 themselves must never be mistaken for one of their
    # own slope-family sub-keys — they're real levels, handled by
    # _parse_level_key, not _parse_slope_key.
    assert _parse_slope_key("sma_9") is None
    assert _parse_slope_key("ema_20") is None


def test_non_sma_ema_slope_shaped_keys_are_out_of_scope():
    # Regression/KAMA (decision #67) publish an analogous slope/r2/dist
    # family with the identical grouping gap — deliberately NOT handled
    # by this function, scoped to sma_/ema_ only per decision #85.
    assert _parse_slope_key("regression_9_slope") is None
    assert _parse_slope_key("regression_9_r2") is None
    assert _parse_slope_key("kama_9_slope") is None
    assert _parse_slope_key("cam_r1") is None
    assert _parse_slope_key("vwap") is None
