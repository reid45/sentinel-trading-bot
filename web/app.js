"use strict";
/* Sentinel front end. Plain JS, no build step. Polls the local server only (never any third-party API). */
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const LS = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) { } } };
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
let S = null, page = "trade", sel = null, simple = LS.get("simple", "1") === "1", soundOn = LS.get("sound", "0") === "1", lastEv = null;
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v, d = 2) => (v < 0 ? "-" : "") + "$" + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const sm = (v, d = 2) => (v >= 0 ? "+" : "-") + money(Math.abs(v), d).replace("-", "");
const pct = (v, d = 2) => (v >= 0 ? "+" : "") + v.toFixed(d) + "%";
const cls = v => v > 0 ? "up" : v < 0 ? "down" : "";
const px = v => v == null ? "--" : v >= 1000 ? v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : v >= 10 ? v.toFixed(3) : v >= 1 ? v.toFixed(4) : v.toPrecision(4);
const dt = t => new Date(t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const tm = t => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const dur = ms => ms <= 0 ? "now" : ms >= 3600000 ? Math.floor(ms / 3600000) + "h " + Math.floor(ms % 3600000 / 60000) + "m" : Math.max(1, Math.floor(ms / 60000)) + "m";
const NAMES = { BTC: "Bitcoin", ETH: "Ethereum", SOL: "Solana", BNB: "BNB", XRP: "XRP", DOGE: "Dogecoin", ADA: "Cardano", LINK: "Chainlink", LTC: "Litecoin", AVAX: "Avalanche", EURUSD: "Euro / US Dollar", GBPUSD: "Pound / US Dollar", USDJPY: "US Dollar / Yen" };
const short = i => i.symbol.replace("=X", "").replace("USDT", "");
const coinHTML = i => { const s = short(i); return ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK", "LTC", "AVAX"].includes(s) ? `<span class="coin"><img src="vendor/coins/${s.toLowerCase()}.svg" alt="" loading="lazy"></span>` : `<span class="coin">${esc(s.slice(0, 3))}</span>`; };
const insts = () => S.cfg.instruments.filter(i => i.enabled);

/* ---------- dom helpers */
function icons(root = document) { $$("svg[data-i]", root).forEach(s => { if (s.dataset.done) return; s.setAttribute("viewBox", "0 0 24 24"); s.innerHTML = (window.ICONS || {})[s.dataset.i] || ""; s.dataset.done = 1; }); }
function setHTML(el, h) { if (el._h === h) return false; el._h = h; el.innerHTML = h; icons(el); return true; }
function swap(fn) { if (document.startViewTransition && !reduced) document.startViewTransition(fn); else fn(); }
const bar = { n: 0, start() { this.n++; const b = $("#bar"); b.style.opacity = 1; b.style.width = "35%"; }, done() { if (--this.n > 0) return; const b = $("#bar"); b.style.width = "100%"; setTimeout(() => { b.style.opacity = 0; b.style.width = "0"; }, 350); } };
async function api(path, body, quiet) {
  if (!quiet) bar.start();
  try {
    const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || r.statusText);
    return j;
  } finally { if (!quiet) bar.done(); }
}
const guard = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, "loss", "Something went wrong"); } };
function toast(msg, kind = "info", title = "") {
  const d = document.createElement("div"); d.className = "toast " + kind; d.innerHTML = (title ? `<b>${esc(title)}</b>` : "") + esc(msg); $("#toasts").appendChild(d);
  setTimeout(() => { d.classList.add("out"); setTimeout(() => d.remove(), 320); }, 4600);
}
let actx = null;
function beep(f) { if (!soundOn) return; try { actx = actx || new (window.AudioContext || window.webkitAudioContext)(); let t = actx.currentTime; f.forEach(x => { const o = actx.createOscillator(), g = actx.createGain(); o.frequency.value = x; g.gain.setValueAtTime(.05, t); g.gain.exponentialRampToValueAtTime(.0001, t + .12); o.connect(g).connect(actx.destination); o.start(t); o.stop(t + .12); t += .1; }); } catch (e) { } }
/* number roll: only the digits that changed animate (inspired by transitions.dev's digit flip; own implementation) */
function roll(el, text) {
  const prev = el.dataset.v || ""; if (prev === text) return; el.dataset.v = text; el.setAttribute("aria-label", text);
  const c = [...text], p = [...prev], off = p.length - c.length;
  el.innerHTML = c.map((ch, i) => { const o = p[i + off]; return (!prev || o === undefined || o === ch || !/[0-9]/.test(ch) || reduced) ? `<span class="ch">${esc(ch)}</span>` : `<span class="ch r" style="--d:${(c.length - i) * 24}ms"><i class="o">${esc(o)}</i><i class="n">${esc(ch)}</i></span>`; }).join("");
  if (prev) { const n = parseFloat(text.replace(/[^0-9.\-]/g, "")), o = parseFloat(prev.replace(/[^0-9.\-]/g, "")); if (n !== o) { el.classList.remove("flash-up", "flash-down"); void el.offsetWidth; el.classList.add(n > o ? "flash-up" : "flash-down"); } }
}
function seg(el, onpick) {
  const th = $(".thumb", el), btns = $$("button", el);
  const move = () => { const a = $("button.on", el); if (!a) { th.style.opacity = 0; return; } th.style.opacity = 1; th.style.width = a.offsetWidth + "px"; th.style.transform = `translateX(${a.offsetLeft - 3}px)`; };
  el.set = v => { btns.forEach(b => b.classList.toggle("on", b.dataset.v === v)); requestAnimationFrame(move); };
  btns.forEach(b => b.onclick = () => { el.set(b.dataset.v); onpick && onpick(b.dataset.v); });
  el.move = move; return el;
}
function words(el, text) {
  if (el._t === text) return; el._t = text; let n = 0;
  el.innerHTML = esc(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").split(/(<b>.*?<\/b>|\s+)/).map(w => /^\s*$/.test(w) || !w ? w : (w.startsWith("<b>") ? `<span class="w" style="--i:${n++}">${w}</span>` : `<span class="w" style="--i:${n++}">${w}</span>`)).join("");
}

/* ---------- sheets (modal with origin-aware spring) */
let sheetOpen = false;
function openSheet(html, trigger) {
  const sc = $("#scrim"), sh = $("#sheet"); sh.className = "sheet"; sh.innerHTML = html; icons(sh);
  const r = (trigger || {}).getBoundingClientRect ? trigger.getBoundingClientRect() : { left: innerWidth / 2, top: innerHeight / 2, width: 0, height: 0 };
  sc.classList.remove("closing"); sc.classList.add("on"); sheetOpen = true;
  const s = sh.getBoundingClientRect(); sh.style.setProperty("--ox", (r.left + r.width / 2 - s.left) + "px"); sh.style.setProperty("--oy", (r.top + r.height / 2 - s.top) + "px");
  return sh;
}
function closeSheet() { if (!sheetOpen) return; sheetOpen = false; const sc = $("#scrim"); sc.classList.add("closing"); setTimeout(() => { sc.classList.remove("on", "closing"); $("#sheet").className = "sheet"; $("#sheet").innerHTML = ""; }, 230); }
$("#scrim").onmousedown = e => { if (e.target.id === "scrim") closeSheet(); };
function confirmSheet(title, body, ok, danger, trigger) {
  return new Promise(res => {
    const sh = openSheet(`<h2 style="margin:0 0 8px;font-size:18px">${esc(title)}</h2><p class="note">${body}</p><div class="row" style="margin-top:18px;justify-content:flex-end"><button class="btn" id="cn">Cancel</button><button class="btn ${danger ? "danger" : "pri"}" id="ok">${esc(ok)}</button></div>`, trigger);
    $("#cn", sh).onclick = () => { closeSheet(); res(false); }; $("#ok", sh).onclick = () => { closeSheet(); res(true); };
  });
}

/* ---------- tooltips */
const tip = $("#tip");
document.addEventListener("mouseover", e => { const t = e.target.closest("[data-tip]"); if (t) { tip.textContent = t.dataset.tip; tip.style.display = "block"; } });
document.addEventListener("mousemove", e => { if (tip.style.display === "block") { tip.style.left = Math.min(e.clientX + 14, innerWidth - 320) + "px"; tip.style.top = e.clientY + 18 + "px"; } });
document.addEventListener("mouseout", e => { if (e.target.closest("[data-tip]")) tip.style.display = "none"; });

/* ---------- charts (TradingView Lightweight Charts) */
const COL = { up: "#2ed3a0", down: "#ff5e7a", acc: "#7c9cff", acc2: "#b48cff", warn: "#ffb454" };
function mkChart(el, extra) {
  return LightweightCharts.createChart(el, Object.assign({
    autoSize: true, layout: { background: { type: "solid", color: "rgba(0,0,0,0)" }, textColor: "#6a7280", fontFamily: "Inter, system-ui, sans-serif", fontSize: 11 },
    grid: { vertLines: { color: "rgba(255,255,255,.025)" }, horzLines: { color: "rgba(255,255,255,.04)" } },
    crosshair: { mode: 1, vertLine: { color: "rgba(255,255,255,.28)", style: 3, labelBackgroundColor: "#242a36" }, horzLine: { color: "rgba(255,255,255,.28)", style: 3, labelBackgroundColor: "#242a36" } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: .08, bottom: .2 } }, timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 5 },
    localization: { locale: navigator.language }
  }, extra || {}));
}
const T = ms => Math.floor(ms / 1000);
const prec = p => p >= 1000 ? 2 : p >= 100 ? 3 : p >= 10 ? 3 : p >= 1 ? 4 : 6;
const TC = { chart: null, candle: null, vol: null, ov: {}, lines: [], inst: null, data: null, last: null, live: null, ready: false };
function ensureChart() {
  if (TC.chart) return;
  TC.chart = mkChart($("#chart")); const c = TC.chart;
  TC.candle = c.addCandlestickSeries({ upColor: COL.up, downColor: COL.down, borderVisible: false, wickUpColor: COL.up, wickDownColor: COL.down });
  TC.vol = c.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  c.priceScale("vol").applyOptions({ scaleMargins: { top: .84, bottom: 0 } });
  c.subscribeCrosshairMove(p => {
    const el = $("#ohlc"); if (!el) return; const d = p.seriesData && p.seriesData.get(TC.candle);
    el.innerHTML = d ? `<span>O ${px(d.open)}</span><span>H ${px(d.high)}</span><span>L ${px(d.low)}</span><span>C <b class="${d.close >= d.open ? "up" : "down"}">${px(d.close)}</b></span>` : "";
  });
}
async function loadChart(force) {
  const i = S.cfg.instruments.find(x => x.id === sel); if (!i) return;
  ensureChart();
  const d = await api("/api/candles?inst=" + encodeURIComponent(sel), undefined, true); if (d.error || sel !== i.id) return;
  TC.data = d; const cs = d.candles, n = cs.length - (d.forming ? 1 : 0), p = prec(cs[n - 1][4]);
  TC.candle.applyOptions({ priceFormat: { type: "price", precision: p, minMove: Math.pow(10, -p) } });
  TC.candle.setData(cs.map(c => ({ time: T(c[0]), open: c[1], high: c[2], low: c[3], close: c[4] })));
  TC.vol.setData(cs.map(c => ({ time: T(c[0]), value: c[5], color: c[4] >= c[1] ? "rgba(46,211,160,.22)" : "rgba(255,94,122,.22)" })));
  if (TC.inst !== sel) { Object.values(TC.ov).forEach(s => TC.chart.removeSeries(s)); TC.ov = {}; }
  const style = { upper: [COL.acc, "Breakout high"], lower: [COL.acc, "Breakout low"], trend: [COL.warn, "Trend (EMA)"], fast: [COL.acc, "Fast EMA"], slow: [COL.acc2, "Slow EMA"] };
  Object.entries(d.overlays).forEach(([k, arr]) => {
    if (!TC.ov[k]) TC.ov[k] = TC.chart.addLineSeries({ color: style[k][0], lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, lineStyle: k === "upper" || k === "lower" ? 0 : 2 });
    TC.ov[k].setData(arr.map((v, j) => v == null ? null : { time: T(cs[j][0]), value: v }).filter(Boolean));
  });
  $("#legend").innerHTML = Object.keys(d.overlays).filter(k => k !== "lower").map(k => `<span><i style="background:${style[k][0]}"></i>${style[k][1]}</span>`).join("");
  const at = t => { let b = cs[0][0]; for (const c of cs) { if (c[0] <= t) b = c[0]; else break; } return T(b); };
  const mk = [];
  d.trades.forEach(t => { mk.push({ time: at(t.t_open), position: t.side > 0 ? "belowBar" : "aboveBar", color: t.side > 0 ? COL.up : COL.down, shape: t.side > 0 ? "arrowUp" : "arrowDown", text: t.side > 0 ? "Buy" : "Sell" }); mk.push({ time: at(t.t_close), position: t.side > 0 ? "aboveBar" : "belowBar", color: t.pnl >= 0 ? COL.up : COL.down, shape: "circle", text: sm(t.pnl, 0) }); });
  if (d.position) mk.push({ time: at(d.position.t_open), position: d.position.side > 0 ? "belowBar" : "aboveBar", color: d.position.side > 0 ? COL.up : COL.down, shape: d.position.side > 0 ? "arrowUp" : "arrowDown", text: d.position.side > 0 ? "Buy" : "Sell" });
  mk.sort((a, b) => a.time - b.time); TC.candle.setMarkers(mk);
  TC.lines.forEach(l => TC.candle.removePriceLine(l)); TC.lines = [];
  if (d.position) [["Entry", d.position.entry, "#a2a9b6"], ["Stop", d.position.stop, COL.down]].forEach(([t, v, c]) => TC.lines.push(TC.candle.createPriceLine({ price: v, color: c, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: t })));
  TC.last = cs[cs.length - 1].slice(); if (TC.inst !== sel || force) TC.chart.timeScale().fitContent(); TC.chart.timeScale().applyOptions({ rightOffset: 5 });
  if (TC.inst !== sel) { const w = Math.min(cs.length, 110); TC.chart.timeScale().setVisibleLogicalRange({ from: cs.length - w, to: cs.length + 4 }); }
  TC.inst = sel; $("#chartBox").style.opacity = 1;
}
function liveTick() {
  if (!TC.last || !S || page !== "trade") return; const st = S.engine.status[sel]; if (!st || !st.price) return;
  const b = TC.last, c = st.price; if (c === b[4] && b._l) return; b[4] = c; b[2] = Math.max(b[2], c); b[3] = Math.min(b[3], c); b._l = 1;
  TC.candle.update({ time: T(b[0]), open: b[1], high: b[2], low: b[3], close: c });
}

