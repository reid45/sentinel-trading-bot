# What actually works: the strategy study

Everything the bot trades by default comes from this study. It was run on **real Binance perpetual-futures candles** (3 years of hourly data
for 40 coins, 2 years of 15-minute data for 12), with **fees, slippage and funding charged on every trade**, and each result is judged on data
the parameters never saw. All scripts are in [`research/`](../research) so anyone can reproduce or challenge the numbers.

> **Plain-English summary.** Short-term trading (15 minutes to 1 hour) **lost money after costs in every test**, including the popular
> Smart-Money / ICT ideas (liquidity sweeps, fair value gaps, order blocks). Slower **4-hour trend-following** earned a small, consistent profit
> across many coins. So the bot trades the slow system on many coins at once (more trades through breadth, not speed), keeps the fast AI day
> trader as a clearly-labelled experiment, and shows its own evidence before every AI decision.
> These are historical results, not promises: the edge is small, drawdowns of 10 to 20 percent are normal, and the future can differ.

## Method

1. **Costs are real.** Taker fee 0.05% + slippage 0.03% per side (about 0.16% round trip) plus funding. Stops are checked inside each candle and win ties against targets.
2. **Walk-forward.** Parameters are chosen on the first 60% of each market; the last 40% is the test. Pooled across markets.
3. **Robustness over best-case.** For every family we also ask: of *all* settings that looked good in-sample, how many stayed positive out-of-sample?
4. **Fresh coins.** The final settings were fixed from 12 coins, then run unchanged on 28 coins they never saw.
5. **Shared account.** Portfolio tests use one account with the live engine's rules: max open trades, leverage cap, risk per trade, fees (`tradebot/backtest.py:portfolio_run`).

## Result 1: speed destroys the edge

Pooled over 12 coins, out-of-sample, per-coin return at 0.5% risk per trade (`research/study.py`, `research/study2.py`):

| Family | 15m (4 coins, 2 years) | 1h (12 coins, 3 years) | 4h (12 coins, 3 years) |
|--------|-----|----|----|
| Liquidity sweep (stop hunt) | **1 of 708** settings profitable in-sample; best one lost 1.9% out-of-sample | 0 of 288 | - |
| Fair value gap retest | 0 of 768; OOS **-7% to -10%** | 0 of 384; OOS -8% | - |
| Order block retest | 0 of 768; OOS -2% to -3% | 0 of 384; OOS -1% to -4% | - |
| Donchian breakout | - | OOS **-2.2%** median (0 of 23 stayed positive) | **+1.6%** (33 of 33 stayed positive) |
| Breakout + higher-timeframe trend | - | OOS -0.8% (6 of 19 positive) | **+1.3%** (39 of 39 positive) |
| EMA cross + ADX | - | OOS -3.8% (0 of 11) | **+0.6%** (19 of 23 positive) |
| Volatility squeeze breakout | - | -0.5% (0 of 4) | +0.1% (11 of 20) |
| Range mean-reversion (Bollinger + RSI) | - | 0 of 288 profitable | 1 of 275 |
| RSI pullback in trend | - | -7% | -3% (0 of 26) |

Raw sweeps on BTC 15m win only 29% of trades at 2R (break-even is about 36%): the patterns are not predictive on their own. Filters (trend, session,
equal-highs, volume climax, structure confirmation) did not rescue them out-of-sample.

The AI day trader's own trigger set tells the same story. `tradebot/tools.py` measures it live per coin: on SOL over the last ~30 days every
trigger lost about **0.2R per trade** after fees, following or fading.

## Result 2: breadth beats speed

The four survivors (Donchian 20, Donchian 40, trend-filtered breakout, EMA 20/150), each run as an independent **sleeve** on every coin, on one shared account
(`research/ensemble_study.py`, 40 coins, two separate time windows):

| Setup (risk per trade, max open) | Earlier window | **Out-of-sample window** | Max drawdown | Sharpe | Trades per week |
|----------------------------------|----------------|--------------------------|--------------|--------|-----------------|
| One system alone (Donchian 40), 6 open | -2.4% | +8.5% | 15% | 0.45 | 6.5 |
| All four, 6 open at 0.50% | +6.9% | **+12.4%** | 11% | 0.79 | 5 |
| All four, 10 open at 0.40% | +7.8% | **+12.4%** | 13.5% | 0.67 | 8-9 |
| **All four, 15 open at 0.30% (the default)** | **+13.4%** | **+18.6%** | **14.5%** | **0.88** | **12-14** |
| All four, 20 open at 0.25% | +12.8% | +11.2% | 15.6% | 0.51 | 18 |
| Same four blended into ONE position per coin | -2.8% to +4.6% | +1.7% to +8.0% | 9-28% | 0.2-0.5 | 7-23 |

Two lessons: (1) running the systems **separately** (own trailing stop each) is clearly better than blending them into one signal; (2) a single
system is unreliable on its own, the mix is steadier. Returns are for roughly one year each window, not annualised promises.

