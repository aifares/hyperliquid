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
import market_hours
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
BIGSWING_MAX_HOLD_S = config.BIGSWING_MAX_HOLD_HOURS * 3600
RALLY_MAX_HOLD_S = config.RALLY_MAX_HOLD_HOURS * 3600
POLL_S = 2.0
RECONCILE_S = 10.0       # how often to re-check the REAL position (live trades
                         # only — matches account_monitor's own poll cadence,
                         # no point checking faster than the cache refreshes)
FADE_STRENGTH = 0.6      # opposing tape strength that counts as momentum death
# FADE must not scratch trades seconds after entry on tape flicker: require a
# minimum hold AND a minimum profit (in R = stop distance) before it can fire.
FADE_MIN_HOLD_S = {"scalp": 180, "swing": 3600, "bigswing": 3600, "rally": 900}
FADE_MIN_PROFIT_R = {"scalp": 0.5, "swing": 1.0, "bigswing": 1.0, "rally": 0.75}

_veto: dict[str, dict] = {}   # coin -> {...} armed full early-exit
_ratchet: set[str] = set()    # coins to ratchet the stop to breakeven (in profit)


def news_response(coin: str, confidence: float, magnitude: float,
                  headline: str) -> str | None:
    """Called from combiner when a signal opposes a currently-held scalp/
    swing/bigswing/rally position (opposite direction already confirmed by
    the caller). Three-tier, asymmetric by design — see config: it should be
    EASIER to protect a position than to open one.
      'exit'    -> full early close armed (strong bad news)
      'protect' -> breakeven-stop ratchet armed (medium bad news; the watcher
                   only acts on it if the position is actually in profit)
      None      -> below both bars, caller should warn only
    """
    if not config.NEWS_EXIT_SCALP_SWING:
        return None
    if confidence >= config.NEWS_EXIT_MIN_CONF and magnitude >= config.NEWS_EXIT_MIN_MAG:
        _veto[coin] = {"conf": confidence, "mag": magnitude,
                       "headline": headline, "ts": time.time()}
        print(f"[watcher] 📰 NEWS-EXIT armed for {coin} "
              f"(conf {confidence:.2f}, mag {magnitude:.2f}) — {headline[:80]!r}")
        return "exit"
    if confidence >= config.NEWS_PROTECT_MIN_CONF and magnitude >= config.NEWS_PROTECT_MIN_MAG:
        _ratchet.add(coin)
        print(f"[watcher] 🛡️ NEWS-PROTECT armed for {coin} "
              f"(conf {confidence:.2f}, mag {magnitude:.2f}) — breakeven if in profit")
        return "protect"
    return None


def target_price(entry: float, stop: float, direction: str, r_mult: float = 2.0) -> float:
    """R-multiple target (default 2R): r_mult times the stop distance, in
    the trade's favor. r_mult is only ever overridden by the bigswing tier
    (config.BIGSWING_TARGET_R) — scalp/swing keep the default 2R."""
    risk = abs(entry - stop)
    return entry + r_mult * risk if direction == "long" else entry - r_mult * risk