/* ---------- state / header */
let sparks = {};
async function refresh() {
  try { S = await api("/api/state", undefined, true); } catch (e) { setHTML($("#engTxt"), "Offline"); return; }
  const e = S.engine, ins = insts();
  if (!sel || !S.cfg.instruments.find(i => i.id === sel)) sel = (ins[0] || {}).id;
  $("#engLed").className = "led " + (e.halted ? "bad" : e.running ? "on" : "warn"); $("#railLed").className = $("#engLed").className;
  $("#engTxt").textContent = e.halted ? "Halted" : e.running ? "Live · " + ins.length + " markets" : "Paused";
  $("#modeChip").textContent = e.mode === "live" ? "Exchange" : "Paper"; $("#modeChip").style.color = e.mode === "live" ? "var(--down)" : "";
  roll($("#acctTxt"), money(e.equity)); $("#engSeg").set(e.running ? "run" : "pause");
  celebrate(S.events); liveTick();
  if (page === "trade") renderTrade(); else if (page === "portfolio") renderPortfolio();
}
function celebrate(evs) {
  const mx = Math.max(0, ...evs.map(v => v.id)); if (lastEv === null) { lastEv = mx; return; }
  evs.filter(v => v.id > lastEv).sort((a, b) => a.id - b.id).forEach(v => {
    if (v.level === "trade") { toast(v.msg, "info", "Trade opened"); beep([520, 780]); }
    else if (v.level === "close") { const m = /([+-]\d+(?:\.\d+)?)$/.exec(v.msg); if (m && +m[1] >= 0) { toast(v.msg, "win", "Profit locked in"); beep([660, 880, 1170]); } else { toast(v.msg, "loss", "Stop hit - loss contained"); beep([300, 220]); } }
    else if (v.level === "error") { toast(v.msg, "loss", "Alert"); beep([200, 200]); }
  }); lastEv = mx;
}

