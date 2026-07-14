"""Earnings run-up tier (Frazzini-Lamont announcement premium).

Backtest (backtests/RESULTS.md): long 10 trading days before each report,
exit before the print — +1.59%/event net of costs, 62% win over 10y, persists
in the last 3y. At 5x: +10.6% EV on margin with ~1% liquidation risk. TSLA
excluded (negative expectancy).

Mechanics here:
  - entry: any time from T-10 trading days until 2 trading days before the
    exit (late entry captures the remaining drift)
  - exit:  15:45 ET on the last session before the print (amc -> report day,
    bmo -> the prior trading day), or a hard stop at -3% raw (-15% margin)
  - sizing: 25% of the current bankroll per event, own concurrency cap so an
    earnings cluster can't drain the news tiers' slots
  - dry-run: positions auto-execute and are journaled like every other tier;
    they SURVIVE bot restarts (resumed from the journal, and exempted from
    orphan cleanup) because a two-week hold must outlive process bounces.
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import account_monitor
import config
import earnings
import executor
import journal
import notifier
from hl_stream import HLStream

NY = ZoneInfo("America/New_York")
POLL_S = 60
_active: set[str] = set()        # "coin:report_date" currently held or done
_veto: dict[str, dict] = {}      # coin -> pending bearish-news early-exit flag


def _open_runup_coins() -> set[str]:
    with journal._conn() as c:  # noqa: SLF001
        return {r[0] for r in c.execute(
            "SELECT coin FROM signals WHERE horizon='runup' AND executed=1 "
            "AND (exit_reason IS NULL OR exit_reason='')").fetchall()}


def flag_bearish(coin: str, direction: str, confidence: float,
                 magnitude: float, headline: str) -> None:
    """Called from the news path for every actionable signal. Arms an early
    exit on a HELD run-up only when a bearish read clears both conviction bars
    — routine chop must not knock us out of a run-up that's meant to ride
    through pre-earnings dips."""
    if not config.RUNUP_NEWS_EXIT or direction != "short":
        return
    if confidence < config.RUNUP_NEWS_MIN_CONF or magnitude < config.RUNUP_NEWS_MIN_MAG:
        return
    if coin not in _open_runup_coins():
        return
    _veto[coin] = {"conf": confidence, "mag": magnitude,
                   "headline": headline, "ts": time.time()}
    print(f"[runup] 📰 NEWS-VETO armed for {coin} "
          f"(conf {confidence:.2f}, mag {magnitude:.2f}) — {headline[:80]!r}")


def _prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _minus_trading_days(d: date, n: int) -> date:
    while n > 0:
        d = _prev_trading_day(d)
        n -= 1
    return d


def plan(coin: str) -> dict | None:
    """Entry/exit schedule for the coin's next report, or None."""
    e = earnings._next.get(coin)  # noqa: SLF001 — same-process calendar cache
    if not e:
        return None
    report = datetime.strptime(e["date"], "%Y-%m-%d").date()
    exit_day = report if e.get("hour") == "amc" else _prev_trading_day(report)
    entry_day = _minus_trading_days(exit_day, config.RUNUP_ENTRY_TDAYS)
    late_cutoff = _minus_trading_days(exit_day, 2)   # too late past this
    exit_ts = datetime.combine(exit_day, dtime(15, 45), tzinfo=NY).timestamp()
    return {"report": report.isoformat(), "entry_day": entry_day,
            "late_cutoff": late_cutoff, "exit_ts": exit_ts}


def _already_traded(coin: str, report: str) -> bool:
    """Ignore DISABLED closes — re-entry is allowed after the tier is re-enabled."""
    with journal._conn() as c:  # noqa: SLF001
        return c.execute(
            "SELECT 1 FROM signals WHERE horizon='runup' AND coin=? "
            "AND headline LIKE ? AND (exit_reason IS NULL OR exit_reason != 'DISABLED')",
            (coin, f"%{report}%")).fetchone() is not None


def _slot(coin: str) -> float:
    """Edge-weighted slot from the run-up bankroll share: names with a bigger
    backtested per-event mean get proportionally more margin (AMD's edge is
    3x INTC's — its slot should be too)."""
    share = executor.bankroll() * config.TIER_BUDGET_FRAC["runup"]
    base = share / 2                       # two full-weight slots per share
    weight = config.RUNUP_EDGE.get(coin, config.RUNUP_EDGE_MEAN) / config.RUNUP_EDGE_MEAN
    target = min(base * weight, base)      # cap at base; weight only shrinks
    available = share - executor.margin_committed("runup")
    return round(max(0.0, min(target, available)), 2)


