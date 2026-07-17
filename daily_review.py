"""Daily trade review — the automated 'check and review all trades' pass.

Produces a full picture of the last 24h and writes a dated report to
research/daily/YYYY-MM-DD.md, returning a compact Telegram-ready summary:
  - realized PnL + win rate for the day, per tier
  - every closed trade (entry->exit, reason, PnL net of fees)
  - open positions RIGHT NOW and whether each has resting protection
  - overnight-crypto shadow results accumulated so far
  - anomalies worth a human look (unprotected live position, kill-switch,
    analyzer errors, repeated stop-outs)

Run standalone (`python daily_review.py`) or via the bot's scheduled task
(main._daily_review_loop). Read-only except for the report file — never trades.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import config
import journal

_NY_OFFSET = -4 * 3600  # ET; display only
REPORT_DIR = Path(__file__).with_name("research") / "daily"
SHADOW_LOG = Path(__file__).with_name("shadow_crypto.jsonl")
BOT_LOG = Path(__file__).with_name("bot.log")


def _info(body: dict):
    req = urllib.request.Request(config.HL_INFO_URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _live_positions() -> list[dict]:
    if not config.WALLET_ADDRESS:
        return []
    out = []
    for dex in ("", "xyz"):
        body = {"type": "clearinghouseState", "user": config.WALLET_ADDRESS}
        if dex:
            body["dex"] = dex
        try:
            d = _info(body)
        except Exception:  # noqa: BLE001
            continue
        for p in d.get("assetPositions", []):
            q = p["position"]
            if float(q.get("szi", 0) or 0) != 0:
                out.append(q)
    return out


def _resting_by_coin() -> dict[str, int]:
    counts: dict[str, int] = {}
    if not config.WALLET_ADDRESS:
        return counts
    for dex in ("", "xyz"):
        body = {"type": "frontendOpenOrders", "user": config.WALLET_ADDRESS}
        if dex:
            body["dex"] = dex
        try:
            for o in _info(body):
                counts[o["coin"]] = counts.get(o["coin"], 0) + 1
        except Exception:  # noqa: BLE001
            pass
    return counts


def build_review() -> tuple[str, str]:
    """Returns (telegram_summary, full_markdown)."""
    now = time.time()
    since = now - 24 * 3600
    day = datetime.utcfromtimestamp(now).strftime("%Y-%m-%d")

    # --- closed trades in the last 24h (real: executed=1, exclude scratch) ---
    with journal._conn() as c:  # noqa: SLF001
        closed = c.execute(
            "SELECT coin, direction, horizon, leverage, entry, exit_px, "
            "exit_reason, margin, exit_ts FROM signals WHERE executed=1 "
            "AND exit_ts >= ? AND exit_reason NOT IN ('ORPHAN','') "
            "AND exit_reason IS NOT NULL ORDER BY exit_ts", (since,)).fetchall()

    lines = [f"# Daily review — {day}", "",
             f"_generated {datetime.utcfromtimestamp(now):%Y-%m-%d %H:%M} UTC_", ""]
    tg = [f"📊 <b>Daily review {day}</b>"]

    if closed:
        day_pnl = 0.0
        wins = 0
        per_tier: dict[str, list[float]] = {}
        lines += ["## Closed trades (last 24h)", "",
                  "| time | coin | dir | tier | entry→exit | reason | net$ |",
                  "|---|---|---|---|---|---|---|"]
        for coin, d, hz, lev, entry, xpx, reason, margin, xts in closed:
            _, mpct = journal.trade_pnl_pct(d, entry, xpx, lev)
            usd = (margin or 0) * mpct / 100
            day_pnl += usd
            wins += 1 if usd > 0 else 0
            per_tier.setdefault(hz, []).append(usd)
            t = datetime.utcfromtimestamp(xts).strftime("%H:%M")
            lines.append(f"| {t} | {coin.replace('xyz:','')} | {d} | {hz} | "
                         f"{entry:g}→{xpx:g} | {reason} | {usd:+.2f} |")
        n = len(closed)
        wr = wins / n * 100
        tier_str = " · ".join(f"{k} {sum(v):+.2f}" for k, v in per_tier.items())
        lines += ["", f"**{n} trades · win {wins}/{n} ({wr:.0f}%) · "
                  f"net PnL ${day_pnl:+.2f}**", f"per tier: {tier_str}", ""]
        tg.append(f"{n} trades · win {wr:.0f}% · <b>${day_pnl:+.2f}</b>")
        tg.append(f"tiers: {tier_str}")
    else:
        lines += ["## Closed trades (last 24h)", "", "_none_", ""]
        tg.append("no closed trades in 24h")

    # --- open positions + protection ---
    positions = _live_positions()
    resting = _resting_by_coin()
    lines += ["## Open positions now", ""]
    unprotected = []
    if positions:
        lines += ["| coin | side | entry | uPnL | lev | resting orders |",
                  "|---|---|---|---|---|---|"]
        for q in positions:
            coin = q["coin"]
            side = "long" if float(q["szi"]) > 0 else "short"
            prot = resting.get(coin, 0)
            if prot == 0:
                unprotected.append(coin)
            lines.append(f"| {coin.replace('xyz:','')} | {side} | {q['entryPx']} | "
                         f"${float(q['unrealizedPnl']):+.2f} | {q['leverage']['value']}x | "
                         f"{prot} |")
        lines.append("")
    else:
        lines += ["_flat_", ""]
    tg.append(f"open: {len(positions)} position(s)"
              + (f" · ⚠️ UNPROTECTED: {', '.join(c.replace('xyz:','') for c in unprotected)}"
                 if unprotected else ""))

    # --- shadow crypto results so far ---
    if SHADOW_LOG.exists():
        recs = [json.loads(l) for l in SHADOW_LOG.read_text().splitlines() if l.strip()]
        if recs:
            nets = [r["net_pct"] for r in recs]
            sw = sum(1 for x in nets if x > 0)
            avg = sum(nets) / len(nets)
            lines += ["## Overnight crypto shadow (cumulative, paper)", "",
                      f"- {len(recs)} shadow trades · win {sw}/{len(recs)} "
                      f"({sw/len(recs)*100:.0f}%) · avg net {avg:+.2f}%/trade", ""]
            tg.append(f"🌙 shadow: {len(recs)} trades, avg {avg:+.2f}%/trade")

    # --- anomalies from the log (last 24h) ---
    anomalies = []
    try:
        for l in BOT_LOG.read_text(errors="replace").splitlines()[-4000:]:
            if "KILL SWITCH" in l or "credit balance" in l or "order failed" in l.lower():
                anomalies.append(l[:160])
    except FileNotFoundError:
        pass
    if unprotected:
        anomalies.insert(0, f"UNPROTECTED live position(s): {', '.join(unprotected)}")
    lines += ["## Anomalies / needs attention", ""]
    lines += ([f"- {a}" for a in anomalies[:12]] if anomalies else ["- none"]) + [""]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"{day}.md").write_text("\n".join(lines))
    return "\n".join(tg), "\n".join(lines)


if __name__ == "__main__":
    summary, full = build_review()
    print(full)
    print("\n--- telegram summary ---\n" + summary)
