"""bigswing tier backtest: does the ACTUAL entry signal in swing_signals.py
(trend-slope + Donchian breakout — the two components computable on daily
bars) have real edge, and what stop/target/conviction/hold parameters
survive it?

Caveat up front, same spirit as the other "proxy" caveats in RESULTS.md:
swing_signals.evaluate() also votes on SUSTAINED ORDER-BOOK IMBALANCE and
LIQUIDATION PRESSURE. Neither is in daily-bar history, so this backtest can
only test the trend+breakout half of the live signal. Treat results here as
what the technical half alone can do; the live signal adds two more votes
that this can't validate historically.

Method per (coin, param-set):
  - trend_slope(n) = % chg of last-n-day close SMA vs the PRIOR n-day SMA
  - breakout(n) = Donchian n-day high/low, EXCLUDING today's bar
  - direction/conviction combiner mirrors swing_signals.evaluate()'s weights
    (0.45 trend + 0.35 breakout, renormalized since the 0.20 imbalance vote
    is unavailable) minus the liquidation-alignment nudge (unavailable too)
  - enter at that day's close when conviction >= MIN_CONVICTION; walk
    forward: STOP checked before TARGET each day (conservative, first-touch
    via day high/low, matches geometry_bt.py convention); for xyz:* names
    only, an OFFHOURS_DERISK check fires at EVERY subsequent day's close
    where profit < 1R (mirrors watcher.py's closing_soon() gate, since on
    daily bars every close IS the closing-soon check); BTC has no such gate
    (24/7, matches watcher.py: only coin.startswith("xyz:") gets it); TIME
    exit at BIGSWING_MAX_HOLD_HOURS/24 days if nothing else fired.
  - no overlapping entries on the same coin while a simulated trade is open
  - net return = FRICTION- AND FUNDING-adjusted raw % (funding via
    common.funding_cost(), same measured-average-rate approach as
    earnings_bt.py/fomc_bt.py — average holds are only ~1-2 days so this is a
    small correction here, but it was a real omission in an earlier version
    of this script to leave it out); also reports the same return scaled by
    the conviction-implied leverage (5-10x) as an "equity %" figure

Second pass: a portfolio-level sim across ALL candidate coins together,
picking the single highest-conviction signal each day (mirrors
bigswing._try_enter's actual one-slot-across-everything behavior), so the
per-coin marginal numbers aren't mistaken for "run all of these at once."
"""
from __future__ import annotations

import itertools
import statistics as st
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from common import CACHE, FRICTION, funding_cost

_avg_funding: dict[str, float] = {}   # coin -> mean hourly rate, cached across calls


def _funding_coin(ticker: str) -> str:
    return "BTC" if ticker == "BTC" else f"xyz:{ticker}"

STOCKS = ["NVDA", "META", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN",
          "AMD", "MU", "INTC", "HOOD"]
CRYPTO = ["BTC"]   # yfinance ticker below is BTC-USD, 24/7, no derisk gate


def load(ticker: str) -> pd.DataFrame:
    pxf = CACHE / f"px_{ticker}.pkl"
    if pxf.exists():
        return pd.read_pickle(pxf)
    yft = "BTC-USD" if ticker == "BTC" else ticker
    t = yf.Ticker(yft)
    px = t.history(period="10y", auto_adjust=True)
    px.index = px.index.tz_localize(None).normalize()
    px.to_pickle(pxf)
    return px


@dataclass
class Params:
    trend_days: int = 10
    trend_strong_pct: float = 8.0
    breakout_days: int = 20
    min_conviction: float = 0.5
    stop_raw: float = 0.025
    target_r: float = 2.0
    max_hold_days: int = 7
    derisk: bool = True   # off-hours de-risk gate (xyz:* only, live behavior)
    derisk_r_mult: float = 1.0   # profit bar (in R) required to survive the
                                  # overnight check; live code hardcodes 1.0
                                  # (watcher.py: "profit < risk") — swept here
                                  # to see if a softer bar helps


