"""Smart-money watchlist — confirmation layer, not a trigger.

Polls the positions of the beta-controlled SKILL wallets + the $11M whale found
in research/wallet_findings.md (method: leaderboard -> taker/concentration
fingerprint -> declustered timing test -> beta-vs-skill control). These are the
only wallets whose entry-conditioned forward returns beat the asset's own drift
net of friction — i.e. demonstrated skill, not beta or luck.

Read-only (public clearinghouseState, no keys). Exposes signal(coin, direction)
so the combiner can CONFIRM a news read when a skill wallet is positioned the
same way, or WARN when the whale sits opposite. Deliberately NOT a standalone
entry trigger and NOT a hard veto — a context/annotation layer we can watch
before ever letting it gate real money (edges are 0.2-0.6%/4h — real but thin).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp

import config

POLL_S = 60
_DEXES = ("", "xyz")

# (address, short label, the market(s) they showed validated skill on)
TRACKED = [
    ("0x45974824c1c4e4d797aa8d057a5499b46cdefe33", "skill-SKHY", "SKHY"),
    ("0xbbbdbbfa1f754aea323af6cc56153e0605e89227", "skill-BTC1", "BTC"),
    ("0xbafae6afa1f7b0001860f627354130c859031b76", "skill-BTC2", "BTC"),
    ("0xbe3f79ae0ab3294aaa3230c1155e912c05b6a55b", "skill-BTC3", "BTC"),
    ("0xdd0c5de50d72e5eaa96816e920e41ce89c4b8888", "skill-META", "META"),
    ("0x9e8b1e51c642f4c8b87c6ba11c53d516a218afc4", "whale",      "multi"),
]
_WHALE = "0x9e8b1e51c642f4c8b87c6ba11c53d516a218afc4"

# coin -> {label: signed_notional}; positive = long, negative = short
LIVE: dict[str, dict[str, float]] = {}
_polled = False


def has_polled() -> bool:
    return _polled


@dataclass
class Confirm:
    verdict: str        # "confirm" | "oppose"
    note: str
    whale: bool         # whether the whale is the one opposing/confirming


def signal(coin: str, direction: str) -> Confirm | None:
    """How the tracked skill wallets are positioned on `coin` relative to our
    intended `direction`. None if none of them hold it."""
    holders = LIVE.get(coin)
    if not holders:
        return None
    want = 1 if direction == "long" else -1
    same = [lbl for lbl, ntl in holders.items() if (ntl > 0) == (want > 0)]
    opp = [lbl for lbl, ntl in holders.items() if (ntl > 0) != (want > 0)]
    whale_opp = "whale" in opp
    whale_same = "whale" in same
    if same and not opp:
        return Confirm("confirm",
                       f"🐋 smart-money CONFIRMS: {', '.join(same)} also {direction} {coin}",
                       whale_same)
    if opp and not same:
        return Confirm("oppose",
                       f"⚠️ smart-money OPPOSES: {', '.join(opp)} are the other way on {coin}",
                       whale_opp)
    # mixed
    return Confirm("oppose" if whale_opp else "confirm",
                   f"🐋 smart-money split on {coin}: same={same or '—'} opp={opp or '—'}",
                   whale_opp or whale_same)


def weight(coin: str, direction: str) -> tuple[float, str]:
    """Bounded conviction delta (+confirm / -oppose) from how the tracked
    wallets are positioned on `coin`, plus a human note. Summed across wallets
    (the whale counts heaviest) then clamped to [MAX_DOWN, MAX_UP] so it can
    only ever tip a borderline decision, not dominate it. (0.0, '') if none of
    them hold the coin."""
    holders = LIVE.get(coin)
    if not holders:
        return 0.0, ""
    # NB (2026-07-17 forensics): considered a "staleness decay" that ignored
    # wallets whose book hadn't changed in >5d — REJECTED after checking the
    # whale: it holds a $11M SPCX short at +$2.55M unrealized, unchanged for
    # 9 days. That's CONVICTION, not staleness, and it exactly confirms our
    # own SPCX short. A held position IS a live commitment of capital; an
    # actually-disengaged wallet has no position and contributes nothing
    # here anyway. So current holdings, undecayed, are the right signal.
    want = 1 if direction == "long" else -1
    same = [l for l, n in holders.items() if (n > 0) == (want > 0)]
    opp = [l for l, n in holders.items() if (n > 0) != (want > 0)]
    delta = 0.0
    for l in same:
        delta += config.SMARTMONEY_CONFIRM_WHALE if l == "whale" else config.SMARTMONEY_CONFIRM_SKILL
    for l in opp:
        delta += config.SMARTMONEY_OPPOSE_WHALE if l == "whale" else config.SMARTMONEY_OPPOSE_SKILL
    delta = max(config.SMARTMONEY_MAX_DOWN, min(config.SMARTMONEY_MAX_UP, round(delta, 3)))
    if delta > 0:
        note = f"🐋 smart-money confirms ({'+'.join(same)}, conv {delta:+.2f})"
    elif delta < 0:
        note = f"⚠️ smart-money opposes ({'+'.join(opp)}, conv {delta:+.2f})"
    else:
        note = f"🐋 smart-money mixed on {coin} (net 0)"
    return delta, note


async def _fetch(session: aiohttp.ClientSession, addr: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for dex in _DEXES:
        body = {"type": "clearinghouseState", "user": addr}
        if dex:
            body["dex"] = dex
        try:
            async with session.post(config.HL_INFO_URL, json=body,
                                    timeout=aiohttp.ClientTimeout(total=15)) as r:
                data = await r.json()
        except Exception:  # noqa: BLE001
            continue
        for ap in data.get("assetPositions", []):
            q = ap.get("position") or {}
            szi = float(q.get("szi", 0) or 0)
            if szi == 0:
                continue
            out[q["coin"]] = szi * float(q.get("entryPx", 0) or 0)  # signed notional
    return out


async def run() -> None:
    global _polled
    print(f"[smartmoney] tracking {len(TRACKED)} wallets (5 skill + whale) every {POLL_S}s")
    async with aiohttp.ClientSession() as session:
        while True:
            snapshot: dict[str, dict[str, float]] = {}
            for addr, label, _ in TRACKED:
                for coin, ntl in (await _fetch(session, addr)).items():
                    snapshot.setdefault(coin, {})[label] = ntl
                await asyncio.sleep(0.2)   # gentle on the API
            LIVE.clear()
            LIVE.update(snapshot)
            _polled = True
            await asyncio.sleep(POLL_S)


if __name__ == "__main__":
    async def _smoke() -> None:
        async with aiohttp.ClientSession() as s:
            for addr, label, mkt in TRACKED:
                pos = await _fetch(s, addr)
                held = {c.replace("xyz:", ""): f"${v:,.0f}" for c, v in pos.items()}
                print(f"{label:11s} {addr[:10]}… holds: {held or 'flat'}")
    asyncio.run(_smoke())
