"""News + orderbook + trend rally tier — decision engine.

Standalone fractional-budget tier (shares TOTAL_BANKROLL with scalp/swing/
runup; NOT full-balance like bigswing):

  1. Periodically arm eligible coins via rally_signals.arm_if_eligible
     (fresh Claude news + per-asset/broad-market trend gate).
  2. Tick-by-tick: HLStream.on_book → rally_signals.on_book_tick → when the
     live book confirms the armed direction, enter immediately.
  3. Size/stop/target/watcher via the normal executor / watcher path
     (horizon='rally').

Every arm and its outcome (confirmed / expired / blocked) is journaled in
rally_arms so post-hoc validation works without historical L2 data.
"""
from __future__ import annotations

import asyncio
import time

import account_monitor
import config
import executor
import journal
import notifier
import rally_signals
import watcher
from hl_stream import HLStream, MarketState
from rally_signals import RallyConfirm

# Confirmations land here from the sync on_book callback (same event loop).
_confirm_q: asyncio.Queue[RallyConfirm] | None = None
# Coins currently being entered / watched by this tier (coin -> signal_id)
_open: dict[str, int] = {}
# Dedup: don't re-log an arm we already journaled this window
_logged_arms: set[str] = set()


def on_book(coin: str, st: MarketState) -> None:
    """Wired into HLStream — must stay sync and cheap."""
    conf = rally_signals.on_book_tick(coin, st)
    if conf is None or _confirm_q is None:
        return
    try:
        _confirm_q.put_nowait(conf)
    except asyncio.QueueFull:
        pass


async def _enter(conf: RallyConfirm, stream: HLStream) -> None:
    if conf.coin in _open:
        return
    if account_monitor.has_polled() and account_monitor.get(conf.coin) is not None:
        if conf.arm_id:
            journal.update_rally_arm(conf.arm_id, outcome="confirmed_blocked",
                                     imbalance=conf.imbalance,
                                     flow_ratio=conf.flow_ratio)
        print(f"[rally] confirmed {conf.direction} {conf.coin} but already holding")
        return

    market = config.MARKET_BY_COIN.get(conf.coin)
    if market is None:
        return
    st = stream.state.get(conf.coin)
    if st is None or st.mid <= 0:
        return

    # Guardrails first so a blocked confirm still lands in rally_arms with a
    # useful outcome (bigswing exclusive / budget / kill switch, etc.).
    block = executor.guardrail_block("rally")
    conflict = executor.coin_direction_conflict(conf.coin, conf.direction)
    if block or conflict:
        reason = block or conflict
        if conf.arm_id:
            journal.update_rally_arm(conf.arm_id, outcome="confirmed_blocked",
                                     imbalance=conf.imbalance,
                                     flow_ratio=conf.flow_ratio)
        print(f"[rally] confirmed {conf.direction} {conf.coin} but blocked: {reason}")
        return

    entry = st.mid
    leverage = min(config.RALLY_LEVERAGE, market.max_leverage)
    stop = notifier.stop_price(entry, conf.direction, "rally")
    target = watcher.target_price(entry, stop, conf.direction, config.RALLY_TARGET_R)
    tape_note = (f"book imb={conf.imbalance:.2f} flow={conf.flow_ratio:.2f} "
                 f"| {conf.note}")

    sid = journal.log_signal(
        coin=conf.coin, direction=conf.direction, confidence=conf.news_conf,
        magnitude=1.0, leverage=leverage, entry=entry,
        headline="rally news+book confirm",
        rationale="news catalyst + trend gate + tick orderbook confirm",
        tape_note=tape_note, horizon="rally", stop=stop, target=target,
        conviction=conf.news_conf, trend_pct=conf.trend_pct,
        imbalance=conf.imbalance, news_note=conf.note,
    )
    pid = executor.register(
        signal_id=sid, coin=conf.coin, label=market.label,
        direction=conf.direction, entry_ref=entry, stop=stop, target=target,
        leverage=leverage, horizon="rally", confidence=conf.news_conf,
    )
    result = await executor.execute(pid)
    # execute() already marked the journal row executed (or failed without);
    # only treat as open if the row is now executed.
    with journal._conn() as c:  # noqa: SLF001
        row = c.execute("SELECT executed FROM signals WHERE id=?", (sid,)).fetchone()
    if not row or not row[0]:
        if conf.arm_id:
            journal.update_rally_arm(conf.arm_id, outcome="confirmed_blocked",
                                     imbalance=conf.imbalance,
                                     flow_ratio=conf.flow_ratio, signal_id=sid)
        print(f"[rally] entry failed for {conf.coin}: {result}")
        return

    if conf.arm_id:
        journal.update_rally_arm(conf.arm_id, outcome="confirmed",
                                 imbalance=conf.imbalance,
                                 flow_ratio=conf.flow_ratio, signal_id=sid)
    _open[conf.coin] = sid
    _logged_arms.discard(conf.coin)
    mode = "DRY RUN" if executor.DRY_RUN else "LIVE"
    await notifier.send(
        f"🚀💰 <b>RALLY {mode} — {conf.direction.upper()} {market.label}</b> "
        f"({conf.coin})\n"
        f"News conf {conf.news_conf:.2f} · book {conf.imbalance:.2f} · "
        f"flow {conf.flow_ratio:.2f}\n"
        f"Entry ~{entry:g} | stop {stop:g} | target {target:g} · {leverage}x\n"
        f"{conf.note}\n{result}")
    print(f"[rally] ENTER {conf.direction} {conf.coin} @ {entry:g} "
          f"conf={conf.news_conf:.2f} imb={conf.imbalance:.2f}")
    asyncio.create_task(_manage(sid, conf.coin, market.label, conf.direction,
                                entry, stop, leverage, stream))