/* ---------- TRADE page */
function buildTrade() {
  $("#p-trade").innerHTML = `<div class="rise">
   <div class="trade"><div style="--i:0;min-width:0">
     <div class="sym"><span id="tCoin"></span><div><div class="name" id="tName"></div><div class="sub" id="tSub"></div></div><div class="price" id="tPrice"></div><span class="pill" id="tChg"></span><span class="grow"></span><span class="pill acc pro" id="tStrat"></span></div>
     <div class="status" id="tStatus"></div>
     <div class="card" id="chartBox" style="padding:0;opacity:.0;transition:opacity .6s"><div class="legend"><span id="ohlc"></span><span id="legend" style="display:flex;gap:10px"></span></div><div id="chart"></div></div>
   </div><div style="--i:1"><div class="card"><h3>Markets <span class="sub" id="wlN"></span></h3><div class="wl scroll" id="wl"></div></div></div></div>
   <div class="card dock" style="--i:2"><div class="seg tabs" id="dockTabs"><div class="thumb"></div><button data-v="pos">Open trades</button><button data-v="act">Activity</button><button data-v="his">History</button></div><div class="scroll" id="dockBody"></div></div></div>`;
  seg($("#dockTabs"), v => { dock = v; renderDock(true); }).set(dock);
}
let dock = "pos", dockH = 0;
function spark(arr, up) {
  if (!arr || arr.length < 2) return '<svg width="64" height="24"></svg>';
  const lo = Math.min(...arr), hi = Math.max(...arr), w = 64, h = 24, pts = arr.map((v, i) => [i / (arr.length - 1) * w, h - 2 - (v - lo) / (hi - lo || 1) * (h - 4)]);
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" "), c = up ? COL.up : COL.down;
  return `<svg width="64" height="24" viewBox="0 0 64 24"><defs><linearGradient id="g${up ? 1 : 0}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${c}" stop-opacity=".3"/><stop offset="1" stop-color="${c}" stop-opacity="0"/></linearGradient></defs><path d="${d} L${w} ${h} L0 ${h}Z" fill="url(#g${up ? 1 : 0})"/><path d="${d}" fill="none" stroke="${c}" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
}
function renderTrade() {
  if (!$("#tName")) { buildTrade(); ensureChart(); }
  const e = S.engine, ins = insts(), i = S.cfg.instruments.find(x => x.id === sel); if (!i) return;
  const st = e.status[i.id] || {}, pos = e.positions.find(p => p.id === i.id), s = short(i);
  setHTML($("#tCoin"), coinHTML(i)); $("#tName").textContent = NAMES[s] || s; $("#tSub").textContent = (i.market === "futures" ? s + "USDT perpetual" : "Forex") + " · 4h candles";
  roll($("#tPrice"), px(st.price)); const ch = $("#tChg"), c24 = (sparks[i.id] || {}).c || 0; ch.textContent = pct(c24) + " 24h"; ch.className = "pill " + (c24 >= 0 ? "up" : "down");
  $("#tStrat").textContent = { donchian: "Breakout strategy", emacross: "EMA cross strategy", pullback: "Pullback strategy" }[i.strategy] || i.strategy;
  const nb = st.next_bar ? st.next_bar - e.now : null; let msg;
  if (st.error) msg = `<svg class="i" data-i="shield-alert"></svg><span>Data hiccup on this market: <b>${esc(st.error)}</b>. Retrying automatically.</span>`;
  else if (pos) msg = `<svg class="i" data-i="${pos.side > 0 ? "trending-up" : "trending-down"}"></svg><span>Holding a <b>${pos.side > 0 ? "long" : "short"}</b> position, <b class="${cls(pos.upnl)}">${sm(pos.upnl)}</b> so far. Safety stop at <b>${px(pos.stop)}</b>.</span>`;
  else msg = `<svg class="i" data-i="activity"></svg><span>${st.signal ? "A signal just fired - I'm checking risk limits." : "Watching for a breakout."} Next 4-hour candle closes in <b>${nb == null ? "--" : dur(nb)}</b>.</span>`;
  setHTML($("#tStatus"), msg);
  const wl = ins.map(x => { const t = e.status[x.id] || {}, sp = sparks[x.id] || {}, up = (sp.c || 0) >= 0, hold = e.positions.some(p => p.id === x.id);
    return `<div class="wl-item ${x.id === sel ? "on" : ""}" data-id="${esc(x.id)}">${coinHTML(x)}<div><div class="t1">${hold ? '<span class="dot"></span>' : ""}${esc(short(x))}</div><div class="t2">${esc(NAMES[short(x)] || "")}</div></div><div>${spark(sp.s, up)}</div><div class="r"><div class="t1">${px(t.price)}</div><div class="t2 ${up ? "up" : "down"}">${pct(sp.c || 0, 1)}</div></div></div>`; }).join("");
  if (setHTML($("#wl"), wl)) $$(".wl-item", $("#wl")).forEach(el => el.onclick = () => { sel = el.dataset.id; TC.inst !== sel && loadChart(); renderTrade(); }); $("#wlN").textContent = ins.length;
  renderDock();
}
function renderDock(force) {
  const e = S.engine; let h;
  if (dock === "pos") h = e.positions.length ? `<table><tr><th>Market</th><th>Side</th><th>Size</th><th>Entry</th><th>Price</th><th>Stop</th><th>Profit now</th><th></th></tr>${e.positions.map(p => `<tr><td class="t"><b>${esc(p.symbol.replace("=X", ""))}</b></td><td><span class="pill ${p.side > 0 ? "up" : "down"}">${p.side > 0 ? "Long" : "Short"}</span></td><td>${(+p.qty).toPrecision(3)}</td><td>${px(p.entry)}</td><td>${px(p.mark)}</td><td>${px(p.stop)}</td><td class="${cls(p.upnl)}">${sm(p.upnl)}</td><td><button class="btn sm" data-close="${esc(p.id)}">Close</button></td></tr>`).join("")}</table>` : `<div class="empty">No open trades. I only enter on a confirmed breakout, so quiet stretches are normal.</div>`;
  else if (dock === "act") h = S.events.length ? `<table>${S.events.map(v => `<tr><td class="mono" style="color:var(--tx3)">${tm(v.t)}</td><td><span class="pill ${{ trade: "acc", close: "up", error: "down", skip: "", info: "" }[v.level] || ""}">${{ trade: "Opened", close: "Closed", error: "Alert", skip: "Skipped", info: "System" }[v.level] || v.level}</span></td><td class="t">${esc(v.msg)}</td></tr>`).join("")}</table>` : '<div class="empty">Nothing yet.</div>';
  else { if (force || Date.now() - dockH > 15000) { dockH = Date.now(); api("/api/trades?n=60", undefined, true).then(t => { dockData = t; if (dock === "his") renderDock(); }); } h = histTable(dockData); }
  if (setHTML($("#dockBody"), h)) $$("[data-close]", $("#dockBody")).forEach(b => b.onclick = guard(async () => { await api("/api/engine", { action: "close", inst: b.dataset.close }); refresh(); }));
}
let dockData = [];
const REASON = { stop: "Stop hit", reverse: "Trend flipped", target: "Target hit", manual: "Closed by you", "kill switch": "Kill switch", tradingview: "TradingView" };
function histTable(t) { return t.length ? `<table><tr><th>Closed</th><th>Market</th><th>Side</th><th>Entry</th><th>Exit</th><th>Result</th><th>Why</th></tr>${t.map(r => `<tr><td class="mono" style="color:var(--tx3)">${dt(r.t_close)}</td><td class="t"><b>${esc(r.symbol.replace("=X", ""))}</b></td><td><span class="pill ${r.side > 0 ? "up" : "down"}">${r.side > 0 ? "Long" : "Short"}</span></td><td>${px(r.entry)}</td><td>${px(r.exit)}</td><td class="${cls(r.pnl)}">${sm(r.pnl)}</td><td class="t">${esc(REASON[r.reason] || r.reason)}</td></tr>`).join("")}</table>` : '<div class="empty">No closed trades yet. Every trade is logged here.</div>'; }

/* ---------- PORTFOLIO page */
const PF = { chart: null, series: null, range: "1d", eq: [] };
function buildPortfolio() {
  $("#p-portfolio").innerHTML = `<div class="rise">
   <div class="grid" style="grid-template-columns:minmax(0,2fr) minmax(280px,1fr)">
    <div class="card" style="--i:0"><div class="row" style="justify-content:space-between;align-items:flex-start"><div><div class="label">Total value <span class="q" data-tip="Everything the account is worth right now: cash plus open trades, after fees. This number is exact, not an estimate.">?</span></div><div class="big" id="pTotal">--</div><div class="row" style="margin-top:8px"><span class="pill" id="pSince"></span><span class="sub" id="pToday"></span></div></div>
     <div class="seg" id="pRange"><div class="thumb"></div><button data-v="1h">1H</button><button data-v="1d">1D</button><button data-v="1w">1W</button><button data-v="all">All</button></div></div><div id="eqBox" style="height:270px;margin-top:10px"></div></div>
    <div class="card" style="--i:1"><h3>Sentinel insight <svg class="i" data-i="sparkles" style="color:var(--acc2)"></svg></h3><div class="insight" id="insight"></div><div class="row" id="tags" style="margin-top:6px"></div></div>
   </div>
   <div class="grid g5" id="kpis" style="margin-top:14px;--i:2"></div>
   <div class="grid g2" style="margin-top:14px"><div class="card" style="--i:3"><h3>Safety systems <span class="sub">live limits</span></h3><div id="meters"></div></div><div class="card" style="--i:4"><h3>Open trades</h3><div class="scroll" id="pPos"></div></div></div></div>`;
  seg($("#pRange"), v => { PF.range = v; drawEq(); }).set(PF.range);
  PF.chart = mkChart($("#eqBox"), { rightPriceScale: { borderVisible: false, scaleMargins: { top: .12, bottom: .1 } } });
  PF.series = PF.chart.addBaselineSeries({ baseValue: { type: "price", price: 1000 }, topLineColor: COL.up, topFillColor1: "rgba(46,211,160,.28)", topFillColor2: "rgba(46,211,160,0)", bottomLineColor: COL.down, bottomFillColor1: "rgba(255,94,122,0)", bottomFillColor2: "rgba(255,94,122,.28)", lineWidth: 2, priceLineVisible: false });
}
function narrate() {
  const e = S.engine, n = insts().length, ps = e.positions, ai = S.cfg.ai;
  if (e.halted) return "**Trading is halted.** The safety systems stopped everything. Open **Settings** and press **Resume** when you're ready.";
  if (!e.running) return "I'm **paused**. Press **Run** and I'll go back to watching " + n + " markets.";
  let t = "All good. I'm watching **" + n + " markets** around the clock. ";
  if (ps.length) { const sum = ps.reduce((a, p) => a + p.upnl, 0); t += "I'm holding **" + ps.length + " trade" + (ps.length > 1 ? "s" : "") + "**, " + (sum >= 0 ? "up " : "down ") + "**" + money(Math.abs(sum)) + "**. "; }
  else t += "No trades open - I only act on confirmed breakouts, so waiting is normal. ";
  t += "Today: **" + sm(e.day_pnl) + "**. ";
  if (e.daily_block) t += "**Daily loss limit reached**; new trades resume tomorrow. ";
  const bad = Object.values(e.status).filter(s => s.error).length; if (bad) t += bad + " market" + (bad > 1 ? "s have" : " has") + " a data hiccup; retrying. ";
  if (ai.mode !== "off" && !ai.has_api_key && ai.provider !== "claude_cli") t += "AI is on but has no key yet. ";
  return t;
}
function kpi(l, v, s, c, tp) { return `<div class="card kpi"><div class="label">${l}${tp ? ` <span class="q" data-tip="${esc(tp)}">?</span>` : ""}</div><div class="mid ${c || ""}" style="margin-top:6px">${v}</div><div class="sub">${s}</div></div>`; }
function meter(label, f, text, tp) { const c = f < .5 ? COL.up : f < .8 ? COL.warn : COL.down; return `<div class="mrow" data-tip="${esc(tp)}"><div class="top"><span>${label}</span><span class="num" style="color:${c}">${text}</span></div><div class="meter"><i style="width:${Math.min(100, f * 100).toFixed(1)}%;background:${c}"></i></div></div>`; }
function renderPortfolio() {
  if (!$("#pTotal")) { buildPortfolio(); drawEq(); }
  const e = S.engine, r = S.cfg.risk, ps = e.positions, profit = e.equity - e.start_equity;
  roll($("#pTotal"), money(e.equity)); const sn = $("#pSince"); sn.textContent = sm(profit) + " (" + pct(e.total_return_pct) + ") since start"; sn.className = "pill " + (profit >= 0 ? "up" : "down");
  $("#pToday").textContent = "Today " + sm(e.day_pnl) + " (" + pct(e.day_pnl_pct) + ")";
  words($("#insight"), narrate());
  const nbs = Object.values(e.status).map(s => s.next_bar).filter(Boolean), nb = nbs.length ? Math.min(...nbs) - e.now : null;
  setHTML($("#tags"), `<span class="chip" data-tip="The most one losing trade can cost">Risk / trade ${money(e.equity * r.per_trade_pct / 100)}</span><span class="chip" data-tip="Next 4-hour candle close, when I look for new signals">Next candle ${nb == null ? "--" : dur(nb)}</span><span class="chip" data-tip="Off, Advise (rates signals) or Veto (can block)">AI ${S.cfg.ai.mode}</span>`);
  setHTML($("#kpis"), kpi("Win rate", e.trades ? e.win_rate.toFixed(0) + "%" : "--", e.trades + " closed trades", "", "Share of closed trades that made money. Trend systems often win only 35-45% of the time, but winners are larger than losers.") +
    kpi("Profit factor", e.profit_factor ? e.profit_factor.toFixed(2) : "--", e.profit_factor ? (e.profit_factor > 1 ? "winners outweigh losers" : "losers outweigh winners") : "needs closed trades", e.profit_factor ? cls(e.profit_factor - 1) : "", "Money won divided by money lost. Above 1 is profitable; 1.5+ is strong.") +
    kpi("Open risk", money(e.open_risk), ps.length + " / " + r.max_positions + " trades open", "", "The most you'd lose if every open stop-loss hit right now.") +
    kpi("Drawdown", e.drawdown_pct.toFixed(2) + "%", "from the account's peak", e.drawdown_pct > 10 ? "down" : "", "How far below its best value the account is. I halt everything at " + r.max_drawdown_halt_pct + "%.") +
    kpi("Today", sm(e.day_pnl), pct(e.day_pnl_pct) + " since 00:00 UTC", cls(e.day_pnl), "Change since midnight UTC."));
  const gross = ps.reduce((a, p) => a + p.qty * (p.mark || p.entry), 0), ex = gross / (e.equity * r.max_total_leverage), dy = Math.max(0, -e.day_pnl_pct) / r.daily_loss_limit_pct, dd = e.drawdown_pct / r.max_drawdown_halt_pct;
  setHTML($("#meters"), meter("Daily loss limit", dy, (dy * 100).toFixed(0) + "% used", "At 100% I stop opening trades until tomorrow.") + meter("Drawdown halt", dd, (dd * 100).toFixed(0) + "% of limit", "At 100% everything is closed and trading halts.") + meter("Exposure", ex, (ex * 100).toFixed(0) + "% of max", "How much of the allowed position size is in use."));
  if (setHTML($("#pPos"), ps.length ? `<table><tr><th>Market</th><th>Side</th><th>Profit now</th><th>Stop</th></tr>${ps.map(p => `<tr><td class="t"><b>${esc(p.symbol.replace("=X", ""))}</b></td><td><span class="pill ${p.side > 0 ? "up" : "down"}">${p.side > 0 ? "Long" : "Short"}</span></td><td class="${cls(p.upnl)}">${sm(p.upnl)}</td><td>${px(p.stop)}</td></tr>`).join("")}</table>` : '<div class="empty">No open trades right now.</div>')) { }
}
async function drawEq() {
  if (!PF.series) return; PF.eq = await api("/api/equity", undefined, true); const e = S.engine, now = Date.now();
  const span = { "1h": 36e5, "1d": 864e5, "1w": 6048e5, all: 1e15 }[PF.range], pts = PF.eq.filter(p => now - p[0] <= span);
  const seen = new Set(), data = []; (pts.length > 1 ? pts : PF.eq).forEach(p => { const t = T(p[0]); if (!seen.has(t)) { seen.add(t); data.push({ time: t, value: p[1] }); } });
  PF.series.applyOptions({ baseValue: { type: "price", price: e.start_equity } }); PF.series.setData(data); PF.chart.timeScale().fitContent();
}

/* ---------- LAB page */
const LAB = { res: null, chart: null };
function buildLab() {
  $("#p-lab").innerHTML = `<div class="rise"><div class="callout" style="--i:0;margin-bottom:14px"><b>Reality check.</b> Past results are not promises. The verdict is judged on the most recent 40% of history, which the optimizer never saw. If it says "no reliable edge", believe it.</div>
  <div class="card" style="--i:1"><div class="form"><div><label class="f">Market</label><select id="lInst"></select></div><div><label class="f">Strategy</label><select id="lStrat"></select></div><div><label class="f">History (years)</label><input id="lYears" type="number" value="5" min="1" max="7"></div><div><label class="f">Risk per trade %</label><input id="lRisk" type="number" value="0.5" step="0.1"></div></div>
   <div class="row" style="margin-top:14px"><button class="btn pri" id="lRun">Backtest</button><button class="btn" id="lOpt">Optimize</button><button class="btn" id="lPort">Whole portfolio</button><button class="btn go" id="lApply" style="display:none">Use these settings live</button><span class="sub" id="lStatus"></span><span class="grow"></span><button class="btn sm pro" id="lAdv">Parameters</button></div>
   <div class="acc-body" id="lAccBody"><div><textarea id="lParams" style="margin-top:12px"></textarea></div></div></div><div id="lOut" style="margin-top:14px"></div></div>`;
  $("#lInst").innerHTML = insts().map(i => `<option value="${esc(i.id)}">${esc(short(i))} (${i.market === "futures" ? "crypto" : "forex"})</option>`).join("");
  $("#lStrat").innerHTML = Object.entries(S.strategies).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  const fill = () => { const i = S.cfg.instruments.find(x => x.id === $("#lInst").value); $("#lStrat").value = i.strategy; $("#lParams").value = JSON.stringify(i.params); };
  $("#lInst").onchange = fill; fill();
  $("#lStrat").onchange = () => { const d = S.defaults[$("#lStrat").value], p = {}; Object.entries(d).forEach(([k, v]) => p[k] = v[Math.floor(v.length / 2)]); $("#lParams").value = JSON.stringify(p); };
  $("#lAdv").onclick = () => $("#lAccBody").classList.toggle("open");
  $("#lRun").onclick = () => runLab("single"); $("#lOpt").onclick = () => runLab("optimize"); $("#lPort").onclick = () => runLab("portfolio");
  $("#lApply").onclick = guard(async () => { const id = $("#lInst").value; await api("/api/config", { instruments: S.cfg.instruments.map(i => i.id === id ? { ...i, strategy: $("#lStrat").value, params: LAB.res.params } : i) }); toast("Applied. Used from the next candle.", "info", "Updated"); refresh(); });
}
const runLab = guard(async kind => {
  const i = S.cfg.instruments.find(x => x.id === $("#lInst").value);
  const body = { kind, market: i.market, symbol: i.symbol, tf: i.tf, strategy: $("#lStrat").value, params: JSON.parse($("#lParams").value), years: +$("#lYears").value, risk: +$("#lRisk").value };
  $$("#p-lab .btn").forEach(b => b.disabled = true); $("#lApply").style.display = "none"; bar.start();
  try {
    const { job } = await api("/api/backtest", body, true);
    for (; ;) {
      const j = await api("/api/job?id=" + job, undefined, true);
      if (j.state === "running") { $("#lStatus").innerHTML = '<span class="spin"></span>' + esc(j.progress || "working"); await new Promise(r => setTimeout(r, 700)); continue; }
      if (j.state === "error") throw new Error(j.error);
      LAB.res = j.result; $("#lStatus").textContent = ""; renderLab(j.result); if (j.result.params && kind !== "portfolio") $("#lApply").style.display = ""; break;
    }
  } finally { $$("#p-lab .btn").forEach(b => b.disabled = false); bar.done(); }
});
function tiles(m, label) { const t = (l, v, c, tp) => `<div class="card kpi"><div class="label">${l}${tp ? ` <span class="q" data-tip="${esc(tp)}">?</span>` : ""}</div><div class="mid ${c || ""}" style="margin-top:6px">${v}</div></div>`;
  return `<div class="label" style="margin:18px 2px 8px">${label}</div><div class="grid g5">${t("Return", pct(m.ret * 100, 1), cls(m.ret))}${t("Per year", pct(m.cagr * 100, 1), cls(m.cagr))}${t("Worst dip", (m.mdd * 100).toFixed(1) + "%", m.mdd > .25 ? "down" : "", "Biggest fall from a peak")}${t("Sharpe", m.sharpe.toFixed(2), cls(m.sharpe - .3), "Return per unit of risk. Under 0.3 is weak, over 1 is strong.")}${t("Trades", m.trades)}</div>`; }
function renderLab(r) {
  let h = `<div class="verdict ${r.verdict[0]}">${esc(r.verdict[1])}</div><div class="card"><h3>${r.kind === "portfolio" ? "All enabled markets together" : "Strategy vs. just holding"} <span class="sub pro">${esc(JSON.stringify(r.params || ""))}</span></h3><div id="lChart" style="height:300px"></div></div>`;
  if (r.kind === "portfolio") h += tiles(r.metrics, "Full period") + tiles(r.first_half, "First half") + tiles(r.second_half, "Second half (unseen)") + `<div class="card" style="margin-top:14px"><h3>Each market on its own</h3><table><tr><th>Market</th><th>Return</th><th>Worst dip</th><th>Sharpe</th><th>Trades</th></tr>${r.per_symbol.map(s => `<tr><td class="t"><b>${esc(s.symbol.replace("=X", ""))}</b></td><td class="${cls(s.ret)}">${pct(s.ret, 1)}</td><td>${s.mdd}%</td><td>${s.sharpe}</td><td>${s.trades}</td></tr>`).join("")}</table></div>`;
  else { h += tiles(r.metrics, "Full period"); if (r.insample) h += tiles(r.insample, "Training data (first 60%)"); h += tiles(r.oos, "Unseen data (the one that matters)"); }
  $("#lOut").innerHTML = h; if (LAB.chart) { LAB.chart.remove(); LAB.chart = null; }
  LAB.chart = mkChart($("#lChart")); const a = LAB.chart.addAreaSeries({ lineColor: COL.acc, topColor: "rgba(124,156,255,.3)", bottomColor: "rgba(124,156,255,0)", lineWidth: 2, priceLineVisible: false });
  const f = pts => { const seen = new Set(); return pts.filter(p => { const t = T(p[0]); if (seen.has(t)) return false; seen.add(t); return true; }).map(p => ({ time: T(p[0]), value: p[1] })); };
  a.setData(f(r.curve)); if (r.buyhold) LAB.chart.addLineSeries({ color: "#6a7280", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }).setData(f(r.buyhold)); LAB.chart.timeScale().fitContent();
}

/* ---------- AI page */
function buildAI() {
  $("#p-ai").innerHTML = `<div class="rise"><div class="grid g2"><div class="card" style="--i:0"><h3>AI analyst <span class="pill acc" id="aiState"></span></h3>
   <p class="note" style="margin-top:0">For each trade signal that passes every safety check, the analyst reads one compact briefing (trend, volatility, funding rate, open interest, long/short crowding, your open book) and rates the odds of follow-through. It can call three read-only tools to fetch more facts. Typical cost: <b>about 0.05 cents</b> per signal, because the fixed part of the prompt is cached.</p>
   <div class="seg" id="aiMode" style="margin-bottom:14px"><div class="thumb"></div><button data-v="off">Off</button><button data-v="advise">Advise</button><button data-v="veto">Veto</button></div>
   <div class="form"><div><label class="f">Provider</label><select id="aiProv"></select></div><div><label class="f">Model</label><input id="aiModel"></div><div><label class="f">API key <span id="aiHas" class="up"></span></label><input id="aiKey" type="password" placeholder="stored only on this PC" autocomplete="off"></div><div class="pro"><label class="f">Veto cut-off %</label><input id="aiMinP" type="number" min="0" max="100"></div></div>
   <div class="row" style="margin-top:14px"><button class="btn pri" id="aiSave">Save</button><button class="btn" id="aiTest" data-tip="Sends one tiny message (~25 tokens, a fraction of a cent)">Test connection</button><span class="sub" id="aiTestOut"></span></div><p class="sub" id="aiHelp" style="margin-top:12px"></p></div>
   <div class="card" style="--i:1"><h3>Spending guardrails</h3><div id="aiBudget"></div><div class="sub" id="aiBudgetNote" style="margin-top:12px"></div></div></div>
   <div class="grid g2" style="margin-top:14px"><div class="card" style="--i:2"><h3>Does the AI actually help? <span class="sub">live scorecard</span></h3><div id="aiScore"></div></div><div class="card" style="--i:3"><h3>Historical test <span class="sub">6,800 signals</span></h3><div id="aiVal"></div></div></div>
   <div class="card" style="margin-top:14px;--i:4"><h3>Recent decisions <button class="btn sm" id="aiBrief" data-tip="One call, about 300 tokens">Write a brief</button></h3><div class="note" id="aiBriefOut" style="white-space:pre-wrap;margin-bottom:8px"></div><div class="scroll" id="aiLog"></div></div></div>`;
  seg($("#aiMode")); $("#aiProv").innerHTML = Object.keys(S.presets).map(k => `<option>${k}</option>`).join("");
  $("#aiProv").onchange = () => { const p = S.presets[$("#aiProv").value]; $("#aiModel").value = p[1]; aiHelp(); };
  $("#aiSave").onclick = guard(async () => { await api("/api/config", { ai: { mode: $("#aiMode").querySelector(".on").dataset.v, provider: $("#aiProv").value, base_url: S.presets[$("#aiProv").value][0] || S.cfg.ai.base_url, model: $("#aiModel").value, min_p: +$("#aiMinP").value, api_key: $("#aiKey").value } }); $("#aiKey").value = ""; toast("AI settings saved", "info", "Saved"); await refresh(); renderAI(true); });
  $("#aiTest").onclick = guard(async () => { $("#aiTestOut").innerHTML = '<span class="spin"></span>testing'; const r = await api("/api/ai/test", { provider: $("#aiProv").value, model: $("#aiModel").value, api_key: $("#aiKey").value }, true); $("#aiTestOut").innerHTML = r.ok ? `<span class="up">Connected</span> · ${r.ms} ms · ${r.tokens} tokens` : `<span class="down">${esc(r.error)}</span>`; });
  $("#aiBrief").onclick = guard(async () => { $("#aiBriefOut").innerHTML = '<span class="spin"></span>writing'; const r = await api("/api/ai/brief", {}, true); $("#aiBriefOut").textContent = r.ok ? r.text : "Failed: " + r.error; });
}
function aiHelp() { const t = { deepseek: "DeepSeek: platform.deepseek.com. A few dollars lasts months at this usage.", gemini: "Google Gemini: aistudio.google.com. Has a free tier (check current limits).", groq: "Groq: console.groq.com. Has a free tier (check current limits).", openrouter: "OpenRouter: some models are free (names ending :free).", openai: "OpenAI API (a ChatGPT subscription does not include API access).", custom: "Any OpenAI-compatible endpoint, even local Ollama.", claude_cli: "Uses the Claude CLI on this PC, logged into your Claude account. No key needed." }; $("#aiHelp").textContent = t[$("#aiProv").value] || ""; }
let aiT = 0;
async function renderAI(force) {
  if (!$("#aiMode")) buildAI(); const a = S.cfg.ai;
  if (!$("#aiMode")._init) { $("#aiMode")._init = 1; $("#aiMode").set(a.mode); $("#aiProv").value = a.provider; $("#aiModel").value = a.model; $("#aiMinP").value = a.min_p; aiHelp(); }
  $("#aiHas").textContent = a.has_api_key ? "saved" : ""; setHTML($("#aiState"), { off: "Off", advise: "Advising", veto: "Veto on" }[a.mode]);
  if (!force && Date.now() - aiT < 8000) return; aiT = Date.now();
  const l = await api("/api/ai/log", undefined, true), sp = l.spend, b = l.budget, hitr = sp.hit + sp.miss ? sp.hit / (sp.hit + sp.miss) * 100 : 0;
  setHTML($("#aiBudget"), meter("Today (money)", sp.usd_day / b.max_usd_day, "$" + sp.usd_day.toFixed(4) + " / $" + b.max_usd_day, "Hard daily dollar cap. When it's reached the AI simply stops; trading continues.") + meter("Today (calls)", sp.calls_day / b.max_calls_day, sp.calls_day + " / " + b.max_calls_day, "Hard daily call cap.") + meter("Lifetime cap", sp.usd / b.max_usd_total, "$" + sp.usd.toFixed(3) + " / $" + b.max_usd_total, "Total the app will ever spend on AI until you raise this."));
  $("#aiBudgetNote").innerHTML = `${sp.calls} calls · ${sp.tokens.toLocaleString()} tokens · cache hit rate <b>${hitr.toFixed(0)}%</b> (cached tokens cost ~100x less) · ${l.peak ? "peak" : "off-peak"} pricing now. The analyst never runs when a trade is already blocked, and never twice for the same candle.`;
  const sc = l.score; setHTML($("#aiScore"), `<p class="note" style="margin-top:0">Every trade is tagged with the AI's rating. This table shows what actually happened to trades it rated high vs. low. ${sc.n < 30 ? `<b>Only ${sc.n} rated trades so far, so it's too early to conclude anything.</b>` : ""}</p><table><tr><th>AI rating</th><th>Trades</th><th>Win rate</th><th>Avg result</th></tr>${sc.rows.map(r => `<tr><td class="t">${r.label}</td><td>${r.n}</td><td>${r.win == null ? "--" : r.win.toFixed(0) + "%"}</td><td class="${cls(r.avg || 0)}">${r.avg == null ? "--" : sm(r.avg)}</td></tr>`).join("")}</table>`);
  const v = l.validation; setHTML($("#aiVal"), v ? `<table><tr><th>Setup</th><th>Trades</th><th>Sharpe*</th><th>Return*</th></tr>${v.rows.map((r, i) => `<tr><td class="t">${esc(r.label)}</td><td>${r.trades}</td><td class="${i ? cls(r.oos - v.rows[0].oos) : ""}">${r.oos.toFixed(2)}</td><td class="${i ? cls(r.oos_ret - v.rows[0].oos_ret) : ""}">${pct(r.oos_ret, 1)}</td></tr>`).join("")}</table><p class="sub">*on data the tuning never saw. ${esc(v.verdict)}</p>` : "");
  setHTML($("#aiLog"), l.rows.length ? `<table><tr><th>Time</th><th>Market</th><th>Decision</th><th>Analyst said</th></tr>${l.rows.map(r => `<tr><td class="mono" style="color:var(--tx3)">${dt(r.t)}</td><td class="t">${esc(r.inst)}</td><td><span class="pill ${r.take ? "up" : "down"}">${r.take ? "Go" : "Blocked"}</span></td><td class="t">${esc(r.reply || "")}</td></tr>`).join("")}</table>` : '<div class="empty">No ratings yet. They appear when a trade signal fires.</div>');
}

