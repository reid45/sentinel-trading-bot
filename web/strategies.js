/* Strategies page: choose how the bot trades (Trend following, Smart-money day trader, Scalper), tune each, and see an honest live scorecard per style.
   Loaded before app.js; the page is registered in app.js (PAGES + router) and polled while visible. */
if (window.ICONS) window.ICONS.layers = '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>';

const STRAT = (() => {
  const FEES = { std: ["Standard (maker 2.0, taker 5.0 bps)", 2.0, 5.0, 1.0], bnb: ["With BNB fee discount (1.8 / 4.5)", 1.8, 4.5, 1.0], vip: ["VIP tier (1.0 / 3.0)", 1.0, 3.0, 0.5], mm: ["Market-maker tier (0 / 2.5)", 0.0, 2.5, 0.5] };
  const COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT"];
  let D = null, built = false, lastT = 0, busy = false;
  const owner = () => !S || !S.me || S.me.role === "owner";
  const sw = (id, on) => `<label class="sw"><input type="checkbox" id="${id}"${on ? " checked" : ""}><span></span></label>`;
  const opt = (list, cur) => list.map(([v, t]) => `<option value="${v}"${String(v) === String(cur) ? " selected" : ""}>${t}</option>`).join("");
  const num = (id, v, step, w) => `<input id="${id}" type="number" step="${step || 1}" value="${v}" style="max-width:${w || 100}px">`;
  const fld = (label, ctl, tip) => `<div><label class="f">${label}${tip ? ` <span class="q" data-tip="${esc(tip)}">?</span>` : ""}</label>${ctl}</div>`;
  const perfKpis = d => `<div class="grid g5" style="grid-template-columns:repeat(auto-fit,minmax(90px,1fr));margin:12px 0">${kpi("Trades", d.perf.n, "closed", "")}${kpi("Win rate", d.perf.n ? d.perf.win.toFixed(0) + "%" : "--", "of trades", "")}${kpi("Profit", d.perf.n ? sm(d.perf.pnl) : "--", d.perf.n ? "last 7 days " + sm(d.perf.week) : "none yet", d.perf.n ? cls(d.perf.pnl) : "")}${kpi("Open now", d.open, "positions", "")}</div>`;

  function head(d, extra) {
    return `<div class="row" style="justify-content:space-between;align-items:flex-start;gap:10px"><div><h3 style="margin:0 0 4px">${esc(d.name)} <span class="pill ${d.tone === "good" ? "up" : ""}" style="${d.tone === "warn" ? "color:var(--warn)" : ""}">${esc(d.badge)}</span></h3><div class="sub">${esc(d.tagline)}</div></div>
      <div class="row" style="gap:8px;flex:none"><span class="sub" id="stOn_${d.id}"></span>${sw("sw_" + d.id, d.enabled)}</div></div>${extra || ""}`;
  }
  const how = d => `<div style="margin:10px 0 4px;font-weight:550;font-size:12.5px">How it works</div><ul class="note" style="margin:0 0 10px;padding-left:18px">${d.how.map(x => `<li>${esc(x)}</li>`).join("")}</ul><div class="callout" style="margin:10px 0"><b>Evidence.</b> ${esc(d.evidence)}</div>`;

  function build() {
    const c = S.cfg, sm_ = c.smart, sc = c.scalp, fs = owner() ? "" : " disabled";
    const t = D.trend, m = D.smart, a = D.scalp;
    $("#p-strategies").innerHTML = `<div class="rise"><div class="card" style="--i:0;margin-bottom:14px"><h3>Choose how the bot trades</h3>
      <p class="note" style="margin:0">Three styles, each with its own switch, settings and scorecard. The <b>Trend following</b> style is the one that held up in testing. The other two are here to try on practice money: they show their own results so you can judge them. You can run several at once; they share one account but never share a trade.</p></div>
      <fieldset${fs} style="border:0;padding:0;margin:0"><div class="sgrid">
      <div class="card" style="--i:1" id="stc_trend">${head(t)}${how(t)}<div id="stPerf_trend">${perfKpis(t)}</div>
        <div class="row" style="gap:8px;flex-wrap:wrap"><span class="sub">Presets:</span><button class="btn sm" data-preset="cautious">Cautious (0.15%)</button><button class="btn sm pri" data-preset="trendmix">Trend mix (0.3%)</button><button class="btn sm" data-preset="bold">Bold (0.5%)</button><button class="btn sm" id="stScan">Open the trend scanner</button></div></div>

      <div class="card" style="--i:2" id="stc_smart">${head(m)}${how(m)}<div id="stPerf_smart">${perfKpis(m)}</div>
        <div class="form">${fld("Crypto", sw("smCrypto", sm_.crypto))}${fld("US stocks", sw("smStocks", sm_.stocks), "Opening-range version, during US market hours, long only")}
          ${fld("Markets to watch", `<select id="smMarkets">${opt([[3, "Top 3 by liquidity"], [5, "Top 5"], [8, "Top 8"], [12, "Top 12"]], sm_.markets)}</select>`, "Crypto only: the most liquid coins on your list are scanned first")}
          ${fld("Confirmations needed", `<select id="smScore">${opt([[4, "4: the bare sequence"], [5, "5: plus liquidity (default)"], [6, "6: plus a big-player clue"], [7, "7: everything agrees"]], sm_.min_score)}</select>`, "Out of 7: trend, sweep, displacement, retest, liquidity, and up to 2 big-player clues")}
          ${fld("Max trades per day", num("smMax", sm_.max_trades_day, 1, 80))}${fld("Risk per trade %", num("smRisk", sm_.risk_pct, 0.1, 80))}${fld("Minimum reward : risk", num("smRR", sm_.rr_min, 0.5, 80))}
          ${fld("Min 24h volume $M (crypto)", num("smVol", sm_.min_dollar_vol_m, 10, 90), "Skips thin markets")}${fld("AI sign-off", sw("smAI", sm_.use_ai), "A final yes/no from your AI on each fully confirmed setup (a few cents a day at most). Rules alone trade if no AI is connected.")}</div>
        <div style="margin:14px 0 6px;font-weight:550;font-size:12.5px">What it is waiting for right now</div><div class="scroll" id="stWait" style="max-height:230px"></div></div>

      <div class="card" style="--i:3" id="stc_scalp">${head(a)}${how(a)}<div id="stPerf_scalp">${perfKpis(a)}</div><div id="stBE"></div>
        <div class="form">${fld("Fee tier", `<select id="scTier">${opt(Object.entries(FEES).map(([k, v]) => [k, v[0]]).concat([["custom", "Custom (type below)"]]), "custom")}</select>`)}
          ${fld("Maker fee (bps)", num("scMk", sc.maker_bps, 0.1, 80), "Limit orders that rest in the book. 1 bp = 0.01%")}${fld("Taker fee (bps)", num("scTk", sc.taker_bps, 0.1, 80), "Market orders, used for stops")}${fld("Slippage on stops (bps)", num("scSl", sc.slip_bps, 0.1, 80))}
          ${fld("Risk per scalp %", num("scRisk", sc.risk_pct, 0.05, 80), "Of the account, fees included. Small by design")}${fld("Max scalps per day", num("scMax", sc.max_trades_day, 10, 80))}${fld("Daily loss limit %", num("scLoss", sc.daily_loss_pct, 0.25, 80), "Stops for the day when reached")}
          ${fld("Target (x ATR)", num("scTgt", sc.tgt, 0.1, 80))}${fld("Stop (x ATR)", num("scStop", sc.stop, 0.1, 80))}${fld("Max minutes in a trade", num("scHold", sc.hold, 1, 80))}
          ${fld("Pause itself if losing", sw("scAuto", sc.auto_pause), "After 40 trades, if the average is below zero after fees it switches itself off")}</div>
        <div class="row" style="gap:12px;flex-wrap:wrap;margin-top:8px"><span class="sub">Coins:</span>${COINS.map(s => `<label class="row" style="gap:5px;font-size:12.5px;cursor:pointer"><input type="checkbox" data-coin="${s}"${sc.markets.includes(s) ? " checked" : ""}>${s.replace("USDT", "")}</label>`).join("")}</div>
        <div class="row" style="margin-top:12px"><button class="btn pri" id="scSave">Save scalper settings</button><button class="btn" id="scRestart">Restart with a fresh scorecard</button><span class="sub" id="scOut"></span></div></div>
      </div></fieldset></div>`;
    icons($("#p-strategies"));
    wire();
    built = true;
  }

  function wire() {
    ["trend", "smart", "scalp"].forEach(id => { $("#sw_" + id).onchange = guard(async e => { await api("/api/style", { style: id, action: e.target.checked ? "on" : "off" }); toast(e.target.checked ? "Switched on" : "Switched off", "info", D[id].name); await refresh(); poll(true); }); });
    $$("[data-preset]").forEach(b => b.onclick = guard(async () => {
      const ok = await confirmSheet("Apply the " + b.textContent.split(" (")[0] + " preset?", "Every crypto market switches to the trend mix on 4-hour candles and the risk limits change. Open swing trades must be closed first.", "Apply", false, b); if (!ok) return;
      try { await api("/api/preset", { name: b.dataset.preset }); } catch (e) { if (/open/.test(e.message) && await confirmSheet("Close open swing trades?", e.message + " Close them and continue?", "Close and apply", true, b)) await api("/api/preset", { name: b.dataset.preset, flatten: true }); else throw e; }
      toast("Preset applied", "win", "Done"); refresh(); }));
    $("#stScan").onclick = () => go("trader");
    const patch = (sec, o) => api("/api/config", { [sec]: o }, true).then(refresh);
    $("#smCrypto").onchange = e => patch("smart", { crypto: e.target.checked }); $("#smStocks").onchange = e => patch("smart", { stocks: e.target.checked });
    $("#smMarkets").onchange = e => patch("smart", { markets: +e.target.value }); $("#smScore").onchange = e => patch("smart", { min_score: +e.target.value });
    $("#smMax").onchange = e => patch("smart", { max_trades_day: +e.target.value }); $("#smRisk").onchange = e => patch("smart", { risk_pct: +e.target.value });
    $("#smRR").onchange = e => patch("smart", { rr_min: +e.target.value }); $("#smVol").onchange = e => patch("smart", { min_dollar_vol_m: +e.target.value }); $("#smAI").onchange = e => patch("smart", { use_ai: e.target.checked });
    $("#scTier").onchange = e => { const f = FEES[e.target.value]; if (f) { $("#scMk").value = f[1]; $("#scTk").value = f[2]; $("#scSl").value = f[3]; } };
    ["scMk", "scTk", "scSl"].forEach(id => $("#" + id).oninput = () => { $("#scTier").value = "custom"; });
    $("#scSave").onclick = guard(async () => {
      const n = id => +$("#" + id).value, coins = $$("[data-coin]:checked").map(x => x.dataset.coin);
      if (!coins.length) throw new Error("Pick at least one coin.");
      await api("/api/config", { scalp: { maker_bps: n("scMk"), taker_bps: n("scTk"), slip_bps: n("scSl"), risk_pct: n("scRisk"), max_trades_day: n("scMax"), daily_loss_pct: n("scLoss"), tgt: n("scTgt"), stop: n("scStop"), hold: n("scHold"), auto_pause: $("#scAuto").checked, markets: coins } });
      $("#scOut").innerHTML = '<span class="up">Saved</span>'; refresh(); });
    $("#scRestart").onclick = guard(async () => { await api("/api/style", { style: "scalp", action: "restart" }); toast("Scalper restarted with a fresh scorecard", "info", "Scalper"); await refresh(); poll(true); });
  }

  const age = t => { const s = Math.max(0, (Date.now() - t) / 1000); return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : Math.round(s / 3600) + " h ago"; };

  function paint() {
    ["trend", "smart", "scalp"].forEach(id => {
      const d = D[id], on = d.enabled; const sw_ = $("#sw_" + id); if (sw_ && document.activeElement !== sw_) sw_.checked = on;
      $("#stOn_" + id).textContent = on ? "On" : "Off"; $("#stOn_" + id).className = "sub " + (on ? "up" : "");
      setHTML($("#stPerf_" + id), perfKpis(d));
    });
    const st = D.smart.status || {}, rows = Object.entries(st).sort((a, b) => b[1].t - a[1].t);
    setHTML($("#stWait"), !D.smart.enabled ? '<div class="empty">Switch the style on to see what it is waiting for.</div>' : rows.length ? `<table><tr><th>Market</th><th>Status</th><th>When</th></tr>${rows.map(([k, v]) => `<tr><td class="t"><b>${esc(k.replace("USDT", ""))}</b></td><td style="white-space:normal">${esc(v.text)}</td><td class="sub">${age(v.t)}</td></tr>`).join("")}</table>` : '<div class="empty">Scanning on the next closed candle...</div>');
    const a = D.scalp.scalp, be = a.breakeven;
    setHTML($("#stBE"), a.n ? `<div class="callout ${a.net_bps >= 0 ? "" : "warn"}" style="margin:0 0 10px"><b>Fee check.</b> ${esc(be ? be.msg : "Collecting trades: " + a.n + " so far (needs 20 for a verdict).")} <span class="sub">${a.per_day.toFixed(0)} trades a day, ${a.today} today, ${D.scalp.pending} order(s) resting.</span></div>` : '<div class="sub" style="margin-bottom:10px">No scalps yet. They appear when price stretches away from its average on a 1-minute candle.</div>');
  }

  async function poll(force) {
    if (busy || !S) return; if (!force && Date.now() - lastT < 4000) return; busy = true; lastT = Date.now();
    try { D = await api("/api/styles", undefined, true); if (!built) build(); paint(); } catch (e) { } finally { busy = false; }
  }
  return { poll, render() { built = false; D = null; poll(true); } };
})();
