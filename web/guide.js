"use strict";
/* Beginner layer: glossary tooltips, setup wizard, guided tour, Help page, getting-started checklist.
   Loaded BEFORE app.js and contains declarations only; app.js calls guideInit() once the first state has loaded. */
const GLOSS = {
  long: "Buy (go long): you profit if the price goes UP.",
  short: "Sell (go short): you profit if the price goes DOWN. Futures and forex allow this; plain crypto spot does not.",
  stop: "A safety stop is an automatic exit. If price moves against a trade to this level, the bot closes it so one bad trade can't hurt much.",
  drawdown: "How far your account has fallen from its highest value. The emergency brake halts everything if it gets too deep.",
  profit: "Your account value now minus where you started. Includes fees and trades that are still open.",
  winrate: "The share of finished trades that made money. A trend-following bot often wins only 35-45% of the time, because its winners are much bigger than its losers.",
  candle: "One bar on the chart. Here each candle covers 4 hours of price movement.",
  breakout: "When price pushes beyond its recent high or low. The bot treats this as the possible start of a trend and enters.",
  risk: "The most one trade is allowed to lose, as a percent of your account. 0.5% of $1,000 is $5.",
  practice: "Practice mode uses real market prices but fake money. Nothing here can lose real money."
};
const term = (t, k) => `<span class="term" data-tip="${esc(GLOSS[k])}">${t}</span>`;
function sideWord(side) { return simple ? (side > 0 ? "buy" : "sell") : (side > 0 ? "long" : "short"); }
function sidePill(side) { const w = sideWord(side); return `<span class="pill ${side > 0 ? "up" : "down"}" data-tip="${esc(side > 0 ? GLOSS.long : GLOSS.short)}">${w[0].toUpperCase() + w.slice(1)}</span>`; }
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ---------- Home KPI row (4 simple cards; Pro adds the technical ones) */
function kpiSet(e, r, ps) {
  const profit = e.equity - e.start_equity;
  return kpi("Profit", sm(profit), pct(e.total_return_pct) + " since start", cls(profit), GLOSS.profit) +
    kpi("Today", sm(e.day_pnl), pct(e.day_pnl_pct) + " since midnight UTC", cls(e.day_pnl), "Change in your account value since 00:00 UTC.") +
    kpi("Open trades", ps.length + " / " + r.max_positions, "the bot never holds more than " + r.max_positions, "", "How many positions the bot is holding right now. It has a hard limit.") +
    kpi("Win rate", e.trades ? e.win_rate.toFixed(0) + "%" : "--", e.trades ? e.trades + " finished trades" : "no finished trades yet", "", GLOSS.winrate) +
    `<div class="pro" style="display:contents">` +
    kpi("Profit factor", e.profit_factor ? e.profit_factor.toFixed(2) : "--", e.profit_factor ? (e.profit_factor > 1 ? "winners outweigh losers" : "losers outweigh winners") : "needs closed trades", e.profit_factor ? cls(e.profit_factor - 1) : "", "Money won divided by money lost. Above 1 is profitable; 1.5+ is strong.") +
    kpi("Open risk", money(e.open_risk), "if every stop hit now", "", "The most you'd lose if every open stop-loss hit right now.") +
    kpi("Drawdown", e.drawdown_pct.toFixed(2) + "%", "from the account's peak", e.drawdown_pct > 10 ? "down" : "", GLOSS.drawdown + " Halt at " + r.max_drawdown_halt_pct + "%.") + `</div>`;
}

