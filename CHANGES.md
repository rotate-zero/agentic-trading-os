# CHANGES — pending delivery `ibkr-broker-panel-validation-deferred`

Base commit: `main` @ `3c80afd` ("Backtest panel ibkr real data option"), re-fetched immediately before packaging (no newer commits). **Supersedes the earlier zip `ibkr-broker-panel-validation-harness`; apply only this one.** The script is byte-identical to the earlier one.

## What changed

- **Documented the deferral** of Task 2 (first real-IBKR validation of the broker panel): no paper Gateway/TWS was reachable (two independent checks), and Saqib chose to set the process aside on 2026-09-19.
- `docs/decisions/future-ideas.md`: new section **27** with what it is, why deferred, the two open items awaiting a decision (P5, P1), a concrete trigger, and where it plugs in. No decision number assigned.
- `docs/architecture/ibkr-broker-panel-validation.md`: status set to DEFERRED; new "Deferral record" with evidence, what exists, what is *not* known, and a resume checklist; Results section explicitly empty.
- `backend/README.md`: the ❌ "actual live connection" line stays (nothing was verified) and now points at the deferral, the script, the runbook and future-ideas #27.
- Included unchanged from the earlier delivery: `backend/scripts/check_ibkr_broker_panel.py` (tooling ready for the day the process resumes; it is not run by anything).
- No application code changed. **`backend/app/api/routes/broker.py` is untouched.**

## Not changed, on purpose

- `docs/roadmap/phase-roadmap.md` already says Phase 3 has "two of three connections unverified live"; still accurate.
- No decision-log entry (`confirmed-decisions.md` / `INDEX.md`): a deferral with a trigger belongs in `future-ideas.md`. The results delivery will carry the decision entry.
