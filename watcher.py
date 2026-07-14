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

import account_monitor
import config
import executor
import journal
import notifier
import tape
from hl_stream import HLStream


def _live_executed(signal_id: int) -> bool:
    with journal._conn() as c:  # noqa: SLF001
        row = c.execute("SELECT executed, dry_run FROM signals WHERE id=?",
                        (signal_id,)).fetchone()
    return bool(row and row[0] and not row[1])

SCALP_MAX_HOLD_S = 4 * 3600
SWING_MAX_HOLD_S = 72 * 3600
POLL_S = 2.0
RECONCILE_S = 10.0       # how often to re-check the REAL position (live trades
                         # only — matches account_monitor's own poll cadence,
                         # no point checking faster than the cache refreshes)
FADE_STRENGTH = 0.6      # opposing tape strength that counts as momentum death
# FADE must not scratch trades seconds after entry on tape flicker: require a
# minimum hold AND a minimum profit (in R = stop distance) before it can fire.
FADE_MIN_HOLD_S = {"scalp": 180, "swing": 3600}
FADE_MIN_PROFIT_R = {"scalp": 0.5, "swing": 1.0}

_veto: dict[str, dict] = {}   # coin -> {"conf","mag","headline","ts"} armed early-exit


def flag_bearish(coin: str, confidence: float, magnitude: float, headline: str) -> bool:
    """Called from combiner when a signal opposes a currently-held scalp/swing
    position (direction already confirmed opposite by the caller). Arms an
    early NEWS exit — same conviction bars as the run-up veto, since FADE only
    protects a position that's already winning; a losing trade has nothing
    else to catch a genuinely dead thesis before the clock runs out. Returns
    whether it actually armed, so the caller can tell the user which happened."""
    if not config.NEWS_EXIT_SCALP_SWING:
        return False
    if confidence < config.NEWS_EXIT_MIN_CONF or magnitude < config.NEWS_EXIT_MIN_MAG:
        return False
    _veto[coin] = {"conf": confidence, "mag": magnitude,
                   "headline": headline, "ts": time.time()}
    print(f"[watcher] 📰 NEWS-VETO armed for {coin} "
          f"(conf {confidence:.2f}, mag {magnitude:.2f}) — {headline[:80]!r}")
    return True


def target_price(entry: float, stop: float, direction: str) -> float:
    """2R target: twice the stop distance, in the trade's favor."""
    risk = abs(entry - stop)
    return entry + 2 * risk if direction == "long" else entry - 2 * risk


