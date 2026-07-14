"""Quick PnL/hit-rate report over the signal journal.

    .venv/bin/python stats.py            # everything (alert-only + executed)
    .venv/bin/python stats.py --executed  # only trades where Execute was tapped
"""
from __future__ import annotations

import sys

import journal


def _print(title: str, s: dict) -> None:
    print(f"\n=== {title} ===")
    if s.get("n", 0) == 0:
        print("  no closed trades yet")
        return
    wr = s["win_rate"]
    print(f"  closed trades   : {s['n']}  (scalp {s['scalp_n']}, swing {s['swing_n']})")
    print(f"  win / loss      : {s['wins']} / {s['losses']}  "
          f"(win rate {wr:.0%})" if wr is not None else "")
    print(f"  total PnL       : {s['total_margin_pct']:+.1f}% on margin  "
          f"({s['total_raw_pct']:+.1f}% raw underlying move)")
    print(f"  avg per trade   : {s['avg_margin_pct']:+.1f}% on margin")
    print(f"  best / worst    : {s['best_margin_pct']:+.1f}% / {s['worst_margin_pct']:+.1f}%")


if __name__ == "__main__":
    executed_only = "--executed" in sys.argv
    if executed_only:
        _print("EXECUTED — dry run", journal.summary(executed_only=True, dry_run=True))
        _print("EXECUTED — live", journal.summary(executed_only=True, dry_run=False))
    else:
        _print("ALL alerts (fired, whether executed or not)", journal.summary())
        _print("EXECUTED only (dry + live)", journal.summary(executed_only=True))
