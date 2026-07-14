"""Fast tier: derive tape/order-flow signals from the live tick state.

Pure, synchronous, microsecond-cheap. Produces a TapeSignal that the combiner
uses to *confirm* (or veto) a news-driven idea. None of this calls Claude.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import config
from hl_stream import MarketState


@dataclass
class TapeSignal:
    coin: str
    # net taker flow over the window, signed: +buys -sells (in base units)
    net_flow: float
    flow_ratio: float          # buys / (buys+sells), 0..1
    book_imbalance: float      # bids / (bids+asks), 0..1
    liquidation_burst: float   # size of liquidations in window
    trade_count: int
    bias: str                  # "long", "short", or "flat"
    strength: float            # 0..1 conviction of the tape

    def confirms(self, direction: str) -> bool:
        """Does the tape agree with a proposed 'long'/'short' idea?"""
        if self.bias == "flat":
            return False
        return self.bias == direction


def analyze(st: MarketState, window_s: float = config.TAPE_WINDOW_SECONDS) -> TapeSignal:
    trades = st.recent_trades(window_s)
    buys = sum(t.sz for t in trades if t.side == "B")
    sells = sum(t.sz for t in trades if t.side == "A")
    liqs = sum(t.sz for t in trades if t.liquidation)
    total = buys + sells
    flow_ratio = buys / total if total > 0 else 0.5
    net_flow = buys - sells
    imb = st.book_imbalance()

    # Combine flow and resting-book imbalance into a single directional score.
    # flow is the aggressor signal; imbalance is the passive/support signal.
    flow_score = (flow_ratio - 0.5) * 2      # -1..1
    book_score = (imb - 0.5) * 2             # -1..1
    combined = 0.6 * flow_score + 0.4 * book_score

    strength = min(abs(combined), 1.0)
    if strength < 0.2 or total <= 0:
        bias = "flat"
    elif combined > 0:
        bias = "long"
    else:
        bias = "short"

    return TapeSignal(
        coin=st.coin,
        net_flow=net_flow,
        flow_ratio=flow_ratio,
        book_imbalance=imb,
        liquidation_burst=liqs,
        trade_count=len(trades),
        bias=bias,
        strength=round(strength, 3),
    )


def format_line(sig: TapeSignal) -> str:
    return (
        f"{sig.coin:14s} bias={sig.bias:5s} str={sig.strength:.2f} "
        f"flow={sig.flow_ratio:.2f} book={sig.book_imbalance:.2f} "
        f"liq={sig.liquidation_burst:.2f} n={sig.trade_count}"
    )


# --- manual smoke test -------------------------------------------------------
if __name__ == "__main__":
    import asyncio
    import contextlib

    from hl_stream import HLStream

    async def _smoke() -> None:
        coins = [m.coin for m in config.MARKETS]
        stream = HLStream(coins)
        task = asyncio.create_task(stream.run())
        print("collecting 20s of tape...")
        await asyncio.sleep(20)
        stream.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        print("\n=== tape signals ===")
        for st in stream.state.values():
            print(format_line(analyze(st)))

    asyncio.run(_smoke())
