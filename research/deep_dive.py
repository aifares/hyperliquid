"""Deep-dive the informed-signature candidates: bigger sample (page history
back), split-half robustness, net-of-cost forward capture, long/short mix,
and whether the edge is copyable (forward move must beat 0.11% friction)."""
import json, urllib.request, time, statistics, bisect, collections, datetime as dt
from pathlib import Path

HL = "https://api.hyperliquid.xyz/info"
FRICTION = 0.11  # % round trip
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

def full_addr(prefix):
    return next(r["ethAddress"] for r in lb if r["ethAddress"].startswith(prefix))

def all_fills(addr):
    """Last 2000 fills — the proven-aligning window (paging back further pulls
    timestamps predating the coin's candle history and breaks alignment)."""
    return info({"type": "userFills", "user": addr}) or []

def candles(coin, tmin, tmax):
    span_d = (tmax - tmin) / 86400_000
    interval = "15m" if span_d < 40 else "1h"   # stay under the ~5000-bar cap
    cs = info({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
              "startTime": tmin - 3 * 3600_000, "endTime": tmax + 5 * 3600_000}}) or []
    return [{"t": c["t"], "c": float(c["c"])} for c in cs]

def events_from(fills, coin):
    entries = [f for f in fills if f["coin"] == coin and f["dir"].startswith("Open")]
    ev = []
    for f in sorted(entries, key=lambda x: x["time"]):
        d = 1 if "Long" in f["dir"] else -1
        if ev and ev[-1][1] == d and f["time"] - ev[-1][0] < 20 * 60_000:
            continue
        ev.append((f["time"], d))
    return ev

report = ["", "## Deep-dive: informed candidates (%s)" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"), ""]
print_lines = []
for pfx, coin_short in TARGETS.items():
    addr = full_addr(pfx)
    fills = all_fills(addr)
    coin = f"xyz:{coin_short}"
    ev = events_from(fills, coin)
    if len(ev) < 5:
        # fall back to that wallet's actual dominant coin
        ntl = collections.Counter(f["coin"] for f in fills)
        coin = ntl.most_common(1)[0][0]
        ev = events_from(fills, coin)
    tmin, tmax = min(e[0] for e in ev), max(e[0] for e in ev)
    cs = candles(coin, tmin, tmax)
    times = [c["t"] for c in cs]
    def px(t):
        i = bisect.bisect_right(times, t) - 1
        return cs[i]["c"] if 0 <= i < len(cs) else None
    def mv(t, mins, s):
        p0, p1 = px(t), px(t + mins * 60_000)
        return None if not p0 or not p1 else (p1 - p0) / p0 * 100 * s
    longs = sum(1 for _, d in ev if d == 1)
    rows = [(t, d, mv(t, -60, d), mv(t, 60, d), mv(t, 240, d)) for t, d in ev]
    rows = [r for r in rows if r[2] is not None and r[3] is not None]
    n = len(rows)
    if n < 5:
        report += [f"### `{addr}` — {coin}",
                   f"- insufficient candle-aligned events ({n}); skipped", ""]
        print_lines.append(f"{addr[:10]} {coin.replace('xyz:','')} n={n} SKIP")
        continue
    pre = statistics.mean(r[2] for r in rows)
    f1 = statistics.mean(r[3] for r in rows)
    f4 = statistics.mean(r[4] for r in rows if r[4] is not None)
    hit1 = sum(1 for r in rows if r[3] > 0) / n * 100
    # split-half consistency
    mid = n // 2
    h1f = statistics.mean(r[3] for r in rows[:mid])
    h2f = statistics.mean(r[3] for r in rows[mid:])
    span_d = (tmax - tmin) / 86400_000
    net4 = f4 - FRICTION
    copyable = "YES" if (f4 > FRICTION and h1f > 0 and h2f > 0 and hit1 >= 55) else "no"
    report += [f"### `{addr}` — {coin}",
        f"- sample: {n} entry-events over {span_d:.0f} days · {longs} long / {n-longs} short",
        f"- move in entry dir: **pre1h {pre:+.2f}%** → **fwd1h {f1:+.2f}%** → **fwd4h {f4:+.2f}%**",
        f"- hit1h {hit1:.0f}% · split-half fwd1h [{h1f:+.2f}% | {h2f:+.2f}%]",
        f"- fwd4h net of {FRICTION}% friction: **{net4:+.2f}%** · copyable edge: **{copyable}**", ""]
    print_lines.append(f"{addr[:10]} {coin.replace('xyz:','')} n={n} {longs}L/{n-longs}S "
        f"pre{pre:+.2f} f1{f1:+.2f} f4{f4:+.2f} hit{hit1:.0f}% split[{h1f:+.2f}|{h2f:+.2f}] "
        f"net4{net4:+.2f} copyable={copyable}")

with open(OUT, "a") as f:
    f.write("\n".join(report) + "\n")
print("\n".join(print_lines))
print(f"appended -> {OUT}")
