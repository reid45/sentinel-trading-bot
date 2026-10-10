"use strict";
/* Markets catalog + favorites, AI connect sheet, AI page, Trader page (day trading, live thoughts, chat), swing timeframe.
   Declarations only: loaded before app.js, which calls into these once state has loaded. */
const PROV = id => (S.providers || []).find(p => p.id === id) || {};
const nameOf = i => i.name || NAMES[short(i)] || short(i);
const CATLBL = { crypto: "Crypto", stock: "Stock", etf: "ETF", forex: "Forex", commodity: "Commodity", index: "Index" };
const TFLBL = { "1m": "1-minute", "5m": "5-minute", "15m": "15-minute", "1h": "1-hour", "4h": "4-hour", "1d": "daily" };
const TFS = ["1m", "5m", "15m", "1h", "4h", "1d"];
const sortedInsts = () => [...S.cfg.instruments].sort((a, b) => (b.fav ? 1 : 0) - (a.fav ? 1 : 0));
function coinFor(i) {
  const s = short(i), crypto = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "LTC", "AVAX"];
  if (crypto.includes(s)) return `<span class="coin"><img src="vendor/coins/${s.toLowerCase()}.svg" alt="" loading="lazy"></span>`;
  let h = 0; for (const ch of i.symbol) h = (h * 31 + ch.charCodeAt(0)) % 360;
  const t = s.replace(/[^A-Z0-9]/gi, "").slice(0, s.length > 3 ? 3 : s.length) || "?";
  return `<span class="coin" style="background:linear-gradient(135deg,hsl(${h} 50% 34%),hsl(${(h + 40) % 360} 50% 20%))">${esc(t)}</span>`;
}
const favApi = (id, value) => api("/api/watchlist", { action: "fav", id, value }, true).then(refresh);
const tradeApi = (id, value) => api("/api/watchlist", { action: "trade", id, value }, true).then(refresh);
async function removeMarket(id, trigger) {
  const i = S.cfg.instruments.find(x => x.id === id); if (!i) return;
  if (!await confirmSheet("Remove " + short(i) + "?", "It leaves your list. If the bot is holding a trade in it, that trade is closed first.", "Remove", true, trigger)) return;
  await guard(async () => { await api("/api/watchlist", { action: "remove", id }); toast(short(i) + " removed", "info", "Done"); await refresh(); })();
}

/* ---------- Home: your markets (favorites first) */
function renderHomeMarkets() {
  const el = $("#hMarkets"); if (!el) return; const e = S.engine;
  const h = sortedInsts().map(i => {
    const t = e.status[i.id] || {}, sp = sparks[i.id] || {}, up = (sp.c || 0) >= 0, hold = e.positions.find(p => p.id === i.id || p.id === "day:" + i.id);
    return `<div class="mcard ${hold ? "holding" : ""}" data-id="${esc(i.id)}"><button class="star ${i.fav ? "on" : ""}" data-fav="${esc(i.id)}" data-tip="${i.fav ? "Remove from favorites" : "Favorite: pins to the top"}">&#9733;</button>
     <div class="mtop">${coinFor(i)}<div><div class="t1">${esc(short(i))}</div><div class="t2">${esc(nameOf(i))}</div></div></div>
     <div class="mpx"><b class="num">${px(t.price)}</b><span class="${up ? "up" : "down"}">${pct(sp.c || 0, 1)}</span></div><div class="mspark">${spark(sp.s, up)}</div>
     <div class="mfoot"><button class="tag ${i.enabled ? "on" : ""}" data-trade="${esc(i.id)}" data-tip="${i.enabled ? "The bot is allowed to trade this. Click to switch to watch-only." : "Watch only. Click to let the bot trade it."}">${hold ? "Holding" : i.enabled ? "Bot trades" : "Watching"}</button><button class="x" data-rm="${esc(i.id)}" data-tip="Remove from my list">&#10005;</button></div></div>`;
  }).join("") + `<button class="mcard add" id="addMkt2"><span>+</span>Add market</button>`;
  if (!setHTML(el, h)) return;
  $$(".mcard[data-id]", el).forEach(c => c.onclick = ev => {
    const t = ev.target.closest("[data-fav],[data-trade],[data-rm]"); const id = c.dataset.id;
    if (t && t.dataset.fav) { ev.stopPropagation(); favApi(id, !S.cfg.instruments.find(x => x.id === id).fav); }
    else if (t && t.dataset.trade) { ev.stopPropagation(); tradeApi(id, !S.cfg.instruments.find(x => x.id === id).enabled); }
    else if (t && t.dataset.rm) { ev.stopPropagation(); removeMarket(id, t); }
    else { sel = id; chartTf = null; go("trade"); }
  });
  $("#addMkt2", el).onclick = ev => openAdd(ev.currentTarget);
}

