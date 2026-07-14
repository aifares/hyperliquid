"""Position watcher: turns entry alerts into a full buy→sell notification cycle.

After each entry alert fires, a watcher task follows the live mid price and
sends an EXIT alert when one of these hits:
  - STOP:    price crossed the stop printed on the entry alert
  - TARGET:  price reached 2R (twice the stop distance in your favor)
  - FADE:    trade is in profit but the tape has flipped hard against it
  - TIME:    max hold reached (scalps are same-session by definition)
"""
from __future__ import annotations

import asyncio
import time

import journal
import notifier
import tape
from hl_stream import HLStream

SCALP_MAX_HOLD_S = 4 * 3600
SWING_MAX_HOLD_S = 72 * 3600
POLL_S = 2.0
FADE_STRENGTH = 0.6      # opposing tape strength that counts as momentum death
# FADE must not scratch trades seconds after entry on tape flicker: require a
# minimum hold AND a minimum profit (in R = stop distance) before it can fire.
FADE_MIN_HOLD_S = {"scalp": 180, "swing": 3600}
FADE_MIN_PROFIT_R = {"scalp": 0.5, "swing": 1.0}


def target_price(entry: float, stop: float, direction: str) -> float:
    """2R target: twice the stop distance, in the trade's favor."""
    risk = abs(entry - stop)
    return entry + 2 * risk if direction == "long" else entry - 2 * risk


async def watch(*, signal_id: int, coin: str, label: str, direction: str,
                entry: float, stop: float, horizon: str, leverage: int,
                stream: HLStream) -> None:
    tgt = target_price(entry, stop, direction)
    opened = time.time()
    deadline = opened + (SCALP_MAX_HOLD_S if horizon == "scalp" else SWING_MAX_HOLD_S)
    long = direction == "long"
    risk = abs(entry - stop)

    while True:
        await asyncio.sleep(POLL_S)
        st = stream.state.get(coin)
        if st is None or st.mid <= 0:
            continue
        mid = st.mid

        if (long and mid <= stop) or (not long and mid >= stop):
            reason, emoji, note = "STOP", "🛑", "Stop hit — exit now, thesis invalidated."
        elif (long and mid >= tgt) or (not long and mid <= tgt):
            reason, emoji, note = "TARGET", "🎯", "2R target reached — take profit / trail the rest."
        elif time.time() >= deadline:
            reason, emoji, note = "TIME", "⏰", (
                "Max hold reached — scalps don't become investments. Close or re-evaluate."
                if horizon == "scalp" else "Swing window expired — re-evaluate the thesis.")
        else:
            # momentum fade: only exits a trade with real profit banked after a
            # real hold; losers are the stop's job, scratches are noise
            profit = (mid - entry) if long else (entry - mid)
            enough_profit = profit >= FADE_MIN_PROFIT_R[horizon] * risk
            held_enough = (time.time() - opened) >= FADE_MIN_HOLD_S[horizon]
            tsig = tape.analyze(st)
            opposing = tsig.bias not in ("flat", direction) and tsig.strength >= FADE_STRENGTH
            if enough_profit and held_enough and opposing:
                reason, emoji, note = "FADE", "📉", (
                    f"Tape flipped {tsig.bias} (str {tsig.strength:.2f}) while in profit — "
                    "momentum gone, consider exiting into strength.")
            else:
                continue

        pnl_pct = ((mid - entry) / entry * 100) * (1 if long else -1)
        text = notifier.build_exit_alert(
            label=label, coin=coin, direction=direction, entry=entry, exit_px=mid,
            pnl_pct=pnl_pct, reason=reason, emoji=emoji, note=note, horizon=horizon,
            leverage=leverage,
        )
        await notifier.send(text)
        journal.record_exit(signal_id, exit_px=mid, reason=reason)
        print(f"[watcher] EXIT {reason} {coin} @ {mid} ({pnl_pct:+.2f}% raw)")
        return