async def _enter(coin: str, p: dict, stream: HLStream) -> None:
    st = stream.state.get(coin)
    if st is None or st.mid <= 0:
        return
    entry = st.mid
    slot = _slot(coin)
    if slot < config.MIN_MARGIN_PER_TRADE:
        print(f"[runup] {coin}: no budget (${slot:.2f} free) — skipping")
        _active.add(f"{coin}:{p['report']}")   # don't retry every minute
        return
    stop = entry * (1 - config.RUNUP_STOP_RAW)
    label = config.MARKET_BY_COIN[coin].label
    late = date.today() > p["entry_day"]

    live_note = ""
    if not executor.DRY_RUN:
        await executor._load_sz_decimals()  # noqa: SLF001
        sz = executor.position_size(coin, entry, config.RUNUP_LEVERAGE, slot)
        if sz <= 0:
            print(f"[runup] {coin}: size 0 for ${slot:.2f} margin — skipping")
            _active.add(f"{coin}:{p['report']}")
            return
        try:
            live_note = "\n" + await asyncio.to_thread(
                executor.place_runup_entry_sync, coin, sz,
                config.RUNUP_LEVERAGE, stop)
        except Exception as e:  # noqa: BLE001 — no order, no journal entry
            await notifier.send(f"❌ RUN-UP order failed for {label}: {e!r}")
            print(f"[runup] {coin}: live entry FAILED: {e!r}")
            _active.add(f"{coin}:{p['report']}")   # don't hammer a broken order
            return

    sid = journal.log_signal(
        coin=coin, direction="long", confidence=0.7, magnitude=0.5,
        leverage=config.RUNUP_LEVERAGE, entry=entry,
        headline=f"RUNUP into {p['report']} print",
        rationale="Earnings announcement premium (backtested +1.59%/event net)",
        tape_note=f"calendar entry{' (late)' if late else ''}; "
                  f"exit before print @ {p['report']}",
        horizon="runup",
    )
    journal.mark_executed(sid, dry_run=executor.DRY_RUN, margin=slot)
    _active.add(f"{coin}:{p['report']}")
    days_left = max(0, int((p['exit_ts'] - time.time()) / 86400))
    await notifier.send(
        f"📈 <b>EARNINGS RUN-UP — LONG {label}</b> ({coin})\n"
        f"<b>Entry:</b> {entry:g}  ·  {config.RUNUP_LEVERAGE}x  ·  "
        f"${slot:.2f} margin{' · late entry' if late else ''}\n"
        f"<b>Stop:</b> {stop:g} (-{config.RUNUP_STOP_RAW*100:.0f}% raw / "
        f"-{config.RUNUP_STOP_RAW*config.RUNUP_LEVERAGE*100:.0f}% margin)\n"
        f"<b>Exit:</b> before the {p['report']} print (~{days_left}d)\n"
        f"<i>Announcement-premium tier — backtested, "
        f"{'DRY RUN' if executor.DRY_RUN else 'LIVE'}.</i>{live_note}")
    print(f"[runup] ENTER long {coin} @ {entry:g} (${slot:.2f} @ "
          f"{config.RUNUP_LEVERAGE}x, exit {p['report']})")
    asyncio.create_task(_watch(sid, coin, label, entry, stop, p["exit_ts"], stream))


