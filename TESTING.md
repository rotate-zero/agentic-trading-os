# TESTING — pending delivery `ibkr-broker-panel-validation-deferred`

**Docs-only deferral record plus the unchanged validation script.** Nothing in this delivery was run against IBKR, and nothing may be read as an IBKR result.

## What changed

See `CHANGES.md`: `docs/decisions/future-ideas.md` (#27), `docs/architecture/ibkr-broker-panel-validation.md` (status, deferral record, resume checklist), `backend/README.md` (pointer on the ❌ line), plus the unchanged `backend/scripts/check_ibkr_broker_panel.py`.

## How to verify

```bash
# the script is byte-identical to the previously stub-tested one
sha256sum backend/scripts/check_ibkr_broker_panel.py
git diff --stat -- backend/app        # expect: empty (no application code changed)
git diff -- backend/app/api/routes/broker.py   # expect: empty
grep -n "^## 27\." docs/decisions/future-ideas.md
```

## What was verified, and how

- The deferral facts come from two checks: a TCP probe from the authoring sandbox (`127.0.0.1:4002` refused) and a local session on Saqib's machine (no listener on 4002, no TWS/Gateway process, endpoint `127.0.0.1:4002`, client ID `1`, worktree clean), as he relayed it. Nothing was re-checked on his machine from here.
- Script logic was previously exercised against a stub backend (real FastAPI routers, Event Bus and `/ws`; fake `IB`): happy path, connect failure, provider-takeover guard, pre-existing session, missing listener, every static guard, zero-ticks FAIL branch, backend/confirmed client-ID mismatch. That validates the script only.

## What isn't covered

- Everything about real IBKR: adapter behaviour through the routes, tick flow, Broker panel rendering, predictions P1–P7.
- Backend pytest and `tsc -b` were not run: no application or frontend code changed.

## Manual merge notes

- `docs/decisions/future-ideas.md` and `backend/README.md` are **full-file copies from `main` @ `3c80afd`** with one addition/edit each. If a parallel session touched either file since, diff before copying rather than overwriting.
- Section number **27** in `future-ideas.md` is the next free one at `3c80afd`; if another session also added a section, renumber this one (nothing else references the number except this delivery's own docs: `README.md`, the runbook, `CHANGES.md`).
- Repo-root `CHANGES.md` and `TESTING.md` are per-delivery replacements and collide with any other pending delivery's; keep whichever lands last or merge the sections.
- If the earlier `ibkr-broker-panel-validation-harness` zip was already applied, this one overwrites the same script/doc paths harmlessly.
