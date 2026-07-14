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
    """USD margin locked in executed trades that haven't exited yet,
    optionally for one tier ('scalp' / 'swing' / 'runup')."""
    q = ("SELECT COALESCE(SUM(margin), 0) FROM signals "
         "WHERE executed=1 AND (exit_reason IS NULL OR exit_reason='')")
    args: tuple = ()
    if tier:
        q += " AND horizon = ?"
        args = (tier,)
    with journal._conn() as c:  # noqa: SLF001
        row = c.execute(q, args).fetchone()
    return float(row[0])


def open_positions(tier: str) -> int:
    with journal._conn() as c:  # noqa: SLF001
        return int(c.execute(
            "SELECT COUNT(*) FROM signals WHERE executed=1 "
            "AND (exit_reason IS NULL OR exit_reason='') AND horizon=?",
            (tier,)).fetchone()[0])


def bankroll() -> float:
    """Working bankroll: starting budget compounded by realized PnL — wins are
    reinvested into bigger slots, losses shrink them."""
    return max(0.0, config.TOTAL_BANKROLL + journal.realized_pnl_usd())


def guardrail_block(tier: str = "scalp") -> str | None:
    """Return a human reason if trading is currently blocked, else None.
    Kill switch is global; concurrency and budget are per tier."""
    if losses_today() >= config.DAILY_LOSS_LIMIT:
        return (f"🛑 KILL SWITCH: {config.DAILY_LOSS_LIMIT} stopped-out trades "
                f"today. Halted until midnight ET.")
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


def _place_bracket_sync(pt: PendingTrade, sz: float, margin: float) -> str:
    """Blocking SDK calls: market entry + reduce-only stop & target triggers."""
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils import constants

    wallet = Account.from_key(config.HL_AGENT_PRIVATE_KEY)
    ex = Exchange(wallet, constants.MAINNET_API_URL,
                  account_address=config.WALLET_ADDRESS or None,
                  perp_dexs=["xyz"])
    is_buy = pt.direction == "long"

    entry = ex.market_open(pt.coin, is_buy, sz)
    if entry.get("status") != "ok":
        raise RuntimeError(f"entry rejected: {entry}")

    # Server-side exits (reduce-only triggers), opposite side of the entry.
    stop_o = ex.order(pt.coin, not is_buy, sz, pt.stop,
                      {"trigger": {"triggerPx": pt.stop, "isMarket": True, "tpsl": "sl"}},
                      reduce_only=True)
    tp_o = ex.order(pt.coin, not is_buy, sz, pt.target,
                    {"trigger": {"triggerPx": pt.target, "isMarket": True, "tpsl": "tp"}},
                    reduce_only=True)
    notes = []
    if stop_o.get("status") != "ok":
        notes.append(f"⚠️ stop order rejected: {stop_o}")
    if tp_o.get("status") != "ok":
        notes.append(f"⚠️ target order rejected: {tp_o}")
    tail = ("\n" + "\n".join(notes)) if notes else "\n✅ stop + target resting on exchange."
    return (f"✅ FILLED: {pt.direction.upper()} {sz} {pt.coin} "
            f"(${margin:.2f} margin @ {pt.leverage}x){tail}")


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