/* ---------- SETTINGS page */
function buildSettings() {
  $("#p-settings").innerHTML = `<div class="rise"><div class="grid g2">
   <div class="card" style="--i:0"><h3>Account</h3><div class="form"><div><label class="f">Mode</label><select id="sMode"><option value="paper">Paper (built-in simulator)</option><option value="live">Exchange (demo / testnet / real)</option></select></div><div><label class="f">Account size</label><button class="btn" id="sAcct" style="width:100%">Change size and risk</button></div></div>
    <div class="callout" id="liveBox" style="display:none;margin-top:12px">Exchange mode sends API orders. <b>Demo/Testnet</b> is play money; <b>Real</b> is real money and these adapters have never run on a real account. Type <b>I UNDERSTAND</b>: <input id="sConfirm" style="margin-top:8px"></div>
    <div class="row" style="margin-top:16px;justify-content:space-between"><div><div style="font-weight:550">Show technical details</div><div class="sub">Strategy names, parameters, extra columns</div></div><label class="sw"><input type="checkbox" id="sPro"><span></span></label></div>
    <div class="row" style="margin-top:12px;justify-content:space-between"><div><div style="font-weight:550">Sound effects</div><div class="sub">Short tones when trades open or close</div></div><label class="sw"><input type="checkbox" id="sSound"><span></span></label></div></div>
   <div class="card" style="--i:1"><h3>Safety limits</h3><div class="form"><div><label class="f">Risk per trade %</label><input id="rPer" type="number" step="0.1"></div><div><label class="f">Max open trades</label><input id="rMax" type="number"></div><div><label class="f">Max leverage / trade</label><input id="rLev" type="number" step="0.5"></div><div><label class="f">Max total leverage</label><input id="rTot" type="number" step="0.5"></div><div><label class="f">Daily loss stop %</label><input id="rDay" type="number" step="0.5"></div><div><label class="f">Drawdown halt %</label><input id="rDd" type="number"></div></div></div>
   <div class="card" style="--i:2"><h3>Exchange link</h3><div class="form"><div><label class="f">Exchange</label><select id="bEx"><option value="bybit">Bybit</option><option value="binance">Binance Futures</option></select></div><div><label class="f">Network</label><select id="bNet"></select></div><div><label class="f">API key <span id="bkHas" class="up"></span></label><input id="bKey" type="password" autocomplete="off"></div><div><label class="f">API secret</label><input id="bSec" type="password" autocomplete="off"></div></div>
    <div class="row" style="margin-top:14px"><button class="btn" id="bTest">Save and test connection</button><span class="sub" id="bTestOut"></span></div><p class="sub">Bybit Demo Trading = real prices, play money, email sign-up, no ID. Create keys with trade permission and no withdrawal permission.</p></div>
   <div class="card pro" style="--i:3"><h3>TradingView webhook</h3><p class="note" style="margin-top:0">Optional. Expose port 8765 with a tunnel (<code>ngrok http 8765</code>) and use <code>https://&lt;tunnel&gt;/hook</code> as an alert's webhook URL. Only <code>/hook</code> is reachable through a tunnel. Needs a paid TradingView plan.</p><label class="f">Secret</label><input id="whSecret" readonly onclick="this.select()"></div></div>
   <div class="card pro" style="margin-top:14px;--i:4"><h3>Markets <button class="btn sm" id="addInst">Add market</button></h3><div class="scroll" style="max-height:none" id="instTable"></div></div>
   <div class="row" style="margin-top:16px"><button class="btn pri" id="sSave">Save settings</button><button class="btn" id="sResume">Resume after halt</button><span class="grow"></span><button class="btn danger" id="sReset">Reset paper account</button></div></div>`;
  $("#sMode").onchange = () => { $("#liveBox").style.display = $("#sMode").value === "live" && S.cfg.mode !== "live" ? "block" : "none"; };
  $("#sAcct").onclick = e => openAcct(e.currentTarget); $("#bEx").onchange = () => fillNets($("#bEx").value);
  $("#sPro").onchange = e => { simple = !e.target.checked; LS.set("simple", simple ? "1" : "0"); applyView(); }; $("#sSound").onchange = e => { soundOn = e.target.checked; LS.set("sound", soundOn ? "1" : "0"); beep([600, 900]); };
  $("#bTest").onclick = guard(async () => { $("#bTestOut").innerHTML = '<span class="spin"></span>testing'; await api("/api/config", { broker: brokerPatch() }, true); $("#bKey").value = $("#bSec").value = ""; const r = await api("/api/broker/test", {}, true); $("#bTestOut").innerHTML = r.ok ? `<span class="up">Connected</span> · balance ${money(r.equity)}` : `<span class="down">${esc(r.error)}</span>`; refresh(); });
  $("#addInst").onclick = e => { const sh = openSheet(`<h2 style="margin:0 0 8px;font-size:18px">Add a market</h2><p class="note">Crypto perp on Binance/Bybit like <b>SOLUSDT</b>, or Yahoo forex like <b>AUDUSD=X</b>.</p><input id="newSym" placeholder="Symbol" autofocus><div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn" id="cn">Cancel</button><button class="btn pri" id="ok">Add</button></div>`, e.currentTarget); $("#cn", sh).onclick = closeSheet; $("#ok", sh).onclick = guard(async () => { const sym = $("#newSym").value.trim().toUpperCase().replace("=x", "=X"); if (!sym) return; const fx = sym.includes("=X"); await api("/api/config", { instruments: [...S.cfg.instruments, { market: fx ? "forex" : "futures", symbol: sym, tf: "4h", strategy: fx ? "emacross" : "donchian", enabled: true, params: fx ? { fast: 10, slow: 100, adx: 15, stop: 3, trail: 1, tp: 0, reverse: 1 } : { n: 40, trend: 0, stop: 4, trail: 1, tp: 0, reverse: 1 } }] }); closeSheet(); await refresh(); fillSettings(true); }); };
  $("#sSave").onclick = guard(async () => {
    const ins = S.cfg.instruments.map((i, n) => ({ ...i, enabled: $$(".ienable")[n].checked, strategy: $$(".istrat")[n].value, params: JSON.parse($$(".iparams")[n].value) })), num = id => +$("#" + id).value;
    await api("/api/config", { mode: $("#sMode").value, confirm_live: $("#sConfirm").value, instruments: ins, risk: { per_trade_pct: num("rPer"), max_positions: num("rMax"), max_leverage_per_pos: num("rLev"), max_total_leverage: num("rTot"), daily_loss_limit_pct: num("rDay"), max_drawdown_halt_pct: num("rDd") }, broker: brokerPatch() });
    $("#bKey").value = $("#bSec").value = $("#sConfirm").value = ""; toast("Settings saved", "info", "Saved"); await refresh(); fillSettings(true);
  });
  $("#sResume").onclick = guard(async () => { await api("/api/engine", { action: "resume" }); toast("Trading resumed", "info", "Online"); refresh(); });
  $("#sReset").onclick = async e => { if (!await confirmSheet("Reset paper account?", "This erases all paper trades, positions and history, and restarts from your starting balance.", "Reset", true, e.currentTarget)) return; await guard(async () => { await api("/api/reset", { confirm: "RESET" }); toast("Paper account reset", "info", "Done"); refresh(); })(); };
}
function fillNets(ex) { const sel = $("#bNet"), want = ex === "bybit" ? [["demo", "Demo Trading (play money)"], ["testnet", "Testnet"], ["real", "REAL MONEY"]] : [["true", "Testnet"], ["false", "REAL MONEY"]]; if (sel.dataset.ex !== ex) { sel.innerHTML = want.map(([v, l]) => `<option value="${v}">${l}</option>`).join(""); sel.dataset.ex = ex; } }
function brokerPatch() { const ex = $("#bEx").value, k = $("#bKey").value, sc = $("#bSec").value, net = $("#bNet").value; return ex === "bybit" ? { exchange: ex, bybit_env: net, bybit_key: k, bybit_secret: sc } : { exchange: ex, binance_testnet: net === "true", binance_key: k, binance_secret: sc }; }
function fillSettings(force) {
  if (!$("#sMode")) buildSettings(); if (!force && document.activeElement && document.activeElement.closest("#p-settings")) return;
  const c = S.cfg, r = c.risk; $("#sMode").value = c.mode; $("#sPro").checked = !simple; $("#sSound").checked = soundOn;
  [["rPer", "per_trade_pct"], ["rMax", "max_positions"], ["rLev", "max_leverage_per_pos"], ["rTot", "max_total_leverage"], ["rDay", "daily_loss_limit_pct"], ["rDd", "max_drawdown_halt_pct"]].forEach(([id, k]) => $("#" + id).value = r[k]);
  fillNets(c.broker.exchange); $("#bEx").value = c.broker.exchange; $("#bNet").value = c.broker.exchange === "bybit" ? c.broker.bybit_env : String(c.broker.binance_testnet);
  $("#bkHas").textContent = (c.broker.exchange === "bybit" ? c.broker.has_bybit_key : c.broker.has_binance_key) ? "saved" : ""; $("#whSecret").value = c.webhook_secret; $("#sMode").onchange();
  $("#instTable").innerHTML = `<table><tr><th>On</th><th>Market</th><th>Type</th><th>Strategy</th><th>Parameters</th><th></th></tr>${c.instruments.map((i, n) => `<tr><td><label class="sw"><input type="checkbox" class="ienable" ${i.enabled ? "checked" : ""}><span></span></label></td><td class="t"><b>${esc(i.symbol)}</b></td><td class="t">${i.market}</td><td class="t"><select class="istrat" style="width:auto">${Object.keys(S.strategies).map(k => `<option ${k === i.strategy ? "selected" : ""}>${k}</option>`).join("")}</select></td><td><input class="iparams" value='${esc(JSON.stringify(i.params))}' style="min-width:340px"></td><td><button class="btn sm" data-rm="${n}">Remove</button></td></tr>`).join("")}</table>`;
  $$("[data-rm]").forEach(b => b.onclick = guard(async () => { await api("/api/config", { instruments: S.cfg.instruments.filter((_, i) => i !== +b.dataset.rm) }); await refresh(); fillSettings(true); }));
}

