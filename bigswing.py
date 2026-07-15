"""Full-balance swing strategy ("bigswing") — the decision engine.

Standalone tier, independent of the news notifier's scalp/swing/runup tiers:
  - ONE position at a time, sized off nearly the full LIVE account balance
    (account_monitor.spot_available()), not the TOTAL_BANKROLL fractional pool
  - long or short, leverage scaled 5x-10x by a technical/orderbook conviction
    score from swing_signals.py (the PRIMARY trigger)
  - the existing Claude news pipeline is a SECONDARY confirm/veto only —
    reused via combiner.latest_read(), never a second news ledger
  - fully automated: no Telegram confirm button
  - also detects and ADOPTS a manually-opened position on a watched coin,
    attaching the same stop/target + overnight de-risk + hard equity safety
    net a bot-opened trade gets

Read backtests/RESULTS.md's "All-in single-stock strategy" section before
trusting this with real money — the aspirational return target this tier
chases is explicitly flagged there as unsupported by the data, and repeated
10x+ overnight holds are flagged as not survivable. The guardrails in
watcher.py (overnight de-risk, hard equity stop) exist specifically to
address that; config.BIGSWING_ENABLED defaults OFF and executor.DRY_RUN
defaults on (no HL_AGENT_PRIVATE_KEY) until you deliberately go live.
"""
from __future__ import annotations

import asyncio
import time

import account_monitor
import combiner
import config
import executor
import journal
import notifier
import swing_signals
import watcher
from hl_stream import HLStream
from swing_signals import SwingSignal

_open: dict | None = None   # {"coin", "sid"} while a bigswing position is live


def is_open() -> bool:
    return _open is not None


def _leverage_for(conviction: float, max_lev_market: int) -> int:
    """Conviction 0.6 -> BIGSWING_MIN_LEVERAGE, 1.0 -> BIGSWING_MAX_LEVERAGE,
    linear between; floor stays at the minimum below 0.6 (a trade only fires
    once conviction already clears BIGSWING_MIN_CONVICTION anyway)."""
    lo, hi = config.BIGSWING_MIN_LEVERAGE, config.BIGSWING_MAX_LEVERAGE
    if conviction <= 0.6:
        lev = lo
    else:
        frac = min((conviction - 0.6) / 0.4, 1.0)
        lev = lo + frac * (hi - lo)
    return max(lo, min(round(lev), max_lev_market, hi))


def _news_adjustment(coin: str, direction: str) -> tuple[float, bool, str]:
    """(conviction delta, vetoed, note) from the EXISTING Claude news
    pipeline's latest read on this coin — secondary confirmation only, reuses
    combiner._signal_ledger via latest_read() rather than a second ledger."""
    read = combiner.latest_read(coin)
    if read is None:
        return 0.0, False, "no recent news read"
    age_s = time.time() - read["ts"]
    if age_s > config.BIGSWING_NEWS_WINDOW_S:
        return 0.0, False, f"news read {age_s/3600:.1f}h old (stale, ignored)"
    if read["direction"] == direction:
        if read["confidence"] >= config.BIGSWING_NEWS_BOOST_MIN_CONF:
            return (config.BIGSWING_NEWS_BOOST_AMOUNT, False,
                    f"news confirms (conf {read['confidence']:.2f})")
        return 0.0, False, "news agrees but confidence too low to boost"
    if read["confidence"] >= config.BIGSWING_NEWS_VETO_MIN_CONF:
        return 0.0, True, (f"news opposes with conf {read['confidence']:.2f} "
                           f">= veto bar {config.BIGSWING_NEWS_VETO_MIN_CONF} "
                           f"— entry vetoed")
    return 0.0, False, "news opposes but not confidently enough to veto"


async def _try_enter(stream: HLStream) -> None:
    best: SwingSignal | None = None
    best_note = ""
    for market in config.BIGSWING_MARKETS:
        # evaluate() can hit the network on a cold/stale candles or funding
        # cache (candles.get()/trend.funding_rate()) — to_thread so a slow
        # fetch never stalls the websocket loop or anything else running
        # concurrently (same reasoning as combiner.py's trend.fade_block call).
        sig = await asyncio.to_thread(swing_signals.evaluate, market.coin)
        if sig.direction == "none":
            continue
        delta, vetoed, note = _news_adjustment(sig.coin, sig.direction)
        if vetoed:
            print(f"[bigswing] {sig.coin} {sig.direction} technical "
                  f"conv={sig.conviction:.2f} but {note}")
            continue
        conviction = min(1.0, sig.conviction + delta)
        min_conviction = (config.BIGSWING_BTC_MIN_CONVICTION if sig.coin == "BTC"
                          else config.BIGSWING_MIN_CONVICTION)
        if conviction < min_conviction:
            continue
        sig = SwingSignal(sig.coin, sig.direction, conviction, sig.trend_pct,
                          sig.breakout, sig.imbalance, sig.liq_note, sig.funding_note)
        if best is None or conviction > best.conviction:
            best, best_note = sig, note
    if best is not None:
        await _enter(best, best_note, stream)


