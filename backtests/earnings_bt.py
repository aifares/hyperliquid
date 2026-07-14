"""Backtest 1: earnings run-up (Frazzini-Lamont) and PEAD on our 10 stock perps.

Uses 10y of adjusted daily stock data (the perp tracks the stock 1:1) with
today's Hyperliquid cost structure applied:
  - run-up:  long, enter close 10 trading days before the report, exit the
             last close before the print (amc -> announcement-day close;
             bmo -> previous day's close)
  - PEAD:    enter the first close AFTER the print, direction = sign of the
             EPS surprise, hold 10 trading days
Costs: 0.11% friction per round trip + measured avg hourly funding over the
actual calendar hold (longs pay when funding is positive, shorts receive).
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from common import FRICTION, funding_cost

TICKERS = ["NVDA", "META", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN",
           "AMD", "MU", "INTC"]
RUNUP_DAYS = 10       # trading days before print to enter
PEAD_HOLD = 10        # trading days to hold the drift
MIN_SURPRISE = 0.5    # |EPS surprise %| below this -> no PEAD signal

_avg_funding: dict[str, float] = {}


def load(ticker: str):
    from common import CACHE
    pxf = CACHE / f"px_{ticker}.pkl"
    edf = CACHE / f"ed_{ticker}.pkl"
    if pxf.exists() and edf.exists():
        return pd.read_pickle(pxf), pd.read_pickle(edf)
    t = yf.Ticker(ticker)
    px = t.history(period="10y", auto_adjust=True)
    px.index = px.index.tz_localize(None).normalize()
    ed = t.get_earnings_dates(limit=60)
    ed = ed[ed["Reported EPS"].notna() & ed["EPS Estimate"].notna()]
    px.to_pickle(pxf)
    ed.to_pickle(edf)
    return px, ed


def run_ticker(ticker: str) -> dict:
    px, ed = load(ticker)
    closes = px["Close"]
    days = closes.index
    coin = f"xyz:{ticker}"
    runups, peads = [], []

    for ts, row in ed.iterrows():
        amc = ts.hour >= 12                     # 16:00 -> after close
        a_date = pd.Timestamp(ts.date())
        if a_date <= days[0] or a_date > days[-1]:
            continue                            # outside the price window
        pos = days.searchsorted(a_date)
        if pos >= len(days):
            continue
        # announcement trading day index (bmo on a non-trading day -> next day)
        idx = pos if (pos < len(days) and days[pos] == a_date) else pos

        # ---- run-up leg (long into the print) --------------------------------
        exit_i = idx if amc else idx - 1
        entry_i = exit_i - RUNUP_DAYS
        if entry_i >= 0 and exit_i < len(days):
            entry, exitp = closes.iloc[entry_i], closes.iloc[exit_i]
            hours = (days[exit_i] - days[entry_i]).total_seconds() / 3600
            r = (exitp / entry - 1) - FRICTION - funding_cost(
                coin, hours, "long", _avg_funding)
            runups.append(r)

        # ---- PEAD leg (after the print, direction = surprise sign) ----------
        surp = row.get("Surprise(%)")
        if pd.isna(surp) or abs(surp) < MIN_SURPRISE:
            continue
        direction = "long" if surp > 0 else "short"
        entry_i = idx + 1 if amc else idx
        exit_i = entry_i + PEAD_HOLD
        if entry_i < len(days) and exit_i < len(days):
            entry, exitp = closes.iloc[entry_i], closes.iloc[exit_i]
            raw = (exitp / entry - 1) * (1 if direction == "long" else -1)
            hours = (days[exit_i] - days[entry_i]).total_seconds() / 3600
            r = raw - FRICTION - funding_cost(coin, hours, direction, _avg_funding)
            peads.append(r)

    def agg(xs):
        if not xs:
            return dict(n=0)
        return dict(n=len(xs), mean=st.mean(xs), median=st.median(xs),
                    win=sum(1 for x in xs if x > 0) / len(xs),
                    total=sum(xs))
    return {"runup": agg(runups), "pead": agg(peads),
            "runup_list": runups, "pead_list": peads}


def main() -> None:
    print(f"{'ticker':7s} | {'run-up: n':>9s} {'mean':>7s} {'med':>7s} {'win%':>5s}"
          f" | {'PEAD: n':>7s} {'mean':>7s} {'med':>7s} {'win%':>5s}")
    all_r, all_p = [], []
    for t in TICKERS:
        try:
            res = run_ticker(t)
        except Exception as e:  # noqa: BLE001
            print(f"{t:7s} | ERROR {e!r}")
            continue
        r, p = res["runup"], res["pead"]
        def fmt(a):
            if a["n"] == 0:
                return f"{'—':>7s} {'—':>7s} {'—':>7s} {'—':>5s}"
            return (f"{a['n']:>7d} {a['mean']*100:>+6.2f}% {a['median']*100:>+6.2f}%"
                    f" {a['win']*100:>4.0f}%")
        print(f"{t:7s} | {fmt(r)[2:]} | {fmt(p)}")
        all_r.extend(res["runup_list"])
        all_p.extend(res["pead_list"])
    for name, xs in (("run-up", all_r), ("PEAD  ", all_p)):
        if xs:
            wins = sum(1 for x in xs if x > 0)
            print(f"\nAGGREGATE {name}: n={len(xs)}, mean {st.mean(xs)*100:+.2f}%, "
                  f"median {st.median(xs)*100:+.2f}%, win {wins/len(xs)*100:.0f}%, "
                  f"sum {sum(xs)*100:+.1f}% (net of fees+slippage+funding, notional)")


if __name__ == "__main__":
    main()
