"""Claude analyzer tier.

Event-driven (NOT tick-by-tick): each incoming NewsEvent is classified by Claude
into a directional trade idea on one of the watched markets. Uses Haiku 4.5 for
cheap, fast triage. Output is a strict JSON schema enforced via tool use.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from anthropic import AsyncAnthropic

import config
from events import NewsEvent

MODEL = "claude-haiku-4-5-20251001"   # fast/cheap triage tier

_MARKET_LIST = "\n".join(
    f"- {m.coin}: {m.label} (max {m.max_leverage}x)" for m in config.MARKETS
)

_SYSTEM = (
    "You are a trading-desk news analyst for a leveraged perp notifier on "
    "Hyperliquid. You receive a single news item and decide whether it is a "
    "tradable, market-moving catalyst for exactly one of these markets:\n"
    f"{_MARKET_LIST}\n\n"
    "Rules:\n"
    "- Only act on genuinely new, price-moving catalysts. Ignore recaps, "
    "opinion, old news, and vague commentary.\n"
    "- direction 'long' if the news is bullish for the asset, 'short' if "
    "bearish, 'none' if not actionable or not about a watched market.\n"
    "- magnitude: expected size of the move (0=negligible, 1=huge/CPI-surprise).\n"
    "- confidence: how sure you are this is real and correctly interpreted.\n"
    "- horizon: 'scalp' for immediate catalysts that reprice within minutes-hours "
    "(breaking macro, geopolitical shocks, surprise announcements); 'swing' for "
    "slower theses that play out over days (guidance changes, analyst cycles, "
    "product launches, regulatory processes).\n"
    "- Be conservative: when unsure, use direction 'none'."
)

# Tool schema forces well-formed structured output.
_TOOL = {
    "name": "emit_signal",
    "description": "Emit the structured trade signal for this news item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "coin": {
                "type": "string",
                "enum": [m.coin for m in config.MARKETS] + ["NONE"],
                "description": "Which watched market this affects, or NONE.",
            },
            "direction": {"type": "string", "enum": ["long", "short", "none"]},
            "magnitude": {"type": "number", "minimum": 0, "maximum": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "horizon": {"type": "string", "enum": ["scalp", "swing"],
                        "description": "scalp: reprices in minutes-hours; swing: days."},
            "rationale": {"type": "string", "description": "One concise sentence."},
        },
        "required": ["coin", "direction", "magnitude", "confidence", "horizon",
                     "rationale"],
    },
}


@dataclass
class NewsSignal:
    event: NewsEvent
    coin: str
    direction: str      # long / short / none
    magnitude: float
    confidence: float
    rationale: str
    horizon: str = "scalp"   # scalp (minutes-hours) or swing (days)

    @property
    def actionable(self) -> bool:
        if self.direction not in ("long", "short") or self.coin not in config.MARKET_BY_COIN:
            return False
        if self.horizon == "swing":
            # Swing rides a multi-day thesis: demand more conviction from the
            # news itself since instantaneous tape matters less.
            return self.confidence >= 0.6 and self.magnitude >= 0.4
        return self.confidence >= 0.5 and self.magnitude >= 0.3


class Analyzer:
    def __init__(self) -> None:
        self.client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)

    async def analyze(self, ev: NewsEvent) -> NewsSignal:
        out: dict = {}
        # A dropped headline is a lost signal — retry through short network blips.
        for attempt in (1, 2, 3):
            try:
                resp = await self.client.messages.create(
                    model=MODEL,
                    max_tokens=400,
                    system=_SYSTEM,
                    tools=[_TOOL],
                    tool_choice={"type": "tool", "name": "emit_signal"},
                    messages=[{"role": "user",
                               "content": f"News item from {ev.source}:\n\n{ev.text}"}],
                )
                out = _extract_tool_input(resp)
                break
            except Exception as e:  # noqa: BLE001
                print(f"[analyzer] error (attempt {attempt}/3): {e!r}")
                if attempt < 3:
                    await asyncio.sleep(3 * attempt)
        coin = out.get("coin", "NONE")
        direction = out.get("direction", "none")
        if coin == "NONE":
            direction = "none"
        return NewsSignal(
            event=ev,
            coin=coin,
            direction=direction,
            magnitude=float(out.get("magnitude", 0.0)),
            confidence=float(out.get("confidence", 0.0)),
            rationale=str(out.get("rationale", "")),
            horizon=out.get("horizon", "scalp"),
        )


def _extract_tool_input(resp) -> dict:
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    return {}


# --- offline test (no API): validate schema/dataclass wiring -----------------
if __name__ == "__main__":
    async def _test() -> None:
        if not config.ANTHROPIC_API_KEY:
            print("No ANTHROPIC_API_KEY set — running schema self-check only.")
            print("tool enum coins:", _TOOL["input_schema"]["properties"]["coin"]["enum"])
            sig = NewsSignal(
                event=NewsEvent("test", "x"), coin="BTC", direction="long",
                magnitude=0.7, confidence=0.8, rationale="test",
            )
            print("actionable sample:", sig.actionable)
            return
        analyzer = Analyzer()
        samples = [
            "BREAKING: US CPI comes in hot at 4.1% vs 3.6% expected, Fed rate cut odds collapse",
            "NVIDIA announces record Q3 datacenter revenue, raises guidance well above estimates",
            "Analyst shares thoughts on why they like markets long term",
        ]
        for s in samples:
            sig = await analyzer.analyze(NewsEvent("test", s))
            print(f"\n{s[:60]}...")
            print(f"  -> {sig.coin} {sig.direction} mag={sig.magnitude} "
                  f"conf={sig.confidence} actionable={sig.actionable}")
            print(f"     {sig.rationale}")

    asyncio.run(_test())
