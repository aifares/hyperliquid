"""All-in strategy design study 2: catalyst-continuation.

Question: after a stock has a big, high-volume ("catalyst") day, does it keep
moving in that direction over the next 1-3 sessions (continuation - argues
FOR a multi-day hold toward +15-20% equity / ~3-4% raw at 5x), or does it
tend to mean-revert (argues for fast profit-taking / tight targets)?

Catalyst day definition (proxy for "news/tape event", since we only have
daily bars): |close-to-close return| >= CATALYST_MOVE_PCT AND volume >=
VOL_MULT x the trailing 20-day average volume. Forward returns measured
from THAT DAY'S CLOSE (i.e. the earliest a same-day chaser could realistically
enter) over the next 1/2/3 trading days, in the catalyst's own direction.

A random-day baseline (unconditional forward returns, same holding periods)
is shown alongside so continuation isn't confused with the stock's normal
drift/vol.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from common import CACHE, FRICTION

TICKERS = ["NVDA", "META", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN",
           "AMD", "MU", "INTC", "HOOD"]
CATALYST_MOVE_PCT = 2.0
VOL_MULT = 1.5
HORIZONS = [1, 2, 3]
TARGET_RAW = 3.5   # the +15-20%-equity-at-5x target expressed in raw stock %


def load(ticker: str) -> pd.DataFrame:
    pxf = CACHE / f"px_{ticker}.pkl"
    if pxf.exists():
        return pd.read_pickle(pxf)
    t = yf.Ticker(ticker)
    px = t.history(period="10y", auto_adjust=True)
    px.index = px.index.tz_localize(None).normalize()
    px.to_pickle(pxf)
    return px


def analyze(ticker: str) -> tuple[dict, list[dict]]:
    px = load(ticker)
    close = px["Close"]
    ret = close.pct_change() * 100
    vol = px["Volume"]
    avgvol20 = vol.rolling(20).mean().shift(1)

    is_catalyst = (ret.abs() >= CATALYST_MOVE_PCT) & (vol >= VOL_MULT * avgvol20)
    idx = close.index
    events = []
    for i, ts in enumerate(idx):
        if not bool(is_catalyst.iloc[i]) or i < 20:
            continue
        direction = 1 if ret.iloc[i] > 0 else -1
        row = {"date": ts.date(), "day_move": ret.iloc[i], "dir": direction}
        c0 = close.iloc[i]
        ok = True
        for h in HORIZONS:
            if i + h >= len(close):
                ok = False
                break
            row[f"fwd{h}d"] = (close.iloc[i + h] - c0) / c0 * 100 * direction
        if ok:
            events.append(row)

    # unconditional (random-day) baseline over the same tail of history
    baseline = {}
    for h in HORIZONS:
        fwd = (close.shift(-h) - close) / close * 100
        baseline[f"fwd{h}d"] = fwd.dropna().abs().mean()  # magnitude, direction-agnostic

    n = len(events)
    if n == 0:
        return {"ticker": ticker, "n": 0}, []
    df = pd.DataFrame(events)
    summary = {"ticker": ticker, "n": n,
              "up_days": (df["dir"] == 1).sum(), "down_days": (df["dir"] == -1).sum()}
    for h in HORIZONS:
        col = f"fwd{h}d"
        summary[f"avg_{col}"] = df[col].mean()
        summary[f"win_{col}"] = (df[col] > 0).mean() * 100
        summary[f"hit_target_{col}"] = (df[col] >= TARGET_RAW).mean() * 100
        summary[f"baseline_{col}"] = baseline[col]
    return summary, events


def main() -> None:
    rows = []
    all_events = []
    for tk in TICKERS:
        s, ev = analyze(tk)
        if s["n"] > 0:
            rows.append(s)
            all_events.extend((tk, e) for e in ev)

    print(f"=== catalyst days: |move|>={CATALYST_MOVE_PCT}% & vol>={VOL_MULT}x avg20, "
          f"10y daily, {TICKERS} ===\n")
    for s in rows:
        print(f"{s['ticker']:6s} n={s['n']:4d} ({s['up_days']}up/{s['down_days']}down)  "
              f"fwd1d avg={s['avg_fwd1d']:+.2f}% win={s['win_fwd1d']:.0f}%  "
              f"fwd2d avg={s['avg_fwd2d']:+.2f}% win={s['win_fwd2d']:.0f}%  "
              f"fwd3d avg={s['avg_fwd3d']:+.2f}% win={s['win_fwd3d']:.0f}% "
              f"hit{TARGET_RAW:.1f}%={s['hit_target_fwd3d']:.0f}%")

    # pooled
    total_n = sum(s["n"] for s in rows)
    pooled_avg = {h: sum(s[f"avg_fwd{h}d"] * s["n"] for s in rows) / total_n for h in HORIZONS}
    pooled_win = {h: sum(s[f"win_fwd{h}d"] * s["n"] for s in rows) / total_n for h in HORIZONS}
    pooled_hit = {h: sum(s[f"hit_target_fwd{h}d"] * s["n"] for s in rows) / total_n for h in HORIZONS}
    pooled_base = {h: sum(s[f"baseline_fwd{h}d"] * s["n"] for s in rows) / total_n for h in HORIZONS}
    print(f"\n=== POOLED, {total_n} catalyst events across {len(rows)} names ===")
    for h in HORIZONS:
        edge = pooled_avg[h] - 0  # continuation itself is the signal, vs baseline magnitude below
        print(f"  fwd{h}d: avg continuation {pooled_avg[h]:+.2f}%  win-rate {pooled_win[h]:.0f}%  "
              f"reach +{TARGET_RAW:.1f}% raw: {pooled_hit[h]:.0f}%  "
              f"| baseline unconditional |move| {pooled_base[h]:.2f}%")

    net_fwd3 = pooled_avg[3] - FRICTION * 100
    print(f"\nfwd3d continuation net of {FRICTION*100:.2f}% friction: {net_fwd3:+.2f}%")
    verdict = ("CONTINUATION (ride it)" if pooled_win[3] > 55 and pooled_avg[3] > 1.0 else
               "WEAK/MIXED (fade risk material)" if pooled_win[3] < 50 else "MARGINAL")
    print(f"VERDICT: {verdict}")


if __name__ == "__main__":
    main()
