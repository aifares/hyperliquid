# Taker-wallet timing sweep — 2026-07-15 00:36

Scanned 204 of top 220 sharp-individual candidates (acct $10k-$3M, +all-time, +30d, from 6633 such wallets / 40,487 total). Kept 12 that are >=50% xyz-stock, >=40% taker, with >=5 declustered entry-events.

**Class counts:** {'INFORMED': 4, 'MOMENTUM': 5, 'MIXED': 3}

Timing test: price move IN ENTRY DIRECTION, 1h before vs 1h/4h after.
INFORMED = move follows entry (fwd1h>0 and >pre1h). MOMENTUM = move precedes entry.

| wallet | acct | allTimePnL | topMkt | xyz% | taker% | events | pre1h | fwd1h | fwd4h | hit% | class |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0xd0640fcb… | $483,885 | $2,538,039 | SKHX | 89 | 87 | 8 | -0.31 | +0.78 | +2.09 | 62 | INFORMED |
| 0x45974824… | $891,241 | $3,888,755 | SKHY | 89 | 50 | 21 | -0.22 | +0.46 | +0.94 | 57 | INFORMED |
| 0xdd0c5de5… | $588,414 | $3,795,729 | META | 81 | 76 | 7 | -0.17 | +0.37 | +0.89 | 57 | INFORMED |
| 0x8923cdff… | $428,727 | $9,434,739 | XYZ100 | 51 | 100 | 7 | -0.33 | +0.30 | -0.25 | 29 | INFORMED |
| 0xfb8c3414… | $630,441 | $8,964,605 | SKHX | 89 | 56 | 10 | +0.72 | +0.22 | +0.24 | 50 | MOMENTUM |
| 0xc5ed4501… | $757,228 | $3,598,615 | SKHX | 99 | 100 | 76 | +0.09 | -0.01 | -0.04 | 50 | MOMENTUM |
| 0x0ce048fe… | $1,862,388 | $4,184,105 | SP500 | 56 | 80 | 7 | -0.06 | -0.02 | -0.06 | 43 | MIXED |
| 0xff2c13a2… | $2,446,806 | $2,162,413 | SP500 | 59 | 65 | 12 | +0.22 | -0.07 | -0.20 | 40 | MOMENTUM |
| 0xdd84ce1a… | $308,225 | $2,211,324 | XYZ100 | 80 | 70 | 11 | -0.24 | -0.10 | -0.24 | 36 | MIXED |
| 0xdbd1bac8… | $2,399,688 | $5,080,050 | SKHX | 72 | 60 | 14 | -1.62 | -0.10 | +0.89 | 50 | MIXED |
| 0x99b1098d… | $242,026 | $26,479,219 | SKHX | 100 | 100 | 8 | +1.88 | -0.13 | +0.63 | 50 | MOMENTUM |
| 0x8e096995… | $2,982,036 | $59,925,293 | SKHX | 100 | 86 | 8 | -0.15 | -0.49 | -0.76 | 12 | MOMENTUM |

## Informed-signature wallets (the ones worth a deeper look)

- `0xd0640fcb1ef5ac385b57701c5ef7030331c8d80b` — xyz:SKHX, 8 events, fwd1h +0.78% vs pre1h -0.31%, hit 62%
- `0x45974824c1c4e4d797aa8d057a5499b46cdefe33` — xyz:SKHY, 21 events, fwd1h +0.46% vs pre1h -0.22%, hit 57%
- `0xdd0c5de50d72e5eaa96816e920e41ce89c4b8888` — xyz:META, 7 events, fwd1h +0.37% vs pre1h -0.17%, hit 57%
- `0x8923cdff38a43d1fd59d323c35b900047e431bac` — xyz:XYZ100, 7 events, fwd1h +0.30% vs pre1h -0.33%, hit 29%

## Deep-dive: informed candidates (2026-07-15 00:38)

### `0x45974824c1c4e4d797aa8d057a5499b46cdefe33` — xyz:SKHY
- sample: 20 entry-events over 5 days · 21 long / -1 short
- move in entry dir: **pre1h -0.22%** → **fwd1h +0.51%** → **fwd4h +0.90%**
- hit1h 60% · split-half fwd1h [+0.47% | +0.56%]
- fwd4h net of 0.11% friction: **+0.79%** · copyable edge: **YES**

