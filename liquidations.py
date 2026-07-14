"""Parser + rolling aggregator for the @HyperliquidLiquidations Telegram feed.

Messages there are structured, high-frequency liquidation prints, e.g.:
    "🔴 #FARTCOIN Liquidated Long: $438K at $0.1367 - hyperlens"

This is tick-level flow data, not narrative news — sending each one through
Claude would be slow and pointless (nothing to "interpret"). Instead we parse
it directly and maintain a rolling $-notional pressure per coin, which the
combiner surfaces as extra context on every alert and uses to fire an instant
cascade heads-up on watched markets (no Claude round-trip).
"""
from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass

import config

_PATTERN = re.compile(
    r"#([\w:]+)\s+Liquidated\s+(Long|Short):\s*\$([\d,\.]+)\s*([KMB]?)\s+at\s+\$?([\d,\.]+)",
    re.IGNORECASE,
)
_MULT = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}

WINDOW_S = 300           # rolling window for pressure aggregation
CASCADE_ALERT_USD = 150_000   # single or clustered notional that triggers a heads-up
CASCADE_COOLDOWN_S = 180

_events: dict[str, deque[tuple[float, str, float]]] = {}   # coin -> (ts, side, usd)
_last_cascade_alert: dict[str, float] = {}


@dataclass
class LiqEvent:
    coin: str
    side: str        # "Long" or "Short" (the side that got liquidated)
    size_usd: float
    price: float


def parse(text: str) -> LiqEvent | None:
    m = _PATTERN.search(text)
    if not m:
        return None
    coin, side, num, suffix, price = m.groups()
    size = float(num.replace(",", "")) * _MULT.get(suffix.upper(), 1)
    return LiqEvent(coin=coin, side=side, size_usd=size,
                    price=float(price.replace(",", "")))


def record(ev: LiqEvent) -> None:
    dq = _events.setdefault(ev.coin, deque())
    dq.append((time.time(), ev.side, ev.size_usd))
    cutoff = time.time() - WINDOW_S
    while dq and dq[0][0] < cutoff:
        dq.popleft()


def pressure(coin: str) -> dict:
    """Rolling $ liquidated per side for `coin` over the last WINDOW_S."""
    dq = _events.get(coin, deque())
    cutoff = time.time() - WINDOW_S
    long_usd = sum(u for ts, side, u in dq if ts >= cutoff and side == "Long")
    short_usd = sum(u for ts, side, u in dq if ts >= cutoff and side == "Short")
    return {
        "long_usd": long_usd,
        "short_usd": short_usd,
        "net_usd": short_usd - long_usd,   # +: shorts getting squeezed, -: longs flushed
        "total_usd": long_usd + short_usd,
    }


def pressure_note(coin: str) -> str:
    p = pressure(coin)
    if p["total_usd"] <= 0:
        return "no recent liquidations"
    return (f"liqs {WINDOW_S//60}m: ${p['long_usd']:,.0f} longs / "
            f"${p['short_usd']:,.0f} shorts flushed")


def cascade_alert_text(ev: LiqEvent) -> str | None:
    """Return heads-up text if this event pushes a watched coin's pressure over
    threshold, else None. Applies its own cooldown per coin."""
    if ev.coin not in config.MARKET_BY_COIN:
        return None
    p = pressure(ev.coin)
    if p["total_usd"] < CASCADE_ALERT_USD:
        return None
    last = _last_cascade_alert.get(ev.coin, 0.0)
    if time.time() - last < CASCADE_COOLDOWN_S:
        return None
    _last_cascade_alert[ev.coin] = time.time()
    label = config.MARKET_BY_COIN[ev.coin].label
    dominant = "LONGS" if p["long_usd"] > p["short_usd"] else "SHORTS"
    return (
        f"🔥 <b>Liquidation cascade — {label}</b> ({ev.coin})\n"
        f"${p['total_usd']:,.0f} liquidated in the last {WINDOW_S//60}m, "
        f"mostly {dominant}.\n"
        f"Last print: {ev.side} ${ev.size_usd:,.0f} @ {ev.price:,.4g}\n"
        f"<i>Flow/context signal, not a directional call by itself.</i>"
    )


if __name__ == "__main__":
    samples = [
        "🔴 #FARTCOIN Liquidated Long: $438K at $0.1367 - hyperlens",
        "🔴 #BTC Liquidated Long: $250K at $61,900 - hyperlens",
        "🟢 #BTC Liquidated Short: $50K at $62,100 - hyperlens",
        "🔴 #xyz:NVDA Liquidated Long: $180K at $203.50 - hyperlens",
        "not a liquidation message at all",
    ]
    for s in samples:
        ev = parse(s)
        print(f"{s[:55]:55s} -> {ev}")
        if ev:
            record(ev)
    print("\nBTC pressure:", pressure("BTC"))
    print("BTC pressure note:", pressure_note("BTC"))
    ev = parse(samples[3])
    print("cascade check (xyz:NVDA, below threshold):", cascade_alert_text(ev))
    # push NVDA over threshold
    record(LiqEvent("xyz:NVDA", "Long", 40_000, 203.0))
    print("cascade check (xyz:NVDA, now over threshold):", cascade_alert_text(ev))
