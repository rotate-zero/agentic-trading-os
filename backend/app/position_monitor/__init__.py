"""
Position Monitor-lite (decision slug `position-monitor-lite`). See
docs/architecture/execution-engine-design.md §6.6 and decision #171's own
"needs EX-5, still open, left as a marked extension point, not built" —
this package is that extension point's consumer, named directly there as
"a clearly separate, later increment (Position Monitor-lite's own task)".

Scope, restated precisely (this task's own §3 judgment call): decide WHEN
and WHY a held position should exit — stop, target, or end-of-day flatten
(EX-11's recommendation (a), in-process monitoring) — and latch that
decision so it is never made twice for the same position. That is the
whole job. This package does not decide HOW the exit gets placed: no
event is published, no order is placed, `schemas/events/execution.py` is
untouched, and `execution_engine`/`governor` are never called. Actually
placing a produced `ExitIntent` needs EX-5's answer first (does a
protective exit need a fresh Governor-class decision, or just a
reduce-only guard? — still open, unlike EX-11, explicitly NOT proceeding
on a recommendation per §7.1) — that is a separate, later task's job,
once EX-5 is actually confirmed by Saqib.

`ports.py` — this module's own narrow, frozen-dataclass read Protocol
(`PositionReader.get_open_positions()`), deliberately not importing or
reusing `portfolio_state.snapshot.PortfolioSnapshot` or
`governor.ports.PortfolioStateReader` directly (see that module's own
docstring for the full reasoning). `portfolio_state_reader.py` is the
concrete read-only adapter over the real Portfolio State snapshot.

`engine.py` — `PositionMonitor`, the same subscribe -> own queue ->
worker shape every engine in this codebase uses (decision #84's
pattern). Subscribes to `PriceUpdated`/`CandleClosed` (both already
published, normal lane, no new event/schema). No module-level singleton
getter ships here (unlike governor's `get_authorizer_stub()` or
execution_engine's `get_execution_engine()`); `main.py` wiring remains
outside the adapter task.
"""
