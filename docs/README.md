# Documentation

`docs/` is the authoritative location for all architecture and design documentation for this project. Implementation code lives under `backend/` and `frontend/`; it does not carry design reasoning of its own — that reasoning lives here, and code comments should point back to the relevant doc rather than re-explaining it.

**The rule that matters most:** any change that affects architecture, a confirmed decision, a phase's scope, or an API contract updates the relevant document below *as part of the same change* — not as a follow-up task, and not left for someone to reverse-engineer from a diff later.

## Layout

| Folder | Purpose |
|---|---|
| [`architecture/`](./architecture/) | The two living architecture documents — `system-design.md` (how it's built) and `trading-intelligence-architecture.md` (how it thinks). These describe the system *as it's meant to be built*, not a decision log or a plan. |
| [`decisions/`](./decisions/) | `confirmed-decisions.md` (settled, numbered, append-only) and `future-ideas.md` (deliberately deferred, with trigger conditions). See that folder's own `README.md` for the convention. |
| [`roadmap/`](./roadmap/) | `phase-roadmap.md` — the phased delivery plan, deliverables, exit criteria, and a living status per phase. |
| [`diagrams/`](./diagrams/) | Standalone rendered diagrams (Mermaid/SVG), for anything too complex for the inline ASCII diagrams already in `architecture/`. Currently just a placeholder. |
| [`api/`](./api/) | External-facing REST/WebSocket contracts (as opposed to the internal Event Bus contracts in `architecture/system-design.md` §10). Currently just a placeholder. |

## Reading order

For a new contributor (or a new context window): `architecture/system-design.md` → `architecture/trading-intelligence-architecture.md` → `decisions/confirmed-decisions.md` → `roadmap/phase-roadmap.md`. Check `decisions/future-ideas.md` before proposing something that "feels missing" — it's probably already been considered and deliberately parked, with a reason and a trigger condition attached.
