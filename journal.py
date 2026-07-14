"""SQLite signal journal.

Every fired alert is logged with the mid price at fire time. A background task
records the price 1/5/30 minutes later so you can measure hit-rate in SHADOW
mode before ever risking money.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import config

DB = Path(__file__).with_name("signals.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    coin TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL,
    magnitude REAL,
    leverage INTEGER,
    horizon TEXT DEFAULT 'scalp',
    entry REAL,
    headline TEXT,
    rationale TEXT,
    tape_note TEXT,
    px_1m REAL,
    px_5m REAL,
    px_30m REAL,
    exit_ts REAL,
    exit_px REAL,
    exit_reason TEXT,
    executed INTEGER DEFAULT 0,
    dry_run INTEGER DEFAULT 0,
    margin REAL DEFAULT 0
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.execute(_SCHEMA)
    _migrate(c)
    return c


def _migrate(c: sqlite3.Connection) -> None:
    """Add columns introduced after a DB already existed (idempotent)."""
    cols = {row[1] for row in c.execute("PRAGMA table_info(signals)")}
    if "dry_run" not in cols:
        c.execute("ALTER TABLE signals ADD COLUMN dry_run INTEGER DEFAULT 0")
    if "margin" not in cols:
        c.execute("ALTER TABLE signals ADD COLUMN margin REAL DEFAULT 0")


def log_signal(*, coin: str, direction: str, confidence: float, magnitude: float,
               leverage: int, entry: float, headline: str, rationale: str,
               tape_note: str, horizon: str = "scalp") -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO signals (ts,coin,direction,confidence,magnitude,leverage,"
            "horizon,entry,headline,rationale,tape_note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), coin, direction, confidence, magnitude, leverage,
             horizon, entry, headline, rationale, tape_note),
        )
        return cur.lastrowid


def mark_executed(signal_id: int, *, dry_run: bool, margin: float = 0.0) -> None:
    with _conn() as c:
        c.execute("UPDATE signals SET executed=1, dry_run=?, margin=? WHERE id=?",
                  (int(dry_run), margin, signal_id))


def record_exit(signal_id: int, *, exit_px: float, reason: str) -> None:
    with _conn() as c:
        c.execute("UPDATE signals SET exit_ts=?, exit_px=?, exit_reason=? WHERE id=?",
                  (time.time(), exit_px, reason, signal_id))


def realized_pnl_usd() -> float:
    """Realized $ PnL across closed executed trades: margin × leveraged move,
    net of round-trip taker fees on the notional."""
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(margin * leverage * "
            "  (((exit_px - entry) / entry) "
            "   * (CASE direction WHEN 'long' THEN 1 ELSE -1 END) - ?)), 0) "
            "FROM signals WHERE executed=1 AND exit_px IS NOT NULL AND entry > 0 "
            "AND exit_reason != 'ORPHAN'",  # restart artifacts, not real trades
            (config.ROUND_TRIP_FEE,)
        ).fetchone()
    return float(row[0])


def close_orphans(mids: dict[str, float] | None = None) -> int:
    """Force-close open rows left over from a previous process (their watcher
    tasks died with it). With live `mids`, the exit is recorded at the actual
    current price — reason RESTART, real PnL, counts in stats and bankroll.
    Without prices (fallback), scratch at entry as ORPHAN (excluded)."""
    mids = mids or {}
    with _conn() as c:
        rows = c.execute(
            "SELECT id, coin FROM signals WHERE (exit_reason IS NULL OR "
            "exit_reason='') AND horizon != 'runup'").fetchall()
        n = 0
        for sid, coin in rows:
            mid = mids.get(coin, 0.0)
            if mid > 0:
                c.execute("UPDATE signals SET exit_ts=?, exit_px=?, "
                          "exit_reason='RESTART' WHERE id=?",
                          (time.time(), mid, sid))
            else:
                c.execute("UPDATE signals SET exit_ts=?, exit_px=entry, "
                          "exit_reason='ORPHAN' WHERE id=?", (time.time(), sid))
            n += 1
        return n


def record_followup(signal_id: int, column: str, price: float) -> None:
    assert column in ("px_1m", "px_5m", "px_30m")
    with _conn() as c:
        c.execute(f"UPDATE signals SET {column}=? WHERE id=?", (price, signal_id))


def trade_pnl_pct(direction: str, entry: float, exit_px: float, leverage: int) -> tuple[float, float]:
    """(raw_pct, margin_pct) move from entry to exit, signed for direction,
    NET of round-trip taker fees (fees scale with notional, so on margin they
    scale with leverage)."""
    if not entry:
        return 0.0, 0.0
    raw = (exit_px - entry) / entry * 100
    if direction == "short":
        raw = -raw
    raw -= config.ROUND_TRIP_FEE * 100
    return raw, raw * leverage


def summary(*, executed_only: bool = False, dry_run: bool | None = None) -> dict:
    """Aggregate stats over closed trades (exit_reason IS NOT NULL).

    executed_only=True restricts to trades where the Execute button was tapped.
    dry_run=True/False further filters simulated vs real fills (only meaningful
    when executed_only=True, since alert-only rows have dry_run=0 by default).
    """
    q = "SELECT direction, entry, exit_px, leverage, horizon, executed, dry_run " \
        "FROM signals WHERE exit_reason IS NOT NULL AND exit_reason != 'ORPHAN'"
    if executed_only:
        q += " AND executed=1"
        if dry_run is not None:
            q += f" AND dry_run={int(dry_run)}"
    with _conn() as c:
        rows = c.execute(q).fetchall()

    if not rows:
        return {"n": 0}

    wins = losses = 0
    raw_sum = margin_sum = 0.0
    best = worst = None
    by_horizon: dict[str, list[float]] = {"scalp": [], "swing": []}

    for direction, entry, exit_px, leverage, horizon, _ex, _dr in rows:
        if not entry or not exit_px:
            continue
        raw, margin = trade_pnl_pct(direction, entry, exit_px, leverage or 1)
        raw_sum += raw
        margin_sum += margin
        by_horizon.setdefault(horizon, []).append(margin)
        if margin > 0:
            wins += 1
        else:
            losses += 1
        if best is None or margin > best:
            best = margin
        if worst is None or margin < worst:
            worst = margin

    n = wins + losses
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "win_rate": wins / n if n else None,
        "total_raw_pct": round(raw_sum, 2),
        "total_margin_pct": round(margin_sum, 2),
        "avg_margin_pct": round(margin_sum / n, 2) if n else None,
        "best_margin_pct": round(best, 2) if best is not None else None,
        "worst_margin_pct": round(worst, 2) if worst is not None else None,
        "scalp_n": len(by_horizon.get("scalp", [])),
        "swing_n": len(by_horizon.get("swing", [])),
    }


def hit_rate() -> dict:
    """Simple directional hit-rate at the 5m mark (for quick review)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT direction, entry, px_5m FROM signals WHERE px_5m IS NOT NULL"
        ).fetchall()
    wins = total = 0
    for direction, entry, px5 in rows:
        if not entry or not px5:
            continue
        total += 1
        moved_up = px5 > entry
        if (direction == "long" and moved_up) or (direction == "short" and not moved_up):
            wins += 1
    return {"n": total, "wins": wins, "hit_rate": (wins / total) if total else None}


if __name__ == "__main__":
    sid = log_signal(coin="xyz:GOLD", direction="short", confidence=0.7,
                     magnitude=0.6, leverage=25, entry=4002.45,
                     headline="test", rationale="test", tape_note="test")
    record_followup(sid, "px_5m", 3990.0)
    print("logged id", sid, "| hit_rate:", hit_rate())
    # keep the db clean after self-test
    with _conn() as c:
        c.execute("DELETE FROM signals WHERE id=?", (sid,))