/* ---------- account sheet */
const PRESETS = [100, 250, 500, 1000, 2500, 5000, 10000, 25000], RISKS = [[0.25, "Careful"], [0.5, "Balanced"], [1, "Bold"], [2, "Max"]]; let acctRisk = 0.5;
function openAcct(trigger) {
  const e = S.engine, live = e.mode === "live"; acctRisk = S.cfg.risk.per_trade_pct;
  const sh = openSheet(`<h2 style="margin:0 0 4px;font-size:18px">Account size and risk</h2><p class="note" style="margin-top:0">${live ? "In Exchange mode the balance is whatever is in your exchange account." : "Choose how much paper money to start with."}</p>
   <label class="f">Starting balance</label><div class="chips" id="aChips">${PRESETS.map(v => `<button data-v="${v}" ${live ? "disabled" : ""}>${money(v, 0)}</button>`).join("")}</div><input id="aAmt" type="number" min="10" step="10" value="${Math.round(e.start_equity)}" ${live ? "disabled" : ""} style="margin-top:10px">
   <label class="f" style="margin-top:16px">How bold should each trade be?</label><div class="chips" id="aRisk">${RISKS.map(([v, n]) => `<button data-r="${v}">${n} · ${v}%</button>`).join("")}</div>
   <div class="note" id="aPrev" style="margin-top:14px"></div>${live ? "" : '<div class="callout" style="margin-top:12px">A new balance starts a <b>fresh paper account</b>; old paper trades are cleared.</div>'}
   <div class="row" style="margin-top:18px;justify-content:flex-end"><button class="btn" id="aCn">Cancel</button><button class="btn pri" id="aOk">Apply</button></div>`, trigger);
  const prev = () => { const a = +$("#aAmt").value || 0, r = S.cfg.risk; $$("#aChips button").forEach(b => b.classList.toggle("on", +b.dataset.v === a)); $$("#aRisk button").forEach(b => b.classList.toggle("on", +b.dataset.r === acctRisk));
    $("#aPrev").innerHTML = `With <b>${money(a, 0)}</b>, one losing trade costs at most about <b>${money(a * acctRisk / 100)}</b>. Trading pauses for the day after a <b>${money(a * r.daily_loss_limit_pct / 100)}</b> loss and halts after a <b>${money(a * r.max_drawdown_halt_pct / 100)}</b> drawdown.` + (a < 500 && !live ? `<br><span class="warn">Small accounts can't trade every market: exchanges have minimum order sizes. The activity log says when a trade is too small.</span>` : ""); };
  $("#aChips", sh).onclick = e => { const b = e.target.closest("button"); if (b && !b.disabled) { $("#aAmt").value = b.dataset.v; prev(); } }; $("#aRisk", sh).onclick = e => { const b = e.target.closest("button"); if (b) { acctRisk = +b.dataset.r; prev(); } }; $("#aAmt", sh).oninput = prev; prev();
  $("#aCn", sh).onclick = closeSheet;
  $("#aOk", sh).onclick = guard(async () => { if (acctRisk !== S.cfg.risk.per_trade_pct) await api("/api/config", { risk: { per_trade_pct: acctRisk } }); if (!live && +$("#aAmt").value !== S.engine.start_equity) await api("/api/account", { amount: +$("#aAmt").value }); closeSheet(); toast("Account updated", "info", "Ready"); await refresh(); });
}
$("#acctChip").onclick = e => S && openAcct(e.currentTarget);

