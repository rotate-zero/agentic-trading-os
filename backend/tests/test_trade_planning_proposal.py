"""Pure proposal serialization boundary; real plan_entry outputs, no Governor, Event Bus or database."""

import ast
import copy
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.governor.evidence import detach_evidence
from app.strategy_engine.base_strategy import Opportunity
from app.trade_planning import (
    FixedNotionalSizing,
    PROPOSAL_SCHEMA_VERSION,
    ProposalError,
    ReferenceObservation,
    TradePlan,
    UnsupportedProposalVersion,
    plan_entry,
    proposals_equal,
    serialize_proposal,
    validate_proposal,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 9, 14, 32, tzinfo=UTC)
OBSERVED = datetime(2026, 10, 9, 14, 31, 59, 250000, tzinfo=UTC)
EXCHANGE = datetime(2026, 10, 9, 14, 31, 59, tzinfo=UTC)


def opportunity(direction="BUY", stop=99.5, target=105.0) -> Opportunity:
    return Opportunity(
        strategy="test", version="1", direction=direction, confidence=0.8,
        structural_invalidation=stop, structural_target=target,
        evidence={}, setup_detected_at=NOW,
    )


def make_plan(direction="BUY", stop=99.5, target=105.0, price=100.0, notional=1000.0,
              observed_at=OBSERVED, exchange_ts=EXCHANGE, now=NOW) -> TradePlan:
    plan = plan_entry(
        "AAPL", opportunity(direction, stop, target),
        ReferenceObservation(price=price, observed_at=observed_at, exchange_ts=exchange_ts),
        notional, now,
    )
    assert isinstance(plan, TradePlan)
    return plan


def long_proposal() -> dict:
    return serialize_proposal(make_plan())


# ------------------------------------------------------------- valid proposals


def test_long_proposal_matches_documented_shape_exactly() -> None:
    assert serialize_proposal(make_plan()) == {
        "schema_version": 1, "origin": "auto", "direction": "long",
        "entry": "100.0", "entry_basis": "last_price_update",
        "reference_observed_at": "2026-10-09T14:31:59.250000+00:00", "reference_age_seconds": "0.75",
        "reference_exchange_ts": "2026-10-09T14:31:59+00:00", "reference_source_age_seconds": "1.0",
        "stop": "99.5", "target": "105.0", "target_on_profit_side": True,
        "size": 10, "planned_risk_usd": "5.0", "r_multiple": 10.0,
        "sizing": {"method": "fixed_notional", "fixed_notional_usd": "1000.0"},
        "planned_at": "2026-10-09T14:32:00+00:00",
    }
    assert PROPOSAL_SCHEMA_VERSION == 1


def test_short_proposal() -> None:
    p = serialize_proposal(make_plan("SELL", 101.0, 95.0))
    assert (p["direction"], p["entry"], p["stop"], p["target"], p["size"]) == ("short", "100.0", "101.0", "95.0", 10)
    assert (p["planned_risk_usd"], p["r_multiple"], p["target_on_profit_side"]) == ("10.0", 5.0, True)


def test_key_order_follows_the_contract() -> None:
    assert list(long_proposal()) == [
        "schema_version", "origin", "direction", "entry", "entry_basis",
        "reference_observed_at", "reference_age_seconds", "reference_exchange_ts", "reference_source_age_seconds",
        "stop", "target", "target_on_profit_side", "size", "planned_risk_usd", "r_multiple", "sizing", "planned_at",
    ]


def test_symbol_is_not_part_of_the_proposal() -> None:
    assert "symbol" not in long_proposal()


def test_values_are_carried_not_recalculated() -> None:
    plan = dataclasses.replace(make_plan(), size=3, planned_risk_usd=Decimal("123.45"), r_multiple=9.9)
    p = serialize_proposal(plan)
    assert (p["size"], p["planned_risk_usd"], p["r_multiple"]) == (3, "123.45", 9.9)


# ------------------------------------------------------------ nulls and clocks


