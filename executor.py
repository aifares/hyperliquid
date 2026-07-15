"""Semi-auto trade executor.

Confirmed alerts register a PendingTrade; tapping [Execute] in Telegram calls
execute(). With no HL_AGENT_PRIVATE_KEY set this runs in DRY-RUN mode: the full
flow works but no order leaves the machine.

Sizing comes from a shared TOTAL_BANKROLL: each trade is allotted a slot of
(bankroll / MAX_CONCURRENT_POSITIONS) scaled by the signal's confidence, capped
by whatever margin isn't already committed to open trades. Real mode places a
market entry at margin × leverage notional, plus reduce-only trigger orders for
the stop and the 2R target (server-side, so exits fire even if this process dies).

Guardrails enforced here, not in the UI:
  - kill switch: DAILY_LOSS_LIMIT stopped-out executed trades in a day -> halt
  - MAX_CONCURRENT_POSITIONS executed-and-unresolved trades
  - budget: committed margin across open trades never exceeds TOTAL_BANKROLL
  - stale buttons: pending trades expire after PENDING_TRADE_TTL_S
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp

import account_monitor
import config
import journal

_NY = ZoneInfo("America/New_York")

DRY_RUN = not bool(config.HL_AGENT_PRIVATE_KEY)

_pending: dict[str, "PendingTrade"] = {}
_sz_decimals: dict[str, int] = {}          # coin -> szDecimals from meta


@dataclass
class PendingTrade:
    id: str
    signal_id: int
    coin: str
    label: str
    direction: str          # long / short
    entry_ref: float        # price when alert fired
    stop: float
    target: float
    leverage: int
    horizon: str
    confidence: float       # analyzer confidence, scales the margin slot
    created: float

    @property
    def expired(self) -> bool:
        return time.time() - self.created > config.PENDING_TRADE_TTL_S


def register(*, signal_id: int, coin: str, label: str, direction: str,
             entry_ref: float, stop: float, target: float, leverage: int,
             horizon: str, confidence: float = 0.5) -> str:
    pid = uuid.uuid4().hex[:12]
    _pending[pid] = PendingTrade(
        id=pid, signal_id=signal_id, coin=coin, label=label, direction=direction,
        entry_ref=entry_ref, stop=stop, target=target, leverage=leverage,
        horizon=horizon, confidence=confidence, created=time.time(),
    )
    # opportunistic cleanup
    for k in [k for k, v in _pending.items() if v.expired]:
        _pending.pop(k, None)
    return pid


def get(pid: str) -> PendingTrade | None:
    return _pending.get(pid)


def discard(pid: str) -> None:
    _pending.pop(pid, None)


# --- guardrails ---------------------------------------------------------------
def _midnight_ny_ts() -> float:
    now = datetime.now(tz=_NY)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def losses_today() -> int:
    with journal._conn() as c:  # noqa: SLF001 - journal owns schema creation
        row = c.execute(
            "SELECT COUNT(*) FROM signals WHERE executed=1 AND exit_reason='STOP' "
            "AND exit_ts >= ?", (_midnight_ny_ts(),)).fetchone()
    return int(row[0])


def open_executed_positions() -> int:
    """News-tier (scalp/swing) open count — the run-up tier has its own cap,
    but every tier shares the same bankroll via margin_committed()."""
    with journal._conn() as c:  # noqa: SLF001
        row = c.execute(
            "SELECT COUNT(*) FROM signals WHERE executed=1 AND exit_reason IS NULL "
            "AND horizon != 'runup'"
        ).fetchone()
    return int(row[0])


def margin_committed(tier: str | None = None) -> float:
    """USD margin locked in open trades, optionally for one tier ('scalp' /
    'swing' / 'runup'). The journal tags which coin belongs to which tier
    (Hyperliquid has no concept of tiers) — but the $ amount comes from the
    LIVE position when one exists, since a manual resize makes the journal's
    stored `margin` stale immediately. Dry-run rows have no real position to
    check, so they fall back to the journal's own figure."""
    q = ("SELECT coin, margin, dry_run FROM signals "
         "WHERE executed=1 AND (exit_reason IS NULL OR exit_reason='')")
    args: tuple = ()
    if tier:
        q += " AND horizon = ?"
        args = (tier,)
    with journal._conn() as c:  # noqa: SLF001
        rows = c.execute(q, args).fetchall()
    total = 0.0
    for coin, margin, dry in rows:
        live = None if dry else account_monitor.get(coin)
        total += live.margin_used if live else margin
    return total