async def watch(*, signal_id: int, coin: str, label: str, direction: str,
                entry: float, stop: float, horizon: str, leverage: int,
                stream: HLStream, opened: float | None = None,
                equity_baseline: float | None = None,
                partial_done: bool = False) -> None:
    """equity_baseline is bigswing-only: the account equity snapshot at
    entry/adoption time, used by the hard equity safety net below. Ignored
    for scalp/swing/runup. partial_done=True on resume means the 1R half was
    already banked in a previous run (journal.partial_px) — never bank twice."""
    r_mult = (config.BIGSWING_TARGET_R if horizon == "bigswing"
              else config.RALLY_TARGET_R if horizon == "rally"
              else 2.0)
    tgt = target_price(entry, stop, direction, r_mult)
    opened = opened or time.time()
    if horizon == "scalp":
        max_hold = SCALP_MAX_HOLD_S
    elif horizon == "bigswing":
        max_hold = BIGSWING_MAX_HOLD_S
    elif horizon == "rally":
        max_hold = RALLY_MAX_HOLD_S
    else:
        max_hold = SWING_MAX_HOLD_S
    deadline = opened + max_hold
    long = direction == "long"
    risk = abs(entry - stop)
    # A paper position has no real order to reconcile against — this whole
    # check only applies to genuinely live trades.
    is_live = not executor.DRY_RUN and _live_executed(signal_id)
    next_reconcile = time.time() + RECONCILE_S
    # Partial-exit state: peak_profit drives the runner's 1R trail. Resumed
    # trades seed partial_done from the journal so the half is never banked
    # twice across restarts. Partials apply to swing/bigswing/rally only —
    # run-up keeps its validated exit shape, scalp is dead (0 slots).
    partial_ok = (config.PARTIAL_EXIT
                  and horizon in ("swing", "bigswing", "rally"))
    peak_profit = 0.0
    if partial_done:
        # Resumed after the half was already banked: the runner must come
        # back breakeven-protected, whatever the frozen entry-time stop was.
        stop = max(stop, entry) if long else min(stop, entry)
        tgt = (entry + config.PARTIAL_RUNNER_TARGET_R * risk if long
               else entry - config.PARTIAL_RUNNER_TARGET_R * risk)

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
                # VANISHED (not the old catch-all EXTERNAL): distinct code so
                # attribution queries can separate "left the exchange outside
                # the bot's own exits" from restart force-closes (ORPHAN) and
                # deliberate manual flips (EXTERNAL_FLIP).
                reason, emoji, note = "VANISHED", "🔚", (
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
                # Persist the correction too — without this the journal row
                # keeps the stale decision-time reference price forever, even
                # though the bot itself moves on to the real one in memory
                # (matters for building a backtest off real trade records).
                journal.update_entry(signal_id, entry)
                # A resize is itself a new event — the original entry/stop
                # relationship no longer applies regardless of whether this
                # trade predates the geometry fix, so recompute fresh here
                # (unlike resume_live, which must preserve a pre-fix trade's
                # frozen stop across a plain restart).
                stop = notifier.stop_price(entry, direction, horizon, coin)
                tgt = target_price(entry, stop, direction, r_mult)
                risk = abs(entry - stop)
                if partial_ok and partial_done:
                    # a resized runner keeps its breakeven protection
                    stop = max(stop, entry) if long else min(stop, entry)
                    tgt = (entry + config.PARTIAL_RUNNER_TARGET_R * risk if long
                           else entry - config.PARTIAL_RUNNER_TARGET_R * risk)

        if reason is None:
            if mid <= 0:
                continue
            profit = (mid - entry) if long else (entry - mid)

            # News-protect ratchet: bad news too weak to justify a full exit,
            # but enough to stop risking the gain. Only acts when in profit —
            # move the stop to breakeven (non-destructive: we stay in, but the
            # trade can't turn into a loss from here). Underwater positions are
            # left to the price stop and the higher full-exit bar; we do NOT
            # force a loss-close on medium news.
            if coin in _ratchet:
                _ratchet.discard(coin)
                # Require a REAL cushion (>=1R) before ratcheting to breakeven.
                # profit>0 alone was the SPCX bug: a +1% short got its stop
                # snapped to the entry-price magnet, and a tiny bounce shook it
                # out at breakeven 2h before a -10% crash. At >=1R the position
                # is well clear of entry, so a retest back to breakeven is a
                # genuine reversal worth protecting against, not noise. Move
                # ONLY the stop — recomputing tgt/risk off a breakeven stop
                # would zero the risk and collapse the target onto entry.
                if profit >= risk and ((long and entry > stop) or (not long and entry < stop)):
                    stop = entry
                    print(f"[watcher] 🛡️ {coin} stop -> breakeven {entry:g} "
                          f"(news-protect, up >=1R)")
                else:
                    print(f"[watcher] news-protect on {coin} skipped — only up "
                          f"{profit:g} (< 1R {risk:g}); not snapping to breakeven")

            # --- partial exit at 1R + trailed runner (audit 2026-07-17) ----
            # Zero of 45 trades ever reached the 2R target; winners exited
            # via FADE at ~1R, making realized geometry symmetric (1R win vs
            # 1R loss at 39% win rate = negative). At +1R: bank half, move
            # the stop on the rest to breakeven (the trade can no longer
            # lose), and let the runner ride toward 3R behind a 1R trail.
            peak_profit = max(peak_profit, profit)
            if partial_ok and not partial_done and profit >= risk > 0:
                live_pos = account_monitor.get(coin) if is_live else None
                if is_live and live_pos is None:
                    pass   # gone from the exchange — reconcile handles it
                elif (live_pos is not None
                      and abs(live_pos.size) * mid < config.PARTIAL_MIN_NOTIONAL):
                    # too small to split into two >=$10 pieces: skip the bank,
                    # keep the whole position but still breakeven-protect it
                    partial_done = True
                    stop = max(stop, entry) if long else min(stop, entry)
                    tgt = (entry + config.PARTIAL_RUNNER_TARGET_R * risk if long
                           else entry - config.PARTIAL_RUNNER_TARGET_R * risk)
                    print(f"[watcher] 💰 {coin} at +1R but notional too small "
                          f"to split — breakeven + trail on the full size")
                else:
                    bank_note = "paper"
                    if is_live:
                        await executor._load_sz_decimals()  # noqa: SLF001
                        bank_note = await asyncio.to_thread(
                            executor.partial_close_sync, coin,
                            config.PARTIAL_EXIT_FRAC)
                    partial_done = True
                    journal.mark_partial(signal_id, mid)
                    stop = entry
                    tgt = (entry + config.PARTIAL_RUNNER_TARGET_R * risk if long
                           else entry - config.PARTIAL_RUNNER_TARGET_R * risk)
                    print(f"[watcher] 💰 PARTIAL {coin} @ {mid:g} (+1R): "
                          f"{bank_note}; runner -> breakeven stop, "
                          f"{config.PARTIAL_RUNNER_TARGET_R:g}R target")
                    await notifier.send(
                        f"💰 <b>Banked half your {direction.upper()} {label}"
                        f"</b> ({coin}, {horizon}) at +1R\n"
                        f"<b>Entry:</b> {entry:g} → <b>Now:</b> {mid:g}\n"
                        f"{bank_note}\n"
                        f"<i>Runner: stop moved to breakeven (can't lose from "
                        f"here), trailing 1R behind the best price toward "
                        f"{config.PARTIAL_RUNNER_TARGET_R:g}R.</i>")
            if partial_ok and partial_done:
                # 1R trail from the high-water mark — only ever tightens
                trail = ((entry + peak_profit) - risk if long
                         else (entry - peak_profit) + risk)
                stop = max(stop, trail) if long else min(stop, trail)

            # --- bigswing-only guardrails (checked ahead of the normal
            # stop/target so they act as an override, not an afterthought) --
            equity_now = None
            if (horizon == "bigswing" and equity_baseline
                    and account_monitor.has_polled()):
                equity_now = account_monitor.account_value()

            if (equity_now is not None and equity_now > 0
                    and equity_now <= equity_baseline * (1 - config.BIGSWING_EQUITY_STOP_PCT)):
                reason, emoji, note = "EQUITY_STOP", "🚨", (
                    f"Hard equity safety net: account equity ${equity_now:,.2f} is "
                    f"at/below the floor ${equity_baseline * (1 - config.BIGSWING_EQUITY_STOP_PCT):,.2f} "
                    f"({config.BIGSWING_EQUITY_STOP_PCT*100:.0f}% down from the "
                    f"${equity_baseline:,.2f} snapshot at entry) — force-closing NOW, "
                    f"independent of the resting price stop, in case a gap jumped "
                    f"past it or the trigger order failed.")
            elif (long and mid <= stop) or (not long and mid >= stop):
                if partial_done and profit > 0:
                    reason, emoji, note = "TRAIL", "🏁", (
                        "Trailing stop hit — runner gave back 1R from its best "
                        "price; banking the rest in profit (half was already "
                        "taken at +1R).")
                else:
                    reason, emoji, note = "STOP", "🛑", "Stop hit — exit now, thesis invalidated."
            elif (long and mid >= tgt) or (not long and mid <= tgt):
                reason, emoji, note = "TARGET", "🎯", "2R target reached — take profit / trail the rest."
            elif (horizon == "bigswing" and coin.startswith("xyz:")
                  and coin not in config.CONTINUOUS_MARKETS
                  and market_hours.closing_soon() and profit < risk):
                reason, emoji, note = "OFFHOURS_DERISK", "🌙", (
                    "Market closing soon and this trade isn't up >=1R yet — "
                    "flattening rather than holding a full-balance position "
                    "through an overnight/weekend gap (your own backtest "
                    "flags repeated overnight holds at this size/leverage as "
                    "not survivable; see backtests/RESULTS.md).")
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
                # (profit already computed above, ahead of the bigswing checks)
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
        # this for VANISHED (already gone — nothing to close) and
        # EXTERNAL_FLIP (a human changed it deliberately — the bot must not
        # touch a position it no longer recognizes, only stop watching it).
        if is_live and reason not in ("VANISHED", "EXTERNAL_FLIP"):
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
        _veto.pop(coin, None)      # position gone; drop any stale flags
        _ratchet.discard(coin)
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
    first in-loop reconcile tick. The STOP is resolved via
    notifier.resolve_stop() — its own frozen value from entry time, or the
    legacy formula for a pre-fix row — never recomputed from today's config,
    so a geometry change (SCALP_STOP_RAW/SWING_STOP_RAW) never retroactively
    moves a stop under a position that's already open."""
    n = 0
    for (sid, coin, direction, horizon, lev, entry, ts, stored_stop,
         partial_px) in journal.open_live_rows():
        label = config.MARKET_BY_COIN[coin].label if coin in config.MARKET_BY_COIN else coin
        live_pos = account_monitor.get(coin) if account_monitor.has_polled() else None
        if live_pos:
            entry = live_pos.entry or entry
            lev = live_pos.leverage or lev
        stop = notifier.resolve_stop(entry, direction, lev, horizon, stored_stop)
        asyncio.create_task(watch(
            signal_id=sid, coin=coin, label=label, direction=direction,
            entry=entry, stop=stop, horizon=horizon, leverage=lev, stream=stream,
            opened=ts, partial_done=partial_px is not None,
        ))
        n += 1
    return n
