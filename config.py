"""Central configuration: loads .env and defines the watched markets."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))


def _req(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v


def _opt(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# --- Secrets -----------------------------------------------------------------
PERPLEXITY_API_KEY = _opt("PERPLEXITY_API_KEY")
ALPACA_API_KEY = _opt("ALPACA_API_KEY")       # real-time Benzinga news (free,
ALPACA_API_SECRET = _opt("ALPACA_API_SECRET")  # paper keys work — read-only feed)
TELEGRAM_API_ID = _opt("TELEGRAM_API_ID")
TELEGRAM_API_HASH = _opt("TELEGRAM_API_HASH")
TELEGRAM_BOT_TOKEN = _opt("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALERT_CHAT_ID = _opt("TELEGRAM_ALERT_CHAT_ID")
ANTHROPIC_API_KEY = _opt("ANTHROPIC_API_KEY")
FINNHUB_API_KEY = _opt("FINNHUB_API_KEY")
WALLET_ADDRESS = _opt("WALLET_ADDRESS")
HL_AGENT_PRIVATE_KEY = _opt("HL_AGENT_PRIVATE_KEY")

# --- Execution guardrails (semi-auto mode) -----------------------------------
TOTAL_BANKROLL = 40.0           # USD total margin budget across ALL open trades
                                # (raised 2026-07-14 from $17.5 as account grew)
MIN_MARGIN_PER_TRADE = 2.0      # below this leftover, skip rather than dust in
                                # ($2 @ 5x = $10 notional, the exchange minimum)
MAX_CONCURRENT_POSITIONS = 3    # bot won't open a 4th
DAILY_LOSS_LIMIT = 3            # stopped-out executed trades/day -> halt
PENDING_TRADE_TTL_S = 900       # Execute button expires after 15 min
AUTO_EXECUTE_DRY_RUN = True     # dry-run trades itself (tests sizing/budget)
AUTO_EXECUTE_LIVE = True        # live trades itself too — fully automated per
                                # user (2026-07-14); flip False for button-confirm

TELEGRAM_CHANNELS = [
    c.strip().lstrip("@")
    for c in _opt("TELEGRAM_CHANNELS").split(",")
    if c.strip()
]


# --- Markets -----------------------------------------------------------------
@dataclass(frozen=True)
class Market:
    coin: str            # exact Hyperliquid ws "coin" (e.g. "BTC" or "xyz:NVDA")
    label: str           # human label used in alerts
    max_leverage: int    # exchange cap
    # keywords that map an incoming news item to this market
    keywords: tuple[str, ...] = field(default_factory=tuple)


# Markets with real liquidity, re-checked live 2026-07-13 (24h vol / OI):
# NVDA $60M/$203M, META $59M/$59M, MU $270M/$185M, INTC $28M/$33M,
# MSFT $23M/$61M, TSLA $23M/$25M, AAPL $21M/$37M, AMD $19M/$10M,
# GOOGL $10M/$57M, AMZN $9M/$20M. Still too thin: PLTR/NFLX/COST (<$5M).
# Stock-focused per user preference; BTC kept as the single crypto market.
MARKETS: list[Market] = [
    Market("xyz:NVDA", "NVIDIA", 20,
           ("nvidia", "nvda", "jensen huang", "gpu", "ai chip", "datacenter")),
    Market("xyz:META", "Meta", 20,
           ("meta", "facebook", "instagram", "zuckerberg", "whatsapp", "llama")),
    Market("xyz:TSLA", "Tesla", 20,
           ("tesla", "tsla", "musk", "elon", "cybertruck", "robotaxi", "ev")),
    Market("xyz:AAPL", "Apple", 20,
           ("apple", "aapl", "iphone", "tim cook", "app store")),
    Market("xyz:MSFT", "Microsoft", 20,
           ("microsoft", "msft", "azure", "copilot", "satya nadella", "openai")),
    Market("xyz:GOOGL", "Alphabet", 20,
           ("google", "googl", "alphabet", "gemini", "youtube", "waymo")),
    Market("xyz:AMZN", "Amazon", 20,
           ("amazon", "amzn", "aws", "prime", "andy jassy")),
    Market("xyz:AMD", "AMD", 10,
           ("amd", "advanced micro", "lisa su", "ryzen", "instinct")),
    Market("xyz:MU", "Micron", 10,
           ("micron", "mu", "dram", "hbm", "memory chips", "nand")),
    Market("xyz:INTC", "Intel", 10,
           ("intel", "intc", "foundry", "x86")),
    Market("xyz:HOOD", "Robinhood", 10,
           ("robinhood", "hood", "vlad tenev", "retail brokerage",
            "retail trading", "commission-free")),
    # --- expanded universe (2026-07-16): most-liquid xyz builder-perps not
    # already watched, so the news engine can trade the ACTUAL name a headline
    # is about instead of a proxy (e.g. SK Hynix news mapped to MU before).
    # Vetted by 24h volume in the metaAndAssetCtxs survey; all excluded from
    # run-up (never backtested — see RUNUP_EXCLUDE). ---
    Market("xyz:SKHX", "SK Hynix", 10,
           ("sk hynix", "hynix", "skhynix", "hbm memory")),
    Market("xyz:SNDK", "SanDisk", 10,
           ("sandisk", "sndk", "nand flash", "flash storage")),
    Market("xyz:SMSN", "Samsung", 10,
           ("samsung", "smsn", "galaxy phone", "samsung electronics")),
    Market("xyz:SPCX", "SpaceX", 20,
           ("spacex", "spcx", "starship", "starlink", "raptor engine")),
    Market("xyz:CRCL", "Circle", 10,
           ("circle internet", "crcl", "usdc issuer", "stablecoin issuer")),
    Market("xyz:MRVL", "Marvell", 10,
           ("marvell", "mrvl", "marvell technology")),
    Market("xyz:NBIS", "Nebius", 10,
           ("nebius", "nbis", "ai cloud")),
    Market("xyz:ORCL", "Oracle", 10,
           ("oracle", "orcl", "larry ellison", "oracle cloud")),
    Market("xyz:XYZ100", "Nasdaq-100 (XYZ100)", 30,
           ("nasdaq", "ndx", "xyz100", "tech stocks", "qqq", "cpi", "fed",
            "rate", "inflation", "jobs report", "fomc")),
    Market("xyz:SP500", "S&P 500", 50,
           ("s&p", "sp500", "spx", "s and p", "500 index", "tariff", "gdp")),
    Market("BTC", "BTC", 40,
           ("btc", "bitcoin", "crypto", "etf inflow", "halving")),
]

MARKET_BY_COIN = {m.coin: m for m in MARKETS}
DEFAULT_LEVERAGE = 20   # scalp tier: hold minutes-hours, flat same session
SWING_LEVERAGE = 5      # swing tier: multi-day holds need liq 20%+ away

# --- Stop/target geometry (fixed %, NOT leverage-derived) ---------------------
# Replaces the old "half the liquidation distance" formula (0.5/leverage,
# i.e. 2.5% for a 20x scalp / 10% for a 5x swing): the geometry backtest
# (backtests/RESULTS.md) found those targets essentially unreachable in the
# tier's own hold window (scalp needed 5% raw in <4h, swing needed 20% raw in
# 3 days). New: scalp 0.5% stop / 1.0% target (2R), swing 3% stop / 6% target
# (2R) — target is still computed as 2R via watcher.target_price(), so only
# the stop fraction needs to change here. Applies to NEW entries only — an
# already-open trade keeps whatever stop was frozen into journal.signals.stop
# at its own entry time; see notifier.resolve_stop().
SCALP_STOP_RAW = 0.005
SWING_STOP_RAW = 0.03
# --- Volatility-aware stops (2026-07-17 audit) --------------------------------
# A fixed 3% stop treats AAPL (quiet) and SKHX (6% intraday on the Kospi rout)
# identically: SKHX gets noise-stopped, AAPL's stop is needlessly generous.
# Swing stops scale with the name's OWN daily ATR instead, clamped so a quiet
# tape can't produce a hair-trigger stop nor a wild one an unsurvivable stop.
# Sizing compensates inversely (executor: risk-constant margin) so every swing
# trade risks about the same $ regardless of which name it's in. Falls back
# to SWING_STOP_RAW when candle history is missing. New trades only — an open
# position's stop stays frozen at its entry-time value (see resolve_stop).
ATR_STOPS = _opt("ATR_STOPS", "1") == "1"
ATR_STOP_MULT = 1.5             # stop distance = 1.5 x 14d ATR%
ATR_STOP_MIN = 0.015            # never tighter than 1.5% raw
ATR_STOP_MAX = 0.05             # never wider than 5% raw
# --- Correlated-beta cap (2026-07-17 audit) -----------------------------------
# GOOGL short + SPCX short + AMD short is ONE short-tech-beta bet occupying
# three slots at 3x the intended factor risk — a Nasdaq bounce hits all of
# them at once. Cap same-direction positions across the correlated tech
# cluster (counted from LIVE exchange positions, any tier, manual included —
# factor exposure is factor exposure regardless of who opened it). Run-up is
# exempt: its long-into-earnings basket IS the validated strategy shape.
CORRELATED_TECH = {m.coin for m in MARKETS
                   if m.coin not in ("BTC",)}   # every equity/index perp
MAX_CORRELATED_SAME_DIR = 2
# --- Partial exits (2026-07-17 audit) -----------------------------------------
# 45 trades, ZERO hit the 2R target; all real winners exited via FADE at ~1R.
# Realized geometry was therefore symmetric (risk 1R to win 1R) at a 39% win
# rate — negative expectancy. Asymmetry fix: at +1R bank half, move the stop
# on the rest to breakeven (trade can no longer lose), and trail the runner
# 1R behind its best price toward a 3R target. Applies to watcher-managed
# tiers (swing/bigswing/rally); run-up keeps its validated exit shape.
PARTIAL_EXIT = _opt("PARTIAL_EXIT", "1") == "1"
PARTIAL_EXIT_FRAC = 0.5         # fraction closed at the 1R bank
PARTIAL_MIN_NOTIONAL = 22.0     # skip the partial if halving would leave less
                                # than the exchange's $10 order min per piece
PARTIAL_RUNNER_TARGET_R = 3.0   # runner rides toward 3R (was fixed 2R for all)
# --- Funding-aware entries (2026-07-17 research) ------------------------------
# Measured live: SKHX shorts pay ~1.1%/DAY funding (crowd-shorted Korea/memory
# complex, -417%/yr) — a multi-day swing there bleeds more in carry than its
# expected edge. If holding the direction costs > FUNDING_COSTLY_DAILY, demand
# extra conviction; > FUNDING_BLOCK_DAILY, refuse outright. Missing data never
# gates (funding.daily_cost returns None -> no adjustment).
FUNDING_COSTLY_DAILY = 0.003    # >0.3%/day against us: need +0.05 conviction
FUNDING_BLOCK_DAILY = 0.008     # >0.8%/day against us: block the swing entry
# --- Korea->US semi lead-lag (validated 2026-07-17, shadow-first) -------------
# 60d hourly backtest: Korea-session (00-07 UTC) move of SKHX+SMSN >=1% predicts
# same-direction US-RTH basket return on MU/NVDA/AMD/SNDK: +0.85%/day mean
# (+1.13% median, 66% win, n=35, both halves positive, works long AND short,
# net of measured 2bp fees, robust to 14:00 UTC entry). Runs in SHADOW mode
# (signal + telegram + jsonl, no orders) until forward-validated live.
KOREA_ENABLED = _opt("KOREA_ENABLED", "1") == "1"
KOREA_LIVE = _opt("KOREA_LIVE", "0") == "1"        # flip after shadow proof
KOREA_MIN_SIGNAL = 0.01         # |Korea session move| to arm
KOREA_SIGNAL_COINS = ["xyz:SKHX", "xyz:SMSN"]
KOREA_TRADE_COINS = ["xyz:MU", "xyz:NVDA", "xyz:AMD", "xyz:SNDK"]

# --- News-tier entry quality gates (2026-07-14, from live-trade review) --------
# Floors raised from scalp 0.5/0.3 + swing 0.6/0.4: every fired trade was
# hugging the old minimums, and the one that lost (MU short, conf 0.60) sat
# exactly on the swing floor while the one that won (NVDA, conf 0.65) cleared
# it. 0.65/0.40 keeps the winner and rejects the floor-huggers. With ~99% of
# signals already rejected by slots/tape, the few that trade should be the
# best few — capacity is the scarce resource, not ideas.
SCALP_MIN_CONF = 0.65
SCALP_MIN_MAG = 0.40
SWING_MIN_CONF = 0.70   # 0.65->0.70 (2026-07-17 audit): 47% of headlines were
SWING_MIN_MAG = 0.50    # scoring actionable — slots are the scarce resource,
                        # spend them on stronger reads (0.40->0.50 mag too)

# Never fade a strong same-day move: the 30s tape window is blind to daily
# trend (it read "flat" while MU was +6.7% on the day and let a short through
# on a misread headline). Blocks any news entry AGAINST a day move >= this %.
TREND_FILTER_PCT = 3.0

# Index scalps scratched or bled all session (recap headlines aren't
# catalysts; 20x round-trip fees eat the scratches). Scalp tier is single
# names only; swing on indexes stays allowed (macro theses are real).
SCALP_EXCLUDE = {"xyz:XYZ100", "xyz:SP500"}

# 24/7, NON-US-listed perps: SpaceX is private, SK Hynix + Samsung are Korean.
# They do NOT halt at the 16:00 ET US close and trade through "overnight" (SPCX
# crashed -10% at 18:00 ET on 2026-07-16). So the US-market-hours rules —
# RTH-only scalps, and the bigswing offhours-derisk that flattens before an
# overnight gap — are WRONG for them (they cost the SPCX short a correct win).
# These names are exempt from both; managed on price/tape/smart-money instead.
CONTINUOUS_MARKETS = {"xyz:SPCX", "xyz:SKHX", "xyz:SMSN"}

# Scalps must play out in liquid hours: the losing MU short opened 05:20 ET
# into a thin pre-market book and drifted to the 4h clock. Stock/index scalps
# only during NYSE RTH; BTC (natively 24/7) is exempt. Swings are exempt too —
# multi-day holds aren't hostage to the entry hour's liquidity.
SCALP_RTH_ONLY = True

# --- Earnings run-up tier (backtested: +1.59%/event net, 62% win, 10y; see
# backtests/RESULTS.md — at 5x: +10.6% EV on margin, 1% liquidation risk) ----
# Toggle via RUNUP_ENABLED in .env (user can hand earnings back to manual).
RUNUP_ENABLED = _opt("RUNUP_ENABLED", "0") == "1"
RUNUP_LEVERAGE = 5
RUNUP_ENTRY_TDAYS = 10          # trading days before the print to enter
RUNUP_STOP_RAW = 0.03           # -3% raw = -15% on margin hard stop
RUNUP_MAX_CONCURRENT = 4        # 2026-07-16: raised 2->4 to deploy idle capital
                                # into peak earnings season (INTC 7/23, MSFT/META
                                # 7/29, AAPL/AMZN 7/30, AMD 8/4) — more of the
                                # ONE validated edge, each slot independently
                                # sized+stopped (vs bigswing's all-in-on-one)
RUNUP_EXCLUDE = {"xyz:TSLA",                 # negative run-up expectancy in backtest
                 # 2026-07-16 expansion — never in the run-up backtest, news-only
                 "xyz:SKHX", "xyz:SNDK", "xyz:SMSN", "xyz:SPCX",
                 "xyz:CRCL", "xyz:MRVL", "xyz:NBIS", "xyz:ORCL",
                 "xyz:HOOD"}    # never backtested for run-up (added to MARKETS
                                # 2026-07-14 only for news monitoring of the
                                # user's manual position) — validate before funding

# News-veto exit: bail a held run-up EARLY if a genuinely bad, high-conviction
# catalyst lands on that exact ticker — before the -3% price stop would trip.
# UNVALIDATED tweak to a validated strategy (the +1.59%/event edge was pure
# calendar mechanics; 54% of WINNING run-ups dip -2.5% first, so the bar is set
# high to avoid whipsawing out of normal noise). Toggle via RUNUP_NEWS_EXIT.
RUNUP_NEWS_EXIT = _opt("RUNUP_NEWS_EXIT", "0") == "1"
RUNUP_NEWS_MIN_CONF = 0.80      # analyzer confidence floor for a veto
RUNUP_NEWS_MIN_MAG = 0.60       # analyzer magnitude floor (size of the move)

# Bad news on a HELD position — THREE-TIER, asymmetric response (2026-07-15).
# The old single bar (0.80/0.60) was HIGHER than the 0.65/0.40 ENTRY bar — i.e.
# it was harder for the bot to get OUT of a position than it was to get IN, so
# ordinary bad news just warned while the bot sat in the trade. That's backwards:
# protecting capital you already hold should be EASIER than risking new capital.
#   - EXIT   (>= these bars): full early close, any PnL. Lowered 0.80->0.70.
#   - PROTECT(>= these, below EXIT AND below entry): if in profit, ratchet the
#     stop to breakeven and stay in (non-destructive — can't turn into a loss
#     from here); if underwater, warn only (price stop / EXIT bar handle it).
#   - below PROTECT: warn only (a heads-up, never a "short now" instruction).
NEWS_EXIT_SCALP_SWING = _opt("NEWS_EXIT_SCALP_SWING", "0") == "1"
NEWS_EXIT_MIN_CONF = 0.70
NEWS_EXIT_MIN_MAG = 0.50
NEWS_PROTECT_MIN_CONF = 0.55
NEWS_PROTECT_MIN_MAG = 0.35

# --- Data-driven bankroll allocation (across ALL methods) ----------------------
# RUN-UP IS THE BACKBONE (2026-07-15): it is the ONLY edge validated end-to-end
# (+1.59%/event net, 62% win, n=389 over 10y), and the catalyst-continuation
# study (RESULTS.md) confirmed swing-alone is roughly break-even — a real but
# thin edge, not a growth engine. So the account grows on run-up; swing/rally
# are opportunistic fillers BETWEEN earnings windows, and scalp (weakest AND
# costliest — geometry study showed its targets near-unreachable) is trimmed
# to a token allocation. Shares recompute off the LIVE bankroll, so winners
# grow their own budgets and losers shrink — allocation self-adjusts.
#   runup 50% / swing 75% — user set runup=swing equal at 50/50 (2026-07-17),
#               then raised swing's concurrency to 3 slots and asked for the
#               budget to scale with it, so swing's share went 50->75% to
#               hold ~$10/slot at 3 concurrent instead of shrinking to
#               ~$6.67/slot. NOTE: this now gives swing MORE total budget
#               than run-up, even though run-up is still the only
#               end-to-end-validated edge (+1.59%/event) and swing backtests
#               roughly break-even — revisit if swing drags on PnL.
#   rally  5% — news + trend gate + tick-orderbook confirm
#   scalp  5% — token only; hasn't earned more (fee-heavy, geometry-broken)
TIER_BUDGET_FRAC = {"runup": 0.50, "swing": 0.75, "scalp": 0.0, "rally": 0.05}
# NOTE: fractions deliberately sum >1.0 (opportunistic over-subscription so
# capital never idles waiting for one tier) — executor.guardrail_block +
# allocate_margin enforce a GLOBAL cap so total committed margin can never
# exceed the bankroll; tiers race for the shared headroom first-come.
# swing raised to 3 slots (2026-07-17, user request) at the $40 bankroll's
# 75% share ($30 -> $10/slot, matching the old 2-slot size): the 3rd slot is
# real capacity, not just a number bump — guardrail_block() already checks
# live free exchange balance before every trade (see executor.py), so it
# only actually fires when margin isn't already tied up in run-up/bigswing.
# scalp stays at 1 — weakest tier, hasn't earned more concurrency yet;
# rally (news + trend + tick-orderbook) also one slot until it earns more
TIER_MAX_CONCURRENT = {"runup": 4, "swing": 3, "scalp": 0, "rally": 1}
# runup 2->4 (2026-07-17 audit): earnings_runup sizes its slots as share/
# RUNUP_MAX_CONCURRENT (=4) but this cap was still 2, so the validated edge
# could only ever deploy HALF its budget — the two constants must match.
# scalp -> 0 = tier killed (audit: realized-negative, fee-heaviest at
# 10-20x, geometry study showed targets near-unreachable). Scalp SIGNALS
# still analyze/alert; they just can't take real margin.
# per-name backtested mean net return per run-up event (% notional, 10y)
RUNUP_EDGE = {
    "xyz:AMD": 2.72, "xyz:MU": 2.54, "xyz:NVDA": 2.34, "xyz:GOOGL": 2.02,
    "xyz:META": 1.57, "xyz:AMZN": 1.56, "xyz:MSFT": 1.35, "xyz:AAPL": 1.19,
    "xyz:INTC": 0.91,
}
RUNUP_EDGE_MEAN = sum(RUNUP_EDGE.values()) / len(RUNUP_EDGE)   # ≈1.80


# --- Full-balance swing tier ("bigswing") -------------------------------------
# Standalone strategy: ONE position at a time, sized off nearly the full LIVE
# account balance (account_monitor.spot_available(), NOT the TOTAL_BANKROLL
# fractional pool the other tiers share), long or short, leverage scaled 5x-10x
# by a technical/orderbook conviction score (swing_signals.py) rather than the
# news+tape combiner. News is a secondary confirm/veto only (see bigswing.py).
#
# IMPORTANT — read backtests/RESULTS.md "All-in single-stock strategy" section
# before funding this for real: that study found the aspirational return
# target ("+15-20%/trade, full balance, repeat") UNSUPPORTED, and flagged
# 10x+ held overnight, repeated, as NOT SURVIVABLE (gaps >=10% happen ~1/261
# sessions/name). The overnight de-risk rule and hard equity stop below exist
# specifically to address that finding — do not remove them to "size up".
BIGSWING_ENABLED = _opt("BIGSWING_ENABLED", "0") == "1"
# Curated focus list + stop/conviction, VALIDATED by backtests/bigswing_bt.py
# (2026-07-15) — that script backtests the ACTUAL swing_signals.py entry
# logic (trend-slope + Donchian breakout; the sustained order-book-imbalance
# and liquidation-pressure votes aren't in daily-bar history, so they're
# untested here — see the module docstring) over 10y of daily bars per name,
# net of friction, WITH the real overnight de-risk mechanics simulated
# (fires ~50-70% of the time on these names — most stock-perp trades resolve
# in ~1-2 days, only BTC reliably runs multi-day; re-read the chat/RESULTS.md
# before assuming a longer typical hold). One-slot-across-all-coins portfolio
# sim on this curated set: ~82 trades/yr, 48.8% win, +0.35%/trade net raw
# (≈+1.8% to +3.5% equity at 5-10x) — small and repeatable, not a home run,
# consistent with every other tier's validated edge in this repo.
#   BTC       — 24/7, exempt from the overnight de-risk flatten (the ONLY name
#               here that can genuinely run the full multi-day swing/max hold);
#               backtested net +0.57%/trade, best of the set
#   xyz:HOOD  — backtested net +0.36%/trade (2nd best); requested addition
#   xyz:MU    — backtested net +0.11-0.22%/trade; best liquidity/OI of the
#               stock book ($270M/$185M); requested addition ("DRAM")
#   xyz:AMD   — backtested net +0.16-0.18%/trade
#   xyz:NVDA  — backtested net ~+0.07%/trade (thin edge, kept for liquidity)
#   xyz:TSLA  — backtested net +0.30-0.49%/trade on THIS signal (re-added:
#               the run-up tier's TSLA exclusion was a DIFFERENT strategy/
#               signal and does not transfer here)
# Dropped vs the prior list: xyz:META backtested NEGATIVE on this signal
# (-0.14% to -0.16%/trade); also excluded for the same reason: xyz:INTC,
# xyz:MSFT, xyz:AMZN (all net-negative-to-flat), xyz:GOOGL/xyz:AAPL (net
# ~flat, too thin to bother with in a one-slot-at-a-time design).
# BTC temporarily EXCLUDED from new entries (requested 2026-07-15: "no
# bitcoin only stocks for now") despite backtesting best of the set — a
# currently-open BTC position (opened before this change) is NOT affected,
# it keeps being managed normally to its own exit; this only blocks bigswing
# from opening a NEW BTC position. Re-add "BTC" to the set below to resume.
BIGSWING_MARKETS = [m for m in MARKETS if m.coin in
                    {"xyz:HOOD", "xyz:MU", "xyz:AMD", "xyz:NVDA", "xyz:TSLA"}]
BIGSWING_MIN_LEVERAGE = 5
BIGSWING_MAX_LEVERAGE = 10
BIGSWING_MIN_CONVICTION = 0.4                 # backtest sweep: 0.4 beat both 0.5
                                               # (prior default) and 0.3 net of
                                               # friction on the curated markets
BIGSWING_BTC_MIN_CONVICTION = 0.7             # BTC gets a HIGHER bar than the
                                               # stock names (requested 2026-07-15):
                                               # only trade it on a genuinely
                                               # high-conviction read, not the
                                               # general 0.4 floor. 0.7 is also
                                               # where the leverage curve starts
                                               # scaling past the 5x floor.
BIGSWING_STOP_RAW = 0.035                     # 3.5% raw stop — backtest sweep
                                               # beat 2.5% almost everywhere on
                                               # this signal/universe (2.5% was
                                               # tighter than these names' normal
                                               # daily noise, causing whipsaws)
BIGSWING_TARGET_R = 2.0                       # 2R target (= 7% raw at the stop above)
BIGSWING_BALANCE_BUFFER = 0.08                # leave 8% of free balance unallocated
                                               # (fees/funding/slippage headroom)
BIGSWING_EQUITY_STOP_PCT = 0.20               # hard safety net: force-flatten if
                                               # account equity drops >=20% from the
                                               # entry-time snapshot, regardless of
                                               # whether the resting price stop fired
BIGSWING_ADOPT_MANUAL = True                  # detect + manage a manually-opened
                                               # position too (stop/target + the
                                               # same de-risk/equity-stop rules)
BIGSWING_ADOPT_SKIP_IF_STOP_EXISTS = True     # don't double-bracket a manual trade
                                               # that already has a resting stop/tp
BIGSWING_PAUSE_OTHER_TIERS = True             # scalp/swing/runup/rally are NOT
                                               # started at all when bigswing is
                                               # on (main.py), AND executor
                                               # guardrails refuse their entries
                                               # — one wallet, one strategy, no
                                               # mixing. Flip to False only if
                                               # you deliberately want tiers to
                                               # share the account again.
# PAUSE bigswing WITHOUT abandoning an open position (2026-07-16, user request
# to de-risk): when set, bigswing still RESUMES and MANAGES a position it
# already holds (keeps the watcher, stop/target, de-risk rules) but opens NO
# new entries and adopts no manual ones — and its exclusivity is lifted so the
# diversified run-up-backbone tiers take back over once the open position
# closes and frees the margin. Cleaner than BIGSWING_ENABLED=0, which would
# orphan the live position (bracket stays on the exchange but nothing records
# its exit). Flip back to 0 to resume full bigswing.
BIGSWING_PAUSE_ENTRIES = _opt("BIGSWING_PAUSE_ENTRIES", "0") == "1"
BIGSWING_REENTRY_COOLDOWN_S = 2 * 3600        # after ANY bigswing exit on a coin,
                                               # wait this long before re-entering
                                               # the SAME coin (today's MU churn:
                                               # FADE profit @12:42 → reload 32s
                                               # later → gave the win back). Does
                                               # not block a DIFFERENT coin.
BIGSWING_REQUIRE_SECONDARY = True             # refuse trend-ONLY entries: need a
                                               # Donchian breakout OR a sustained
                                               # book-imbalance sample too. The
                                               # 11:03 / 13:08 MU shorts that
                                               # cleared the 0.4 bar on trend
                                               # alone (breakout=none, book n/a)
                                               # are exactly what this blocks.
BIGSWING_MAX_HOLD_HOURS = 7 * 24              # 7 days — longer than the news-tier swing
BIGSWING_SAMPLE_S = 30                        # entry-scan / adoption-check cadence
BIGSWING_TREND_DAYS = 10                      # trend-slope lookback (candles.py)
BIGSWING_TREND_STRONG_PCT = 8.0               # % move over BIGSWING_TREND_DAYS treated
                                               # as a full-strength trend vote
BIGSWING_BREAKOUT_DAYS = 20                   # Donchian breakout lookback
BIGSWING_CANDLE_REFRESH_S = 1800              # 30 min

# News is SECONDARY here (reuses the existing Claude pipeline via
# combiner.latest_read() — no second news ledger): a fresh, same-direction
# read nudges conviction up; a fresh, high-confidence OPPOSING read vetoes
# the entry outright. It never originates a bigswing trade by itself.
BIGSWING_NEWS_WINDOW_S = 4 * 3600             # how long a news read still counts
BIGSWING_NEWS_BOOST_MIN_CONF = 0.6            # confidence floor for a confirm boost
BIGSWING_NEWS_BOOST_AMOUNT = 0.15             # conviction added on confirmation
BIGSWING_NEWS_VETO_MIN_CONF = 0.75            # confidence floor for a hard veto

# --- News + orderbook + trend rally tier ("rally") ----------------------------
# Standalone, fractional-budget tier (NOT full-balance like bigswing). A
# Claude news catalyst ARMS a coin when per-asset + broad-market trend agree
# (or don't strongly oppose); entry only fires when the LIVE orderbook
# confirms tick-by-tick (not the 30s/15m sampled windows used elsewhere).
# Off by default — the tick-level half cannot be classically backtested
# (no historical L2), so run dry/shadow and review journal.rally_arm_stats()
# before flipping real orders on. See rally.py / rally_signals.py.
RALLY_ENABLED = _opt("RALLY_ENABLED", "0") == "1"
# Stock focus (no BTC for now — same scope preference as bigswing); includes
# names where a news-led momentum chase is plausibly liquid enough.
RALLY_MARKETS = [m for m in MARKETS if m.coin in
                 {"xyz:NVDA", "xyz:TSLA", "xyz:AMD", "xyz:MU", "xyz:HOOD",
                  "xyz:META", "xyz:AAPL", "xyz:MSFT", "xyz:GOOGL", "xyz:AMZN"}]
RALLY_LEVERAGE = 5
RALLY_STOP_RAW = 0.02                         # 2% raw stop (between scalp 0.5%
                                               # and swing 3% — momentum chase)
RALLY_TARGET_R = 2.0
RALLY_MAX_HOLD_HOURS = 24                     # flatten within a day; not a swing
RALLY_ARM_WINDOW_S = 30 * 60                  # how long a news read keeps a coin
                                               # armed waiting for book confirm
RALLY_NEWS_MIN_CONF = 0.60                    # Claude confidence floor to arm
RALLY_TREND_DAYS = 10                         # per-asset + broad-market lookback
RALLY_TREND_VETO_PCT = 4.0                    # if per-asset trend_slope opposes
                                               # the news direction by >= this %,
                                               # refuse to arm (don't fight the
                                               # name's own multi-day tape)
RALLY_BROAD_MARKET = "xyz:SP500"              # regime proxy for stock perps
RALLY_BROAD_MARKET_VETO = True                # also veto when the broad market
                                               # slope strongly opposes direction
RALLY_BOOK_IMBALANCE = 0.65                   # tick book side fraction to confirm
                                               # (long needs >= this, short <= 1-)
RALLY_FLOW_WINDOW_S = 5.0                     # short aggression window (trades)
RALLY_FLOW_MIN_RATIO = 0.60                   # buys/(buys+sells) for long confirm
                                               # (mirrors 1- for short)
RALLY_SAMPLE_S = 5                            # arming-sweep cadence (book ticks
                                               # themselves fire entries instantly)
RALLY_CANDLE_REFRESH_S = 1800                 # 30 min — shared candles module

# --- Smart-money watchlist (confirmation layer, see smartmoney.py) ------------
# Tracks the beta-controlled SKILL wallets + the $11M whale from wallet
# research. Annotates news alerts with how demonstrated-skill wallets are
# positioned on the same coin. Default = annotate/confirm only (NOT a gate);
# flip SMARTMONEY_VETO on later, after watching, to actually block entries the
# whale sits opposite to. Edges are thin (0.2-0.6%/4h) so validate before it
# ever touches the trade decision.
SMARTMONEY_ENABLED = _opt("SMARTMONEY_ENABLED", "1") == "1"
SMARTMONEY_VETO = _opt("SMARTMONEY_VETO", "0") == "1"
# WEIGHTED into the decision (2026-07-16): smart-money positioning nudges the
# signal's EFFECTIVE conviction up (confirm) / down (oppose), which then flows
# into BOTH the entry bar (an oppose can drag a marginal signal below the tier
# floor and block it) AND the position size (allocate_margin scales with
# conviction). Deltas are small and TOTAL-bounded so a thin edge (0.2-0.6%/4h)
# can only tip borderline calls, never override a strongly-convicted read.
SMARTMONEY_WEIGHTED = _opt("SMARTMONEY_WEIGHTED", "1") == "1"
SMARTMONEY_CONFIRM_SKILL = 0.05      # per skill wallet agreeing
SMARTMONEY_CONFIRM_WHALE = 0.10      # the $11M whale agreeing
SMARTMONEY_OPPOSE_SKILL = -0.08      # per skill wallet against
SMARTMONEY_OPPOSE_WHALE = -0.15      # the whale against (heaviest single input)
SMARTMONEY_MAX_UP = 0.12             # total confirm nudge cap
SMARTMONEY_MAX_DOWN = -0.18          # total oppose nudge cap

# --- Endpoints ---------------------------------------------------------------
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"

# --- Fees ---------------------------------------------------------------------
TAKER_FEE = 0.00045             # 0.045% of notional per side (market orders)
ROUND_TRIP_FEE = 2 * TAKER_FEE  # entry + exit; as fraction of notional

# --- Tuning ------------------------------------------------------------------
NEWS_POLL_SECONDS = 120         # how often the Perplexity poller runs (was 90;
                                # raised to cut Sonar credits ~25% — worst-case
                                # 2min news latency, and scalps are RTH-only now)
# Dedup: Perplexity re-serves one story reworded for hours (observed 19x/day
# for a single story) — each rewording used to cost a full Haiku call.
NEWS_DEDUP_WINDOW_S = 2700      # 45 min: how long a seen story suppresses lookalikes
NEWS_DEDUP_SIM = 0.75           # overlap coefficient (∩ ÷ smaller set) to count
                                # as the same story — 61% of headlines on the
                                # measured day; escalations of a story within the
                                # window are also suppressed, acceptable since
                                # ALERT_COOLDOWN gates same-thesis re-alerts anyway
TAPE_WINDOW_SECONDS = 30        # rolling window for trade-flow momentum
BOOK_IMBALANCE_THRESHOLD = 0.65 # fraction of top-of-book depth on one side
ALERT_COOLDOWN_SECONDS = 1800   # min gap between alerts for same coin+direction
ALERT_BURST_MAX = 3             # max full alerts per burst window (all markets) —
ALERT_BURST_WINDOW_S = 600      # one macro headline shouldn't fan out to 7 alerts

# --- Overnight crypto SHADOW mode ---------------------------------------------
# Stocks are dead when the US market is closed (thin xyz books -> the bot's
# RTH gates block them), so the account otherwise sits idle overnight. Crypto
# trades 24/7. The crypto backtest (RESULTS.md) found NO validated edge, so
# this is SHADOW ONLY: when a crypto news signal fires off-hours it is
# tape-confirmed, logged to shadow_crypto.jsonl with a 4h forward outcome, and
# announced on Telegram — but NEVER placed as a real order. Analyze the log
# after a couple weeks; only wire it to real money if it actually shows edge.
CRYPTO_NIGHT_SHADOW = _opt("CRYPTO_NIGHT_SHADOW", "1") == "1"
CRYPTO_COINS = {"BTC"}          # deepest 24/7 liquidity; altcoins tested worse
CRYPTO_SHADOW_HOLD_H = 4        # paper-hold horizon (matches wallet-research fwd4h)

# --- Reversal guard (per-coin signal ledger) ----------------------------------
# A same-coin direction FLIP within this window, against a recently confident
# read, needs extra conviction to fire — one headline shouldn't reverse a
# thesis that was just confirmed; that takes accumulating contradiction.
NEWS_REVERSAL_WINDOW_S = 7200        # 2h: how long a prior read still "counts"
NEWS_REVERSAL_PRIOR_MIN_CONF = 0.6   # the prior read must have been reasonably sure
NEWS_REVERSAL_MIN_CONF = 0.75        # the flipping signal must clear this bar