def test_missing_clocks_stay_null_and_never_use_the_local_clock() -> None:
    p = serialize_proposal(make_plan(observed_at=None, exchange_ts=None))
    assert (p["reference_observed_at"], p["reference_age_seconds"]) == (None, None)
    assert (p["reference_exchange_ts"], p["reference_source_age_seconds"]) == (None, None)
    assert p["planned_at"] == "2026-10-09T14:32:00+00:00"


def test_each_clock_is_independent() -> None:
    only_local = serialize_proposal(make_plan(exchange_ts=None))
    assert only_local["reference_age_seconds"] == "0.75" and only_local["reference_source_age_seconds"] is None
    only_source = serialize_proposal(make_plan(observed_at=None))
    assert only_source["reference_age_seconds"] is None and only_source["reference_source_age_seconds"] == "1.0"


def test_old_source_tick_with_fresh_envelope_keeps_both_ages_distinct() -> None:
    p = serialize_proposal(make_plan(observed_at=NOW - timedelta(milliseconds=100),
                                     exchange_ts=NOW - timedelta(hours=2, seconds=3)))
    assert p["reference_age_seconds"] == "0.1"
    assert p["reference_source_age_seconds"] == "7203.0"


def test_ages_are_exact_to_the_microsecond() -> None:
    p = serialize_proposal(make_plan(observed_at=NOW - timedelta(days=2, seconds=3, microseconds=1),
                                     exchange_ts=NOW))
    assert p["reference_age_seconds"] == "172803.000001"
    assert p["reference_source_age_seconds"] == "0.0"


def test_negative_future_ages_are_recorded_honestly_without_clamping() -> None:
    p = serialize_proposal(make_plan(observed_at=NOW + timedelta(seconds=2, milliseconds=500),
                                     exchange_ts=NOW + timedelta(microseconds=1)))
    assert p["reference_age_seconds"] == "-2.5"
    assert p["reference_source_age_seconds"] == "-0.000001"
    assert validate_proposal(p) == p  # still a valid, replayable record; policy is cutover's job


def test_aware_timestamps_are_normalized_to_utc() -> None:
    dhaka, eastern = timezone(timedelta(hours=6)), timezone(timedelta(hours=-4))
    p = serialize_proposal(make_plan(
        observed_at=OBSERVED.astimezone(eastern), exchange_ts=EXCHANGE.astimezone(dhaka), now=NOW.astimezone(dhaka),
    ))
    assert p == long_proposal()
    for key in ("planned_at", "reference_observed_at", "reference_exchange_ts"):
        assert p[key].endswith("+00:00")


# ------------------------------------------------------ exact numeric encoding


def test_planned_risk_is_the_exact_decimal_not_a_float_artifact() -> None:
    plan = make_plan(price=0.3, stop=0.1, target=0.9)
    assert plan.size == 3333 and plan.size * abs(plan.entry - plan.stop) != 666.6  # float arithmetic drifts
    p = serialize_proposal(plan)
    assert p["planned_risk_usd"] == "666.6" == format(plan.planned_risk_usd, "f")
    assert (p["entry"], p["stop"], p["target"]) == ("0.3", "0.1", "0.9")


def test_tiny_and_large_values_never_use_exponent_notation() -> None:
    plan = make_plan(price=1e-05, stop=5e-06, target=3e-05)
    p = serialize_proposal(plan)
    assert (p["entry"], p["stop"], p["target"]) == ("0.00001", "0.000005", "0.00003")
    big = serialize_proposal(dataclasses.replace(plan, planned_risk_usd=Decimal("1E+30")))
    assert big["planned_risk_usd"] == "1" + "0" * 30
    for text in (p["entry"], p["stop"], p["target"], p["planned_risk_usd"], big["planned_risk_usd"]):
        assert "e" not in text.lower()
    assert validate_proposal(big) == big


