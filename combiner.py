"""Combiner: turn a Claude NewsSignal + live tape into an alert decision.

A full BUY/SELL alert fires only when the news idea and the order-flow tape
AGREE. News-only ideas are downgraded to a low-priority 'heads up'. Per-market
cooldown prevents spam.
"""
from __future__ import annotations

import asyncio
import time

import account_monitor
import config
import earnings
import earnings_runup
import executor
import journal
import liquidations
import market_hours
import notifier
import tape
import tg_buttons
import trend
import watcher
from analyzer import NewsSignal
from hl_stream import HLStream

_last_alert: dict[str, float] = {}   # "coin:direction" -> ts of last full alert
_recent_alerts: list[float] = []     # ts of every full alert, any market
_last_contra: dict[str, float] = {}  # "coin" -> ts of last contra-signal warning
_last_trend_note: dict[str, float] = {}  # "coin:direction" -> ts of last trend-block note
_signal_ledger: dict[str, dict] = {} # coin -> {"direction","confidence","ts"} of
                                      # the last actionable read, any tier/outcome —
                                      # powers the reversal guard below


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

    # A bearish read can veto a held run-up early — independent of whether the
    # news TIER goes on to trade it (cooldown/budget/tape gating below don't
    # apply to protecting an open run-up from a real catalyst).
    earnings_runup.flag_bearish(
        sig.coin, sig.direction, sig.confidence, sig.magnitude, sig.event.text)

    # Per-coin signal ledger: capture the PRIOR read before overwriting, so
    # the reversal guard below can compare "what we just said" to "what we're
    # saying now" — this is what makes a flip cost more than a confirmation.
    prior = _signal_ledger.get(sig.coin)
    _signal_ledger[sig.coin] = {
        "direction": sig.direction, "confidence": sig.confidence, "ts": time.time()}

    # You already hold this coin the OTHER way — this is a contradiction of a
    # real position, not just news noise. Existence + direction come from the
    # LIVE exchange position (catches a manual trade the journal never logged,
    # or a manual flip the journal's stored direction would miss); the tier
    # label is journal-only since Hyperliquid has no concept of tiers — a
    # fully manual position with no matching journal row is labeled "manual".
    # Reframe as a warning (never a trade alert) regardless of tape/cooldown,
    # and never open the opposite side — coin_direction_conflict blocks it
    # anyway (the exchange nets same-coin fills into one position).
    live_pos = account_monitor.get(sig.coin)
    if live_pos and live_pos.side != sig.direction:
        journal_row = journal.held_position(sig.coin)
        tier = journal_row[1] if journal_row else "manual"
        # Scalp/swing has no other way to cut a losing trade on a dead thesis
        # (FADE only protects winners; STOP is a big price move away) — a
        # strong enough opposing read arms a real early exit, same bars as
        # the run-up news-veto. Run-up itself is armed separately above.
        armed = tier in ("scalp", "swing") and watcher.flag_bearish(
            sig.coin, sig.confidence, sig.magnitude, sig.event.text)
        if time.time() - _last_contra.get(sig.coin, 0.0) >= config.ALERT_COOLDOWN_SECONDS:
            _last_contra[sig.coin] = time.time()
            if armed:
                await notifier.send(
                    f"📰 <b>News-exit armed ({market.label})</b>\n"
                    f"You hold {live_pos.side.upper()} ({tier}) — news reads "
                    f"{sig.direction.upper()} with conviction (conf "
                    f"{sig.confidence:.2f}, mag {sig.magnitude:.2f}).\n"
                    f"<b>Headline:</b> {sig.event.text}\n"
                    f"<i>Closing early on the next check (~2s) rather than "
                    f"waiting for the stop or the clock.</i>")
            else:
                await notifier.send(
                    f"⚠️ <b>Contra-signal ({market.label})</b>\n"
                    f"You hold {live_pos.side.upper()} ({tier}) — news now reads "
                    f"{sig.direction.upper()} (conf {sig.confidence:.2f}).\n"
                    f"<b>Headline:</b> {sig.event.text}\n"
                    f"<i>Not opening the opposite side — one position per coin. "
                    f"Watching, not acting.</i>")
        print(f"[combiner] contra-signal {sig.direction} {sig.coin} "
              f"(holding {live_pos.side}, {tier}) armed={armed}")
        return

    # --- entry-quality gates (2026-07-14, from live-trade review) -------------
    # These stop NEW entries only; the run-up veto and contra-signal handling
    # above already ran, so held positions stay fully protected regardless.
    if sig.horizon == "scalp" and sig.coin in config.SCALP_EXCLUDE:
        print(f"[combiner] no index scalps: {sig.direction} {sig.coin} skipped "
              f"(recap headlines aren't catalysts; swing on indexes still allowed)")
        return
    if (config.SCALP_RTH_ONLY and sig.horizon == "scalp"
            and sig.coin.startswith("xyz:") and not market_hours.is_rth()):
        print(f"[combiner] off-hours: scalp {sig.direction} {sig.coin} skipped "
              f"(thin book outside NYSE RTH; the losing 05:20 MU short is why)")
        return
    # Never fade a strong same-day move — the 30s tape window can't see daily
    # trend, so this is checked against actual 24h price change. to_thread:
    # the lookup may do one cached network refresh and must not block the loop.
    fade = await asyncio.to_thread(trend.fade_block, sig.coin, sig.direction)
    if fade:
        key = f"{sig.coin}:{sig.direction}"
        if time.time() - _last_trend_note.get(key, 0.0) >= config.ALERT_COOLDOWN_SECONDS:
            _last_trend_note[key] = time.time()
            await notifier.send(
                f"🚫 <b>Trend filter ({market.label})</b>\n{fade}\n"
                f"<b>Headline:</b> {sig.event.text}\n"
                f"<i>Not fighting the day's move — watching, not acting.</i>")
        print(f"[combiner] trend filter: {sig.direction} {sig.coin} blocked — {fade}")
        return

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

    # Reversal guard: flipping direction on the same coin soon after a fairly
    # confident opposite read needs to clear a higher conviction bar than a
    # fresh or confirming signal — one headline shouldn't undo a thesis that
    # was just backed by a real read. This is the flip-flop the news feed
    # produces on ordinary back-and-forth headlines, not a genuine reversal.
    is_reversal = (
        prior is not None and prior["direction"] != sig.direction
        and prior["confidence"] >= config.NEWS_REVERSAL_PRIOR_MIN_CONF
        and (time.time() - prior["ts"]) < config.NEWS_REVERSAL_WINDOW_S
    )
    reversal_note = ""
    if is_reversal and sig.confidence < config.NEWS_REVERSAL_MIN_CONF:
        fire = False
        age_m = int((time.time() - prior["ts"]) / 60)
        reversal_note = (
            f" — reversal from {prior['direction']} {age_m}m ago "
            f"(conf {sig.confidence:.2f} < {config.NEWS_REVERSAL_MIN_CONF} needed to flip)")

    if fire and _cooldown_ok(cd_key) and not _burst_ok():
        print(f"[combiner] burst limit: suppressing {sig.direction} {sig.coin} "
              f"({config.ALERT_BURST_MAX} alerts already in "
              f"{config.ALERT_BURST_WINDOW_S // 60}m)")
        return

    if fire and _cooldown_ok(cd_key):
        auto = config.AUTO_EXECUTE_DRY_RUN if executor.DRY_RUN else config.AUTO_EXECUTE_LIVE
        conflict = executor.coin_direction_conflict(sig.coin, sig.direction)
        # Guardrails are checked BEFORE the signal is journaled: a fully-auto
        # signal that can't execute anyway must not leave a phantom "open"
        # row with no exit_reason, or the dashboard shows a fake position
        # forever. A same-coin opposite-direction position is blocked outright.
        block = conflict or (auto and executor.guardrail_block(sig.horizon))
        if auto and block:
            await notifier.send(
                f"⛔ Skipped {sig.direction.upper()} {market.label} ({sig.horizon}): "
                f"{block}\n<b>Headline:</b> {sig.event.text}")
            print(f"[combiner] skipped {sig.direction} {sig.coin}: {block}")
            return
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
        # Computed BEFORE log_signal and frozen into the row (journal.stop):
        # this exact value is what the trade keeps for its entire life, even
        # if SCALP_STOP_RAW/SWING_STOP_RAW change later — see
        # notifier.resolve_stop() and the "new trades only" scoping.
        stop = notifier.stop_price(entry, sig.direction, sig.horizon)
        sid = journal.log_signal(
            coin=sig.coin, direction=sig.direction, confidence=sig.confidence,
            magnitude=sig.magnitude, leverage=leverage, entry=entry,
            headline=sig.event.text, rationale=sig.rationale, tape_note=tape_note,
            horizon=sig.horizon, stop=stop,
        )
        pid = executor.register(
            signal_id=sid, coin=sig.coin, label=market.label,
            direction=sig.direction, entry_ref=entry, stop=stop,
            target=watcher.target_price(entry, stop, sig.direction),
            leverage=leverage, horizon=sig.horizon, confidence=sig.confidence,
        )
        if auto:
            mode = "dry run" if executor.DRY_RUN else "LIVE"
            await notifier.send(text)
            result = await executor.execute(pid)
            await notifier.send(f"🤖 auto-executed ({mode}):\n{result}")
            print(f"[combiner] auto-exec {mode}: {result.splitlines()[0]}")
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
        if reversal_note:
            reason = "needs more conviction to flip" + reversal_note
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