def signal_series(px: pd.DataFrame, p: Params) -> pd.DataFrame:
    """One row per day: direction ("long"/"short"/"none") + conviction 0..1,
    using only trend+breakout (the two daily-bar-computable votes)."""
    close, high, low = px["Close"], px["High"], px["Low"]
    n = p.trend_days
    sma = close.rolling(n).mean()
    trend_pct = (sma - sma.shift(n)) / sma.shift(n) * 100

    m = p.breakout_days
    donch_hi = high.rolling(m).max().shift(1)   # excludes today, like candles.donchian
    donch_lo = low.rolling(m).min().shift(1)
    breakout = pd.Series("none", index=px.index)
    breakout[close >= donch_hi] = "up"
    breakout[close <= donch_lo] = "down"

    long_w = pd.Series(0.0, index=px.index)
    short_w = pd.Series(0.0, index=px.index)
    strength = (trend_pct.abs() / p.trend_strong_pct).clip(upper=1.0)
    has_trend = trend_pct.notna()
    long_w[has_trend & (trend_pct > 0)] += 0.45 * strength[has_trend & (trend_pct > 0)]
    short_w[has_trend & (trend_pct < 0)] += 0.45 * strength[has_trend & (trend_pct < 0)]
    long_w[breakout == "up"] += 0.35
    short_w[breakout == "down"] += 0.35

    direction = pd.Series("none", index=px.index)
    conviction = pd.Series(0.0, index=px.index)
    active = (long_w > 0) | (short_w > 0)
    direction[active & (long_w >= short_w)] = "long"
    direction[active & (short_w > long_w)] = "short"
    conviction[active] = long_w[active].where(direction[active] == "long", short_w[active])
    conviction = conviction.clip(upper=1.0)

    return pd.DataFrame({"direction": direction, "conviction": conviction,
                         "trend_pct": trend_pct, "breakout": breakout})


def simulate(ticker: str, px: pd.DataFrame, sig: pd.DataFrame, p: Params,
             is_xyz: bool) -> list[dict]:
    close, high, low = px["Close"].values, px["High"].values, px["Low"].values
    idx = px.index
    n = len(px)
    trades = []
    i = max(p.trend_days * 2, p.breakout_days + 1)
    while i < n - 1:
        row = sig.iloc[i]
        if row["direction"] == "none" or row["conviction"] < p.min_conviction:
            i += 1
            continue
        direction = row["direction"]
        long = direction == "long"
        entry = float(close[i])
        stop = entry * (1 - p.stop_raw) if long else entry * (1 + p.stop_raw)
        risk = abs(entry - stop)
        target = entry + p.target_r * risk if long else entry - p.target_r * risk

        outcome, exit_px, hold_days = "TIME", float(close[min(i + p.max_hold_days, n - 1)]), p.max_hold_days
        j_final = min(i + p.max_hold_days, n - 1)
        for k in range(1, p.max_hold_days + 1):
            j = i + k
            if j >= n:
                j_final = n - 1
                break
            hi, lo, c = float(high[j]), float(low[j]), float(close[j])
            if long:
                if lo <= stop:
                    outcome, exit_px, hold_days = "STOP", stop, k
                    j_final = j
                    break
                if hi >= target:
                    outcome, exit_px, hold_days = "TARGET", target, k
                    j_final = j
                    break
            else:
                if hi >= stop:
                    outcome, exit_px, hold_days = "STOP", stop, k
                    j_final = j
                    break
                if lo <= target:
                    outcome, exit_px, hold_days = "TARGET", target, k
                    j_final = j
                    break
            if p.derisk and is_xyz:
                profit = (c - entry) if long else (entry - c)
                if profit < risk * p.derisk_r_mult:
                    outcome, exit_px, hold_days = "DERISK", c, k
                    j_final = j
                    break
            j_final = j
        else:
            outcome, exit_px, hold_days = "TIME", float(close[j_final]), p.max_hold_days

        raw_ret = (exit_px / entry - 1) if long else (entry / exit_px - 1)
        fcost = funding_cost(_funding_coin(ticker), hold_days * 24, direction, _avg_funding)
        ret = raw_ret - FRICTION - fcost
        trades.append({"ticker": ticker, "date": idx[i], "direction": direction,
                       "conviction": float(row["conviction"]), "outcome": outcome,
                       "ret": ret, "hold_days": hold_days})
        i = j_final + 1   # no overlapping trades on the same coin
    return trades


