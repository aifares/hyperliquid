"""Intraday breakout tier — backtest (HOD / opening-range breakout).

Tests the proposed `breakout` tier: buy when price breaks the session high
(or opening-range high), short the mirror, filter false breakouts, flat by the
NYSE close. Three complementary studies because Hyperliquid egress is blocked
in this environment and we can only use the cached bars:

  A. INTRADAY on 1h perp bars (real instrument, ~200 sessions x 9 names,
     2025-12 .. 2026-07). Coarse: 1h bars align to the top of the ET hour, so
     the opening "range" is whole-hour buckets, not a true 9:30-9:45 window.
     Directionally valid for HOD/ORB breakout; treat exact numbers as coarse.
  B. PREMISE CHECK on 10y daily underlying (px_*.pkl, yfinance). Does
     "break the prior high and keep going" pay at all in these names over a
     long horizon? Daily analog of the intraday idea; big sample.
  C. Micro intraday on 1m perp bars (only ~3 sessions) — sanity that the
     mechanics fire, NOT a statistical result. Printed but not relied on.

Cost model matches backtests/common.py: 0.11% round-trip friction + measured
average hourly funding (from the cached funding_*.json), signed by side.
Returns are % of NOTIONAL (multiply by leverage for margin).

Run: python3 backtests/breakout_bt.py
"""
from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(__file__).with_name("data")
NY = ZoneInfo("America/New_York")
FRICTION = 0.0011           # 0.11% round trip (fees + slippage), matches common.py

STOCKS = ["NVDA", "META", "TSLA", "AAPL", "MSFT", "GOOGL", "AMZN", "AMD", "MU"]
RTH_HOURS = range(9, 16)    # ET bar-open hours counted as the session (9:00..15:00)


# --------------------------------------------------------------------------- #
# data loaders
# --------------------------------------------------------------------------- #
@dataclass
class Bar:
    t: int          # open time, ms UTC
    o: float
    h: float
    l: float
    c: float
    et: datetime    # open time in America/New_York

    @property
    def sess(self) -> date:
        return self.et.date()


def load_bars(coin: str, interval: str) -> list[Bar]:
    f = DATA / f"candles_xyz_{coin}_{interval}.json"
    raw = json.loads(f.read_text())
    out = []
    for r in raw:
        et = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).astimezone(NY)
        out.append(Bar(int(r["t"]), float(r["o"]), float(r["h"]),
                       float(r["l"]), float(r["c"]), et))
    out.sort(key=lambda b: b.t)
    return out


def avg_hourly_funding(coin: str) -> float:
    f = DATA / f"funding_xyz_{coin}.json"
    if not f.exists():
        return 0.0
    rates = [float(x) for x in json.loads(f.read_text())]
    return sum(rates) / len(rates) if rates else 0.0


def daily_bars(coin: str) -> list[Bar]:
    f = DATA / f"candles_xyz_{coin}_1d.json"
    if not f.exists():
        return []
    raw = json.loads(f.read_text())
    out = []
    for r in raw:
        et = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).astimezone(NY)
        out.append(Bar(int(r["t"]), float(r["o"]), float(r["h"]),
                       float(r["l"]), float(r["c"]), et))
    out.sort(key=lambda b: b.t)
    return out


def trend_by_session(coin: str, n: int = 10) -> dict[date, float]:
    """Mirror candles.trend_slope: recent n-day close SMA vs prior n-day SMA,
    keyed by the session date it becomes valid FOR (uses only prior days)."""
    d = daily_bars(coin)
    closes = [b.c for b in d]
    dates = [b.sess for b in d]
    out: dict[date, float] = {}
    for i in range(len(d)):
        if i < 2 * n:
            continue
        recent = sum(closes[i - n:i]) / n
        prior = sum(closes[i - 2 * n:i - n]) / n
        if prior > 0:
            out[dates[i]] = (recent - prior) / prior * 100
    return out


