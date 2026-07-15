"""Orderbook/technical signal scorer for the bigswing tier.

Pure-code (no LLM calls), the PRIMARY trigger for bigswing — unlike the news
notifier's scalp/swing tiers, which lead with Claude's news read and use tape
as confirmation, bigswing leads with technical/orderbook structure and treats
news as secondary confirmation (see bigswing.py). Combines:

  - multi-day trend + Donchian breakout (candles.py)      -> primary vote
  - SUSTAINED order-book imbalance, sampled over minutes    -> confirm/dampen
    (NOT the 30s scalp tape window in tape.py — a multi-day swing needs a
    persistent lean, not a tick)
  - liquidation pressure (liquidations.py)                  -> context boost,
    same-direction only

Funding rate is surfaced as CONTEXT ONLY, never a directional input —
backtests/RESULTS.md's crypto sweep found funding-extreme fade dead on every
coin tested.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

import config
import liquidations
import trend
import candles
from hl_stream import HLStream

# --- sustained book imbalance (sampled, not instantaneous) --------------------
_IMB_WINDOW_S = 900.0     # 15 min rolling window
_IMB_SAMPLE_S = 30.0      # sample cadence
_imbalance_hist: dict[str, deque[tuple[float, float]]] = {}   # coin -> (ts, imb 0..1)
_last_sample: dict[str, float] = {}


def sample_book(stream: HLStream) -> None:
    """Call periodically (bigswing.run()'s loop) to build the sustained-
    imbalance history this module needs. Cheap: pure in-memory read of
    hl_stream's already-live state, no extra network calls."""
    now = time.time()
    for coin, st in stream.state.items():
        if now - _last_sample.get(coin, 0.0) < _IMB_SAMPLE_S:
            continue
        _last_sample[coin] = now
        dq = _imbalance_hist.setdefault(coin, deque())
        dq.append((now, st.book_imbalance()))
        cutoff = now - _IMB_WINDOW_S
        while dq and dq[0][0] < cutoff:
            dq.popleft()


def sustained_imbalance(coin: str) -> float | None:
    """Average book imbalance (0..1, >0.5 = bid-heavy) over the sample
    window, or None if not enough samples have accumulated yet."""
    dq = _imbalance_hist.get(coin)
    if not dq or len(dq) < 3:
        return None
    return sum(v for _, v in dq) / len(dq)


@dataclass
class SwingSignal:
    coin: str
    direction: str          # "long" / "short" / "none"
    conviction: float        # 0..1
    trend_pct: float | None
    breakout: str            # "up" / "down" / "none"
    imbalance: float | None
    liq_note: str
    funding_note: str


def _breakout(coin: str) -> str:
    n = config.BIGSWING_BREAKOUT_DAYS
    dc = candles.donchian(coin, n)
    cs = candles.get(coin)
    if dc is None or not cs:
        return "none"
    hi, lo = dc
    last = cs[-1].c
    if last >= hi:
        return "up"
    if last <= lo:
        return "down"
    return "none"


def _funding_note(coin: str) -> str:
    fr = trend.funding_rate(coin)
    if fr is None:
        return "funding: n/a"
    apr = fr * 24 * 365 * 100
    return f"funding {fr * 100:+.4f}%/hr (~{apr:+.0f}%/yr APR, context only)"


def evaluate(coin: str) -> SwingSignal:
    """Score one coin. Never raises — missing data just drops that vote."""
    trend_pct = candles.trend_slope(coin, config.BIGSWING_TREND_DAYS)
    breakout = _breakout(coin)
    imb = sustained_imbalance(coin)
    liq = liquidations.pressure(coin)
    liq_note = liquidations.pressure_note(coin)
    funding_note = _funding_note(coin)

    votes: list[tuple[str, float]] = []   # (direction, weight)
    if trend_pct is not None:
        strength = min(abs(trend_pct) / config.BIGSWING_TREND_STRONG_PCT, 1.0)
        votes.append(("long" if trend_pct > 0 else "short", 0.45 * strength))
    if breakout != "none":
        votes.append(("long" if breakout == "up" else "short", 0.35))
    if imb is not None:
        imb_strength = min(abs(imb - 0.5) * 2, 1.0)
        votes.append(("long" if imb > 0.5 else "short", 0.20 * imb_strength))

    if not votes:
        return SwingSignal(coin, "none", 0.0, trend_pct, breakout, imb,
                           liq_note, funding_note)

    long_w = sum(w for d, w in votes if d == "long")
    short_w = sum(w for d, w in votes if d == "short")
    direction = "long" if long_w >= short_w else "short"
    conviction = max(long_w, short_w)

    # Liquidation cascades add conviction only when they align with the
    # direction already chosen above — context confirmation, never a
    # standalone trigger by itself.
    if liq["total_usd"] > 0:
        squeeze_dir = "long" if liq["net_usd"] > 0 else "short"
        if squeeze_dir == direction:
            conviction = min(1.0, conviction + 0.1)

    if long_w == 0.0 and short_w == 0.0:
        direction, conviction = "none", 0.0

    return SwingSignal(
        coin=coin, direction=direction, conviction=round(min(conviction, 1.0), 3),
        trend_pct=trend_pct, breakout=breakout, imbalance=imb,
        liq_note=liq_note, funding_note=funding_note,
    )


if __name__ == "__main__":
    for c in ("xyz:NVDA", "xyz:TSLA", "BTC"):
        print(evaluate(c))
