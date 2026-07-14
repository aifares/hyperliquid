"""Shared data layer for the backtests.

Cost model (applied everywhere):
  - Hyperliquid taker fee 0.045%/side -> 0.09% of notional per round trip
  - Slippage assumption 0.01%/side -> 0.02% per round trip (liquid names)
  - Funding: measured average hourly funding per coin from Hyperliquid's own
    funding history (paginated to listing). Longs pay positive funding,
    shorts receive it. Applied over actual hold duration.

All returns reported as fractions of NOTIONAL (raw). Multiply by leverage for
margin returns; costs scale identically, so relative conclusions are unchanged.
"""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

CACHE = Path(__file__).with_name("data")
CACHE.mkdir(exist_ok=True)

FEE_RT = 0.0009          # round-trip taker fees, fraction of notional
SLIP_RT = 0.0002         # round-trip slippage assumption
FRICTION = FEE_RT + SLIP_RT   # 0.11% per round trip

HL_INFO = "https://api.hyperliquid.xyz/info"


def hl_info(body: dict) -> object:
    req = urllib.request.Request(HL_INFO, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def hl_candles(coin: str, interval: str, days: int) -> list[dict]:
    """Most recent candles (API caps ~5000 bars). Cached for the session."""
    f = CACHE / f"candles_{coin.replace(':', '_')}_{interval}.json"
    if f.exists() and time.time() - f.stat().st_mtime < 6 * 3600:
        return json.loads(f.read_text())
    now = int(time.time() * 1000)
    out = hl_info({"type": "candleSnapshot", "req": {
        "coin": coin, "interval": interval,
        "startTime": now - days * 86400_000, "endTime": now}})
    f.write_text(json.dumps(out))
    return out


def hl_avg_hourly_funding(coin: str) -> float:
    """Mean hourly funding rate over all available history (paginated)."""
    f = CACHE / f"funding_{coin.replace(':', '_')}.json"
    if f.exists() and time.time() - f.stat().st_mtime < 24 * 3600:
        rates = json.loads(f.read_text())
    else:
        rates, start = [], int(time.time() * 1000) - 400 * 86400_000
        while True:
            page = hl_info({"type": "fundingHistory", "coin": coin,
                            "startTime": start})
            if not page:
                break
            rates.extend(float(p["fundingRate"]) for p in page)
            last = page[-1]["time"]
            if len(page) < 500:
                break
            start = last + 1
            time.sleep(0.3)
        f.write_text(json.dumps(rates))
    return sum(rates) / len(rates) if rates else 0.0


def funding_cost(coin: str, hours: float, direction: str,
                 avg_cache: dict[str, float]) -> float:
    """Signed funding cost as fraction of notional (positive = reduces return)."""
    if coin not in avg_cache:
        avg_cache[coin] = hl_avg_hourly_funding(coin)
    avg = avg_cache[coin]
    sign = 1.0 if direction == "long" else -1.0
    return sign * avg * hours
