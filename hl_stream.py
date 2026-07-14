"""Hyperliquid websocket tick engine.

Subscribes to trades, l2Book and bbo for each watched market and maintains a
lightweight in-memory view of the tape and top-of-book. Everything here is pure
code (no network to Claude) so it runs at full tick speed.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import websockets

import config


@dataclass
class Trade:
    ts: float
    px: float
    sz: float
    side: str          # "B" (buy/taker long) or "A" (sell/taker short)
    liquidation: bool


@dataclass
class MarketState:
    coin: str
    best_bid: float = 0.0
    best_ask: float = 0.0
    bid_depth: float = 0.0        # summed size of top N bid levels
    ask_depth: float = 0.0        # summed size of top N ask levels
    trades: deque[Trade] = field(default_factory=lambda: deque(maxlen=2000))
    last_update: float = 0.0

    @property
    def mid(self) -> float:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return self.best_bid or self.best_ask

    def book_imbalance(self) -> float:
        """Fraction of top-of-book depth resting on the bid side (0..1)."""
        total = self.bid_depth + self.ask_depth
        if total <= 0:
            return 0.5
        return self.bid_depth / total

    def recent_trades(self, window_s: float) -> list[Trade]:
        cutoff = time.time() - window_s
        return [t for t in self.trades if t.ts >= cutoff]


class HLStream:
    """Manages one websocket connection with auto-reconnect."""

    BOOK_LEVELS = 10  # how many levels to sum for depth imbalance

    def __init__(self, coins: list[str], on_trade: Callable[[str, Trade], None] | None = None):
        self.coins = coins
        self.state: dict[str, MarketState] = {c: MarketState(c) for c in coins}
        self.on_trade = on_trade
        self._stop = asyncio.Event()

    async def run(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    config.HL_WS_URL, ping_interval=20, ping_timeout=20, max_size=None
                ) as ws:
                    await self._subscribe(ws)
                    backoff = 1
                    async for raw in ws:
                        self._handle(raw)
            except Exception as e:  # noqa: BLE001 - reconnect on any error
                if self._stop.is_set():
                    break
                print(f"[hl] ws error: {e!r}; reconnecting in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _subscribe(self, ws) -> None:
        for coin in self.coins:
            for sub in (
                {"type": "trades", "coin": coin},
                {"type": "l2Book", "coin": coin},
                {"type": "bbo", "coin": coin},
            ):
                await ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
        print(f"[hl] subscribed trades/l2Book/bbo for {len(self.coins)} markets")

    def _handle(self, raw: str) -> None:
        msg = json.loads(raw)
        channel = msg.get("channel")
        data = msg.get("data")
        if channel == "trades":
            self._on_trades(data)
        elif channel == "l2Book":
            self._on_book(data)
        elif channel == "bbo":
            self._on_bbo(data)

    def _on_trades(self, data) -> None:
        for t in data or []:
            coin = t.get("coin")
            st = self.state.get(coin)
            if st is None:
                continue
            tr = Trade(
                ts=t.get("time", time.time() * 1000) / 1000.0,
                px=float(t["px"]),
                sz=float(t["sz"]),
                side=t.get("side", "B"),
                liquidation=bool(t.get("liquidation", False)),
            )
            st.trades.append(tr)
            st.last_update = time.time()
            if self.on_trade:
                self.on_trade(coin, tr)

    def _on_book(self, data) -> None:
        coin = data.get("coin")
        st = self.state.get(coin)
        if st is None:
            return
        levels = data.get("levels") or [[], []]
        bids, asks = levels[0], levels[1]
        if bids:
            st.best_bid = float(bids[0]["px"])
            st.bid_depth = sum(float(l["sz"]) for l in bids[: self.BOOK_LEVELS])
        if asks:
            st.best_ask = float(asks[0]["px"])
            st.ask_depth = sum(float(l["sz"]) for l in asks[: self.BOOK_LEVELS])
        st.last_update = time.time()

    def _on_bbo(self, data) -> None:
        coin = data.get("coin")
        st = self.state.get(coin)
        if st is None:
            return
        bbo = data.get("bbo") or [None, None]
        if bbo[0]:
            st.best_bid = float(bbo[0]["px"])
        if bbo[1]:
            st.best_ask = float(bbo[1]["px"])
        st.last_update = time.time()

    def stop(self) -> None:
        self._stop.set()


# --- manual smoke test -------------------------------------------------------
async def _smoke() -> None:
    coins = [m.coin for m in config.MARKETS]
    stream = HLStream(coins)
    task = asyncio.create_task(stream.run())
    print("listening 15s for live ticks...")
    await asyncio.sleep(15)
    stream.stop()
    with contextlib.suppress(asyncio.CancelledError):
        task.cancel()
        await task
    print("\n=== snapshot ===")
    for coin, st in stream.state.items():
        n = len(st.trades)
        print(f"{coin:16s} mid={st.mid:>12.4f}  bid_depth={st.bid_depth:>10.3f} "
              f"ask_depth={st.ask_depth:>10.3f}  imbalance={st.book_imbalance():.2f}  trades={n}")


if __name__ == "__main__":
    asyncio.run(_smoke())
