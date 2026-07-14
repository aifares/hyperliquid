"""Telegram inline-button layer for semi-auto execution.

- send_trade_alert(): posts an entry alert with [⚡ Execute] [❌ Skip] buttons.
- run(): long-polls getUpdates for callback taps and routes them to executor.

Only one getUpdates consumer may run at a time (Telegram API constraint), so
this loop lives in main.py alongside everything else.
"""
from __future__ import annotations

import asyncio

import aiohttp

import config
import executor
import journal

_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def _fmt_summary(title: str, s: dict) -> str:
    if s.get("n", 0) == 0:
        return f"<b>{title}</b>\nno closed trades yet"
    return (
        f"<b>{title}</b>\n"
        f"closed: {s['n']} (scalp {s['scalp_n']}, swing {s['swing_n']})\n"
        f"win/loss: {s['wins']}/{s['losses']} ({s['win_rate']:.0%})\n"
        f"total PnL: {s['total_margin_pct']:+.1f}% margin "
        f"({s['total_raw_pct']:+.1f}% raw)\n"
        f"avg/trade: {s['avg_margin_pct']:+.1f}%  "
        f"best {s['best_margin_pct']:+.1f}%  worst {s['worst_margin_pct']:+.1f}%"
    )


async def _handle_stats_command(s: aiohttp.ClientSession, chat_id: int) -> None:
    text = (
        _fmt_summary("📊 ALL alerts", journal.summary()) + "\n\n" +
        _fmt_summary("✅ Executed (dry+live)", journal.summary(executed_only=True)) + "\n\n" +
        _fmt_summary("🧪 Executed — dry run", journal.summary(executed_only=True, dry_run=True)) + "\n\n" +
        _fmt_summary("💰 Executed — live", journal.summary(executed_only=True, dry_run=False))
    )
    await s.post(f"{_BASE}/sendMessage", json={
        "chat_id": chat_id, "text": text, "parse_mode": "HTML"})


async def send_trade_alert(text: str, pending_id: str) -> None:
    kb = {"inline_keyboard": [[
        {"text": "⚡ Execute", "callback_data": f"exec:{pending_id}"},
        {"text": "❌ Skip", "callback_data": f"skip:{pending_id}"},
    ]]}
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{_BASE}/sendMessage", json={
            "chat_id": config.TELEGRAM_ALERT_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": kb,
        }) as r:
            if r.status != 200:
                print(f"[buttons] send failed {r.status}: {await r.text()}")


async def _handle_callback(s: aiohttp.ClientSession, cq: dict) -> None:
    cq_id = cq["id"]
    data = cq.get("data", "")
    msg = cq.get("message") or {}
    action, _, pid = data.partition(":")

    if action == "exec":
        status = await executor.execute(pid)
    elif action == "skip":
        executor.discard(pid)
        status = "❌ Skipped."
    else:
        status = "⚠️ Unknown action."

    await s.post(f"{_BASE}/answerCallbackQuery",
                 json={"callback_query_id": cq_id, "text": status[:190]})
    # Append the outcome under the original alert and remove the buttons.
    if msg.get("message_id"):
        await s.post(f"{_BASE}/editMessageText", json={
            "chat_id": msg["chat"]["id"],
            "message_id": msg["message_id"],
            "text": (msg.get("text") or "") + f"\n\n———\n{status}",
            "reply_markup": {"inline_keyboard": []},
        })


async def run() -> None:
    """Long-poll for button taps and /stats commands. Coexists with alert sending."""
    offset = 0
    print("[buttons] callback + command listener running")
    async with aiohttp.ClientSession() as s:
        while True:
            try:
                async with s.get(f"{_BASE}/getUpdates", params={
                    "timeout": 50, "offset": offset,
                    "allowed_updates": '["callback_query","message"]',
                }, timeout=aiohttp.ClientTimeout(total=60)) as r:
                    data = await r.json()
                for upd in data.get("result", []):
                    offset = max(offset, upd["update_id"] + 1)
                    if "callback_query" in upd:
                        await _handle_callback(s, upd["callback_query"])
                    elif "message" in upd:
                        msg = upd["message"]
                        if (msg.get("text") or "").strip().lower().startswith("/stats"):
                            await _handle_stats_command(s, msg["chat"]["id"])
            except Exception as e:  # noqa: BLE001
                print(f"[buttons] poll error: {e!r}")
                await asyncio.sleep(3)