def test_integer_boolean_and_float_distinctions() -> None:
    p = long_proposal()
    assert type(p["size"]) is int and type(p["target_on_profit_side"]) is bool
    assert type(p["r_multiple"]) is float and type(p["entry"]) is str
    for bad in (True, 10.0, "10", 0, -1):
        with pytest.raises(ProposalError):
            validate_proposal({**p, "size": bad})
    for bad in (1, 0, "true", None):
        with pytest.raises(ProposalError):
            validate_proposal({**p, "target_on_profit_side": bad})
    with pytest.raises(ProposalError):
        validate_proposal({**p, "r_multiple": True})
    with pytest.raises(ProposalError):
        serialize_proposal(dataclasses.replace(make_plan(), size=True))
    with pytest.raises(ProposalError):
        serialize_proposal(dataclasses.replace(make_plan(), size=10.0))
    with pytest.raises(ProposalError):
        serialize_proposal(dataclasses.replace(make_plan(), target_on_profit_side=1))


def test_null_optional_values_are_preserved_as_null() -> None:
    plan = dataclasses.replace(make_plan(), target=None, r_multiple=None, target_on_profit_side=False)
    p = serialize_proposal(plan)
    assert (p["target"], p["r_multiple"], p["target_on_profit_side"]) == (None, None, False)
    assert validate_proposal(json.loads(json.dumps(p))) == p


def test_manual_origin_is_carried_not_rewritten() -> None:
    assert serialize_proposal(dataclasses.replace(make_plan(), origin="manual"))["origin"] == "manual"


# ----------------------------------------------------- serializer rejections


@pytest.mark.parametrize("changes", [
    {"entry": float("nan")}, {"entry": float("inf")}, {"entry": 0.0}, {"entry": -1.0}, {"entry": 100},
    {"entry": True}, {"entry": "100.0"}, {"stop": float("-inf")}, {"stop": 0.0}, {"target": float("nan")},
    {"target": -5.0}, {"r_multiple": float("nan")}, {"r_multiple": float("inf")}, {"r_multiple": 10},
    {"r_multiple": True}, {"size": 0}, {"size": -3}, {"size": "10"},
    {"planned_risk_usd": Decimal("NaN")}, {"planned_risk_usd": Decimal("Infinity")},
    {"planned_risk_usd": Decimal("-0.01")}, {"planned_risk_usd": Decimal("-0")},
    {"planned_risk_usd": 5.0}, {"planned_risk_usd": "5.0"},
    {"direction": "BUY"}, {"direction": None}, {"origin": "robot"},
    {"max_hold_seconds": 60}, {"corroboration": ("a",)}, {"corroboration": []},
    {"planned_at": datetime(2026, 10, 9, 14, 32)}, {"planned_at": "2026-10-09T14:32:00+00:00"},
    {"reference_observed_at": datetime(2026, 10, 9, 14, 31)}, {"reference_exchange_ts": 1700000000},
    {"sizing": FixedNotionalSizing(fixed_notional_usd=0.0)},
    {"sizing": FixedNotionalSizing(fixed_notional_usd=float("nan"))},
    {"sizing": FixedNotionalSizing(fixed_notional_usd=1000)},
    {"sizing": FixedNotionalSizing(fixed_notional_usd=1000.0, method="kelly")},
    {"sizing": {"method": "fixed_notional", "fixed_notional_usd": 1000.0}},
])
def test_serializer_rejects_unrepresentable_plan_values(changes) -> None:
    with pytest.raises(ProposalError):
        serialize_proposal(dataclasses.replace(make_plan(), **changes))


def test_serializer_rejects_non_plans_and_does_not_stringify_objects() -> None:
    for bad in (None, {}, long_proposal(), object(), "plan"):
        with pytest.raises(ProposalError):
            serialize_proposal(bad)


# ------------------------------------------------------ validator rejections


@pytest.mark.parametrize("version", [0, 2, 99, -1])
def test_unsupported_versions_are_refused_explicitly(version) -> None:
    with pytest.raises(UnsupportedProposalVersion):
        validate_proposal({**long_proposal(), "schema_version": version})
    assert issubclass(UnsupportedProposalVersion, ProposalError)


