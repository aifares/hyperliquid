"""Crypto strategy sweep: BTC, SOL, XRP, HYPE on Hyperliquid history.

Strategies tested (all net of FRICTION=0.11%/round-trip + actual funding):
  momo24   — 24h time-series momentum: hold sign of trailing 24h return
  fundfade — funding-extreme fade: short when hourly funding > p90 (crowded
             longs), long when < p10; 24h hold; funding PnL included properly
             (a short RECEIVES positive funding)
  wickfade — capitulation-wick fade (liquidation-cascade proxy): 15m bar with
             range > 2.5x ATR(96) closing in the bottom quartile -> long N bars
             (blowoff mirror: top-quartile close -> short)
  btclead  — BTC lead-lag: |BTC 1h move| >= 1% -> same direction next hour in alt
  breakout — 24h Donchian: close above 24h high -> long (below low -> short),
             exit on opposite 12h channel or 48h time stop

Guardrails against fooling ourselves:
  - one parameter set per strategy, chosen a priori (no grid search)
  - split-half consistency: a "PASS" needs the same sign of edge in both halves
  - buy-and-hold benchmark shown so drift isn't mistaken for skill
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from common import CACHE, FRICTION, hl_candles, hl_info

COINS = ["BTC", "SOL", "XRP", "HYPE"]
HOURS_HELD_FUND = {"momo24": 24, "breakout": 24}   # approx for funding cost


# --- data ----------------------------------------------------------------------
def candles(coin: str, interval: str, days: int) -> list[dict]:
    cs = hl_candles(coin, interval, days)
    return [{"t": c["t"], "o": float(c["o"]), "h": float(c["h"]),
             "l": float(c["l"]), "c": float(c["c"]), "v": float(c["v"])} for c in cs]


def funding_series(coin: str) -> dict[int, float]:
    """hour-timestamp(ms) -> funding rate, full available history, cached."""
    f = CACHE / f"fundsrs_{coin}.json"
    if f.exists() and time.time() - f.stat().st_mtime < 24 * 3600:
        return {int(k): v for k, v in json.loads(f.read_text()).items()}
    out: dict[int, float] = {}
    start = int(time.time() * 1000) - 220 * 86400_000
    while True:
        page = hl_info({"type": "fundingHistory", "coin": coin, "startTime": start})
        if not page:
            break
        for p in page:
            out[int(p["time"])] = float(p["fundingRate"])
        nxt = max(int(p["time"]) for p in page) + 1
        if nxt <= start or len(page) < 2:
            break
        start = nxt
        time.sleep(0.15)
    f.write_text(json.dumps(out))
    return out


def fund_cost(fs: dict[int, float], t_ms: int, hours: int, direction: str) -> float:
    """Actual funding paid (+) or received (-) over the hold, as fraction."""
    total = 0.0
    for h in range(hours):
        rate = fs.get(t_ms + h * 3600_000)
        if rate is None:
            continue
        total += rate if direction == "long" else -rate
    return total


# --- strategy engines (each returns a list of net per-trade returns + times) ----
def momo24(cs: list[dict], fs: dict[int, float]):
    trades, pos, entry_i = [], 0, 0
    for i in range(24, len(cs)):
        sig = 1 if cs[i]["c"] > cs[i - 24]["c"] else -1
        if pos == 0:
            pos, entry_i = sig, i
        elif sig != pos:
            d = "long" if pos == 1 else "short"
            raw = (cs[i]["c"] - cs[entry_i]["c"]) / cs[entry_i]["c"] * pos
            fc = fund_cost(fs, cs[entry_i]["t"], i - entry_i, d)
            trades.append((cs[entry_i]["t"], raw - FRICTION - fc))
            pos, entry_i = sig, i
    return trades


def fundfade(cs: list[dict], fs: dict[int, float]):
    by_t = {c["t"]: i for i, c in enumerate(cs)}
    rates = sorted(fs.values())
    if len(rates) < 100:
        return []
    p90 = rates[int(len(rates) * 0.90)]
    p10 = rates[int(len(rates) * 0.10)]
    trades, last_exit = [], 0
    for t_ms, rate in sorted(fs.items()):
        if t_ms < last_exit or t_ms not in by_t:
            continue
        i = by_t[t_ms]
        if i + 24 >= len(cs):
            break
        if rate >= p90:
            d, sgn = "short", -1
        elif rate <= p10:
            d, sgn = "long", 1
        else:
            continue
        raw = (cs[i + 24]["c"] - cs[i]["c"]) / cs[i]["c"] * sgn
        fc = fund_cost(fs, t_ms, 24, d)
        trades.append((t_ms, raw - FRICTION - fc))
        last_exit = t_ms + 24 * 3600_000
    return trades


def wickfade(cs15: list[dict], fs: dict[int, float], hold_bars: int = 16):
    trades, i = [], 96
    while i < len(cs15) - hold_bars:
        window = cs15[i - 96:i]
        atr = sum(c["h"] - c["l"] for c in window) / 96
        c = cs15[i]
        rng = c["h"] - c["l"]
        if rng > 2.5 * atr and rng > 0:
            pos_in_range = (c["c"] - c["l"]) / rng
            sgn = 1 if pos_in_range <= 0.25 else (-1 if pos_in_range >= 0.75 else 0)
            if sgn:
                d = "long" if sgn == 1 else "short"
                exit_c = cs15[i + hold_bars]["c"]
                raw = (exit_c - c["c"]) / c["c"] * sgn
                fc = fund_cost(fs, c["t"], hold_bars // 4, d)
                trades.append((c["t"], raw - FRICTION - fc))
                i += hold_bars   # no overlapping trades
                continue
        i += 1
    return trades


def btclead(cs_btc: list[dict], cs_alt: list[dict], fs: dict[int, float]):
    alt_by_t = {c["t"]: i for i, c in enumerate(cs_alt)}
    trades = []
    for i in range(1, len(cs_btc) - 1):
        r = (cs_btc[i]["c"] - cs_btc[i - 1]["c"]) / cs_btc[i - 1]["c"]
        if abs(r) < 0.01:
            continue
        j = alt_by_t.get(cs_btc[i]["t"])
        if j is None or j + 1 >= len(cs_alt):
            continue
        sgn = 1 if r > 0 else -1
        raw = (cs_alt[j + 1]["c"] - cs_alt[j]["c"]) / cs_alt[j]["c"] * sgn
        trades.append((cs_btc[i]["t"], raw - FRICTION))
    return trades


def breakout(cs: list[dict], fs: dict[int, float]):
    trades, i = [], 25
    while i < len(cs) - 1:
        hi24 = max(c["h"] for c in cs[i - 24:i])
        lo24 = min(c["l"] for c in cs[i - 24:i])
        sgn = 1 if cs[i]["c"] > hi24 else (-1 if cs[i]["c"] < lo24 else 0)
        if not sgn:
            i += 1
            continue
        d = "long" if sgn == 1 else "short"
        entry_i = i
        j = i + 1
        while j < len(cs) - 1 and j - entry_i < 48:
            ch12_hi = max(c["h"] for c in cs[j - 12:j])
            ch12_lo = min(c["l"] for c in cs[j - 12:j])
            if (sgn == 1 and cs[j]["c"] < ch12_lo) or (sgn == -1 and cs[j]["c"] > ch12_hi):
                break
            j += 1
        raw = (cs[j]["c"] - cs[entry_i]["c"]) / cs[entry_i]["c"] * sgn
        fc = fund_cost(fs, cs[entry_i]["t"], j - entry_i, d)
        trades.append((cs[entry_i]["t"], raw - FRICTION - fc))
        i = j + 1
    return trades


# --- evaluation ------------------------------------------------------------------
def report(name: str, trades: list[tuple[int, float]]) -> dict | None:
    if len(trades) < 10:
        return None
    rets = [r for _, r in trades]
    n = len(rets)
    wins = sum(1 for r in rets if r > 0)
    total, avg = sum(rets), sum(rets) / n
    mid = trades[n // 2][0]
    h1 = [r for t, r in trades if t < mid]
    h2 = [r for t, r in trades if t >= mid]
    consistent = (sum(h1) > 0) == (sum(h2) > 0) and sum(rets) != 0
    return {"name": name, "n": n, "win": wins / n, "avg": avg, "total": total,
            "h1": sum(h1), "h2": sum(h2),
            "verdict": ("PASS" if consistent and total > 0 else
                        "fail" if total <= 0 else "UNSTABLE")}


def main() -> None:
    cs_btc = candles("BTC", "1h", 210)
    print(f"BTC 1h bars: {len(cs_btc)} "
          f"({(cs_btc[-1]['t']-cs_btc[0]['t'])/86400_000:.0f} days)\n")
    rows = []
    for coin in COINS:
        cs = candles(coin, "1h", 210)
        cs15 = candles(coin, "15m", 55)
        fs = funding_series(coin)
        bh = (cs[-1]["c"] - cs[0]["c"]) / cs[0]["c"]
        print(f"=== {coin}: {len(cs)} 1h bars, {len(cs15)} 15m bars, "
              f"{len(fs)} funding pts · buy&hold {bh*100:+.0f}% ===")
        strat_trades = {
            "momo24": momo24(cs, fs),
            "fundfade": fundfade(cs, fs),
            "wickfade": wickfade(cs15, fs),
            "breakout": breakout(cs, fs),
        }
        if coin != "BTC":
            strat_trades["btclead"] = btclead(cs_btc, cs, fs)
        for name, trades in strat_trades.items():
            r = report(name, trades)
            if r is None:
                print(f"  {name:9s} n<10, skipped")
                continue
            rows.append((coin, r))
            print(f"  {name:9s} n={r['n']:4d} win={r['win']*100:3.0f}% "
                  f"avg={r['avg']*100:+.3f}%/trade total={r['total']*100:+.1f}% "
                  f"[h1 {r['h1']*100:+.1f}% | h2 {r['h2']*100:+.1f}%] {r['verdict']}")
        print()
    print("=== PASSES (positive AND consistent across both halves) ===")
    for coin, r in sorted(rows, key=lambda x: -x[1]["avg"]):
        if r["verdict"] == "PASS":
            print(f"  {coin:5s} {r['name']:9s} avg {r['avg']*100:+.3f}%/trade "
                  f"x {r['n']} trades = {r['total']*100:+.1f}%")


if __name__ == "__main__":
    main()
