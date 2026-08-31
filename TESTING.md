# TESTING — docs-only change

No application code touched. Two files edited, both under `docs/`:

1. `docs/decisions/future-ideas.md` — new entry **#20, Time-to-Target Estimator
   (Temporal Expectation) & Hypothesis Health**, appended after #19 (Crypto
   Trading), following the file's existing What it is / Why deferred /
   Boundary / Trigger to revisit / Where it would plug in shape. Includes a
   "Visualization companion" subsection for the minion-walk concept, scoped
   as non-blocking and tied to the same entry.

2. `docs/architecture/strategy-engine-design.md` — one-line addition to the
   **Companion documents** header (the doc's existing citation list), adding
   `#20 Time-to-Target Estimator` alongside the already-cited `#5`/`#7`/`#11`
   future-ideas.md entries. No other line in this file changed — the
   confirmed `Opportunity`/`StrategyConfig` schemas (§3/§4) are untouched;
   this idea hasn't graduated from "future idea" to "locked design."

No decision-log entry: nothing was confirmed here, so `confirmed-decisions.md`
and `INDEX.md` are correctly left untouched, per `docs/decisions/README.md`'s
own stated rule that `future-ideas.md` entries aren't numbered decisions.

## How to verify

Since nothing here is executable, verification is a read-through, not a test
run:

```bash
git diff -- docs/decisions/future-ideas.md docs/architecture/strategy-engine-design.md
```

Unzip this delivery directly into the project root (it overlays cleanly onto
the existing `docs/` tree, no new files outside it) and diff against your
working copy before committing.