/* ---------- add-markets sheet (searchable catalog like a charting platform) */
let ADD = { cat: "all", q: "", rows: [], timer: null, trade: true };
function openAdd(trigger) {
  const cats = [["all", "All"], ["crypto", "Crypto"], ["stock", "Stocks"], ["etf", "ETFs"], ["forex", "Forex"], ["commodity", "Commodities"], ["index", "Indices"]];
  const sh = openSheet(`<div class="row" style="justify-content:space-between;margin-bottom:6px"><h2 style="margin:0;font-size:20px">Add markets</h2><button class="btn sm" id="adClose">Done</button></div>
   <p class="note" style="margin:0 0 12px">Search thousands of stocks, ETFs, crypto, forex, commodities and indices. Star a market to pin it to the top of your home screen.</p>
   <input id="adQ" placeholder="Search: Apple, Tesla, NVDA, BTC, gold, EURUSD..." autocomplete="off" style="font-size:15px">
   <div class="chips" id="adCats" style="margin:12px 0">${cats.map(([k, l]) => `<button data-c="${k}" class="${k === ADD.cat ? "on" : ""}">${l}</button>`).join("")}</div>
   <div class="row" style="margin-bottom:10px"><label class="sw"><input type="checkbox" id="adTrade" ${ADD.trade ? "checked" : ""}><span></span></label><span class="note" style="margin:0">Let the bot trade what I add <span class="sub">(off = just watch the price)</span></span></div>
   <div class="scroll addlist" id="adList"><div class="empty"><span class="spin"></span>loading</div></div>`, trigger);
  sh.classList.add("wide"); ADD.q = "";
  $("#adClose", sh).onclick = closeSheet;
  $("#adTrade", sh).onchange = e => ADD.trade = e.target.checked;
  $("#adCats", sh).onclick = e => { const b = e.target.closest("button"); if (b) { ADD.cat = b.dataset.c; $$("#adCats button").forEach(x => x.classList.toggle("on", x === b)); adSearch(); } };
  $("#adQ", sh).oninput = e => { ADD.q = e.target.value; clearTimeout(ADD.timer); ADD.timer = setTimeout(adSearch, 260); };
  $("#adQ", sh).focus(); adSearch();
}
async function adSearch() {
  const list = $("#adList"); if (!list) return; const q = ADD.q, c = ADD.cat;
  try { const d = await api("/api/catalog?q=" + encodeURIComponent(q) + "&cat=" + c + "&n=70", undefined, true); if (q !== ADD.q || !$("#adList")) return; ADD.rows = d.rows; adDraw(); }
  catch (e) { list.innerHTML = '<div class="empty">Search failed. Check your internet connection.</div>'; }
}
function adDraw() {
  const list = $("#adList"); if (!list) return;
  list.innerHTML = ADD.rows.length ? ADD.rows.map((r, n) => `<div class="adrow" data-n="${n}">${coinFor(r)}<div class="grow"><div class="t1">${esc(short(r))} <span class="pill">${CATLBL[r.cat] || r.cat}</span></div><div class="t2">${esc(r.name)}</div></div>
     <button class="star ${r.fav ? "on" : ""}" data-star="${n}" data-tip="Favorite (pins to the top)">&#9733;</button><button class="btn sm ${r.added ? "go" : "pri"}" data-add="${n}">${r.added ? "&#10003; Added" : "+ Add"}</button></div>`).join("") : '<div class="empty">No matches. Try a ticker or company name.</div>';
  $$("[data-add]", list).forEach(b => b.onclick = guard(async () => {
    const r = ADD.rows[+b.dataset.add]; b.disabled = true;
    if (r.added) { const id = S.cfg.instruments.find(i => i.market === r.market && i.symbol === r.symbol).id; await api("/api/watchlist", { action: "remove", id }, true); r.added = false; r.fav = false; }
    else { await api("/api/watchlist", { action: "add", items: [Object.assign({}, r, { fav: r.fav })], trade: ADD.trade }, true); r.added = true; toast(short(r) + " added to your list", "info", ADD.trade ? "Bot will trade it" : "Watching"); }
    await refresh(); adDraw();
  }));
  $$("[data-star]", list).forEach(b => b.onclick = guard(async () => {
    const r = ADD.rows[+b.dataset.star], v = !r.fav;
    if (!r.added) { await api("/api/watchlist", { action: "add", items: [Object.assign({}, r, { fav: v })], trade: ADD.trade }, true); r.added = true; }
    else { await api("/api/watchlist", { action: "fav", id: S.cfg.instruments.find(i => i.market === r.market && i.symbol === r.symbol).id, value: v }, true); }
    r.fav = v; await refresh(); adDraw();
  }));
  icons(list);
}

