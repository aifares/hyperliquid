"""Beta-vs-skill control for the informed candidates.

Both are ~100% long a single ripping name (SK Hynix). A perma-long in an
uptrend shows positive forward returns with ZERO timing skill — it's just
beta. Real edge = their entry-conditioned forward return must beat the
asset's UNCONDITIONAL forward drift (what a random long at any bar would get)
over the same window. Edge = conditioned_fwd - baseline_fwd.
"""
import json, urllib.request, time, statistics, bisect
from pathlib import Path

HL = "https://api.hyperliquid.xyz/info"
TARGETS = {"0x45974824": "SKHY", "0xd0640fcb": "SKHX"}
OUT = Path(__file__).with_name("wallet_findings.md")

def info(body, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(HL, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            time.sleep(0.5 * (k + 1))
    return None

lb = json.load(open("/tmp/lb.json"))["leaderboardRows"]
def full_addr(p): return next(r["ethAddress"] for r in lb if r["ethAddress"].startswith(p))

report = ["", "## Beta-vs-skill control (the decisive test)", ""]
out_lines = []
for pfx, coin_short in TARGETS.items():
    addr = full_addr(pfx)
    coin = f"xyz:{coin_short}"
    fills = info({"type": "userFills", "user": addr}) or []
    entries = [f for f in fills if f["coin"] == coin and f["dir"].startswith("Open")]
    ev = []
    for f in sorted(entries, key=lambda x: x["time"]):
        d = 1 if "Long" in f["dir"] else -1
        if ev and ev[-1][1] == d and f["time"] - ev[-1][0] < 20 * 60_000:
            continue
        ev.append((f["time"], d))
    if not ev:
        continue
    tmin, tmax = min(e[0] for e in ev), max(e[0] for e in ev)
    cs = info({"type": "candleSnapshot", "req": {"coin": coin, "interval": "15m",
              "startTime": tmin - 3 * 3600_000, "endTime": tmax + 5 * 3600_000}}) or []
    cs = [{"t": c["t"], "c": float(c["c"])} for c in cs]
    times = [c["t"] for c in cs]
    def px(t):
        i = bisect.bisect_right(times, t) - 1
        return cs[i]["c"] if 0 <= i < len(cs) else None
    # wallet conditioned forward (raw, long perspective — all long anyway)
    def fwd(t, mins):
        p0, p1 = px(t), px(t + mins * 60_000)
        return None if not p0 or not p1 else (p1 - p0) / p0 * 100
    w1 = [x for t, _ in ev if (x := fwd(t, 60)) is not None]
    w4 = [x for t, _ in ev if (x := fwd(t, 240)) is not None]
    # baseline: unconditional forward at EVERY bar within the window
    b1 = [x for c in cs if tmin <= c["t"] <= tmax and (x := fwd(c["t"], 60)) is not None]
    b4 = [x for c in cs if tmin <= c["t"] <= tmax and (x := fwd(c["t"], 240)) is not None]
    cw1, cw4 = statistics.mean(w1), statistics.mean(w4)
    bl1, bl4 = statistics.mean(b1), statistics.mean(b4)
    edge1, edge4 = cw1 - bl1, cw4 - bl4
    # win rate vs baseline win rate
    verdict = ("SKILL" if edge4 > 0.11 and edge1 > 0 else
               "BETA (no timing edge over just being long)" if edge4 <= 0.05 else "MARGINAL")
    report += [f"### `{addr}` — {coin}  ({len(ev)} entries, all-long single name)",
        f"- wallet entry fwd:   1h {cw1:+.2f}% · 4h {cw4:+.2f}%",
        f"- asset baseline fwd: 1h {bl1:+.2f}% · 4h {bl4:+.2f}%  (random long, same window)",
        f"- **timing edge over beta: 1h {edge1:+.2f}% · 4h {edge4:+.2f}% → {verdict}**", ""]
    out_lines.append(f"{addr[:10]} {coin_short} entryFwd4h {cw4:+.2f}% vs baseline {bl4:+.2f}% "
                     f"= edge {edge4:+.2f}% -> {verdict}")

with open(OUT, "a") as f:
    f.write("\n".join(report) + "\n")
print("\n".join(out_lines))
print("appended ->", OUT)