async def _manage(sid: int, coin: str, label: str, direction: str, entry: float,
                  stop: float, leverage: int, stream: HLStream) -> None:
    try:
        await watcher.watch(
            signal_id=sid, coin=coin, label=label, direction=direction,
            entry=entry, stop=stop, horizon="rally", leverage=leverage,
            stream=stream,
        )
    finally:
        _open.pop(coin, None)


async def _sweep_arm(stream: HLStream) -> None:
    """Periodic: try to arm every rally market off latest news+trend."""
    for market in config.RALLY_MARKETS:
        # trend_slope can hit network on cold cache — off the event loop
        setup = await asyncio.to_thread(rally_signals.arm_if_eligible, market.coin)
        if setup is None:
            continue
        if setup.arm_id is None and market.coin not in _logged_arms:
            arm_id = journal.log_rally_arm(
                coin=setup.coin, direction=setup.direction,
                news_conf=setup.news_conf, trend_pct=setup.trend_pct,
                broad_trend_pct=setup.broad_trend_pct, note=setup.note)
            rally_signals.set_arm_id(market.coin, arm_id)
            setup.arm_id = arm_id
            _logged_arms.add(market.coin)
            print(f"[rally] ARMED {setup.direction} {setup.coin} "
                  f"({setup.note}) — waiting for book confirm")
        # Instant check against current book (don't wait for next WS push)
        st = stream.state.get(market.coin)
        if st is not None and _confirm_q is not None:
            conf = rally_signals.on_book_tick(market.coin, st)
            if conf is not None:
                try:
                    _confirm_q.put_nowait(conf)
                except asyncio.QueueFull:
                    pass


async def _expire_loop() -> None:
    """Mark expired arms in the journal so arm→outcome stats stay complete."""
    while True:
        expired = rally_signals._expire_stale()  # noqa: SLF001
        for a in expired:
            _logged_arms.discard(a.coin)
            if a.arm_id:
                journal.update_rally_arm(a.arm_id, outcome="expired")
                print(f"[rally] EXPIRED {a.direction} {a.coin} (no book confirm)")
        await asyncio.sleep(5)


async def run(stream: HLStream) -> None:
    global _confirm_q
    if not config.RALLY_ENABLED:
        print("[rally] disabled (RALLY_ENABLED=0)")
        return
    _confirm_q = asyncio.Queue(maxsize=64)
    # Resume any live rally rows via the shared watcher.resume_live path
    # (open_live_rows already includes horizon='rally').
    print(f"[rally] tier active: news+trend arm → tick-book confirm, "
          f"{config.RALLY_LEVERAGE}x, stop {config.RALLY_STOP_RAW*100:.1f}%, "
          f"{config.TIER_BUDGET_FRAC.get('rally', 0)*100:.0f}% bankroll share, "
          f"{'DRY RUN' if executor.DRY_RUN else 'LIVE'}")
    asyncio.create_task(_expire_loop(), name="rally-expire")

    while True:
        await _sweep_arm(stream)
        # Drain any pending confirms without blocking the arm sweep forever
        deadline = time.time() + config.RALLY_SAMPLE_S
        while time.time() < deadline:
            timeout = max(0.05, deadline - time.time())
            try:
                conf = await asyncio.wait_for(_confirm_q.get(), timeout=timeout)
            except asyncio.TimeoutError:
                break
            await _enter(conf, stream)