/* ---------- AI connect sheet (the one obvious place for an API key) */
let AIX = { prov: "deepseek", model: "", base: "", key: "", models: [], live: false, mode: "advise", show: false };
function aiState() {
  const a = S.cfg.ai, m = PROV(a.provider), connected = (a.provider === "claude_cli") || (!!a.has_api_key) || (m.needs_key === false && !!a.base_url);
  return { connected, label: m.label || a.provider, off: a.mode === "off" };
}
function aiChipUpdate() {
  const c = $("#aiChip"); if (!c || !S) return; const st = aiState();
  c.className = "chip btn" + (!st.connected && !st.off ? " need" : "");
  c.innerHTML = `<span class="led ${st.connected && !st.off ? "on" : st.off ? "" : "warn"}"></span>` + (st.off ? "AI off" : st.connected ? "AI · " + esc(st.label) : "Connect AI");
}
function openAI(trigger) {
  const a = S.cfg.ai; AIX = { prov: a.provider, model: a.model, base: a.base_url, key: "", models: PROV(a.provider).models || [], live: false, mode: a.mode === "off" ? "off" : "advise", show: false };
  const sh = openSheet(`<div id="aiRoot"></div>`, trigger); sh.classList.add("wide"); aiDraw();
}
function aiDraw() {
  const root = $("#aiRoot"); if (!root) return; const p = PROV(AIX.prov), saved = S.cfg.ai.provider === AIX.prov && S.cfg.ai.has_api_key;
  const groups = S.groups || [];
  root.innerHTML = `<div class="row" style="justify-content:space-between"><h2 style="margin:0;font-size:20px">Connect an AI</h2><button class="btn sm" id="aiX">Close</button></div>
   <p class="note" style="margin:6px 0 14px">Pick a provider, paste your key, press Save. Works with ${(S.providers || []).length} services: anything that follows the OpenAI format, plus Claude, Gemini and local models. Your key stays on this computer.</p>
   ${groups.map(g => `<div class="label" style="margin:12px 0 6px">${esc(g)}</div><div class="provgrid">${(S.providers || []).filter(x => x.group === g).map(x => `<button data-p="${x.id}" class="${x.id === AIX.prov ? "on" : ""}"><b>${esc(x.label)}</b><span>${esc(x.tag)}</span></button>`).join("")}</div>`).join("")}
   <div class="card" style="margin-top:16px;background:rgba(255,255,255,.03)"><div class="note" style="margin:0 0 12px">${esc(p.note || "")}</div>
    <div class="form">
     ${p.needs_key !== false ? `<div style="grid-column:1/-1"><label class="f">API key ${saved ? '<span class="up">(saved - leave empty to keep it)</span>' : ""} ${p.key_url ? `<a href="${esc(p.key_url)}" target="_blank" rel="noopener" style="margin-left:8px">Get a key &rarr;</a>` : ""}</label><div class="row" style="flex-wrap:nowrap"><input id="aiKeyIn" type="${AIX.show ? "text" : "password"}" autocomplete="off" placeholder="${saved ? "&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;" : "Paste your API key here"}" value="${esc(AIX.key)}" style="font-size:15px"><button class="btn sm" id="aiShow">${AIX.show ? "Hide" : "Show"}</button></div></div>` : ""}
     ${p.id !== "claude_cli" ? `<div><label class="f">Model <button class="btn sm" id="aiRefresh" style="margin-left:6px" data-tip="Ask the provider which models your key can use (free)">Refresh list</button></label><select id="aiModelSel">${[...new Set([AIX.model, ...AIX.models])].filter(Boolean).map(m => `<option ${m === AIX.model ? "selected" : ""}>${esc(m)}</option>`).join("")}<option value="__other">Type another...</option></select></div>` : '<div><label class="f">Model</label><input id="aiModelIn" value="' + esc(AIX.model) + '"></div>'}
     ${p.editable_url || p.needs_key === false && p.id !== "claude_cli" ? `<div><label class="f">Server address</label><input id="aiBase" value="${esc(AIX.base)}" placeholder="https://.../v1"></div>` : ""}
    </div>
    <div class="seg" id="aiModeSeg" style="margin-top:14px"><div class="thumb"></div><button data-v="advise">Advise</button><button data-v="off">AI off</button></div>
    <span class="sub" style="margin-left:10px">Advise = AI rates trades and powers the day trader and chat. It never touches your money by itself.</span>
    <div class="row" style="margin-top:16px"><button class="btn pri" id="aiSaveBtn">Save &amp; connect</button><button class="btn" id="aiTestBtn" data-tip="Sends one tiny message (about 25 tokens)">Test connection</button><span class="sub" id="aiOut"></span></div></div>`;
  icons(root); const seg_ = seg($("#aiModeSeg"), v => AIX.mode = v); seg_.set(AIX.mode);
  $("#aiX").onclick = closeSheet;
  $$(".provgrid button", root).forEach(b => b.onclick = () => { AIX.key = $("#aiKeyIn") ? $("#aiKeyIn").value : AIX.key; const np = PROV(b.dataset.p); AIX.prov = np.id; AIX.model = (S.cfg.ai.provider === np.id ? S.cfg.ai.model : np.model); AIX.base = (S.cfg.ai.provider === np.id ? S.cfg.ai.base_url : np.base_url); AIX.models = np.models || []; AIX.live = false; aiDraw(); });
  const sw = $("#aiShow"); if (sw) sw.onclick = () => { AIX.key = $("#aiKeyIn").value; AIX.show = !AIX.show; aiDraw(); };
  const ms = $("#aiModelSel"); if (ms) ms.onchange = () => { if (ms.value === "__other") { const v = prompt("Model name:"); if (v) { AIX.model = v; aiDraw(); } else ms.value = AIX.model; } else AIX.model = ms.value; };
  const mi = $("#aiModelIn"); if (mi) mi.oninput = () => AIX.model = mi.value;
  const bs = $("#aiBase"); if (bs) bs.oninput = () => AIX.base = bs.value;
  const rf = $("#aiRefresh"); if (rf) rf.onclick = guard(async () => { $("#aiOut").innerHTML = '<span class="spin"></span>asking the provider'; AIX.key = ($("#aiKeyIn") || {}).value || AIX.key; const r = await api("/api/ai/models", aiBody(), true); AIX.models = r.models; AIX.live = r.live; if (!AIX.models.includes(AIX.model) && AIX.models.length) AIX.model = AIX.models[0]; aiDraw(); $("#aiOut").innerHTML = r.live ? `<span class="up">${r.models.length} models found</span>` : `<span class="warn">Showing suggestions${r.error ? ": " + esc(r.error) : ""}</span>`; });
  $("#aiTestBtn").onclick = guard(async () => { AIX.key = ($("#aiKeyIn") || {}).value || AIX.key; $("#aiOut").innerHTML = '<span class="spin"></span>testing'; const r = await api("/api/ai/test", aiBody(), true); $("#aiOut").innerHTML = r.ok ? `<span class="up">Connected</span> &middot; ${r.ms} ms &middot; ${r.tokens} tokens` : `<span class="down">${esc(r.error)}</span>`; });
  $("#aiSaveBtn").onclick = guard(async () => {
    AIX.key = ($("#aiKeyIn") || {}).value || AIX.key; const p2 = PROV(AIX.prov); const body = Object.assign({ mode: AIX.mode }, aiBody()); delete body.max_tokens;
    await api("/api/config", { ai: { mode: AIX.mode, provider: AIX.prov, base_url: body.base_url || p2.base_url, model: AIX.model || p2.model, api_key: AIX.key } }); closeSheet(); toast(AIX.mode === "off" ? "AI switched off" : p2.label + " connected", "win", "Saved"); await refresh(); if (page === "ai") renderAI(true);
  });
}
function aiBody() { const p = PROV(AIX.prov); return { provider: AIX.prov, base_url: AIX.base || p.base_url, model: AIX.model, api_key: AIX.key }; }