def open_positions(tier: str) -> int:
    with journal._conn() as c:  # noqa: SLF001
        return int(c.execute(
            "SELECT COUNT(*) FROM signals WHERE executed=1 "
            "AND (exit_reason IS NULL OR exit_reason='') AND horizon=?",
            (tier,)).fetchone()[0])


def bankroll() -> float:
    """Sizing ceiling: a plain hardcoded number the user edits in config.py
    directly whenever they add/remove funds — no compounding, no dependence
    on live equity (which now includes manual trades and would otherwise make
    every tier's budget swing with market noise and trades the bot didn't
    decide). Live equity is still shown accurately elsewhere (dashboard,
    account_monitor's own alerts) — just not folded into this ceiling."""
    return config.TOTAL_BANKROLL


def coin_direction_conflict(coin: str, direction: str) -> str | None:
    """One position per coin, full stop. Hyperliquid NETS every same-coin
    fill into a single on-chain position regardless of tier, direction, or
    who opened it (the bot, or a manual trade) — so a second entry doesn't
    make a second position, it resizes the one that's already there. Checked
    purely against the LIVE exchange position, not the journal: a manual
    trade the bot never logged is just as much a conflict as one it opened
    itself, and a manually-flipped position is caught here too since we're
    reading its actual current side, not a stale stored direction."""
    live = account_monitor.get(coin)
    if live:
        return (f"🔒 already holding {live.side} {abs(live.size):g} {coin} "
                f"({live.leverage}x live) — one position per coin (the "
                f"exchange nets them into one anyway).")
    return None


def bigswing_active() -> bool:
    """True if the full-balance bigswing tier currently holds an open
    position. Checked directly against the journal (not bigswing.py's
    in-memory state) to avoid a circular import — bigswing.py imports this
    module, so this module must not import bigswing.py back."""
    if not config.BIGSWING_PAUSE_OTHER_TIERS:
        return False
    with journal._conn() as c:  # noqa: SLF001
        row = c.execute(
            "SELECT 1 FROM signals WHERE horizon='bigswing' AND executed=1 "
            "AND (exit_reason IS NULL OR exit_reason='') LIMIT 1").fetchone()
    return row is not None


def guardrail_block(tier: str = "scalp") -> str | None:
    """Return a human reason if trading is currently blocked, else None.
    Kill switch and real-margin check are global; concurrency and budget are
    per tier."""
    if losses_today() >= config.DAILY_LOSS_LIMIT:
        return (f"🛑 KILL SWITCH: {config.DAILY_LOSS_LIMIT} stopped-out trades "
                f"today. Halted until midnight ET.")
    # bigswing claims ~the whole account for its single open position — the
    # scalp/swing/runup tiers must not also place real orders against the
    # same wallet while it's holding one, or they'd be fighting over margin
    # bigswing already committed.
    if tier != "bigswing" and bigswing_active():
        return ("⛔ bigswing holds an open full-balance position — other "
                "tiers stay paused so they don't compete for the same real "
                "margin.")
    # The bot's own tier-budget math can say "plenty of room" while the real
    # exchange account has nothing free (e.g. a manual trade parked margin
    # the bot's own tracking never sees) — check REAL spot balance before
    # even considering tier budget, so it refuses cleanly instead of
    # attempting an order that the exchange would just reject anyway. Only
    # meaningful for real orders — a paper (DRY_RUN) fill never touches the
    # actual account, so this doesn't apply there.
    if not DRY_RUN and account_monitor.has_polled():
        free = account_monitor.spot_available()
        if free < config.MIN_MARGIN_PER_TRADE:
            return (f"💸 Only ${free:.2f} actually free on the exchange "
                    f"(below the ${config.MIN_MARGIN_PER_TRADE:.0f} minimum) — "
                    f"account fully deployed, not attempting a trade.")
    cap = config.TIER_MAX_CONCURRENT.get(tier, 2)
    if open_positions(tier) >= cap:
        return (f"⛔ {cap} {tier} positions already open — "
                f"close one before adding risk.")
    share = bankroll() * config.TIER_BUDGET_FRAC.get(tier, 0.2)
    committed = margin_committed(tier)
    if share - committed < config.MIN_MARGIN_PER_TRADE:
        return (f"💸 {tier} budget spent: ${committed:.2f} of ${share:.2f} "
                f"share — wait for a {tier} position to close.")
    return None


