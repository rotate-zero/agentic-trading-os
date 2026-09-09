"""
Declarative StrategyConfig.gate_conditions enforcement (strategy-engine-
design.md §2b, decision #117). Centralizes environmental preconditions
the Scheduler checks BEFORE evaluate() is called, per §2b's own text —
resolves the duplication risk of every strategy re-checking the same
market-condition precondition inside its own evaluate(), same "tunable
numbers as data, pattern logic as code" split §3 already applies to
thresholds, extended to cover gates too.

ARCHITECTURAL RULE (made explicit, decision #118) — this module and
StrategyScheduler are the ONLY code allowed to interpret
gate_conditions. StrategyConfig.gate_conditions is declarative
configuration, nothing more; StrategyScheduler (via the two functions
below) is its sole enforcement authority. A Strategy subclass may
declare gate_conditions on its own StrategyConfig, but must never
independently interpret the dict, invent a new key's meaning, or
enforce one itself — that would silently fork one condition into two
possibly-divergent implementations, the exact failure mode a single
shared registry exists to prevent. A strategy's own inline
MarketClock.is_regular_session() call (4 of the 7 today — see decision
#117's own finding) is a pre-existing, currently-tolerated exception,
not a template: it predates this rule, is flagged as redundant, and is
tracked for removal (§10 D16) precisely so it stops being a second
place "session" could ever be interpreted differently. No NEW strategy
should add another one.

v1 SCOPE — supports exactly one key, "session": "regular". Verified
directly against every one of the 7 built strategies' own
default_config() (grepped, not assumed — see decision #117): all 7
declare exactly {"session": "regular"}, nothing else. §2b's own
illustrative StrategyConfig schema comment (strategy-engine-design.md
§3, `{"vix_min": 20, "session": "regular"}`) does NOT correspond to any
real field anywhere in this codebase — grepped directly, no "vix" key
exists in schemas/events/market_state.py or schemas/events/context.py —
so this module does not build support for "vix_min", or for any other
condition nothing here can actually check yet.

EXTENSIBILITY, without a structural rewrite. A genuinely new condition
key is added by (1) adding it to `_RECOGNIZED` below with its allowed
values, and (2) adding one branch to `gate_conditions_satisfied()`.
Nothing else in this module, or in scheduler.py, needs to change shape
— this is the whole point of keeping the registry and the check
colocated and small rather than embedding "session" ad hoc inside
scheduler.py's own control flow.

UNKNOWN/MALFORMED gate_conditions — fail loudly at registration, not
silently at eval time. Real call, not the obvious default — see
decision #117 in confirmed-decisions.md for the full reasoning. Short
version: decision #114's own precedent for an adjacent case
(ScheduleTrigger.kind not yet wired, e.g. on_event) was "accepted into
the registry but logged as unreachable" — a strategy declaring that
today simply doesn't run yet, and nothing about its OWN correctness is
silently wrong. An unrecognized gate_conditions key is a materially
different failure: StrategyConfig.rationale (§3) is the operator's own
record of WHY a precondition exists, and if the key it names is never
actually checked by anything, the operator has silently lost a
precondition they believe is protecting them — exactly the "honest
state over fabricated state" failure this project's own confirmed
decisions (#89, #91, #98) consistently refuse to allow elsewhere.
`validate_gate_conditions()` is called once per strategy at
StrategyScheduler construction time (not per-candle) and raises
ValueError — the Scheduler itself fails to construct, so the app never
starts with a StrategyConfig it can't actually honor. Both an
unrecognized KEY and an unrecognized VALUE for a recognized key
(e.g. `{"session": "pre_market"}` — a real Session member, but not one
this module's "regular" check implements) fail the same way, for the
same reason: either one is a declared precondition this build cannot
actually evaluate.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.market_clock import get_market_clock

# key -> set of values this module actually knows how to check. See the
# module docstring's EXTENSIBILITY note for how to add a new key.
_RECOGNIZED: dict[str, set[str]] = {
    "session": {"regular"},
}


def validate_gate_conditions(strategy_name: str, gate_conditions: dict[str, Any]) -> None:
    """Raises ValueError for any key or value this module doesn't
    actually know how to check. Call once per strategy at registration
    time (StrategyScheduler.__init__) — see module docstring's "fail
    loudly at registration" section for why this is a hard failure, not
    a logged-and-continue warning. An empty dict (StrategyConfig's own
    default, base_strategy.py §3) always passes trivially — nothing to
    validate."""
    for key, value in gate_conditions.items():
        if key not in _RECOGNIZED:
            raise ValueError(
                f"{strategy_name}: gate_conditions declares unrecognized key {key!r} — "
                f"this build only enforces {sorted(_RECOGNIZED)}. A declared precondition "
                f"nothing checks is worse than none at all (see gate_conditions.py's module "
                f"docstring) — fix the StrategyConfig, or extend this module to actually "
                f"recognize the key, before this strategy can be registered."
            )
        if value not in _RECOGNIZED[key]:
            raise ValueError(
                f"{strategy_name}: gate_conditions[{key!r}] = {value!r} is not a value this "
                f"build knows how to check — supported values for {key!r} are "
                f"{sorted(_RECOGNIZED[key])}."
            )


def gate_conditions_satisfied(gate_conditions: dict[str, Any], candle_ts: datetime) -> bool:
    """True iff every declared condition holds for candle_ts. An empty
    dict always passes — no restriction declared, not "block
    everything" (StrategyConfig.gate_conditions defaults to {},
    base_strategy.py §3, and every one of today's 7 real configs sets
    it to a non-empty dict anyway, so the empty-dict case is currently
    reachable only via a hand-built StrategyConfig, e.g. in tests).

    `candle_ts` must be the triggering event's own timestamp, never
    wall-clock `datetime.now()` — §7's backtest-safety invariant, the
    same rule every Strategy.evaluate() itself already follows. Passing
    a naive (tz-unaware) datetime raises inside MarketClock, by design
    (MarketClock._now()) — not re-guarded here.

    Assumes `gate_conditions` already passed `validate_gate_conditions()`
    — this function does not re-validate; it trusts the registration-time
    check already ran and raised for anything it couldn't honor."""
    session = gate_conditions.get("session")
    if session == "regular" and not get_market_clock().is_regular_session(candle_ts):
        return False
    return True
