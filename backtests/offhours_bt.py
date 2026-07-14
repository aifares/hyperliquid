"""Backtest 2: off-hours behavior of the xyz stock perps.

Questions:
  1. How much do these perps move while NYSE is closed vs during RTH?
  2. Do overnight (last RTH close -> next RTH open) moves CONTINUE through the
     next session (real repricing) or REVERT (thin-book noise)?

Uses all available 1h Hyperliquid candles per market (~5000 bars max).
"""
from __future__ import annotations

import statistics as st
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from common import hl_candles

NY = ZoneInfo("America/New_York")
COINS = ["xyz:NVDA", "xyz:META", "xyz:TSLA", "xyz:AAPL", "xyz:MSFT",
         "xyz:GOOGL", "xyz:AMZN", "xyz:AMD", "xyz:MU", "xyz:INTC",
         "xyz:XYZ100", "xyz:SP500"]


def bucket(ts_ms: int) -> str:
    d = datetime.fromtimestamp(ts_ms / 1000, tz=NY)
    if d.weekday() >= 5:
        return "weekend"
    if 10 <= d.hour < 16:          # full RTH hour bars (skip the 9:30 partial)
        return "rth"
    if d.hour == 9:
        return "skip"
    return "offhours"


def run_coin(coin: str) -> dict | None:
    bars = hl_candles(coin, "1h", 400)
    if len(bars) < 500:
        return None
    # per-bar absolute moves by bucket
    moves: dict[str, list[float]] = {"rth": [], "offhours": [], "weekend": []}
    for b in bars:
        o, c = float(b["o"]), float(b["c"])
        if o <= 0:
            continue
        k = bucket(b["t"])
        if k in moves:
            moves[k].append(abs(c / o - 1))

    # overnight persistence: close of last RTH bar -> open of first RTH bar
    # next trading day (the "overnight gap"), then that day's RTH return.
    pairs: list[tuple[float, float]] = []
    days: dict = {}
    for b in bars:
        if bucket(b["t"]) != "rth":
            continue
        d = datetime.fromtimestamp(b["t"] / 1000, tz=NY).date()
        rec = days.setdefault(d, {"open": float(b["o"]), "close": float(b["c"])})
        rec["close"] = float(b["c"])
    ordered = sorted(days)
    for i in range(1, len(ordered)):
        prev = days[ordered[i - 1]]
        cur = days[ordered[i]]
        if prev["close"] > 0 and cur["open"] > 0:
            overnight = cur["open"] / prev["close"] - 1
            intraday = cur["close"] / cur["open"] - 1
            pairs.append((overnight, intraday))

    big = [(o, i) for o, i in pairs if abs(o) >= 0.005]   # gaps >= 0.5%
    cont = sum(1 for o, i in big if o * i > 0)
    out = {
        "rth_vol": st.mean(moves["rth"]) if moves["rth"] else 0,
        "off_vol": st.mean(moves["offhours"]) if moves["offhours"] else 0,
        "wkd_vol": st.mean(moves["weekend"]) if moves["weekend"] else 0,
        "n_days": len(pairs),
        "n_gaps": len(big),
        "continue_rate": cont / len(big) if big else None,
        "avg_intraday_after_gap": st.mean(i for _, i in big) if big else None,
    }
    return out


def main() -> None:
    print(f"{'coin':11s} {'RTH |bar|':>9s} {'off |bar|':>9s} {'wkd |bar|':>9s}"
          f" {'gaps≥.5%':>8s} {'continue':>8s}")
    rates = []
    for c in COINS:
        r = run_coin(c)
        if r is None:
            print(f"{c:11s} insufficient data")
            continue
        cr = f"{r['continue_rate']*100:.0f}%" if r["continue_rate"] is not None else "—"
        print(f"{c:11s} {r['rth_vol']*100:>8.3f}% {r['off_vol']*100:>8.3f}%"
              f" {r['wkd_vol']*100:>8.3f}% {r['n_gaps']:>8d} {cr:>8s}")
        if r["continue_rate"] is not None:
            rates.append((r["continue_rate"], r["n_gaps"]))
    if rates:
        tot = sum(n for _, n in rates)
        w = sum(r * n for r, n in rates) / tot
        print(f"\nWeighted continuation rate after >=0.5% overnight gaps: "
              f"{w*100:.0f}% over {tot} gaps "
              f"(>50% = gaps continue; <50% = gaps revert)")


if __name__ == "__main__":
    main()