async def watch(*, signal_id: int, coin: str, label: str, direction: str,
                entry: float, stop: float, horizon: str, leverage: int,
                stream: HLStream, opened: float | None = None) -> None:
    tgt = target_price(entry, stop, direction)
    opened = opened or time.time()
    deadline = opened + (SCALP_MAX_HOLD_S if horizon == "scalp" else SWING_MAX_HOLD_S)
    long = direction == "long"
    risk = abs(entry - stop)
    # A paper position has no real order to reconcile against — this whole
    # check only applies to genuinely live trades.
    is_live = not executor.DRY_RUN and _live_executed(signal_id)
    next_reconcile = time.time() + RECONCILE_S

    while True:
        await asyncio.sleep(POLL_S)
        st = stream.state.get(coin)
        mid = st.mid if st and st.mid > 0 else 0.0
        reason = emoji = note = None

        # Reconcile against the REAL position periodically — catches a manual
        # close/flip/resize the bot would otherwise never learn about, since
        # entry/stop/leverage above were only ever captured once at open time.
        if is_live and time.time() >= next_reconcile and account_monitor.has_polled():
            next_reconcile = time.time() + RECONCILE_S
            live_pos = account_monitor.get(coin)
            if live_pos is None:
                reason, emoji, note = "EXTERNAL", "🔚", (
                    "Position no longer exists on the exchange (closed "
                    "manually, an exchange-side order filled, or liquidation) "
                    "— stopping the watch, nothing left here to manage.")
                mid = mid or entry
            elif live_pos.side != direction:
                reason, emoji, note = "EXTERNAL_FLIP", "🔀", (
                    f"Position was flipped to {live_pos.side} outside the bot — "
                    f"this thesis no longer applies to what's actually held. "
                    f"Stopping the watch rather than adopting the new side.")
                mid = live_pos.entry or mid or entry
            elif live_pos.entry > 0 and (
                    abs(live_pos.entry - entry) > 1e-9
                    or (live_pos.leverage and live_pos.leverage != leverage)):
                print(f"[watcher] {coin} resized live: entry {entry:g}->"
                      f"{live_pos.entry:g}, lev {leverage}x->{live_pos.leverage}x "
                      f"— recomputing stop/target off the real position")
                entry = live_pos.entry
                leverage = live_pos.leverage or leverage
                stop = notifier.stop_price(entry, direction, leverage)
                tgt = target_price(entry, stop, direction)
                risk = abs(entry - stop)

        if reason is None:
            if mid <= 0:
                continue
            if (long and mid <= stop) or (not long and mid >= stop):
                reason, emoji, note = "STOP", "🛑", "Stop hit — exit now, thesis invalidated."
            elif (long and mid >= tgt) or (not long and mid <= tgt):
                reason, emoji, note = "TARGET", "🎯", "2R target reached — take profit / trail the rest."
            elif coin in _veto:
                v = _veto.pop(coin)
                reason, emoji, note = "NEWS", "📰", (
                    f"Bearish catalyst against this position (conf {v['conf']:.2f}, "
                    f"mag {v['mag']:.2f}) — exiting early rather than waiting on "
                    f"the stop or the clock: {v['headline'][:120]}")
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

        # Live positions must actually leave the exchange: FADE/TIME have no
        # resting order, STOP/TARGET leave a sibling trigger to cancel. Skip
        # this for EXTERNAL (already gone — nothing to close) and
        # EXTERNAL_FLIP (a human changed it deliberately — the bot must not
        # touch a position it no longer recognizes, only stop watching it).
        if is_live and reason not in ("EXTERNAL", "EXTERNAL_FLIP"):
            status = await asyncio.to_thread(executor.close_position_sync, coin)
            print(f"[watcher] live close {coin}: {status}")

        pnl_pct = ((mid - entry) / entry * 100) * (1 if long else -1)
        text = notifier.build_exit_alert(
            label=label, coin=coin, direction=direction, entry=entry, exit_px=mid,
            pnl_pct=pnl_pct, reason=reason, emoji=emoji, note=note, horizon=horizon,
            leverage=leverage,
        )
        await notifier.send(text)
        journal.record_exit(signal_id, exit_px=mid, reason=reason)
        _veto.pop(coin, None)   # position gone; drop any stale veto flag
        print(f"[watcher] EXIT {reason} {coin} @ {mid} ({pnl_pct:+.2f}% raw)")
        return


def resume_live(stream: HLStream) -> int:
    """Re-arm watchers for real (dry_run=0) scalp/swing positions that
    survived a restart — a genuine order + resting stop/target already sits
    on the exchange, so these must be watched, never force-scratched like
    dry-run rows are (see journal.close_orphans). Seeds entry/leverage from
    the LIVE position when the cache already has data (not just the
    journal's stale snapshot), so a manual resize between the last shutdown
    and this restart is reflected immediately rather than waiting for the
    first in-loop reconcile tick."""
    n = 0
    for sid, coin, direction, horizon, lev, entry, ts in journal.open_live_rows():
        label = config.MARKET_BY_COIN[coin].label if coin in config.MARKET_BY_COIN else coin
        live_pos = account_monitor.get(coin) if account_monitor.has_polled() else None
        if live_pos:
            entry = live_pos.entry or entry
            lev = live_pos.leverage or lev
        stop = notifier.stop_price(entry, direction, lev)
        asyncio.create_task(watch(
            signal_id=sid, coin=coin, label=label, direction=direction,
            entry=entry, stop=stop, horizon=horizon, leverage=lev, stream=stream,
            opened=ts,
        ))
        n += 1
    return n