/* ---------- AI page */
function buildAI() {
  $("#p-ai").innerHTML = `<div class="rise"><div class="grid g2"><div class="card" style="--i:0"><h3>AI connection <span class="pill" id="aiState"></span></h3><div id="aiConn"></div>
   <p class="note">The AI reads compact market briefings and helps in three places: it <b>rates each swing-trade signal</b>, it makes the <b>decisions in day trading</b>, and it <b>answers your questions in chat</b>. Typical cost is a few hundredths of a cent per decision.</p></div>
   <div class="card" style="--i:1"><h3>Spending guardrails</h3><div id="aiBudget"></div><div class="sub" id="aiBudgetNote" style="margin-top:12px"></div></div></div>
   <div class="grid g2 pro" style="margin-top:14px"><div class="card" style="--i:2"><h3>Does the AI actually help? <span class="sub">live scorecard</span></h3><div id="aiScore"></div></div><div class="card" style="--i:3"><h3>Historical test <span class="sub">6,800 signals</span></h3><div id="aiVal"></div></div></div>
   <div class="card" style="margin-top:14px;--i:4"><h3>Recent AI decisions <button class="btn sm" id="aiBrief" data-tip="One call, about 300 tokens">Write a brief</button></h3><div class="note" id="aiBriefOut" style="white-space:pre-wrap;margin-bottom:8px"></div><div class="scroll" id="aiLog"></div></div></div>`;
  $("#aiBrief").onclick = guard(async () => { $("#aiBriefOut").innerHTML = '<span class="spin"></span>writing'; const r = await api("/api/ai/brief", {}, true); $("#aiBriefOut").textContent = r.ok ? r.text : "Failed: " + r.error; });
}
let aiT = 0;
async function renderAI(force) {
  if (!$("#aiConn")) buildAI(); const a = S.cfg.ai, st = aiState();
  setHTML($("#aiState"), st.off ? "Off" : st.connected ? "Connected" : "Not connected"); $("#aiState").className = "pill " + (st.off ? "" : st.connected ? "up" : "warn");
  setHTML($("#aiConn"), `<div class="row" style="justify-content:space-between;margin-bottom:12px"><div><div style="font-size:18px;font-weight:600">${esc(st.label)}</div><div class="sub">${esc(a.provider === "claude_cli" ? "Claude account" : a.model)}</div></div><button class="btn pri" id="aiOpen">${st.connected ? "Change provider or key" : "Connect an AI"}</button></div>`);
  $("#aiOpen").onclick = e => openAI(e.currentTarget);
  if (!force && Date.now() - aiT < 8000) return; aiT = Date.now();
  const l = await api("/api/ai/log", undefined, true), sp = l.spend, b = l.budget, hitr = sp.hit + sp.miss ? sp.hit / (sp.hit + sp.miss) * 100 : 0;
  setHTML($("#aiBudget"), meter("Today (money)", sp.usd_day / b.max_usd_day, "$" + sp.usd_day.toFixed(4) + " / $" + b.max_usd_day, "Hard daily dollar cap. When reached the AI simply stops; trading continues.") + meter("Today (calls)", sp.calls_day / b.max_calls_day, sp.calls_day + " / " + b.max_calls_day, "Hard daily call cap.") + meter("Lifetime cap", sp.usd / b.max_usd_total, "$" + sp.usd.toFixed(3) + " / $" + b.max_usd_total, "Total the app will ever spend on AI until you raise this."));
  $("#aiBudgetNote").innerHTML = `${sp.calls} calls &middot; ${sp.tokens.toLocaleString()} tokens &middot; cache hit rate <b>${hitr.toFixed(0)}%</b> &middot; ${l.peak ? "peak" : "off-peak"} pricing now.`;
  const sc = l.score; setHTML($("#aiScore"), `<p class="note" style="margin-top:0">Each swing trade is tagged with the AI's rating. ${sc.n < 30 ? `<b>Only ${sc.n} rated trades so far: too early to conclude anything.</b>` : ""}</p><table><tr><th>AI rating</th><th>Trades</th><th>Win rate</th><th>Avg result</th></tr>${sc.rows.map(r => `<tr><td class="t">${r.label}</td><td>${r.n}</td><td>${r.win == null ? "--" : r.win.toFixed(0) + "%"}</td><td class="${cls(r.avg || 0)}">${r.avg == null ? "--" : sm(r.avg)}</td></tr>`).join("")}</table>`);
  const v = l.validation; setHTML($("#aiVal"), v ? `<table><tr><th>Setup</th><th>Trades</th><th>Sharpe*</th><th>Return*</th></tr>${v.rows.map((r, i) => `<tr><td class="t">${esc(r.label)}</td><td>${r.trades}</td><td class="${i ? cls(r.oos - v.rows[0].oos) : ""}">${r.oos.toFixed(2)}</td><td class="${i ? cls(r.oos_ret - v.rows[0].oos_ret) : ""}">${pct(r.oos_ret, 1)}</td></tr>`).join("")}</table><p class="sub">*on data the tuning never saw. ${esc(v.verdict)}</p>` : "");
  setHTML($("#aiLog"), l.rows.length ? `<table><tr><th>Time</th><th>Market</th><th>Decision</th><th>Said</th></tr>${l.rows.map(r => `<tr><td class="mono" style="color:var(--tx3)">${dt(r.t)}</td><td class="t">${esc(r.inst)}</td><td><span class="pill ${r.take ? "up" : "down"}">${r.take ? "Go" : "Pass"}</span></td><td class="t">${esc(r.reply || "")}</td></tr>`).join("")}</table>` : '<div class="empty">No decisions yet.</div>');
}

