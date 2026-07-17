"""Telegram alert formatter + sender (via the bot API).

Builds the human-readable BUY/SELL alert including entry, stop, and the
liquidation price at the configured leverage so the risk is visible at a glance.
"""
from __future__ import annotations

import html

import aiohttp

import candles
import config

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"


def esc(text: str) -> str:
    """HTML-escape free text (headlines, rationales) before it lands inside a
    parse_mode=HTML message — raw '<'/'&' in a headline makes Telegram reject
    the whole send with 'can't parse entities' and the alert silently drops
    (observed 2026-07-16: a run of 400s, alerts lost for 35+ minutes)."""
    return html.escape(str(text), quote=False)


def liquidation_price(entry: float, direction: str, leverage: int) -> float:
    """Approx isolated-margin liquidation price (ignores fees/funding).

    Long liquidates when price falls ~1/leverage; short when it rises.
    """
    frac = 1.0 / leverage
    if direction == "long":
        return entry * (1 - frac)
    return entry * (1 + frac)


def stop_price(entry: float, direction: str, horizon: str = "scalp",
               coin: str | None = None) -> float:
    """Stop for a BRAND NEW entry only. Swing stops are volatility-aware when
    `coin` is given and ATR_STOPS is on: 1.5x the name's own 14d ATR%,
    clamped to [ATR_STOP_MIN, ATR_STOP_MAX] — a fixed 3% treated quiet AAPL
    and 6%-a-day SKHX identically (audit 2026-07-17: SKHX noise-stopped
    repeatedly). Falls back to the tier's fixed % when no candle history.
    An already-open trade's stop is frozen in journal.signals.stop at entry
    time; use resolve_stop() to read it back rather than recomputing here,
    or a later config edit would silently retighten/loosen a position that's
    already live."""
    if horizon == "swing":
        frac = config.SWING_STOP_RAW
        if coin and config.ATR_STOPS:
            ap = candles.atr_pct_cached(coin)
            if ap:
                frac = min(config.ATR_STOP_MAX,
                           max(config.ATR_STOP_MIN, config.ATR_STOP_MULT * ap))
    elif horizon == "bigswing":
        frac = config.BIGSWING_STOP_RAW
    elif horizon == "rally":
        frac = config.RALLY_STOP_RAW
    else:
        frac = config.SCALP_STOP_RAW
    if direction == "long":
        return entry * (1 - frac)
    return entry * (1 + frac)


def legacy_stop_price(entry: float, direction: str, leverage: int) -> float:
    """The pre-fix formula (half the distance to liquidation) — ONLY for
    reconstructing the stop of a trade opened before the geometry fix, whose
    journal row has no stored `stop` (NULL = legacy)."""
    frac = 0.5 / leverage
    if direction == "long":
        return entry * (1 - frac)
    return entry * (1 + frac)


def resolve_stop(entry: float, direction: str, leverage: int, horizon: str,
                 stored_stop: float | None) -> float:
    """The stop to use for an ALREADY-OPEN trade: its own frozen value if one
    was stored at entry, else the legacy formula (rows opened before the
    stop column existed). Never recomputes from today's config — that would
    let a later geometry change silently move a live position's real risk."""
    if stored_stop:
        return stored_stop
    return legacy_stop_price(entry, direction, leverage)


def build_alert(*, label: str, coin: str, direction: str, entry: float,
                leverage: int, confidence: float, magnitude: float,
                rationale: str, headline: str, tape_note: str,
                horizon: str = "scalp", off_hours_note: str = "") -> str:
    emoji = "🟢" if direction == "long" else "🔴"
    action = "LONG / BUY" if direction == "long" else "SHORT / SELL"
    tier = "⚡ SCALP (minutes–hours, exit same session)" if horizon == "scalp" \
        else "🌊 SWING (multi-day hold — sized at low leverage)"
    liq = liquidation_price(entry, direction, leverage)
    stop = stop_price(entry, direction, horizon, coin)
    conv = int(round(confidence * magnitude * 100))

    def fmt(x: float) -> str:
        return f"{x:,.4f}".rstrip("0").rstrip(".") if x < 100 else f"{x:,.2f}"

    lines = [
        f"{emoji} <b>{action} {label}</b>  ({coin})",
        f"<b>Tier:</b> {tier}",
        f"<b>Conviction:</b> {conv}/100  (conf {confidence:.2f} × mag {magnitude:.2f})",
        f"<b>Leverage:</b> {leverage}x",
        "",
        f"<b>Entry:</b> ~{fmt(entry)}",
        f"<b>Target (2R):</b> {fmt(entry + 2 * (entry - stop) if direction == 'long' else entry - 2 * (stop - entry))}",
        f"<b>Stop:</b> {fmt(stop)}",
        f"<b>Liq @ {leverage}x:</b> {fmt(liq)}",
        "",
        f"<b>Why:</b> {esc(rationale)}",
        f"<b>Headline:</b> {esc(headline)}",
        f"<b>Tape:</b> {esc(tape_note)}",
    ]
    if off_hours_note:
        lines += ["", off_hours_note]
    lines += ["", "<i>Not financial advice. Notifier only — confirm before trading.</i>"]
    return "\n".join(lines)


def build_exit_alert(*, label: str, coin: str, direction: str, entry: float,
                     exit_px: float, pnl_pct: float, reason: str, emoji: str,
                     note: str, horizon: str, leverage: int) -> str:
    def fmt(x: float) -> str:
        return f"{x:,.4f}".rstrip("0").rstrip(".") if x < 100 else f"{x:,.2f}"

    side = "LONG" if direction == "long" else "SHORT"
    return (
        f"{emoji} <b>EXIT {reason} — {side} {label}</b>  ({coin}, {horizon})\n"
        f"<b>Entry:</b> {fmt(entry)}  →  <b>Now:</b> {fmt(exit_px)}\n"
        f"<b>Move:</b> {pnl_pct:+.2f}% raw  "
        f"(≈ {(pnl_pct - config.ROUND_TRIP_FEE * 100) * leverage:+.1f}% on margin "
        f"at {leverage}x, net of fees)\n"
        f"\n"
        f"{esc(note)}\n"
        f"\n"
        f"<i>Not financial advice. Notifier only — confirm before trading.</i>"
    )


async def send(text: str) -> bool:
    async with aiohttp.ClientSession() as s:
        async with s.post(_API, data={
            "chat_id": config.TELEGRAM_ALERT_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }) as r:
            ok = r.status == 200
            if not ok:
                print(f"[notifier] send failed {r.status}: {await r.text()}")
            return ok


# --- test: send a sample alert through the real bot --------------------------
if __name__ == "__main__":
    import asyncio

    sample = build_alert(
        label="Gold", coin="xyz:GOLD", direction="short", entry=4002.45,
        leverage=25, confidence=0.72, magnitude=0.6,
        rationale="Iran tension premium unwinding after de-escalation headline.",
        headline="Gold falls as Iran tensions ease, weak US jobs data offset.",
        tape_note="sellers dominant (flow 0.11, book 0.29) — confirms short",
    )
    print(sample)
    asyncio.run(send(sample))
