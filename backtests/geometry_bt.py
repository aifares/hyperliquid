"""Backtest 3: do our stop/target geometries fit how these markets move?

Current bot geometry (from notifier.stop_price + watcher.target_price):
  scalp @20x: stop 2.5% raw, target 5.0% raw (2R), max hold 4h,
              FADE eligible at +1.25% raw (0.5R)
  swing @5x : stop 10% raw, target 20% raw, max hold 72h, FADE at +10%

Method: enter every N bars in BOTH directions (agnostic to signal quality —
this measures the geometry, not the signal), walk bars forward, first touch
wins (stop checked before target inside the same bar = conservative).
Outputs the exit mix and net expectancy per outcome including 0.11% friction.
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import FRICTION, hl_candles

COINS = ["xyz:NVDA", "xyz:META", "xyz:TSLA", "xyz:AAPL", "xyz:MSFT",
         "xyz:GOOGL", "xyz:AMZN", "xyz:AMD", "xyz:MU", "xyz:INTC",
         "xyz:XYZ100", "xyz:SP500", "BTC"]


def simulate(bars: list[dict], every: int, window: int,
             stop_f: float, tgt_f: float, fade_f: float) -> dict | None:
    if len(bars) < window + every:
        return None
    outcomes = {"TARGET": [], "STOP": [], "TIME": []}
    fade_eligible = 0
    n = 0
    for i in range(0, len(bars) - window, every):
        entry = float(bars[i]["c"])
        if entry <= 0:
            continue
        for direction in (1, -1):    # long, short
            n += 1
            stop = entry * (1 - direction * stop_f)
            tgt = entry * (1 + direction * tgt_f)
            fade = entry * (1 + direction * fade_f)
            hit = "TIME"
            exit_px = float(bars[i + window]["c"])
            saw_fade = False
            for j in range(i + 1, i + window + 1):
                hi, lo = float(bars[j]["h"]), float(bars[j]["l"])
                if direction == 1:
                    if hi >= fade:
                        saw_fade = True
                    if lo <= stop:
                        hit, exit_px = "STOP", stop
                        break
                    if hi >= tgt:
                        hit, exit_px = "TARGET", tgt
                        break
                else:
                    if lo <= fade:
                        saw_fade = True
                    if hi >= stop:
                        hit, exit_px = "STOP", stop
                        break
                    if lo <= tgt:
                        hit, exit_px = "TARGET", tgt
                        break
            ret = direction * (exit_px / entry - 1) - FRICTION
            outcomes[hit].append(ret)
            if saw_fade:
                fade_eligible += 1
    total = sum(len(v) for v in outcomes.values())
    if not total:
        return None
    return {
        "n": total,
        "mix": {k: len(v) / total for k, v in outcomes.items()},
        "mean": {k: (st.mean(v) if v else None) for k, v in outcomes.items()},
        "fade_ok": fade_eligible / total,
        "expectancy": st.mean([r for v in outcomes.values() for r in v]),
    }


def report(title: str, interval: str, days: int, every: int, window: int,
           stop_f: float, tgt_f: float, fade_f: float) -> None:
    print(f"\n=== {title}: stop {stop_f*100:.1f}%  target {tgt_f*100:.1f}%  "
          f"window {window} bars ({interval}) ===")
    print(f"{'coin':11s} {'n':>5s} {'🎯tgt':>6s} {'🛑stop':>6s} {'⏰time':>6s}"
          f" {'fade-elig':>9s} {'expectancy':>10s}")
    for c in COINS:
        bars = hl_candles(c, interval, days)
        r = simulate(bars, every, window, stop_f, tgt_f, fade_f)
        if r is None:
            print(f"{c:11s} insufficient data")
            continue
        m = r["mix"]
        print(f"{c:11s} {r['n']:>5d} {m['TARGET']*100:>5.1f}% {m['STOP']*100:>5.1f}%"
              f" {m['TIME']*100:>5.1f}% {r['fade_ok']*100:>8.1f}%"
              f" {r['expectancy']*100:>+9.3f}%")


def main() -> None:
    # scalp: 1m bars, 4h = 240 bars, entries every 15 min
    report("SCALP geometry @20x", "1m", 5, 15, 240,
           stop_f=0.025, tgt_f=0.05, fade_f=0.0125)
    # swing: 1h bars, 72h = 72 bars, entries every 4h
    report("SWING geometry @5x", "1h", 400, 4, 72,
           stop_f=0.10, tgt_f=0.20, fade_f=0.10)
    print("\nNote: entries are direction-agnostic (both ways at every step) — "
          "this measures whether the geometry is reachable at all, not signal "
          "quality. Expectancy ≈ -friction is the no-edge baseline (-0.11%).")


if __name__ == "__main__":
    main()
