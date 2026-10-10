# Sentinel

Autonomous crypto-perp + forex trading bot with a TradingView-style dashboard. Pure Python standard library (3.7+) on the server, plain JS in the browser. No build step, no paid services required.

## Run
Double-click `run.bat` (or `py server.py`) and open http://localhost:8765. It starts in **paper mode** and resumes by itself after restarts.
Tests: `py tests.py` (33 tests, none of which can touch a real AI API). CLI: `py sentinel.py help`.

## The dashboard
- **Trade**: live TradingView-style candlestick chart (TradingView Lightweight Charts) with the strategy's own levels, your entries/exits and stop, a watchlist with sparklines, and a plain-English status line. Dock: open trades, activity, history.
- **Portfolio**: account value with an animated number roll, equity curve (1H/1D/1W/All), insight text, KPIs, live safety meters.
- **Lab**: backtest, walk-forward optimize, whole-portfolio test. Verdict judged on unseen data.
- **AI**: analyst mode (Off/Advise/Veto), spending guardrails, live scorecard, validation table, decision log.
- **Settings**: account size + risk, safety limits, exchange link, markets. `Ctrl K` opens a command palette. Keys 1-5 switch pages.
- Motion: view transitions between pages, sliding pill indicators, per-digit number roll, origin-aware spring sheets, blur-rise toasts, skeleton loaders. All off under "reduce motion".
- The browser only ever talks to the local server, polls only while the tab is visible, and caches third-party files for a week.

## The AI analyst: how it spends tokens (DeepSeek docs applied)
- One request per **actionable** signal (after strategy + every risk check pass), never for blocked trades, never twice for the same market/candle/side.
- `deepseek-flash`, **thinking disabled** (thinking is on by default and bills hidden tokens), `max_tokens` 140, JSON reply.
- Prompt = static system text + static tool definitions first (byte-identical -> automatic context-cache hits at ~1/50th the miss price), compact dossier last.
- The dossier already contains the facts that matter (trend/volatility/momentum, BTC regime, funding rate + history, open-interest change, long/short ratio, your open book, recent results) so most signals need **one** request. Three read-only tools (`candles`, `derivs`, `journal`) cover anything else, capped at 2 rounds.
- Hard limits in Settings -> AI page: 120 calls/day, $0.25/day, $3 lifetime (defaults). Circuit breaker pauses AI for 10 min after 3 failures. Any failure fails open (trading continues).
- Cost is computed from the real `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` fields and peak/off-peak pricing, and shown live.
- **Advise** is the default: the rating is shown and every trade is tagged with it, so the AI page shows (for free) whether high-rated trades really do better. The earlier 6,800-signal historical test found that blocking trades on the AI's rating did *not* improve results (see `docs/ai_validation.json`); Veto is available but unproven.

## Measured results (not promises)
13 markets, 5 years of real data, 4h bars, after fees/slippage/funding: +55% total at 0.5% risk/trade (7.6%/yr, worst drawdown 19.6%, Sharpe 0.56); +109% at 1% (13.1%/yr, 29% drawdown). A modest edge, tuned on this same data, so expect worse live. More risk = more return *and* more drawdown, not more edge.

## Setup steps
1. `run.bat`. 2. Click the account chip to choose size/risk. 3. AI page: paste a DeepSeek key, Test connection. 4. Optional exchange paper trading: Bybit Demo Trading (email-only sign-up) -> API key (trade, no withdraw) -> Settings -> Exchange.

## Limits
- Exchange adapters (Bybit/Binance) pass mock-exchange tests and Bybit's real public rules but have never traded on a real account. Forex is built-in paper only (Yahoo prices, approximate).
- `data/` holds your database and API key. It is in `.gitignore`; keep it out of cloud-synced folders if you care about that.
- Past performance does not predict future results.
