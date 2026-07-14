"""Telegram channel reader (Telethon, user session).

Streams new messages from the configured channels into an asyncio.Queue of
NewsEvent. Requires a one-time interactive login (see tg_login.py) which creates
the local `notifier.session` file; after that this runs headless.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from telethon import TelegramClient, events

import config
import liquidations
import notifier
from events import NewsEvent

SESSION = str(Path(__file__).with_name("notifier"))  # -> notifier.session

# This channel is structured tick-level liquidation prints, not narrative news —
# parse it directly instead of burning a Claude call per message.
LIQUIDATION_CHANNEL = "hyperliquidliquidations"


def build_client() -> TelegramClient:
    return TelegramClient(SESSION, int(config.TELEGRAM_API_ID), config.TELEGRAM_API_HASH)


async def run(queue: "asyncio.Queue[NewsEvent]") -> None:
    client = build_client()
    await client.start()  # uses existing session; will not prompt if logged in
    me = await client.get_me()
    print(f"[tg] logged in as {me.username or me.first_name}; "
          f"watching {len(config.TELEGRAM_CHANNELS)} channels")

    # Resolve channel entities up front so a bad handle fails loudly.
    entities = []
    for ch in config.TELEGRAM_CHANNELS:
        try:
            entities.append(await client.get_entity(ch))
        except Exception as e:  # noqa: BLE001
            print(f"[tg] WARN could not resolve @{ch}: {e!r}")

    @client.on(events.NewMessage(chats=entities))
    async def _handler(event):  # noqa: ANN001
        text = event.message.message or ""
        if not text.strip():
            return
        chat = await event.get_chat()
        name = getattr(chat, "username", None) or getattr(chat, "title", "?")

        if (name or "").lower() == LIQUIDATION_CHANNEL:
            ev = liquidations.parse(text)
            if ev is not None:
                liquidations.record(ev)
                if ev.coin in config.MARKET_BY_COIN:
                    print(f"[liq] {ev.coin} {ev.side} ${ev.size_usd:,.0f} @ {ev.price:g}")
                cascade = liquidations.cascade_alert_text(ev)
                if cascade:
                    await notifier.send(cascade)
                    print(f"[liq] CASCADE ALERT sent for {ev.coin}")
                return  # never goes through Claude
            # unparseable message from this channel (rare) — fall through to Claude

        await queue.put(NewsEvent(source=f"telegram:{name}", text=text))

    await client.run_until_disconnected()


# --- standalone tail (prints messages, no queue consumer) --------------------
if __name__ == "__main__":
    async def _tail() -> None:
        q: asyncio.Queue[NewsEvent] = asyncio.Queue()

        async def _printer() -> None:
            while True:
                ev = await q.get()
                print(f"\n[{ev.source}] {ev.text[:200]}")

        await asyncio.gather(run(q), _printer())

    asyncio.run(_tail())
