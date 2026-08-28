# TESTING — premarket_volume_ratio

Unzip at project root. No migration needed. Overwrites
`backend/app/feature_engine/engine.py` (adds `_maybe_refresh_premarket_baseline`
+ `_update_premarket_volume_ratio` — `vwap_ext`/`vwap`/everything else
untouched), `backend/app/core/config.py` (adds
`feature_engine_premarket_lookback_days`, default 5), and 3 test files
(see "Bugs found and fixed" below for why). Adds
`backend/tests/test_premarket_volume_ratio.py`.

## 1. Run the tests

```bash
cd backend
pytest tests/test_premarket_volume_ratio.py -v
pytest   # full suite
```

**255/255 passing**, confirmed on two consecutive runs while real
wall-clock time was sitting in ET pre-market hours the whole time — not
a lucky window, an actual fix (see below).

## 2. What premarket_volume_ratio actually is

Same shape as regular-session `rvol`, reusing its exact pure function —
just against your own symbol's historical pre-market volume instead of
its daily volume, and only published while still inside the 4:00-9:30am
window. Needs `feature_engine_premarket_lookback_days` (default 5)
complete prior pre-market sessions cached before it publishes anything —
cold start is honest absence, not a guess.

The fetch itself (`_maybe_refresh_premarket_baseline`) runs once per
(symbol, ET day) against whatever's registered as your historical
provider (Polygon today) — same seam Daily Levels already uses, just
requesting 1-minute bars instead of 1-day ones, since a single daily bar
can't tell you how much of it was pre-market.

## 3. Bugs found and fixed while building this (not new features)

**Six existing tests broke** the moment this feature started making its
own real fetch calls against the same historical-provider interface
Daily Levels/ATR/RVOL already share — their fake providers tracked one
global call count, and this feature's own legitimate second call (1m,
not 1d) pushed that count from 1 to 2. Fixed by making those fakes count
calls *per timeframe* instead, so the original claim they protect (the
1d fetch is shared, not duplicated) is still checked correctly, without
being tripped up by an unrelated new consumer.

**Four other tests had a latent, pre-existing bug** — they used
`datetime.now(timezone.utc)` as their base timestamp instead of a fixed
one. This was always a live risk (pre-market H/L already published
unconditionally during pre-market hours before any of today's work), but
only actually broke once the sandbox's real clock happened to cross into
ET pre-market hours while I was testing this. Fixed by anchoring all
four to a fixed Saturday — guaranteed `Session.CLOSED` no matter what
hour they run at, so this can't recur. Worth knowing about since it's a
pattern (`datetime.now()` in a test) that could bite again wherever else
it might still exist in the suite — not something I went looking for
beyond the four that actually failed.

## What this doesn't do yet

Nothing consumes `premarket_volume_ratio` for actual ORB screening —
that's Scanner-side work (a "pre-market movers" scan type, per
`docs/architecture/premarket-accumulator-design.md` §6), deliberately
not started until this feature itself has been watched against a few
real pre-market sessions first.
