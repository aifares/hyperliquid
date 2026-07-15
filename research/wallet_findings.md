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