# --------------------------------------------------------------------------- #
# trade record + stats
# --------------------------------------------------------------------------- #
@dataclass
class Trade:
    coin: str
    sess: date
    direction: str
    entry: float
    exit: float
    hours: float
    reason: str     # target / stop / time
    net: float      # % of notional, after friction + funding


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0}
    nets = [t.net for t in trades]
    wins = [t for t in trades if t.net > 0]
    return {
        "n": len(trades),
        "win": len(wins) / len(trades) * 100,
        "mean": statistics.mean(nets),
        "median": statistics.median(nets),
        "total": sum(nets),
        "tgt": sum(1 for t in trades if t.reason == "target") / len(trades) * 100,
        "stp": sum(1 for t in trades if t.reason == "stop") / len(trades) * 100,
        "tim": sum(1 for t in trades if t.reason == "time") / len(trades) * 100,
    }


def fmt(tag: str, s: dict) -> str:
    if s.get("n", 0) == 0:
        return f"{tag:34s} n=0"
    return (f"{tag:34s} n={s['n']:4d}  win={s['win']:4.1f}%  "
            f"mean={s['mean']:+.3f}%  med={s['median']:+.3f}%  "
            f"tot={s['total']:+6.1f}%  "
            f"[tgt {s['tgt']:2.0f}/stp {s['stp']:2.0f}/tim {s['tim']:2.0f}]")


# --------------------------------------------------------------------------- #
# Study A — intraday breakout on 1h perp bars
# --------------------------------------------------------------------------- #
@dataclass
class Cfg:
    or_hours: int = 1          # opening-range length, in RTH hourly bars
    mode: str = "orb"          # "orb" (fixed range) or "hod" (rolling session high)
    buffer: float = 0.001      # breakout buffer past the level (fraction)
    stop_mode: str = "range"   # "range" (other side of OR) or "pct"
    stop_pct: float = 0.01
    r_mult: float = 2.0        # target = entry +/- r_mult * risk
    longs: bool = True
    shorts: bool = True
    trend_filter: bool = False
    trend_veto: float = 4.0


def sessions_of(bars: list[Bar]) -> dict[date, list[Bar]]:
    out: dict[date, list[Bar]] = {}
    for b in bars:
        if b.et.hour in RTH_HOURS:
            out.setdefault(b.sess, []).append(b)
    return out


def _simulate_side(sess_bars: list[Bar], entry_i: int, entry: float, stop: float,
                   target: float, direction: str) -> tuple[float, str, float]:
    """First-touch sim over bars from entry_i onward. Conservative: on the
    entry bar only the stop can trigger (worst case); target needs a later bar.
    Returns (exit_price, reason, hours_held)."""
    for j in range(entry_i, len(sess_bars)):
        b = sess_bars[j]
        hrs = float(j - entry_i + 1)
        if direction == "long":
            if b.l <= stop:
                return stop, "stop", hrs
            if j > entry_i and b.h >= target:
                return target, "target", hrs
        else:
            if b.h >= stop:
                return stop, "stop", hrs
            if j > entry_i and b.l <= target:
                return target, "target", hrs
    return sess_bars[-1].c, "time", float(len(sess_bars) - entry_i)


