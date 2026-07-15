"""Smart-money scanner: sweep taker-heavy leaderboard wallets through the
informed-vs-momentum timing test, hunting for a copyable edge.

Method (per wallet):
  - fetch fills; keep wallets concentrated (>=50% notional) in xyz STOCK perps
    and taker-leaning (aggressive = crossing the spread to get in)
  - decluster entry-fills into events (same dir within 20 min = one scaled entry)
  - for each entry event, measure price move IN THE ENTRY DIRECTION over the
    1h BEFORE (already-happened = chasing) vs 1h/4h AFTER (follows entry = early)
  - classify INFORMED (fwd1h>0 and fwd1h>pre1h), MOMENTUM (pre1h>fwd1h), MIXED

Candle data cached per coin (many wallets share dominant coins) to bound calls.
Full per-wallet detail -> research/wallet_findings.md; terse summary to stdout.
"""
import json, urllib.request, time, collections, statistics, bisect, datetime as dt
from pathlib import Path

HL = "https://api.hyperliquid.xyz/info"
LB = "/tmp/lb.json"
OUT = Path(__file__).with_name("wallet_findings.md")
SCAN_TOP = 220          # candidates to fetch fills for
MIN_XYZ_SHARE = 0.50
MIN_TAKER = 0.40
MIN_EVENTS = 5          # below this, no verdict


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


# candidate wallets: sharp individuals, profitable now + all-time
lb = json.load(open(LB))["leaderboardRows"]
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

_candle_cache = {}
def candles(coin):
    if coin in _candle_cache: return _candle_cache[coin]
    now = int(time.time() * 1000)
    cs = info({"type": "candleSnapshot", "req": {"coin": coin, "interval": "15m",
              "startTime": now - 45 * 86400_000, "endTime": now}}) or []
    cs = [{"t": c["t"], "c": float(c["c"])} for c in cs]
    _candle_cache[coin] = (cs, [c["t"] for c in cs])
    return _candle_cache[coin]


def timing(coin, entries):
    cs, times = candles(coin)
    if len(cs) < 20: return None
    def px(t):
        i = bisect.bisect_right(times, t) - 1
        return cs[i]["c"] if 0 <= i < len(cs) else None
    events = []
    for f in sorted(entries, key=lambda x: x["time"]):
        d = 1 if "Long" in f["dir"] else -1
        if events and events[-1][1] == d and f["time"] - events[-1][0] < 20 * 60_000:
            continue
        events.append((f["time"], d))
    def mv(t, mins, s):
        p0, p1 = px(t), px(t + mins * 60_000)
        return None if not p0 or not p1 else (p1 - p0) / p0 * 100 * s
    pre = [m for e in events if (m := mv(e[0], -60, e[1])) is not None]
    f1 = [m for e in events if (m := mv(e[0], 60, e[1])) is not None]
    f4 = [m for e in events if (m := mv(e[0], 240, e[1])) is not None]
    if len(f1) < MIN_EVENTS: return None
    pre1, fwd1, fwd4 = statistics.mean(pre), statistics.mean(f1), statistics.mean(f4)
    hit = sum(1 for m in f1 if m > 0) / len(f1) * 100
    cls = ("INFORMED" if fwd1 > 0 and fwd1 > pre1 else
           "MOMENTUM" if pre1 > fwd1 else "MIXED")
    return dict(n=len(events), pre1=pre1, fwd1=fwd1, fwd4=fwd4, hit=hit, cls=cls)


results = []
scanned = 0
for addr, av, at in cands[:SCAN_TOP]:
    fills = info({"type": "userFills", "user": addr})
    time.sleep(0.2)
    if not fills: continue
    scanned += 1
    ntl = collections.Counter()
    taker = 0
    for f in fills:
        ntl[f["coin"]] += float(f["sz"]) * float(f["px"])
        if f.get("crossed"): taker += 1
    tot = sum(ntl.values()) or 1
    xyz_share = sum(v for k, v in ntl.items() if k.startswith("xyz:")) / tot
    taker_pct = taker / len(fills)
    coin = ntl.most_common(1)[0][0]
    if xyz_share < MIN_XYZ_SHARE or taker_pct < MIN_TAKER or not coin.startswith("xyz:"):
        continue
    entries = [f for f in fills if f["coin"] == coin and f["dir"].startswith("Open")]
    tm = timing(coin, entries)
    if not tm: continue
    pnl = sum(float(f.get("closedPnl", 0)) for f in fills)
    results.append(dict(addr=addr, av=av, at=at, coin=coin, xyz=xyz_share,
                        taker=taker_pct, pnl=pnl, **tm))

# rank: informed first, by forward-capture
results.sort(key=lambda r: (r["cls"] == "INFORMED", r["fwd1"]), reverse=True)
cls_counts = collections.Counter(r["cls"] for r in results)

# --- write full findings ---
lines = [f"# Taker-wallet timing sweep — {dt.datetime.now():%Y-%m-%d %H:%M}", "",
         f"Scanned {scanned} of top {SCAN_TOP} sharp-individual candidates "
         f"(acct $10k-$3M, +all-time, +30d, from {len(cands)} such wallets / "
         f"40,487 total). Kept {len(results)} that are >=50% xyz-stock, "
         f">=40% taker, with >={MIN_EVENTS} declustered entry-events.", "",
         f"**Class counts:** {dict(cls_counts)}", "",
         "Timing test: price move IN ENTRY DIRECTION, 1h before vs 1h/4h after.",
         "INFORMED = move follows entry (fwd1h>0 and >pre1h). MOMENTUM = move precedes entry.",
         "", "| wallet | acct | allTimePnL | topMkt | xyz% | taker% | events | pre1h | fwd1h | fwd4h | hit% | class |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
for r in results:
    lines.append(f"| {r['addr'][:10]}… | ${r['av']:,.0f} | ${r['at']:,.0f} | "
                 f"{r['coin'].replace('xyz:','')} | {r['xyz']*100:.0f} | {r['taker']*100:.0f} | "
                 f"{r['n']} | {r['pre1']:+.2f} | {r['fwd1']:+.2f} | {r['fwd4']:+.2f} | "
                 f"{r['hit']:.0f} | {r['cls']} |")
informed = [r for r in results if r["cls"] == "INFORMED"]
lines += ["", "## Informed-signature wallets (the ones worth a deeper look)", ""]
if informed:
    for r in informed:
        lines.append(f"- `{r['addr']}` — {r['coin']}, {r['n']} events, "
                     f"fwd1h {r['fwd1']:+.2f}% vs pre1h {r['pre1']:+.2f}%, hit {r['hit']:.0f}%")
else:
    lines.append("- NONE. Every qualifying wallet chases (move precedes entry) or is mixed.")
OUT.write_text("\n".join(lines) + "\n")

# --- terse stdout ---
print(f"scanned={scanned} kept={len(results)} classes={dict(cls_counts)}")
print("top by fwd1h capture:")
for r in results[:12]:
    print(f"  {r['addr'][:10]}… {r['coin'].replace('xyz:',''):7s} n={r['n']:2d} "
          f"pre{r['pre1']:+.2f} fwd1h{r['fwd1']:+.2f} fwd4h{r['fwd4']:+.2f} hit{r['hit']:.0f}% {r['cls']}")
print(f"INFORMED wallets: {len(informed)} -> {[r['addr'][:10] for r in informed]}")
print(f"full detail -> {OUT}")
