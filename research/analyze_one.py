"""Full workup on a single pasted wallet: capital flow, market concentration,
taker/maker, per-trade PnL, and the informed-vs-momentum timing test with the
beta-vs-skill baseline control on its dominant market."""
import sys, json, urllib.request, time, statistics, bisect, collections, datetime as dt

HL = "https://api.hyperliquid.xyz/info"
ADDR = sys.argv[1] if len(sys.argv) > 1 else "0x9e8b1e51c642f4c8b87c6ba11c53d516a218afc4"
FRICTION = 0.11

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

print("wallet:", ADDR)
# leaderboard rank / pnl
try:
    lb = json.load(open("/tmp/lb.json"))["leaderboardRows"]
    row = next((r for r in lb if r["ethAddress"].lower() == ADDR.lower()), None)
    if row:
        def w(win):
            for p in row.get("windowPerformances", []):
                if p[0] == win: return float(p[1].get("pnl", 0) or 0)
            return 0
        print(f"leaderboard: acct ${float(row['accountValue']):,.0f} · allTimePnL ${w('allTime'):,.0f} · 30d ${w('month'):,.0f}")
    else:
        print("leaderboard: not in top rows")
except Exception as e:
    print("lb lookup failed:", e)

# current positions across both dexes
for dex in ("", "xyz"):
    body = {"type": "clearinghouseState", "user": ADDR}
    if dex: body["dex"] = dex
    d = info(body) or {}
    aps = d.get("assetPositions", [])
    if aps:
        av = d["marginSummary"]["accountValue"]
        print(f"[{dex or 'core'}] acctVal ${float(av):,.0f}:")
        for p in aps:
            q = p["position"]
            print(f"   {q['coin']:10s} sz={float(q['szi']):>12,.4f} entry={float(q['entryPx']):>10,.4f} "
                  f"{q['leverage']['value']}x uPnL=${float(q['unrealizedPnl']):+,.0f}")

fills = info({"type": "userFills", "user": ADDR}) or []
if not fills:
    print("no fills"); sys.exit()
fills.sort(key=lambda f: f["time"])
span = f"{dt.datetime.fromtimestamp(fills[0]['time']/1000):%m-%d %H:%M}->{dt.datetime.fromtimestamp(fills[-1]['time']/1000):%m-%d %H:%M}"
ntl = collections.Counter(); taker = 0
for f in fills:
    ntl[f["coin"]] += float(f["sz"])*float(f["px"])
    if f.get("crossed"): taker += 1
tot = sum(ntl.values()) or 1
pnl = sum(float(f.get("closedPnl", 0)) for f in fills)
print(f"\nfills: {len(fills)} ({span}) · taker {taker/len(fills)*100:.0f}% · closedPnL(sample) ${pnl:,.0f}")
print("top markets:", [(k.replace('xyz:',''), f"{v/tot*100:.0f}%") for k, v in ntl.most_common(5)])

# timing + beta control on dominant coin
coin = ntl.most_common(1)[0][0]
entries = [f for f in fills if f["coin"] == coin and f["dir"].startswith("Open")]
ev = []
for f in entries:
    d = 1 if "Long" in f["dir"] else -1
    if ev and ev[-1][1] == d and f["time"]-ev[-1][0] < 20*60_000: continue
    ev.append((f["time"], d))
print(f"\ndominant {coin}: {len(ev)} entry-events ({sum(1 for _,d in ev if d==1)}L/{sum(1 for _,d in ev if d==-1)}S)")
if len(ev) >= 5:
    tmin, tmax = min(e[0] for e in ev), max(e[0] for e in ev)
    span_d = (tmax-tmin)/86400_000
    itv = "15m" if span_d < 40 else "1h"
    cs = info({"type": "candleSnapshot", "req": {"coin": coin, "interval": itv,
              "startTime": tmin-3*3600_000, "endTime": tmax+5*3600_000}}) or []
    cs = [{"t": c["t"], "c": float(c["c"])} for c in cs]
    times = [c["t"] for c in cs]
    def px(t):
        i = bisect.bisect_right(times, t)-1
        return cs[i]["c"] if 0 <= i < len(cs) else None
    def mv(t, mins, s):
        p0, p1 = px(t), px(t+mins*60_000)
        return None if not p0 or not p1 else (p1-p0)/p0*100*s
    pre = [x for t, d in ev if (x := mv(t, -60, d)) is not None]
    f1 = [x for t, d in ev if (x := mv(t, 60, d)) is not None]
    f4 = [x for t, d in ev if (x := mv(t, 240, d)) is not None]
    if f4:
        bias = 1 if sum(1 for _,d in ev if d==1) >= len(ev)/2 else -1
        b4 = [x for c in cs if tmin <= c["t"] <= tmax and (x := mv(c["t"], 240, bias)) is not None]
        cw4, bl4 = statistics.mean(f4), (statistics.mean(b4) if b4 else 0)
        edge = cw4 - bl4
        cls = ("INFORMED/SKILL" if statistics.mean(f1) > 0 and edge > FRICTION else
               "BETA (just long the trend)" if edge <= 0.05 else "MOMENTUM/marginal")
        print(f"  pre1h {statistics.mean(pre):+.2f}% -> fwd1h {statistics.mean(f1):+.2f}% -> fwd4h {cw4:+.2f}%")
        print(f"  beta baseline fwd4h {bl4:+.2f}% -> timing edge {edge:+.2f}% -> {cls}")
    else:
        print("  no candle alignment")
else:
    print("  too few events for timing test")
