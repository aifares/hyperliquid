# Quant research session — 2026-07-17

Systematic edge-hunt across fees, funding, lead-lag, wallets, and geometry.
Every backtest is net of **measured** fees (not assumed). Data: Hyperliquid
`candleSnapshot` (hourly/daily), `fundingHistory`, `userFillsByTime`,
`metaAndAssetCtxs`, `clearinghouseState`.

## 0. Real fee & funding constants (the foundation)

Pulled from this wallet's actual fills (`userFillsByTime`, 5 days, 118 fills):

| venue | measured fee | notes |
|---|---|---|
| xyz perps | **0.9 bps/side** (~1.8 bps round trip) | 112 fills, $12.1k notional, $1.09 fees |
| BTC (main dex) | **4.5 bps/side** | standard taker, 6 fills |
| funding paid (5d) | −$0.38 total | small, but concentrated (see §3) |

Fee schedule confirms: taker 4.5 bps, maker 1.5 bps, VIP tiers at $5M/$25M
volume. **Key implication:** the old `backtests/RESULTS.md` assumed 0.11%
round-trip friction — real xyz friction is **~0.018%**, 6× cheaper. Every
xyz backtested edge is therefore *understated*; the run-up/lead-lag numbers
have more margin than modeled. BTC stays expensive — keep its conviction bar
high.

## 1. Korea→US semiconductor lead-lag — VALIDATED ✅ (new tier, shadow-first)

**Thesis:** SKHX (SK Hynix) and SMSN (Samsung) perps trade the Seoul session
24/7 while US names sleep. Overnight semiconductor information is priced in
Korea *hours* before the US open.

**Test:** 60d hourly. Signal = mean(SKHX, SMSN) return over the Korea session
(00:00→07:00 UTC). When |signal| ≥ 1%, trade an equal-weight same-direction
basket of MU/NVDA/AMD/SNDK, enter 14:00 UTC, exit 19:55 UTC. Net of 2 bps.

| metric | result |
|---|---|
| mean / event (per day) | **+0.85%** |
| median | +1.13% |
| win rate | **66%** (n=35 days) |
| first half / second half | +1.07% / +0.88% (both positive) |
| long days / short days | +1.32% / +0.76% (works both ways) |
| entry robustness | +0.97% at 13:00, +0.85% at 14:00 |

Correlation(Korea overnight, US RTH same-day) = **+0.24** — modest but real
and, crucially, *tradable with a time offset* (the Korea move completes before
the US entry). Better per-event than any tier except run-up, with a 6h hold.

**Shipped:** `korea.py` — SHADOW mode by default (`KOREA_LIVE=0`): computes
the signal, Telegrams the call + result, logs to `korea_shadow.jsonl` for
forward validation. Flip `KOREA_LIVE=1` after the live record confirms.

## 2. Funding carry — was an unmodeled leak, now gated ✅

Measured live funding (`metaAndAssetCtxs`), annualized:

| market | funding/yr | who pays |
|---|---|---|
| SKHX | **−417% to −504%** | **shorts pay longs ~1.37%/day** |
| SMSN | −161% | shorts pay ~0.40%/day |
| SNDK | +49% | longs pay ~0.13%/day |
| most US names | +2% to +30% | longs pay a little |

**The leak:** the bot shorted SKHX three times this week (trades 41/44/46)
paying **~1.37%/day** carry it never accounted for — a multi-day SKHX short
needs >1.4%/day of price move just to break even on funding. The whole
Korea/memory complex is crowd-shorted, so the crowd pays through the nose to
hold the popular side.

**Shipped:** `funding.py` polls every 10 min; combiner now (a) **blocks** a
swing/bigswing entry whose carry exceeds 0.8%/day against us, (b) demands
+0.05 conviction above 0.3%/day, (c) annotates the alert otherwise. Verified:
SKHX short → BLOCK, SMSN short → costly, US names → clear.

## 3. Overnight drift & post-drop reversion — mostly dead, one confirm

- **Overnight hold (US close→next open), 11 names, 660 samples:** mean
  +0.015%. **No drift edge** — holding through the gap is a coin flip; the
  bigswing off-hours de-risk rule is correct to flatten.
- **Buy the dip (long next RTH after a −4% day), n=35:** mean **−0.43%**,
  46% win. Dip-buying these names is *negative*.
