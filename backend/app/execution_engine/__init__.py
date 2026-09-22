"""
Execution Engine — entry-order half only (decision execution-authorizer-
and-engine). See docs/architecture/execution-engine-design.md §6.3/§6.4
and decisions #168/#170.

Scope, restated precisely (this task's own scope item 2, read against the
AC ownership list in §6 of the task prompt — flagged as a judgment call,
overridable): consume OrderApproved (critical lane), verify an
authorization-gate match against the governor's committed decision (I2,
AC #19 entry-gate half), mint/verify the deterministic client-order ID
(I10), perform an idempotent ledger insert keyed on it, check the
configured venue supports the order's execution mode (AC #5 venue-refusal
half), and call OrderVenue.place_order(). Fill application (§6.3 steps
6-7: on_order_update handling, dedup, OrderFilled publishing) is NOT
built here — it isn't in this task's owned AC list (no AC #8/#9/#13/#14),
and it couples directly to Portfolio State, which this task's file
boundary forbids touching. `place_order()` IS called, so a venue can
begin processing an order, but nothing in this delivery consumes the
resulting update — that consumer is a clearly separate, later increment
(Position Monitor-lite's own task), same as the reduce-only/exit guard.
"""