/* ---------- router, nav, command palette */
const PAGES = [["trade", "chart-candlestick", "Trade"], ["portfolio", "wallet", "Portfolio"], ["lab", "flask-conical", "Lab"], ["ai", "sparkles", "AI"], ["settings", "settings", "Settings"]];
$("#nav").insertAdjacentHTML("beforeend", PAGES.map(p => `<button data-p="${p[0]}" data-label="${p[2]}" aria-label="${p[2]}"><svg class="i" data-i="${p[1]}"></svg></button>`).join(""));
function moveNav() { const i = PAGES.findIndex(p => p[0] === page), th = $(".nav .thumb"); th.style.transform = `translateY(${i * 48}px)`; }
function go(p) {
  if (p === page && S) return; const run = () => {
    page = p; $$(".page").forEach(e => e.classList.toggle("on", e.id === "p-" + p)); $$(".nav button").forEach(b => b.classList.toggle("on", b.dataset.p === p)); $("#title").textContent = PAGES.find(x => x[0] === p)[2]; moveNav(); $("#views").scrollTop = 0;
    if (!S) return; ({ trade: () => { renderTrade(); loadChart(); }, portfolio: () => { renderPortfolio(); drawEq(); }, lab: () => { if (!$("#lInst")) buildLab(); }, ai: () => renderAI(true), settings: () => fillSettings(true) })[p]();
  }; swap(run);
}
$$(".nav button").forEach(b => b.onclick = () => go(b.dataset.p));
function openCmd() {
  const acts = [["Go to Trade", "chart-candlestick", () => go("trade")], ["Go to Portfolio", "wallet", () => go("portfolio")], ["Go to Strategy Lab", "flask-conical", () => go("lab")], ["Go to AI analyst", "sparkles", () => go("ai")], ["Go to Settings", "settings", () => go("settings")],
    [S.engine.running ? "Pause trading" : "Run trading", S.engine.running ? "pause" : "play", () => engineAct(S.engine.running ? "stop" : "start")], ["Change account size and risk", "wallet", () => openAcct()], [simple ? "Show technical details" : "Hide technical details", "sliders-horizontal", () => { simple = !simple; LS.set("simple", simple ? "1" : "0"); applyView(); }],
    ...insts().map(i => ["Open " + short(i) + " chart", "trending-up", () => { sel = i.id; go("trade"); if (page === "trade") { renderTrade(); loadChart(); } }])];
  const sh = openSheet(`<div class="cmd" style="margin:0"><input id="cq" placeholder="Search markets or actions..." autocomplete="off"><div id="cl" style="margin-top:8px;max-height:46vh;overflow:auto"></div></div>`); sh.style.padding = "10px"; let idx = 0, list = acts;
  const draw = () => { $("#cl").innerHTML = list.map((a, i) => `<div class="it ${i === idx ? "on" : ""}" data-i="${i}"><svg class="i" data-i="${a[1]}"></svg>${esc(a[0])}</div>`).join("") || '<div class="empty">No matches</div>'; icons($("#cl")); $$(".it", $("#cl")).forEach(el => { el.onmouseenter = () => { idx = +el.dataset.i; $$(".it", $("#cl")).forEach(x => x.classList.toggle("on", x === el)); }; el.onclick = () => run(); }); };
  const run = () => { const a = list[idx]; if (a) { closeSheet(); setTimeout(a[2], 60); } };
  $("#cq").oninput = () => { const q = $("#cq").value.toLowerCase(); list = acts.filter(a => a[0].toLowerCase().includes(q)); idx = 0; draw(); };
  $("#cq").onkeydown = e => { if (e.key === "ArrowDown") { idx = Math.min(list.length - 1, idx + 1); draw(); e.preventDefault(); } else if (e.key === "ArrowUp") { idx = Math.max(0, idx - 1); draw(); e.preventDefault(); } else if (e.key === "Enter") run(); };
  draw(); $("#cq").focus();
}
$("#searchBtn").onclick = openCmd;
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); sheetOpen ? closeSheet() : S && openCmd(); return; }
  if (e.key === "Escape") closeSheet(); if (/INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName) || e.ctrlKey || e.metaKey || sheetOpen) return;
  const k = "12345".indexOf(e.key); if (k >= 0) go(PAGES[k][0]);
});
const engineAct = guard(async a => { await api("/api/engine", { action: a }); await refresh(); });
seg($("#engSeg"), v => engineAct(v === "run" ? "start" : "stop"));
$("#killBtn").onclick = async e => { if (!await confirmSheet("Emergency stop?", "This closes <b>every</b> open position at market and halts all trading until you resume it in Settings.", "Close everything", true, e.currentTarget)) return; guard(async () => { await api("/api/engine", { action: "kill" }); toast("Everything closed and trading halted.", "loss", "Kill switch"); refresh(); })(); };
function applyView() { document.body.classList.toggle("simple", simple); const s = $("#sPro"); if (s) s.checked = !simple; }