/* ---------- getting-started checklist on Home */
function renderStart() {
  const el = $("#startCard"); if (!el) return;
  if (LS.get("hideStart", "0") === "1") { el.style.display = "none"; return; }
  const e = S.engine, ui = S.cfg.ui || {}, ai = S.cfg.ai, n = trading().length;
  const items = [
    [e.running, "The bot is running", "It watches " + n + " markets and acts when a 4-hour candle closes.", null],
    [ui.onboarded, "Your practice account is set up", "Starting balance " + money(e.start_equity, 0) + ", risk " + S.cfg.risk.per_trade_pct + "% per trade.", ["Change", "acct"]],
    [ui.tour_done, "Take the 1-minute tour", "I point at each important part of the screen and explain it.", ["Start tour", "tour"]],
    [ai.has_api_key || ai.provider === "claude_cli", "Connect an AI (optional)", ai.has_api_key ? "Connected. It rates trades, powers day trading and answers your questions." : "Needed for day trading and chat. Works with 14 providers, some free.", ["Connect", "ai"]],
    [e.trades > 0 || e.positions.length > 0, "Wait for the first trade", "The bot only enters on strong breakouts, so this can take hours or days. That is normal.", null]];
  const done = items.filter(i => i[0]).length; el.style.display = done === items.length ? "none" : "block";
  const h = `<h3>Getting started <span class="sub">${done} of ${items.length} done &middot; <a href="#" id="hideStart">hide</a></span></h3><div class="start">${items.map(i => `<div class="it ${i[0] ? "done" : ""}"><span class="ck"></span><div><b>${i[1]}</b><div class="sub">${i[2]}</div></div>${i[3] && !i[0] ? `<button class="btn sm" data-act="${i[3][1]}">${i[3][0]}</button>` : ""}</div>`).join("")}</div>`;
  if (setHTML(el, h)) {
    $("#hideStart", el).onclick = ev => { ev.preventDefault(); LS.set("hideStart", "1"); el.style.display = "none"; };
    $$("[data-act]", el).forEach(b => b.onclick = () => ({ acct: () => openAcct(b), tour: () => startTour(0), ai: () => openAI(b) })[b.dataset.act]());
  }
}

