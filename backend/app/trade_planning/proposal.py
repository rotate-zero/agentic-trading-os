"""Pure, strict serialization boundary for a TradePlan's persisted proposal.

`serialize_proposal(plan)` turns a landed `TradePlan` into the versioned,
plain-JSON object that the simulated authorizer stores at `trades.thesis["proposal"]`
(execution-engine-design.md §6.14.7, trading-intelligence-architecture.md
§19.5). `validate_proposal(value)` re-checks a stored or requested object
against the same schema and returns a detached normalized copy.
`proposals_equal(a, b)` answers the replay question "identical or conflicting?".

This module only *converts and checks*. It never recalculates quantity,
planned risk or R (the plan's values are carried), never authorizes, and has
no clock, I/O, logging, Event Bus or database dependency. The Governor ledger
consumes this boundary for proposal persistence and replay comparison.

Honest state over fabricated state: nothing is repaired. An unsupported
version, a missing/extra key, a wrong type, a naive timestamp, a non-finite
number or a value the v1 schema cannot represent raises `ProposalError`; it is
never coerced, stringified, clamped, defaulted or dropped.

Encoding (schema_version 1):

* money-like values (`entry`, `stop`, `target`, `planned_risk_usd`,
  `sizing.fixed_notional_usd`) are exact decimal strings in plain positional
  notation (no exponent). A float is converted through its shortest repr
  (`Decimal(repr(x))`), the same convention the planner uses for risk;
* `size` is a JSON integer, booleans stay booleans (a bool is never an int);
* `r_multiple` is a finite JSON number (float) or null, as the event publishes;
* timestamps are UTC ISO-8601 strings; naive inputs are refused;
* the two ages are exact decimal strings of `planned_at - clock` in seconds.
  They are descriptive, may be negative (a future clock) and are recorded as
  they are: no threshold, no clamping. Freshness policy belongs to cutover.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.trade_planning.plan import FixedNotionalSizing, TradePlan

PROPOSAL_SCHEMA_VERSION = 1
ENTRY_BASIS = "last_price_update"

_DIRECTIONS = ("long", "short")
_ORIGINS = ("auto", "manual")
_TOP_KEYS = (
    "schema_version", "origin", "direction", "entry", "entry_basis",
    "reference_observed_at", "reference_age_seconds",
    "reference_exchange_ts", "reference_source_age_seconds",
    "stop", "target", "target_on_profit_side", "size", "planned_risk_usd",
    "r_multiple", "sizing", "planned_at",
)
_SIZING_KEYS = ("method", "fixed_notional_usd")
_DECIMAL_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
_ONE_MICROSECOND = timedelta(microseconds=1)


class ProposalError(ValueError):
    """The plan or proposal is not representable/valid under the v1 schema. Never repaired."""


class UnsupportedProposalVersion(ProposalError):
    """`schema_version` is not a version this code understands."""


# ---------------------------------------------------------------- primitives


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _price_text(value: Any, name: str, *, positive: bool = True) -> str:
    """Exact decimal string of a finite float (shortest repr, no exponent)."""
    if isinstance(value, bool) or not isinstance(value, float):
        raise ProposalError(f"{name} must be a float, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ProposalError(f"{name} must be finite, got {value!r}")
    if positive and value <= 0:
        raise ProposalError(f"{name} must be positive, got {value!r}")
    return _decimal_text(Decimal(repr(float(value))))


def _utc_text(value: Any, name: str) -> str:
    if not isinstance(value, datetime):
        raise ProposalError(f"{name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProposalError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _age_text(planned_at: datetime, clock: datetime) -> str:
    """Exact seconds of planned_at - clock, e.g. '0.75', '1.0', '-2.5', '0.0'."""
    micros = (planned_at - clock) // _ONE_MICROSECOND
    whole, frac = divmod(abs(micros), 1_000_000)
    fraction = f"{frac:06d}".rstrip("0") or "0"
    return f"{'-' if micros < 0 else ''}{whole}.{fraction}"


def _decimal_from_text(value: Any, name: str) -> Decimal:
    if not isinstance(value, str) or _DECIMAL_RE.fullmatch(value) is None:
        raise ProposalError(f"{name} must be a plain decimal string, got {value!r}")
    return Decimal(value)


def _parse_utc(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ProposalError(f"{name} must be a UTC ISO-8601 string, got {type(value).__name__}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ProposalError(f"{name} is not an ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0) or parsed.isoformat() != value:
        raise ProposalError(f"{name} must be canonical UTC (+00:00) ISO-8601, got {value!r}")
    return parsed


def _require_keys(value: Any, keys: tuple[str, ...], name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProposalError(f"{name} must be an object, got {type(value).__name__}")
    for key in value:
        if type(key) is not str:
            raise ProposalError(f"{name} has non-string key {key!r}")
    missing = [k for k in keys if k not in value]
    extra = [k for k in value if k not in keys]
    if missing or extra:
        raise ProposalError(f"{name} keys differ from schema (missing={missing}, unexpected={extra})")
    return value


# ----------------------------------------------------------------- serialize


def serialize_proposal(plan: TradePlan) -> dict[str, Any]:
    """Return a detached, strict-JSON proposal for a landed `TradePlan`.

    Carries the plan's values as they are. `symbol` is not part of the
    proposal: the decision row that stores it already identifies the symbol.
    A plan the v1 schema cannot represent (hold time, corroboration, an
    unknown origin/direction/sizing method) is refused, not truncated.
    """
    if not isinstance(plan, TradePlan):
        raise ProposalError(f"plan must be a TradePlan, got {type(plan).__name__}")
    if plan.direction not in _DIRECTIONS:
        raise ProposalError(f"unsupported direction {plan.direction!r}")
    if plan.origin not in _ORIGINS:
        raise ProposalError(f"unsupported origin {plan.origin!r}")
    if plan.max_hold_seconds is not None:
        raise ProposalError("max_hold_seconds is not representable in schema_version 1")
    if plan.corroboration != ():
        raise ProposalError("corroboration is not representable in schema_version 1")
    if not _is_int(plan.size) or plan.size < 1:
        raise ProposalError(f"size must be an integer >= 1, got {plan.size!r}")
    if not isinstance(plan.target_on_profit_side, bool):
        raise ProposalError("target_on_profit_side must be a bool")
    if not isinstance(plan.planned_risk_usd, Decimal) or not plan.planned_risk_usd.is_finite():
        raise ProposalError("planned_risk_usd must be a finite Decimal")
    if plan.planned_risk_usd < 0 or plan.planned_risk_usd.is_signed():
        raise ProposalError("planned_risk_usd must not be negative")
    if plan.r_multiple is not None:
        if isinstance(plan.r_multiple, bool) or not isinstance(plan.r_multiple, float):
            raise ProposalError(f"r_multiple must be a float or None, got {type(plan.r_multiple).__name__}")
        if not math.isfinite(plan.r_multiple):
            raise ProposalError(f"r_multiple must be finite, got {plan.r_multiple!r}")
    sizing = plan.sizing
    if not isinstance(sizing, FixedNotionalSizing) or sizing.method != "fixed_notional":
        raise ProposalError("sizing must be FixedNotionalSizing with method 'fixed_notional'")

    planned_at = _utc_text(plan.planned_at, "planned_at")

    def reference_clock(clock: Any, name: str) -> tuple[str | None, str | None]:
        if clock is None:
            return None, None
        return _utc_text(clock, name), _age_text(plan.planned_at, clock)

    observed_at, observed_age = reference_clock(plan.reference_observed_at, "reference_observed_at")
    exchange_ts, source_age = reference_clock(plan.reference_exchange_ts, "reference_exchange_ts")

    proposal = {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "origin": plan.origin,
        "direction": plan.direction,
        "entry": _price_text(plan.entry, "entry"),
        "entry_basis": ENTRY_BASIS,
        "reference_observed_at": observed_at,
        "reference_age_seconds": observed_age,
        "reference_exchange_ts": exchange_ts,
        "reference_source_age_seconds": source_age,
        "stop": _price_text(plan.stop, "stop"),
        "target": None if plan.target is None else _price_text(plan.target, "target"),
        "target_on_profit_side": plan.target_on_profit_side,
        "size": int(plan.size),
        "planned_risk_usd": _decimal_text(plan.planned_risk_usd),
        "r_multiple": None if plan.r_multiple is None else float(plan.r_multiple),
        "sizing": {
            "method": sizing.method,
            "fixed_notional_usd": _price_text(sizing.fixed_notional_usd, "sizing.fixed_notional_usd"),
        },
        "planned_at": planned_at,
    }
    # Self-check: what we emit must satisfy the same validator a replay uses.
    return validate_proposal(proposal)


# ------------------------------------------------------------------ validate


def validate_proposal(value: Any) -> dict[str, Any]:
    """Return a detached normalized copy of a v1 proposal, or raise `ProposalError`.

    Checks shape and internal consistency only. It does not recompute size,
    planned risk or R, and it does not apply any freshness threshold.
    """
    if not isinstance(value, dict):
        raise ProposalError(f"proposal must be an object, got {type(value).__name__}")
    version = value.get("schema_version")
    if not _is_int(version):
        raise ProposalError(f"schema_version must be an integer, got {version!r}")
    if version != PROPOSAL_SCHEMA_VERSION:
        raise UnsupportedProposalVersion(f"unsupported proposal schema_version {version!r}")
    data = _require_keys(value, _TOP_KEYS, "proposal")

    if data["origin"] not in _ORIGINS or type(data["origin"]) is not str:
        raise ProposalError(f"origin must be one of {_ORIGINS}, got {data['origin']!r}")
    if data["direction"] not in _DIRECTIONS or type(data["direction"]) is not str:
        raise ProposalError(f"direction must be one of {_DIRECTIONS}, got {data['direction']!r}")
    if data["entry_basis"] != ENTRY_BASIS or type(data["entry_basis"]) is not str:
        raise ProposalError(f"entry_basis must be {ENTRY_BASIS!r}, got {data['entry_basis']!r}")

    out: dict[str, Any] = {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "origin": data["origin"],
        "direction": data["direction"],
        "entry_basis": ENTRY_BASIS,
    }
    for key in ("entry", "stop"):
        if _decimal_from_text(data[key], key) <= 0:
            raise ProposalError(f"{key} must be positive, got {data[key]!r}")
        out[key] = data[key]
    if data["target"] is None:
        out["target"] = None
    else:
        if _decimal_from_text(data["target"], "target") <= 0:
            raise ProposalError(f"target must be positive, got {data['target']!r}")
        out["target"] = data["target"]
    if not isinstance(data["target_on_profit_side"], bool):
        raise ProposalError("target_on_profit_side must be a bool")
    out["target_on_profit_side"] = data["target_on_profit_side"]
    if not _is_int(data["size"]) or data["size"] < 1:
        raise ProposalError(f"size must be an integer >= 1, got {data['size']!r}")
    out["size"] = int(data["size"])
    if _decimal_from_text(data["planned_risk_usd"], "planned_risk_usd") < 0 or data["planned_risk_usd"].startswith("-"):
        raise ProposalError(f"planned_risk_usd must not be negative, got {data['planned_risk_usd']!r}")
    out["planned_risk_usd"] = data["planned_risk_usd"]

    r = data["r_multiple"]
    if r is None:
        out["r_multiple"] = None
    else:
        if not (_is_int(r) or isinstance(r, float)):
            raise ProposalError(f"r_multiple must be a finite number or null, got {r!r}")
        if not math.isfinite(r):
            raise ProposalError(f"r_multiple must be finite, got {r!r}")
        out["r_multiple"] = float(r)  # JSON has one number type; 10 and 10.0 are the same value

    sizing = _require_keys(data["sizing"], _SIZING_KEYS, "sizing")
    if sizing["method"] != "fixed_notional" or type(sizing["method"]) is not str:
        raise ProposalError(f"sizing.method must be 'fixed_notional', got {sizing['method']!r}")
    if _decimal_from_text(sizing["fixed_notional_usd"], "sizing.fixed_notional_usd") <= 0:
        raise ProposalError(f"sizing.fixed_notional_usd must be positive, got {sizing['fixed_notional_usd']!r}")
    out["sizing"] = {"method": "fixed_notional", "fixed_notional_usd": sizing["fixed_notional_usd"]}

    planned_at = _parse_utc(data["planned_at"], "planned_at")
    out["planned_at"] = data["planned_at"]
    for clock_key, age_key in (
        ("reference_observed_at", "reference_age_seconds"),
        ("reference_exchange_ts", "reference_source_age_seconds"),
    ):
        clock, age = data[clock_key], data[age_key]
        if clock is None or age is None:
            if clock is not None or age is not None:
                raise ProposalError(f"{clock_key} and {age_key} must both be null or both be present")
            out[clock_key] = None
            out[age_key] = None
            continue
        if _age_text(planned_at, _parse_utc(clock, clock_key)) != age:
            raise ProposalError(f"{age_key} {age!r} is not planned_at minus {clock_key}")
        out[clock_key] = clock
        out[age_key] = age

    return {key: out[key] for key in _TOP_KEYS}


# ------------------------------------------------------------------- compare


def _comparison_key(proposal: dict[str, Any]) -> tuple:
    """Validated proposals compared by meaning: decimal strings by value, others exactly."""
    decimal_fields = {"entry", "stop", "target", "planned_risk_usd"}
    parts: list[Any] = []
    for key in _TOP_KEYS:
        item = proposal[key]
        if key in decimal_fields and item is not None:
            item = Decimal(item)
        elif key == "sizing":
            item = (item["method"], Decimal(item["fixed_notional_usd"]))
        parts.append((key, type(item), item))
    return tuple(parts)


def proposals_equal(stored: Any, requested: Any) -> bool:
    """True when two proposals are the same plan; False when they conflict.

    Both are validated first, so an invalid or unsupported-version value
    raises `ProposalError` instead of comparing as "different". Decimal
    strings compare by value ("5.0" equals "5.00"); a bool never equals an
    int and an integer size never equals a float.
    """
    return _comparison_key(validate_proposal(stored)) == _comparison_key(validate_proposal(requested))
