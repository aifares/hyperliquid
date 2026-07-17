"""Combiner: turn a Claude NewsSignal + live tape into an alert decision.

A full BUY/SELL alert fires only when the news idea and the order-flow tape
AGREE. News-only ideas are downgraded to a low-priority 'heads up'. Per-market
cooldown prevents spam.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import account_monitor
import config
import earnings
import earnings_runup
import executor
import funding
import journal
import liquidations
import market_hours
import notifier
import smartmoney
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
_shadow_last: dict[str, float] = {}      # "coin:direction" -> ts of last shadow entry
_SHADOW_LOG = Path(__file__).with_name("shadow_crypto.jsonl")
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


def latest_read(coin: str) -> dict | None:
    """The most recent actionable Claude news read for `coin` (any
    tier/outcome), or None. Backs the reversal guard above; also reused by
    bigswing.py as a SECONDARY confirm/veto on its own technical/orderbook
    signal — bigswing never leads with news, but a strongly opposing recent
    read still blocks an entry."""
    return _signal_ledger.get(coin)


async def handle_signal(sig: NewsSignal, stream: HLStream) -> None:
    """Called for every analyzed news item."""
    if not sig.actionable:
        return
    market = config.MARKET_BY_COIN[sig.coin]

    # Overnight crypto SHADOW: when the US market is closed the stock perps are
    # thin and the bot's RTH gates block them, so the account sits idle. Route
    # crypto news signals to a paper-only shadow test instead (never a real
    # order) to collect forward data before committing capital. See config.
    if (config.CRYPTO_NIGHT_SHADOW and sig.coin in config.CRYPTO_COINS
            and not market_hours.is_rth()):
        await _shadow_crypto(sig, stream, market)
        return

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
        # SMART-MONEY SHIELD: if the informed wallets CONFIRM the position we
        # already hold, a contradictory news read is the suspect one — don't
        # let it arm an exit/protect. This is the SPCX lesson: a bullish
        # headline fired against our short right before a -10% crash the whale
        # was correctly short into; the news was wrong, the whale was right.
        if config.SMARTMONEY_ENABLED and smartmoney.has_polled():
            sm_held, sm_hnote = smartmoney.weight(sig.coin, live_pos.side)
            if sm_held > 0:
                if time.time() - _last_contra.get(sig.coin, 0.0) >= config.ALERT_COOLDOWN_SECONDS:
                    _last_contra[sig.coin] = time.time()
                    await notifier.send(
                        f"🛡️ <b>Ignoring contra-news on your {live_pos.side.upper()} "
                        f"{market.label}</b>\nA {sig.direction} headline came in, but "
                        f"{sm_hnote} — holding with the informed money, not the news.\n"
                        f"<b>Headline:</b> {notifier.esc(sig.event.text)}")
                print(f"[combiner] smart-money shield: kept {live_pos.side} {sig.coin} "
                      f"(sm confirms +{sm_held:.2f}), ignored contra news")
                return
        # Scalp/swing/bigswing have no other way to cut a losing trade on a
        # dead thesis (FADE only protects a position already up >=1R; the
        # price stop is a big move away) — a strong enough opposing read
        # arms a real early exit, same conservative bars as the run-up
        # news-veto (conf>=0.80/mag>=0.60, stricter than bigswing's own
        # 0.75 ENTRY-veto bar, appropriate since this force-closes a live
        # full-balance position rather than just blocking a new one).
        # Run-up itself is armed separately above.
        # Three-tier response (bot-managed tiers only; manual positions get a
        # heads-up but the bot won't act on a trade it didn't open).
        resp = (watcher.news_response(sig.coin, sig.confidence, sig.magnitude,
                                      sig.event.text)
                if tier in ("scalp", "swing", "bigswing", "rally") else None)
        held = f"{live_pos.side.upper()} {market.label} ({tier})"
        if time.time() - _last_contra.get(sig.coin, 0.0) >= config.ALERT_COOLDOWN_SECONDS:
            _last_contra[sig.coin] = time.time()
            if resp == "exit":
                await notifier.send(
                    f"📕 <b>Closing your {held} early</b>\n"
                    f"Strong opposing news (conf {sig.confidence:.2f}, mag "
                    f"{sig.magnitude:.2f}).\n<b>Headline:</b> {notifier.esc(sig.event.text)}\n"
                    f"<i>Market-closing on the next check (~2s) — not waiting "
                    f"on the stop or the clock. (This is an EXIT of your long, "
                    f"not a new short.)</i>")
            elif resp == "protect":
                await notifier.send(
                    f"🛡️ <b>Protecting your {held}</b>\n"
                    f"Conflicting news (conf {sig.confidence:.2f}, mag "
                    f"{sig.magnitude:.2f}) — not strong enough to bail, but "
                    f"enough to stop risking the gain.\n"
                    f"<b>Headline:</b> {notifier.esc(sig.event.text)}\n"
                    f"<i>If the position is in profit, its stop moves to "
                    f"breakeven so it can't turn into a loss. Holding, not "
                    f"shorting.</i>")
            else:
                await notifier.send(
                    f"⚠️ <b>Conflicting news on your {held}</b>\n"
                    f"A {sig.direction} read came in (conf {sig.confidence:.2f}) "
                    f"but it's too weak to act on.\n"
                    f"<b>Headline:</b> {notifier.esc(sig.event.text)}\n"
                    f"<i>Just a heads-up — this is NOT a signal to short. "
                    f"Holding your existing position.</i>")
        print(f"[combiner] contra {sig.direction} {sig.coin} "
              f"(holding {live_pos.side}, {tier}) -> {resp or 'warn'}")
        return

    # --- entry-quality gates (2026-07-14, from live-trade review) -------------
    # These stop NEW entries only; the run-up veto and contra-signal handling
    # above already ran, so held positions stay fully protected regardless.
    # No INDEX trading on ANY news tier (2026-07-16): the 2026-07-16 index
    # trades were the day's whipsaw losers — XYZ100/SP500 move on macro data
    # (CPI/inflation/Fed), which is the most efficiently-priced AND most
    # contradictory-headline-prone news there is. The feed literally served
    # "inflation worse than expected" and "inflation lower than expected" 90s
    # apart on the same print, sawing the bot out of both sides. Extended from
    # scalp-only to scalp AND swing (previously "swing on indexes allowed").
    if sig.horizon in ("scalp", "swing") and sig.coin in config.SCALP_EXCLUDE:
        print(f"[combiner] no index {sig.horizon}: {sig.direction} {sig.coin} "
              f"skipped (macro news efficiently priced + contradictory)")
        return
    if (config.SCALP_RTH_ONLY and sig.horizon == "scalp"
            and sig.coin.startswith("xyz:")
            and sig.coin not in config.CONTINUOUS_MARKETS
            and not market_hours.is_rth()):
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
                f"<b>Headline:</b> {notifier.esc(sig.event.text)}\n"
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
    # Smart-money weighting: how the validated-skill wallets + the whale are
    # positioned on this coin nudges our EFFECTIVE conviction up (confirm) or
    # down (oppose). eff_conf then drives BOTH the entry bar (below) and the
    # position size (passed to executor.register). sm still carries the
    # whale-oppose flag for the optional hard veto.
    sm = None
    sm_delta = 0.0
    if config.SMARTMONEY_ENABLED and smartmoney.has_polled():
        sm = smartmoney.signal(sig.coin, sig.direction)
        if config.SMARTMONEY_WEIGHTED:
            sm_delta, sm_note = smartmoney.weight(sig.coin, sig.direction)
            if sm_note:
                tape_note += f"; {sm_note}"
        elif sm:
            tape_note += f"; {sm.note}"
    eff_conf = max(0.0, min(1.0, sig.confidence + sm_delta))
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

    # Smart-money drag: if opposition pulled effective conviction below the
    # tier's entry floor, the trade no longer qualifies — a marginal read the
    # informed money disagrees with is exactly the one to skip. Strong reads
    # (high base conf) survive and just get sized down via eff_conf.
    if sm_delta < 0 and fire:
        floor = config.SWING_MIN_CONF if sig.horizon in ("swing", "bigswing") else config.SCALP_MIN_CONF
        if eff_conf < floor:
            fire = False
            reversal_note = (f" — smart-money drag: eff conf {eff_conf:.2f} < "
                             f"{floor:.2f} floor")

    # Hard smart-money veto (opt-in, off by default): don't take a trade the
    # $11M whale is positioned against, regardless of conviction — the single
    # biggest, most concentrated informed bet on the book. Only when VETO=1.
    if config.SMARTMONEY_VETO and sm and sm.verdict == "oppose" and sm.whale and fire:
        fire = False
        reversal_note = f" — {sm.note}"

    # Funding carry: a multi-day swing that bleeds funding needs to clear that
    # cost before it's worth taking. Measured live 2026-07-17: SKHX shorts pay
    # ~1.1%/day (crowd-shorted Korea/memory complex). >BLOCK/day = refuse;
    # >COSTLY/day = demand +0.05 more conviction. Scalps ignore it (intraday).
    if fire and sig.horizon in ("swing", "bigswing"):
        fcost = funding.daily_cost(sig.coin, sig.direction)
        if fcost is not None and fcost > config.FUNDING_BLOCK_DAILY:
            fire = False
            reversal_note = (f" — funding {fcost*100:.2f}%/day against a "
                             f"{sig.direction} here; carry eats the edge")
        elif fcost is not None and fcost > config.FUNDING_COSTLY_DAILY:
            if eff_conf < (config.SWING_MIN_CONF + 0.05):
                fire = False
                reversal_note = (f" — funding {fcost*100:.2f}%/day against us "
                                 f"needs eff conf >= {config.SWING_MIN_CONF + 0.05:.2f} "
                                 f"(have {eff_conf:.2f})")
            else:
                tape_note += f"; ⚠️ pays {fcost*100:.2f}%/day funding"

    if fire and _cooldown_ok(cd_key) and not _burst_ok():
        print(f"[combiner] burst limit: suppressing {sig.direction} {sig.coin} "
              f"({config.ALERT_BURST_MAX} alerts already in "
              f"{config.ALERT_BURST_WINDOW_S // 60}m)")
        return

    if fire and _cooldown_ok(cd_key):
        auto = config.AUTO_EXECUTE_DRY_RUN if executor.DRY_RUN else config.AUTO_EXECUTE_LIVE
        conflict = (executor.coin_direction_conflict(sig.coin, sig.direction)
                    or executor.correlation_block(sig.coin, sig.direction))
        # Guardrails are checked BEFORE the signal is journaled: a fully-auto
        # signal that can't execute anyway must not leave a phantom "open"
        # row with no exit_reason, or the dashboard shows a fake position
        # forever. A same-coin opposite-direction position is blocked outright;
        # eff_conf gates the reserved last swing slot (executor.guardrail_block).
        block = conflict or (auto and executor.guardrail_block(sig.horizon, eff_conf))
        if auto and block:
            await notifier.send(
                f"⛔ Skipped {sig.direction.upper()} {market.label} ({sig.horizon}): "
                f"{block}\n<b>Headline:</b> {notifier.esc(sig.event.text)}")
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
        stop = notifier.stop_price(entry, sig.direction, sig.horizon, sig.coin)
        # eff_conf (news conviction ± smart-money weight) is the OPERATIVE
        # conviction the trade is sized on, so store + register with it.
        sid = journal.log_signal(
            coin=sig.coin, direction=sig.direction, confidence=eff_conf,
            magnitude=sig.magnitude, leverage=leverage, entry=entry,
            headline=sig.event.text, rationale=sig.rationale, tape_note=tape_note,
            horizon=sig.horizon, stop=stop,
        )
        pid = executor.register(
            signal_id=sid, coin=sig.coin, label=market.label,
            direction=sig.direction, entry_ref=entry, stop=stop,
            target=watcher.target_price(entry, stop, sig.direction),
            leverage=leverage, horizon=sig.horizon, confidence=eff_conf,
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
            f"<b>Headline:</b> {notifier.esc(sig.event.text)}\n"
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


# --- overnight crypto shadow (paper only, NEVER a real order) ------------------
async def _shadow_crypto(sig: NewsSignal, stream: HLStream, market) -> None:
    """Off-hours crypto news signal -> tape-confirm, announce, and log a 4h
    paper outcome to shadow_crypto.jsonl. Deliberately isolated from the real
    trading path (its own cooldown, its own log file, no journal row, no
    executor call) so it can NEVER place an order or pollute live stats."""
    st = stream.state.get(sig.coin)
    if st is None or st.mid <= 0:
        return
    tsig = tape.analyze(st)
    if not tsig.confirms(sig.direction):   # only shadow what a real trade would take
        print(f"[shadow] {sig.coin} {sig.direction}: tape not confirming — skip")
        return
    key = f"{sig.coin}:{sig.direction}"
    if time.time() - _shadow_last.get(key, 0.0) < config.ALERT_COOLDOWN_SECONDS:
        return
    _shadow_last[key] = time.time()
    entry = st.mid
    await notifier.send(
        f"🌙 <b>[SHADOW] would {sig.direction.upper()} {market.label}</b> — "
        f"off-hours crypto test, NO real order.\n"
        f"Entry {entry:g} · conf {sig.confidence:.2f} mag {sig.magnitude:.2f}\n"
        f"<b>Headline:</b> {notifier.esc(sig.event.text)}\n"
        f"<i>Paper-holding {config.CRYPTO_SHADOW_HOLD_H}h to measure the edge.</i>")
    print(f"[shadow] would {sig.direction} {sig.coin} @ {entry:g} (off-hours crypto)")
    asyncio.create_task(_shadow_settle(
        sig.coin, sig.direction, entry, sig.confidence, sig.magnitude,
        sig.event.text, stream))


async def _shadow_settle(coin: str, direction: str, entry: float, conf: float,
                         mag: float, headline: str, stream: HLStream) -> None:
    await asyncio.sleep(config.CRYPTO_SHADOW_HOLD_H * 3600)
    st = stream.state.get(coin)
    exit_px = st.mid if st and st.mid > 0 else entry
    raw = (exit_px - entry) / entry * 100 * (1 if direction == "long" else -1)
    net = raw - config.ROUND_TRIP_FEE * 100
    rec = {"ts": time.time(), "coin": coin, "dir": direction, "entry": entry,
           "exit": exit_px, "raw_pct": round(raw, 3), "net_pct": round(net, 3),
           "conf": conf, "mag": mag, "hold_h": config.CRYPTO_SHADOW_HOLD_H,
           "headline": headline[:120]}
    try:
        with open(_SHADOW_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:  # noqa: BLE001 — a shadow-log write must never break trading
        print(f"[shadow] log write failed: {e!r}")
    await notifier.send(
        f"🌙 [SHADOW] {coin} {direction.upper()} settled (paper): "
        f"{net:+.2f}% net over {config.CRYPTO_SHADOW_HOLD_H}h")
    print(f"[shadow] settled {coin} {direction} {net:+.2f}% net")
