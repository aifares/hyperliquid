"""Live account position tracker (read-only).

Polls Hyperliquid clearinghouseState for the configured wallet address —
both the core perps book and the xyz (HIP-3 stocks) book — and alerts on:
  - position opened / closed / flipped / resized
  - liquidation proximity (price within LIQ_WARN_PCT of liq price)

Needs only the PUBLIC wallet address. No keys, cannot trade or withdraw.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp

import config
import notifier

POLL_S = 10
LIQ_WARN_PCT = 2.0          # warn when price is within 2% of liquidation
_DEXES = ("", "xyz")        # core book + HIP-3 stocks book

# Shared live-state cache: the single read path for "what's actually open on
# the exchange right now" — executor/watcher/combiner read this synchronously
# instead of trusting the journal's write-once-at-open snapshot, which goes
# stale the moment a position is manually resized/flipped/closed outside the
# bot. Refreshed every POLL_S by run() below; zero extra network calls added
# anywhere else in the codebase.
LIVE: dict[str, "Position"] = {}
LIVE_ACCOUNT_VALUE = 0.0
SPOT_AVAILABLE = 0.0
_polled_once = False


@dataclass
class Position:
    coin: str
    size: float              # signed: + long, - short
    entry: float
    upnl: float
    liq: float
    margin_used: float
    leverage: int

    @property
    def side(self) -> str:
        return "long" if self.size > 0 else "short"


def get(coin: str) -> Position | None:
    """The live position on this coin, or None if nothing's open there."""
    return LIVE.get(coin)


def account_value() -> float:
    """Live exchange equity — for display/monitoring, NOT the sizing ceiling
    (that's config.TOTAL_BANKROLL, a hardcoded number the user edits directly)."""
    return LIVE_ACCOUNT_VALUE


def has_polled() -> bool:
    return _polled_once


def spot_available() -> float:
    """Real, uncommitted USDC free to back a NEW position — from the spot
    balance's own maintenance-adjusted figure (matches the app's "Available
    Balance" exactly), NOT the xyz dex's clearinghouse `withdrawable`, which
    reads as $0 the moment funds are parked in any open position there even
    though the account overall still has free capital sitting in spot."""
    return SPOT_AVAILABLE


async def _fetch_positions(session: aiohttp.ClientSession, wallet: str,
                           ) -> tuple[dict[str, Position], float]:
    out: dict[str, Position] = {}
    account_val = 0.0
    for dex in _DEXES:
        body = {"type": "clearinghouseState", "user": wallet}
        if dex:
            body["dex"] = dex
        async with session.post(config.HL_INFO_URL, json=body,
                                timeout=aiohttp.ClientTimeout(total=15)) as r:
            r.raise_for_status()
            data = await r.json()
        account_val += float(data.get("marginSummary", {}).get("accountValue", 0) or 0)
        for ap in data.get("assetPositions", []):
            p = ap.get("position") or {}
            szi = float(p.get("szi", 0) or 0)
            if szi == 0:
                continue
            coin = p["coin"]
            out[coin] = Position(
                coin=coin,
                size=szi,
                entry=float(p.get("entryPx", 0) or 0),
                upnl=float(p.get("unrealizedPnl", 0) or 0),
                liq=float(p.get("liquidationPx", 0) or 0),
                margin_used=float(p.get("marginUsed", 0) or 0),
                leverage=int((p.get("leverage") or {}).get("value", 0)),
            )
    return out, account_val


async def _fetch_spot_available(session: aiohttp.ClientSession, wallet: str) -> float:
    body = {"type": "spotClearinghouseState", "user": wallet}
    async with session.post(config.HL_INFO_URL, json=body,
                            timeout=aiohttp.ClientTimeout(total=15)) as r:
        r.raise_for_status()
        data = await r.json()
    for token_id, avail in data.get("tokenToAvailableAfterMaintenance", []):
        if token_id == 0:   # USDC is always token 0
            return float(avail)
    return 0.0


def _mark_price(coin: str, stream) -> float:
    if stream is not None:
        st = stream.state.get(coin)
        if st and st.mid > 0:
            return st.mid
    return 0.0


def _fmt(x: float) -> str:
    return f"{x:,.4f}".rstrip("0").rstrip(".") if abs(x) < 100 else f"{x:,.2f}"