def run_sweep(params_list: list[Params]) -> None:
    data: dict[str, pd.DataFrame] = {}
    for tk in STOCKS + CRYPTO:
        data[tk] = load(tk)

    print(f"{'params':46s} {'n':>5s} {'win%':>6s} {'tgt%':>6s} {'stop%':>6s} "
          f"{'derisk%':>7s} {'time%':>6s} {'avgHold':>7s} {'exp(raw)':>9s} {'exp@lev':>9s}")
    best = None
    for p in params_list:
        all_trades = []
        for tk in STOCKS + CRYPTO:
            px = data[tk]
            sig = signal_series(px, p)
            all_trades.extend(simulate(tk, px, sig, p, is_xyz=tk in STOCKS))
        if not all_trades:
            continue
        n = len(all_trades)
        rets = [t["ret"] for t in all_trades]
        win = sum(1 for r in rets if r > 0) / n * 100
        mix = {k: sum(1 for t in all_trades if t["outcome"] == k) / n * 100
              for k in ("TARGET", "STOP", "DERISK", "TIME")}
        avg_hold = st.mean(t["hold_days"] for t in all_trades)
        exp_raw = st.mean(rets)
        # conviction-implied leverage, same mapping as bigswing._leverage_for
        def lev_for(c):
            if c <= 0.6:
                return 5.0
            return 5.0 + min((c - 0.6) / 0.4, 1.0) * 5.0
        exp_lev = st.mean(t["ret"] * lev_for(t["conviction"]) for t in all_trades)
        label = (f"tr{p.trend_days}/bo{p.breakout_days}/mc{p.min_conviction}/"
                f"st{p.stop_raw*100:.1f}/tg{p.target_r}R/h{p.max_hold_days}d")
        print(f"{label:46s} {n:>5d} {win:>5.1f}% {mix['TARGET']:>5.1f}% "
              f"{mix['STOP']:>5.1f}% {mix['DERISK']:>6.1f}% {mix['TIME']:>5.1f}% "
              f"{avg_hold:>6.1f}d {exp_raw*100:>+8.3f}% {exp_lev*100:>+8.2f}%")
        if best is None or exp_raw > best[1]:
            best = (p, exp_raw, exp_lev, n, win, all_trades)
    return best


def per_coin_breakdown(p: Params, universe: list[str] | None = None) -> None:
    universe = universe or (STOCKS + CRYPTO)
    print(f"\n=== per-coin breakdown @ {p} ===")
    print(f"{'coin':7s} {'n':>4s} {'win%':>6s} {'tgt%':>6s} {'stop%':>6s} "
          f"{'derisk%':>7s} {'time%':>6s} {'exp(raw)':>9s}")
    for tk in universe:
        px = load(tk)
        sig = signal_series(px, p)
        trades = simulate(tk, px, sig, p, is_xyz=tk in STOCKS)
        if not trades:
            print(f"{tk:7s} no trades")
            continue
        n = len(trades)
        rets = [t["ret"] for t in trades]
        win = sum(1 for r in rets if r > 0) / n * 100
        mix = {k: sum(1 for t in trades if t["outcome"] == k) / n * 100
              for k in ("TARGET", "STOP", "DERISK", "TIME")}
        print(f"{tk:7s} {n:>4d} {win:>5.1f}% {mix['TARGET']:>5.1f}% {mix['STOP']:>5.1f}% "
              f"{mix['DERISK']:>6.1f}% {mix['TIME']:>5.1f}% {st.mean(rets)*100:>+8.3f}%")


