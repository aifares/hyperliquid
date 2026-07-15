"""Extended workup for the $11M whale: page fills back ~2 weeks, decluster
per market, run timing + beta control on each market with enough events."""
import json, urllib.request, time, statistics, bisect, collections, datetime as dt
from pathlib import Path

HL = "https://api.hyperliquid.xyz/info"
ADDR = "0x9e8b1e51c642f4c8b87c6ba11c53d516a218afc4"
FRICTION = 0.11
OUT = Path(__file__).with_name("wallet_findings.md")

def info(body, tries=3):
    for k in range(tries):
        try:
            req = urllib.request.Request(HL, data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.load(r)
        except Exception:
            time.sleep(0.6 * (k + 1))
    return None

# page back up to ~12k fills / 21 days
fills, end = [], int(time.time() * 1000)
floor_ms = end - 21 * 86400_000
for _ in range(6):
    page = info({"type": "userFillsByTime", "user": ADDR,
                 "startTime": floor_ms, "endTime": end})
    if not page: break
    fills.extend(page)
    oldest = min(f["time"] for f in page)
    if oldest <= floor_ms or len(page) < 100: break
    end = oldest - 1
    time.sleep(0.3)
seen, uniq = set(), []
for f in fills:
    k = (f["time"], f.get("tid", f.get("oid")))
    if k not in seen:
        seen.add(k); uniq.append(f)
fills = sorted(uniq, key=lambda f: f["time"])
span_d = (fills[-1]["time"] - fills[0]["time"]) / 86400_000 if fills else 0
print(f"fills paged: {len(fills)} over {span_d:.1f} days")
pnl = sum(float(f.get("closedPnl", 0)) for f in fills)
fees = sum(float(f.get("fee", 0)) for f in fills)
print(f"closedPnL ${pnl:,.0f} · fees ${fees:,.0f}")

ntl = collections.Counter()
for f in fills:
    ntl[f["coin"]] += float(f["sz"]) * float(f["px"])
tot = sum(ntl.values()) or 1
print("markets:", [(k.replace('xyz:',''), f"{v/tot*100:.0f}%") for k, v in ntl.most_common(6)])

lines = [f"", f"## Whale workup `{ADDR[:12]}…` ({dt.datetime.now():%Y-%m-%d %H:%M})",
         f"", f"$11M acct · $24.5M all-time · +$5.1M/30d · 100% taker · "
         f"{len(fills)} fills/{span_d:.1f}d paged · closedPnL ${pnl:,.0f} · "
         f"currently SHORT ~$11M SPCX (uPnL +$1.67M)", ""]

for coin, v in ntl.most_common(4):
    if v / tot < 0.02: continue
    entries = [f for f in fills if f["coin"] == coin and f["dir"].startswith("Open")]
    ev = []
    for f in entries:
        d = 1 if "Long" in f["dir"] else -1
        if ev and ev[-1][1] == d and f["time"] - ev[-1][0] < 20 * 60_000: continue
        ev.append((f["time"], d))
    if len(ev) < 5:
        line = f"- {coin.replace('xyz:','')}: only {len(ev)} entry-events — too few, no verdict"
        print(line); lines.append(line); continue
    tmin, tmax = min(e[0] for e in ev), max(e[0] for e in ev)
    itv = "15m" if (tmax - tmin) / 86400_000 < 40 else "1h"
    cs = info({"type": "candleSnapshot", "req": {"coin": coin, "interval": itv,
              "startTime": tmin - 3 * 3600_000, "endTime": tmax + 5 * 3600_000}}) or []
    cs = [{"t": c["t"], "c": float(c["c"])} for c in cs]
    if len(cs) < 20:
        lines.append(f"- {coin}: no candles"); continue
    times = [c["t"] for c in cs]
    def px(t):
        i = bisect.bisect_right(times, t) - 1
        return cs[i]["c"] if 0 <= i < len(cs) else None
    def mv(t, mins, s):
        p0, p1 = px(t), px(t + mins * 60_000)
        return None if not p0 or not p1 else (p1 - p0) / p0 * 100 * s
    pre = [x for t, d in ev if (x := mv(t, -60, d)) is not None]
    f1 = [x for t, d in ev if (x := mv(t, 60, d)) is not None]
    f4 = [x for t, d in ev if (x := mv(t, 240, d)) is not None]
    if not f4 or not pre:
        lines.append(f"- {coin}: alignment failed"); continue
    longs = sum(1 for _, d in ev if d == 1)
    bias = 1 if longs >= len(ev) / 2 else -1
    b4 = [x for c in cs if tmin <= c["t"] <= tmax and (x := mv(c["t"], 240, bias)) is not None]
    bl4 = statistics.mean(b4) if b4 else 0.0
    cw4, edge = statistics.mean(f4), statistics.mean(f4) - (statistics.mean(b4) if b4 else 0)
    hit = sum(1 for x in f1 if x > 0) / len(f1) * 100
    cls = ("INFORMED/SKILL" if statistics.mean(f1) > 0 and edge > FRICTION else
           "BETA" if edge <= 0.05 else "MOMENTUM/marginal")
    line = (f"- {coin.replace('xyz:',''):7s} n={len(ev)} {longs}L/{len(ev)-longs}S · "
            f"pre1h {statistics.mean(pre):+.2f}% fwd1h {statistics.mean(f1):+.2f}% "
            f"fwd4h {cw4:+.2f}% · baseline {bl4:+.2f}% · edge {edge:+.2f}% · hit {hit:.0f}% → {cls}")
    print(line); lines.append(line)

with open(OUT, "a") as f:
    f.write("\n".join(lines) + "\n")
print("appended ->", OUT)
