"""Real-time news via Alpaca's Benzinga-sourced news websocket.

Fixes the latency problem found in the SPCX audit: Perplexity reported
"SpaceX drops 3.08%" ~2 HOURS after the actual move, having reworded the
same story 8 times along the way. Benzinga is the real-time squawk feed
retail algo desks actually use — headlines arrive in seconds, not hours.
Runs ALONGSIDE Perplexity (not a replacement): Perplexity does broad
macro/geopolitical web search Benzinga doesn't cover; Benzinga is fast and
company-catalyst-focused (earnings, upgrades, M&A) — exactly the single-name
catalyst pattern that's actually been profitable (TSMC -> NVDA).

Free with any Alpaca account (paper keys work fine — this is a read-only
data subscription, no trading). Auto-reconnects on drop.
"""
from __future__ import annotations

import asyncio
import json

import websockets

import config
from events import NewsEvent

_WS_URL = "wss://stream.data.alpaca.markets/v1beta1/news"
_RECONNECT_DELAY_S = 5


def _symbols() -> list[str]:
    """Bare tickers Alpaca/Benzinga would tag (xyz:NVDA -> NVDA); BTC and the
    index proxies (XYZ100/SP500) have no Benzinga symbol, so they're dropped —
    Perplexity still covers macro/index news."""
    out = []
    for m in config.MARKETS:
        sym = m.coin.replace("xyz:", "")
        if m.coin == "BTC" or m.coin in ("xyz:XYZ100", "xyz:SP500"):
            continue
        out.append(sym)
    return out


def _to_event(item: dict) -> NewsEvent | None:
    if item.get("T") != "n":
        return None
    headline = (item.get("headline") or "").strip()
    if not headline:
        return None
    summary = (item.get("summary") or "").strip()
    text = f"{headline}. {summary}" if summary and summary != headline else headline
    syms = item.get("symbols") or []
    if syms:
        text = f"[{'/'.join(syms)}] {text}"
    return NewsEvent(source="alpaca-benzinga", text=text, url=item.get("url", ""))


async def run(queue: "asyncio.Queue[NewsEvent]") -> None:
    if not (config.ALPACA_API_KEY and config.ALPACA_API_SECRET):
        print("[alpaca] disabled — ALPACA_API_KEY/SECRET not set")
        return
    symbols = _symbols()
    print(f"[alpaca] real-time Benzinga news for {len(symbols)} symbols")
    while True:
        try:
            async with websockets.connect(_WS_URL, ping_interval=20) as ws:
                await ws.send(json.dumps({
                    "action": "auth",
                    "key": config.ALPACA_API_KEY,
                    "secret": config.ALPACA_API_SECRET,
                }))
                # The server sends an automatic {"msg":"connected"} the moment
                # the socket opens, BEFORE the actual auth confirmation — read
                # until we see "authenticated" or "error", not just message #1.
                authed = False
                auth_error: dict | None = None
                for _ in range(4):
                    msgs = json.loads(await ws.recv())
                    if not isinstance(msgs, list):
                        msgs = [msgs]
                    for m in msgs:
                        if m.get("msg") == "authenticated":
                            authed = True
                        elif m.get("T") == "error":
                            auth_error = m
                    if authed or auth_error:
                        break
                if auth_error:
                    # Never permanently die here — a "connection limit
                    # exceeded" from the OLD socket not yet closed (e.g. right
                    # after a bot restart) looks identical to a real bad key,
                    # and is common/transient. Back off and retry either way;
                    # this task must never go silently dark for the rest of
                    # the run (2026-07-17: a bare `return` here did exactly
                    # that after our first restart with the new key).
                    print(f"[alpaca] auth error, retrying in "
                          f"{_RECONNECT_DELAY_S}s: {auth_error}")
                    await asyncio.sleep(_RECONNECT_DELAY_S)
                    continue
                if not authed:
                    print("[alpaca] auth did not confirm — reconnecting")
                    continue
                await ws.send(json.dumps({"action": "subscribe", "news": symbols}))
                sub_resp = await ws.recv()
                print(f"[alpaca] connected + subscribed: {sub_resp[:200]}")

                async for raw in ws:
                    try:
                        items = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(items, list):
                        items = [items]
                    for item in items:
                        ev = _to_event(item)
                        if ev:
                            print(f"[alpaca] {ev.text[:90]!r}")
                            await queue.put(ev)
        except Exception as e:  # noqa: BLE001 — reconnect, never crash the bot
            print(f"[alpaca] connection error, reconnecting in "
                  f"{_RECONNECT_DELAY_S}s: {e!r}")
            await asyncio.sleep(_RECONNECT_DELAY_S)


if __name__ == "__main__":
    async def _smoke() -> None:
        q: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(run(q))
        try:
            for _ in range(5):
                ev = await asyncio.wait_for(q.get(), timeout=120)
                print("GOT:", ev)
        except asyncio.TimeoutError:
            print("(no news in 120s — feed is quiet or market closed; "
                  "connection itself is what matters for this smoke test)")
        finally:
            task.cancel()

    asyncio.run(_smoke())
