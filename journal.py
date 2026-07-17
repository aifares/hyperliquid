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
    margin REAL DEFAULT 0,
    stop REAL DEFAULT NULL,
    target REAL DEFAULT NULL,
    fill_entry REAL DEFAULT NULL,
    conviction REAL DEFAULT NULL,
    trend_pct REAL DEFAULT NULL,
    breakout TEXT DEFAULT NULL,
    imbalance REAL DEFAULT NULL,
    liq_note TEXT DEFAULT NULL,
    funding_note TEXT DEFAULT NULL,
    news_note TEXT DEFAULT NULL,
    equity_baseline REAL DEFAULT NULL
);
"""


_RALLY_ARMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS rally_arms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    coin TEXT NOT NULL,
    direction TEXT NOT NULL,
    news_conf REAL,
    trend_pct REAL,
    broad_trend_pct REAL,
    note TEXT,
    outcome TEXT DEFAULT 'armed',
    confirmed_ts REAL,
    imbalance REAL,
    flow_ratio REAL,
    signal_id INTEGER
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.execute(_SCHEMA)
    c.execute(_RALLY_ARMS_SCHEMA)
    _migrate(c)
    return c


def _migrate(c: sqlite3.Connection) -> None:
    """Add columns introduced after a DB already existed (idempotent)."""
    cols = {row[1] for row in c.execute("PRAGMA table_info(signals)")}
    if "dry_run" not in cols:
        c.execute("ALTER TABLE signals ADD COLUMN dry_run INTEGER DEFAULT 0")
    if "margin" not in cols:
        c.execute("ALTER TABLE signals ADD COLUMN margin REAL DEFAULT 0")
    if "stop" not in cols:
        c.execute("ALTER TABLE signals ADD COLUMN stop REAL DEFAULT NULL")
    # bigswing backtest-fidelity columns (2026-07-15): the full conviction
    # breakdown + the REAL exchange fill price (vs. the decision-time
    # reference `entry`) so a future backtest can be built from actual live
    # trades instead of re-deriving assumptions.
    for col, decl in (
        ("target", "REAL DEFAULT NULL"),
        ("fill_entry", "REAL DEFAULT NULL"),
        ("conviction", "REAL DEFAULT NULL"),
        ("trend_pct", "REAL DEFAULT NULL"),
        ("breakout", "TEXT DEFAULT NULL"),
        ("imbalance", "REAL DEFAULT NULL"),
        ("liq_note", "TEXT DEFAULT NULL"),
        ("funding_note", "TEXT DEFAULT NULL"),
        ("news_note", "TEXT DEFAULT NULL"),
        ("equity_baseline", "REAL DEFAULT NULL"),
        # partial-exit bookkeeping (2026-07-17): price at which the 1R half
        # was banked (NULL = no partial taken). Survives restarts so a
        # resumed watcher doesn't bank the same half twice.
        ("partial_px", "REAL DEFAULT NULL"),
    ):
        if col not in cols:
            c.execute(f"ALTER TABLE signals ADD COLUMN {col} {decl}")


def log_signal(*, coin: str, direction: str, confidence: float, magnitude: float,
               leverage: int, entry: float, headline: str, rationale: str,
               tape_note: str, horizon: str = "scalp",
               stop: float | None = None, target: float | None = None,
               conviction: float | None = None, trend_pct: float | None = None,
               breakout: str | None = None, imbalance: float | None = None,
               liq_note: str | None = None, funding_note: str | None = None,
               news_note: str | None = None,
               equity_baseline: float | None = None) -> int:
    """The conviction/trend_pct/breakout/imbalance/liq_note/funding_note/
    news_note/equity_baseline/target fields are bigswing-specific (None for
    scalp/swing/runup) — captured here rather than only in the Telegram
    notification text so a real trade's full decision inputs survive in a
    queryable form for building a backtest off live data later."""
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO signals (ts,coin,direction,confidence,magnitude,leverage,"
            "horizon,entry,headline,rationale,tape_note,stop,target,conviction,"
            "trend_pct,breakout,imbalance,liq_note,funding_note,news_note,"
            "equity_baseline) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), coin, direction, confidence, magnitude, leverage,
             horizon, entry, headline, rationale, tape_note, stop, target,
             conviction, trend_pct, breakout, imbalance, liq_note, funding_note,
             news_note, equity_baseline),
        )
        return cur.lastrowid


def mark_partial(signal_id: int, px: float) -> None:
    """Record that the 1R partial was banked at `px` (idempotence marker for
    the watcher's partial-exit logic across restarts)."""
    with _conn() as c:
        c.execute("UPDATE signals SET partial_px=? WHERE id=?", (px, signal_id))


def record_fill(signal_id: int, fill_entry: float) -> None:
    """The REAL average fill price from the exchange, distinct from `entry`
    (the mid-price reference the stop/target math was computed off at
    decision time) — the gap between them is real slippage, useful signal
    for a backtest built from live data rather than a proxy."""
    with _conn() as c:
        c.execute("UPDATE signals SET fill_entry=? WHERE id=?", (fill_entry, signal_id))


def update_entry(signal_id: int, entry: float) -> None:
    """Corrects a stale `entry` after watcher.py detects a live resize/
    reconcile — without this, the persisted row keeps the original
    decision-time reference price forever even after the bot itself starts
    using a corrected one in memory."""
    with _conn() as c:
        c.execute("UPDATE signals SET entry=? WHERE id=?", (entry, signal_id))


def promote_to_bigswing(signal_id: int, *, entry: float, leverage: int,
                        margin: float, stop: float, target: float,
                        equity_baseline: float,
                        conviction: float | None = None) -> None:
    """Re-label an open live row as bigswing (e.g. rally opened small, user
    sized up, wants bigswing stop/target/overnight de-risk/equity net)."""
    with _conn() as c:
        c.execute(
            "UPDATE signals SET horizon='bigswing', entry=?, leverage=?, margin=?, "
            "stop=?, target=?, equity_baseline=?, "
            "conviction=COALESCE(?, conviction), "
            "headline='bigswing (promoted from rally)', "
            "rationale='rally entry re-managed under bigswing rules' "
            "WHERE id=? AND executed=1 AND (exit_reason IS NULL OR exit_reason='')",
            (entry, leverage, margin, stop, target, equity_baseline,
             conviction, signal_id))


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
    """Force-close open PAPER rows left over from a previous process (dry_run=1
    only — no real order exists to reattach to, so scratch them). Real live
    fills (dry_run=0) are NOT touched here: a real order + resting stop/target
    is sitting on the exchange and must be resumed, not fake-closed — see
    watcher.resume_live()."""
    mids = mids or {}
    with _conn() as c:
        rows = c.execute(
            "SELECT id, coin FROM signals WHERE (exit_reason IS NULL OR "
            "exit_reason='') AND horizon != 'runup' AND dry_run=1").fetchall()
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


def held_position(coin: str) -> tuple[str, str] | None:
    """(direction, horizon) of the position currently open on this coin, if
    any — used to catch a contra-signal against something you actually hold."""
    with _conn() as c:
        row = c.execute(
            "SELECT direction, horizon FROM signals WHERE executed=1 AND coin=? "
            "AND (exit_reason IS NULL OR exit_reason='') LIMIT 1", (coin,)).fetchone()
    return tuple(row) if row else None


def all_held_positions() -> list[tuple[str, str, str]]:
    """(coin, direction, horizon) for every open position — fed to the
    analyzer as context so it knows what's already held."""
    with _conn() as c:
        return c.execute(
            "SELECT coin, direction, horizon FROM signals WHERE executed=1 "
            "AND (exit_reason IS NULL OR exit_reason='')").fetchall()


def open_live_rows() -> list[tuple]:
    """Real (dry_run=0), scalp/swing positions still open in the journal —
    these have a genuine order + resting stop/target on the exchange and
    must be resumed with a watcher on restart, never force-closed. Includes
    the stop FROZEN at entry time (NULL for legacy rows opened before that
    column existed) so a restart can't silently apply a newer geometry.
    Excludes 'runup' (own resume in earnings_runup._resume) AND 'bigswing'
    (own resume in bigswing._resume, which also needs to reconstruct the
    equity-safety-net baseline — watcher.resume_live() has no concept of
    that, so it must never touch a bigswing row)."""
    with _conn() as c:
        return c.execute(
            "SELECT id, coin, direction, horizon, leverage, entry, ts, stop, "
            "partial_px FROM signals "
            "WHERE executed=1 AND dry_run=0 AND horizon NOT IN ('runup', 'bigswing') "
            "AND (exit_reason IS NULL OR exit_reason='')").fetchall()


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


def export_rows(horizon: str | None = None) -> list[dict]:
    """Every logged field for real trades (dry_run=0), as plain dicts — the
    raw material for building your own backtest off live data instead of a
    historical proxy. Pass horizon='bigswing' to scope to that tier."""
    q = "SELECT * FROM signals WHERE dry_run=0"
    args: tuple = ()
    if horizon:
        q += " AND horizon=?"
        args = (horizon,)
    q += " ORDER BY ts"
    with _conn() as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def export_csv(path: str, horizon: str | None = None) -> int:
    """Writes export_rows() to a CSV file. Returns the row count."""
    import csv
    rows = export_rows(horizon)
    if not rows:
        return 0
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    return len(rows)


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


# --- rally arm journal (news+trend armed, waiting for tick book confirm) ------
def log_rally_arm(*, coin: str, direction: str, news_conf: float,
                  trend_pct: float | None, broad_trend_pct: float | None,
                  note: str) -> int:
    """Every time rally arms a coin — needed because the tick-book confirm
    half can't be classically backtested, so live/shadow arm→outcome data
    is the main validation path."""
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO rally_arms (ts,coin,direction,news_conf,trend_pct,"
            "broad_trend_pct,note,outcome) VALUES (?,?,?,?,?,?,?,'armed')",
            (time.time(), coin, direction, news_conf, trend_pct,
             broad_trend_pct, note))
        return cur.lastrowid


def update_rally_arm(arm_id: int, *, outcome: str,
                     imbalance: float | None = None,
                     flow_ratio: float | None = None,
                     signal_id: int | None = None) -> None:
    """outcome in {'confirmed','expired','confirmed_blocked','cancelled'}."""
    with _conn() as c:
        c.execute(
            "UPDATE rally_arms SET outcome=?, confirmed_ts=?,"
            "imbalance=COALESCE(?, imbalance), flow_ratio=COALESCE(?, flow_ratio),"
            "signal_id=COALESCE(?, signal_id) WHERE id=?",
            (outcome, time.time(), imbalance, flow_ratio, signal_id, arm_id))


def rally_arm_stats() -> dict:
    """Post-hoc validation summary for the rally arm→confirm pipeline."""
    with _conn() as c:
        rows = c.execute("SELECT outcome FROM rally_arms").fetchall()
    counts: dict[str, int] = {}
    for (outcome,) in rows:
        counts[outcome] = counts.get(outcome, 0) + 1
    n = sum(counts.values())
    confirmed = counts.get("confirmed", 0) + counts.get("confirmed_blocked", 0)
    return {
        "n": n,
        "armed": counts.get("armed", 0),
        "confirmed": counts.get("confirmed", 0),
        "confirmed_blocked": counts.get("confirmed_blocked", 0),
        "expired": counts.get("expired", 0),
        "cancelled": counts.get("cancelled", 0),
        "confirm_rate": (confirmed / n) if n else None,
        "by_outcome": counts,
    }


if __name__ == "__main__":
    sid = log_signal(coin="xyz:GOLD", direction="short", confidence=0.7,
                     magnitude=0.6, leverage=25, entry=4002.45,
                     headline="test", rationale="test", tape_note="test")
    record_followup(sid, "px_5m", 3990.0)
    print("logged id", sid, "| hit_rate:", hit_rate())
    print("rally_arm_stats:", rally_arm_stats())
    # keep the db clean after self-test
    with _conn() as c:
        c.execute("DELETE FROM signals WHERE id=?", (sid,))
