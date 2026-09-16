# CHANGES — decision #142 (architecture doc rollover: strategy-engine-design.md split into four files)

`docs/architecture/strategy-engine-design.md` had grown to 175KB, driven by decisions #139–#141 landing large content into its §7 (Backtest Runner), §10 (Open decisions), and §14–§18 (per-strategy build history). Same trigger decision #79/#80 used for `confirmed-decisions.md`'s own rollover, applied here for the first time to an architecture doc.

## What changed

- **Split at `##` boundaries into four files**, section/D-item numbers unchanged, moved bodies byte-identical to the original:
  - `strategy-engine-design.md` (kept) — §0–§6 (including the complete §5), §8–§9, §11–§13. ~66KB.
  - `backtest-runner-design.md` (new) — §7, whole. ~42.5KB. Matches the existing one-file-per-subsystem convention already used by `daily-levels-design.md`/`scanner-design.md`/`premarket-accumulator-design.md`.
  - `strategy-engine-open-decisions.md` (new) — §10 (D1–D19 table), whole. ~23.5KB. Kept separate from build history — different reading pattern (lookup table vs. long-form narrative).
  - `strategy-engine-build-history.md` (new) — §14–§18, whole. ~45KB.
- Added a "Where content moved" map near the top of `strategy-engine-design.md`, mirroring `INDEX.md`'s own role for the decision log, so old `§7`/`§10`/`§14–18` citations from immutable decision history remain traceable in one hop.
- Updated 21 bare internal self-references inside the retained core file's own kept sections to name the file the content now lives in. References to *other* documents' section numbers were left untouched.
- Updated citations and companion-document lists in the living docs that cited a moved section: `system-design.md`, `trading-intelligence-architecture.md`, `trading-intelligence-overview.md`. `future-ideas.md` and `phase-roadmap.md` needed no changes — their existing citations all named sections that stayed put.
- Left `confirmed-decisions.md`, `INDEX.md`, and `docs/decisions/archive/*.md` untouched — decision content is immutable; the moved-sections map is the intended remedy for anyone following an old citation from there.
- Left ~40 backend/frontend code comments citing section numbers unchanged — out of scope for this docs-only delivery, flagged as a follow-up.

No application code changed. This is a documentation-only reorganization.
