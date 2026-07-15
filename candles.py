"""Live daily OHLC candles for swing technical signals.

Generalizes backtests/common.py:hl_candles for live use: fetches daily bars
per coin from Hyperliquid's candleSnapshot endpoint and caches them in memory,
refreshed periodically by run() (called from main.py). Powers
swing_signals.py's trend/breakout/ATR calculations — a multi-WEEK lookback,
unlike trend.py's single-day change or tape.py's 30-second window.

Pure data layer: no opinions about direction/conviction live here.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from dataclasses import dataclass

import config

_TTL_S = 1800.0       # 30 min default staleness tolerance for a lazy refresh
_LOOKBACK_DAYS = 40   # covers a 20d Donchian + a bit of trend/ATR history
_INTERVAL = "1d"

_cache: dict[str, list["Candle"]] = {}   # coin -> candles, oldest..newest
_fetched_at: dict[str, float] = {}


@dataclass
class Candle:
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float


def _parse(raw: list[dict]) -> list[Candle]:
    out = []
    for r in raw or []:
        try:
            out.append(Candle(
                t=int(r["t"]), o=float(r["o"]), h=float(r["h"]),
                l=float(r["l"]), c=float(r["c"]), v=float(r.get("v", 0) or 0)))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _fetch_sync(coin: str) -> list[Candle]:
    now_ms = int(time.time() * 1000)
    body = {"type": "candleSnapshot", "req": {
        "coin": coin, "interval": _INTERVAL,
        "startTime": now_ms - _LOOKBACK_DAYS * 86_400_000, "endTime": now_ms}}
    req = urllib.request.Request(
        config.HL_INFO_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = json.load(r)
    return _parse(raw)


def refresh(coin: str) -> None:
    """Blocking refresh for one coin — call via asyncio.to_thread from async
    code, or directly from a background thread/loop (see run())."""
    try:
        candles = _fetch_sync(coin)
        if candles:
            _cache[coin] = candles
            _fetched_at[coin] = time.time()
    except Exception as e:  # noqa: BLE001 — stale beats broken
        print(f"[candles] refresh failed for {coin} (serving stale): {e!r}")


def get(coin: str) -> list[Candle]:
    """Daily candles for `coin`, oldest-first. Lazily refreshes inline if
    stale or never fetched — same pattern as trend.day_change_pct(); the
    periodic run() task keeps this from being hit on the hot path normally."""
    if time.time() - _fetched_at.get(coin, 0.0) > _TTL_S:
        refresh(coin)
    return _cache.get(coin, [])


def trend_slope(coin: str, n: int = 10) -> float | None:
    """% change of the last n-day close SMA vs the PRIOR n-day SMA — positive
    = uptrend, negative = downtrend. None if there isn't enough history yet."""
    candles = get(coin)
    if len(candles) < 2 * n:
        return None
    closes = [c.c for c in candles]
    recent = sum(closes[-n:]) / n
    prior = sum(closes[-2 * n:-n]) / n
    if prior <= 0:
        return None
    return (recent - prior) / prior * 100


def donchian(coin: str, n: int = 20) -> tuple[float, float] | None:
    """(n-day high, n-day low), EXCLUDING today's still-forming bar."""
    candles = get(coin)
    if len(candles) < n + 1:
        return None
    window = candles[-(n + 1):-1]
    if not window:
        return None
    return max(c.h for c in window), min(c.l for c in window)


def atr(coin: str, n: int = 14) -> float | None:
    """Average true range over the last n complete days, in raw price units."""
    candles = get(coin)
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(-n, 0):
        cur, prev = candles[i], candles[i - 1]
        trs.append(max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c)))
    return sum(trs) / len(trs) if trs else None


def atr_pct(coin: str, n: int = 14) -> float | None:
    """ATR as a fraction of the latest close — for a volatility-aware stop."""
    candles = get(coin)
    a = atr(coin, n)
    if a is None or not candles or candles[-1].c <= 0:
        return None
    return a / candles[-1].c


async def refresh_all(coins: list[str]) -> None:
    for coin in coins:
        await asyncio.to_thread(refresh, coin)


async def run(coins: list[str], interval_s: float = _TTL_S) -> None:
    """Background task: keep every watched coin's daily candles fresh."""
    await refresh_all(coins)
    print(f"[candles] warmed {sum(1 for c in coins if c in _cache)}/{len(coins)} coin(s)")
    while True:
        await asyncio.sleep(interval_s)
        await refresh_all(coins)


if __name__ == "__main__":
    for c in ("xyz:NVDA", "xyz:TSLA", "BTC"):
        refresh(c)
        n = len(get(c))
        print(f"{c:12s} n={n:3d}  slope10={trend_slope(c)}  "
              f"donchian20={donchian(c)}  atr_pct={atr_pct(c)}")
