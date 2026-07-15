"""Deeper hunt: rank wallets by TRUE realized win rate + profit factor from
reconstructed round-trip trades (not price proxies).

A 'trade' = position opens from flat and runs until it returns to flat/flips.
We accumulate each fill's closedPnl over that life -> one realized trade PnL.
This is the gold-standard win metric the leaderboard's aggregate PnL hides
(one $10M bet and 1000 small losses can look identical in total PnL).

Rank by: win_rate, profit_factor (gross wins / gross losses), expectancy,
with a hard minimum trade count for significance. Then the survivors get the
beta control in a later pass.
"""
import json, urllib.request, time, statistics, collections, datetime as dt
from pathlib import Path

HL = "https://api.hyperliquid.xyz/info"
SCAN_TOP = 320
MIN_TRADES = 15
OUT = Path(__file__).with_name("winrate_findings.md")

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
def wv(r, w):
    for p in r.get("windowPerformances", []):
        if p[0] == w: return p[1]
    return {}

# broaden the net: include mid-size wallets with strong RECENT performance,
# not just biggest all-time — an edge often shows in a smaller, sharper book
cands = []
for r in lb:
    av = float(r.get("accountValue", 0) or 0)
    at = float(wv(r, "allTime").get("pnl", 0) or 0)
    m30 = float(wv(r, "month").get("pnl", 0) or 0)
    wk = float(wv(r, "week").get("pnl", 0) or 0)
    # profitable all-time AND (strong month or strong week), any size 5k-5M
    if 5_000 <= av <= 5_000_000 and at > 10_000 and m30 > 0 and wk > 0:
        # rough return-on-account signal to favor efficient books
        roa = at / av if av else 0
        cands.append((r["ethAddress"], av, at, m30, roa))
# rank candidates by return-on-account (edge density), not raw PnL
cands.sort(key=lambda x: x[4], reverse=True)


def round_trips(fills):
    """Reconstruct realized round-trip trades per coin. Returns list of
    (coin, entry_ts, exit_ts, pnl, notional)."""
    trips = []
    bycoin = collections.defaultdict(list)
    for f in sorted(fills, key=lambda x: x["time"]):
        bycoin[f["coin"]].append(f)
    for coin, fs in bycoin.items():
        pos = 0.0          # signed size
        open_ts = None
        acc_pnl = 0.0
        acc_ntl = 0.0
        for f in fs:
            sz = float(f["sz"]) * (1 if f["dir"].endswith(("Long",)) or "Buy" in f["dir"] or f["side"] == "B" else -1)
            # use side: 'B' buy, 'A' sell
            b = f["side"] == "B"
            ssz = float(f["sz"]) * (1 if b else -1)
            cp = float(f.get("closedPnl", 0) or 0)
            if pos == 0 and ssz != 0:
                open_ts = f["time"]
            pos += ssz
            acc_pnl += cp
            acc_ntl += float(f["sz"]) * float(f["px"])
            if abs(pos) < 1e-9 and open_ts is not None:
                trips.append((coin, open_ts, f["time"], acc_pnl, acc_ntl))
                open_ts, acc_pnl, acc_ntl = None, 0.0, 0.0
    return trips


results = []
scanned = 0
for addr, av, at, m30, roa in cands[:SCAN_TOP]:
    fills = info({"type": "userFills", "user": addr}); time.sleep(0.18)
    if not fills or len(fills) < 20:
        continue
    scanned += 1
    trips = round_trips(fills)
    closed = [t for t in trips if t[3] != 0]
    if len(closed) < MIN_TRADES:
        continue
    pnls = [t[3] for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)
    wr = len(wins) / n * 100
    pf = (sum(wins) / abs(sum(losses))) if losses else 99.0
    exp = statistics.mean(pnls)
    # consistency: profitable in both halves
    mid = n // 2
    h1 = sum(pnls[:mid]); h2 = sum(pnls[mid:])
    consistent = h1 > 0 and h2 > 0
    ntl = collections.Counter(f["coin"] for f in fills)
    topmkt = ntl.most_common(1)[0][0]
    results.append(dict(addr=addr, av=av, at=at, n=n, wr=wr, pf=pf, exp=exp,
                        consistent=consistent, top=topmkt,
                        total=sum(pnls)))

# rank: consistent first, then win rate, then profit factor
results.sort(key=lambda r: (r["consistent"], r["wr"], r["pf"]), reverse=True)

lines = [f"# Win-rate / profit-factor deep scan — {dt.datetime.now():%Y-%m-%d %H:%M}", "",
         f"Scanned {scanned} return-on-account-ranked wallets (acct $5k-$5M, "
         f"+all-time, +30d, +7d). Reconstructed true round-trip trades from "
         f"realized closedPnl; kept {len(results)} with >={MIN_TRADES} closed trades.", "",
         "| wallet | acct | topMkt | trades | win% | profitFactor | expectancy$ | both-halves+ |",
         "|---|---|---|---|---|---|---|---|"]
for r in results:
    lines.append(f"| {r['addr'][:10]}… | ${r['av']:,.0f} | {r['top'].replace('xyz:','')} | "
                 f"{r['n']} | {r['wr']:.0f}% | {r['pf']:.2f} | ${r['exp']:,.0f} | "
                 f"{'yes' if r['consistent'] else 'no'} |")
OUT.write_text("\n".join(lines) + "\n")

# terse: the genuinely high win-rate + consistent ones
elite = [r for r in results if r["consistent"] and r["wr"] >= 60 and r["pf"] >= 1.5]
print(f"scanned={scanned} kept={len(results)} elite(wr>=60,pf>=1.5,consistent)={len(elite)}")
for r in results[:15]:
    print(f"  {r['addr'][:10]} {r['top'].replace('xyz:',''):7s} n={r['n']:3d} "
          f"wr={r['wr']:3.0f}% pf={r['pf']:4.1f} exp=${r['exp']:>8,.0f} "
          f"{'CONSIST' if r['consistent'] else '-'}")
print(f"elite addrs: {[r['addr'][:10] for r in elite]}")
print("full ->", OUT)
