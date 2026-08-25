# Decisions

Two files with different jobs, plus (as of decision #79) an index and an archive layer on top of one of them:

- **[`confirmed-decisions.md`](./confirmed-decisions.md)** — settled. Each entry is numbered, states the decision, and links to the architecture section that implements it. Append-only: a decision that's later reversed gets a new numbered entry explaining the reversal, rather than an edit that erases the original reasoning.
- **[`future-ideas.md`](./future-ideas.md)** — deliberately not settled. Each entry names what the idea is, why it's deferred (not rejected), and — critically — a concrete **trigger condition** for when it's worth revisiting. Before reviving anything here, check whether the trigger actually occurred; if it hasn't, the honest move is to leave it parked, not to talk yourself into "close enough."

Rule of thumb: if you're arguing about whether to build something, it belongs in `future-ideas.md` until the argument is resolved. Once resolved, the decision (build it, or explicitly don't) moves to `confirmed-decisions.md`.

---

## Why this file structure exists (decision #79, rolled over once more at #80)

At 78 entries, `confirmed-decisions.md` alone was 260KB (~65,000 tokens). Every Claude session on this project fetches the repo and reads the docs before proposing anything — that's a standing project rule, not incidental — so that cost was paid in full at the start of every session regardless of what the session was actually about. Decision #79 split the growing history into three pieces; decision #80 performed the first scheduled rollover once the open file crossed the same size trigger again:

| File | What it holds | Size (as of #80) |
|---|---|---|
| `INDEX.md` | One line per decision, #1–latest, mapped to whichever file has the full text | ~9.7KB |
| `confirmed-decisions.md` | The **open** file — the most recent range of decisions (currently #80 onward). All new decisions get appended here. | starts fresh at #80 |
| `archive/001-060.md` | **Frozen.** Decisions #1–#60, verbatim, never edited again. | ~159KB |
| `archive/061-079.md` | **Frozen.** Decisions #61–#79, verbatim, never edited again. | ~105KB |

Numbering stays global and strictly sequential across every file — which physical file an entry lives in has no bearing on its number.

## For any Claude session working on this project: how to read

1. **Read `INDEX.md` first.** It's a fraction of the cost of the full history and tells you which file has the full text of anything relevant to your task.
2. **Pull full text only for what's actually relevant** — either the open `confirmed-decisions.md` or a specific `archive/*.md` chunk — via targeted `grep`, the same way this project already prefers targeted search over broad reads elsewhere.
3. **To find the current highest decision number**, check `INDEX.md`'s last row. Don't rely on the open file's tail alone — if a rollover (see below) happened without the index being updated in the same change, the open file's last entry won't be the true maximum. If the open file's last entry and `INDEX.md`'s last row disagree, **stop and flag it to Saqib** rather than guessing which one is stale — the same handling this project already uses for any other numbering collision.

## How to write a new decision

1. Determine the next number from `INDEX.md` (see above), cross-checked against the open file's own tail.
2. Append the new entry to whichever file is **currently open** (`confirmed-decisions.md`) — never to a file under `archive/`.
3. **In the same change**, add one new row to `INDEX.md` for the new entry. An unindexed decision defeats the entire point of this structure — this step is not optional and not a follow-up for later.
4. If the decision changes architecture, update `system-design.md` (or the relevant design doc) in the same change too — this was already the rule before #79 and doesn't change.

## How to maintain: when to roll over to a new archive file

Entry length in this project has grown substantially over time (early entries run a few hundred characters; recent ones run several thousand) — so the rollover trigger is **file size, not entry count**.

- After appending a new decision, check the open file's size: `wc -c docs/decisions/confirmed-decisions.md`.
- If it's approaching roughly **100KB (~25,000 tokens)**, freeze it:
  1. Move the current open file to `archive/0XX-0YY.md`, zero-padded to match the existing naming (e.g. `archive/061-095.md`), where XX/YY are the first and last decision numbers it contains.
  2. Start a fresh, empty `confirmed-decisions.md` for entries going forward.
  3. Update `INDEX.md`'s file-location column for every entry in the newly archived range, in the same change.
- Do not pick an arbitrary entry-count cutoff (e.g. "every 50 entries") — the size skew means that stops being a meaningful bound as entries keep getting longer.
