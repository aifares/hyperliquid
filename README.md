# Hyperliquid News Notifier

A leveraged-perp **buy/sell notifier** (not an auto-trader) for Hyperliquid.
It fuses live news with live order-flow and pings your Telegram when they agree.

- **Data spine:** Hyperliquid websocket — tick-by-tick trades, order book, BBO.
  Stock-focused basket (liquidity-checked): NVDA, META, TSLA, AAPL (20x),
  XYZ100 (30x), SP500 (50x), BTC (40x) as the single crypto market.
- **News:** your Telegram channels (Telethon) + Perplexity Sonar real-time search.
- **Brain:** Claude (Haiku 4.5) classifies each news item → asset, direction,
  magnitude, confidence. Event-driven, never tick-by-tick (that would be absurdly
  costly/slow).
- **Fast tier:** pure-code tape signals (flow imbalance, book imbalance, liquidations).
- **Two alert tiers:**
  - ⚡ **Scalp (20x):** immediate catalysts, hold minutes–hours, flat same
    session. Fires only when Claude's news read **and** the live tape agree.
  - 🌊 **Swing (5x):** multi-day theses (guidance, product cycles). Higher
    news-conviction bar; tape is context, not a gate. 5x because at 20x a
    normal 3%-daily-range stock liquidates you, and funding (NVDA ~43% APR on
    notional) compounds brutally on margin over days.
  - Unconfirmed ideas become low-priority heads-up notes.
- **Full buy→sell cycle:** after every entry alert a position watcher
  (`watcher.py`) follows the price and sends an EXIT alert on: 🛑 stop hit,
  🎯 2R target reached, 📉 momentum fade (tape flips hard against a winning
  trade), or ⏰ time stop (4h for scalps, 72h for swings).
- **Off-hours tagging:** stock perps trade 24/7 but NYSE doesn't — alerts
  outside 9:30–16:00 ET carry a thin-book/gap-risk warning. After-hours
  earnings repricing is a genuine edge window for this bot.
- **Journal:** every alert logged to SQLite with price at +1/+5/+30 min so you can
  measure hit-rate in shadow mode before risking money.

## Architecture

```
Telegram channels ─(Telethon)─┐
Perplexity Sonar ──(poller)───┤─► event queue ─► Claude analyzer ─┐
                              │                                   ▼
Hyperliquid WS ─► tick engine ─────────────► tape signals ─► combiner ─► Telegram alert
                 (BTC + xyz perps)                                     └► SQLite journal
```

## Files

| File | Role |
|------|------|
| `config.py` | env + watched markets (only ≥20x markets) |
| `hl_stream.py` | Hyperliquid websocket tick engine |
| `tape.py` | fast-tier order-flow signals |
| `tg_reader.py` / `tg_login.py` | Telegram channel reader + one-time login |
| `news.py` | Perplexity Sonar poller |
| `analyzer.py` | Claude news→signal classifier |
| `combiner.py` | merge news+tape, cooldown, decide |
| `notifier.py` | Telegram alert formatting + send |
| `journal.py` | SQLite outcome tracking |
| `main.py` | orchestrator |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# fill in .env (ANTHROPIC_API_KEY still needed)
.venv/bin/python tg_login.py     # one-time: phone + code, creates notifier.session
```

## Run

```bash
# Shadow mode first — reasons out loud, sends NO alerts. Run this for days.
SHADOW_MODE=1 .venv/bin/python main.py

# Live mode — sends alerts to your Telegram.
.venv/bin/python main.py
```

## Component self-tests

Each module runs standalone: `.venv/bin/python hl_stream.py` (live ticks),
`tape.py`, `news.py`, `notifier.py` (sends a sample alert), `journal.py`,
`analyzer.py` (schema check / live once key is set).

## Risk notes

- At 20x, ~5% against you = liquidation; at 40x, ~2.5%. Every alert carries a stop
  and the liq price. **This tool never places orders** — you trade manually.
- Your edge is slower-burn news, not front-running scheduled prints (CPI/FOMC) —
  HFT reprices those in milliseconds; this pipeline is seconds.
- **Run shadow mode and check `journal.hit_rate()` before trusting it with money.**
# hyperliquid
