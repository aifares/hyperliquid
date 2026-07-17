"""Live funding-rate cache for entry gating.

Found 2026-07-17: SKHX shorts were paying ~1.1%/DAY in funding (the whole
Korea/memory complex is crowd-shorted, -417%/yr annualized on SKHX) and the
bot had no idea — swing shorts there need >1%/day of price edge just to
break even on carry. This module polls metaAndAssetCtxs every 10 minutes and
exposes the signed DAILY funding cost of holding a direction, so the
combiner can demand extra conviction for (or refuse) entries that bleed
carry, and annotate ones that earn it.

Convention: Hyperliquid `funding` is the hourly rate LONGS pay (negative =
shorts pay longs). daily_cost(coin, direction) returns what WE would pay
per day as a fraction of notional — positive = we bleed, negative = we earn.
"""
from __future__ import annotations

import asyncio
import time

import aiohttp

import config

POLL_S = 600
_rates: dict[str, float] = {}     # coin -> hourly funding rate (longs pay)
_polled_at = 0.0


def daily_cost(coin: str, direction: str) -> float | None:
    """Signed daily funding as a fraction of notional for holding `direction`
    on `coin`. Positive = we PAY that much per day; negative = we EARN.
    None if the cache has no data (never gate on missing data)."""
    if coin not in _rates or time.time() - _polled_at > 3 * POLL_S:
        return None
    hourly = _rates[coin]
    per_day = hourly * 24
    return per_day if direction == "long" else -per_day


async def run() -> None:
    global _polled_at
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                for dex in ("", "xyz"):
                    body: dict = {"type": "metaAndAssetCtxs"}
                    if dex:
                        body["dex"] = dex
                    async with session.post(
                            config.HL_INFO_URL, json=body,
                            timeout=aiohttp.ClientTimeout(total=20)) as r:
                        data = await r.json()
                    for asset, ctx in zip(data[0]["universe"], data[1]):
                        try:
                            _rates[asset["name"]] = float(ctx.get("funding", 0) or 0)
                        except (TypeError, ValueError):
                            continue
                _polled_at = time.time()
            except Exception as e:  # noqa: BLE001 — stale beats crashed
                print(f"[funding] poll error: {e!r}")
            await asyncio.sleep(POLL_S)


if __name__ == "__main__":
    async def _smoke() -> None:
        task = asyncio.create_task(run())
        await asyncio.sleep(5)
        for c in ("xyz:SKHX", "xyz:SMSN", "xyz:NVDA", "BTC"):
            for d in ("long", "short"):
                print(c, d, daily_cost(c, d))
        task.cancel()
    asyncio.run(_smoke())
