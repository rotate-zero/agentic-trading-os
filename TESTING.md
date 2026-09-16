# Decision-log integrity correction — ordering, headers, CHANGES.md backfill

Documentation-only. No code, tests, or architecture docs touched.

## What changed

1. **`docs/decisions/confirmed-decisions.md`** — physically reordered so entries
   read `134 → 135 → 136 → 137 → 138` top to bottom (was
   `134 → 137 → 135 → 136 → 138`). Entries #135 and #136 also had their opening
   line reformatted from `N. **long lead sentence**` to `### N. long lead
   sentence` (a real markdown heading, dropping the now-redundant bold markers),
   matching the `### N. Title` convention already used by #134/#137/#138. No
   other entry's structure changed.
2. **`CHANGES.md`** — replaced with a backfilled entry for decision #138 (was
   still showing #137's entry; #138 never overwrote it). See the note at the top
   of that file for how it was reconstructed.

## What did NOT change

No word of any decision's substance was altered. Verified programmatically:
stripped every entry (`###`/plain numbering, bold markers, `---` rules,
whitespace) from both the original and corrected `confirmed-decisions.md` and
compared per-decision-number, not as one concatenated blob (since reordering
necessarily changes concatenated-string position) — **all five entries (#134–
#138) are identical after stripping structural markup.** The pre-existing
4-space indentation inside several of #135's and #136's internal paragraphs
(present in the original, not something this delivery introduced or corrected)
was left exactly as found — out of this delivery's stated scope.

`docs/decisions/INDEX.md` was not touched — it was already in correct order
(confirmed during the original audit) and required no fix.

## Root-cause note (for the record, not re-litigated here)

Commit timestamps on `confirmed-decisions.md` (via GitHub's own commit history
for that path) show #135 and #136 both merged to `main` before #137 did. #137's
own text was spliced in directly after #134's entry — consistent with that
session writing against a local copy of the file that predated #135/#136's
merges, despite correctly claiming the number 137 for itself. The number was
re-checked before writing; the physical file position was not.

## Verification performed

- Per-entry structural-diff check described above (script-based, not visual).
- `grep -nE "^### [0-9]+\." docs/decisions/confirmed-decisions.md` on the
  corrected file returns exactly `134, 135, 136, 137, 138`, in that order, no
  gaps, no duplicates.
- `diff -rq` against a freshly re-pulled clone of current `main`, confirming the
  only two files that differ anywhere in the repository are
  `docs/decisions/confirmed-decisions.md` and `CHANGES.md`.

## Deliberately not covered

- No new decision-log entry was minted for this correction itself. Project
  convention mints a new entry for corrections to a decision's *substance*; this
  is a structural/packaging fix to entries whose substance is unchanged. Flagged
  for Saqib to decide whether a correction entry is still wanted for the audit
  trail — not added unilaterally here, since assigning a real decision number is
  explicitly reserved for exactly that kind of judgment call.
- The pre-existing 4-space-indent quirk inside #135/#136's internal paragraphs
  (noted above) — unrelated to what was asked, not touched.
