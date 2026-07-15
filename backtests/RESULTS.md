# Backtest results — 2026-07-13

Cost model everywhere: 0.09% round-trip taker fees + 0.02% slippage = **0.11%
friction per round trip**, plus **measured average hourly funding** per coin
(from Hyperliquid funding history, paginated to listing), signed by side.
Returns quoted as % of notional (multiply by leverage for margin).

## 1. Earnings run-up (Frazzini-Lamont) — VALIDATED ✅
Long 10 trading days before the report, exit last close before the print.
10y × 10 tickers, n=389 events, NET:
- mean **+1.59%/event**, median +1.45%, **win 62%**
- last 3 years only (n=119): +1.61%/event, win 58% — edge persists
- per name: works on 9/10 (best: AMD +2.72%, MU +2.54%, NVDA +2.34%,
  GOOGL +2.02%; TSLA is the exception at -0.33% — exclude TSLA)
- at 3x position-tier leverage ≈ +4.8% on margin per event, ~10 events/quarter

## 2. PEAD (post-earnings drift) — VALIDATED, selectively ✅
Enter first close after the print, direction = EPS surprise sign, hold 10
trading days. n=382, NET:
- mean **+1.30%/event**, median +0.83%, win 56%
- last 3y (n=114): +1.81%/event — persists
- strong: TSLA +4.05%, AMD +2.68%, AAPL +1.67%, INTC +1.39%
- dead: MSFT +0.17%, GOOGL +0.10%, MU +0.00% — trade PEAD only on names
  where it historically works

## 3. Off-hours perp behavior — NO EDGE, NO DANGER ➖
~7 months of hourly perp candles, 12 xyz markets:
- off-hours bars move ~½ of RTH bars; weekends ~¼ (books thin but quiet)
- overnight gaps ≥0.5%: **51% continuation** over 1,191 gaps = coin flip
- conclusion: off-hours moves neither revert nor continue predictably;
  current policy (trade with a tag, no suppression) is correct as-is

## 4. Stop/target geometry — CURRENT CONFIG BROKEN ❌ (fix identified)
Direction-agnostic first-touch sim (measures reachability, not edge):
- current scalp (stop 2.5% / target 5% / 4h): targets hit **0.0–0.5%** of the
  time; 91–100% of trades exit by TIME. The FADE profit gate (+1.25% raw) is
  reachable in only 0–25% of trades. The geometry is leverage-derived, not
  market-derived, and the markets simply don't move that much in 4h.
- current swing (10%/20%/72h): targets 0–2%, TIME 79–100%. Same disease.
- sweep result (aggregated, all 13 markets):
  - scalp 0.5%/1.0%: 7.7% 🎯 / 25.2% 🛑 / 67.0% ⏰ — reachable
  - scalp 0.3%/0.6%: 14.5% / 39.2% / 46.3% — most active
  - swing 3%/6%:     10.6% / 35.1% / 54.2%
- recommendation: scalp stop 0.5% raw (=-10% margin at 20x), target 1.0%
  (=+20% margin); swing stop 3% (=-15% at 5x), target 6% (=+30%).
  Expectancy at random entry ≈ -0.11% (friction) for all geometries, as it
  must be — the signal provides the edge, the geometry only has to be
  reachable so the signal can express itself.

## 5. Pre-FOMC announcement drift — DEAD, DO NOT BUILD ❌
44 FOMC announcement days 2021–2026 vs all days, SPY close-to-close, NET:
- FOMC-day mean -0.041% vs all-day mean -0.042% → edge **+0.000%, t=0.00**
- the Lucca-Moench (2011) drift has fully decayed post-publication in this
  sample. The numeric macro-print SNIPER (react to CPI/NFP surprises at
  release) remains untested here — that's a different trade and still open.

## Caveats
- The xyz perps didn't exist over the backtest horizon; stock data proxies the
  underlying + today's perp cost structure applied. Basis/premium risk not
  modeled.
- Funding modeled at each coin's historical average (NVDA ≈ +19%/yr etc.);
  funding spikes around crowded events could be worse.
- 1m scalp geometry sample is only ~3.5 days (API depth limit) — regime-specific.
- Earnings n≈38-40/name: solid for direction, not for fine parameter tuning.

## Crypto sweep (2026-07-14) — BTC, SOL, XRP, HYPE · 208 days of 1h/15m + full funding history

Five a-priori strategies (no grid search), net of 0.11% friction + actual hourly
funding, split-half consistency required. 21 combos tested, **1 pass**:

| Strategy | Result |
|---|---|
| momo24 (24h momentum) | dead everywhere (26–29% win; chop eats trends) |
| fundfade (funding-extreme fade) | dead everywhere (extremes persist, don't revert) |
| btclead (BTC 1h lead-lag into alts) | decisively dead (−21% to −66%); priced in within the hour |
| breakout (24h Donchian) | +BTC h1 only — UNSTABLE; negative elsewhere |
| **wickfade (capitulation-wick fade, BTC only)** | **PASS: +0.096%/trade, n=62, 55% win, both halves positive — but h2 only +0.2%, edge ≈ 1 friction unit. TOO THIN to fund at current size.** |

Context: buy&hold over the window: BTC −26%, SOL −37%, XRP −41%, HYPE +168%.
Nothing tested beat simply holding HYPE — and none of these coins' strategies
came close to the earnings run-up (+1.59%/event, n=389). SOL/XRP/HYPE produced
NO deployable signal at all.

**Verdict: no crypto tier. BTC stays news-gated optionality. wickfade goes on
the shadow-watch list — worth re-testing with more history or live liquidation
prints as the trigger instead of the candle proxy, not worth real margin yet.**

## All-in single-stock strategy: gap-risk + catalyst-continuation studies (2026-07-15)

**Question tested:** full-balance, one stock at a time, enter on news/tape, target +15-20% equity per trade, repeat.

### Study 1 — overnight gap risk (10y daily, 11 names incl. HOOD)
Pooled across 26,353 trading days: mean |gap| 1.05%. Frequency of adverse gaps:
|gap|>=4%: 3.5% of days (~1/29 sessions/name) · >=7%: 1.1% (~1/95) · >=10%: 0.4%
(~1/261) · >=15%: 0.09% (~1/1,100) · >=20%: 0.03% (~1/2,900). Worst observed:
META -24.5% (2022-10-27 earnings), INTC -24.5% (2024-08-02), AMD +37.5% (2025-10-06).
**A gap CAN jump straight past a resting stop with no fill at the trigger price**
— at 5x (liq ~20% away) a >=20% gap is rare (~1/2,900 sessions/name) but real;
at 10x (liq ~10% away) a >=10% gap happens ~1/261 sessions/name, non-trivial
over a year of active rotation. Verdict: 5x + "flatten overnight unless up
>=1R" is survivable; 10x+ held overnight across many rotations is not.

### Study 2 — catalyst-day continuation (daily-bar proxy: |move|>=2% & vol>=1.5x avg20)
1,764 catalyst events pooled. Raw (undisciplined) forward continuation from
the catalyst close: fwd1d -0.18%, fwd2d +0.01%, fwd3d -0.03% — essentially A
COIN FLIP, no drift edge once the catalyst day itself is excluded. Only
22% of events reach +3.5% raw within 3 days (well under the ~33% a 2R
setup needs to break even on a raw average). MU and INTC show modest positive
individual drift (fwd3d +0.38%/+0.39%, win 56%/53%); MSFT is clearly negative
(fwd3d -0.95%, win 41%) — quality/mega-cap names mean-revert, higher-vol
semis/momentum names show more follow-through.

**However**, applying an actual 2R stop/target structure (not just averaging)
recovers real, positive expectancy at every size tested — the edge lives in
cutting losers at a stop, not in the stock "keeps going" story:

| target | stop | hit% | stopout% | expectancy(raw) | net of 0.11% friction |
|---|---|---|---|---|---|
| 1.0% | 0.5% | 41% | 44% | +0.23% | +0.12% |
| 2.0% | 1.0% | 33% | 39% | +0.40% | +0.29% |
| 3.5% | 1.8% | 22% | 33% | +0.54% | +0.43% |
| 5.0% | 2.5% | 14% | 28% | +0.58% | +0.47% |

At 5x that's **+0.6% to +2.4% equity per trade** — real, but nowhere near the
+15-20%-per-trade aspiration. The edge is small-and-repeatable (same shape as
the existing swing tier), not a home-run-per-trade mechanism.

**Caveat:** this proxy chases an ALREADY-COMPLETED daily move; it is NOT the
same as the bot's actual entry (real-time news + intraday tape confirmation,
which can act before the move fully plays out — the live NVDA trade hit
+2.99% in <12h, beating this proxy's average). Treat this as a conservative
floor, not a ceiling, on the real signal's edge — an honest limitation of
only having daily bars available for the historical test.

**VERDICT: the "+15-20% per trade, full balance, rinse-repeat" plan as stated
is NOT supported by this data.** A real small edge exists and compounds like
the current swing tier; the run-up tier (+1.59%/event, 62% win, n=389)
remains the strongest validated edge in the whole program — nothing found
here beats it. Recommend against concentrating full balance on an aspirational
15-20%-per-trade target; recommend instead sizing UP the existing validated
tiers with disciplined 2R stops, informed by this table.

## bigswing signal backtest (2026-07-15) — `bigswing_bt.py`

**Question tested:** does the ACTUAL bigswing entry signal (`swing_signals.py`:
trend-slope + Donchian breakout) have edge, and does the coded overnight
de-risk rule (flatten a stock-perp trade at the NYSE close unless up >=1R)
help or hurt, once simulated with real stop/target mechanics (not just
averaging forward returns like the catalyst study above)?

**Caveat:** `swing_signals.evaluate()` also votes on sustained order-book
imbalance (0.20 weight) and liquidation pressure (a same-direction nudge).
Neither exists in daily-bar history, so this only backtests the trend+breakout
two-thirds of the live signal. Treat this as a floor on the real signal, same
spirit as the catalyst study's own caveat above.

**Method:** 10y daily bars (yfinance, same tickers as the studies above, plus
BTC-USD), entries at each day's close when the trend+breakout combiner's
conviction clears a threshold, first-touch stop/target via daily high/low
(stop checked first, conservative), the REAL `market_hours.closing_soon()`
de-risk gate simulated as "exit at close if profit < 1R" for every xyz:*
name on every subsequent day (BTC exempt, matching the live code), net of
0.11% friction.

**Findings:**
- The overnight de-risk gate fires on **50-90% of stock-perp trades** at the
  stop widths tested — most bigswing stock trades resolve in **~1-2 days**,
  not a multi-day swing, because reaching 1R profit (>=stop distance) by the
  very next close is genuinely uncommon at typical daily volatility. **BTC is
  the only name that can run the full multi-day hold** (no de-risk gate).
- Softening the de-risk bar below the coded 1.0R (tested 0.25R/0.5R/0.75R)
  did **not** meaningfully improve net expectancy — kept the code as-is
  (`watcher.py`'s hardcoded "profit < risk" / 1R stays).
- **Stop width: 2.5% (prior default) was too tight** — close to or inside
  these names' normal daily noise, causing routine whipsaws. **3.5% raw
  beat 2.5% in nearly every row of the sweep.**
- **Per-name net expectancy (trend10/breakout20/stop3.5%/2R, 10y, net of
  friction), sorted best to worst:**

  | coin | n | win% | target% | stop% | derisk% | exp/trade (raw) |
  |---|---|---|---|---|---|---|
  | BTC | 585 | 42.4% | 33.0% | 53.8% | 0% (exempt) | **+0.574%** |
  | xyz:HOOD | 422 | 50.7% | 18.5% | 29.4% | 52.1% | **+0.518%** |
  | xyz:TSLA | 734 | 50.0% | 12.7% | 23.7% | 63.5% | **+0.298%** |
  | xyz:MU | 692 | 49.4% | 9.4% | 17.6% | 73.0% | **+0.224%** |
  | xyz:AMD | 731 | 47.5% | 11.4% | 23.5% | 65.1% | **+0.155%** |
  | xyz:NVDA | 695 | 47.8% | 8.2% | 19.4% | 72.4% | +0.065% |
  | xyz:AAPL | 509 | 48.1% | 1.6% | 5.3% | 93.1% | +0.041% |
  | xyz:GOOGL | 440 | 46.1% | 3.6% | 5.0% | 91.1% | +0.021% |
  | xyz:AMZN | 502 | 46.0% | 4.6% | 9.4% | 86.1% | -0.018% |
  | xyz:MSFT | 390 | 45.4% | 2.1% | 5.9% | 92.1% | -0.108% |
  | xyz:META | 552 | 42.0% | 2.9% | 9.2% | 87.9% | -0.161% |
  | xyz:INTC | 552 | 45.5% | 5.4% | 16.8% | 77.7% | -0.176% |

  Note the same signal produces a POSITIVE result on TSLA here, unlike the
  earnings run-up tier (which excludes TSLA for a negative edge on ITS
  signal) — different strategy, different signal, exclusions don't transfer
  between tiers.
- **Portfolio-level sim (one slot across all coins at once, best-conviction
  wins each day — matches `bigswing._try_enter`'s real behavior), curated
  universe {BTC, HOOD, MU, AMD, NVDA, TSLA} vs the full 12-name universe:**
  curated **+0.35%/trade** (mc0.4/stop3.5%/2R, n=815, 81.5 trades/yr, 48.8%
  win) vs full-universe +0.24%/trade (dragged down by META/INTC/MSFT/AMZN) —
  narrowing the universe to the backtested winners is a real, measured
  improvement, not just a liquidity preference.
- Conviction threshold: 0.4 beat both 0.5 (prior default) and 0.3 net of
  friction on the curated set (0.061% / 0.352% / 0.193% respectively) — but
  this sweep only has the trend+breakout half of conviction to work with, so
  don't over-read this as proof the real (3-vote) conviction score should be
  gated lower; it's just what this proxy showed.

**Changes made to `config.py` from this backtest:**
`BIGSWING_MARKETS` narrowed to `{BTC, xyz:HOOD, xyz:MU, xyz:AMD, xyz:NVDA,
xyz:TSLA}` (dropped META, added HOOD/AMD/TSLA per the table above),
`BIGSWING_STOP_RAW` 2.5% -> 3.5%, `BIGSWING_MIN_CONVICTION` 0.5 -> 0.4.
`BIGSWING_TARGET_R` (2.0) and the trend/breakout lookbacks (10d/20d) were
already at backtested-good values and are unchanged.

**Still small and repeatable, not a home run** — same conclusion as the
catalyst study above, now grounded in the tier's actual entry logic instead
of a generic proxy.

**Update (same day):** the first version of this backtest omitted funding
carry cost over the hold (inconsistent with `earnings_bt.py`/`fomc_bt.py`,
which both apply it via `common.funding_cost()`) — fixed. Re-run with
funding included: curated-set portfolio result moved from +0.35%/trade to
**+0.34%/trade** (n=815, 48.8% win) — funding is a rounding error here given
the ~1.3-day average hold; every per-name ranking and the config choices
above are unchanged.