/* ---------- setup wizard */
const WZ = { i: 0, amt: 1000, risk: 0.5, key: "", provider: "deepseek", back: false, tested: "" };
const WZ_RISK = [[0.25, "Careful", "Smaller bets, calmer ride"], [0.5, "Balanced", "Recommended starting point"], [1, "Bold", "Bigger swings both ways"]];
function openWizard() {
  const fresh = !S.engine.trades && !S.engine.positions.length;
  WZ.i = 0; WZ.amt = (fresh && S.engine.start_equity === 10000) ? 1000 : Math.round(S.engine.start_equity);   // keep a size the user already chose WZ.risk = S.cfg.risk.per_trade_pct; WZ.key = ""; WZ.tested = "";
  const sh = openSheet('<div id="wzRoot"></div>'); sh.classList.add("wiz"); sheetLock = true; wzDraw(false);
}
function wzDraw(back) {
  const root = $("#wzRoot"); if (!root) return; WZ.back = back;
  const steps = [wzWelcome, wzMoney, wzAI, wzReady], last = WZ.i === steps.length - 1;
  root.innerHTML = `<div class="body"><div class="step ${back ? "back" : ""}">${steps[WZ.i]()}</div></div>
   <div class="foot2"><a href="#" id="wzSkip" class="sub">Skip setup</a><span class="grow"></span><div class="dots">${steps.map((_, k) => `<i class="${k === WZ.i ? "on" : ""}"></i>`).join("")}</div><span class="grow"></span>
   ${WZ.i ? '<button class="btn" id="wzBack">Back</button>' : ""}<button class="btn pri" id="wzNext">${last ? "Start the bot" : WZ.i ? "Continue" : "Get started"}</button></div>`;
  icons(root); wzBind();
}
function wzWelcome() {
  return `<div class="label">Welcome</div><h2>Meet Sentinel</h2><p class="lead">A trading bot that watches ${trading().length} crypto and currency markets around the clock and buys or sells for you using one simple idea: <b>follow strong trends and cut losses fast.</b></p>
   <div class="feat"><div><svg class="i" data-i="shield-alert"></svg><p style="margin:0"><b>Practice mode first</b><span>Real market prices, fake money. You cannot lose real money while you learn.</span></p></div>
   <div><svg class="i" data-i="zap"></svg><p style="margin:0"><b>Safety is built in</b><span>Every trade has an automatic stop-loss, and strict daily and drawdown limits switch the bot off if things go badly.</span></p></div>
   <div><svg class="i" data-i="power"></svg><p style="margin:0"><b>You stay in control</b><span>Pause any time, or press the red button to close everything instantly.</span></p></div></div>`;
}
function wzMoney() {
  const risk = WZ.amt * WZ.risk / 100;
  return `<div class="label">Step 1 of 3</div><h2>How much practice money?</h2><p class="lead">Pick any amount. It is only pretend money, so choose something close to what you might use for real later.</p>
   <div class="chips" id="wzAmts">${PRESETS.slice(0, 7).map(v => `<button data-v="${v}" class="${v === WZ.amt ? "on" : ""}">${money(v, 0)}</button>`).join("")}</div><input id="wzAmt" type="number" min="10" step="10" value="${WZ.amt}" style="margin-top:10px;max-width:200px">
   <p class="label" style="margin:18px 0 8px">How bold should each trade be?</p>
   <div class="choice" id="wzRisk">${WZ_RISK.map(([v, n, d]) => `<button data-r="${v}" class="${v === WZ.risk ? "on" : ""}"><b>${n} &middot; ${v}%</b><span>${d}</span></button>`).join("")}</div>
   <p class="note" id="wzPrev" style="margin-top:14px">With <b>${money(WZ.amt, 0)}</b>, one losing trade costs at most about <b>${money(risk)}</b>.${WZ.amt < 500 ? ' <span class="warn">Small accounts cannot trade every market because exchanges have minimum order sizes.</span>' : ""}</p>`;
}
function wzAI() {
  return `<div class="label">Step 2 of 3 &middot; optional</div><h2>Add an AI helper?</h2><p class="lead">When the bot spots a trade, the AI helper can read a short briefing and rate its chances. It costs about <b>a twentieth of a cent</b> per trade, and the bot works fine without it. You can add this later.</p>
   <div class="choice" id="wzProv">${[["deepseek", "DeepSeek", "Cheap, a few dollars lasts months"], ["gemini", "Google Gemini", "Has a free tier"], ["__more", "More providers", "OpenAI, Claude, Grok, local and 10 more"]].map(([k, n, d]) => `<button data-p="${k}" class="${WZ.provider === k ? "on" : ""}"><b>${n}</b><span>${d}</span></button>`).join("")}</div>
   ${WZ.provider === "__more" ? '<p class="note" style="margin-top:16px">You will pick from every provider on the next screen right after setup.</p>' : `<label class="f" style="margin-top:16px">API key (optional, stored only on this computer) <a href="${esc(PROV(WZ.provider).key_url || "#")}" target="_blank" rel="noopener" style="margin-left:8px">Get a key &rarr;</a></label><input id="wzKey" type="password" autocomplete="off" placeholder="Paste a key, or leave empty to skip" value="${esc(WZ.key)}">
   <div class="row" style="margin-top:12px"><button class="btn sm" id="wzTest" ${WZ.key ? "" : "disabled"}>Test key</button><span class="sub" id="wzTestOut">${WZ.tested}</span></div>
   <p class="sub" style="margin-top:12px">${esc(PROV(WZ.provider).note || "")} The app also enforces a hard daily and lifetime spending cap.</p>`}`;
}
function wzReady() {
  return `<div class="label">Step 3 of 3</div><h2>Ready to start</h2><p class="lead">Here is what will happen when you press the button:</p>
   <div class="feat"><div><svg class="i" data-i="wallet"></svg><p style="margin:0"><b>${money(WZ.amt, 0)} of practice money</b><span>Each trade risks at most ${WZ.risk}% (${money(WZ.amt * WZ.risk / 100)}).</span></p></div>
   <div><svg class="i" data-i="activity"></svg><p style="margin:0"><b>The bot starts watching ${trading().length} markets</b><span>It looks for breakouts each time a 4-hour candle closes. Quiet stretches are normal.</span></p></div>
   <div><svg class="i" data-i="sparkles" ></svg><p style="margin:0"><b>AI helper: ${WZ.key ? "on, in advise mode" : (S.cfg.ai.has_api_key ? "already connected" : "off for now")}</b><span>${WZ.key || S.cfg.ai.has_api_key ? "It rates trades but never blocks them." : "You can add a key any time from the AI helper page."}</span></p></div></div>
   <p class="note">After you press start I will offer a one-minute tour of the screen.</p>`;
}
function wzBind() {
  const q = id => $(id, $("#wzRoot"));
  q("#wzSkip").onclick = async ev => { ev.preventDefault(); await guard(async () => { await api("/api/config", { ui: { onboarded: true } }); })(); sheetLock = false; closeSheet(); refresh(); };
  const back = q("#wzBack"); if (back) back.onclick = () => { WZ.i--; wzDraw(true); };
  q("#wzNext").onclick = () => { if (WZ.i < 3) { WZ.i++; wzDraw(false); } else wzFinish(); };
  const amts = q("#wzAmts"); if (amts) {
    const upd = () => { $$("#wzAmts button").forEach(b => b.classList.toggle("on", +b.dataset.v === WZ.amt)); $$("#wzRisk button").forEach(b => b.classList.toggle("on", +b.dataset.r === WZ.risk)); const r = WZ.amt * WZ.risk / 100; $("#wzPrev").innerHTML = `With <b>${money(WZ.amt, 0)}</b>, one losing trade costs at most about <b>${money(r)}</b>.${WZ.amt < 500 ? ' <span class="warn">Small accounts cannot trade every market because exchanges have minimum order sizes.</span>' : ""}`; };
    amts.onclick = ev => { const b = ev.target.closest("button"); if (b) { WZ.amt = +b.dataset.v; q("#wzAmt").value = WZ.amt; upd(); } };
    q("#wzAmt").oninput = ev => { WZ.amt = Math.max(10, +ev.target.value || 10); upd(); };
    q("#wzRisk").onclick = ev => { const b = ev.target.closest("button"); if (b) { WZ.risk = +b.dataset.r; upd(); } };
  }
  const prov = q("#wzProv"); if (prov) {
    prov.onclick = ev => { const b = ev.target.closest("button"); if (b) { WZ.key = q("#wzKey") ? q("#wzKey").value : ""; WZ.provider = b.dataset.p; wzDraw(false); } };
    if (q("#wzKey")) q("#wzKey").oninput = ev => { WZ.key = ev.target.value.trim(); q("#wzTest").disabled = !WZ.key; };
    if (q("#wzTest")) q("#wzTest").onclick = guard(async () => {
      $("#wzTestOut").innerHTML = '<span class="spin"></span>testing (about 25 tokens)';
      const r = await api("/api/ai/test", { provider: WZ.provider, base_url: PROV(WZ.provider).base_url, model: PROV(WZ.provider).model, api_key: WZ.key }, true);
      WZ.tested = r.ok ? '<span class="up">Key works</span>' : '<span class="down">' + esc(r.error) + "</span>"; $("#wzTestOut").innerHTML = WZ.tested;
    });
  }
}
function wzFinish() { return guard(async () => {
  const btn = $("#wzNext"); btn.disabled = true; btn.innerHTML = '<span class="spin"></span>Starting';
  if (WZ.risk !== S.cfg.risk.per_trade_pct) await api("/api/config", { risk: { per_trade_pct: WZ.risk } }, true);
  if (S.engine.mode !== "live" && WZ.amt !== S.engine.start_equity) await api("/api/account", { amount: WZ.amt }, true);
  if (WZ.key && WZ.provider !== "__more") await api("/api/config", { ai: { mode: "advise", provider: WZ.provider, base_url: PROV(WZ.provider).base_url, model: PROV(WZ.provider).model, api_key: WZ.key } }, true);
  await api("/api/config", { ui: { onboarded: true } }, true);
  try { await api("/api/engine", { action: "start" }, true); } catch (e) { }
  sheetLock = false; closeSheet(); await refresh(); toast("The bot is running in practice mode.", "win", "You're all set");
  if (WZ.provider === "__more") { setTimeout(() => openAI(), 600); return; }
  setTimeout(async () => { if (await confirmSheet("Take the 1-minute tour?", "I will point at each important part of the screen and explain it in plain English.", "Show me around", false)) startTour(0); else api("/api/config", { ui: { tour_done: true } }, true); }, 700);
})(); }

