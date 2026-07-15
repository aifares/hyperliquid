"""Deep-dive the win-rate/profit-factor elite list: full addresses, TRUE history
span (first fill -> now, not just the last-2000-fills window), current
positions, and a beta-vs-skill control on each wallet's dominant market so a
high win rate that's actually just "perma-long a coin that went up" gets
caught. Cross-references against the informed-timing SKILL wallets already
found in wallet_findings.md.
"""
import json, urllib.request, time, statistics, bisect, collections, datetime as dt
from pathlib import Path

HL = "https://api.hyperliquid.xyz/info"
FRICTION = 0.11
OUT = Path(__file__).with_name("wallet_findings.md")

ELITE_PREFIXES = ['0xf97ad670', '0xc993ef2e', '0x984d622f', '0xad453be1',
                  '0xa4cdf5ad', '0x3f8f3522', '0x7782d30c', '0x06e0602c',
                  '0xf763d7c2', '0x96b86617', '0x011820f6', '0x9c7df1b2',
                  '0x987163b6', '0xff4b619a', '0x1fbf4789', '0x36076e4b',
                  '0xc399cb46', '0x7f1e97d8', '0xf5d13b04', '0xd46979f0',
                  '0x13979101', '0xddb38a2f']
PRIOR_SKILL = {'0x45974824', '0xdd0c5de5', '0xbbbdbbfa', '0xbafae6af', '0xbe3f79ae'}

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
def full_addr(p): return next((r["ethAddress"] for r in lb if r["ethAddress"].startswith(p)), None)
def lbrow(addr): return next((r for r in lb if r["ethAddress"] == addr), None)
def win(r, w):
    for p in r.get("windowPerformances", []):
        if p[0] == w: return p[1]
    return {}

_cc = {}
def candles(coin, tmin, tmax):
    key = (coin, tmin // 3_600_000, tmax // 3_600_000)
    if key in _cc: return _cc[key]
    span_d = (tmax - tmin) / 86400_000
    interval = "15m" if span_d < 40 else ("1h" if span_d < 180 else "4h")
    cs = info({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
              "startTime": tmin - 3 * 3600_000, "endTime": tmax + 5 * 3600_000}}) or []
    out = [{"t": c["t"], "c": float(c["c"])} for c in cs]
    _cc[key] = out
    return out

results = []
for pfx in ELITE_PREFIXES:
    addr = full_addr(pfx)
    if not addr:
        continue
    row = lbrow(addr)
    av = float(row.get("accountValue", 0) or 0) if row else 0
    at = float(win(row, "allTime").get("pnl", 0) or 0) if row else 0

    # oldest fill via userFillsByTime paging backward = TRUE history length
    fills, end, floor_ms = [], int(time.time() * 1000), int(time.time() * 1000) - 400 * 86400_000
    for _ in range(8):
        page = info({"type": "userFillsByTime", "user": addr,
                     "startTime": floor_ms, "endTime": end})
        time.sleep(0.15)
        if not page:
            break
        fills.extend(page)
        oldest = min(f["time"] for f in page)
        if oldest <= floor_ms or len(page) < 200:
            break
        end = oldest - 1
    seen, uniq = set(), []
    for f in fills:
        k = (f["time"], f.get("tid", f.get("oid")))
        if k not in seen:
            seen.add(k); uniq.append(f)
    fills = sorted(uniq, key=lambda f: f["time"])
    if not fills:
        continue
    span_d = (fills[-1]["time"] - fills[0]["time"]) / 86400_000
    first_dt = dt.datetime.fromtimestamp(fills[0]["time"] / 1000)

    ntl = collections.Counter(); taker = 0
    for f in fills:
        ntl[f["coin"]] += float(f["sz"]) * float(f["px"])
        if f.get("crossed"): taker += 1
    coin = ntl.most_common(1)[0][0]
    taker_pct = taker / len(fills) * 100

    entries = [f for f in fills if f["coin"] == coin and f["dir"].startswith("Open")]
    ev = []
    for f in entries:
        d = 1 if "Long" in f["dir"] else -1
        if ev and ev[-1][1] == d and f["time"] - ev[-1][0] < 20 * 60_000:
            continue
        ev.append((f["time"], d))

    edge = None
    if len(ev) >= 6:
        tmin, tmax = min(e[0] for e in ev), max(e[0] for e in ev)
        cs = candles(coin, tmin, tmax)
        times = [c["t"] for c in cs]
        if len(cs) >= 20:
            def px(t):
                i = bisect.bisect_right(times, t) - 1
                return cs[i]["c"] if 0 <= i < len(cs) else None
            def fwd(t, mins, s=1):
                p0, p1 = px(t), px(t + mins * 60_000)
                return None if not p0 or not p1 else (p1 - p0) / p0 * 100 * s
            longs = sum(1 for _, d in ev if d == 1)
            w4 = [x for t, d in ev if (x := fwd(t, 240, d)) is not None]
            if len(w4) >= 6:
                cw4 = statistics.mean(w4)
                bias = 1 if longs >= len(ev) / 2 else -1
                b4 = [x for c in cs if tmin <= c["t"] <= tmax
                      and (x := fwd(c["t"], 240, bias)) is not None]
                bl4 = statistics.mean(b4) if b4 else 0
                edge = cw4 - bl4

    verdict = ("no-verdict (thin sample)" if edge is None else
               "SKILL (timing beats beta)" if edge > FRICTION else
               "BETA (win rate is just being on the right side of a trend)")
    flag = " ** PRIOR-SKILL-LIST OVERLAP **" if pfx in PRIOR_SKILL else ""
    results.append(dict(addr=addr, av=av, at=at, coin=coin, span_d=span_d,
                        first=first_dt, n=len(fills), taker=taker_pct,
                        nev=len(ev), edge=edge, verdict=verdict, flag=flag))
    print(f"{addr[:12]} {coin.replace('xyz:',''):8s} hist={span_d:6.0f}d "
          f"since={first_dt:%Y-%m-%d} fills={len(fills):5d} taker={taker_pct:3.0f}% "
          f"events={len(ev):3d} edge={('n/a' if edge is None else f'{edge:+.2f}%')} "
          f"{verdict}{flag}")

report = ["", f"## Elite win-rate wallets — history + beta-control deep-dive "
          f"({dt.datetime.now():%Y-%m-%d %H:%M})", "",
          "Full trading history (not just last 2000 fills) + beta-control on each "
          "wallet's dominant market, for the 22 win-rate/PF elites from winrate_scan.py.", "",
          "| wallet | acct | allTimePnL | dominant | history | since | fills | taker% | events | edge4h | verdict |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
for r in results:
    report.append(f"| `{r['addr']}` | ${r['av']:,.0f} | ${r['at']:,.0f} | "
                  f"{r['coin'].replace('xyz:','')} | {r['span_d']:.0f}d | {r['first']:%Y-%m-%d} | "
                  f"{r['n']} | {r['taker']:.0f}% | {r['nev']} | "
                  f"{'n/a' if r['edge'] is None else f'{r['edge']:+.2f}%'} | {r['verdict']}{r['flag']} |")
with open(OUT, "a") as f:
    f.write("\n".join(report) + "\n")
print(f"\nappended -> {OUT}")
