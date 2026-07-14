"""Combiner: turn a Claude NewsSignal + live tape into an alert decision.

A full BUY/SELL alert fires only when the news idea and the order-flow tape
AGREE. News-only ideas are downgraded to a low-priority 'heads up'. Per-market
cooldown prevents spam.
"""
from __future__ import annotations

import asyncio
import time

import config
import earnings
import executor
import journal
import liquidations
import market_hours
import notifier
import tape
import tg_buttons
import watcher
from analyzer import NewsSignal
from hl_stream import HLStream

_last_alert: dict[str, float] = {}   # "coin:direction" -> ts of last full alert
_recent_alerts: list[float] = []     # ts of every full alert, any market


def _cooldown_ok(key: str) -> bool:
    last = _last_alert.get(key, 0.0)
    return (time.time() - last) >= config.ALERT_COOLDOWN_SECONDS


def _burst_ok() -> bool:
    cutoff = time.time() - config.ALERT_BURST_WINDOW_S
    _recent_alerts[:] = [t for t in _recent_alerts if t >= cutoff]
    return len(_recent_alerts) < config.ALERT_BURST_MAX


async def handle_signal(sig: NewsSignal, stream: HLStream) -> None:
    """Called for every analyzed news item."""
    if not sig.actionable:
        return
    market = config.MARKET_BY_COIN[sig.coin]
    st = stream.state.get(sig.coin)
    if st is None or st.mid <= 0:
        print(f"[combiner] no live price for {sig.coin}; skipping")
        return

    tsig = tape.analyze(st)
    entry = st.mid
    if sig.horizon == "swing":
        leverage = min(config.SWING_LEVERAGE, market.max_leverage)
    else:
        leverage = min(config.DEFAULT_LEVERAGE, market.max_leverage)

    confirmed = tsig.confirms(sig.direction)
    tape_note = (
        f"flow {tsig.flow_ratio:.2f}, book {tsig.book_imbalance:.2f}, "
        f"bias {tsig.bias} (str {tsig.strength:.2f}); {liquidations.pressure_note(sig.coin)}"
    )
    e_note = earnings.note(sig.coin)
    if e_note:
        tape_note += f"; {e_note}"
    off_note = market_hours.off_hours_tag(sig.coin)
    # Same coin + same direction = same thesis, regardless of horizon —
    # Perplexity re-serves reworded versions of one headline for a while.
    cd_key = f"{sig.coin}:{sig.direction}"

    # Scalps live or die on immediate flow -> tape must confirm.
    # Swings ride a multi-day thesis -> tape is context, not a gate (but a
    # strongly opposing tape still holds the alert back).
    if sig.horizon == "swing":
        fire = not (tsig.bias not in ("flat", sig.direction) and tsig.strength >= 0.5)
        tape_verdict = " — context only (swing)"
    else:
        fire = confirmed
        tape_verdict = " — CONFIRMS" if confirmed else ""

    if fire and _cooldown_ok(cd_key) and not _burst_ok():
        print(f"[combiner] burst limit: suppressing {sig.direction} {sig.coin} "
              f"({config.ALERT_BURST_MAX} alerts already in "
              f"{config.ALERT_BURST_WINDOW_S // 60}m)")
        return

    if fire and _cooldown_ok(cd_key):
        _last_alert[cd_key] = time.time()
        _recent_alerts.append(time.time())
        text = notifier.build_alert(
            label=market.label, coin=sig.coin, direction=sig.direction,
            entry=entry, leverage=leverage, confidence=sig.confidence,
            magnitude=sig.magnitude, rationale=sig.rationale,
            headline=sig.event.text, tape_note=tape_note + tape_verdict,
            horizon=sig.horizon, off_hours_note=off_note,
        )
        if sig.horizon == "swing":
            gap_warn = earnings.swing_gap_warning(sig.coin)
            if gap_warn:
                text = f"{gap_warn}\n\n{text}"
        sid = journal.log_signal(
            coin=sig.coin, direction=sig.direction, confidence=sig.confidence,
            magnitude=sig.magnitude, leverage=leverage, entry=entry,
            headline=sig.event.text, rationale=sig.rationale, tape_note=tape_note,
            horizon=sig.horizon,
        )
        stop = notifier.stop_price(entry, sig.direction, leverage)
        pid = executor.register(
            signal_id=sid, coin=sig.coin, label=market.label,
            direction=sig.direction, entry_ref=entry, stop=stop,
            target=watcher.target_price(entry, stop, sig.direction),
            leverage=leverage, horizon=sig.horizon, confidence=sig.confidence,
        )
        if executor.DRY_RUN and config.AUTO_EXECUTE_DRY_RUN:
            await notifier.send(text)
            result = await executor.execute(pid)
            await notifier.send(f"🤖 auto-executed (dry run):\n{result}")
            print(f"[combiner] auto-exec dry-run: {result.splitlines()[0]}")
        else:
            await tg_buttons.send_trade_alert(text, pid)
        asyncio.create_task(_track_outcome(sid, stream, sig.coin))
        asyncio.create_task(watcher.watch(
            signal_id=sid, coin=sig.coin, label=market.label,
            direction=sig.direction, entry=entry, stop=stop,
            horizon=sig.horizon, leverage=leverage, stream=stream,
        ))
        print(f"[combiner] ALERT {sig.horizon} {sig.direction} {sig.coin} @ {entry}")
    else:
        reason = "cooldown" if fire else "tape disagrees"
        # Heads-up: news is real but not confirmed by flow. Low-priority note.
        heads_up = (
            f"👀 <b>Heads-up ({sig.direction.upper()} {market.label}, {sig.horizon})</b>\n"
            f"News looks {sig.direction} but {reason}.\n"
            f"<b>Headline:</b> {sig.event.text}\n"
            f"<b>Tape:</b> {tape_note}\n"
            f"<i>No confirmed entry — watching.</i>"
        )
        if reason != "cooldown":
            await notifier.send(heads_up)
        print(f"[combiner] heads-up {sig.coin} ({reason})")


async def _track_outcome(signal_id: int, stream: HLStream, coin: str) -> None:
    """Record price 1/5/30m after an alert for shadow-mode scoring."""
    start = time.time()
    for offset, col in ((60, "px_1m"), (300, "px_5m"), (1800, "px_30m")):
        delay = offset - (time.time() - start)
        if delay > 0:
            await asyncio.sleep(delay)
        st = stream.state.get(coin)
        if st and st.mid > 0:
            journal.record_followup(signal_id, col, st.mid)