@pytest.mark.parametrize("version", [None, "1", 1.0, True, [1]])
def test_malformed_versions_are_refused(version) -> None:
    with pytest.raises(ProposalError) as err:
        validate_proposal({**long_proposal(), "schema_version": version})
    assert not isinstance(err.value, UnsupportedProposalVersion)


def test_missing_version_and_non_objects_are_refused() -> None:
    p = long_proposal()
    del p["schema_version"]
    for bad in (p, None, [], "{}", 1, long_proposal().items()):
        with pytest.raises(ProposalError):
            validate_proposal(bad)


@pytest.mark.parametrize("key", [k for k in long_proposal() if k != "schema_version"])
def test_every_field_is_required(key) -> None:
    p = long_proposal()
    del p[key]
    with pytest.raises(ProposalError):
        validate_proposal(p)


@pytest.mark.parametrize("extra", [{"symbol": "AAPL"}, {"max_hold_seconds": None}, {"note": 1}])
def test_unknown_fields_are_refused_not_dropped(extra) -> None:
    with pytest.raises(ProposalError):
        validate_proposal({**long_proposal(), **extra})
    p = long_proposal()
    p["sizing"] = {**p["sizing"], **extra}
    with pytest.raises(ProposalError):
        validate_proposal(p)


@pytest.mark.parametrize("key,bad", [
    ("entry", 100.0), ("entry", "1e2"), ("entry", "NaN"), ("entry", "Infinity"), ("entry", " 1"), ("entry", "1."),
    ("entry", "+1"), ("entry", "01.5"), ("entry", ""), ("entry", "0"), ("entry", "-1"), ("entry", None),
    ("stop", "0.0"), ("stop", "-0"), ("stop", None), ("stop", 99.5),
    ("target", ""), ("target", "0"), ("target", 105.0), ("target", "1,5"),
    ("planned_risk_usd", "-1"), ("planned_risk_usd", "-0"), ("planned_risk_usd", "NaN"), ("planned_risk_usd", 5.0),
    ("planned_risk_usd", None), ("planned_risk_usd", Decimal("5.0")),
    ("r_multiple", float("nan")), ("r_multiple", float("inf")), ("r_multiple", float("-inf")),
    ("r_multiple", "10.0"), ("r_multiple", True), ("r_multiple", [10.0]),
    ("direction", "BUY"), ("direction", "Long"), ("direction", None), ("direction", ["long"]),
    ("origin", "robot"), ("origin", None), ("entry_basis", "fill"), ("entry_basis", None),
    ("planned_at", "2026-10-09T14:32:00"), ("planned_at", "2026-10-09T14:32:00Z"),
    ("planned_at", "2026-10-09T20:32:00+06:00"), ("planned_at", "2026-10-09 14:32:00+00:00"),
    ("planned_at", "not a time"), ("planned_at", None), ("planned_at", NOW),
    ("reference_observed_at", "2026-10-09T14:31:59.250000"), ("reference_observed_at", 5),
    ("reference_exchange_ts", "2026-10-09T09:31:59-05:00"), ("reference_exchange_ts", ""),
    ("reference_age_seconds", 0.75), ("reference_age_seconds", "0.750"), ("reference_age_seconds", "1.0"),
    ("reference_age_seconds", None), ("reference_age_seconds", "NaN"),
    ("reference_source_age_seconds", "0"), ("reference_source_age_seconds", None),
    ("reference_exchange_ts", None), ("reference_observed_at", None),
    ("sizing", None), ("sizing", []), ("sizing", {"method": "fixed_notional"}),
])
def test_validator_rejects_malformed_fields(key, bad) -> None:
    with pytest.raises(ProposalError):
        validate_proposal({**long_proposal(), key: bad})