### `0xd0640fcb1ef5ac385b57701c5ef7030331c8d80b` — xyz:SKHX
- sample: 8 entry-events over 1 days · 8 long / 0 short
- move in entry dir: **pre1h -0.31%** → **fwd1h +0.80%** → **fwd4h +2.11%**
- hit1h 62% · split-half fwd1h [+1.33% | +0.26%]
- fwd4h net of 0.11% friction: **+2.00%** · copyable edge: **YES**


## Beta-vs-skill control (the decisive test)

### `0x45974824c1c4e4d797aa8d057a5499b46cdefe33` — xyz:SKHY  (21 entries, all-long single name)
- wallet entry fwd:   1h +0.46% · 4h +0.94%
- asset baseline fwd: 1h +0.13% · 4h +0.52%  (random long, same window)
- **timing edge over beta: 1h +0.33% · 4h +0.43% → SKILL**

### `0xd0640fcb1ef5ac385b57701c5ef7030331c8d80b` — xyz:SKHX  (8 entries, all-long single name)
- wallet entry fwd:   1h +0.83% · 4h +2.13%
- asset baseline fwd: 1h +0.59% · 4h +2.44%  (random long, same window)
- **timing edge over beta: 1h +0.23% · 4h -0.30% → BETA (no timing edge over just being long)**


## Own-markets scan — informed wallets on the names WE trade (2026-07-15 00:49)

Scanned 240; 11 taker-concentrated in our markets with >=6 events. Beta-controlled (entry fwd4h vs asset drift).

- `0xdd0c5de5…` META n=7 7L/0S · entryFwd4h +0.89% vs baseline +0.25% = edge +0.63% → SKILL
- `0xbafae6af…` BTC n=23 22L/1S · entryFwd4h +0.22% vs baseline -0.07% = edge +0.29% → SKILL
- `0xbbbdbbfa…` BTC n=56 52L/4S · entryFwd4h +0.22% vs baseline -0.06% = edge +0.28% → SKILL
- `0xbe3f79ae…` BTC n=13 13L/0S · entryFwd4h +0.27% vs baseline +0.06% = edge +0.21% → SKILL
- `0xd67ca2c6…` BTC n=11 8L/3S · entryFwd4h +0.23% vs baseline +0.08% = edge +0.15% → SKILL
- `0xdd84ce1a…` XYZ100 n=11 0L/11S · entryFwd4h +0.04% vs baseline -0.01% = edge +0.05% → BETA
- `0xcb159411…` BTC n=8 1L/7S · entryFwd4h +0.01% vs baseline -0.03% = edge +0.04% → BETA
- `0x0ce048fe…` SP500 n=7 3L/4S · entryFwd4h -0.06% vs baseline -0.00% = edge -0.06% → BETA
- `0x8923cdff…` XYZ100 n=7 0L/7S · entryFwd4h -0.25% vs baseline +0.00% = edge -0.25% → BETA
- `0xcac1f1c3…` BTC n=9 2L/7S · entryFwd4h -0.34% vs baseline +0.03% = edge -0.37% → BETA
- `0xf191539a…` BTC n=30 16L/14S · entryFwd4h -0.63% vs baseline -0.11% = edge -0.52% → BETA

## Whale workup `0x9e8b1e51c6…` (2026-07-15 00:49)

$11M acct · $24.5M all-time · +$5.1M/30d · 100% taker · 2000 fills/1.2d paged · closedPnL $1,162,963 · currently SHORT ~$11M SPCX (uPnL +$1.67M)

- CL: only 0 entry-events — too few, no verdict
- MU: only 2 entry-events — too few, no verdict
- SPCX: only 3 entry-events — too few, no verdict

---

# EXECUTIVE SUMMARY (2026-07-15)

**Question:** is there a wallet on Hyperliquid whose trading gives us a copyable edge?