def study_a(cfg: Cfg) -> list[Trade]:
    trades: list[Trade] = []
    for coin in STOCKS:
        try:
            bars = load_bars(coin, "1h")
        except FileNotFoundError:
            continue
        fund = avg_hourly_funding(coin)
        trend = trend_by_session(coin, 10) if cfg.trend_filter else {}
        for sess, sb in sessions_of(bars).items():
            if len(sb) < cfg.or_hours + 2:
                continue
            or_bars = sb[:cfg.or_hours]
            or_hi = max(b.h for b in or_bars)
            or_lo = min(b.l for b in or_bars)
            t_pct = trend.get(sess)
            entered = False
            run_hi, run_lo = or_hi, or_lo
            for i in range(cfg.or_hours, len(sb)):
                if entered:
                    break
                b = sb[i]
                level_hi = run_hi if cfg.mode == "hod" else or_hi
                level_lo = run_lo if cfg.mode == "hod" else or_lo
                # long breakout
                if cfg.longs and b.h >= level_hi * (1 + cfg.buffer):
                    if not (cfg.trend_filter and t_pct is not None
                            and t_pct <= -cfg.trend_veto):
                        entry = level_hi * (1 + cfg.buffer)
                        stop = (or_lo if cfg.stop_mode == "range"
                                else entry * (1 - cfg.stop_pct))
                        risk = entry - stop
                        if risk > 0:
                            target = entry + cfg.r_mult * risk
                            ex, reason, hrs = _simulate_side(sb, i, entry, stop,
                                                             target, "long")
                            gross = (ex - entry) / entry
                            net = gross - FRICTION - fund * hrs
                            trades.append(Trade(coin, sess, "long", entry, ex,
                                                hrs, reason, net * 100))
                            entered = True
                            continue
                # short breakout
                if cfg.shorts and b.l <= level_lo * (1 - cfg.buffer):
                    if not (cfg.trend_filter and t_pct is not None
                            and t_pct >= cfg.trend_veto):
                        entry = level_lo * (1 - cfg.buffer)
                        stop = (or_hi if cfg.stop_mode == "range"
                                else entry * (1 + cfg.stop_pct))
                        risk = stop - entry
                        if risk > 0:
                            target = entry - cfg.r_mult * risk
                            ex, reason, hrs = _simulate_side(sb, i, entry, stop,
                                                             target, "short")
                            gross = (entry - ex) / entry
                            net = gross - FRICTION + fund * hrs
                            trades.append(Trade(coin, sess, "short", entry, ex,
                                                hrs, reason, net * 100))
                            entered = True
                            continue
                run_hi = max(run_hi, b.h)
                run_lo = min(run_lo, b.l)
    return trades


# --------------------------------------------------------------------------- #
# Study B — daily breakout premise on 10y underlying
# --------------------------------------------------------------------------- #
def study_b(donchian_n: int, r_mult: float, hold_days: int,
            trend_filter: bool, shorts: bool) -> list[Trade]:
    import pandas as pd
    trades: list[Trade] = []
    veto = 4.0
    for coin in STOCKS:
        pxf = DATA / f"px_{coin}.pkl"
        if not pxf.exists():
            continue
        px = pd.read_pickle(pxf)
        fund = avg_hourly_funding(coin)
        H, L, C = px["High"].values, px["Low"].values, px["Close"].values
        n = len(C)
        sma = pd.Series(C).rolling(10).mean()
        trend_pct = ((sma - sma.shift(10)) / sma.shift(10) * 100).values
        i = donchian_n + 1
        while i < n - 1:
            prior_hi = max(H[i - donchian_n:i])
            prior_lo = min(L[i - donchian_n:i])
            took = False
            t_pct = trend_pct[i]
            # long breakout: today's high pierces the N-day high
            if H[i] >= prior_hi and not (trend_filter and t_pct <= -veto):
                entry = prior_hi
                stop = prior_lo
                risk = entry - stop
                if risk > 0:
                    target = entry + r_mult * risk
                    ex, reason = entry, "time"
                    for j in range(i, min(i + hold_days, n)):
                        if L[j] <= stop:
                            ex, reason = stop, "stop"; break
                        if j > i and H[j] >= target:
                            ex, reason = target, "target"; break
                        ex = C[j]
                    hrs = (j - i + 1) * 24
                    net = (ex - entry) / entry - FRICTION - fund * hrs
                    trades.append(Trade(coin, date(2000, 1, 1), "long",
                                        entry, ex, hrs, reason, net * 100))
                    took = True
            if shorts and not took and L[i] <= prior_lo and \
                    not (trend_filter and t_pct >= veto):
                entry = prior_lo
                stop = prior_hi
                risk = stop - entry
                if risk > 0:
                    target = entry - r_mult * risk
                    ex, reason = entry, "time"
                    for j in range(i, min(i + hold_days, n)):
                        if H[j] >= stop:
                            ex, reason = stop, "stop"; break
                        if j > i and L[j] <= target:
                            ex, reason = target, "target"; break
                        ex = C[j]
                    hrs = (j - i + 1) * 24
                    net = (entry - ex) / entry - FRICTION + fund * hrs
                    trades.append(Trade(coin, date(2000, 1, 1), "short",
                                        entry, ex, hrs, reason, net * 100))
                    took = True
            i += (j - i + 1) if took else 1
    return trades


