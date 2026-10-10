# Sentinel User Manual

*A plain-English guide for people who have never run a trading bot.*

Sentinel watches 13 crypto and currency markets around the clock and buys or sells for you using one simple idea: **follow strong trends and cut losses fast.** It starts in **practice mode**: real market prices, fake money. Nothing in practice mode can lose real money.

> **Not financial advice.** Past results do not predict the future. Even in the best backtest this bot earns a modest return with real losing streaks. Only ever risk money you can afford to lose.

---

## 1. Install and start (5 minutes)

**You need:** a Windows, Mac or Linux computer and [Python 3.7 or newer](https://www.python.org/downloads/). On Windows, tick **"Add python.exe to PATH"** during install. Sentinel needs no other software and no paid accounts.

| System | How to start |
|---|---|
| Windows | Double-click **`run.bat`** in the Sentinel folder |
| Mac / Linux | Open a terminal in the folder and run `./run.sh` |

A black window opens (**keep it open**; closing it stops the bot) and your browser opens the dashboard at **http://localhost:8765**. If the browser does not open by itself, type that address in.

Everything runs on your own computer. The dashboard is only reachable from your computer.

> **Stop the bot:** close the black window (or press `Ctrl+C` in it). **Start it again:** run `run.bat` again; it remembers your account and trades.

## 2. First launch: the setup wizard

The first time you open the dashboard, a short wizard appears. It takes about a minute:

1. **Meet Sentinel**: what the bot does, and that you are in practice mode.
2. **How much practice money?** Pick a preset ($100 to $10,000) or type your own. Then choose how bold each trade is:
   - **Careful 0.25%**: smaller bets, calmer ride.
   - **Balanced 0.5%** *(recommended)*.
   - **Bold 1%**: bigger swings both ways.
   The screen tells you the most one losing trade can cost.
3. **AI helper (optional)**: paste an API key if you want a second opinion on trades, or skip. The bot works fine without it. (See section 7.)
4. **Ready**: press **Start the bot**. Your practice account is created, the bot starts watching the markets, and the wizard offers a 1-minute **guided tour**.

You can re-run the wizard any time from **Help → Re-run setup**, and replay the tour from **Help → Take the guided tour**.

## 3. The screen at a glance

The left rail switches pages. Press `Ctrl+K` (or `Cmd+K`) for a quick search to jump anywhere, and the number keys **1–5** also switch pages.

**Top bar**
- **Live · 13 markets / Paused / Halted**: the bot's status.
- **Practice mode**: reminder that no real money is involved.
- **Account chip** (wallet icon + amount): your account value. Click it to change your starting balance and how bold each trade is.
- **Run / Pause**: the on/off switch. *Run*: the bot checks every market and trades by itself. *Pause*: no new trades (open trades keep their safety stops).
- **Red power button**: the emergency stop. It closes everything and halts the bot (confirmation required). See section 8.

### Home
- **Getting started** checklist (disappears when done, or click *hide*).
- **Total value**: what your account is worth now, plus profit/loss since you started. The chart shows your account over the last hour / day / week / all time.
- **Sentinel insight**: a plain-English summary of what the bot is doing right now.
- **Profit · Today · Open trades · Win rate**: the numbers that matter most.
- **Safety systems**: three meters: *Daily loss limit*, *Emergency brake*, *Money in trades*. Green is calm, amber means getting close, red means a limit is almost reached.
- **Open trades**: what the bot currently holds, its profit so far, and its safety-exit price.

### Markets
- A professional candlestick chart (TradingView's open-source charting). Each bar is one **4-hour candle**: green if price rose, red if it fell.
- The thin **blue lines** are the recent high and low the bot watches. When price breaks through one, the bot trades.
- **Arrows** mark the bot's buys and sells; circles mark exits with the result; a **dashed red line** is the safety stop of an open trade.
- A sentence above the chart says what the bot is doing on that market and when the next candle closes.
- The **market list** on the right shows all 13 markets with a mini price chart. A glowing dot means the bot holds a trade there. Click any market to switch.
- The bottom panel has **Open trades**, **Activity** (a log of everything the bot did and why) and **History** (finished trades).

### AI helper
Optional second opinion on each trade signal. See section 7.

### Help
Quick start, how the bot works, a glossary, troubleshooting, and the tour.

### Settings
Mode (practice or exchange), account size, safety limits, exchange connection, and the Pro-mode switch.

### Simple mode and Pro mode
Sentinel starts in **Simple mode**, which shows only what a beginner needs. Switch on **Pro mode** (Settings → *Pro mode*, or `Ctrl+K` → *Switch to Pro mode*) to add the **Lab**, strategy names and parameters, extra table columns, drawdown and profit-factor statistics, the AI scorecard, and the markets table.

## 4. What the bot actually does

1. Every **4 hours**, when a new candle closes, the bot checks each market.
2. **Entry.** For crypto, if the price closes above its 40-candle high (or below its 40-candle low) that is a *breakout*, often the start of a trend, so it enters in that direction. For the three currency pairs it uses a different trend signal (fast and slow moving averages crossing, confirmed by a trend-strength filter).
3. **Size.** It sizes the trade so that if the safety stop is hit you lose only your chosen risk (0.5% of the account by default, so about $5 on $1,000). Leverage is capped.
4. **Safety stop.** Every trade gets an automatic stop-loss placed at 4× the market's normal 4-hour movement (3× for currencies). As the trade moves in your favor the stop follows it (a *trailing stop*) and never moves back.
5. **Exit.** The trade closes when the stop is hit, or when the trend flips the other way.
6. **Rules that cannot be broken.** At most 6 trades at once, never more than 3× leverage, a daily-loss limit, and an emergency drawdown brake (section 8).

Most trades lose a little and a few win big. A win rate of 35–45% is normal for this style, so a quiet or losing week is not a sign something is broken.

## 5. Practice account

- **Change the balance:** click the account chip → pick an amount (or type one) → choose how bold → **Apply**. This starts a **fresh practice account** (old practice trades are cleared). Re-applying the same amount on an untouched account does nothing.
- **Small accounts:** exchanges have minimum order sizes. With a very small balance some markets (for example Bitcoin) may be too expensive to trade at your risk level. The **Activity** log tells you when this happens ("too small for this account"). $1,000 trades almost everything.
- **Reset everything:** Settings → *Reset paper account*.

## 6. Reading the numbers

| Term | Meaning |
|---|---|
| **Total value** | Cash plus open trades, after fees. If you closed everything right now this is what you'd have. |
| **Profit** | Total value minus your starting balance. |
| **Today** | Change since midnight UTC. |
| **Win rate** | Share of finished trades that made money. |
| **Profit factor** (Pro) | Money won ÷ money lost. Above 1 is profitable, 1.5+ is strong. |
| **Drawdown** (Pro) | How far the account is below its highest value. |
| **Open risk** (Pro) | The most you would lose if every open stop hit now. |
| **Buy (long)** | A bet that price goes **up**. |
| **Sell (short)** | A bet that price goes **down**. |
| **Candle** | One 4-hour bar on the chart. |

Hover any **?** or dotted underline for an explanation.

## 7. The AI helper (optional)

When the bot spots a trade that has already passed every safety check, the AI helper reads a short briefing (trend, volatility, funding rate, open interest, how crowded the trade is, your open positions) and rates its chances from 0 to 100. It can also look up extra data with three read-only tools.

**Three modes** (AI helper page):
- **Off**: no AI calls at all.
- **Advise** *(default)*: the AI rates trades and every trade is tagged with its rating, but it **never blocks a trade**.
- **Veto** *(Pro)*: trades rated below your cut-off are skipped. In our testing on ~6,800 historical signals this did **not** improve results, so it is not recommended.

**Getting a key.** [DeepSeek](https://platform.deepseek.com) (cheap; a few dollars lasts months) or [Google Gemini](https://aistudio.google.com) (has a free tier). Paste it in the wizard or on the AI helper page and press **Test connection** (about 25 tokens, a fraction of a cent). The key is stored only on your computer.

**It cannot run up a bill.** The app enforces hard limits: 120 AI calls per day, $0.25 per day and $3 lifetime (all editable in the config). Typical cost is about **0.03–0.05 cents per trade signal**. If a limit is reached, or the AI is down, the AI simply stops and **trading continues normally**.

**Does it help?** Pro mode shows a live **scorecard** comparing trades the AI rated low, mid and high. Judge it by that, not by how clever it sounds. With under ~30 rated trades it is too early to conclude anything.

## 8. Safety systems

| Protection | What it does | Default |
|---|---|---|
| **Safety stop** | Every trade has one; closes the trade if price moves against it. | always on |
| **Risk per trade** | Caps the loss of a single trade. | 0.5% of account |
| **Max open trades** | Never more than this many at once. | 6 |
| **Leverage cap** | Limits borrowed exposure per trade and in total. | 3× |
| **Daily loss limit** | After this loss in one UTC day, no new trades until tomorrow. | 3% |
| **Emergency brake** | If the account falls this far below its peak, everything is closed and the bot halts. | 20% |
| **Kill switch** | The red power button: close everything and halt now. | manual |
| **AI budget** | Hard daily and lifetime AI spending caps; AI failures never block trading. | see §7 |

After a halt, open **Settings → Resume after halt** once you have looked at why it happened.

## 9. Practice on a real exchange (optional)

By default Sentinel simulates fills itself. For exchange-realistic practice you can connect **Bybit Demo Trading** (real prices, play money, email sign-up, no ID):

1. Create a free account at bybit.com and switch it to **Demo Trading**.
2. In Demo mode create an **API key** with *trade* permission and **no withdrawal permission**.
3. In Sentinel: **Settings → Exchange link**: Exchange = Bybit, Network = Demo, paste the key and secret, press **Save and test connection** (you should see your demo balance).
4. Set **Mode** to *Exchange*, type `I UNDERSTAND` when asked, and save.

> **Important:** the exchange connections are tested against simulated exchanges and Bybit's published rules, but have **never been run on a real account**. Expect to fix small things on first contact. Do not use real money. Forex has no exchange adapter and always runs in the built-in simulator.

## 10. Pro features

- **Lab**: backtest any market and strategy over up to 7 years; **Optimize** (walk-forward: tuned on the first 60% of history and judged on the unseen last 40%); **Whole portfolio** test. Read the verdict banner: if it says *no reliable edge*, believe it.
- **Command line**: `py sentinel.py status`, `positions`, `trades`, `events`, `start`, `stop`, `kill`, `resume`, `flatten`, `close <id>`, `backtest SYMBOL [--optimize]`, `portfolio`, `ai-test`, `broker-test`, `config [key [value]]`.
- **TradingView alerts**: `POST /hook` accepts alerts through the same safety rules (needs a paid TradingView plan and a tunnel such as ngrok; only `/hook` is reachable from outside).
- **Trade export**: History → CSV at `/api/trades.csv`.

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| Page won't load | The black window from `run.bat` must stay open. Run it again. |
| Page looks old or broken after an update | Press `Ctrl+F5` once to refresh. |
| "Python was not found" | Install Python 3.7+ and tick *Add to PATH*. |
| Nothing is trading | Normal. The bot waits for strong breakouts; this can take days. |
| "Data hiccup" on a market | A price feed was briefly unreachable. It retries by itself. |
| "too small for this account" | Raise the practice balance. |
| Status says **Halted** | A safety limit tripped. Check Activity, then Settings → Resume. |
| AI says no key / unavailable | Add a key on the AI helper page, or ignore it; trading is unaffected. |
| Port 8765 already in use | Another copy is running. Close it, or start with `PORT=8766` set. |

**Where is my data?** In the `data/` folder next to the program: `bot.db` (trades), `config.json` (settings and your API keys). Back it up if you care about it; do not share it. If the folder is inside a cloud-synced folder (OneDrive, Dropbox) your keys sync too.

## 12. Honest expectations

Over 5 years of real past data, the default setup (13 markets, 0.5% risk per trade, after fees, slippage and funding) returned about **+55% total (≈7.6% a year) with a worst dip of ≈20%**; at 1% risk about +109% with a ≈29% dip. That is a modest, real edge, not a get-rich-quick machine. The settings were chosen after seeing that data, so live results will probably be worse, and individual markets often show no edge on their own. Expect losing streaks. Run in practice mode for weeks before you consider anything else.
