"""All-in strategy design study 1: overnight gap risk.

For each of the 11 stock names, measure the distribution of overnight gaps
(today's open vs yesterday's close) over 10y of daily bars. This calibrates:
  - what leverage is survivable for an OVERNIGHT hold (gap risk is separate
    from and can jump straight past a resting stop-loss order)
  - how often a gap alone would blow through stop distances at 5x/10x/20x
  - whether the "flatten unless up >=1R" overnight rule is justified

Raw-move thresholds checked against stop distances actually in use:
  2% raw  = SCALP stop (0.5% * ... no, matches roughly the new fixed 0.5%
            scalp / would-be tighter all-in stop)
  4% raw  = swing stop (3% * ~1.3x) / all-in stop candidate
  7% raw  = ~2x the all-in stop (serious equity damage at 5x: -35%)
  10% raw = liquidation distance at 10x
  20% raw = liquidation distance at 5x
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from common import CACHE

TICKERS = ["NVDA", "META", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN",
           "AMD", "MU", "INTC", "HOOD"]
THRESH = [2, 4, 7, 10, 15, 20]


def load(ticker: str) -> pd.DataFrame:
    pxf = CACHE / f"px_{ticker}.pkl"
    if pxf.exists():
        return pd.read_pickle(pxf)
    t = yf.Ticker(ticker)
    px = t.history(period="10y", auto_adjust=True)
    px.index = px.index.tz_localize(None).normalize()
    px.to_pickle(pxf)
    return px


def gaps_for(ticker: str) -> pd.Series:
    px = load(ticker)
    prev_close = px["Close"].shift(1)
    gap = (px["Open"] - prev_close) / prev_close * 100
    return gap.dropna()


def main() -> None:
    rows = []
    all_gaps = []
    for tk in TICKERS:
        g = gaps_for(tk)
        all_gaps.append(g)
        n = len(g)
        row = {"ticker": tk, "n_days": n, "mean_abs": g.abs().mean(),
               "worst_down": g.min(), "worst_up": g.max()}
        for th in THRESH:
            row[f">={th}%"] = (g.abs() >= th).sum() / n * 100
        rows.append(row)

    df = pd.DataFrame(rows).set_index("ticker")
    pd.set_option("display.width", 160)
    pd.set_option("display.float_format", lambda x: f"{x:.2f}")
    print(f"=== overnight gap distribution, 10y daily, {TICKERS} ===\n")
    print(df.to_string())

    pooled = pd.concat(all_gaps)
    print(f"\n=== POOLED across all {len(TICKERS)} names, {len(pooled)} trading days ===")
    print(f"mean |gap|: {pooled.abs().mean():.2f}%")
    for th in THRESH:
        pct = (pooled.abs() >= th).sum() / len(pooled) * 100
        # implied frequency: once every N trading days on average, per name
        freq = 1 / (pct / 100) if pct > 0 else float("inf")
        print(f"  |gap| >= {th:>2}%: {pct:5.2f}% of days  (~once every {freq:.0f} sessions/name)")

    print("\n=== worst single gaps observed (all names) ===")
    combined = []
    for tk in TICKERS:
        g = gaps_for(tk)
        for ts, v in g.items():
            combined.append((tk, ts.date(), v))
    combined.sort(key=lambda x: x[2])
    print("worst DOWN gaps:")
    for tk, d, v in combined[:8]:
        print(f"  {tk:6s} {d} {v:+.1f}%")
    print("worst UP gaps:")
    for tk, d, v in combined[-8:][::-1]:
        print(f"  {tk:6s} {d} {v:+.1f}%")


if __name__ == "__main__":
    main()