/* ---------- guided tour */
const TOUR = [
  { page: "portfolio", sel: null, t: "Welcome to Sentinel", b: "This is a quick tour. Sentinel is a trading bot: it watches the markets, buys and sells for you, and protects you with automatic stop-losses. You are in <b>practice mode</b>, so nothing here uses real money." },
  { page: "portfolio", sel: "#acctChip", t: "Your practice account", b: "This is what your account is worth right now. Click it any time to change your starting balance or how bold each trade is." },
  { page: "portfolio", sel: "#engSeg", t: "The on/off switch", b: "<b>Run</b> means the bot is working: it checks every market and trades by itself. <b>Pause</b> stops it opening new trades. Trades already open keep their safety stops." },
  { page: "portfolio", sel: "#pTotal", t: "Your total value", b: "The big number is your account value, with the profit or loss since you started right under it. The chart shows how it has changed over time." },
  { page: "portfolio", sel: "#meters", t: "Safety systems", b: "Three limits protect you. If a bad day eats the daily limit, the bot stops opening trades until tomorrow. If losses run too deep, an emergency brake closes everything." },
  { page: "trade", sel: "#chartBox", t: "The market chart", b: "Each bar is one 4-hour <b>candle</b>: green if the price rose, red if it fell. The blue lines are the recent high and low the bot watches. When price breaks through, the bot trades. Arrows show its trades." },
  { page: "trade", sel: "#wl", t: "Your markets", b: "These are the markets the bot watches. The small line is the recent price path. A glowing dot means the bot is holding a trade there. Click any market to open its chart." },
  { page: "trade", sel: "#tStatus", t: "What the bot is thinking", b: "This sentence tells you in plain English what the bot is doing for the market you are looking at, and when the next 4-hour candle closes." },
  { page: "portfolio", sel: "#hMarkets", t: "My markets", b: "Everything you follow lives here. Press the <b>star</b> to pin a favorite to the top, the tag to let the bot trade it (or just watch), and <b>Add market</b> to search thousands of stocks, ETFs, crypto, forex and commodities." },
  { page: "portfolio", sel: "#aiChip", t: "Connect an AI", b: "This is where the API key goes. Pick from 15 providers (DeepSeek, OpenAI, Claude, Gemini, Grok, local models and more), paste a key and press Save. It powers day trading and chat, and has hard spending caps." },
  { page: "strategies", sel: "#stc_trend", t: "Pick how the bot trades", b: "Three styles, each with its own switch, settings and scorecard. <b>Trend following</b> is the one that held up in our tests. <b>Smart-money day trader</b> waits for the big players to act before following. <b>Scalper</b> makes many tiny fast trades and shows you exactly how fees decide whether it can ever pay." },
  { page: "trader", sel: "#scList", t: "The trend scanner", b: "The bot's main strategy waits for the price to break out of its recent range on a 4-hour candle. This list shows how far each coin is from its next trigger, so a quiet day is explained, not mysterious." },
  { page: "portfolio", sel: "#healthChip", t: "System health", b: "This chip turns amber or red if something needs attention (price feed, AI, a stuck loop). Click it for a plain-English list of what is wrong and how to fix it." },
  { page: "settings", sel: null, t: "Make it yours", b: "On the left, one slider sets how much you risk per trade and one switch sets the timeframe. The tabs on the right hold <b>Look</b> (theme, colours, layout), <b>Alerts</b> (phone notifications), <b>Access</b> (sign-in and people) and <b>More</b> (mode, exchange, optional trading rules)." },
  { page: "trader", sel: "#dToggle", t: "Quick day trading (experimental)", b: "Switch on the <b>AI day trader</b> for 15-minute trades. Watch its live thoughts as it scans, decides and trades, and ask it questions in the chat. It runs on practice money and is unproven, so start small." },
  { page: "portfolio", sel: "#killBtn", t: "The emergency button", b: "If you ever want everything closed immediately, press this. It closes all positions and halts trading until you resume it in Settings." },
  { page: "help", sel: null, t: "You're ready", b: "Leave the bot running and check <b>Home</b> once a day. The bot only trades on strong breakouts, so quiet days are normal. The <b>Help</b> page has a glossary and answers to common questions. Replay this tour from there any time." }];
