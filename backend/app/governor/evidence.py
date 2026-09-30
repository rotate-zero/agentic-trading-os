"""Strict, side-effect-free validation of an approved Opportunity's evidence.

`Opportunity.evidence` (strategy-engine-design.md §4) is the strategy's own
reasoning at signal time. The Governor stores a detached copy of it in
`trades.thesis["evidence"]` at acceptance, because nothing else persists it
(`OpportunityCache` overwrites in memory).

Honest state over fabricated state: this module NEVER repairs a payload. A
value that is not plain JSON is refused with `EvidenceError`; it is not
coerced, stringified, dropped, truncated or replaced with `{}`. The
distinctions JSON encoders would erase silently are refused explicitly too:

* NaN / +-Infinity (not JSON; PostgreSQL JSONB cannot store them),
* non-`str` mapping keys (`json.dumps` would turn `1` into `"1"`),
* tuples, sets, datetimes, Decimals, bytes, numpy scalars and any other
  non-JSON type (`json.dumps` would either raise late or convert silently),
* structures nested deeper than `MAX_DEPTH` (this also refuses reference
  cycles without recursing forever).

Every real strategy today emits nested dicts of str / int / float / bool /
None only, so the strictness costs nothing on the production path.
"""
from __future__ import annotations

import math
from typing import Any

MAX_DEPTH = 32


class EvidenceError(ValueError):
    """The evidence payload is not plain, finite JSON. Never auto-repaired."""


def _detach(value: Any, path: str, depth: int) -> Any:
    if depth > MAX_DEPTH:
        raise EvidenceError(f"evidence nested deeper than {MAX_DEPTH} levels at {path} (cycle or excess depth)")
    # bool is an int subclass; test order is irrelevant because both pass through unchanged.
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidenceError(f"non-finite number {value!r} at {path}")
        return float(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise EvidenceError(f"non-string key {key!r} ({type(key).__name__}) at {path}")
            out[key] = _detach(item, f"{path}.{key}", depth + 1)
        return out
    if isinstance(value, list):
        return [_detach(item, f"{path}[{i}]", depth + 1) for i, item in enumerate(value)]
    raise EvidenceError(f"non-JSON value of type {type(value).__name__} at {path}")


def detach_evidence(evidence: Any) -> dict[str, Any]:
    """Return a deep, detached, JSON-safe copy of `evidence`, or raise `EvidenceError`.

    The top level must be a dict (`Opportunity.evidence: dict`). An empty dict
    is a legitimate strategy value and is preserved as given.
    """
    if not isinstance(evidence, dict):
        raise EvidenceError(f"evidence must be a dict, got {type(evidence).__name__}")
    return _detach(evidence, "evidence", 0)


def _same(a: Any, b: Any) -> bool:
    """Structural equality that, unlike `==`, does not equate True with 1.

    Numbers compare by value across int/float (JSONB may return `1e22` as an
    integer, and that must not look like a conflicting replay).
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def evidence_equal(stored: Any, requested: Any) -> bool:
    """True when two already-detached evidence values are the same record."""
    return _same(stored, requested)
