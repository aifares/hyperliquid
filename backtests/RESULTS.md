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
