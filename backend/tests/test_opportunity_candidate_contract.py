"""C1 pure candidate contract: models, canonical identities, purity.

No database, Event Bus, Scheduler, Governor or wall clock is involved.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.trading_intelligence import candidate_contract, candidate_eligibility, candidate_state
from app.trading_intelligence.candidate_contract import (
    CANDIDATE_ID_PREFIX,
    EVALUATION_ID_PREFIX,
    CandidateContractError,
    EvaluationBatch,
    OpportunityContent,
    StrategyDisposition,
    UnavailablePrerequisite,
    candidate_id,
    canonical_identity_payload,
    canonical_timestamp,
    evaluation_id,
)

T0 = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)
ONE_MIN = timedelta(minutes=1)


# --- Shared builders (imported by the reducer/eligibility tests) -----------


def make_opportunity(direction: str = "long", confidence: float = 0.7, **overrides) -> OpportunityContent:
    fields = dict(
        direction=direction,
        confidence=confidence,
        structural_invalidation=99.0 if direction == "long" else 101.0,
        structural_target=102.0 if direction == "long" else 98.0,
        evidence={"conditions": {"x": 1}, "reason": "test"},
    )
    fields.update(overrides)
    return OpportunityContent.create(**fields)


def opportunity_disposition(strategy: str = "orb", version: str = "1", direction: str = "long", **kw) -> StrategyDisposition:
    return StrategyDisposition(strategy, version, "opportunity", make_opportunity(direction, **kw))


def make_batch(
    ts: datetime = T0,
    dispositions=None,
    *,
    symbol: str = "AAPL",
    timeframe: str = "1m",
    mode: str = "simulated",
    interval: timedelta = ONE_MIN,
    completed_delay: timedelta = timedelta(seconds=1),
    unavailable=(),
) -> EvaluationBatch:
    if dispositions is None and not unavailable:
        dispositions = [opportunity_disposition()]
    return EvaluationBatch(
        symbol=symbol,
        trigger_timeframe=timeframe,
        mode=mode,
        source_candle_ts=ts,
        source_interval_start=ts,
        source_interval_close=ts + interval,
        completed_at=ts + interval + completed_delay,
        dispositions=tuple(dispositions or ()),
        unavailable_prerequisites=tuple(unavailable),
    )


# --- Identity --------------------------------------------------------------


def ids(direction: str = "long", ts: datetime = T0, **kw):
    args = dict(symbol="AAPL", strategy="orb", strategy_version="1", trigger_timeframe="1m")
    args.update(kw)
    return (
        evaluation_id(args["symbol"], args["strategy"], args["strategy_version"], args["trigger_timeframe"], ts),
        candidate_id(args["symbol"], args["strategy"], args["strategy_version"], args["trigger_timeframe"], ts, direction),
    )


def test_long_and_short_share_evaluation_id_but_not_candidate_id() -> None:
    eval_long, cand_long = ids("long")
    eval_short, cand_short = ids("short")
    assert eval_long == eval_short
    assert cand_long != cand_short
    assert eval_long.startswith(EVALUATION_ID_PREFIX)
    assert cand_long.startswith(CANDIDATE_ID_PREFIX)


def test_every_documented_input_changes_the_identity() -> None:
    base_eval, base_cand = ids()
    variants = [
        ids(symbol="MSFT"),
        ids(strategy="gap"),
        ids(strategy_version="2"),
        ids(trigger_timeframe="5m"),
        ids(ts=T0 + ONE_MIN),
    ]
    for variant_eval, variant_cand in variants:
        assert variant_eval != base_eval
        assert variant_cand != base_cand


def test_timezone_equivalent_timestamps_give_identical_ids() -> None:
    dhaka = timezone(timedelta(hours=6))
    new_york = timezone(timedelta(hours=-4))
    assert ids(ts=T0) == ids(ts=T0.astimezone(dhaka)) == ids(ts=T0.astimezone(new_york))
    assert canonical_timestamp(T0.astimezone(dhaka)) == "2026-10-09T14:00:00.000000Z"


def test_microsecond_difference_is_a_different_candle() -> None:
    assert ids(ts=T0) != ids(ts=T0 + timedelta(microseconds=1))


def test_canonical_payload_is_documented_and_stable() -> None:
    payload = canonical_identity_payload("candidate", "AAPL", "orb", "1", "1m", T0, "long")
    assert payload == '["tios.candidate",1,"AAPL","orb","1","1m","2026-10-09T14:00:00.000000Z","long"]'
    assert canonical_identity_payload("evaluation", "AAPL", "orb", "1", "1m", T0) == (
        '["tios.evaluation",1,"AAPL","orb","1","1m","2026-10-09T14:00:00.000000Z"]'
    )
    # Golden vector: changing the encoding must bump IDENTITY_ENCODING_VERSION.
    assert candidate_id("AAPL", "orb", "1", "1m", T0, "long") == "cnd1:" + hashlib.sha256(payload.encode()).hexdigest()
    assert candidate_id("AAPL", "orb", "1", "1m", T0, "long") == (
        "cnd1:293db50c10f17a71359b15f33abb57c7fffc1c7abfcdd625b1a9d33ff128b1b1"
    )
    assert evaluation_id("AAPL", "orb", "1", "1m", T0) == (
        "evl1:22a5eb8ef9d76a3e33aedfaf521cfe3adf857dd65f05f9a6896c41c7b3013753"
    )


def test_separator_injection_cannot_collide_identities() -> None:
    # A delimiter-joined encoding would collide here; JSON array encoding does not.
    first = evaluation_id("AB", "C", "1", "1m", T0)
    second = evaluation_id("A", "BC", "1", "1m", T0)
    assert first != second
    assert evaluation_id('A","B', "C", "1", "1m", T0) != evaluation_id("A", 'B","C', "1", "1m", T0)


def test_confidence_and_times_do_not_affect_identity() -> None:
    low = make_batch(dispositions=[opportunity_disposition(confidence=0.1)])
    high = make_batch(dispositions=[opportunity_disposition(confidence=0.99)], completed_delay=timedelta(minutes=9))
    a, b = low.dispositions[0], high.dispositions[0]
    assert low.evaluation_id_for(a) == high.evaluation_id_for(b)
    assert low.candidate_id_for(a) == high.candidate_id_for(b)


def test_candidate_identity_is_not_a_trade_uuid() -> None:
    import uuid

    _, cand = ids()
    with pytest.raises(ValueError):
        uuid.UUID(cand)


def test_identity_rejects_bad_inputs() -> None:
    with pytest.raises(CandidateContractError):
        evaluation_id("", "orb", "1", "1m", T0)
    with pytest.raises(CandidateContractError):
        evaluation_id("AAPL", "orb", " 1", "1m", T0)
    with pytest.raises(CandidateContractError):
        evaluation_id("AAPL", "orb", "1", "1m", datetime(2026, 10, 9, 14, 0))  # naive
    with pytest.raises(CandidateContractError):
        candidate_id("AAPL", "orb", "1", "1m", T0, "BUY")
    with pytest.raises(CandidateContractError):
        canonical_identity_payload("evaluation", "AAPL", "orb", "1", "1m", T0, "long")


# --- Models ----------------------------------------------------------------


def test_opportunity_content_is_detached_and_immutable() -> None:
    evidence = {"conditions": {"levels": [1, 2]}, "reason": "r"}
    original = copy.deepcopy(evidence)
    content = make_opportunity(evidence=evidence)
    assert evidence == original  # caller payload never mutated
    evidence["conditions"]["levels"].append(3)  # later caller mutation cannot reach the value
    assert content.evidence_copy() == original
    copy_one = content.evidence_copy()
    copy_one["conditions"]["levels"].append(99)
    assert content.evidence_copy() == original  # every read is a fresh copy
    with pytest.raises(FrozenInstanceError):
        content.confidence = 0.1  # type: ignore[misc]


def test_opportunity_content_validation() -> None:
    with pytest.raises(CandidateContractError):
        make_opportunity(confidence=float("nan"))
    with pytest.raises(CandidateContractError):
        make_opportunity(structural_target=float("inf"))
    with pytest.raises(CandidateContractError):
        make_opportunity(direction="BUY")
    with pytest.raises(CandidateContractError):
        make_opportunity(status="open")
    with pytest.raises(CandidateContractError):
        make_opportunity(expected_horizon_minutes=0)
    with pytest.raises(CandidateContractError):
        make_opportunity(evidence={"when": T0})  # must be serialised first
    with pytest.raises(CandidateContractError):
        make_opportunity(evidence={"x": float("nan")})
    with pytest.raises(CandidateContractError):
        OpportunityContent("long", 0.5, 1.0, 2.0, "actionable", None, '{"b": 1}')  # non-canonical text


def test_disposition_shape_rules() -> None:
    StrategyDisposition("orb", "1", "no_opportunity")
    StrategyDisposition("orb", "1", "gated", reason="volume_gate")
    StrategyDisposition("orb", "1", "error", reason="ValueError")
    with pytest.raises(CandidateContractError):
        StrategyDisposition("orb", "1", "opportunity")
    with pytest.raises(CandidateContractError):
        StrategyDisposition("orb", "1", "no_opportunity", opportunity=make_opportunity())
    with pytest.raises(CandidateContractError):
        StrategyDisposition("orb", "1", "gated")
    with pytest.raises(CandidateContractError):
        StrategyDisposition("orb", "1", "error", reason="")
    with pytest.raises(CandidateContractError):
        StrategyDisposition("orb", "1", "weird")  # type: ignore[arg-type]


def test_batch_normalises_times_to_utc_and_keeps_them_separate() -> None:
    dhaka = timezone(timedelta(hours=6))
    batch = EvaluationBatch(
        symbol="AAPL",
        trigger_timeframe="1m",
        mode="simulated",
        source_candle_ts=T0.astimezone(dhaka),
        source_interval_start=T0.astimezone(dhaka),
        source_interval_close=(T0 + ONE_MIN).astimezone(dhaka),
        completed_at=(T0 + ONE_MIN + timedelta(seconds=3)).astimezone(dhaka),
        dispositions=(opportunity_disposition(),),
    )
    assert batch.source_candle_ts == T0 and batch.source_candle_ts.utcoffset() == timedelta(0)
    assert batch.source_interval_close == T0 + ONE_MIN
    assert batch.completed_at == T0 + ONE_MIN + timedelta(seconds=3)
    assert batch.completed_at != batch.source_interval_close


def test_batch_validation() -> None:
    with pytest.raises(CandidateContractError):  # naive time
        EvaluationBatch("AAPL", "1m", "simulated", datetime(2026, 10, 9, 14), T0, T0 + ONE_MIN, T0, (opportunity_disposition(),))
    with pytest.raises(CandidateContractError):  # candle outside its interval
        EvaluationBatch("AAPL", "1m", "simulated", T0 + ONE_MIN, T0, T0 + ONE_MIN, T0, (opportunity_disposition(),))
    with pytest.raises(CandidateContractError):  # empty
        make_batch(dispositions=[], unavailable=())
    with pytest.raises(CandidateContractError):  # duplicate strategy version
        make_batch(dispositions=[opportunity_disposition(), opportunity_disposition(direction="short")])
    with pytest.raises(CandidateContractError):  # unavailable and dispositions together
        make_batch(unavailable=[UnavailablePrerequisite("features", "candle_mismatch")], dispositions=[opportunity_disposition()])
    with pytest.raises(CandidateContractError):  # duplicate prerequisite name
        make_batch(unavailable=[UnavailablePrerequisite("a", "x"), UnavailablePrerequisite("a", "y")])
    with pytest.raises(CandidateContractError):
        make_batch(mode="sandbox")


def test_unavailable_prerequisites_are_explicit_and_not_selectable() -> None:
    batch = make_batch(unavailable=[UnavailablePrerequisite("market_state", "candle_ts_mismatch")])
    assert not batch.selectable
    assert batch.dispositions == ()
    assert batch.unavailable_prerequisites[0].name == "market_state"
    assert make_batch().selectable


def test_dispositions_are_ordered_deterministically() -> None:
    forward = make_batch(dispositions=[opportunity_disposition("gap"), StrategyDisposition("orb", "1", "no_opportunity")])
    backward = make_batch(dispositions=[StrategyDisposition("orb", "1", "no_opportunity"), opportunity_disposition("gap")])
    assert forward == backward


def test_non_opportunity_dispositions_have_no_candidate_id() -> None:
    batch = make_batch(dispositions=[StrategyDisposition("orb", "1", "no_opportunity")])
    assert batch.candidate_id_for(batch.dispositions[0]) is None
    assert batch.evaluation_id_for(batch.dispositions[0]).startswith(EVALUATION_ID_PREFIX)


# --- Purity ----------------------------------------------------------------

_MODULES = (candidate_contract, candidate_state, candidate_eligibility)
_ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "hashlib",
    "json",
    "math",
    "types",
    "typing",
    "app.trading_intelligence.candidate_contract",
    "app.trading_intelligence.candidate_state",
}
_FORBIDDEN_CALLS = {"now", "utcnow", "today", "time", "monotonic", "perf_counter", "open", "print", "uuid4", "random", "sleep"}


@pytest.mark.parametrize("module", _MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_modules_have_no_clock_io_or_wiring_imports(module) -> None:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in _ALLOWED_IMPORTS, f"{module.__name__} imports {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert node.module in _ALLOWED_IMPORTS, f"{module.__name__} imports from {node.module}"
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in _FORBIDDEN_CALLS, f"{module.__name__} calls {name}()"


def test_importing_the_core_pulls_in_no_application_services() -> None:
    import subprocess
    import sys

    backend = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import app.trading_intelligence.candidate_eligibility\n"
        "bad = sorted(m for m in sys.modules if m.startswith('app.') and not m.startswith('app.trading_intelligence'))\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code, str(backend)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_golden_json_vectors_round_trip() -> None:
    # The documented payload is plain JSON an independent reader can rebuild.
    payload = canonical_identity_payload("candidate", "AAPL", "orb", "1", "1m", T0, "short")
    assert json.loads(payload) == ["tios.candidate", 1, "AAPL", "orb", "1", "1m", "2026-10-09T14:00:00.000000Z", "short"]
