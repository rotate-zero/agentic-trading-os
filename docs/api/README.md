# API Contracts

Currently empty. This is the home for `api-contracts.md` (referenced in `../architecture/system-design.md` §8's folder structure as "generated/maintained separately") — the external-facing contract: REST route shapes under `backend/app/api/routes/`, and the WebSocket channel/envelope shapes from `backend/app/api/websocket/channels.py`.

**Distinction from `../architecture/system-design.md` §10:** §10 documents the *internal* Event Bus payload schemas (module-to-module, in-process). This folder documents the *external* contract (frontend-to-backend, or any future external client-to-backend). They're related — WebSocket channel payloads are often a re-published subset of Event Bus events — but they're not the same contract, and a change to one doesn't necessarily require a change to the other.

Populate this once the WebSocket Gateway and REST routes stabilize enough to document without immediately going stale — likely toward the end of Phase 2.

## Route contracts currently documented in architecture documents

This folder is still not populated as a full contract. Until it is, route shapes live beside the module that owns them. Read-only observation routes:

- `GET /intelligence/candidate-observation` — the C2 observation-only candidate snapshot (availability states, counts, bounded candidate rows with truncation, freshness, reset and diagnostics; no query parameters). Full contract: `../architecture/trading-intelligence-architecture.md` §19.2, "C2 observation status API and Execution-panel section". It exposes C2 observation only; it does not activate D1, D2 or I1 entry cutover.
