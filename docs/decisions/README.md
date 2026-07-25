# Decisions

Two files, two different jobs:

- **[`confirmed-decisions.md`](./confirmed-decisions.md)** — settled. Each entry is numbered, states the decision, and links to the architecture section that implements it. Append-only: a decision that's later reversed gets a new numbered entry explaining the reversal, rather than an edit that erases the original reasoning.
- **[`future-ideas.md`](./future-ideas.md)** — deliberately not settled. Each entry names what the idea is, why it's deferred (not rejected), and — critically — a concrete **trigger condition** for when it's worth revisiting. Before reviving anything here, check whether the trigger actually occurred; if it hasn't, the honest move is to leave it parked, not to talk yourself into "close enough."

Rule of thumb: if you're arguing about whether to build something, it belongs in `future-ideas.md` until the argument is resolved. Once resolved, the decision (build it, or explicitly don't) moves to `confirmed-decisions.md`.
