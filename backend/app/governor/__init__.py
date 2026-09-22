"""
Governor — the authorizer stub (decision #171).
See docs/architecture/execution-engine-design.md §6.2 and decisions
#168/#170. Subscribes to OpportunityCreated, runs the fixed rule pipeline
(rules.py) against a fresh read of Portfolio State / MarketClock / Settings
every time (I6's per-decision fail-closed layer — nothing is cached across
decisions), and publishes TradePlanned -> GovernorDecision -> OrderApproved
or PlanRejected.

Deliberately named ``governor`` (matching the design doc's own vocabulary
and system-design.md's Governor component), not ``authorizer`` — "stub"
describes THIS delivery's scope (fixed-limits, no learned sizing, no
manual-trading path), not a different, permanent module name.
"""