- **Short-continuation (short next RTH after a −4% day), n=35:** mean
  **+0.39%**, 54% win. Weak but positive — **validates the bot's existing
  chase-the-selloff behavior** (the AMD/GOOGL/MU shorts). Down keeps going
  down here; don't catch the knife.

## 4. Funding-squeeze (crowded-short → bounce) — REJECTED (inconsistent)

Tested whether extreme negative funding predicts a forward squeeze. SKHX:
crowded-short quartile → +1.40% fwd 24h (squeeze). But SNDK: crowded-short →
−0.75% while low-funding → +2.24% (momentum, the opposite). No consistent
sign across names; likely just bounces inside SKHX's −26%/14d downtrend. Not
built — would be curve-fitting one name.

## 5. Wallet forensics (14d `userFillsByTime`)

| wallet | fills | closed PnL | last fill | edge |
|---|---|---|---|---|
| **whale** | 2000 | **+$2.14M** | holds SPCX short | see below |
| **skill-META** | 2000 | **+$68k** | 121h | xyz:META +$22k, MU +$21.6k, SNDK +$12.9k — **best equity wallet** |
| skill-BTC2 | 288 | +$33k | 2h | BTC/ETH/@107, active |
| skill-SKHY | 1919 | +$17k | 28h | SKHY +$10k (its named edge holds) |
| skill-BTC3 | 429 | −$7k | 9h | net loser lately — **demote** |
| skill-BTC1 | 12 | ~$0 | 12h | barely trades — **candidate to drop** |

**Whale headline finding:** the whale is **currently short SPCX $11M at
+$2.55M unrealized**, unchanged for 9 days — and the bot holds a SPCX short
(trade 49) that this exactly confirms (+0.12 conviction, verified live).

**Self-correction:** I first added a "staleness decay" to ignore wallets
whose books hadn't changed in 5 days. Checking the whale revealed this as a
**regression** — a $11M/+$2.55M held position is *conviction*, not
staleness, and the decay would have silently discarded the single best
smart-money signal on the book. Reverted. A held position is a live capital
commitment; a truly disengaged wallet holds nothing and contributes nothing
anyway.

## 6. ATR-stop geometry — VALIDATED ✅ (already shipped this session)

Replayed 73 daily swing-short setups per name; % noise-stopped within 3 days,
fixed-3% vs ATR-based (1.5×ATR, clamped 1.5–5%):

| name | fixed-3% stopped | ATR stopped | ATR frac |
|---|---|---|---|
| NVDA | 41% | **19%** | 4.6% |
| SKHX | 73% | 59% | 5.0% |
| MU | 74% | 63% | 5.0% |
| SNDK | 82% | 71% | 5.0% |
| AAPL | 27% | 22% | 3.1% |

ATR stops cut noise-outs hardest on the volatile names (NVDA halved) while
barely touching calm AAPL — exactly the intent. The fixed 3% was a
noise-magnet on the Korea/memory complex.

## 7. New market candidate — commodities (research only, not built)

The whale's *historical* $2M was on **crude oil (xyz:CL)**. CL ($171M OI, 20x)
and BRENTOIL ($187M OI) are the deepest non-equity xyz markets — more liquid
than most of our single names. No validated edge yet and no news pipeline for
oil (Benzinga/Perplexity are equity/macro), so **not added** — flagged as the
top candidate for the next research session (oil has clean macro catalysts:
OPEC, inventories, geopolitics — which the analyzer already sees).

---

## Shipped this session (all live after restart)
1. `funding.py` + combiner carry-gate (blocks the SKHX-short leak)
2. `korea.py` shadow tier (validated lead-lag, forward-validating)
3. Reverted the smart-money staleness regression (keeps whale SPCX signal)
4. (from the audit earlier) ATR stops, partials, correlation cap, global
   margin cap, run-up 4-slot fix, swing floors, scalp kill, HTML escaping.

## Open threads for next session
- Flip `KOREA_LIVE=1` once the shadow record shows ≥1 week tracking backtest.
- Demote skill-BTC3 (losing) and skill-BTC1 (inactive) from the tracked set.
- Commodities universe (CL/BRENTOIL) with an oil-news pipeline.
- Consider a maker-entry (limit-at-retrace) to turn the 4.5bp BTC taker into
  a 1.5bp maker, and to enter the news-chase shorts at a better price.