/* ---------- TRADER page: day trading controls, live thoughts, chat */
const TH = { rows: [], last: 0, timer: 0 }, CH = { msgs: [], busy: false };
const KINDS = { candidate: ["activity", "Spotted", "acc"], decision: ["sparkles", "Decided", "acc"], open: ["trending-up", "Opened", "up"], close: ["x", "Closed", ""], skip: ["pause", "Passed", ""], manage: ["refresh-cw", "Managing", ""] };
function buildTrader() {
  $("#p-trader").innerHTML = `<div class="rise"><div class="trader">
   <div class="tcol"><div class="card" style="--i:0"><h3>Day trading <span class="pill" id="dState"></span></h3>
     <p class="note" style="margin:0 0 14px">Quick trades on <b>15-minute</b> candles. A scanner finds setups, then the AI decides with full market data. Every step appears below.</p>
     <div class="form"><div><label class="f">Markets to watch</label><select id="dMarkets"><option value="3">3 (favorites first)</option><option value="6">6</option><option value="10">10</option></select></div>
      <div><label class="f">How picky is it?</label><select id="dPicky"><option value="50">Relaxed: more trades</option><option value="60">Balanced</option><option value="75">Strict: fewer, higher confidence</option></select></div>
      <div><label class="f">Longest time in a trade</label><select id="dHold"><option value="2">30 minutes</option><option value="4">1 hour</option><option value="8">2 hours</option><option value="16">4 hours</option><option value="24">6 hours</option></select></div></div>
     <div class="row" style="margin-top:16px"><button class="btn pri" id="dToggle" style="min-width:200px">Start day trading</button><span class="sub" id="dNote"></span></div>
     <div class="grid g5" id="dStats" style="margin-top:16px;grid-template-columns:repeat(auto-fit,minmax(110px,1fr))"></div>
     <div class="callout" style="margin-top:14px">Honest note: in our backtests every rule-based strategy lost money at this speed because trading fees add up. The AI day trader is <b>unproven</b>, so it runs on practice money and the scorecard will show whether it truly works. Start small.</div></div>
    <div class="card" style="--i:1"><h3>Live thoughts <span class="sub" id="thN"></span></h3><div class="thoughts" id="thFeed"></div></div></div>
   <div class="card chat" style="--i:2"><h3>Ask the trader <span class="sub">read-only: it can explain, it can't trade</span></h3><div class="msgs" id="chMsgs"></div>
     <div class="sugg" id="chSugg"></div><form id="chForm" class="row" style="flex-wrap:nowrap"><input id="chIn" placeholder="Ask anything: Why did you buy? What are you watching?" autocomplete="off"><button class="btn pri" id="chSend">Send</button></form></div></div></div>`;
  const patch = d => api("/api/config", { day: d }, true).then(refresh);
  $("#dMarkets").onchange = e => patch({ markets: +e.target.value }); $("#dPicky").onchange = e => patch({ min_conf: +e.target.value }); $("#dHold").onchange = e => patch({ max_hold_bars: +e.target.value });
  $("#dToggle").onclick = guard(async e => {
    const on = S.cfg.day.enabled;
    if (!on && !aiState().connected) { openAI(e.currentTarget); return toast("Day trading needs an AI to make decisions. Connect one first.", "info", "One more step"); }
    if (!on) { const b = S.cfg.ai.budget || {}; await api("/api/config", { day: { enabled: true }, ai: { budget: { max_calls_day: Math.max(b.max_calls_day || 0, 600), max_usd_day: Math.max(b.max_usd_day || 0, 0.6), max_usd_total: Math.max(b.max_usd_total || 0, 5) } } }, true); if (!S.engine.running) await api("/api/engine", { action: "start" }, true); toast("Scanning your markets every 15 minutes.", "win", "Day trading is on"); }
    else { await api("/api/config", { day: { enabled: false } }, true); toast("No new day trades. Open ones keep their stops.", "info", "Day trading paused"); }
    await refresh(); renderTrader();
  });
  $("#chForm").onsubmit = ev => { ev.preventDefault(); const t = $("#chIn").value.trim(); if (t) chatSend(t); };
  const sg = ["How am I doing today?", "What are you watching right now?", "Why did you take your last trade?", "What's the riskiest thing in my account?"];
  $("#chSugg").innerHTML = sg.map(s => `<button type="button" class="sg">${esc(s)}</button>`).join(""); $$(".sg", $("#chSugg")).forEach(b => b.onclick = () => chatSend(b.textContent));
  chatDraw();
}
function renderTrader() {
  if (!$("#dToggle")) buildTrader(); const d = S.cfg.day, e = S.engine, ds = e.day || {}, st = aiState();
  if (!document.activeElement || !["dMarkets", "dPicky", "dHold"].includes(document.activeElement.id)) { $("#dMarkets").value = d.markets; $("#dPicky").value = String(d.min_conf); $("#dHold").value = String(d.max_hold_bars); }
  $("#dToggle").textContent = d.enabled ? "Pause day trading" : "Start day trading"; $("#dToggle").className = "btn " + (d.enabled ? "stop" : "pri");
  const ps = e.positions.filter(p => String(p.id).startsWith("day:")), pnl = ps.reduce((a, p) => a + p.upnl, 0);
  setHTML($("#dState"), d.enabled ? "ON" : "OFF"); $("#dState").className = "pill " + (d.enabled ? "up" : "");
  $("#dNote").textContent = !st.connected ? "Needs an AI connection first" : d.enabled ? "Scanning " + d.markets + " markets every 15 min" : "Practice money only";
  setHTML($("#dStats"), kpi("Scans", ds.scans || 0, "candles checked", "") + kpi("Setups", ds.candidates || 0, "sent to the AI", "") + kpi("Day trades", ds.trades || 0, "opened", "") + kpi("Open now", ps.length, ps.length ? sm(pnl) : "none", cls(pnl)));
  thPoll(); chatDraw();
}
async function thPoll() {
  if (Date.now() - TH.timer < 2500) return; TH.timer = Date.now();
  try { const rows = await api("/api/thoughts?after=" + TH.last + "&n=60", undefined, true); if (rows.length) { rows.reverse().forEach(r => { TH.rows.unshift(Object.assign(r, { fresh: TH.last > 0 })); }); TH.rows = TH.rows.slice(0, 80); TH.last = Math.max(TH.last, ...rows.map(r => r.id)); } } catch (e) { }
  thDraw();
}
function thDraw() {
  const el = $("#thFeed"); if (!el) return; $("#thN").textContent = TH.rows.length ? TH.rows.length + " recent" : "";
  const h = TH.rows.length ? TH.rows.map(r => { const k = KINDS[r.kind] || ["activity", r.kind, ""]; return `<div class="th ${r.fresh ? "new" : ""}"><span class="ico ${k[2]}"><svg class="i" data-i="${k[0]}"></svg></span><div><div class="thh"><b>${k[1]}</b><span class="chip">${esc(short({ symbol: r.symbol || "" }) || "system")}</span><span class="mono" style="color:var(--tx3)">${tm(r.t)}</span></div><div class="tht">${esc(r.text)}</div></div></div>`; }).join("") : '<div class="empty">Nothing yet. When day trading is on, you will see every scan, decision and trade here as it happens.</div>';
  if (setHTML(el, h)) TH.rows.forEach(r => r.fresh = false);
}
function chatDraw() {
  const el = $("#chMsgs"); if (!el) return;
  const h = CH.msgs.length ? CH.msgs.map(m => m.role === "user" ? `<div class="bub me">${esc(m.content)}</div>` : `<div class="bub ai">${(m.steps || []).map(s => `<span class="stp"><svg class="i" data-i="zap"></svg>${esc(({ account: "Checked your account", positions: "Looked at open positions", thoughts: "Read my recent thoughts", market: "Looked up " + ((s.args || {}).symbol || "a market"), journal: "Read the trade journal", derivs: "Fetched funding and open interest", candles: "Read recent candles" })[s.tool] || s.tool)}</span>`).join("")}<div class="ans">${esc(m.content)}</div>${m.usd ? `<div class="sub">$${m.usd.toFixed(4)}</div>` : ""}</div>`).join("") + (CH.busy ? '<div class="bub ai"><span class="skel" style="display:block;width:140px;height:14px"></span></div>' : "") : '<div class="empty">Ask me anything about your account, the markets, or why I made a decision.</div>';
  if (setHTML(el, h)) el.scrollTop = el.scrollHeight;
}
async function chatSend(text) {
  if (CH.busy) return; CH.msgs.push({ role: "user", content: text }); CH.busy = true; $("#chIn").value = ""; chatDraw();
  try {
    const r = await api("/api/chat", { messages: CH.msgs.map(m => ({ role: m.role, content: m.content })) }, true);
    CH.msgs.push({ role: "assistant", content: r.answer, steps: r.steps, usd: r.usd });
    if (r.error === "nokey") toast("Connect an AI to chat.", "info", "AI needed");
  } catch (e) { CH.msgs.push({ role: "assistant", content: "Something went wrong: " + e.message }); }
  CH.busy = false; chatDraw();
}

/* ---------- swing bot timeframe (Settings) */
async function setSpeed(tf, trigger) {
  const hasPos = S.engine.positions.some(p => !String(p.id).startsWith("day:"));
  const fast = ["1m", "5m", "15m", "1h"].includes(tf);
  if (!await confirmSheet("Switch the swing bot to " + TFLBL[tf] + " candles?", (hasPos ? "Open swing trades will be closed first. " : "") + (fast ? "<b>Be careful:</b> in our backtests the classic rules <b>lost money</b> at this speed because trading fees add up. 4-hour is the only setting that was profitable. Use the Lab to check before trusting it." : "This is the setting our backtests support."), "Switch", fast, trigger)) return;
  await guard(async () => { await api("/api/speed", { tf, flatten: true }); toast("Now trading on " + TFLBL[tf] + " candles", "info", "Updated"); await refresh(); fillSettings(true); })();
}
