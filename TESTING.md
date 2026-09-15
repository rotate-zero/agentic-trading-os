# TESTING.md — Stale build-status documentation audit (expanded pass)

## What this delivery is

A documentation-only correction pass. **Zero application code changed** —
confirmed by `diff -rq` against a freshly re-pulled clone of `main`, both
immediately before writing anything and again immediately before
packaging: only the four files listed below differ from an untouched
clone. Nothing under `backend/` or `frontend/` was touched. No decision-log
entry was created (see "Decision log" below for why).

This expands the originally-scoped 6 claims to 9, after a second Claude
instance's audit found 3 additional instances of the exact same
underlying staleness while re-verifying the original 6 against current
`main` and real code. All 9 are corrected in this one delivery, per
explicit instruction not to leave any confirmed-false implementation-status
statement for a separate pass.

## Files changed (4)

- `docs/architecture/system-design.md`
- `docs/architecture/daily-levels-design.md`
- `docs/architecture/strategy-engine-design.md`
- `docs/architecture/trading-intelligence-architecture.md`

**Explicitly left unchanged, confirmed already accurate (byte-identical to
the untouched clone):** `feature-engine-indicator-expansion.md`,
`feature-engine-chart-migration.md`, `scanner-design.md`.

## The 9 corrections

### Originally-scoped (6)

**1. `system-design.md` — top "Companion documents" blurb, Daily Levels.**
Was: *"the stage-by-stage plan for the Daily Levels support/resistance
clustering indicator (confirmed decision #59), direction locked but not
yet built."* Now: *"...all four stages built (decisions #60–#65)."*
Evidence: `backend/app/feature_engine/indicators/daily_levels.py` and
`backend/app/models/daily_levels.py` are real code; decisions #60 (Stage
1: backend calculation), #63 (Stage 2: identity/persistence, migration
0003), #64 (Stage 3: Level Interaction integration), #61 (Stage 4:
frontend rendering) — all four checked off in this same document's own
§9 Staged plan, which was already accurate before this delivery.