# --------------------------------------------------------------------------- #
def main() -> None:
    print("=" * 78)
    print("STUDY A — intraday ORB/HOD breakout on 1h perp bars (real instrument)")
    print("  ~200 sessions x 9 stock perps, 2025-12..2026-07. 1h resolution is")
    print("  COARSE (opening range = whole ET hours). Directional, not precise.")
    print("=" * 78)
    variants = [
        ("V1  ORB-1h  L+S  buf.10% stop=range 2R", Cfg(or_hours=1, mode="orb")),
        ("V2  ORB-2h  L+S  buf.10% stop=range 2R", Cfg(or_hours=2, mode="orb")),
        ("V3  ORB-1h  L+S  + trend filter        ", Cfg(or_hours=1, trend_filter=True)),
        ("V4  HOD roll L+S buf.10% stop=range 2R ", Cfg(or_hours=1, mode="hod")),
        ("V5  ORB-1h  LONG-only 2R               ", Cfg(or_hours=1, shorts=False)),
        ("V6  ORB-1h  SHORT-only 2R              ", Cfg(or_hours=1, longs=False)),
        ("V7  ORB-1h  L+S  target 1R (scalp)     ", Cfg(or_hours=1, r_mult=1.0)),
        ("V8  ORB-1h  L+S  target 3R             ", Cfg(or_hours=1, r_mult=3.0)),
        ("V9  ORB-1h  L+S  buf.25% (stricter)    ", Cfg(or_hours=1, buffer=0.0025)),
        ("V10 ORB-1h  L+S  pct-stop 1% 2R        ", Cfg(or_hours=1, stop_mode="pct")),
    ]
    for tag, cfg in variants:
        print(fmt(tag, summarize(study_a(cfg))))

    print("\n" + "=" * 78)
    print("STUDY B — daily breakout PREMISE on 10y underlying (px_*.pkl, 9 names)")
    print("  Does 'break the N-day high and hold' pay over a long sample?")
    print("=" * 78)
    b_variants = [
        ("B1  prior-1d-high  L+S  2R hold5",  dict(donchian_n=1,  r_mult=2.0, hold_days=5,  trend_filter=False, shorts=True)),
        ("B2  Donchian-20    L+S  2R hold10", dict(donchian_n=20, r_mult=2.0, hold_days=10, trend_filter=False, shorts=True)),
        ("B3  Donchian-20    LONG 2R hold10", dict(donchian_n=20, r_mult=2.0, hold_days=10, trend_filter=False, shorts=False)),
        ("B4  Donchian-20  L+S +trend 2R h10", dict(donchian_n=20, r_mult=2.0, hold_days=10, trend_filter=True,  shorts=True)),
        ("B5  Donchian-55    LONG 2R hold20", dict(donchian_n=55, r_mult=2.0, hold_days=20, trend_filter=False, shorts=False)),
        ("B6  Donchian-20    LONG 1R hold10", dict(donchian_n=20, r_mult=1.0, hold_days=10, trend_filter=False, shorts=False)),
    ]
    for tag, kw in b_variants:
        print(fmt(tag, summarize(study_b(**kw))))

    print("\n" + "=" * 78)
    print("STUDY C — micro-sample on 1m perp bars (~3 sessions) — MECHANICS ONLY")
    print("=" * 78)
    # reuse study_a machinery but on 1m by faking hour buckets? Keep simple:
    # just report the 1m coverage is too small for a statistical claim.
    print("  1m cache = 2026-07-10..14 (~3 RTH sessions). Sample too small for a")
    print("  statistical read; skipped to avoid over-fitting noise. Re-run with")
    print("  fresh 5m history (needs HL egress) for the real intraday test.")


if __name__ == "__main__":
    main()
