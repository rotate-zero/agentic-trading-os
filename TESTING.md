# TESTING.md — Decisions #90 (corrected) + #91 delivery

## Scope

Documentation only, same as every delivery since decision #87. No
application code exists in `strategy_engine/`, Market State, or Context
Engine yet — nothing to run against real Postgres, no `pytest`/`tsc -b`/
`vite build`.

## Important: this replaces the previous #90, not layers on top of it

The prior local #90 draft (Context/Fundamentals only, with two "open
question" paragraphs) was confirmed never pushed to GitHub. It's been
fully rewritten in place — same decision number, corrected content — not
kept as history and superseded by a new number. `confirmed-decisions.md`
and `INDEX.md` both reflect the corrected #90 only; nothing from the old
draft's "open question" framing survives as still-open.

## What changed

- `docs/architecture/trading-intelligence-architecture.md`:
  - §2: new four-part governing rule appended (decision #91)
  - §4: full rewrite — score-first representation, new intro diagram,
    per-symbol dimension list corrected (`Market breadth` removed,
    `Acceleration` promoted), new Cross-symbol dimensions subsection with
    `CrossSymbolState` schema (SPY/QQQ/IWM pulled forward from Phase 5),
    Implementation note revised to flag its own supersession of the old
    restart-rebuild decision, composite-state persistence resolved
    (sentinel row, no new table)
  - §5: boundary rule stated explicitly at the top; `GapProvider`,
    `LevelsProvider`, `VolatilityRegimeProvider` removed from the v1
    provider list; `SectorCorrelationProvider` split (static → already on
    `symbol_fundamentals`, dynamic → deferred to §4); `NewsFlagProvider`'s
    output widened from a bare boolean to a small derived-field group;
    `symbol_fundamentals` schema updated (`market_cap` refresh split from
    `profile_updated_at`); stale `VolatilityRegimeProvider`/`breadth`
    mentions in the refresh-cadence paragraph removed
- `docs/architecture/strategy-engine-design.md` — §5: `market_state_at_entry`
  comment updated to point at the new score-based shape (cross-reference
  only, no schema change)
- `docs/decisions/confirmed-decisions.md` — #90 replaced in full, #91
  appended
- `docs/decisions/INDEX.md` — #90 row replaced, #91 row appended
- `docs/decisions/future-ideas.md` — entries #22 (score→band classification),
  #23 (full market breadth), #24 (sector ETF relative strength) appended

## Verification performed

- `grep -rn "GapProvider|LevelsProvider|VolatilityRegimeProvider|
  SectorCorrelationProvider" docs/architecture/*.md` — every remaining hit
  is inside the two paragraphs explaining why each was cut, not residual
  usage anywhere else in either document.
- `grep -n "Market breadth" trading-intelligence-architecture.md` — exactly
  one hit, in the sentence explaining its removal from the per-symbol list;
  confirmed it no longer appears in the dimension list itself.
- `grep -c "decision #90|decision #91"` across every architecture doc and
  future-ideas.md — confirms both are referenced everywhere they should be
  (13 hits in `trading-intelligence-architecture.md`, 6 across the three
  new future-ideas entries) and nowhere they shouldn't (0 in unrelated
  architecture docs).
- `grep -c "^## "` on `future-ideas.md` — 24, confirming #22–#24 landed as
  distinct entries and nothing got merged or dropped mid-edit.
- Manual read-through of the new §4 against `strategy-engine-design.md`
  §5's `market_state_at_entry`/`_at_exit` fields — confirmed the score
  shape and the `StrategyOutcome` fields that will eventually hold it are
  consistent, not just individually correct.

## Two things this delivery deliberately leaves open (not oversights)

- Whether the original restart-rebuild-from-history decision
  (`trading-intelligence-architecture.md` §4, Implementation note) still
  applies once bands actually get built — flagged explicitly in both §4
  and decision #91 as needing a real re-look, not assumed to carry over
  unchanged from before score-first existed.
- Finnhub's free-tier limits on `/stock/profile2`,
  `/stock/financials-reported`, and `/calendar/earnings` — still need the
  empirical spike flagged in decision #90, not assumed correct from
  documentation.

## Not done (same as always)

Live click-through / doc rendering check — left to Saqib.