**2. `system-design.md` — same blurb, Feature Engine indicator
expansion.** Was: *"the stage-by-stage plan for ATR/Session Change/Gap/
Regression/KAMA (confirmed decision #67), direction locked but not yet
built."* Now: *"...all five families built (decisions #67–#71)."*
Evidence: `backend/app/feature_engine/indicators/{atr,gap,session_change,
regression,kama}.py` all exist; `feature-engine-indicator-expansion.md`'s
own status header already correctly read "All five feature families now
built and tested" — this document's pointer to it just hadn't been
updated to match.

**3. `system-design.md` — body text, Feature Engine implementation-status
paragraph (~line 157).** Was: *"A new Daily Levels indicator ... has its
direction locked (decision #59) but is not yet built."* This is a
**second, separate location** with the same staleness as #1 — a prior
audit of this same document had assumed this body paragraph was already
correct (it correctly describes ATR/Gap/Session Change/Regression/KAMA/
RVOL as built) but missed that its own Daily Levels sentence, a few
clauses later in the same paragraph, was not. Now: *"Daily Levels ... is
built — all four stages complete (decisions #60–#65)"* — same evidence
as #1.

**4. `daily-levels-design.md` — the document's own `Status:` header.**
Was: *"Stage 0 confirmed ... **No application code has been written
yet** — this document and decision #59 are the direction lock."* This
directly contradicted the same document's own §9 (Staged plan), a few
dozen lines below, which already had all four stages checked off with
full build detail, test counts, and decision citations. Now: *"Built —
all four stages complete (decisions #60–#65) ... Originally Stage 0
only ..."* — the original review-process narrative (three-way review,
Saqib + Claude + Grok + ChatGPT) is preserved, relabeled as the
document's original Stage 0 state.

**5. `strategy-engine-design.md` — the document's own `Status:` header.**
Was: *"Stage 0 confirmed ... **No application code has been written
yet** — `strategy_engine/` doesn't exist anywhere in the repo."* Directly
contradicted by the filesystem: `backend/app/strategy_engine/` has 7 real
strategy files plus `base_strategy.py`/`scheduler.py`/`gate_conditions.py`/
`scoring_utils.py`/`level_touch_tracking.py` — verified directly
(`find`/`ls`), not inferred. Also contradicted by this same document's
own later sections (§12 Staged plan, §14–§18 build walkthroughs),
already accurate. **Authoritative decisions establishing Strategy
Engine's current built scope, identified directly from the decision log
(not from memory, and not from Backtest Runner decisions, which are a
separately-scoped extension of this same plan and are cited separately
below):**
  - #99 — Stage 1: `base_strategy.py` + `orb_strategy.py` (ORB, first
    strategy)
  - #104 — Gap Strategy
  - #105 — Volume Spike Strategy
  - #109 — First Pullback
  - #110 — Reversal
  - #113 — Momentum + VWAP, explicitly "closing out the full planned
    set" (all 7 v1 strategies)
  - #114, #115, #116 — Strategy Scheduler (both tracks), fully built and
    verified end-to-end
  - #117 — declarative `gate_conditions` enforcement, wired into the
    Scheduler

  The corrected header cites exactly these, states plainly that Decision
  Engine's and Governor's evidence-informed logic (§6) remain target-shape
  only (confirmed: no `decision_engine`/`governor` module exists under
  `backend/app/`), and points to §7 for Backtest Runner's own separately-
  cited build account rather than re-deriving or guessing those decision
  numbers here.

**6. `trading-intelligence-architecture.md` — top "Companion documents"
blurb, Strategy Engine.** Was: *"§8–14's direction lock (decisions
#87–88): ... Concept only, no application code yet."* Directly
contradicted by this same document's own §8 body text (~line 302), which
already correctly reads *"Internal design locked; all seven planned v1
strategies are now built (decisions #99, #104, #105, #109, #110,
#113)"* — the top blurb just hadn't been updated to match. Now points to
the corrected `strategy-engine-design.md` status header rather than
duplicating the citation list a third time.

### Found via the required sweep, same underlying staleness, additional
### locations (3)

**7. `trading-intelligence-architecture.md` §14, ~line 410.** Was:
*"Schema direction-locked, not yet built: an atomic `StrategyOutcome`
record per closed trade ... persists to `strategy_outcomes`."* Directly
contradicted by decision #120 (`Performance Intelligence's persistence
layer built — strategy_outcomes + backtests tables ... migration,
ORM as StrategyOutcomeRecord/BacktestRunRecord`), confirmed against real
code: `backend/alembic/versions/0008_strategy_outcomes_and_backtests.py`,
`StrategyOutcomeRecord`/`BacktestRunRecord` in
`backend/app/models/trading_intelligence.py`. Corrected precisely, not
rounded to a flat "built": the schema/write path exist, real
backtest-derived rows exist since decision #128, but there are zero
live-trading rows since no Execution Engine/Position Monitor exists yet
to call the write path for a real trade.

**8. `system-design.md` §4.13 Database, ~line 280.** Was: *"`strategy_
outcomes` (... no migration yet — the table itself is still not
built ...)."* Same underlying fact as #7, second file. Corrected with the
same precision (built, real backtest rows exist, zero live rows yet)
and the same decision #120/#128 citations.

**9. `strategy-engine-design.md` §8, ~line 856.** Was: *"Market State
Engine (where Participation — buyer/seller control — would live) isn't
built."* Directly contradicted by decisions #92/#93/#96/#97 (Market
State Engine built) — already correctly described as built elsewhere in
`system-design.md` itself. The genuinely-still-true surrounding point
("nothing computed today is sub-1-minute") was preserved and given its
own citation (decision #103 — Market State Engine's per-symbol recompute
is keyed to 1m `FeaturesUpdated` arrivals specifically, never faster);
only the false "isn't built" clause was corrected.

## Intentionally retained historical/still-accurate wording (not
## changed, checked directly, not assumed)

- **Stage 0 checklist entries** in `daily-levels-design.md` §9,
  `strategy-engine-design.md` §12, and `feature-engine-indicator-
  expansion.md` (`"Stage 0 — Lock the direction in writing (no
  application code)"`) — these are `[x]`-checked historical stage
  entries describing what Stage 0 itself consisted of, immediately
  followed by every later stage also checked off. Left alone: accurate,
  clearly historical, not a current-status claim.
- **`trading-intelligence-architecture.md` ~line 191** ("`MarketState`
  doesn't exist yet ... M2 isn't built") — explicitly labeled "M1 build
  note (decision #92)" describing that milestone's temporary state, with
  its own "goes back on once M2 lands" framing. Already properly dated
  and qualified. Left alone.
- **`trading-intelligence-architecture.md` ~line 320** (Decision
  Engine's evidence-informed arbitration, "direction-locked, not yet
  built") — still genuinely accurate. Confirmed directly: no
  `decision_engine` module exists under `backend/app/`. Left alone.
- **`trading-intelligence-architecture.md` ~line 435** ("WorldView
  (still not built)") — still genuinely accurate, no such module exists.
  Left alone.
- **`strategy-engine-design.md` ~line 307** ("Automatic reweighting ...
  isn't built now") — still genuinely accurate; only human-reviewed
  promotion exists. Properly framed as real, triggerable future work.
  Left alone.
- **`strategy-engine-design.md` §7 (~lines 424–540)**, the Backtest
  Runner section's original prospective design narrative and its own
  "As-built note (decision #128)"/"(decision #130)" annotations — this
  section was already confirmed accurate in a prior pass and is not
  touched here; it's the source cited by fix #5 above rather than
  duplicated.
- **`feature-engine-indicator-expansion.md`, `feature-engine-chart-
  migration.md`, `scanner-design.md`** — all three confirmed already
  accurate (own status headers correctly describe current build state,
  including `feature-engine-chart-migration.md`'s explicit remaining-work
  list and `scanner-design.md`'s sequential revision-note structure).
  Byte-identical to the untouched clone; not part of this delivery.

## Documentation/link validation performed

- Every markdown companion-doc link touched by this delivery
  (`` [`name.md`](./name.md) `` syntax) re-checked for balanced
  brackets/parens after editing — all intact.
- Every file referenced by a companion-doc link in the two edited
  top-of-document blurbs (`system-design.md`, `trading-intelligence-
  architecture.md`) confirmed to actually exist on disk at the
  referenced path.
- No new links added or removed — only surrounding prose/citations
  changed.

## No application code changed — confirmation

`diff -rq` against a freshly re-pulled clone of `main`, run twice (once
immediately before editing, once immediately before packaging this
delivery): the only differences in both runs are the 4 files listed
above plus this file. Nothing under `backend/` or `frontend/` appears in
either diff.

## Decision log

**No new decision-log entry was created.** Re-checked the protocol in
`docs/decisions/README.md` directly rather than assuming one was needed:
its "Rule of thumb" ties `confirmed-decisions.md` entries to actual
settled decisions (arguments resolved, work done), and its own
established practice — confirmed by reading real prior entries, not
assumed — is to fold a stale-caption correction into a decision entry
that's *also* doing substantive work as a side note (decision #98:
"`system-design.md` §4.8's stale 'not yet built' caption for Market
State/Context corrected"; decision #102: "`premarket-accumulator-
design.md`'s stale 'DRAFT, no code' status line ... corrected in this
same change"; decision #119: "Also fixes a stale §13 line claiming First
Pullback/Reversal were unbuilt") — never as a standalone numbered entry
for the correction alone. This delivery does no code work to attach such
a note to, so no entry was created, per explicit instruction not to
create one unless the protocol requires it. Decision-log tail re-checked
against a fresh clone both before and after this delivery: still `#137`,
unchanged, confirming this delivery didn't need to account for any
parallel-session numbering collision either.
