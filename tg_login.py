"""One-time interactive Telegram login.

Run this ONCE in your own terminal:

    .venv/bin/python tg_login.py

It will ask for your phone number (international format, e.g. +9715...) and the
login code Telegram sends you (and your 2FA password if you have one). On success
it writes `notifier.session` next to this file; the bot then runs headless.
"""
from __future__ import annotations

import asyncio

import config
from tg_reader import build_client


async def main() -> None:
    if not config.TELEGRAM_API_ID or not config.TELEGRAM_API_HASH:
        raise SystemExit("TELEGRAM_API_ID / TELEGRAM_API_HASH missing in .env")
    client = build_client()
    await client.start()  # prompts for phone + code interactively
    me = await client.get_me()
    print(f"\nLogged in as {me.first_name} (@{me.username}). Session saved.")
    print("Resolving your channels...")
    for ch in config.TELEGRAM_CHANNELS:
        try:
            ent = await client.get_entity(ch)
            title = getattr(ent, "title", getattr(ent, "username", ch))
            print(f"  ok  @{ch:22s} -> {title}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL @{ch:22s} -> {e!r}")
    await client.disconnect()
    print("\nDone. You can now run the bot.")


if __name__ == "__main__":
    asyncio.run(main())