**Method:** leaderboard (40,487 wallets) → sharp-individual filter ($10k–$3M acct,
profitable all-time AND last 30d, 6,633 candidates) → taker/concentration
fingerprint → declustered entry-event timing test (pre-1h vs fwd-1h/4h move)
→ beta-vs-skill baseline control (entry-conditioned forward return must beat
the asset's own unconditional drift by more than 0.11% friction).

**Findings:**
1. Raw "informed signature" (taker-heavy + concentrated + rich) is NOT edge:
   most such wallets are momentum chasers (0x99b1…: +$26M but enters AFTER
   +2% moves) or pure beta (0xd0640fcb…: +2.1%/4h forward capture, but the
   asset itself drifted +2.4% — no timing skill at all).
2. Beta-controlled SKILL wallets found (timing edge net of drift + friction):
   - 0x45974824… SKHY  n=21  edge +0.43%/4h  (split-half consistent)
   - 0xdd0c5de5… META  n=7   edge +0.63%/4h  (thin sample)
   - 0xbbbdbbfa… BTC   n=56  edge +0.28%/4h  (best sample)
   - 0xbafae6af… BTC   n=23  edge +0.29%/4h
   - 0xbe3f79ae… BTC   n=13  edge +0.21%/4h
   A cluster of 4 INDEPENDENT BTC long-side timers with consistent positive
   edge is the most robust find — BTC dip-timing skill exists at 4h horizon.
3. The $11M whale (0x9e8b1e51…, found externally): $24.5M all-time, +$5.1M/30d,
   100% taker, ~5 massive conviction bets/day (CL crude, MU, SPCX). Currently
   SHORT ~$11M SPCX (+$1.67M uPnL). Timing test NOT feasible: it prints >2,000
   fills/day and the public API only exposes ~1 day of history — too few
   declustered events. Track its POSITIONS (public, real-time), not its timing.
4. No insider signature found: nothing enters quiet price ahead of large moves
   with high hit rate at scale. What survives testing is skill, not foreknowledge.

**Recommendation (not yet built):** a smart-money watchlist poller — track the
5 skill wallets' + whale's open positions via clearinghouseState (public,
10s cadence, same infra as account_monitor). Use as CONFIRMATION signal:
when our news engine fires on a name AND a skill wallet is positioned the
same way, boost conviction; when they're positioned against us, warn.
Do NOT blind-copy: edges are 0.2–0.6%/4h — real but thin; sizing/timing slippage
can eat them. Validate the watchlist signal in shadow before wiring to money.

## Elite win-rate wallets — history + beta-control deep-dive (2026-07-15 01:10)

Full trading history (not just last 2000 fills) + beta-control on each wallet's dominant market, for the 22 win-rate/PF elites from winrate_scan.py.

| wallet | acct | allTimePnL | dominant | history | since | fills | taker% | events | edge4h | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| `0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1` | $820,280 | $3,375,780 | HYPE | 8d | 2025-09-01 | 1998 | 48% | 29 | n/a | no-verdict (thin sample) |
| `0xc993ef2e69fb55a2beb1c2344469be4fe0fc72b9` | $12,877 | $89,451 | HYPE | 243d | 2025-09-13 | 2000 | 66% | 56 | +0.11% | SKILL (timing beats beta) |
| `0x984d622f98e0d423eed96a873b1c545eb934f1a1` | $89,923 | $333,016 | BTC | 140d | 2025-06-10 | 2000 | 86% | 55 | n/a | no-verdict (thin sample) |
| `0xad453be1e0280a1a47c715f4424a9821aebc98f8` | $23,379 | $79,976 | ASTER | 364d | 2025-06-23 | 529 | 44% | 14 | n/a | no-verdict (thin sample) |
| `0xa4cdf5ada61c413e68acfa80ffaaa23c98d1f940` | $5,877 | $635,789 | ETH | 85d | 2025-06-10 | 1999 | 70% | 33 | n/a | no-verdict (thin sample) |
| `0x3f8f352217687d755c6d7b879eaed1e9141eb66a` | $50,449 | $517,951 | PENGU | 94d | 2025-06-10 | 1999 | 83% | 43 | n/a | no-verdict (thin sample) |
| `0x7782d30cf2850815fd84cd99790b4bc6b2c23fd0` | $15,903 | $174,092 | XPL | 27d | 2025-08-27 | 2000 | 49% | 30 | n/a | no-verdict (thin sample) |
| `0x06e0602c9158ee8478365c74606346d90d06df67` | $13,105 | $96,386 | ETH | 41d | 2025-06-10 | 2000 | 99% | 72 | n/a | no-verdict (thin sample) |
| `0xf763d7c28ce6a5df1068fdda9627ae19dff65dee` | $136,444 | $1,315,542 | @166 | 358d | 2025-06-12 | 1998 | 55% | 0 | n/a | no-verdict (thin sample) |
| `0x96b866179f9e7fc566b99b22d5426e9aff893b3e` | $8,052 | $36,393 | YZY | 1d | 2025-10-23 | 2000 | 84% | 36 | n/a | no-verdict (thin sample) |
| `0x011820f60d75a2b870ffe64a4764873e14bc8ede` | $120,167 | $511,428 | PUMP | 73d | 2025-06-10 | 2000 | 79% | 24 | n/a | no-verdict (thin sample) |
| `0x9c7df1b20d01aee41d22aac090767739640f60f6` | $5,992 | $18,389 | SOL | 25d | 2025-10-19 | 2000 | 52% | 13 | n/a | no-verdict (thin sample) |
| `0x987163b6b482c30c2f5f3aa2760109668eb0091d` | $719,176 | $5,153,175 | ETH | 41d | 2025-06-10 | 2000 | 63% | 7 | n/a | no-verdict (thin sample) |
| `0xff4b619ae22acf9bec42e9e113b8e71e4a109ab2` | $6,159 | $19,458 | BTC | 42d | 2026-02-01 | 1995 | 94% | 50 | n/a | no-verdict (thin sample) |
| `0x1fbf4789ac39de79936ccc29fa6789db6848a275` | $40,981 | $538,659 | HYPE | 37d | 2025-12-29 | 2000 | 57% | 107 | n/a | no-verdict (thin sample) |
| `0x36076e4bfad9624d7feba562326fdfa2063ede23` | $102,876 | $333,332 | LIT | 3d | 2026-05-25 | 2000 | 7% | 9 | +2.48% | SKILL (timing beats beta) |
| `0xc399cb4679a5f59a6a4f00473437d0273dd5d7b4` | $14,270 | $428,284 | @107 | 148d | 2025-06-12 | 1990 | 99% | 0 | n/a | no-verdict (thin sample) |
| `0x7f1e97d8eb3ddb8659cb59d611becc438f89b7a2` | $58,832 | $243,021 | BTC | 215d | 2025-10-31 | 2000 | 100% | 35 | +0.26% | SKILL (timing beats beta) |
| `0xf5d13b0477c1ca0722cf1ed8879cc3c26530c5e9` | $9,241 | $70,951 | ETH | 150d | 2025-06-10 | 1995 | 56% | 22 | n/a | no-verdict (thin sample) |
| `0xd46979f07f5d1e86ae2dcc5e6e0f3af5fe270471` | $64,658 | $225,133 | ZEC | 36d | 2025-09-07 | 2000 | 90% | 10 | n/a | no-verdict (thin sample) |
| `0x139791013f6dcc26f4052e6a7b8ed50099e7d15b` | $118,979 | $505,289 | BTC | 358d | 2025-06-12 | 1914 | 93% | 4 | n/a | no-verdict (thin sample) |

---

# ROUND 4 — win-rate/profit-factor scan + history/beta cross-check (2026-07-15 01:10)

**New method:** instead of scanning for "informed timing signature" first, reconstruct
TRUE realized round-trip trades (open-to-flat) from `closedPnl` across a broader
candidate net (acct $5k-$5M, profitable allTime+month+week, 289 wallets scanned,
ranked by return-on-account). This is the gold-standard win-rate metric the
leaderboard's aggregate PnL hides. 122 wallets had >=15 closed trades; 22 were
"elite" (win% >=60, profit factor >=1.5, consistent both halves).

**Critical filter applied to the elite 22:** most trade HYPE/memecoins/HIP-3 exotics
(PENGU, YZY, XPL, CASHCAT, LIT, ASTER, unnamed @107/@150/@166 markets) with SHORT
real history — many wallets' "since" date landed exactly on the 400-day paging
floor with thin (<40 day) actual track records once verified, or traded too few
declustered entries to run the beta control. Only wallets with >=6 events AND
enough candle history to control for beta survived:

- **`0x7f1e97d8eb3ddb8659cb59d611becc438f89b7a2`** — BTC dominant, **215 days**
  real history, 35 entry-events, 65% win rate, profit factor 12.5, $1,917
  expectancy/trade. Beta-controlled entryFwd4h +0.26% vs asset baseline = **edge
  +0.26%/4h → SKILL.** This is an INDEPENDENT confirmation (different scan
  method entirely) of the BTC dip-timing cluster found in Round 3 — the cluster
  is now 5 wallets strong.
- `0xc993ef2e69...` — HYPE dominant, 243 days history, 56 events, edge +0.11%/4h →
  marginal (barely clears the 0.11% friction floor; not compelling).
- `0x36076e4bfa...` — LIT, edge +2.48%/4h but only 3 days of real history and 7%
  taker rate (mostly maker/market-making, not directional timing) → discarded,
  insufficient track record.

**No insider found in this pass either.** Every high-win-rate wallet with a real
long track record is either (a) part of the confirmed BTC dip-timing skill cluster,
or (b) trading illiquid/exotic markets we can't act on, or (c) too short a history
to trust.

## FINAL CONSOLIDATED WATCHLIST (2026-07-15 01:10)

Cross-referencing all 4 rounds, live positions pulled just now:

| wallet | edge (beats beta) | sample | history | live position right now |
|---|---|---|---|---|
| `0x45974824c1c4e4d797aa8d057a5499b46cdefe33` | **+0.43%/4h** (best) | n=21, xyz:SKHY | — | SHORT xyz:SPCX (+$26.5k uPnL), SHORT xyz:LLY (+$5.9k), LONG xyz:SKHY (+$3.0k), + smaller BTC/HYPE/XYZ100/JPY/IBM/CXMT |
| `0xdd0c5de50d72e5eaa96816e920e41ce89c4b8888` | +0.63%/4h (thin, n=7) | xyz:META | — | LONG xyz:META 340.7 @ $661.5 (-$139 uPnL) — **one of our own markets** |
| `0xbafae6afa1f7b0001860f627354130c859031b76` | +0.29%/4h | n=23, BTC | — | flat |
| `0xbbbdbbfa1f754aea323af6cc56153e0605e89227` | +0.28%/4h (best sample) | n=56, BTC | — | LONG BTC, LONG HYPE, SHORT xyz:SNDK (-$7.9k), LONG xyz:SKHX, LONG xyz:CXMT |
| `0x7f1e97d8eb3ddb8659cb59d611becc438f89b7a2` | +0.26%/4h | n=35, BTC, wr65%/pf12.5 | **215d** | LONG xyz:AMZN 200 @ $244.7 (+$690) — **one of our own markets** |
| `0xbe3f79ae0ab3294aaa3230c1155e912c05b6a55b` | +0.21%/4h | n=13, BTC | — | flat |
| `0xd67ca2c6f8bc84acf4fa4472b82a8740dc0a53ff` | +0.15%/4h | n=11, BTC | — | LONG BTC 80 @ $64,039 (+$46.5k uPnL) |
| `0x9e8b1e51c642f4c8b87c6ba11c53d516a218afc4` (whale, untested for timing) | n/a — too few events/day to test | $24.5M all-time | — | SHORT ~$11M xyz:SPCX (+$1.67M uPnL) |

**Notable cross-confirmation:** `0x45974824` (confirmed SKILL wallet, +0.43%/4h edge)
and the untested $11M whale are **both short xyz:SPCX right now** — two
independent large accounts on the same side, one with a proven timing edge. This
is the single most actionable signal in the whole investigation.

**Bottom line:** no insider signature exists anywhere in ~500+ wallets scanned
across 4 rounds. What exists is a reproducible, cross-validated BTC dip-timing
skill cluster (5 wallets, 2 independent test methods) plus 2 stock-specific
skill wallets (SKHY, META) — real but thin edges (0.15–0.63%/4h before our own
slippage). Track the 8-wallet watchlist's live positions as a confirmation
signal; do not blind-copy.
