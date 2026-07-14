"""Live terminal dashboard for the notifier.

Read-only: prices via REST allMids, positions/trades from signals.sqlite,
activity from bot.log. Runs alongside the background bot without touching it.

    .venv/bin/python dashboard.py            # live, refreshes every 3s
    .venv/bin/python dashboard.py --once     # render one frame and exit
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import config
import journal
import notifier
import watcher

REFRESH_S = 3.0
HERE = Path(__file__).parent
LOG = HERE / "bot.log"
PIDFILE = HERE / ".bot.pid"


# --- data ---------------------------------------------------------------------
def fetch_mids() -> dict[str, float]:
    mids: dict[str, float] = {}
    for dex in ("", "xyz"):
        body = {"type": "allMids"}
        if dex:
            body["dex"] = dex
        req = urllib.request.Request(
            config.HL_INFO_URL, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                mids.update({k: float(v) for k, v in json.load(r).items()})
        except Exception:  # noqa: BLE001 — dashboard must never crash on a blip
            pass
    return mids


def fetch_positions() -> dict[str, dict]:
    """Live on-chain positions keyed by coin, straight from Hyperliquid — so
    the dashboard shows the exchange's OWN entry/mark/PnL/ROE for real
    positions instead of reconstructing them (which drifted from the app)."""
    out: dict[str, dict] = {}
    if not config.WALLET_ADDRESS:
        return out
    for dex in ("", "xyz"):
        body = {"type": "clearinghouseState", "user": config.WALLET_ADDRESS}
        if dex:
            body["dex"] = dex
        req = urllib.request.Request(
            config.HL_INFO_URL, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                for p in json.load(r).get("assetPositions", []):
                    q = p["position"]
                    out[q["coin"]] = q
        except Exception:  # noqa: BLE001 — never crash the dashboard on a blip
            pass
    return out


def bot_status() -> tuple[str, str]:
    """(status_text, style)"""
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 0)
        etime = subprocess.run(
            ["ps", "-o", "etime=", "-p", str(pid)],
            capture_output=True, text=True).stdout.strip()
        return f"RUNNING pid {pid} · up {etime}", "bold green"
    except (ValueError, ProcessLookupError, PermissionError, FileNotFoundError):
        return "NOT RUNNING", "bold red"


def query(sql: str, args: tuple = ()) -> list[tuple]:
    with journal._conn() as c:  # noqa: SLF001 — reuse schema/migration handling
        return c.execute(sql, args).fetchall()


# --- rendering ----------------------------------------------------------------
def _pnl_text(pct: float) -> Text:
    style = "green" if pct > 0 else ("red" if pct < 0 else "dim")
    return Text(f"{pct:+.2f}%", style=style)


def _usd_text(usd: float) -> Text:
    style = "green" if usd > 0 else ("red" if usd < 0 else "dim")
    return Text(f"{usd:+.3f}", style=style)


def _age(ts: float | None) -> str:
    if ts is None:
        return "—"
    s = int(time.time() - ts)
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600}h"


def open_positions(mids: dict[str, float],
                   positions: dict[str, dict]) -> tuple[Table, float]:
    """Real money at risk, live-first: iterates the actual exchange positions
    (`positions`, already fetched from Hyperliquid) as the primary loop, so a
    manual trade with no matching journal row still shows up — labeled tier
    "manual", no stop/target since the bot never assigned it a thesis and
    must not invent one. Journal rows enrich a matching coin with the bot's
    own id/tier/age when a real trade backs the position. Pure paper (dry-run)
    rows have no real position to match, so they're rendered separately using
    the fire-time entry + live mid."""
    rows = query(
        "SELECT id, ts, coin, direction, horizon, leverage, entry, dry_run "
        "FROM signals WHERE executed=1 AND (exit_reason IS NULL OR exit_reason = '') "
        "ORDER BY ts DESC")
    journal_by_coin: dict[str, tuple] = {}
    for r in rows:
        journal_by_coin.setdefault(r[2], r)   # already ORDER BY ts DESC -> keeps latest

    t = Table(expand=True, header_style="bold cyan", border_style="dim")
    for col in ("id", "coin", "dir", "tier", "lev", "entry", "now",
                "stop", "target", "raw", "ROE%", "uPnL$", "age", "exec"):
        t.add_column(col, justify="right" if col not in ("coin", "dir", "tier") else "left")

    total_upnl = 0.0
    rendered = 0

    def _emit(sid, ts, coin, d, hz, lev, entry, now, raw, roe, upnl, exec_label) -> None:
        nonlocal total_upnl, rendered
        if hz == "runup":
            stop, tgt_display = entry * (1 - config.RUNUP_STOP_RAW), "📅"
        elif hz == "manual":
            stop, tgt_display = 0.0, "—"
        else:
            stop = notifier.stop_price(entry, d, lev)
            tgt_display = f"{watcher.target_price(entry, stop, d):g}"
        total_upnl += upnl
        rendered += 1
        t.add_row(
            str(sid), coin.replace("xyz:", ""),
            Text(d.upper(), style="green" if d == "long" else "red"),
            hz, f"{lev}x", f"{entry:g}", f"{now:g}" if now else "?",
            f"{stop:g}" if stop else "—", tgt_display,
            _pnl_text(raw), _pnl_text(roe), _usd_text(upnl), _age(ts), exec_label)

    # Primary: every REAL position on the exchange.
    for coin, pos in positions.items():
        entry = float(pos["entryPx"])
        szi = float(pos["szi"])
        now = float(pos["positionValue"]) / abs(szi) if szi else mids.get(coin, 0.0)
        roe = float(pos["returnOnEquity"]) * 100
        upnl = float(pos["unrealizedPnl"])
        row = journal_by_coin.get(coin)
        if row:
            sid, ts, _, d, hz, lev, _je, dry = row
            exec_label = Text("SIM", style="dim") if dry else Text("LIVE", style="bold red")
        else:  # fully manual — no bot thesis, display-only
            sid, ts, hz, dry = "—", None, "manual", 0
            d = "long" if szi > 0 else "short"
            lev = int((pos.get("leverage") or {}).get("value", 0)) or 1
            exec_label = Text("LIVE", style="dim")
        raw = (now - entry) / entry * 100 * (1 if d == "long" else -1)
        _emit(sid, ts, coin, d, hz, lev, entry, now, raw, roe, upnl, exec_label)

    # Secondary: pure paper (dry-run) rows — never in `positions` by
    # definition, so keep the old fire-time-entry + live-mid method.
    for sid, ts, coin, d, hz, lev, entry, dry in rows:
        if not dry or coin in positions:
            continue
        now = mids.get(coin, 0.0)
        raw, roe = journal.trade_pnl_pct(d, entry, now, lev) if now else (0.0, 0.0)
        _emit(sid, ts, coin, d, hz, lev, entry, now, raw, roe, 0.0, Text("SIM", style="dim"))

    if not rendered:
        t.add_row(*["—"] * 14)
    return t, total_upnl


def watching_signals() -> Table:
    """Signals that fired but never became a position — blocked by a
    guardrail, expired, or (button mode) never confirmed. No money at risk."""
    rows = query(
        "SELECT id, ts, coin, direction, horizon, leverage, entry "
        "FROM signals WHERE executed=0 AND (exit_reason IS NULL OR exit_reason = '') "
        "ORDER BY ts DESC LIMIT 8")
    t = Table(expand=True, header_style="bold yellow", border_style="dim")
    for col in ("id", "coin", "dir", "tier", "lev", "entry", "age"):
        t.add_column(col, justify="right" if col not in ("coin", "dir", "tier") else "left")
    for sid, ts, coin, d, hz, lev, entry in rows:
        t.add_row(
            str(sid), coin.replace("xyz:", ""),
            Text(d.upper(), style="green" if d == "long" else "red"),
            hz, f"{lev}x", f"{entry:g}", _age(ts))
    if not rows:
        t.add_row(*["—"] * 7)
    return t


def closed_trades(limit: int = 12) -> Table:
    rows = query(
        "SELECT ts, coin, direction, horizon, leverage, entry, exit_px, "
        "exit_reason, executed, dry_run "
        "FROM signals WHERE exit_reason IS NOT NULL AND exit_reason != '' "
        "ORDER BY exit_ts DESC LIMIT ?", (limit,))
    t = Table(expand=True, header_style="bold cyan", border_style="dim")
    for col in ("time", "coin", "dir", "tier", "entry→exit", "reason", "exec", "PnL@lev"):
        t.add_column(col, justify="right" if col in ("entry→exit", "PnL@lev") else "left")
    for ts, coin, d, hz, lev, entry, xpx, reason, executed, dry in rows:
        orphan = reason == "ORPHAN"
        _, net = journal.trade_pnl_pct(d, entry, xpx, lev)
        exec_label = ("SIM" if dry else "LIVE") if executed else "—"
        t.add_row(
            Text(time.strftime("%H:%M", time.localtime(ts)), style="dim" if orphan else ""),
            Text(coin.replace("xyz:", ""), style="dim" if orphan else ""),
            Text(d.upper(), style="dim" if orphan else ("green" if d == "long" else "red")),
            Text(hz, style="dim" if orphan else ""),
            Text(f"{entry:g} → {xpx:g}", style="dim" if orphan else ""),
            Text("↻ SCRATCH", style="dim") if orphan else
            {"STOP": "🛑 STOP", "TARGET": "🎯 TGT", "FADE": "📉 FADE",
             "TIME": "⏰ TIME", "RESTART": "↻ RESTART", "PRINT": "📅 PRINT",
             "NEWS": "📰 NEWS", "EXTERNAL": "🔚 EXTERNAL",
             "EXTERNAL_FLIP": "🔀 FLIPPED"}.get(reason, reason),
            Text(exec_label, style="dim" if orphan else ("yellow" if executed else "dim")),
            Text("excluded", style="dim") if orphan else _pnl_text(net))
    if not rows:
        t.add_row(*["—"] * 8)
    return t


def stats_line() -> Text:
    rows = query(
        "SELECT horizon, exit_reason, "
        "  ((exit_px - entry) / entry * (CASE direction WHEN 'long' THEN 1 ELSE -1 END) - ?) "
        "  * 100 * leverage "
        "FROM signals WHERE exit_reason IS NOT NULL AND exit_reason != '' "
        "AND exit_reason != 'ORPHAN'", (config.ROUND_TRIP_FEE,))
    n = len(rows)
    if not n:
        return Text("no closed trades yet", style="dim")
    wins = sum(1 for *_, p in rows if p > 0)
    total = sum(p for *_, p in rows)
    scalp = sum(1 for h, *_ in rows if h == "scalp")
    stops = sum(1 for _, r, _ in rows if r == "STOP")
    tgts = sum(1 for _, r, _ in rows if r == "TARGET")
    txt = Text()
    txt.append(f"closed {n} (scalp {scalp}/swing {n - scalp})  ·  ")
    txt.append(f"win {wins}/{n} ({wins / n * 100:.0f}%)  ·  ")
    txt.append("PnL@lev ")
    txt.append(f"{total:+.1f}%", style="green" if total >= 0 else "red")
    txt.append(f"  ·  🎯 {tgts}  🛑 {stops}")
    txt.append("  ·  net of fees", style="dim")
    return txt


def recent_log(lines: int = 10) -> Text:
    try:
        raw = LOG.read_text().splitlines()
    except FileNotFoundError:
        return Text("no bot.log", style="dim")
    picked = [l for l in raw if l.strip() and not l.startswith("=")][-lines:]
    out = Text()
    for l in picked:
        style = "dim"
        if "ALERT" in l or "CASCADE" in l:
            style = "bold yellow"
        elif "EXIT" in l:
            style = "bold magenta"
        elif "actionable=True" in l:
            style = "white"
        out.append(l[:200] + "\n", style=style)
    return out


def _margin_committed(positions: dict[str, dict]) -> float:
    """Same live-first-else-journal logic as executor.margin_committed(), but
    sourced from THIS process's own `positions` fetch — the dashboard never
    runs account_monitor's poller, so executor's version would see an empty
    cache here and silently fall back to stale journal sums for everything."""
    rows = query(
        "SELECT coin, margin, dry_run FROM signals "
        "WHERE executed=1 AND (exit_reason IS NULL OR exit_reason='')")
    total = 0.0
    for coin, margin, dry in rows:
        pos = None if dry else positions.get(coin)
        total += float(pos["marginUsed"]) if pos else margin
    return total


def render() -> Layout:
    import executor  # local import: avoids a hard dependency for --once smoke use
    mids = fetch_mids()
    positions = fetch_positions()
    status, style = bot_status()
    open_t, open_upnl = open_positions(mids, positions)

    committed = _margin_committed(positions)
    realized = journal.realized_pnl_usd()
    bankroll = executor.bankroll()             # hardcoded ceiling — see executor.bankroll()
    header = Text()
    header.append(" ", style="")
    header.append(status, style=style)
    if executor.DRY_RUN:
        header.append("  ·  DRY-RUN  ·  ", style="bold")
    else:
        header.append("  ·  🔴 LIVE  ·  ", style="bold red")
    header.append(f"{len(config.MARKETS)} markets  ·  ")
    header.append("bankroll ")
    header.append(f"${committed:.0f}/${bankroll:.2f}",
                  style="yellow" if committed else "dim")
    header.append("  ·  realized ")
    header.append(f"${realized:+.2f}", style="green" if realized >= 0 else "red")
    header.append("  ·  unrealized ")
    header.append(f"${open_upnl:+.3f}", style="green" if open_upnl >= 0 else "red")
    header.append(f"  ·  {time.strftime('%H:%M:%S')}")

    layout = Layout()
    layout.split_column(
        Layout(Panel(header, border_style="blue"), size=3),
        Layout(Panel(open_t, title="Open positions (real, filled — money at risk)",
                     border_style="cyan"), ratio=3),
        Layout(Panel(watching_signals(),
                     title="Watching (fired, not filled — no position)",
                     border_style="yellow"), ratio=2),
        Layout(Panel(Group(stats_line(), Text(), closed_trades()),
                     title="Closed signals", border_style="cyan"), ratio=3),
        Layout(Panel(recent_log(), title="Recent activity (bot.log)",
                     border_style="cyan"), ratio=3),
    )
    return layout


def main() -> None:
    console = Console()
    if "--once" in sys.argv:
        console.print(render())
        return
    with Live(render(), console=console, screen=True, refresh_per_second=1) as live:
        while True:
            time.sleep(REFRESH_S)
            live.update(render())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
