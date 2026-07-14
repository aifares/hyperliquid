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
TELEGRAM_API_ID = _opt("TELEGRAM_API_ID")
TELEGRAM_API_HASH = _opt("TELEGRAM_API_HASH")
TELEGRAM_BOT_TOKEN = _opt("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALERT_CHAT_ID = _opt("TELEGRAM_ALERT_CHAT_ID")
ANTHROPIC_API_KEY = _opt("ANTHROPIC_API_KEY")
FINNHUB_API_KEY = _opt("FINNHUB_API_KEY")
WALLET_ADDRESS = _opt("WALLET_ADDRESS")
HL_AGENT_PRIVATE_KEY = _opt("HL_AGENT_PRIVATE_KEY")

# --- Execution guardrails (semi-auto mode) -----------------------------------
TOTAL_BANKROLL = 17.5           # USD total margin budget across ALL open trades
                                # (live 2026-07-14: $17.71 deposited, small buffer)
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

# --- Earnings run-up tier (backtested: +1.59%/event net, 62% win, 10y; see
# backtests/RESULTS.md — at 5x: +10.6% EV on margin, 1% liquidation risk) ----
# Toggle via RUNUP_ENABLED in .env (user can hand earnings back to manual).
RUNUP_ENABLED = _opt("RUNUP_ENABLED", "0") == "1"
RUNUP_LEVERAGE = 5
RUNUP_ENTRY_TDAYS = 10          # trading days before the print to enter
RUNUP_STOP_RAW = 0.03           # -3% raw = -15% on margin hard stop
RUNUP_MAX_CONCURRENT = 2        # own cap (was 4; shrunk for the $17.5 bankroll)
RUNUP_EXCLUDE = {"xyz:TSLA"}    # negative run-up expectancy in backtest

# News-veto exit: bail a held run-up EARLY if a genuinely bad, high-conviction
# catalyst lands on that exact ticker — before the -3% price stop would trip.
# UNVALIDATED tweak to a validated strategy (the +1.59%/event edge was pure
# calendar mechanics; 54% of WINNING run-ups dip -2.5% first, so the bar is set
# high to avoid whipsawing out of normal noise). Toggle via RUNUP_NEWS_EXIT.
RUNUP_NEWS_EXIT = _opt("RUNUP_NEWS_EXIT", "0") == "1"
RUNUP_NEWS_MIN_CONF = 0.80      # analyzer confidence floor for a veto
RUNUP_NEWS_MIN_MAG = 0.60       # analyzer magnitude floor (size of the move)

# Same news-veto exit, generalized to scalp/swing: a held position with no
# tape-driven exit for LOSING trades (FADE only protects winners; STOP is a
# big price move away) otherwise has nothing but the clock to save it from a
# thesis that's gone bad. Same conservative bars as the run-up veto — this is
# still an early-exit override, not a new trading signal, so it stays strict.
NEWS_EXIT_SCALP_SWING = _opt("NEWS_EXIT_SCALP_SWING", "0") == "1"
NEWS_EXIT_MIN_CONF = 0.80
NEWS_EXIT_MIN_MAG = 0.60

# --- Data-driven bankroll allocation (across ALL methods) ----------------------
# Weighted by evidence (backtests/RESULTS.md):
#   runup 50% — the only validated edge: +10.6% EV on margin/event at 5x,
#               62% win over 389 events, ~1% ruin risk
#   swing 30% — unproven but structurally favored: 0.55% friction per trade,
#               multi-day horizon matches how news actually gets priced in
#   scalp 20% — unproven AND costly: 1.8% of margin per round trip at 20x,
#               geometry study showed current targets nearly unreachable.
# Shares recompute off the LIVE bankroll, so tiers that win grow their own
# budgets and tiers that lose shrink — allocation self-adjusts with results.
TIER_BUDGET_FRAC = {"runup": 0.50, "swing": 0.30, "scalp": 0.20}
# one slot per news tier at the $17.5 live bankroll — a $2.60 half-slot would
# sit under the exchange's $10 minimum notional at 5x
TIER_MAX_CONCURRENT = {"runup": 2, "swing": 1, "scalp": 1}
# per-name backtested mean net return per run-up event (% notional, 10y)
RUNUP_EDGE = {
    "xyz:AMD": 2.72, "xyz:MU": 2.54, "xyz:NVDA": 2.34, "xyz:GOOGL": 2.02,
    "xyz:META": 1.57, "xyz:AMZN": 1.56, "xyz:MSFT": 1.35, "xyz:AAPL": 1.19,
    "xyz:INTC": 0.91,
}
RUNUP_EDGE_MEAN = sum(RUNUP_EDGE.values()) / len(RUNUP_EDGE)   # ≈1.80


# --- Endpoints ---------------------------------------------------------------
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"
HL_INFO_URL = "https://api.hyperliquid.xyz/info"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"

# --- Fees ---------------------------------------------------------------------
TAKER_FEE = 0.00045             # 0.045% of notional per side (market orders)
ROUND_TRIP_FEE = 2 * TAKER_FEE  # entry + exit; as fraction of notional

# --- Tuning ------------------------------------------------------------------
NEWS_POLL_SECONDS = 90          # how often the Perplexity poller runs
TAPE_WINDOW_SECONDS = 30        # rolling window for trade-flow momentum
BOOK_IMBALANCE_THRESHOLD = 0.65 # fraction of top-of-book depth on one side
ALERT_COOLDOWN_SECONDS = 1800   # min gap between alerts for same coin+direction
ALERT_BURST_MAX = 3             # max full alerts per burst window (all markets) —
ALERT_BURST_WINDOW_S = 600      # one macro headline shouldn't fan out to 7 alerts

# --- Reversal guard (per-coin signal ledger) ----------------------------------
# A same-coin direction FLIP within this window, against a recently confident
# read, needs extra conviction to fire — one headline shouldn't reverse a
# thesis that was just confirmed; that takes accumulating contradiction.
NEWS_REVERSAL_WINDOW_S = 7200        # 2h: how long a prior read still "counts"
NEWS_REVERSAL_PRIOR_MIN_CONF = 0.6   # the prior read must have been reasonably sure
NEWS_REVERSAL_MIN_CONF = 0.75        # the flipping signal must clear this bar