async def run(wallet: str, stream=None) -> None:
    global LIVE_ACCOUNT_VALUE, SPOT_AVAILABLE, _polled_once
    if not wallet:
        print("[account] no WALLET_ADDRESS; monitor disabled")
        return
    print(f"[account] tracking {wallet[:8]}…{wallet[-4:]} every {POLL_S}s")
    prev: dict[str, Position] = {}
    warned_liq: set[str] = set()
    first = True

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                cur, acct_val = await _fetch_positions(session, wallet)
                spot_avail = await _fetch_spot_available(session, wallet)
            except Exception as e:  # noqa: BLE001
                print(f"[account] poll error: {e!r}")
                await asyncio.sleep(POLL_S)
                continue

            # Publish the snapshot atomically (clear+update, not reassign) so a
            # concurrent reader never sees a half-built dict mid-refresh.
            LIVE.clear()
            LIVE.update(cur)
            LIVE_ACCOUNT_VALUE = acct_val
            SPOT_AVAILABLE = spot_avail
            _polled_once = True

            if first:
                first = False
                if cur:
                    lines = ["📋 <b>Tracking your open positions:</b>"]
                    for p in cur.values():
                        lines.append(
                            f"• {p.side.upper()} {p.coin} {abs(p.size)} @ {_fmt(p.entry)} "
                            f"({p.leverage}x, uPnL {p.upnl:+,.2f}, liq {_fmt(p.liq)})")
                    await notifier.send("\n".join(lines))
                else:
                    await notifier.send("📋 Position monitor live — no open positions.")
            else:
                await _diff_alerts(prev, cur)

            await _liq_warnings(cur, warned_liq, stream)
            prev = cur
            await asyncio.sleep(POLL_S)


async def _diff_alerts(prev: dict[str, Position], cur: dict[str, Position]) -> None:
    for coin, p in cur.items():
        old = prev.get(coin)
        if old is None:
            await notifier.send(
                f"🆕 <b>Position opened:</b> {p.side.upper()} {p.coin} "
                f"{abs(p.size)} @ {_fmt(p.entry)} ({p.leverage}x)\n"
                f"Liq: {_fmt(p.liq)} | margin {p.margin_used:,.2f}")
        elif (old.size > 0) != (p.size > 0):
            await notifier.send(
                f"🔁 <b>Position flipped:</b> {p.coin} now {p.side.upper()} "
                f"{abs(p.size)} @ {_fmt(p.entry)}")
        elif abs(abs(p.size) - abs(old.size)) / abs(old.size) > 0.1:
            verb = "increased" if abs(p.size) > abs(old.size) else "reduced"
            await notifier.send(
                f"↕️ <b>Position {verb}:</b> {p.side.upper()} {p.coin} "
                f"{abs(old.size)} → {abs(p.size)} (uPnL {p.upnl:+,.2f})")
    for coin, old in prev.items():
        if coin not in cur:
            await notifier.send(
                f"✅ <b>Position closed:</b> {old.side.upper()} {coin} "
                f"(last uPnL {old.upnl:+,.2f})")


async def _liq_warnings(cur: dict[str, Position], warned: set[str], stream) -> None:
    for coin, p in cur.items():
        if p.liq <= 0:
            continue
        mark = _mark_price(coin, stream) or p.entry
        if mark <= 0:
            continue
        dist_pct = abs(mark - p.liq) / mark * 100
        if dist_pct <= LIQ_WARN_PCT and coin not in warned:
            warned.add(coin)
            await notifier.send(
                f"🚨 <b>LIQUIDATION WARNING — {p.side.upper()} {coin}</b>\n"
                f"Price {_fmt(mark)} is {dist_pct:.2f}% from liq {_fmt(p.liq)}.\n"
                f"uPnL {p.upnl:+,.2f}. Reduce size or add margin NOW.")
        elif dist_pct > LIQ_WARN_PCT * 2:
            warned.discard(coin)   # re-arm once price moves safely away


# --- read-only smoke test against a public whale address ---------------------
if __name__ == "__main__":
    async def _smoke() -> None:
        whale = "0x31ca8395cf837de08b24da3f660e77761dfb974b"
        async with aiohttp.ClientSession() as s:
            pos, acct_val = await _fetch_positions(s, whale)
        print(f"fetched {len(pos)} open positions for whale {whale[:10]}… "
              f"(accountValue ${acct_val:,.2f})")
        for p in list(pos.values())[:8]:
            print(f"  {p.side:5s} {p.coin:12s} sz={abs(p.size):<12g} entry={_fmt(p.entry):>12s} "
                  f"uPnL={p.upnl:>+12,.2f} liq={_fmt(p.liq)}")

    asyncio.run(_smoke())
