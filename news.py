"""News poller using the Perplexity Sonar API.

Periodically asks Perplexity for fresh, market-moving headlines about the watched
assets and pushes any *new* items onto the shared event queue. Perplexity does
real-time web search, so this catches macro/company news between Telegram posts.
"""
from __future__ import annotations

import asyncio
import json
import time

import aiohttp

import config
from events import NewsEvent

_SEEN: set[str] = set()          # dedup keys already emitted
_SEEN_ORDER: list[str] = []      # bounded FIFO for the dedup set

_ASSETS = ", ".join(m.label for m in config.MARKETS)

_PROMPT = (
    f"What are the latest market-moving news headlines for these assets: "
    f"{_ASSETS}? Focus on macro data, Fed, earnings, regulation, and large "
    f"price moves. Respond ONLY as a JSON array of objects with keys "
    f'"headline" and "asset". No prose, no preamble.'
)


_FILLER = ("not mentioned", "not cited", "not referenced", "not highlighted",
           "not directly mentioned", "no recent", "no major", "no significant",
           "not featured", "no specific")


def _is_placeholder(headline: str) -> bool:
    """Sonar emits filler instead of omitting quiet assets: 'No headlines found
    for X', 'Tesla not cited in current news', etc. Filler phrasing sits early
    in the sentence; real headlines can mention these words later."""
    low = headline.lower()
    if low.startswith("no ") and ("found" in low or "headlines" in low):
        return True
    return any(f in low[:70] for f in _FILLER)


def _remember(key: str) -> bool:
    """Return True if this key is new (and record it)."""
    if key in _SEEN:
        return False
    _SEEN.add(key)
    _SEEN_ORDER.append(key)
    if len(_SEEN_ORDER) > 500:
        old = _SEEN_ORDER.pop(0)
        _SEEN.discard(old)
    return True


async def _query(session: aiohttp.ClientSession) -> list[dict]:
    payload = {
        "model": "sonar",
        "messages": [{"role": "user", "content": _PROMPT}],
        "temperature": 0,
        "search_recency_filter": "hour",   # recency handled by the API, not the prompt
    }
    headers = {"Authorization": f"Bearer {config.PERPLEXITY_API_KEY}"}
    async with session.post(config.PERPLEXITY_URL, json=payload, headers=headers,
                            timeout=aiohttp.ClientTimeout(total=45)) as r:
        r.raise_for_status()
        data = await r.json()
    content = data["choices"][0]["message"]["content"].strip()
    # Sonar sometimes wraps JSON in ```; strip fences.
    if content.startswith("```"):
        content = content.strip("`")
        content = content[content.find("["):]
    try:
        items = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    # Sonar usually returns [{"headline": ..., "asset": ...}] but sometimes a
    # plain list of strings — normalize both shapes to dicts.
    out: list[dict] = []
    for it in items:
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, str) and it.strip():
            out.append({"headline": it})
    return out


async def run(queue: "asyncio.Queue[NewsEvent]") -> None:
    if not config.PERPLEXITY_API_KEY:
        print("[news] no PERPLEXITY_API_KEY; poller disabled")
        return
    print(f"[news] Perplexity poller every {config.NEWS_POLL_SECONDS}s")
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                for item in await _query(session):
                    headline = str(item.get("headline", "")).strip()
                    if not headline or _is_placeholder(headline):
                        continue
                    ev = NewsEvent(source="perplexity", text=headline)
                    if _remember(ev.key()):
                        await queue.put(ev)
            except Exception as e:  # noqa: BLE001
                print(f"[news] poll error: {e!r}")
            await asyncio.sleep(config.NEWS_POLL_SECONDS)


# --- one-shot test -----------------------------------------------------------
if __name__ == "__main__":
    async def _once() -> None:
        async with aiohttp.ClientSession() as s:
            t0 = time.time()
            items = await _query(s)
            print(f"got {len(items)} items in {time.time()-t0:.1f}s")
            for it in items:
                print(" -", it)

    asyncio.run(_once())
