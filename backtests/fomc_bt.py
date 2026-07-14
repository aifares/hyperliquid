"""Backtest 4: pre-FOMC announcement drift (Lucca-Moench) on S&P 500.

Window approximation with daily data: long from the close the day BEFORE the
announcement day to the close ON the announcement day (statement at 2pm ET,
so this holds ~24h through the announcement — close to the documented drift
window). Benchmarked against the unconditional daily return over the same
period. Net of Hyperliquid friction (0.11% round trip); funding over 24h at
SP500's measured average is applied.

FOMC announcement days 2021-2026 scraped from federalreserve.gov.
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from common import CACHE, FRICTION, funding_cost

FOMC = [
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28",
    "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27",
    "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26",
    "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31",
    "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30",
    "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
]

_avg_funding: dict[str, float] = {}


def main() -> None:
    f = CACHE / "px_SPY.pkl"
    if f.exists():
        px = pd.read_pickle(f)
    else:
        px = yf.Ticker("SPY").history(period="10y", auto_adjust=True)
        px.index = px.index.tz_localize(None).normalize()
        px.to_pickle(f)
    closes = px["Close"]
    days = closes.index

    fomc_rets, hit = [], 0
    for ds in FOMC:
        d = pd.Timestamp(ds)
        if d not in days:
            continue
        i = days.get_loc(d)
        if i == 0:
            continue
        raw = closes.iloc[i] / closes.iloc[i - 1] - 1
        net = raw - FRICTION - funding_cost("xyz:SP500", 24, "long", _avg_funding)
        fomc_rets.append(net)
        hit += 1

    # benchmark: every daily close-to-close return in the same span, same costs
    start = days.get_loc(pd.Timestamp(FOMC[0])) if pd.Timestamp(FOMC[0]) in days else 0
    all_rets = [closes.iloc[i] / closes.iloc[i - 1] - 1 - FRICTION
                - funding_cost("xyz:SP500", 24, "long", _avg_funding)
                for i in range(max(start, 1), len(closes))]

    wins = sum(1 for r in fomc_rets if r > 0)
    print(f"FOMC announcement days matched: {hit}/{len(FOMC)}")
    print(f"FOMC-day long SPY (net):   mean {st.mean(fomc_rets)*100:+.3f}%  "
          f"median {st.median(fomc_rets)*100:+.3f}%  win {wins/len(fomc_rets)*100:.0f}%  "
          f"sum {sum(fomc_rets)*100:+.1f}%")
    print(f"ALL days long SPY (net):   mean {st.mean(all_rets)*100:+.3f}%  "
          f"median {st.median(all_rets)*100:+.3f}%  "
          f"win {sum(1 for r in all_rets if r > 0)/len(all_rets)*100:.0f}%")
    edge = st.mean(fomc_rets) - st.mean(all_rets)
    print(f"Per-event edge vs average day: {edge*100:+.3f}% notional "
          f"({len(fomc_rets)} events over ~5.5y)")
    # rough t-stat
    if len(fomc_rets) > 2:
        sd = st.stdev(fomc_rets)
        t = (st.mean(fomc_rets) - st.mean(all_rets)) / (sd / len(fomc_rets) ** 0.5)
        print(f"t-stat of the edge: {t:.2f}")


if __name__ == "__main__":
    main()
