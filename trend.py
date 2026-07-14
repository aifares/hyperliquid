"""Daily-trend awareness for entry gating.

The tape module reads a 30-second window of trades — it literally cannot see
that a stock is up 6.7% on the day (it read MU as "flat" while a misread
headline shorted straight into that uptrend). This module answers the one
question the tape can't: how far has this coin moved TODAY? Sourced from
metaAndAssetCtxs (markPx vs prevDayPx), one request per dex covers every
coin, cached briefly so the combiner can gate every signal without adding a
network round-trip per headline.
"""
from __future__ import annotations

import json
import time
import urllib.request

import config

_TTL_S = 60.0
_cache: dict[str, float] = {}   # coin -> % change vs prev day close
_fetched_at = 0.0


def _refresh_sync() -> None:
    global _fetched_at
    out: dict[str, float] = {}
    for dex in ("", "xyz"):
        body: dict = {"type": "metaAndAssetCtxs"}
        if dex:
            body["dex"] = dex
        req = urllib.request.Request(
            config.HL_INFO_URL, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            meta, ctxs = json.load(r)
        for asset, ctx in zip(meta.get("universe", []), ctxs):
            prev = float(ctx.get("prevDayPx", 0) or 0)
            mark = float(ctx.get("markPx", 0) or 0)
            if prev > 0 and mark > 0:
                out[asset["name"]] = (mark - prev) / prev * 100
    _cache.clear()
    _cache.update(out)
    _fetched_at = time.time()


def day_change_pct(coin: str) -> float | None:
    """Percent move vs previous day close, or None if unavailable.
    Refreshes at most once per _TTL_S; a failed refresh keeps serving the
    last snapshot rather than blocking signal handling."""
    if time.time() - _fetched_at > _TTL_S:
        try:
            _refresh_sync()
        except Exception as e:  # noqa: BLE001 — stale beats broken
            print(f"[trend] refresh failed (serving stale): {e!r}")
    return _cache.get(coin)


def fade_block(coin: str, direction: str) -> str | None:
    """Human reason if this entry would FADE a strong same-day move
    (short into a big up day / long into a big down day), else None."""
    chg = day_change_pct(coin)
    if chg is None:
        return None
    if direction == "short" and chg >= config.TREND_FILTER_PCT:
        return (f"📈 trend filter: {coin} is {chg:+.1f}% today — not shorting "
                f"into a strong up day (limit ±{config.TREND_FILTER_PCT:.0f}%).")
    if direction == "long" and chg <= -config.TREND_FILTER_PCT:
        return (f"📉 trend filter: {coin} is {chg:+.1f}% today — not buying "
                f"into a strong down day (limit ±{config.TREND_FILTER_PCT:.0f}%).")
    return None


if __name__ == "__main__":
    for c in ("xyz:MU", "xyz:NVDA", "xyz:XYZ100", "BTC"):
        print(f"{c:12s} day change: {day_change_pct(c)}")
    print("short MU:", fade_block("xyz:MU", "short"))
    print("long  MU:", fade_block("xyz:MU", "long"))