## Result 3: the current stretch is rough, and a drawdown brake does not fix it

Replaying the **last 50 days** (300 four-hour candles, 38 coins) through the *live engine code* and through the backtester gave nearly the same answer:
engine **-9.75%** (137 trades), backtest **-9.40%** (173 trades). So the code trades like the research says; the market has simply been choppy for trend-following
(`research/engine_replay.py`). A bad stretch of this size is inside the study's drawdown range (worst 14-17%), and it is exactly the experience to expect from time to time.

We also tested cutting position size while the account is in drawdown (`research/brake_study.py`):

| Rule | 0-30% window | 30-60% | Out-of-sample 60-100% | Last 50 days |
|------|--------------|--------|-----------------------|--------------|
| No brake (default) | +8.5% (dd 17%) | +6.3% (dd 10%) | +13.8% (dd 14%) | -12.6% (dd 15%) |
| Halve at 5% drawdown | +7.0% (dd 12%) | -1.7% | +3.0% | -7.8% (dd 11%) |
| Halve at 4%, quarter at 8% | +3.9% | -3.3% | +2.0% | -6.6% (dd 10%) |
| Halve at 8% | +3.8% | +4.0% | +6.4% | -9.0% |

Brakes trim drawdowns by 3-5 points but give up most of the profit, because trend-following earns its money in the recoveries after the dips. Not enabled.
The protections that *are* on (daily-loss stop, drawdown halt at 20%, per-trade risk of 0.3%, max 15 open trades) exist to survive, not to boost returns.

### Portfolio risk rules we tested and did not adopt

Same four windows, same 38 coins (`research/risk_study.py`). A rule is only worth shipping if it helps in most windows:

| Rule | 0-30% | 30-60% | Out-of-sample 60-100% | Last 50 days |
|------|-------|--------|-----------------------|--------------|
| **Current default (no extra rule)** | +8.5% | +6.3% | +13.8% | -12.6% |
| Max 2 trades per coin | +12.9% | +6.1% | +9.4% | -11.0% |
| Max 1 trade per coin | +12.5% | +0.3% | +7.9% | -9.7% |
| Max 9 trades in the same direction | +0.6% | +1.9% | +9.4% | -4.4% |
| Max 7 trades in the same direction | +1.4% | -0.8% | +10.7% | -5.8% |
| Altcoin longs only while BTC is above its 4h EMA50 / EMA100 / EMA200 | +7.9% / -14.6% / -7.4% | -2.4% / -7.7% / -9.7% | +9.8% / +7.7% / +14.8% | -11.4% / -12.0% / -9.5% |

Caps trade profit for protection only in the bad window; the Bitcoin-regime filter is clearly harmful (it blocks the early moves trend-following lives on). The default stays.

## The risk dial: more risk is not more profit

The same 38-coin trend mix on one shared account, 3.4 years of real candles, live-engine limits (3x leverage cap, 15 open trades), only the risk per trade changes
(`research/risk_level_study.py`):

| Risk per trade | Total return (3.4 yr) | Worst dip | 0-30% | 30-60% | 60-100% | Last 50 days |
|---------------:|----------------------:|----------:|------:|-------:|--------:|-------------:|
| 0.30% | +34% | 17% | +8.5% | +6.3% | +13.8% | -12.6% |
| **0.50%** | **+54%** | **27%** | +13.1% | +9.3% | +21.1% | -20.2% |
| 0.75% | +75% | 39% | +17.6% | +11.5% | +27.6% | -28.9% |
| 1.00% | +88% | 49% | +20.5% | +12.0% | +31.0% | -36.8% |
| 1.50% | +75% | 65% | +23.0% | +6.2% | +21.5% | -50.5% |
| **2.00%** | **+7%** | **79%** | -5.6% | -27.7% | +4.2% | -49.7% |

* From 0.3% to 1% profit and drawdown rise together (return per unit of dip stays near 2x). Past 1% the extra risk buys **less** profit: bigger positions hit the leverage cap, so
  trades are skipped, and the dips are deep enough to wipe out the gains. At 2% the whole 3.4 years made +7% with a 79% drawdown.
* The presets pair each risk level with a drawdown halt that fits its historical worst dip: Careful 0.15% (halt 15%), tested default 0.3% (20%), **Balanced 0.5% (30%)**, Bold 1% (50%).
  A halt tighter than the strategy's normal dip would stop the bot right before the recoveries it earns its money on.
* Experimental styles (AI day trader, scalper, smart-money) risk less than the validated system by default (0.25%, 0.1%, 0.5%).

**Slower is not better either.** Textbook daily-candle versions of the same ideas (Donchian 20 with a 100-day filter, Donchian 55, EMA 20/100) on the same coins and account made
**+3% in 3.4 years** at 0.5% risk (4 trades a week, 26% drawdown), against **+54%** for the 4-hour mix (`research/daily_study.py`). 4-hour candles remain the sweet spot between
too many fees and too little information.

## Result 4: the two "fast" and "patient" styles, tested honestly

