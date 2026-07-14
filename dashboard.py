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


def _age(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600}h"


def open_positions(mids: dict[str, float]) -> tuple[Table, float]:
    rows = query(
        "SELECT id, ts, coin, direction, horizon, leverage, entry, executed "
        "FROM signals WHERE exit_reason IS NULL OR exit_reason = '' "
        "ORDER BY ts DESC")
    t = Table(expand=True, header_style="bold cyan", border_style="dim")
    for col in ("id", "coin", "dir", "tier", "lev", "entry", "now",
                "stop", "target", "raw", "PnL@lev", "age", "exec"):
        t.add_column(col, justify="right" if col not in ("coin", "dir", "tier") else "left")
    total_pnl = 0.0
    for sid, ts, coin, d, hz, lev, entry, executed in rows:
        mid = mids.get(coin, 0.0)
        if hz == "runup":
            stop = entry * (1 - config.RUNUP_STOP_RAW)
            tgt = 0.0            # exit is the earnings calendar, not a price
        else:
            stop = notifier.stop_price(entry, d, lev)
            tgt = watcher.target_price(entry, stop, d)
        if mid:
            raw, pnl = journal.trade_pnl_pct(d, entry, mid, lev)  # net of fees
        else:
            raw = pnl = 0.0
        total_pnl += pnl
        t.add_row(
            str(sid), coin.replace("xyz:", ""),
            Text(d.upper(), style="green" if d == "long" else "red"),
            hz, f"{lev}x", f"{entry:g}", f"{mid:g}" if mid else "?",
            f"{stop:g}", f"{tgt:g}" if tgt else "📅", _pnl_text(raw), _pnl_text(pnl),
            _age(ts), "SIM" if executed else "—")
    if not rows:
        t.add_row(*["—"] * 13)
    return t, total_pnl


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
             "TIME": "⏰ TIME", "RESTART": "↻ RESTART"}.get(reason, reason),
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


def render() -> Layout:
    mids = fetch_mids()
    status, style = bot_status()
    open_t, open_pnl = open_positions(mids)

    committed = query(
        "SELECT COALESCE(SUM(margin),0) FROM signals "
        "WHERE executed=1 AND (exit_reason IS NULL OR exit_reason='')")[0][0]
    realized = journal.realized_pnl_usd()
    bankroll = max(0.0, config.TOTAL_BANKROLL + realized)
    header = Text()
    header.append(" ", style="")
    header.append(status, style=style)
    header.append("  ·  DRY-RUN  ·  ", style="bold")
    header.append(f"{len(config.MARKETS)} markets  ·  ")
    header.append("bankroll ")
    header.append(f"${committed:.0f}/${bankroll:.2f}",
                  style="yellow" if committed else "dim")
    header.append("  ·  realized ")
    header.append(f"${realized:+.2f}", style="green" if realized >= 0 else "red")
    header.append("  ·  open PnL@lev ")
    header.append(f"{open_pnl:+.2f}%", style="green" if open_pnl >= 0 else "red")
    header.append(f"  ·  {time.strftime('%H:%M:%S')}")

    layout = Layout()
    layout.split_column(
        Layout(Panel(header, border_style="blue"), size=3),
        Layout(Panel(open_t, title="Open signals (paper-tracked; exec=SIM/LIVE only if Executed)",
                     border_style="cyan"), ratio=3),
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
