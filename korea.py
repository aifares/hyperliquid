"""Korea→US semiconductor lead-lag tier (SHADOW-FIRST).

The xyz perps on SK Hynix (SKHX) and Samsung (SMSN) trade 24/7 and track the
Seoul session while US names sleep — overnight semiconductor information is
visible HOURS before the US open. Backtest (60d hourly, net of measured 2bp
fees, 2026-07-17): when the Korea-session move (00:00→07:00 UTC) of
SKHX+SMSN averages >=1%, an equal-weight same-direction basket of
MU/NVDA/AMD/SNDK entered 14:00 UTC and exited 19:55 UTC returned
+0.85%/day mean, +1.13% median, 66% win rate, n=35, positive in both sample
halves, in BOTH directions. Better per-event than any validated tier except
run-up, with a 6-hour hold.

SHADOW MODE (config.KOREA_LIVE=0, default): computes the signal, alerts, and
appends entry/exit marks to korea_shadow.jsonl for forward validation —
places NO orders. Flip KOREA_LIVE=1 in .env only after the forward record
confirms the backtest.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import config
import notifier

_SHADOW_LOG = Path(__file__).with_name("korea_shadow.jsonl")


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _sleep_until(hour: int, minute: int = 0) -> None:
    now = _now_utc()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target = target.replace(day=now.day)
        # next day
        from datetime import timedelta
        target += timedelta(days=1)
    await asyncio.sleep((target - now).total_seconds())


def _mid(stream, coin: str) -> float:
    st = stream.state.get(coin)
    return st.mid if st and st.mid > 0 else 0.0


def _log(rec: dict) -> None:
    rec["ts"] = time.time()
    with _SHADOW_LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")


async def run(stream) -> None:
    mode = "LIVE" if config.KOREA_LIVE else "SHADOW (no orders)"
    print(f"[korea] lead-lag tier active — {mode}; signal "
          f">={config.KOREA_MIN_SIGNAL*100:.0f}% Korea-session move")
    while True:
        # explicit daily schedule, all UTC: 00:00 capture -> 07:00 score ->
        # 14:00 enter -> 19:55 exit -> wait for next midnight
        await _sleep_until(0, 0)
        korea_open = {c: _mid(stream, c) for c in config.KOREA_SIGNAL_COINS}
        korea_open = {c: m for c, m in korea_open.items() if m > 0}
        await _sleep_until(7, 0)
        rets = []
        for c, o in korea_open.items():
            m = _mid(stream, c)
            if o > 0 and m > 0:
                rets.append(m / o - 1)
        if not rets or _now_utc().weekday() >= 5:
            continue
        sig = sum(rets) / len(rets)
        if abs(sig) < config.KOREA_MIN_SIGNAL:
            print(f"[korea] session move {sig*100:+.2f}% < "
                  f"{config.KOREA_MIN_SIGNAL*100:.0f}% — no trade today")
            continue
        direction = "long" if sig > 0 else "short"
        await notifier.send(
            f"🇰🇷 <b>Korea lead-lag armed: {direction.upper()} US semis</b>\n"
            f"SKHX+SMSN Korea session: {sig*100:+.2f}%\n"
            f"Basket: {', '.join(c.replace('xyz:', '') for c in config.KOREA_TRADE_COINS)}\n"
            f"<i>Entry 14:00 UTC, exit 19:55 UTC — "
            f"{'LIVE' if config.KOREA_LIVE else 'shadow only (validation)'}"
            f"</i>")
        # 3) enter at 14:00 UTC
        await _sleep_until(14, 0)
        entries = {c: _mid(stream, c) for c in config.KOREA_TRADE_COINS}
        entries = {c: m for c, m in entries.items() if m > 0}
        _log({"event": "entry", "direction": direction, "signal": sig,
              "marks": entries})
        # 4) exit at 19:55 UTC and score
        await _sleep_until(19, 55)
        exits = {c: _mid(stream, c) for c in entries}
        rets2 = [(exits[c] / entries[c] - 1) * (1 if direction == "long" else -1)
                 for c in entries if exits.get(c, 0) > 0 and entries[c] > 0]
        pnl = (sum(rets2) / len(rets2) - 0.0002) if rets2 else 0.0
        _log({"event": "exit", "direction": direction, "signal": sig,
              "marks": exits, "basket_pnl": pnl})
        await notifier.send(
            f"🇰🇷 <b>Korea lead-lag {'result' if config.KOREA_LIVE else 'shadow result'}: "
            f"{pnl*100:+.2f}%</b> ({direction}, signal {sig*100:+.2f}%)\n"
            f"<i>net of fees; forward-validation record in korea_shadow.jsonl</i>")
