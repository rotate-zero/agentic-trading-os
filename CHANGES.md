# CHANGES — decision #164: documentation status synchronization

## Current delivery

Documentation-only synchronization of the two living status surfaces that had
fallen behind the implementation and decision record. No architecture or
product decision changes, and no backend or frontend files change.

- `docs/roadmap/phase-roadmap.md` — updates only `Status (living)`: refreshes
  Phase 2–4 facts, replaces the false Phase 5–6 “not started” statement with
  verified built/partial/not-started boundaries, records that the Phase 4
  100-symbol/no-dropped-ticks exit criterion is not demonstrated, and adds the
  delivery's single plain-text status diagram.
- `docs/architecture/scanner-design.md` — changes only the header `Status` line
  to distinguish the real on-demand scanner/universe/UI implementation from
  the continuous cadence, promotion, discovery, and spread work that remains
  unbuilt. The `DRAFT` label stays unchanged.
- `docs/decisions/INDEX.md` — removes the stale hardcoded upper bound from the
  introduction and adds this delivery's row at final numbering.
- `docs/decisions/confirmed-decisions.md` — appends one documentation-sync
  decision; existing entries remain immutable.
- `TESTING.md` — fresh task-specific evidence, collision, continuity, link,
  footprint, and archive verification record.

Read-only drift findings outside this exact boundary are reported in
`TESTING.md` and the new decision entry; none were corrected here.

## Boundary

Exactly six files change: this file, `TESTING.md`, the roadmap, the scanner
design header, and the two live decision-log files. No other documentation,
application code, test code, migration, archive, Git history, or existing
decision content changes.
