# API Contracts

Currently empty. This is the home for `api-contracts.md` (referenced in `../architecture/system-design.md` §8's folder structure as "generated/maintained separately") — the external-facing contract: REST route shapes under `backend/app/api/routes/`, and the WebSocket channel/envelope shapes from `backend/app/api/websocket/channels.py`.

**Distinction from `../architecture/system-design.md` §10:** §10 documents the *internal* Event Bus payload schemas (module-to-module, in-process). This folder documents the *external* contract (frontend-to-backend, or any future external client-to-backend). They're related — WebSocket channel payloads are often a re-published subset of Event Bus events — but they're not the same contract, and a change to one doesn't necessarily require a change to the other.

Populate this once the WebSocket Gateway and REST routes stabilize enough to document without immediately going stale — likely toward the end of Phase 2.
