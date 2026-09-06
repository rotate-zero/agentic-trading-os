# Gap and Volume Spike Strategies — built against the real base_strategy.py

Copy this into your repo root, overwriting the existing path — replaces
the previous drop note (the VWAP MATCH/SCORE/PROPOSE review). That work
was a review of a track's own existing file; this is new code, the
second and third strategies built against ORB's real, working
`base_strategy.py` interface (decision #99) — not a stub, unlike the
Momentum/VWAP review's own scope note.

## What's new

**Two new strategies, `gap_strategy.py` and `volume_spike_strategy.py`,**
delivered together (decisions #104/#105). Both follow the exact
GATE/MATCH/SCORE/PROPOSE anatomy `orb_strategy.py` established, read
`market_state`/`features` directly (never re-deriving what Market State
or Feature Engine already computed), and were built and verified against
the real `Strategy`/`StrategyConfig`/`Opportunity` classes — no stub, no
guessed interface.

**Gap** answers: has today's opening gap held, or already been given
back? Reads `gap_pct`/`gap_dollars` straight off `FeaturesUpdated`
(exactly the boundary `trading-intelligence-architecture.md` §5 already
ruled Gap% belongs to Feature Engine, not Context) and reconstructs the
regular-session open price algebraically (`pdc + gap_dollars`) rather
than tracking a second piece of state for a value Feature Engine already
effectively carries. Fires at most once per symbol per day — a gap's
direction is fixed by `gap_pct`'s own sign, so unlike ORB there's never
a second, opposite direction to test for a reversal.

**Volume Spike** answers: did this candle print unusually heavy volume,
and which way did the market move on it? No engine anywhere in this
codebase computes a per-candle volume baseline (`rvol` is a day-level,
time-of-day-normalized proxy — a different, coarser claim), so this
strategy keeps a small private rolling window of each symbol's own
recent 1m volumes, the same "own private state" precedent ORB's opening
range already established. A single per-symbol cooldown (not a
once-per-day cap, not per-direction) stops it from re-firing on the very
next candle of the same still-elevated move, while still allowing
several genuinely independent spikes across one session. Also corrects
`base_strategy.py`'s own illustrative `on_event("VolumeSpike")` naming —
no such event exists anywhere on the bus — to `every_candle("1m")`.

## Why these two, and why now

Saqib's own planned v1 strategy set (`trading-intelligence-architecture.md`
§8) names seven: ORB, Momentum, First Pullback, VWAP, Gap, Reversal,
Volume Spike. ORB is built; Momentum/VWAP are being rebuilt fresh on a
separate track. This drop picks up two of the three remaining un-started
strategies, built directly against the real interface ORB proved out —
no guessed assumptions to reconcile later the way the discarded
Momentum/VWAP draft needed.

## A documentation-integrity fix, found and corrected in the same change

While appending decisions #104/#105 to `confirmed-decisions.md`, its own
tail no longer matched either new entry — a signal something was already
wrong, not that this session had broken anything new. Traced it directly:
decisions #98 and #99's own closing "Where it's built"/"Verified"
paragraphs were sitting, verbatim, stranded at the very end of the file,
separated from their actual entries (root cause undetermined — no
existing decision claims responsibility for the move). Restored both
paragraphs to their rightful entries — moved, not reworded — and logged
the fix itself as decision #106, since leaving it broken (now with two
more entries sitting in front of the orphaned block) would only compound
the confusion for the next session that reads this file.

The same change rolled `confirmed-decisions.md` over per its own
size-triggered maintenance rule (README.md) — it had crossed the ~100KB
trigger once #104/#105 landed. Decisions #91–#106 are now archived to
`docs/decisions/archive/091-106.md`; the open file starts fresh at #107.

## Files changed

- `backend/app/strategy_engine/gap_strategy.py` — new.
- `backend/app/strategy_engine/volume_spike_strategy.py` — new.
- `backend/tests/test_gap_strategy.py` — new, 15 tests.
- `backend/tests/test_volume_spike_strategy.py` — new, 18 tests.
- `docs/decisions/confirmed-decisions.md` — decisions #104, #105, #106
  appended, then rolled over (now holds #107 onward, currently empty).
- `docs/decisions/archive/091-106.md` — new, frozen archive chunk
  (decisions #91–#106, with #98/#99's paragraphs restored to place).
- `docs/decisions/INDEX.md` — rows added for #104–#106; rows #91–#106's
  file-location column repointed to the new archive file.
- `docs/architecture/trading-intelligence-architecture.md` §8 — updated
  to name Gap/Volume Spike as built alongside ORB, Momentum/VWAP as
  in-progress on a separate track, First Pullback/Reversal as unbuilt.
- `docs/architecture/strategy-engine-design.md` — new §15 (Gap/Volume
  Spike build account, with ASCII flow diagrams for both), §12's staged
  plan updated to check off this work.

## Verified

Both strategies' tests are deliberately DB-free, same posture as
`orb_strategy.py`'s own — pure Python objects (`FeatureSet`/
`MarketState`/`ContextChanged`), real `MarketClock`-anchored ET
timestamps, no Postgres needed. Ran in a fresh venv against
`backend/requirements.txt` in this sandbox (no local Postgres
available here, same standing limitation every DB-gated file in this
project's decision log already carries):

- `pytest tests/test_gap_strategy.py tests/test_volume_spike_strategy.py`
  — 33/33 passed.
- Full existing backend suite, before and after this change: identical
  40 pre-existing DB-connectivity failures, 91 skipped, both unchanged;
  314 passed (281 baseline + these 33 new), zero regressions.
- Sanity-checked `test_base_strategy.py`/`test_orb_strategy.py` still
  pass unmodified against the same run (27/27) — no collateral damage
  to the strategy this work builds alongside.

**Not verified:** against a real local Postgres (none available in this
session) — though neither new strategy file touches the database at
all, unlike files where that caveat means an actual untested code path.

## Next

- **Saqib's own local Postgres run** is still the first real DB-backed
  confirmation for this drop, same standing note every strategy-track
  delivery so far has carried — though again, nothing here touches the
  DB.
- **Momentum and VWAP** remain on their own separate track, being
  rebuilt fresh against the real interface — this drop doesn't touch
  either.
- **First Pullback and Reversal** are the two remaining strategies from
  the planned v1 set, still unbuilt — worth a fresh thread whenever
  that's next in line.
- If the documentation-integrity fix above raises questions about how
  those two paragraphs got separated from their entries in the first
  place, flagging that here since this drop couldn't determine a root
  cause — worth keeping an eye out for whether it recurs.
