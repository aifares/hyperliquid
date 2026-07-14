"""Tees every print() from every module into a timestamped log file.

No other module needs to change — this wraps sys.stdout/stderr once, at
process start, so every existing `print(...)` call across the codebase (tick
engine reconnects, Telegram messages, Perplexity polls, Claude classifications,
combiner decisions, alerts, exits, guardrail checks, errors) lands in one file
with a timestamp, in addition to the console.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

LOG_PATH = Path(__file__).with_name("bot.log")


class _Tee:
    def __init__(self, stream, log_file):
        self.stream = stream
        self.log_file = log_file
        self._at_line_start = True

    def write(self, data: str) -> int:
        n = self.stream.write(data)
        for line in data.splitlines(keepends=True):
            if self._at_line_start and line.strip():
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                self.log_file.write(f"[{ts}] {line}")
            else:
                self.log_file.write(line)
            self._at_line_start = line.endswith("\n")
        self.log_file.flush()
        return n

    def flush(self) -> None:
        self.stream.flush()
        self.log_file.flush()

    def isatty(self) -> bool:
        return False


def init() -> Path:
    """Idempotent: safe to call once at process start."""
    if isinstance(sys.stdout, _Tee):
        return LOG_PATH
    f = open(LOG_PATH, "a", buffering=1)
    f.write(f"\n{'='*70}\nrun started {time.strftime('%Y-%m-%d %H:%M:%S')}\n{'='*70}\n")
    sys.stdout = _Tee(sys.stdout, f)
    sys.stderr = _Tee(sys.stderr, f)
    return LOG_PATH
