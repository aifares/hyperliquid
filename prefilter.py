"""Pre-analyzer filter: don't spend a Haiku call re-analyzing the same story.

Perplexity re-serves one story reworded for hours (observed: a single story
analyzed 19x in one day; 2,892 headlines analyzed on 2026-07-14 of which
~34% were near-duplicates). A token-overlap check against recently-seen
headlines skips the rewordings; the first telling of every story still gets
a full analysis.

A keyword pre-filter (only analyze items mentioning a watched market) was
built, replay-tested against the full 2026-07-14 log, and REJECTED: 34% of
the headlines it skipped had been scored actionable, and it would have
dropped the exact TSMC-revenue headline that produced the day's best trade
(NVDA swing, +$2.57). This feed is curated financial news — nearly everything
is potentially relevant, and catalysts routinely arrive phrased via peers
(TSMC->NVDA, SK Hynix->MU) or macro vocabulary no keyword list anticipates.
Judging relevance IS the analyzer's job; don't pre-empt it with substrings.
"""
from __future__ import annotations

import re
import time
from collections import deque

import config

_STOPWORDS = frozenset(
    "the a an and or of to in on as at for with amid after its is are was "
    "were has have had this that from by said says say much more most be "
    "will would their they his her he she it new".split())
_recent: deque[tuple[frozenset[str], float]] = deque(maxlen=300)

# running tallies for the periodic usage report in main._analyzer_loop
analyzed = 0
skipped_dup = 0


def _tokens(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9%$.]{3,}", text.lower())
    return frozenset(w for w in words if w not in _STOPWORDS)


def is_duplicate(text: str) -> bool:
    """True if this reads as a rewording of a recently-analyzed story.
    Records the text when it is NOT a duplicate (so the next rewording of
    it gets caught)."""
    now = time.time()
    toks = _tokens(text)
    if not toks:
        return False
    cutoff = now - config.NEWS_DEDUP_WINDOW_S
    for seen, ts in _recent:
        if ts < cutoff:
            continue
        inter = len(toks & seen)
        # Overlap coefficient (÷ smaller set), NOT Jaccard (÷ union): a short
        # headline and its long rewording share nearly all of the short one's
        # tokens but Jaccard drowns that in the long one's extra words —
        # measured on the 2026-07-14 log, Jaccard@0.65 caught 34% of true
        # rewordings, overlap@0.75 caught 61%.
        denom = min(len(toks), len(seen))
        if denom and inter / denom >= config.NEWS_DEDUP_SIM:
            return True
    _recent.append((toks, now))
    return False


if __name__ == "__main__":
    a = "SK Hynix shares dropped 8.2% as investors booked profits, impacting Micron"
    b = "SK Hynix shares dropped as much as 8.2% in early trade as investors booked profits, hitting Micron"
    c = "NVIDIA announces record datacenter revenue, raises guidance"
    print("first telling  ->", is_duplicate(a), "(want False)")
    print("reworded copy  ->", is_duplicate(b), "(want True)")
    print("different story->", is_duplicate(c), "(want False)")
