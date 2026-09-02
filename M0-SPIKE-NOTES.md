# M0 Spike Notes — Market State + Context Engine pre-build verification

Covers the M0 task list from decisions #90/#91: "Pre-build verification,
nothing coded yet." Nothing in this delivery is production code — two
standalone scripts and this notes file. No decision-log entry has been
added; that only makes sense once you've run these against real keys and
have real numbers to lock in.

## Files in this delivery

```
backend/scripts/check_finnhub_context_data.py   # M0 #1, #2
backend/scripts/check_etf_and_tick_data.py      # M0 #3, and evidence for #4
```

Unzip at project root — both land in the existing `backend/scripts/`
directory alongside `check_premarket_data_availability.py`, whose
structure they deliberately follow (same sys.path fix, same "hit the real
API, print raw shapes, let a human read the verdict" shape).

Neither script has been run against a live key — finnhub.io and
polygon.io aren't reachable from the sandbox this was written in. Both
import cleanly against your actual `backend/app` package (checked in a
venv with your real `requirements.txt` installed), so the only thing left
untested is the live network behavior itself.

---

## Running M0 #1 + #2 — Finnhub Context Engine data

```
cd backend
python scripts/check_finnhub_context_data.py
python scripts/check_finnhub_context_data.py TSLA NVDA SPY   # custom symbols
```

**What to look for:**

- `/stock/profile2`, `/stock/financials-reported`, `/calendar/earnings`
  returning real data for AAPL/TSLA, and correctly **empty** for SPY
  (ETFs don't have company profiles/financials/earnings) — empty-for-SPY
  is a pass, not a bug. If it's empty for AAPL too, something's wrong
  with the key or endpoint access, not with ETFs.
- The financials-reported section dumps the report's top-level keys and
  one sample balance-sheet line item raw, rather than assuming the shape
  — read that output before writing any TTM-derivation code against it.
  `revenue_ttm`/`net_income_ttm`/`operating_cash_flow_ttm` need summing
  four quarters; confirm the quarterly reports actually line up cleanly
  before committing to that math in `FundamentalsProvider`.
- The news timestamp check tells you directly whether `datetime` is a
  real per-article unix timestamp (needed for `recency_seconds`/
  `count_15m`) or something coarser.
- The burst-test section at the end fires 40 rapid calls at
  `/stock/profile2` and prints any `X-Ratelimit-*` headers seen. If it
  finishes clean with no rate-limit signal, either the ceiling is above
  40/window or Finnhub just doesn't expose quota headers on this
  tier/endpoint — worth widening `_BURST_CALLS` and rerunning if you want
  a tighter number before setting `FundamentalsProvider`'s refresh
  cadence.

**What would actually block the build:** profile2/financials-reported
coming back empty or 403 for a real single-name stock (not an ETF), or
financials-reported's shape being too irregular across symbols to derive
TTM figures without per-symbol special-casing.

---

## Running M0 #3 (+ raw evidence for #4) — SPY/QQQ/IWM data quality

```
cd backend
python scripts/check_etf_and_tick_data.py
python scripts/check_etf_and_tick_data.py --capture-seconds 60
python scripts/check_etf_and_tick_data.py --symbols SPY QQQ IWM AAPL --capture-seconds 45
```

Run this **during regular US market hours (9:30am-4:00pm ET, weekdays)**
— the script warns you if your clock says otherwise, but it'll still run
and just show near-zero trades, which is correct behavior for a closed
market, not a failure.

**Part 1 (Polygon bars) — what to look for:**

- Bar count for SPY/QQQ/IWM's 1m window should look like a normal
  full session (~390 bars for a full regular session, fewer if the day
  isn't fully elapsed yet).
- The `zero-volume bars` percentage — if it's meaningfully higher for
  these three than what you've seen on single-name stocks, that's a real
  ETF-specific liquidity quirk worth noting.
- The daily-vs-summed-1m volume diff — a large mismatch could mean
  extended-hours volume folded into the daily bar differently for ETFs
  than for the stocks you've already validated Polygon against.

**Part 2 (Finnhub live capture) — what to look for:**

- Trade counts per symbol over the capture window — SPY/QQQ/IWM should
  print trades steadily during market hours; if one of the three is
  conspicuously quiet relative to the others, that's worth a second look
  before treating "SPY/QQQ/IWM are always-on subjects" (decision #91) as
  safe to build against as-is.
- **Condition codes observed** — this is the actual answer to "what
  buy/sell classification signal, if any, exists beyond raw price
  direction." If Finnhub's free-tier trade prints carry no useful
  condition codes (likely — most useful trade-classification schemes
  need NBBO quotes, which this tier doesn't stream), the tick-rule
  numbers below are the ceiling of what's achievable without a new data
  source.
- **Tick-rule up/down/flat percentages** — a working demonstration, not a
  finished feature. It shows whether a signed-volume proxy is
  *computable* today from data already flowing through the system. It
  does not tell you whether that proxy is *useful* — that's a question
  for Performance Intelligence once Participation actually exists and
  produces outcomes to check against, same as every other confidence
  question in this project.

---

## My read on M0 #4 (Feature Engine signed-volume signal) — a recommendation, not a decision

This is really a Feature Engine question, not a Context Engine or Market
State Engine one — it doesn't touch either engine's build in scope, so
I'd keep it a separate decision even though it's on this task list.

**What I found tracing the actual data path** (before writing any code):
`FinnhubAdapter`'s WebSocket handler already receives each trade's
condition codes (the `c` field) in `_handle_message`, but `Tick`
(`broker_adapters/base.py`) only carries `symbol/price/size/exchange_ts`
— conditions are parsed out of the raw message and then discarded, never
reaching the `Tick` object. Further downstream, `TickIngestBridge.
_MinuteBucket` only accumulates OHLCV — it has no hook for a running sign
classification per trade either. So this isn't blocked by a missing data
source; it's blocked by nothing in the pipeline capturing per-trade
granularity past the moment a tick arrives.

**What's actually achievable:** Finnhub's free tier is trade prints only
— no NBBO quotes streamed. That rules out real Lee-Ready classification
(comparing each trade to the bid/ask midpoint), which is the standard
approach real order-flow tools use. What IS achievable with zero new
external dependencies is the plain **tick rule**: classify each trade as
an uptick/downtick/unchanged relative to the previous trade's price. It's
a real, established (if cruder) signed-volume proxy — not a guess dressed
up as one — but it should be documented as exactly that: a tick-rule
approximation, not order-flow-based buy/sell classification. That
distinction matters for `evidence.basis`-style honesty once Participation
actually feeds Strategy Engine.

**If you want to unblock this now**, the actual work is small and
narrowly scoped to Feature Engine:
1. Add `sign: Literal[1, -1, 0] | None` (or similar) to `Tick`, computed
   at the point trades are classified against the previous trade's price
   — probably in `FinnhubAdapter._handle_message`, since that's the one
   place that already sees trades in arrival order per symbol.
2. Give `_MinuteBucket` a running `signed_volume` accumulator, published
   as a new field on `CandleClosed`.
3. Feature Engine reads it the same way it already reads
   `open`/`high`/`low`/`close`/`volume` off a closed candle — no new
   architecture, just one more field flowing through an existing path.

**If you'd rather leave it blocked for this build**, that's also
reasonable — decision #91 already says Participation "joins the list
above as another `<dimension>_score` once that signal exists, no design
change needed," so nothing about Context Engine or Market State Engine's
v1 build is gated on this either way. It only matters for whether
Participation ships in the same wave or a later one.

Either way, this is exactly the kind of call worth taking to your
external-consult loop before treating it as settled — flagging it here
as a recommendation with reasoning, not a decision I've made for you.