let TR = null;
function startTour(i) {
  if (TR) return; TR = { i: i || 0, spot: document.createElement("div"), card: document.createElement("div") };
  TR.spot.id = "tourSpot"; TR.card.id = "tourCard"; document.body.append(TR.spot, TR.card); closeSheet();
  TR.key = e => { if (e.key === "Escape") endTour(); else if (e.key === "ArrowRight" || e.key === "Enter") tourGo(1); else if (e.key === "ArrowLeft") tourGo(-1); };
  document.addEventListener("keydown", TR.key, true); tourShow();
}
function tourGo(d) { if (!TR) return; const n = TR.i + d; if (n < 0) return; if (n >= TOUR.length) return endTour(true); TR.i = n; tourShow(); }
async function endTour(done) {
  if (!TR) return; document.removeEventListener("keydown", TR.key, true); TR.spot.remove(); TR.card.remove(); TR = null;
  try { await api("/api/config", { ui: { tour_done: true } }, true); } catch (e) { } if (done) toast("Replay it any time from the Help page.", "info", "Tour complete"); refresh();
}
async function tourShow() {
  const my = TR, s = TOUR[TR.i]; if (page !== s.page) { go(s.page); await sleep(750); } else await sleep(60); if (TR !== my) return;
  let el = s.sel ? $(s.sel) : null; for (let k = 0; k < 20 && s.sel && (!el || !el.getBoundingClientRect().width); k++) { await sleep(100); el = $(s.sel); } if (TR !== my) return;
  if (el) { el.scrollIntoView({ block: "center", behavior: "smooth" }); await sleep(380); }
  const c = my.card, last = my.i === TOUR.length - 1;
  c.innerHTML = `<div class="swap"><div class="sub" style="margin-bottom:6px">Step ${my.i + 1} of ${TOUR.length}</div><h4>${s.t}</h4><p>${s.b}</p><div class="row"><a href="#" id="tSkip" class="sub">${last ? "" : "Skip tour"}</a><span class="row" style="gap:8px">${my.i ? '<button class="btn sm" id="tBack">Back</button>' : ""}<button class="btn pri sm" id="tNext">${last ? "Finish" : "Next"}</button></span></div></div>`;
  $("#tSkip", c).onclick = ev => { ev.preventDefault(); endTour(); }; $("#tNext", c).onclick = () => tourGo(1); const bk = $("#tBack", c); if (bk) bk.onclick = () => tourGo(-1);
  const cw = Math.min(350, innerWidth * .92), ch = c.getBoundingClientRect().height, sp = my.spot;
  if (el) {
    const r = el.getBoundingClientRect(), pad = 8; sp.style.opacity = 1; sp.style.top = r.top - pad + "px"; sp.style.left = r.left - pad + "px"; sp.style.width = r.width + pad * 2 + "px"; sp.style.height = r.height + pad * 2 + "px";
    let top = r.bottom + pad + 16; if (top + ch > innerHeight - 12) top = r.top - pad - 16 - ch; if (top < 12) top = Math.max(12, Math.min(innerHeight - ch - 12, r.top + r.height / 2 - ch / 2));
    c.style.top = top + "px"; c.style.left = Math.max(12, Math.min(innerWidth - cw - 12, r.left + r.width / 2 - cw / 2)) + "px";
  } else { sp.style.opacity = 1; sp.style.width = sp.style.height = "0px"; sp.style.top = innerHeight / 2 + "px"; sp.style.left = innerWidth / 2 + "px"; c.style.top = Math.max(12, innerHeight / 2 - ch / 2) + "px"; c.style.left = Math.max(12, innerWidth / 2 - cw / 2) + "px"; }
}

