## 8. Strategy Engine

Each strategy answers one question and produces an **Opportunity Object**, never a bare BUY/SELL:

```
NVDA
  Momentum:  92
  ORB:       80
  Pullback:  10
```
or, expanded:
```
AAPL — ORB
  Confidence:  82
  Entry:       220.10
  Stop:        219.30
```

No trade yet. Only opportunity. Strategies don't run on a shared clock — each declares its own trigger (every candle, only after 9:35, only after a volume spike, every tick) via the Strategy Scheduler, so timing logic isn't duplicated across strategies (see `system-design.md` §4.7's Scanner cadence for the same pattern applied one layer up).

Planned initial strategy set: ORB, Momentum, First Pullback, VWAP, Gap, Reversal, Volume Spike. News is listed as a future addition, not a v1 strategy.

**Important:** strategy detection is not classification. Nothing decides "this is Momentum, not ORB" — every eligible strategy evaluates independently, several can fire on the same symbol at once (as in the NVDA example above), and it's Opportunity Engine + Decision Engine (§9, §10) that arbitrate. Each strategy only has to answer its own question correctly; it never needs to know what the others think.

---

### 8.1 Detection Process — one shape, filled in differently per pattern

Every strategy's `evaluate()` goes through the same four stages, whatever pattern it encodes:

```
GATE     — cheap, binary preconditions. Is it even worth checking the rest?
MATCH    — the structural pattern test against Market State + Features + Context
SCORE    — how strongly does it match? → Opportunity.confidence
PROPOSE  — the pattern's own implied entry/stop/target → suggested_entry/stop/target
```

**Gate** uses the same `trigger: ScheduleTrigger` mechanism already in the `Strategy` interface (`system-design.md` §4.8) — time-based (`after_time`), event-based (`on_event`), or every candle. This is what keeps expensive pattern logic from running on every symbol every tick.

**Match** is a condition tree over signals Feature Engine and Market State Engine already publish — no new indicators required for any strategy in the planned set. This is where the actual pattern logic lives.

**Score** turns match strength into a 0–100 confidence, not a flat yes/no — "how far past the breakout," "how much volume expansion," "how strong is the level" all feed in. This is what makes ranking (ORB:92 vs Pullback:10) meaningful instead of arbitrary.

**Propose** — see 8.2.

---

### 8.2 Two-Tier Stop/Target

The `suggested_entry/suggested_stop/suggested_target` on an Opportunity Object are **not** the final trade parameters — they're the pattern's own cheap, structural implication, good enough to rank against other opportunities (e.g. ORB's stop is naturally "other side of the opening range"; Reversal's stop is naturally "beyond the level being reversed at").

**Trade Planning Engine (§11)** does the expensive version — fractional Kelly sizing, R multiple, scaling, trailing stop rules — and only for the single opportunity Decision Engine actually selected. Strategies should never try to produce final, sized trade parameters themselves; that work happens once, downstream, for the winner only.

---

### 8.3 Pattern Reference

Gate and Match sketches for each strategy in the planned set. These are starting points for implementation, not final thresholds.

| Strategy | Gate | Match (core condition) | Structural stop |
|---|---|---|---|
| **ORB** | Time window after open (e.g. 09:30–09:45) | Price crosses opening-range high/low + relative volume confirms + Market State trend agrees with breakout direction | Other side of the opening range |
| **Momentum** | Session active, opening range established | Trend strength increasing across timeframes (5m and 15m slope agree), relative volume elevated, no deep pullbacks breaking structure | Most recent swing point, or N×ATR (no clean structural level like ORB has) |
| **First Pullback** | Established trend exists (Trend.duration above threshold) | Price pulls back to EMA9/EMA21 or VWAP without breaking trend structure (higher low holds), volume contracts during pullback then re-expands | Below the pullback low / beyond the MA being tested |
| **VWAP** | Enough session volume for VWAP to be meaningful (skip the first few minutes) | Price crosses VWAP with volume confirmation, direction agrees with Market State trend/participation (don't fade VWAP against trend) | Other side of VWAP |
| **Gap** | Near/at open, using premarket vs. prior-close features | Gap magnitude exceeds threshold; then two sub-variants split on early participation: **Gap-and-Go** (continuation through premarket high/low) vs **Gap Fade** (reversal toward gap-fill level) | Beyond premarket high/low (Go) or beyond the gap-fill level (Fade) |
| **Reversal** | Established move exists (Trend.duration above threshold) | Extended move (session % change beyond threshold) + Participation flips + price at a level with meaningful strength + momentum diverging from price (regression slope improving while price still extending) | Beyond the level being reversed at |
| **Volume Spike** | None — evaluated continuously via `on_event` whenever relative volume crosses threshold | Relative volume spike + directional price move on that volume + spike sustained over N bars, not a single print | ATR-based only — no clean structural level, the one strategy here that leans on Trade Planning Engine's stop logic rather than proposing a strong one itself |

Gap is worth flagging as the one pattern that isn't a single condition tree — it has two structurally opposite outcomes (continuation vs. fade) sharing one gate, decided by early post-open behavior. Worth deciding during implementation whether that's one `Strategy` class with an internal branch, or two separate strategies (`GapGo`, `GapFade`) sharing a base class — the latter probably fits your existing "each strategy is narrow and independently scored" philosophy better.

**Open item:** "Fallen Angel" isn't in the planned set above and needs a definition before it can be added — in particular whether it's a genuinely new pattern (likely a multi-day/weekly-lookback setup, reading from the same daily-bar source as Daily Levels, not intraday `FeatureSet` fields) or a longer-lookback variant of Reversal.

---
