"""Shared event types flowing from ingestion (Telegram/news) to the analyzer."""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class NewsEvent:
    source: str          # "telegram:<channel>" or "perplexity"
    text: str
    ts: float = field(default_factory=time.time)
    url: str = ""

    def key(self) -> str:
        """Dedup key: source-independent hash of the normalized text head."""
        norm = " ".join(self.text.lower().split())[:160]
        return norm