async def _watch(sid: int, coin: str, label: str, entry: float, stop: float,
                 exit_ts: float, stream: HLStream) -> None:
    lev = config.RUNUP_LEVERAGE
    is_live = not executor.DRY_RUN
    while True:
        await asyncio.sleep(30)
        st = stream.state.get(coin)
        mid = st.mid if st and st.mid > 0 else 0.0
        reason = emoji = note = None

        # Reconcile against the REAL position — a manual close/flip/resize on
        # a run-up coin would otherwise be invisible for the whole 10-day hold.
        if is_live and account_monitor.has_polled():
            live_pos = account_monitor.get(coin)
            if live_pos is None:
                reason, emoji, note = "EXTERNAL", "🔚", (
                    "Position no longer exists on the exchange (closed "
                    "manually, the stop fired, or liquidation) — stopping "
                    "the watch, nothing left here to manage.")
                mid = mid or entry
            elif live_pos.side != "long":  # run-up only ever goes long
                reason, emoji, note = "EXTERNAL_FLIP", "🔀", (
                    f"Position was flipped to {live_pos.side} outside the bot "
                    f"— the run-up thesis no longer applies. Stopping the "
                    f"watch rather than adopting the new side.")
                mid = live_pos.entry or mid or entry
            elif live_pos.entry > 0 and abs(live_pos.entry - entry) > 1e-9:
                print(f"[runup] {coin} resized live: entry {entry:g}->"
                      f"{live_pos.entry:g} — recomputing stop off the real entry")
                entry = live_pos.entry
                stop = entry * (1 - config.RUNUP_STOP_RAW)

        if reason is None:
            if mid <= 0:
                continue
            if mid <= stop:
                reason, emoji, note = "STOP", "🛑", "Run-up stop hit — thesis pauses, exit."
            elif time.time() >= exit_ts:
                reason, emoji, note = "PRINT", "📅", (
                    "Exiting before the earnings print — the run-up premium is "
                    "collected; holding through the print is a coin flip we don't take.")
            elif config.RUNUP_NEWS_EXIT and coin in _veto:
                v = _veto.pop(coin)
                reason, emoji, note = "NEWS", "📰", (
                    f"Bearish catalyst (conf {v['conf']:.2f}, mag {v['mag']:.2f}) — "
                    f"exiting the run-up early rather than waiting for the -3% stop: "
                    f"{v['headline'][:120]}")
            else:
                continue
        if is_live and reason not in ("EXTERNAL", "EXTERNAL_FLIP"):
            status = await asyncio.to_thread(executor.close_position_sync, coin)
            print(f"[runup] live close {coin}: {status}")
        pnl_pct = (mid - entry) / entry * 100
        await notifier.send(notifier.build_exit_alert(
            label=label, coin=coin, direction="long", entry=entry, exit_px=mid,
            pnl_pct=pnl_pct, reason=reason, emoji=emoji, note=note,
            horizon="runup", leverage=lev))
        journal.record_exit(sid, exit_px=mid, reason=reason)
        _veto.pop(coin, None)   # position gone; drop any stale veto flag
        print(f"[runup] EXIT {reason} {coin} @ {mid:g} ({pnl_pct:+.2f}% raw)")
        return


def _resume(stream: HLStream) -> int:
    """Re-arm watchers for run-up positions that survived a restart."""
    with journal._conn() as c:  # noqa: SLF001
        rows = c.execute(
            "SELECT id, coin, entry, headline FROM signals WHERE horizon='runup' "
            "AND executed=1 AND (exit_reason IS NULL OR exit_reason='')").fetchall()
    n = 0
    for sid, coin, entry, headline in rows:
        report = headline.split()[-2] if "print" in headline else None
        p = plan(coin)
        exit_ts = p["exit_ts"] if p else time.time() + 86400
        live_pos = account_monitor.get(coin) if account_monitor.has_polled() else None
        if live_pos and live_pos.entry > 0:
            entry = live_pos.entry
        stop = entry * (1 - config.RUNUP_STOP_RAW)
        label = config.MARKET_BY_COIN[coin].label if coin in config.MARKET_BY_COIN else coin
        _active.add(f"{coin}:{report or (p and p['report'])}")
        asyncio.create_task(_watch(sid, coin, label, entry, stop, exit_ts, stream))
        n += 1
    return n


async def run(stream: HLStream) -> None:
    # wait for the earnings calendar to populate
    while not earnings._next:  # noqa: SLF001
        await asyncio.sleep(10)
    resumed = _resume(stream)
    if resumed:
        print(f"[runup] resumed {resumed} open position(s) from the journal")
    print(f"[runup] tier active: {config.RUNUP_LEVERAGE}x, "
          f"T-{config.RUNUP_ENTRY_TDAYS} entries, "
          f"{config.TIER_BUDGET_FRAC['runup']*100:.0f}% bankroll share, "
          f"edge-weighted slots, excludes {sorted(config.RUNUP_EXCLUDE)}")
    while True:
        today = date.today()
        for coin in earnings._stock_symbols():
            if coin in config.RUNUP_EXCLUDE:
                continue
            p = plan(coin)
            if p is None:
                continue
            key = f"{coin}:{p['report']}"
            if key in _active or _already_traded(coin, p["report"]):
                continue
            in_window = p["entry_day"] <= today <= p["late_cutoff"]
            if in_window and time.time() < p["exit_ts"]:
                if executor.open_positions("runup") >= config.RUNUP_MAX_CONCURRENT:
                    continue   # retry next poll; a slot may free up
                await _enter(coin, p, stream)
        await asyncio.sleep(POLL_S)