@pytest.mark.parametrize("sizing", [
    {"method": "kelly", "fixed_notional_usd": "1000.0"},
    {"method": "fixed_notional", "fixed_notional_usd": 1000.0},
    {"method": "fixed_notional", "fixed_notional_usd": "0.0"},
    {"method": "fixed_notional", "fixed_notional_usd": "-5"},
    {"method": "fixed_notional", "fixed_notional_usd": "1e3"},
    {"method": None, "fixed_notional_usd": "1000.0"},
])
def test_validator_rejects_malformed_sizing(sizing) -> None:
    with pytest.raises(ProposalError):
        validate_proposal({**long_proposal(), "sizing": sizing})


def test_non_string_keys_are_refused() -> None:
    p = long_proposal()
    p[1] = "x"
    with pytest.raises(ProposalError):
        validate_proposal(p)


def test_null_ages_require_null_clocks_and_present_ages_must_match_the_clocks() -> None:
    p = serialize_proposal(make_plan(observed_at=None, exchange_ts=None))
    with pytest.raises(ProposalError):
        validate_proposal({**p, "reference_age_seconds": "0.0"})
    with pytest.raises(ProposalError):
        validate_proposal({**p, "reference_observed_at": "2026-10-09T14:31:59+00:00"})
    with pytest.raises(ProposalError):  # a plausible but wrong age is not repaired
        validate_proposal({**long_proposal(), "reference_age_seconds": "0.76"})


def test_validator_applies_no_freshness_threshold() -> None:
    old = serialize_proposal(make_plan(observed_at=NOW - timedelta(days=400), exchange_ts=NOW - timedelta(days=400)))
    assert old["reference_source_age_seconds"] == "34560000.0"
    assert validate_proposal(old) == old


# ------------------------------------------------------------------ detachment


def test_serialized_proposal_is_detached_from_the_plan_and_between_calls() -> None:
    plan = make_plan()
    first = serialize_proposal(plan)
    first["size"] = 999
    first["sizing"]["fixed_notional_usd"] = "1.0"
    second = serialize_proposal(plan)
    assert second == long_proposal() and second["sizing"] is not first["sizing"]
    assert plan.size == 10 and plan.sizing.fixed_notional_usd == 1000.0  # frozen plan untouched


def test_validate_returns_a_detached_copy_and_never_mutates_its_input() -> None:
    source = long_proposal()
    snapshot = copy.deepcopy(source)
    checked = validate_proposal(source)
    assert checked == source and checked is not source and checked["sizing"] is not source["sizing"]
    checked["sizing"]["method"] = "x"
    checked["entry"] = "1"
    assert source == snapshot  # editing the result leaves the input alone
    again = validate_proposal(source)
    source["sizing"]["fixed_notional_usd"] = "5.0"
    assert again["sizing"]["fixed_notional_usd"] == "1000.0"  # editing the input leaves earlier results alone


# ------------------------------------------------------------ JSON round trips


def test_strict_json_round_trip_preserves_every_agreed_value() -> None:
    for plan in (make_plan(), make_plan("SELL", 101.0, 95.0), make_plan(observed_at=None, exchange_ts=None),
                 make_plan(price=0.3, stop=0.1, target=0.9)):
        proposal = serialize_proposal(plan)
        text = json.dumps(proposal, allow_nan=False)
        loaded = json.loads(text)
        assert loaded == proposal
        assert validate_proposal(loaded) == proposal
        assert proposals_equal(proposal, loaded)
        assert type(loaded["size"]) is int and type(loaded["target_on_profit_side"]) is bool
        assert type(loaded["r_multiple"]) is float and type(loaded["planned_risk_usd"]) is str
        assert Decimal(loaded["planned_risk_usd"]) == plan.planned_risk_usd
        assert Decimal(loaded["entry"]) == Decimal(repr(plan.entry))


def test_proposal_satisfies_the_existing_strict_json_validator() -> None:
    proposal = long_proposal()
    assert detach_evidence(proposal) == proposal  # same plain-finite-JSON bar as thesis["evidence"]


def test_jsonb_style_numeric_normalization_is_not_a_conflict() -> None:
    proposal = long_proposal()
    as_stored = json.loads(json.dumps(proposal).replace('"r_multiple": 10.0', '"r_multiple": 10'))
    assert type(as_stored["r_multiple"]) is int
    assert validate_proposal(as_stored) == proposal and type(validate_proposal(as_stored)["r_multiple"]) is float
    assert proposals_equal(proposal, as_stored)


