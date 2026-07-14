"""Orchestrator: runs the whole notifier as one asyncio process.

  Hyperliquid ticks ─► HLStream.state (live prices + tape)
  Telegram + Perplexity ─► event queue ─► Claude analyzer ─► combiner ─► alert

Start (after tg_login.py has been run once):

    .venv/bin/python main.py
"""
from __future__ import annotations

import asyncio
import os

import logsetup

LOG_PATH = logsetup.init()

import account_monitor
import config
import earnings
import earnings_runup
import news
import tg_buttons
import tg_reader
import watcher
from analyzer import Analyzer
from combiner import handle_signal
from events import NewsEvent
from hl_stream import HLStream

SHADOW_MODE = os.getenv("SHADOW_MODE", "0") == "1"


async def _analyzer_loop(queue: "asyncio.Queue[NewsEvent]", stream: HLStream) -> None:
    analyzer = Analyzer()
    while True:
        ev = await queue.get()
        sig = await analyzer.analyze(ev)
        print(f"[analyze] {ev.source}: {ev.text[:70]!r}\n"
              f"          -> {sig.coin} {sig.direction} "
              f"mag={sig.magnitude} conf={sig.confidence} actionable={sig.actionable}")
        if sig.actionable:
            if SHADOW_MODE:
                print(f"          [SHADOW] would evaluate/alert {sig.coin} {sig.direction}")
            else:
                await handle_signal(sig, stream)


def _fetch_mids_sync() -> dict[str, float]:
    """Blocking mid fetch at startup so restart-closures record real prices."""
    import json
    import urllib.request
    mids: dict[str, float] = {}
    for dex in ("", "xyz"):
        body: dict = {"type": "allMids"}
        if dex:
            body["dex"] = dex
        try:
            req = urllib.request.Request(
                config.HL_INFO_URL, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                mids.update({k: float(v) for k, v in json.load(r).items()})
        except Exception:  # noqa: BLE001 — fall back to entry-price scratch
            pass
    return mids


async def main() -> None:
    _preflight()
    import journal
    orphans = journal.close_orphans(_fetch_mids_sync())
    if orphans:
        print(f"[journal] force-closed {orphans} signal(s) from a previous run "
              f"at current prices")
    queue: asyncio.Queue[NewsEvent] = asyncio.Queue()
    coins = [m.coin for m in config.MARKETS]
    stream = HLStream(coins)

    resumed_live = watcher.resume_live(stream)
    if resumed_live:
        print(f"[watcher] resumed {resumed_live} real position(s) from a "
              f"previous run (order + stop/target already on the exchange)")

    tasks = [
        asyncio.create_task(stream.run(), name="hl"),
        asyncio.create_task(_analyzer_loop(queue, stream), name="analyzer"),
    ]
    if config.PERPLEXITY_API_KEY:
        tasks.append(asyncio.create_task(news.run(queue), name="news"))
    if config.TELEGRAM_API_ID and config.TELEGRAM_API_HASH:
        tasks.append(asyncio.create_task(tg_reader.run(queue), name="telegram"))
    if config.WALLET_ADDRESS:
        tasks.append(asyncio.create_task(
            account_monitor.run(config.WALLET_ADDRESS, stream), name="account"))
    if config.TELEGRAM_BOT_TOKEN:
        tasks.append(asyncio.create_task(tg_buttons.run(), name="buttons"))
    if config.FINNHUB_API_KEY:
        tasks.append(asyncio.create_task(earnings.run(), name="earnings"))
        if config.RUNUP_ENABLED:
            tasks.append(asyncio.create_task(
                earnings_runup.run(stream), name="runup"))
        else:
            print("[runup] earnings tier DISABLED (RUNUP_ENABLED=0) — "
                  "calendar warnings stay on")

    from analyzer import MODEL as ANALYZER_MODEL
    from executor import DRY_RUN

    banner = "SHADOW (no alerts sent)" if SHADOW_MODE else "LIVE (alerts on)"
    exec_mode = "DRY-RUN (simulated fills)" if DRY_RUN else "LIVE (real orders)"
    print(f"\n=== Hyperliquid news notifier — {banner} ===")
    print(f"log file: {LOG_PATH}")
    print(f"analyzer model: {ANALYZER_MODEL}")
    print(f"execution: {exec_mode}")
    print(f"markets: {coins}")
    print(f"channels: {config.TELEGRAM_CHANNELS}\n")

    await asyncio.gather(*tasks)


def _preflight() -> None:
    missing = []
    if not config.ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY (analyzer will fail)")
    if not (config.TELEGRAM_API_ID and config.TELEGRAM_API_HASH):
        missing.append("TELEGRAM_API_ID/HASH (no channel reading)")
    if not config.PERPLEXITY_API_KEY:
        missing.append("PERPLEXITY_API_KEY (no news polling)")
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_ALERT_CHAT_ID):
        missing.append("TELEGRAM_BOT_TOKEN/CHAT_ID (cannot send alerts)")
    for m in missing:
        print(f"[preflight] WARN missing {m}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped.")