async def _enter(sig: SwingSignal, news_note: str, stream: HLStream) -> None:
    global _open
    market = config.MARKET_BY_COIN[sig.coin]
    if account_monitor.get(sig.coin) is not None:
        return   # something already open on this coin, any tier — skip
    if not account_monitor.has_polled():
        return
    st = stream.state.get(sig.coin)
    if st is None or st.mid <= 0:
        return

    entry = st.mid
    leverage = _leverage_for(sig.conviction, market.max_leverage)
    free = account_monitor.spot_available()
    margin = round(free * (1 - config.BIGSWING_BALANCE_BUFFER), 2)
    if margin < config.MIN_MARGIN_PER_TRADE:
        print(f"[bigswing] only ${margin:.2f} free of ${free:.2f} — skipping {sig.coin}")
        return

    stop = (entry * (1 - config.BIGSWING_STOP_RAW) if sig.direction == "long"
            else entry * (1 + config.BIGSWING_STOP_RAW))
    target = watcher.target_price(entry, stop, sig.direction, config.BIGSWING_TARGET_R)

    conv_note = (
        (f"trend {sig.trend_pct:+.1f}%" if sig.trend_pct is not None else "trend n/a")
        + f" / breakout {sig.breakout}"
        + (f" / book {sig.imbalance:.2f}" if sig.imbalance is not None else " / book n/a")
        + f" / {sig.liq_note} / {sig.funding_note} / {news_note}"
    )

    result, sid = await executor.execute_bigswing(
        coin=sig.coin, label=market.label, direction=sig.direction,
        entry_ref=entry, stop=stop, target=target, leverage=leverage, margin=margin)
    if not sid:
        print(f"[bigswing] entry blocked for {sig.coin}: {result}")
        return

    equity_baseline = account_monitor.account_value()
    _open = {"coin": sig.coin, "sid": sid}
    mode = "DRY RUN" if executor.DRY_RUN else "LIVE"
    await notifier.send(
        f"🌊💰 <b>BIGSWING {mode} — {sig.direction.upper()} {market.label}</b> "
        f"({sig.coin})\n"
        f"Conviction {sig.conviction:.2f} -> {leverage}x, ${margin:.2f} margin\n"
        f"Entry ~{entry:g} | stop {stop:g} | target {target:g}\n"
        f"{conv_note}\n{result}\n"
        f"<i>Overnight de-risk + hard equity stop ({config.BIGSWING_EQUITY_STOP_PCT*100:.0f}%) "
        f"active. Baseline equity ${equity_baseline:,.2f}.</i>")
    print(f"[bigswing] ENTER {sig.direction} {sig.coin} @ {entry:g} "
          f"conv={sig.conviction:.2f} lev={leverage}x margin=${margin:.2f}")
    asyncio.create_task(_manage(
        sid, sig.coin, market.label, sig.direction, entry, stop, leverage,
        stream, equity_baseline))


async def _manage(sid: int, coin: str, label: str, direction: str, entry: float,
                  stop: float, leverage: int, stream: HLStream,
                  equity_baseline: float, opened: float | None = None) -> None:
    global _open
    try:
        await watcher.watch(
            signal_id=sid, coin=coin, label=label, direction=direction,
            entry=entry, stop=stop, horizon="bigswing", leverage=leverage,
            stream=stream, opened=opened, equity_baseline=equity_baseline,
        )
    finally:
        _open = None


# --- manual position adoption -------------------------------------------------
async def _check_adopt(stream: HLStream) -> bool:
    """Look for a live position bigswing doesn't already know about and no
    OTHER tier claims either. Returns True if one was adopted (caller should
    skip scanning for a NEW entry this cycle since the slot is now full)."""
    if not config.BIGSWING_ADOPT_MANUAL or is_open():
        return False
    if not account_monitor.has_polled():
        return False
    claimed = {coin for coin, _, _ in journal.all_held_positions()}
    for coin in account_monitor.untracked_coins(claimed):
        if coin not in config.MARKET_BY_COIN:
            continue   # not a coin bigswing knows how to manage
        await _adopt(coin, stream)
        return True
    return False


