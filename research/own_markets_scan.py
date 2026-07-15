"""Final iteration: is there a copyable informed wallet on the markets WE
trade? Scans sharp-individual candidates whose dominant market is one of our
own names, runs timing + the beta-vs-skill baseline control in one pass, and
reports only wallets whose entries beat the asset's own drift (real edge)."""
import json, urllib.request, time, statistics, bisect, collections, datetime as dt
from pathlib import Path

HL = "https://api.hyperliquid.xyz/info"
OUR = {"xyz:NVDA","xyz:META","xyz:TSLA","xyz:AAPL","xyz:MSFT","xyz:GOOGL",
       "xyz:AMZN","xyz:AMD","xyz:MU","xyz:INTC","xyz:HOOD","xyz:XYZ100",
       "xyz:SP500","BTC"}
SCAN_TOP = 260
MIN_TAKER = 0.40
MIN_EVENTS = 6
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
            time.sleep(0.5 * (k + 1))
    return None

lb = json.load(open("/tmp/lb.json"))["leaderboardRows"]
def win(r, w):
    for p in r.get("windowPerformances", []):
        if p[0] == w: return p[1]
    return {}
cands = []
for r in lb:
    av = float(r.get("accountValue", 0) or 0)
    at = float(win(r, "allTime").get("pnl", 0) or 0)
    m30 = float(win(r, "month").get("pnl", 0) or 0)
    if 10_000 <= av <= 3_000_000 and at > 20_000 and m30 > 0:
        cands.append((r["ethAddress"], av, at))
cands.sort(key=lambda x: x[2], reverse=True)

_cc = {}
def candles(coin, tmin, tmax):
    if coin in _cc: return _cc[coin]
    cs = info({"type": "candleSnapshot", "req": {"coin": coin, "interval": "15m",
              "startTime": tmin - 3*3600_000, "endTime": tmax + 5*3600_000}}) or []
    _cc[coin] = [{"t": c["t"], "c": float(c["c"])} for c in cs]
    return _cc[coin]

results = []
scanned = 0
for addr, av, at in cands[:SCAN_TOP]:
    fills = info({"type": "userFills", "user": addr}); time.sleep(0.2)
    if not fills: continue
    scanned += 1
    ntl = collections.Counter(); taker = 0
    for f in fills:
        ntl[f["coin"]] += float(f["sz"])*float(f["px"])
        if f.get("crossed"): taker += 1
    coin = ntl.most_common(1)[0][0]
    if coin not in OUR or taker/len(fills) < MIN_TAKER:
        continue
    entries = [f for f in fills if f["coin"] == coin and f["dir"].startswith("Open")]
    ev = []
    for f in sorted(entries, key=lambda x: x["time"]):
        d = 1 if "Long" in f["dir"] else -1
        if ev and ev[-1][1] == d and f["time"] - ev[-1][0] < 20*60_000: continue
        ev.append((f["time"], d))
    if len(ev) < MIN_EVENTS: continue
    tmin, tmax = min(e[0] for e in ev), max(e[0] for e in ev)
    cs = candles(coin, tmin, tmax)
    if len(cs) < 20: continue
    times = [c["t"] for c in cs]
    def px(t):
        i = bisect.bisect_right(times, t)-1
        return cs[i]["c"] if 0 <= i < len(cs) else None
    def fwd(t, mins, s=1):
        p0, p1 = px(t), px(t+mins*60_000)
        return None if not p0 or not p1 else (p1-p0)/p0*100*s
    longs = sum(1 for _, d in ev if d == 1)
    w4 = [x for t, d in ev if (x := fwd(t, 240, d)) is not None]
    if len(w4) < MIN_EVENTS: continue
    cw4 = statistics.mean(w4)
    # beta baseline: sign-weighted by the wallet's net direction bias
    bias = 1 if longs >= len(ev)/2 else -1
    b4 = [x for c in cs if tmin <= c["t"] <= tmax and (x := fwd(c["t"], 240, bias)) is not None]
    bl4 = statistics.mean(b4) if b4 else 0
    edge4 = cw4 - bl4
    results.append((addr, av, at, coin, len(ev), f"{longs}L/{len(ev)-longs}S",
                    cw4, bl4, edge4))

results.sort(key=lambda x: x[8], reverse=True)
skill = [r for r in results if r[8] > FRICTION]
report = ["", "## Own-markets scan — informed wallets on the names WE trade "
          f"({dt.datetime.now():%Y-%m-%d %H:%M})", "",
          f"Scanned {scanned}; {len(results)} taker-concentrated in our markets "
          f"with >={MIN_EVENTS} events. Beta-controlled (entry fwd4h vs asset drift).", ""]
for r in results:
    v = "SKILL" if r[8] > FRICTION else ("BETA" if r[8] <= 0.05 else "marginal")
    report.append(f"- `{r[0][:10]}…` {r[3].replace('xyz:','')} n={r[4]} {r[5]} · "
                  f"entryFwd4h {r[6]:+.2f}% vs baseline {r[7]:+.2f}% = edge {r[8]:+.2f}% → {v}")
with open(OUT, "a") as f:
    f.write("\n".join(report) + "\n")
print(f"scanned={scanned} our-market-concentrated={len(results)} SKILL={len(skill)}")
for r in results[:10]:
    v = "SKILL" if r[8] > FRICTION else ("BETA" if r[8] <= 0.05 else "marg")
    print(f"  {r[0][:10]} {r[3].replace('xyz:',''):7s} n={r[4]:2d} {r[5]:8s} "
          f"fwd4h{r[6]:+.2f} base{r[7]:+.2f} edge{r[8]:+.2f} {v}")
print("appended ->", OUT)