/* ---------- boot + polling (visible tab only; local server only) */
icons(); applyView();
let tk = { st: 0, ch: 0, sp: 0, eq: 0 };
async function pollSparks() { try { sparks = await api("/api/spark", undefined, true); } catch (e) { } }
function tick() {
  if (document.visibilityState !== "visible") return; const n = Date.now();
  if (n - tk.st > 4000) { tk.st = n; refresh(); }
  if (n - tk.sp > 60000) { tk.sp = n; pollSparks().then(() => page === "trade" && S && renderTrade()); }
  if (n - tk.ch > 30000) { tk.ch = n; if (page === "trade" && S) loadChart(); }
  if (n - tk.eq > 30000) { tk.eq = n; if (page === "portfolio" && S) drawEq(); }
  if (page === "ai" && S) renderAI();
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") { tk = { st: 0, ch: 0, sp: 0, eq: 0 }; tick(); } });
window.addEventListener("resize", () => { $("#engSeg").move && $("#engSeg").move(); });
(async function boot() {
  await Promise.all([refresh(), pollSparks()]); tk.st = tk.sp = Date.now();
  if (S) { renderTrade(); await loadChart(true); }
  moveNav(); $$(".nav button").forEach(b => b.classList.toggle("on", b.dataset.p === page)); $("#engSeg").move();
  setTimeout(() => $("#boot").classList.add("out"), 250); setTimeout(() => $("#boot").remove(), 900);
  setInterval(tick, 1000);
})();
