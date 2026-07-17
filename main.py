"""Orchestrator: runs the whole notifier as one asyncio process.

  Hyperliquid ticks ─► HLStream.state (live prices + tape)
  Telegram + Perplexity ─► event queue ─► Claude analyzer ─► combiner ─► alert

Start (after tg_login.py has been run once):

    .venv/bin/python main.py
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import logsetup

LOG_PATH = logsetup.init()

import account_monitor
import bigswing
import candles
import config
import earnings
import earnings_runup
import news
import rally
import tg_buttons
import tg_reader
import watcher
from analyzer import Analyzer
from combiner import handle_signal
from events import NewsEvent
from hl_stream import HLStream

SHADOW_MODE = os.getenv("SHADOW_MODE", "0") == "1"
DAILY_REVIEW_UTC_HOUR = 20   # ~16:00 ET (US close) in summer -> daily wrap-up


async def _daily_review_loop() -> None:
    """Once a day after the US close, build the trade review and Telegram the
    summary. Read-only (never trades); a failure here must never break the bot."""
    import notifier
    import daily_review as dr
    while True:
        now = datetime.now(tz=timezone.utc)
        target = now.replace(hour=DAILY_REVIEW_UTC_HOUR, minute=5, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            summary, _ = await asyncio.to_thread(dr.build_review)
            await notifier.send(summary + "\n<i>full report saved to research/daily/</i>")
            print("[daily-review] sent")
        except Exception as e:  # noqa: BLE001
            print(f"[daily-review] failed: {e!r}")


async def _analyzer_loop(queue: "asyncio.Queue[NewsEvent]", stream: HLStream) -> None:
    import prefilter
    analyzer = Analyzer()
    last_stats = 0.0
    while True:
        ev = await queue.get()
        # Rewordings of an already-analyzed story never reach the API — the
        # first telling was scored minutes ago and cooldowns gate repeats
        # downstream anyway. Saves ~1/3 of Haiku spend (measured 2026-07-14).
        if prefilter.is_duplicate(ev.text):
            prefilter.skipped_dup += 1
            print(f"[prefilter] dup skipped: {ev.text[:70]!r}")
            continue
        prefilter.analyzed += 1
        import time as _time
        if _time.time() - last_stats > 3600 and prefilter.analyzed > 0:
            last_stats = _time.time()
            total = prefilter.analyzed + prefilter.skipped_dup
            print(f"[prefilter] api usage: {prefilter.analyzed} analyzed, "
                  f"{prefilter.skipped_dup} dups skipped "
                  f"({prefilter.skipped_dup / total * 100:.0f}% saved)")
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
    # rally.on_book is a no-op until rally.run() sets up its confirm queue —
    # safe to wire unconditionally so the first book tick after enable works.
    stream = HLStream(coins, on_book=rally.on_book if config.RALLY_ENABLED else None)

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
    if config.ALPACA_API_KEY and config.ALPACA_API_SECRET:
        import alpaca_news
        tasks.append(asyncio.create_task(alpaca_news.run(queue), name="alpaca-news"))
    if config.TELEGRAM_API_ID and config.TELEGRAM_API_HASH:
        tasks.append(asyncio.create_task(tg_reader.run(queue), name="telegram"))
    if config.WALLET_ADDRESS:
        tasks.append(asyncio.create_task(
            account_monitor.run(config.WALLET_ADDRESS, stream), name="account"))
    if config.TELEGRAM_BOT_TOKEN:
        tasks.append(asyncio.create_task(tg_buttons.run(), name="buttons"))
    if config.FINNHUB_API_KEY:
        tasks.append(asyncio.create_task(earnings.run(), name="earnings"))
        # When bigswing owns the wallet exclusively, don't start run-up —
        # calendar warnings stay on via earnings. Rally is allowed alongside
        # bigswing (see below).
        import executor as _ex
        if config.RUNUP_ENABLED and not _ex.bigswing_active():
            tasks.append(asyncio.create_task(
                earnings_runup.run(stream), name="runup"))
        elif config.RUNUP_ENABLED:
            print("[runup] not started — bigswing exclusive mode "
                  "(BIGSWING_PAUSE_OTHER_TIERS)")
        else:
            print("[runup] earnings tier DISABLED (RUNUP_ENABLED=0) — "
                  "calendar warnings stay on")

    # Candles shared by bigswing + rally. Scalp/swing/runup stay paused under
    # exclusive bigswing; rally is started alongside when RALLY_ENABLED.
    candle_coins: list[str] = []
    import executor as _ex2
    exclusive = _ex2.bigswing_active()
    if config.BIGSWING_ENABLED:
        candle_coins.extend(m.coin for m in config.BIGSWING_MARKETS)
        tasks.append(asyncio.create_task(bigswing.run(stream), name="bigswing"))
    else:
        print("[bigswing] full-balance swing tier DISABLED (BIGSWING_ENABLED=0)")
    if config.RALLY_ENABLED:
        candle_coins.extend(m.coin for m in config.RALLY_MARKETS)
        if config.RALLY_BROAD_MARKET not in candle_coins:
            candle_coins.append(config.RALLY_BROAD_MARKET)
        tasks.append(asyncio.create_task(rally.run(stream), name="rally"))
    else:
        print("[rally] news+book+trend tier DISABLED (RALLY_ENABLED=0)")
    # ATR stops need daily candles for EVERY tradeable market, not just the
    # bigswing/rally subsets — one candleSnapshot call per coin per refresh.
    if config.ATR_STOPS:
        candle_coins.extend(m.coin for m in config.MARKETS)
    if candle_coins:
        seen: set[str] = set()
        uniq = [c for c in candle_coins if not (c in seen or seen.add(c))]
        refresh = min(
            config.BIGSWING_CANDLE_REFRESH_S if config.BIGSWING_ENABLED else 10**9,
            config.RALLY_CANDLE_REFRESH_S if config.RALLY_ENABLED else 10**9,
            1800,   # ATR cache: refresh at least every 30 min
        )
        tasks.append(asyncio.create_task(
            candles.run(uniq, refresh), name="candles"))
    tasks.append(asyncio.create_task(_daily_review_loop(), name="daily-review"))
    if config.SMARTMONEY_ENABLED:
        import smartmoney
        tasks.append(asyncio.create_task(smartmoney.run(), name="smartmoney"))
    import funding
    tasks.append(asyncio.create_task(funding.run(), name="funding"))
    if config.KOREA_ENABLED:
        import korea
        tasks.append(asyncio.create_task(korea.run(stream), name="korea"))

    from analyzer import MODEL as ANALYZER_MODEL
    from executor import DRY_RUN

    banner = "SHADOW (no alerts sent)" if SHADOW_MODE else "LIVE (alerts on)"
    exec_mode = "DRY-RUN (simulated fills)" if DRY_RUN else "LIVE (real orders)"
    bigswing_mode = (
        f"ENABLED ({config.BIGSWING_MIN_LEVERAGE}-{config.BIGSWING_MAX_LEVERAGE}x, "
        f"full-balance, exclusive_vs_scalp/swing/runup={exclusive}, "
        f"{'adopts' if config.BIGSWING_ADOPT_MANUAL else 'ignores'} "
        f"manual entries)" if config.BIGSWING_ENABLED else "disabled"
    )
    rally_mode = (
        f"ENABLED ({config.RALLY_LEVERAGE}x, news+trend arm → tick-book confirm)"
        if config.RALLY_ENABLED else "disabled"
    )
    print(f"\n=== Hyperliquid news notifier — {banner} ===")
    print(f"log file: {LOG_PATH}")
    print(f"analyzer model: {ANALYZER_MODEL}")
    print(f"execution: {exec_mode}")
    print(f"bigswing (full-balance tier): {bigswing_mode}")
    print(f"rally (news+book+trend tier): {rally_mode}")
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
