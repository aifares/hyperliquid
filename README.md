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
- **🌊💰 Full-balance swing tier ("bigswing", `BIGSWING_ENABLED`):** a separate,
  fully-automated strategy — ONE position at a time sized off nearly the
  whole live account balance, long or short, leverage scaled 5x-10x by a
  technical/orderbook conviction score (`swing_signals.py`: multi-day trend +
  Donchian breakout + sustained book imbalance + liquidation pressure), with
  the existing news pipeline as a secondary confirm/veto only. Disabled by
  default (`BIGSWING_ENABLED=0`) — see "Full-balance swing tier" below before
  turning it on.
- **🚀💰 News+orderbook+trend rally tier ("rally", `RALLY_ENABLED`):** a
  separate fractional-budget tier — Claude news ARMS a coin when per-asset +
  broad-market trend agree, then entry fires the instant the live orderbook
  confirms tick-by-tick (`rally_signals.py` + `HLStream.on_book`). Disabled
  by default; tick-L2 cannot be classically backtested — run dry/shadow and
  check `journal.rally_arm_stats()` before real orders.

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
| `candles.py` | live daily OHLC fetch/cache — trend, Donchian breakout, ATR |
| `swing_signals.py` | bigswing's technical/orderbook conviction scorer |
| `bigswing.py` | full-balance swing tier: entry engine + manual-position adoption |
| `rally_signals.py` | rally tier: news+trend arm + tick-book confirmation |
| `rally.py` | rally tier driver (fractional budget) |

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

## Full-balance swing tier ("bigswing")

A standalone, fully-automated tier — independent of the scalp/swing/runup
tiers above, which keep sharing `TOTAL_BANKROLL`. **Read
`backtests/RESULTS.md`'s "All-in single-stock strategy" section before
enabling this with real money**: that study tested almost this exact idea
("full-balance, one stock at a time, repeat") and found the aspirational
return target unsupported, and flagged repeated 10x+ overnight holds as not
survivable. The guardrails below exist specifically to address that finding
— don't disable them to "size up".

- **Off by default.** Set `BIGSWING_ENABLED=1` in `.env` and restart the bot
  to turn it on. With no `HL_AGENT_PRIVATE_KEY`, it runs in the same
  DRY-RUN mode as everything else (journaled, no real orders) — **run it in
  dry-run for a stretch and check `journal.summary()` before going live.**
- **Sizing:** one position at a time, margin = nearly all of
  `account_monitor.spot_available()` (not the shared `TOTAL_BANKROLL`).
  Whenever bigswing is enabled, the scalp/swing/runup tiers are paused
  entirely — not just while bigswing happens to hold a fill — so they can
  never compete for the same real margin (`BIGSWING_PAUSE_OTHER_TIERS`).
- **Signal:** primary trigger is technical/orderbook (`swing_signals.py`),
  not news — the existing Claude pipeline only nudges conviction up on
  agreement or vetoes an entry on a strongly opposing, high-confidence read
  (`combiner.latest_read()`).
- **Leverage:** 5x-10x (`BIGSWING_MIN_LEVERAGE`/`BIGSWING_MAX_LEVERAGE`),
  scaled by conviction.
- **Guardrails:**
  - Overnight de-risk: a stock-perp position not up ≥1R flattens once in the
    last 15 minutes of NYSE RTH (`market_hours.closing_soon()`) rather than
    holding through the overnight/weekend gap.
  - Hard equity safety net: force-closes regardless of the resting price
    stop if live account equity ever drops `BIGSWING_EQUITY_STOP_PCT` below
    its entry-time snapshot — a backstop for a gap that jumps past the
    resting stop.
  - Manual-position adoption (`BIGSWING_ADOPT_MANUAL`): if you open a
    position yourself on a watched coin, bigswing detects and manages it
    too (same de-risk + equity-stop rules; skips adding its own stop/target
    if you already have a resting one, `BIGSWING_ADOPT_SKIP_IF_STOP_EXISTS`).

## News + orderbook + trend rally tier ("rally")

Standalone fractional-budget tier (shares `TOTAL_BANKROLL`; never full-balance).
**Off by default** (`RALLY_ENABLED=0`). Tick-level L2 history isn't available,
so this cannot be classically backtested end-to-end — the news+trend gate has
a historical proxy in `backtests/rally_trend_bt.py`, and live/shadow outcomes
live in `journal.rally_arm_stats()`.

- **Arm:** a fresh Claude news read (`combiner.latest_read`) with confidence
  ≥ `RALLY_NEWS_MIN_CONF`, while per-asset and broad-market
  (`RALLY_BROAD_MARKET`, default `xyz:SP500`) trend slopes don't strongly
  oppose (`RALLY_TREND_VETO_PCT`).
- **Fire:** `HLStream.on_book` confirms tick-by-tick — book imbalance past
  `RALLY_BOOK_IMBALANCE` AND short-window trade flow past
  `RALLY_FLOW_MIN_RATIO`, same direction as the arm.
- **Size/risk:** `RALLY_LEVERAGE` (5x), `RALLY_STOP_RAW` (2%), 2R target,
  max hold `RALLY_MAX_HOLD_HOURS` (24h). One concurrent slot.
- While `BIGSWING_ENABLED` + `BIGSWING_PAUSE_OTHER_TIERS`, rally entries are
  blocked (confirms still journal as `confirmed_blocked` for validation).

## Risk notes

- At 20x, ~5% against you = liquidation; at 40x, ~2.5%. Every alert carries a stop
  and the liq price. **This tool never places orders** — you trade manually.
- Your edge is slower-burn news, not front-running scheduled prints (CPI/FOMC) —
  HFT reprices those in milliseconds; this pipeline is seconds.
- **Run shadow mode and check `journal.hit_rate()` before trusting it with money.**
# hyperliquid
