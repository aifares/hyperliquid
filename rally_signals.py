"""News + trend arming gate + tick-level orderbook confirmation for rally.

Standalone from scalp/swing (news-led, tape-checked once at signal time) and
from bigswing (tech-led, 15-min sampled book). Roles:

  - News (catalyst): combiner.latest_read() — ARMS a coin when fresh +
    confident enough. Never entered on news alone.
  - Trend (gate): candles.trend_slope() on the coin AND a broad-market
    proxy (config.RALLY_BROAD_MARKET). Strongly opposing trend vetoes the arm.
  - Orderbook (timing): hl_stream on_book tick — entry only fires when the
    live book + short-window flow confirm the armed direction INSTANTLY.

All book confirmation stays pure in-memory / sync — never block the WS loop.
Arm registry mutations are lock-guarded because arm_if_eligible may run in
asyncio.to_thread while on_book_tick runs on the WS event-loop thread.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import candles
import combiner
import config
from hl_stream import MarketState

# coin -> ArmedSetup currently waiting for book confirm
_armed: dict[str, "ArmedSetup"] = {}
_lock = threading.Lock()


@dataclass
class ArmedSetup:
    coin: str
    direction: str          # "long" / "short"
    armed_ts: float
    expiry_ts: float
    news_conf: float
    trend_pct: float | None
    broad_trend_pct: float | None
    arm_id: int | None = None   # journal.rally_arms row id
    note: str = ""


@dataclass
class RallyConfirm:
    """Book just confirmed an armed setup — rally.py should enter."""
    coin: str
    direction: str
    imbalance: float
    flow_ratio: float
    news_conf: float
    trend_pct: float | None
    broad_trend_pct: float | None
    arm_id: int | None
    note: str


def armed() -> dict[str, ArmedSetup]:
    with _lock:
        return dict(_armed)


def disarm(coin: str) -> ArmedSetup | None:
    with _lock:
        return _armed.pop(coin, None)


def set_arm_id(coin: str, arm_id: int) -> None:
    """Attach the journal row id after log_rally_arm — under the lock so a
    concurrent on_book_tick sees it."""
    with _lock:
        setup = _armed.get(coin)
        if setup is not None:
            setup.arm_id = arm_id


def _expire_stale(now: float | None = None) -> list[ArmedSetup]:
    now = now or time.time()
    with _lock:
        expired = [a for a in _armed.values() if now >= a.expiry_ts]
        for a in expired:
            _armed.pop(a.coin, None)
    return expired


def _trend_ok(direction: str, trend_pct: float | None) -> bool:
    """True unless the slope strongly OPPOSES the proposed direction."""
    if trend_pct is None:
        return True   # missing data = don't veto (news can still arm)
    veto = config.RALLY_TREND_VETO_PCT
    if direction == "long" and trend_pct <= -veto:
        return False
    if direction == "short" and trend_pct >= veto:
        return False
    return True


def arm_if_eligible(coin: str) -> ArmedSetup | None:
    """Check news + trend for `coin`. Returns a new ArmedSetup if eligible
    (and registers it), None otherwise. Idempotent while already armed —
    returns the existing setup without re-arming."""
    _expire_stale()
    with _lock:
        if coin in _armed:
            return _armed[coin]
    if coin not in {m.coin for m in config.RALLY_MARKETS}:
        return None

    read = combiner.latest_read(coin)
    if read is None:
        return None
    age_s = time.time() - read["ts"]
    if age_s > config.RALLY_ARM_WINDOW_S:
        return None
    if read["confidence"] < config.RALLY_NEWS_MIN_CONF:
        return None
    direction = read["direction"]
    if direction not in ("long", "short"):
        return None

    # Network-capable (candles refresh) — caller should use to_thread.
    trend_pct = candles.trend_slope(coin, config.RALLY_TREND_DAYS)
    if not _trend_ok(direction, trend_pct):
        return None

    broad_pct = candles.trend_slope(
        config.RALLY_BROAD_MARKET, config.RALLY_TREND_DAYS)
    if config.RALLY_BROAD_MARKET_VETO and not _trend_ok(direction, broad_pct):
        return None

    # Remaining arm window shrinks with age of the news read — don't give a
    # 25-min-old headline a fresh 30-min book-wait.
    remaining = max(5.0, config.RALLY_ARM_WINDOW_S - age_s)
    now = time.time()
    note = (
        f"news conf {read['confidence']:.2f} age {age_s:.0f}s"
        + (f" / trend {trend_pct:+.1f}%" if trend_pct is not None else " / trend n/a")
        + (f" / broad {broad_pct:+.1f}%" if broad_pct is not None else " / broad n/a")
    )
    setup = ArmedSetup(
        coin=coin, direction=direction, armed_ts=now,
        expiry_ts=now + remaining, news_conf=read["confidence"],
        trend_pct=trend_pct, broad_trend_pct=broad_pct, note=note,
    )
    with _lock:
        # Lost the race to another arm — keep the winner
        if coin in _armed:
            return _armed[coin]
        _armed[coin] = setup
    return setup


def _flow_ratio(st: MarketState) -> float | None:
    trades = st.recent_trades(config.RALLY_FLOW_WINDOW_S)
    if len(trades) < 2:
        return None
    buys = sum(t.sz for t in trades if t.side == "B")
    sells = sum(t.sz for t in trades if t.side == "A")
    total = buys + sells
    if total <= 0:
        return None
    return buys / total


def on_book_tick(coin: str, st: MarketState) -> RallyConfirm | None:
    """Called on EVERY l2Book push. Returns RallyConfirm the instant an
    armed coin's book + short-window flow agree with its armed direction;
    otherwise None. Disarms on confirm so we don't double-fire."""
    with _lock:
        setup = _armed.get(coin)
        if setup is None:
            return None
        now = time.time()
        if now >= setup.expiry_ts:
            _armed.pop(coin, None)
            return None
        # Snapshot fields under the lock; confirm/disarm after checks
        direction = setup.direction
        news_conf = setup.news_conf
        trend_pct = setup.trend_pct
        broad_trend_pct = setup.broad_trend_pct
        arm_id = setup.arm_id
        note = setup.note

    imb = st.book_imbalance()
    flow = _flow_ratio(st)
    thresh = config.RALLY_BOOK_IMBALANCE
    flow_min = config.RALLY_FLOW_MIN_RATIO

    if direction == "long":
        book_ok = imb >= thresh
        flow_ok = flow is not None and flow >= flow_min
    else:
        book_ok = imb <= (1.0 - thresh)
        flow_ok = flow is not None and flow <= (1.0 - flow_min)

    if not (book_ok and flow_ok):
        return None

    with _lock:
        # Re-check still armed as this direction (another path might have
        # expired / confirmed it while we computed).
        cur = _armed.get(coin)
        if cur is None or cur.direction != direction:
            return None
        _armed.pop(coin, None)
        arm_id = cur.arm_id   # may have been set after our snapshot

    return RallyConfirm(
        coin=coin, direction=direction, imbalance=imb,
        flow_ratio=flow if flow is not None else 0.5,
        news_conf=news_conf, trend_pct=trend_pct,
        broad_trend_pct=broad_trend_pct, arm_id=arm_id, note=note,
    )
