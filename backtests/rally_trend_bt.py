"""Rally tier — news+trend GATE only (historical proxy).

The live rally tier arms on a Claude news read when per-asset + broad-market
trend don't strongly oppose, then waits for tick-level orderbook confirmation.
Hyperliquid has no historical L2, so the book-confirm half cannot be
backtested here. This script sanity-checks ONLY the news+trend gate, using
the same catalyst-day proxy catalyst_bt.py uses for "news" (big |move| +
elevated volume), then layers the live RALLY_TREND_DAYS / RALLY_TREND_VETO_PCT
filter and an optional SPY broad-market veto.

If the trend gate kills the (already small) catalyst continuation edge, the
veto is too aggressive; if it lifts win-rate / avg continuation, ship it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
from common import CACHE, FRICTION

TICKERS = ["NVDA", "META", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN",
           "AMD", "MU", "HOOD"]
CATALYST_MOVE_PCT = 2.0
VOL_MULT = 1.5
TREND_DAYS = 10
TREND_VETO_PCT = 4.0
HORIZONS = [1, 2, 3]
STOP_RAW = 2.0   # RALLY_STOP_RAW * 100
TARGET_RAW = 4.0  # 2R


def load(ticker: str) -> pd.DataFrame:
    pxf = CACHE / f"px_{ticker}.pkl"
    if pxf.exists():
        return pd.read_pickle(pxf)
    t = yf.Ticker(ticker)
    px = t.history(period="10y", auto_adjust=True)
    px.index = px.index.tz_localize(None).normalize()
    px.to_pickle(pxf)
    return px


def trend_slope_series(close: pd.Series, n: int = TREND_DAYS) -> pd.Series:
    """Mirror of candles.trend_slope: recent n-day SMA vs prior n-day SMA, %."""
    sma = close.rolling(n).mean()
    prior = sma.shift(n)
    return (sma - prior) / prior * 100


def events_for(ticker: str, spy_trend: pd.Series) -> list[dict]:
    px = load(ticker)
    close = px["Close"]
    ret = close.pct_change() * 100
    vol = px["Volume"]
    avgvol20 = vol.rolling(20).mean().shift(1)
    trend = trend_slope_series(close)

    is_catalyst = (ret.abs() >= CATALYST_MOVE_PCT) & (vol >= VOL_MULT * avgvol20)
    out = []
    for i, ts in enumerate(close.index):
        if not bool(is_catalyst.iloc[i]) or i < 2 * TREND_DAYS:
            continue
        direction = 1 if ret.iloc[i] > 0 else -1
        t_pct = trend.iloc[i]
        if pd.isna(t_pct):
            continue
        # Live gate: refuse only if trend STRONGLY opposes
        trend_ok = not (
            (direction == 1 and t_pct <= -TREND_VETO_PCT) or
            (direction == -1 and t_pct >= TREND_VETO_PCT)
        )
        # Broad-market: map date to SPY trend
        spy_t = spy_trend.get(ts, float("nan"))
        broad_ok = True
        if not pd.isna(spy_t):
            broad_ok = not (
                (direction == 1 and spy_t <= -TREND_VETO_PCT) or
                (direction == -1 and spy_t >= TREND_VETO_PCT)
            )
        row = {
            "ticker": ticker, "date": ts.date(), "dir": direction,
            "day_move": float(ret.iloc[i]), "trend_pct": float(t_pct),
            "spy_trend": float(spy_t) if not pd.isna(spy_t) else None,
            "trend_ok": trend_ok, "broad_ok": broad_ok,
            "pass_gate": trend_ok and broad_ok,
        }
        c0 = close.iloc[i]
        ok = True
        for h in HORIZONS:
            if i + h >= len(close):
                ok = False
                break
            fwd = (close.iloc[i + h] - c0) / c0 * 100 * direction
            row[f"fwd{h}d"] = float(fwd)
        # Path-aware: did +TARGET or -STOP hit first within 3 sessions?
        # Approximate with daily high/low over next 3 days.
        if ok and i + 3 < len(close):
            hit_tgt = hit_stop = False
            for j in range(1, 4):
                hi = px["High"].iloc[i + j]
                lo = px["Low"].iloc[i + j]
                if direction == 1:
                    if lo <= c0 * (1 - STOP_RAW / 100):
                        hit_stop = True
                        break
                    if hi >= c0 * (1 + TARGET_RAW / 100):
                        hit_tgt = True
                        break
                else:
                    if hi >= c0 * (1 + STOP_RAW / 100):
                        hit_stop = True
                        break
                    if lo <= c0 * (1 - TARGET_RAW / 100):
                        hit_tgt = True
                        break
            if hit_tgt:
                row["geom"] = TARGET_RAW - FRICTION * 100
            elif hit_stop:
                row["geom"] = -STOP_RAW - FRICTION * 100
            else:
                row["geom"] = row["fwd3d"] - FRICTION * 100
            out.append(row)
    return out


def summarize(label: str, events: list[dict]) -> dict:
    if not events:
        return {"label": label, "n": 0}
    df = pd.DataFrame(events)
    s = {"label": label, "n": len(df)}
    for h in HORIZONS:
        col = f"fwd{h}d"
        s[f"avg_{col}"] = df[col].mean()
        s[f"win_{col}"] = (df[col] > 0).mean() * 100
    s["avg_geom"] = df["geom"].mean()
    s["win_geom"] = (df["geom"] > 0).mean() * 100
    return s


def main() -> None:
    spy = load("SPY")
    spy_trend = trend_slope_series(spy["Close"])

    all_events: list[dict] = []
    for tk in TICKERS:
        all_events.extend(events_for(tk, spy_trend))

    unfiltered = all_events
    trend_only = [e for e in all_events if e["trend_ok"]]
    gated = [e for e in all_events if e["pass_gate"]]

    print(f"=== rally news+trend GATE proxy ===")
    print(f"catalyst: |move|>={CATALYST_MOVE_PCT}% & vol>={VOL_MULT}x avg20")
    print(f"trend: {TREND_DAYS}d SMA slope, veto opposing >= {TREND_VETO_PCT}%")
    print(f"broad: SPY same veto (proxy for xyz:SP500)")
    print(f"geometry: stop {STOP_RAW}% / target {TARGET_RAW}% (2R), net of "
          f"{FRICTION*100:.2f}% friction\n")

    for subset, label in (
        (unfiltered, "ALL catalyst days (no trend gate)"),
        (trend_only, "per-asset trend gate only"),
        (gated, "per-asset + SPY broad-market gate (LIVE default)"),
    ):
        s = summarize(label, subset)
        if s["n"] == 0:
            print(f"{label}: n=0")
            continue
        print(f"{label}")
        print(f"  n={s['n']:4d}  "
              f"fwd1d avg={s['avg_fwd1d']:+.2f}% win={s['win_fwd1d']:.0f}%  "
              f"fwd2d avg={s['avg_fwd2d']:+.2f}% win={s['win_fwd2d']:.0f}%  "
              f"fwd3d avg={s['avg_fwd3d']:+.2f}% win={s['win_fwd3d']:.0f}%")
        print(f"  geom (stop/target) avg={s['avg_geom']:+.2f}% win={s['win_geom']:.0f}%")

    # Per-name under the LIVE gate
    print("\n=== per-name under LIVE gate (trend+SPY) ===")
    by_tk: dict[str, list] = {}
    for e in gated:
        by_tk.setdefault(e["ticker"], []).append(e)
    for tk in TICKERS:
        ev = by_tk.get(tk, [])
        s = summarize(tk, ev)
        if s["n"] == 0:
            print(f"  {tk:6s} n=0")
            continue
        print(f"  {tk:6s} n={s['n']:3d}  fwd1d={s['avg_fwd1d']:+.2f}% "
              f"win={s['win_fwd1d']:.0f}%  geom={s['avg_geom']:+.2f}% "
              f"win={s['win_geom']:.0f}%")

    g = summarize("gated", gated)
    u = summarize("all", unfiltered)
    if g["n"] and u["n"]:
        print(f"\nGATE EFFECT: kept {g['n']}/{u['n']} "
              f"({g['n']/u['n']*100:.0f}%) of catalyst days")
        print(f"  fwd1d avg {u['avg_fwd1d']:+.2f}% -> {g['avg_fwd1d']:+.2f}%  "
              f"win {u['win_fwd1d']:.0f}% -> {g['win_fwd1d']:.0f}%")
        print(f"  geom     {u['avg_geom']:+.2f}% -> {g['avg_geom']:+.2f}%  "
              f"win {u['win_geom']:.0f}% -> {g['win_geom']:.0f}%")
        if g["avg_geom"] > u["avg_geom"] and g["win_geom"] >= u["win_geom"] - 2:
            verdict = "GATE HELPS (keep RALLY_TREND_VETO + broad veto)"
        elif g["avg_geom"] >= 0:
            verdict = "GATE NEUTRAL/MARGINAL — keep defaults, validate live"
        else:
            verdict = "GATE HURTS — consider widening RALLY_TREND_VETO_PCT"
        print(f"VERDICT: {verdict}")
        print("NOTE: tick-orderbook confirmation is NOT in this backtest — "
              "live/shadow rally_arms outcomes are required before going live.")


if __name__ == "__main__":
    main()