# --- sizing --------------------------------------------------------------------
def allocate_margin(confidence: float, tier: str = "scalp") -> float:
    """Slot from the tier's bankroll share: base = share / tier cap, scaled
    0.8x–1.2x by analyzer confidence, capped by what the tier hasn't already
    committed. Tiers that make money grow their own share (bankroll compounds)."""
    share = bankroll() * config.TIER_BUDGET_FRAC.get(tier, 0.2)
    base = share / config.TIER_MAX_CONCURRENT.get(tier, 2)
    mult = min(1.2, max(0.8, 0.8 + 0.8 * (confidence - 0.5)))
    available = share - margin_committed(tier)
    return round(max(0.0, min(base * mult, available)), 2)
async def _load_sz_decimals() -> None:
    if _sz_decimals:
        return
    async with aiohttp.ClientSession() as s:
        for dex in ("", "xyz"):
            body = {"type": "meta"}
            if dex:
                body["dex"] = dex
            async with s.post(config.HL_INFO_URL, json=body,
                              timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
            for a in data.get("universe", []):
                _sz_decimals[a["name"]] = int(a.get("szDecimals", 2))


def round_px(coin: str, px: float) -> float:
    """Hyperliquid px rules: ≤5 significant figures AND ≤(6 - szDecimals)
    decimals for perps — violating either silently rejects the order."""
    dec = max(0, 6 - _sz_decimals.get(coin, 2))
    return round(float(f"{px:.5g}"), dec)


def position_size(coin: str, price: float, leverage: int, margin: float) -> float:
    """Size so margin used ≈ `margin` USD at the given leverage."""
    notional = margin * leverage
    raw = notional / price
    dec = _sz_decimals.get(coin, 2)
    return round(raw, dec) if dec > 0 else float(int(raw))


# --- execution -------------------------------------------------------------------
async def execute(pid: str) -> str:
    """Called when the user taps [Execute]. Returns a status line for Telegram."""
    pt = _pending.get(pid)
    if pt is None:
        return "⚠️ Unknown or already-handled trade."
    if pt.expired:
        discard(pid)
        return "⌛ Expired — market has moved on since this alert (15 min TTL)."
    block = guardrail_block(pt.horizon)
    if block:
        return block

    margin = allocate_margin(pt.confidence, pt.horizon)
    if margin < config.MIN_MARGIN_PER_TRADE:
        return (f"💸 Only ${margin:.2f} of the ${bankroll():.2f} bankroll "
                f"left — below the ${config.MIN_MARGIN_PER_TRADE:.0f} minimum. Skipping.")

    await _load_sz_decimals()
    sz = position_size(pt.coin, pt.entry_ref, pt.leverage, margin)
    if sz <= 0:
        return f"⚠️ Computed size is 0 for {pt.coin} — price too high for ${margin:.2f} margin."
    notional = sz * pt.entry_ref

    if DRY_RUN:
        discard(pid)
        journal.mark_executed(pt.signal_id, dry_run=True, margin=margin)
        bk = bankroll()
        left = bk - margin_committed()
        return (f"🧪 DRY RUN — would place: {pt.direction.upper()} {sz} {pt.coin} "
                f"(~${notional:,.0f} notional, ${margin:.2f} margin @ "
                f"{pt.leverage}x)\n+ stop @ {pt.stop:,.2f} + target @ {pt.target:,.2f} "
                f"(both reduce-only, server-side)\n"
                f"Bankroll: ${left:.2f} of ${bk:.2f} still free.\n"
                f"Add HL_AGENT_PRIVATE_KEY to go live.")

    try:
        result = await asyncio.to_thread(_place_bracket_sync, pt, sz, margin)
        discard(pid)
        journal.mark_executed(pt.signal_id, dry_run=False, margin=margin)
        return result
    except Exception as e:  # noqa: BLE001
        return f"❌ Order failed: {e!r}"


def _exchange():
    """Fresh SDK Exchange bound to the agent key, trading on the main account."""
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants

    wallet = Account.from_key(config.HL_AGENT_PRIVATE_KEY)
    # perp_dexs=["xyz"] ALONE excludes the default dex ("") from the SDK's
    # internal name_to_coin/coin_to_asset maps (see hyperliquid/info.py:
    # perp_dexs=None -> [""], but an explicit list is used AS GIVEN, no
    # implicit ""). BTC lives on the default dex, not xyz — omitting "" here
    # made every order/cancel/leverage call on BTC fail with KeyError('BTC').
    return Exchange(wallet, constants.MAINNET_API_URL,
                    account_address=config.WALLET_ADDRESS or None,
                    perp_dexs=["", "xyz"])


def _place_bracket_sync(pt: PendingTrade, sz: float, margin: float) -> str:
    """Blocking SDK calls: market entry + reduce-only stop & target triggers.
    coin_direction_conflict() already blocks entries on a coin that's already
    held, so update_leverage should normally succeed (nothing open yet) — a
    failure here means something opened in the race window between that check
    and this order. watcher's live reconciliation (every ~10s) will correct
    the stop/target off the position's REAL leverage regardless, so this is
    just logged for visibility rather than silently swallowed."""
    ex = _exchange()
    is_buy = pt.direction == "long"
    try:  # isolated margin caps the worst case at the allotted margin
        ex.update_leverage(pt.leverage, pt.coin, is_cross=False)
    except Exception as e:  # noqa: BLE001
        print(f"[executor] update_leverage({pt.leverage}x, {pt.coin}) failed "
              f"— a position likely opened in the gap since the conflict "
              f"check; watcher will reconcile the real leverage: {e!r}")

    entry = ex.market_open(pt.coin, is_buy, sz)
    if entry.get("status") != "ok":
        raise RuntimeError(f"entry rejected: {entry}")

    # Server-side exits (reduce-only triggers), opposite side of the entry.
    stop_px = round_px(pt.coin, pt.stop)
    tgt_px = round_px(pt.coin, pt.target)
    stop_o = ex.order(pt.coin, not is_buy, sz, stop_px,
                      {"trigger": {"triggerPx": stop_px, "isMarket": True, "tpsl": "sl"}},
                      reduce_only=True)
    tp_o = ex.order(pt.coin, not is_buy, sz, tgt_px,
                    {"trigger": {"triggerPx": tgt_px, "isMarket": True, "tpsl": "tp"}},
                    reduce_only=True)
    notes = []
    if stop_o.get("status") != "ok":
        notes.append(f"⚠️ stop order rejected: {stop_o}")
    if tp_o.get("status") != "ok":
        notes.append(f"⚠️ target order rejected: {tp_o}")
    tail = ("\n" + "\n".join(notes)) if notes else "\n✅ stop + target resting on exchange."
    return (f"✅ FILLED: {pt.direction.upper()} {sz} {pt.coin} "
            f"(${margin:.2f} margin @ {pt.leverage}x){tail}")


def place_runup_entry_sync(coin: str, sz: float, leverage: int, stop: float) -> str:
    """Live run-up entry: isolated-margin market long + a server-side reduce-only
    stop, so the -3% backstop holds even if this process dies mid-hold."""
    ex = _exchange()
    try:
        ex.update_leverage(leverage, coin, is_cross=False)
    except Exception:  # noqa: BLE001
        pass
    entry = ex.market_open(coin, True, sz)
    if entry.get("status") != "ok":
        raise RuntimeError(f"entry rejected: {entry}")
    stop_px = round_px(coin, stop)
    stop_o = ex.order(coin, False, sz, stop_px,
                      {"trigger": {"triggerPx": stop_px, "isMarket": True, "tpsl": "sl"}},
                      reduce_only=True)
    if stop_o.get("status") != "ok":
        return f"filled, but ⚠️ stop order rejected: {stop_o}"
    return "filled ✅ server-side stop resting."


def guardrail_block_bigswing() -> str | None:
    """Guardrails for the full-balance tier: the kill switch is shared, but
    there's no tier budget/concurrency cap here — the ONE-position-at-a-time
    rule and per-coin conflict check are enforced by bigswing.py itself
    (against the live exchange state) before this is ever reached."""
    if losses_today() >= config.DAILY_LOSS_LIMIT:
        return (f"🛑 KILL SWITCH: {config.DAILY_LOSS_LIMIT} stopped-out trades "
                f"today. Halted until midnight ET.")
    return None


async def execute_bigswing(*, coin: str, label: str, direction: str,
                           entry_ref: float, stop: float, target: float,
                           leverage: int, margin: float) -> tuple[str, int | None]:
    """Full-balance entry for the bigswing tier: same bracket mechanics as
    execute(), but `margin` is passed in directly (bigswing derives it from
    account_monitor.spot_available(), NOT allocate_margin's tier-fraction
    slot) since bigswing claims ~the whole account for its single open
    position. Returns (status message, signal_id) — signal_id is None if
    nothing was actually opened (blocked, zero size, or the order failed),
    so the caller knows not to treat the bigswing slot as occupied."""
    block = guardrail_block_bigswing()
    if block:
        return block, None

    await _load_sz_decimals()
    sz = position_size(coin, entry_ref, leverage, margin)
    if sz <= 0:
        return f"⚠️ Computed size is 0 for {coin} — margin too small for the price.", None
    notional = sz * entry_ref
    pt = PendingTrade(
        id="bigswing", signal_id=0, coin=coin, label=label, direction=direction,
        entry_ref=entry_ref, stop=stop, target=target, leverage=leverage,
        horizon="bigswing", confidence=1.0, created=time.time(),
    )

    if DRY_RUN:
        sid = journal.log_signal(
            coin=coin, direction=direction, confidence=1.0, magnitude=1.0,
            leverage=leverage, entry=entry_ref, headline="bigswing technical entry",
            rationale="full-balance swing entry", tape_note="", horizon="bigswing",
            stop=stop)
        journal.mark_executed(sid, dry_run=True, margin=margin)
        return (f"🧪 DRY RUN — would place: {direction.upper()} {sz} {coin} "
                f"(~${notional:,.0f} notional, ${margin:.2f} margin @ {leverage}x)\n"
                f"+ stop @ {stop:,.4g} + target @ {target:,.4g} (reduce-only, "
                f"server-side)"), sid

    try:
        result = await asyncio.to_thread(_place_bracket_sync, pt, sz, margin)
    except Exception as e:  # noqa: BLE001 — no order placed, nothing to journal
        return f"❌ Order failed: {e!r}", None

    sid = journal.log_signal(
        coin=coin, direction=direction, confidence=1.0, magnitude=1.0,
        leverage=leverage, entry=entry_ref, headline="bigswing technical entry",
        rationale="full-balance swing entry", tape_note="", horizon="bigswing",
        stop=stop)
    journal.mark_executed(sid, dry_run=False, margin=margin)
    return result, sid


def attach_bracket_sync(coin: str, direction: str, sz: float, stop: float,
                        target: float) -> str:
    """Place reduce-only stop+target brackets on a position that ALREADY
    exists on the exchange — bigswing's manual-adoption path. Mirrors the
    tail half of _place_bracket_sync but skips the entry order entirely."""
    ex = _exchange()
    is_buy = direction == "long"
    stop_px = round_px(coin, stop)
    tgt_px = round_px(coin, target)
    notes = []
    stop_o = ex.order(coin, not is_buy, sz, stop_px,
                      {"trigger": {"triggerPx": stop_px, "isMarket": True, "tpsl": "sl"}},
                      reduce_only=True)
    if stop_o.get("status") != "ok":
        notes.append(f"⚠️ stop order rejected: {stop_o}")
    tp_o = ex.order(coin, not is_buy, sz, tgt_px,
                    {"trigger": {"triggerPx": tgt_px, "isMarket": True, "tpsl": "tp"}},
                    reduce_only=True)
    if tp_o.get("status") != "ok":
        notes.append(f"⚠️ target order rejected: {tp_o}")
    return "; ".join(notes) if notes else "✅ stop + target attached to existing position."


def has_resting_stop(coin: str) -> bool:
    """True if the account already has a resting reduce-only trigger order on
    `coin` — used by bigswing's adoption path so it doesn't double-bracket a
    manual position where the user already set their own stop."""
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        info = Info(constants.MAINNET_API_URL, skip_ws=True, perp_dexs=["", "xyz"])
        orders = info.open_orders(config.WALLET_ADDRESS)
        return any(o.get("coin") == coin for o in orders)
    except Exception as e:  # noqa: BLE001 — assume none rather than block adoption
        print(f"[executor] has_resting_stop check failed for {coin}: {e!r}")
        return False


def close_position_sync(coin: str) -> str:
    """Live close: market-close the whole position and cancel resting orders
    (the run-up's server-side stop). Safe if the stop already fired."""
    ex = _exchange()
    notes = []
    try:
        res = ex.market_close(coin)
        if res is None:
            notes.append("no position on exchange (stop already filled?)")
        elif res.get("status") != "ok":
            notes.append(f"close rejected: {res}")
    except Exception as e:  # noqa: BLE001
        notes.append(f"close failed: {e!r}")
    try:
        from hyperliquid.info import Info
        from hyperliquid.utils import constants
        info = Info(constants.MAINNET_API_URL, skip_ws=True, perp_dexs=["", "xyz"])
        for o in info.open_orders(config.WALLET_ADDRESS):
            if o.get("coin") == coin:
                ex.cancel(coin, o["oid"])
    except Exception as e:  # noqa: BLE001
        notes.append(f"stop-cancel failed: {e!r}")
    return "; ".join(notes) if notes else "closed ✅ resting stop cancelled."


if __name__ == "__main__":
    async def _test() -> None:
        print("DRY_RUN:", DRY_RUN)
        await _load_sz_decimals()
        for conf in (0.5, 0.7, 0.9):
            print(f"conf {conf} -> margin ${allocate_margin(conf):.2f}")
        for coin, px in (("xyz:NVDA", 204.0), ("xyz:SP500", 7500.0), ("BTC", 62000.0)):
            for lev in (20, 5):
                m = allocate_margin(0.7)
                sz = position_size(coin, px, lev, m)
                print(f"{coin:12s} @{px:>9,.0f} {lev:>2d}x ${m:.2f} -> sz={sz} "
                      f"(${sz*px:,.0f} notional, szDec={_sz_decimals.get(coin)})")
        print("committed:", margin_committed())
        print("guardrails:", guardrail_block() or "clear")

    asyncio.run(_test())