/* ---------- Help page */
function renderHelp() {
  const el = $("#p-help"); if (el._built) return; el._built = 1;
  const d = (t, c, open) => `<details ${open ? "open" : ""}><summary>${t}</summary><div class="c">${c}</div></details>`;
  el.innerHTML = `<div class="rise"><div class="row" style="margin-bottom:14px;--i:0"><button class="btn pri" id="hTour">Take the guided tour</button><button class="btn" id="hSetup">Re-run setup</button></div><div class="help" style="--i:1">
  ${d("Quick start: 3 steps", `<ol><li><b>Press Run</b> at the top right. The bot starts watching the markets.</li><li><b>Choose your practice money</b> by clicking the account chip at the top. $1,000 is a sensible start.</li><li><b>Leave it running</b> and check <b>Home</b> once a day. The bot only trades when a strong move appears, so waiting is normal.</li></ol>`, true)}
  ${d("What does the bot actually do?", `<p>Every 4 hours, when a new <b>candle</b> closes, it checks each market. If the price has broken above its recent high (or below its recent low), that often marks the start of a trend, so the bot enters a trade in that direction. It immediately sets a <b>safety stop</b> and moves the stop along as the trade moves in its favor. The trade ends when the stop is hit or the trend flips.</p><p>It sizes every trade so that hitting the stop loses only a small, fixed share of your account (0.5% by default). Most trades lose a little; a few win big. That is how trend-following works.</p>`)}
  ${d("Reading the Home page", `<ul><li><b>Total value</b>: what your account is worth now, with profit since you started.</li><li><b>Sentinel insight</b>: a plain-English summary of what the bot is doing.</li><li><b>Profit, Today, Open trades, Win rate</b>: the numbers that matter most.</li><li><b>Safety systems</b>: how close you are to the daily loss limit and the emergency brake. Green is calm.</li><li><b>Open trades</b>: positions the bot holds right now.</li></ul>`)}
  ${d("Reading the Markets page", `<p>The chart shows 4-hour <b>candles</b> (green = price rose, red = price fell). Thin blue lines are the breakout levels the bot watches. Arrows mark the bot's buys and sells, and a dashed red line is the safety stop of an open trade. The list on the right shows every market; click one to switch. The sentence above the chart says what the bot is doing there.</p>`)}
  ${d("Keeping your money safe", `<ul><li><b>Practice mode</b> uses fake money by default.</li><li>Every trade has a <b>safety stop</b>.</li><li>A <b>daily loss limit</b> (3% by default) pauses new trades until tomorrow.</li><li>An <b>emergency brake</b> (20% drawdown) closes everything and halts the bot.</li><li>The red button at the top closes all positions instantly.</li><li>Hard caps limit trade size, number of trades and AI spending.</li></ul>`)}
  ${d("Adding markets and favorites", `<p>Press <b>+ Add market</b> on Home or Markets and search anything: stocks (Apple, Tesla...), ETFs, crypto perpetuals, forex pairs, gold, oil, indices. Press the <b>star</b> to pin a market to the top of your home screen. Each market has a tag: <b>Bot trades</b> (the bot may trade it) or <b>Watching</b> (price only). Press the &#10005; to remove one. Stocks are long-only (no shorting) and trade during market hours.</p>`)}
  ${d("Timeframes and speed", `<p>The chart has a timeframe switcher (1m to 1 day) for looking around. The <b>swing bot</b> trades on one timeframe, set in Settings (Pro). Faster is not better here: with real trading fees, our backtests found <b>only the 4-hour setting profitable</b>; 5 minute, 15 minute and 1 hour lost money. For quick trades use the <b>Trader</b> page's AI day trader instead, which is selective rather than constant.</p>`)}
  ${d("Day trading and chat (the Trader page)", `<p>The AI day trader scans your markets every 15 minutes. A rule-based scanner flags setups (breakouts, extreme RSI, volume surges); the AI then decides long, short or flat with a stop and target. Every step shows in <b>Live thoughts</b>. The <b>chat</b> lets you ask why it did something or what it is watching; it is read-only and cannot place trades. It needs an AI connection, uses practice money, and is <b>unproven</b>: check the results before trusting it.</p>`)}
  ${d("The AI helper", `<p>When a trade signal passes all safety checks, the AI reads a short briefing (trend, volatility, funding rate, open interest, how crowded the trade is) and rates the odds of follow-through. In <b>Advise</b> mode it only gives an opinion. We tested blocking trades on its opinion over 6,800 past signals and it did <b>not</b> improve results, so it never blocks by default. A live scorecard on the AI page (Pro mode) shows whether its ratings turn out to be useful. It costs about 0.05 cents per trade signal, with hard daily and lifetime caps.</p>`)}
  ${d("Practice vs. real money", `<p>Sentinel starts in practice mode. For exchange-style practice, create a free Bybit account, switch it to <b>Demo Trading</b>, make an API key (trading allowed, withdrawals NOT allowed) and enter it in Settings. Only consider real money after weeks of practice, with an amount you can afford to lose. The exchange connections have never been run against a real account.</p>`)}
  ${d("Honest expectations", `<p>Over 5 years of past data, the default setup earned a modest return (about 8% a year at 0.5% risk, with a worst dip near 20%). That is not a get-rich-quick machine and past results do not predict the future. Expect losing streaks. This is not financial advice.</p>`)}
  ${d("Glossary", `<dl>${Object.entries({ "Candle": GLOSS.candle, "Breakout": GLOSS.breakout, "Buy (long)": GLOSS.long, "Sell (short)": GLOSS.short, "Safety stop": GLOSS.stop, "Risk per trade": GLOSS.risk, "Drawdown": GLOSS.drawdown, "Win rate": GLOSS.winrate, "Practice mode": GLOSS.practice }).map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`)}
  ${d("Troubleshooting", `<ul><li><b>The page will not load:</b> make sure the black window opened by <code>run.bat</code> is still open.</li><li><b>No trades yet:</b> normal. The bot waits for strong breakouts.</li><li><b>"Data hiccup" on a market:</b> a price feed was briefly unreachable. It retries automatically.</li><li><b>"Too small for this account":</b> raise your practice balance; exchanges have minimum order sizes.</li><li><b>AI says no key:</b> add one on the AI helper page, or ignore it.</li><li><b>Everything stopped ("Halted"):</b> a safety limit tripped. Open Settings and press Resume after you have looked at why.</li></ul>`)}
  </div></div>`;
  $("#hTour").onclick = () => startTour(0); $("#hSetup").onclick = () => openWizard();
}

function guideInit() { if (S && !(S.cfg.ui || {}).onboarded) setTimeout(openWizard, 600); }