async def _adopt(coin: str, stream: HLStream) -> None:
    global _open
    pos = account_monitor.get(coin)
    if pos is None:
        return
    market = config.MARKET_BY_COIN[coin]
    direction = pos.side
    entry = pos.entry or (stream.state.get(coin).mid if stream.state.get(coin) else 0.0)
    if entry <= 0:
        return
    leverage = pos.leverage or config.BIGSWING_MIN_LEVERAGE
    stop = (entry * (1 - config.BIGSWING_STOP_RAW) if direction == "long"
            else entry * (1 + config.BIGSWING_STOP_RAW))
    target = watcher.target_price(entry, stop, direction, config.BIGSWING_TARGET_R)

    skip_bracket = False
    if executor.DRY_RUN:
        bracket_note = "🧪 DRY RUN — would attach a stop/target bracket (no real order placed)."
    else:
        if config.BIGSWING_ADOPT_SKIP_IF_STOP_EXISTS:
            skip_bracket = await asyncio.to_thread(executor.has_resting_stop, coin)
        if skip_bracket:
            bracket_note = "kept your existing resting order(s) — no duplicate stop/target added."
        else:
            bracket_note = await asyncio.to_thread(
                executor.attach_bracket_sync, coin, direction, abs(pos.size), stop, target)

    sid = journal.log_signal(
        coin=coin, direction=direction, confidence=1.0, magnitude=1.0,
        leverage=leverage, entry=entry, headline="bigswing adopted manual entry",
        rationale="user-opened position adopted for management", tape_note=bracket_note,
        horizon="bigswing", stop=None if skip_bracket else stop)
    journal.mark_executed(sid, dry_run=executor.DRY_RUN, margin=pos.margin_used)

    equity_baseline = account_monitor.account_value()
    _open = {"coin": coin, "sid": sid}
    stop_line = "using your own resting order(s)" if skip_bracket else f"stop {stop:g} / target {target:g}"
    await notifier.send(
        f"🤝 <b>BIGSWING adopted your manual {direction.upper()} {market.label}</b> "
        f"({coin})\n"
        f"Entry {entry:g} · {leverage}x · margin ${pos.margin_used:.2f}\n{stop_line}\n"
        f"{bracket_note}\n"
        f"Now protected by the overnight de-risk rule and the hard equity "
        f"safety net ({config.BIGSWING_EQUITY_STOP_PCT*100:.0f}%, baseline "
        f"${equity_baseline:,.2f} — this is the equity AT ADOPTION time, not "
        f"your true original entry equity, since I wasn't managing this "
        f"position before now).")
    print(f"[bigswing] ADOPTED manual {direction} {coin} @ {entry:g} "
          f"lev={leverage}x margin=${pos.margin_used:.2f}")
    # Use the position's own live open time if this were reconstructable; we
    # don't have it, so start the max-hold clock from adoption time instead.
    asyncio.create_task(_manage(sid, coin, market.label, direction, entry, stop,
                                leverage, stream, equity_baseline))


# --- restart survival ----------------------------------------------------------
def _resume(stream: HLStream) -> int:
    """Re-arm the watcher for a real bigswing position that survived a
    restart. Scoped to horizon='bigswing' only — see journal.open_live_rows()
    for why this can't share watcher.resume_live()'s generic path (it has no
    concept of the equity-safety-net baseline)."""
    global _open
    with journal._conn() as c:  # noqa: SLF001
        rows = c.execute(
            "SELECT id, coin, direction, leverage, entry, ts, stop FROM signals "
            "WHERE horizon='bigswing' AND executed=1 AND dry_run=0 "
            "AND (exit_reason IS NULL OR exit_reason='')").fetchall()
    n = 0
    for sid, coin, direction, lev, entry, ts, stop in rows:
        label = config.MARKET_BY_COIN[coin].label if coin in config.MARKET_BY_COIN else coin
        live_pos = account_monitor.get(coin) if account_monitor.has_polled() else None
        if live_pos:
            entry = live_pos.entry or entry
            lev = live_pos.leverage or lev
        if not stop:
            stop = (entry * (1 - config.BIGSWING_STOP_RAW) if direction == "long"
                   else entry * (1 + config.BIGSWING_STOP_RAW))
        # Best-effort baseline: the TRUE entry-time equity isn't recoverable
        # across a restart, so this re-snapshots at resume time — slightly
        # optimistic if the account was already down when the process
        # bounced, but still catches any further drawdown from here.
        equity_baseline = account_monitor.account_value() or 0.0
        _open = {"coin": coin, "sid": sid}
        asyncio.create_task(_manage(sid, coin, label, direction, entry, stop,
                                    lev, stream, equity_baseline, opened=ts))
        n += 1
    return n


async def run(stream: HLStream) -> None:
    if not config.BIGSWING_ENABLED:
        print("[bigswing] disabled (BIGSWING_ENABLED=0)")
        return
    if not config.WALLET_ADDRESS:
        print("[bigswing] disabled — WALLET_ADDRESS not set (needed to read "
              "live balance/positions)")
        return
    # Wait for account_monitor's first successful poll before resuming: a
    # resumed position's equity-safety-net baseline is snapshotted from
    # account_monitor.account_value(), which defaults to 0.0 pre-poll — that
    # would silently disable the safety net for the resumed trade forever.
    while not account_monitor.has_polled():
        await asyncio.sleep(1)
    resumed = _resume(stream)
    if resumed:
        print(f"[bigswing] resumed {resumed} open position(s) from the journal")
    print(f"[bigswing] tier active: full-balance sizing, "
          f"{config.BIGSWING_MIN_LEVERAGE}-{config.BIGSWING_MAX_LEVERAGE}x, "
          f"{'adopts' if config.BIGSWING_ADOPT_MANUAL else 'ignores'} manual entries, "
          f"{'DRY RUN' if executor.DRY_RUN else 'LIVE'}")
    while True:
        swing_signals.sample_book(stream)
        if not is_open():
            adopted = await _check_adopt(stream)
            if not adopted:
                await _try_enter(stream)
        await asyncio.sleep(config.BIGSWING_SAMPLE_S)