You asked for a high-frequency scalper and a patient day trader that follows the big players. Both were built, and both were tested first:

| Style | Test | Result |
|-------|------|--------|
| **Scalper** | 75 days of 1-minute candles, BTC / ETH / SOL / XRP; mean-reversion, breakout and order-flow families; limit-order entries and exits with conservative fills (price must trade *through* the order, stops pay taker fees + slippage); 148 settings searched, judged on the last 40% | **0 of 148 profitable in-sample.** At retail fees (maker 2 bps / taker 5 bps) the best lost **about 5 bps per trade** out-of-sample; even at a zero-maker / 2.5 bps-taker tier it lost **about 1 bp per trade with a 73% win rate**. Roughly 20 to 140 trades a day. Fees are bigger than the edge. |
| **Smart-money day trader** (crypto) | The full confirmation sequence (4h trend, liquidity sweep, high-volume displacement, retest, target = next liquidity pool, 2R minimum) on 2 years of 15-minute candles, 4 coins, about 750 setups, costs included | About 1 trade a day across 4 coins; average **-0.10R in-sample, -0.27R out-of-sample** (t about -4). It loses. |
| Smart-money, stocks | First-hour and 5-minute opening-range breakouts with relative-volume and VWAP filters, 46 liquid US stocks | 0 of 60 settings profitable on 2 years of hourly bars (out-of-sample -9 to -15 bps per trade); 0 of 39 on 60 days of 5-minute bars (best -0.2 bps) |
| "Following the big players" (public data) | Top-trader positioning, all-account long/short ratio, taker flow, open interest vs the next 1h and 4h return, 12 coins, 30 days | Looked significant when pooled, but **market-neutral** (coin vs coin at each step) top-trader positioning had no predictive power (t = 0.5). Only open interest rising with price and the crowd ratio showed a weak 1-hour effect (IC about 0.07, t about 2.5): far too small to pay a 16 bps round trip |

What was shipped anyway, and why: both styles run in **practice mode only**, behind switches, each with its own scorecard, honest evidence text, and
guards (the scalper pauses itself when its last 40 trades average below zero after fees and shows how far your fee tier is from break-even; the smart-money
trader trades at most 3 times a day, checks liquidity first and only acts after the full confirmation sequence). They exist so you can test your own fee
tier, markets and settings against reality on paper instead of trusting anyone's claims, including ours.

## What this means for the product

* **Default strategy:** Trend mix, 4-hour candles, 38 liquid coins (two contracts whose Binance candles are frozen were removed), 0.3% risk per trade, up to 15 open trades.
* **Why it may look quiet:** it trades when a 4-hour candle closes beyond its recent range. Expect several trades a week across all coins, not several a day.
* **AI day trading** is switched off by default and labelled experimental. If you enable it, it sees the measured track record of each trigger on the coin before it decides, and the
  rules (cool-down, stop-out pause, risk cap, trading hours) apply to it too.
* **What the AI is for:** explaining decisions, reading context (funding, order book, liquidity levels, multi-timeframe trend) and refusing setups the data does not support.
  A separate test (`docs/ai_validation.json`, 6,800 signals) found that AI veto/sizing did **not** improve the trend system, so it is advisory.

## Honest limits

* Backtests ignore partial fills and exchange outages; real results will be a little worse.
* The out-of-sample windows are about 1 to 1.4 years of one market regime. A different regime can produce a flat or negative year.
* Several tests searched many settings; we report how many stayed positive precisely to expose over-fitting, but some optimism remains.
* A small paper account ($500) cannot place trades on high-priced coins like BTC (exchange minimum sizes). The bot says so in plain words instead of failing silently; use a larger practice balance to see all coins.

## Reproduce

```bash
python research/fetch_history.py            # download candles (paced; keep it polite, Binance bans bursts)
python research/study.py 15m                # SMC / ICT families on 15m
python research/study2.py 4h donchian       # any family on any timeframe: donchian mtf_break squeeze bbrev pullback emacross
python research/portfolio_study.py          # single systems on a shared account, tuned vs fresh coins
python research/ensemble_study.py           # the four-system blend
python research/ensemble_union.py           # the single-position blend (worse)
python research/fetch_micro.py              # 75 days of 1-minute candles with taker flow
python research/scalp_study.py              # the scalper, two fee tiers
python research/orb_study.py                # stocks: first-hour opening-range breakout
python research/orb5_study.py               # stocks: 5-minute opening range (60 days only)
python research/footprint_study.py          # big-player positioning vs next-hour returns
python research/playbook_study.py           # the smart-money confirmation sequence on crypto
python research/brake_study.py              # drawdown brake variants (rejected)
python research/risk_study.py               # per-coin / same-direction caps and a BTC regime filter (rejected)
python research/risk_level_study.py         # the risk dial: 0.3% to 2% per trade
python research/daily_study.py              # daily candles vs 4-hour (daily is much worse)
python research/engine_replay.py 300        # the live engine vs the backtester on the last 300 bars
```
