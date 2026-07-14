"""Earnings calendar (Finnhub) for the watched stock perps.

Two jobs:
  1. Risk context — every alert on a stock shows how close the next earnings
     print is; swing entries that would still be open through the print get a
     loud gap-risk warning (a 5x swing through a ±8% earnings move is a coin
     flip, not a thesis).
  2. Awareness — a Telegram digest whenever a watched name's earnings lands
     within the next 7 days, so run-up / PEAD plays can be planned.

Free tier is 60 calls/min; we poll each symbol twice a day.
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime

import aiohttp

import config
import notifier

REFRESH_S = 12 * 3600
LOOKAHEAD_DAYS = 45
NEAR_DAYS = 7            # "earnings soon" context on alerts + digest
SWING_WARN_DAYS = 3      # swing may still be open at the print -> warn

_URL = "https://finnhub.io/api/v1/calendar/earnings"

# coin -> {"date": "2026-08-25", "hour": "amc", "eps": 2.12, "rev": 9.3e10}
_next: dict[str, dict] = {}
_announced: set[str] = set()   # "NVDA:2026-08-25" digests already sent


def _stock_symbols() -> dict[str, str]:
    """coin -> plain ticker for the stock perps (skip indexes and BTC)."""
    skip = {"xyz:XYZ100", "xyz:SP500", "BTC"}
    return {m.coin: m.coin.replace("xyz:", "")
            for m in config.MARKETS if m.coin not in skip}


async def refresh(session: aiohttp.ClientSession) -> None:
    today = date.today()
    frm, to = today.isoformat(), date.fromordinal(
        today.toordinal() + LOOKAHEAD_DAYS).isoformat()
    for coin, sym in _stock_symbols().items():
        try:
            async with session.get(_URL, params={
                "from": frm, "to": to, "symbol": sym,
                "token": config.FINNHUB_API_KEY,
            }, timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
        except Exception as e:  # noqa: BLE001
            print(f"[earnings] fetch error for {sym}: {e!r}")
            continue
        cal = sorted(data.get("earningsCalendar") or [],
                     key=lambda x: x.get("date", ""))
        if cal:
            e = cal[0]
            _next[coin] = {"date": e["date"], "hour": e.get("hour", ""),
                           "eps": e.get("epsEstimate"), "rev": e.get("revenueEstimate")}
        await asyncio.sleep(1.1)   # stay far under the rate limit


def days_until(coin: str) -> int | None:
    e = _next.get(coin)
    if not e:
        return None
    d = datetime.strptime(e["date"], "%Y-%m-%d").date()
    return (d - date.today()).days


def note(coin: str) -> str:
    """Alert context line; empty unless earnings are near."""
    dd = days_until(coin)
    if dd is None or dd > NEAR_DAYS:
        return ""
    e = _next[coin]
    hour = {"amc": "after close", "bmo": "before open"}.get(e["hour"], e["hour"])
    return f"📅 earnings in {dd}d ({e['date']} {hour})"


def swing_gap_warning(coin: str) -> str:
    dd = days_until(coin)
    if dd is None or dd > SWING_WARN_DAYS:
        return ""
    return ("⚠️ <b>Swing would be open through the earnings print</b> — "
            "gap risk, size down or exit before the report.")


async def _digest() -> None:
    lines = []
    for coin in _stock_symbols():
        dd = days_until(coin)
        if dd is None or dd > NEAR_DAYS:
            continue
        e = _next[coin]
        key = f"{coin}:{e['date']}"
        if key in _announced:
            continue
        _announced.add(key)
        label = config.MARKET_BY_COIN[coin].label
        hour = {"amc": "after close", "bmo": "before open"}.get(e["hour"], e["hour"])
        eps = f", EPS est {e['eps']}" if e.get("eps") else ""
        lines.append(f"• <b>{label}</b> — {e['date']} {hour} (in {dd}d{eps})")
    if lines:
        await notifier.send("📅 <b>Earnings watch (next 7 days)</b>\n" + "\n".join(lines))
        print(f"[earnings] digest sent ({len(lines)} names)")


async def run() -> None:
    if not config.FINNHUB_API_KEY:
        print("[earnings] no FINNHUB_API_KEY; calendar disabled")
        return
    async with aiohttp.ClientSession() as session:
        while True:
            await refresh(session)
            known = {c: e["date"] for c, e in _next.items()}
            print(f"[earnings] calendar refreshed: {known}")
            await _digest()
            await asyncio.sleep(REFRESH_S)


if __name__ == "__main__":
    async def _test() -> None:
        async with aiohttp.ClientSession() as s:
            await refresh(s)
        for coin in _stock_symbols():
            dd = days_until(coin)
            e = _next.get(coin)
            print(f"{coin:10s} {e['date'] if e else '—':12s} "
                  f"{(e or {}).get('hour', ''):4s} in {dd if dd is not None else '—'}d"
                  f"  note={note(coin) or '—'}")

    asyncio.run(_test())