# ------------------------------------------------------------------ comparison


def test_identical_proposals_compare_equal() -> None:
    assert proposals_equal(long_proposal(), long_proposal())
    assert proposals_equal(serialize_proposal(make_plan(observed_at=None)), serialize_proposal(make_plan(observed_at=None)))


def test_equal_decimal_values_with_different_text_are_the_same_plan() -> None:
    a = long_proposal()
    b = {**a, "planned_risk_usd": "5.00", "entry": "100.00", "sizing": {**a["sizing"], "fixed_notional_usd": "1000"}}
    assert proposals_equal(a, b) and proposals_equal(b, a)


@pytest.mark.parametrize("changes", [
    {"size": 11}, {"entry": "100.1"}, {"stop": "99.4"}, {"target": "105.1"}, {"target": None},
    {"direction": "short"}, {"origin": "manual"}, {"planned_risk_usd": "5.1"}, {"r_multiple": 10.1},
    {"r_multiple": None}, {"target_on_profit_side": False}, {"planned_at": "2026-10-09T14:32:01+00:00"},
    {"sizing": {"method": "fixed_notional", "fixed_notional_usd": "2000.0"}},
])
def test_conflicting_proposals_compare_different(changes) -> None:
    a, b = long_proposal(), {**long_proposal(), **changes}
    if "planned_at" in changes:  # keep ages consistent so the variant is itself valid
        b = serialize_proposal(make_plan(now=NOW + timedelta(seconds=1)))
    assert not proposals_equal(a, b) and not proposals_equal(b, a)


def test_clock_differences_are_conflicts() -> None:
    base = long_proposal()
    assert not proposals_equal(base, serialize_proposal(make_plan(observed_at=None)))
    assert not proposals_equal(base, serialize_proposal(make_plan(exchange_ts=EXCHANGE - timedelta(seconds=1))))
    assert not proposals_equal(base, serialize_proposal(make_plan(observed_at=OBSERVED + timedelta(milliseconds=1))))


def test_comparison_fails_closed_on_invalid_or_unsupported_input() -> None:
    good = long_proposal()
    for bad in ({**good, "size": True}, {**good, "size": 10.0}, {**good, "entry": 100.0}, None, {}):
        with pytest.raises(ProposalError):
            proposals_equal(good, bad)
        with pytest.raises(ProposalError):
            proposals_equal(bad, good)
    with pytest.raises(UnsupportedProposalVersion):
        proposals_equal(good, {**good, "schema_version": 2})


# ---------------------------------------------------- contracts and purity


def test_existing_trade_plan_contract_is_unchanged() -> None:
    assert [f.name for f in dataclasses.fields(TradePlan)] == [
        "symbol", "direction", "entry", "stop", "target", "size", "r_multiple", "planned_risk_usd",
        "max_hold_seconds", "origin", "corroboration", "planned_at", "reference_observed_at",
        "reference_exchange_ts", "target_on_profit_side", "sizing",
    ]
    assert [f.name for f in dataclasses.fields(ReferenceObservation)] == ["price", "observed_at", "exchange_ts"]
    assert [f.name for f in dataclasses.fields(FixedNotionalSizing)] == ["fixed_notional_usd", "method"]


def test_module_is_pure_stdlib_plus_plan_values() -> None:
    path = Path(__file__).resolve().parents[1] / "app" / "trade_planning" / "proposal.py"
    tree = ast.parse(path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module if node.module and node.module.startswith("app.") else node.module.split(".")[0])
    assert imported <= {"__future__", "math", "re", "datetime", "decimal", "typing", "app.trade_planning.plan"}
    forbidden = {"now", "utcnow", "today", "time", "sleep", "random", "open", "print", "getLogger"}
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
              for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Attribute, ast.Name))}
    assert not (called & forbidden)
    assert "str" not in called  # no arbitrary object stringification