def portfolio_sim(p: Params, universe: list[str] | None = None) -> dict:
    """One slot across ALL coins at once, best-conviction wins each day —
    mirrors bigswing._try_enter's real behavior instead of per-coin marginal
    stats above."""
    universe = universe or (STOCKS + CRYPTO)
    data = {tk: load(tk) for tk in universe}
    sigs = {tk: signal_series(data[tk], p) for tk in data}
    all_days = sorted(set().union(*(set(d.index) for d in data.values())))
    # simulate() already returns non-overlapping per-coin trades; treat every
    # candidate trade as an "event" at its entry date and greedily keep
    # non-overlapping ones across ALL coins (single global slot, matching
    # bigswing's one-position-at-a-time rule). Ties broken by conviction.
    candidates = []
    for tk in data:
        candidates.extend(simulate(tk, data[tk], sigs[tk], p, is_xyz=tk in STOCKS))
    candidates.sort(key=lambda t: t["date"])
    # need exit date per trade -> recompute quickly by re-deriving from hold_days
    for t in candidates:
        t["exit_date"] = t["date"] + pd.Timedelta(days=int(t["hold_days"] * 1.4) + 1)
    accepted = []
    busy_to = None
    # among trades starting on the same day (or while slot busy), prefer
    # higher conviction; re-scan greedily
    candidates.sort(key=lambda t: (t["date"], -t["conviction"]))
    i = 0
    while i < len(candidates):
        t = candidates[i]
        if busy_to is None or t["date"] > busy_to:
            accepted.append(t)
            busy_to = t["exit_date"]
        i += 1
    n = len(accepted)
    if n == 0:
        return {"n": 0}
    rets = [t["ret"] for t in accepted]
    win = sum(1 for r in rets if r > 0) / n * 100
    return {"n": n, "win": win, "exp_raw": st.mean(rets),
           "years": (all_days[-1] - all_days[0]).days / 365.25,
           "trades_per_year": n / max((all_days[-1] - all_days[0]).days / 365.25, 0.1)}


CURATED = ["BTC", "NVDA", "MU", "HOOD", "AMD", "TSLA"]   # winners from stage 1
EXCLUDED = ["META", "INTC", "MSFT", "GOOGL", "AMZN", "AAPL"]  # negative/near-zero


def main() -> None:
    print("=== stage 1: bigswing signal sweep, all candidate coins (trend+breakout "
          "only; imbalance/liq NOT backtestable on daily bars, see module "
          "docstring) ===\n")
    grid = []
    for trend_days, breakout_days in [(10, 20), (20, 40)]:
        for min_conv in [0.3, 0.5, 0.7]:
            for stop_raw in [0.025, 0.035]:
                grid.append(Params(trend_days=trend_days, breakout_days=breakout_days,
                                   min_conviction=min_conv, stop_raw=stop_raw,
                                   target_r=2.0, max_hold_days=7))
    best = run_sweep(grid)

    print("\n=== stage 2: soften the overnight de-risk bar (live code hardcodes "
          "1.0R; testing whether that's actually the right bar) ===\n")
    base = best[0] if best else Params()
    derisk_grid = [Params(**{**base.__dict__, "derisk_r_mult": m})
                  for m in [0.25, 0.5, 0.75, 1.0]]
    best2 = run_sweep(derisk_grid)
    winner = (best2[0] if best2 and best2[1] > best[1] else base)

    print(f"\n=== stage 3: per-coin breakdown @ winning params {winner} ===")
    per_coin_breakdown(winner)

    print(f"\n=== stage 4: curated coin subset {CURATED} vs full universe, "
          f"portfolio-level (ONE slot across coins, best-conviction wins, like "
          f"the live bot) ===")
    port_all = portfolio_sim(winner, STOCKS + CRYPTO)
    port_curated = portfolio_sim(winner, CURATED)
    print(f"full universe : {port_all}")
    print(f"curated only  : {port_curated}")

    print(f"\n=== stage 5: curated subset, per-name check that excluding "
          f"{EXCLUDED} wasn't a fluke of stage-1 params ===")
    per_coin_breakdown(winner)


if __name__ == "__main__":
    main()
