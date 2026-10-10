/* Customization + accounts: appearance (theme, accent, density, palette, branding, home layout), trading rules, alerts, security.
   Loaded before app.js; everything runs lazily once app.js has booted. Appearance applies instantly and is cached in this browser so there is no flash on reload. */
const CUST = (() => {
  const DEF = { theme: "dark", accent: "#7c9cff", density: "comfortable", font: 100, radius: 16, palette: "classic", motion: true, brand: "Sentinel", landing: "portfolio", clock: "24", currency: "USD",
    home: { insight: true, kpis: true, safety: true, open: true, markets: true } };
  const ACCENTS = ["#7c9cff", "#a78bfa", "#2dd4bf", "#34d399", "#fbbf24", "#fb923c", "#fb7185", "#38bdf8"];
  const PALETTES = { classic: ["#2ed3a0", "#ff5e7a", "Green up / red down"], colorblind: ["#4aa8ff", "#ffa94d", "Blue up / orange down (colour-blind safe)"], asia: ["#ff5e7a", "#2ed3a0", "Red up / green down"], mono: ["#e6e8ee", "#7b8292", "Light up / grey down"] };
  const LIGHT_PAL = { classic: ["#0d9b73", "#d6334f"], colorblind: ["#1b7fd6", "#d9791a"], asia: ["#d6334f", "#0d9b73"], mono: ["#1b1f2a", "#9aa1b0"] };
  const CURR = { USD: "$", CAD: "C$", EUR: "€", GBP: "£", AUD: "A$", JPY: "¥", USDT: "USDT " };
  const lsGet = () => { try { return JSON.parse(localStorage.getItem("sentinel.ui") || "{}"); } catch (e) { return {}; } };
  let ui = Object.assign({}, DEF, lsGet(), { home: Object.assign({}, DEF.home, (lsGet().home || {})) });
  let built = false, me = { user: "", role: "owner", auth: false }, lastSig = "", landed = false;
  const root = document.documentElement;

  function hsl(hex) {
    const n = parseInt(hex.slice(1), 16), r = (n >> 16 & 255) / 255, g = (n >> 8 & 255) / 255, b = (n & 255) / 255, mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2;
    let h = 0, s = 0; if (mx !== mn) { const d = mx - mn; s = l > .5 ? d / (2 - mx - mn) : d / (mx + mn); h = mx === r ? (g - b) / d + (g < b ? 6 : 0) : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; h *= 60; }
    return [h, s * 100, l * 100];
  }
  const second = hex => { const [h, s, l] = hsl(hex); return `hsl(${Math.round((h + 38) % 360)} ${Math.round(Math.min(95, s))}% ${Math.round(Math.min(78, l + 4))}%)`; };
  const isLight = () => ui.theme === "light" || (ui.theme === "auto" && matchMedia("(prefers-color-scheme: light)").matches);

  function apply(u) {
    ui = Object.assign({}, ui, u || {}); ui.home = Object.assign({}, DEF.home, ui.home || {});
    const light = isLight();
    root.setAttribute("data-theme", light ? "light" : "dark"); root.style.colorScheme = light ? "light" : "dark";
    const acc = /^#[0-9a-f]{6}$/i.test(ui.accent) ? ui.accent : DEF.accent;
    root.style.setProperty("--acc", acc); root.style.setProperty("--acc2", second(acc));
    root.style.setProperty("--zoom", String(Math.max(.85, Math.min(1.25, ui.font / 100))));
    root.style.setProperty("--r", ui.radius + "px"); root.style.setProperty("--r2", Math.round(ui.radius * .62) + "px");
    const pal = (light ? LIGHT_PAL : PALETTES)[ui.palette] || PALETTES.classic;
    root.style.setProperty("--up", pal[0]); root.style.setProperty("--down", pal[1]);
    if (typeof COL !== "undefined") { COL.up = pal[0]; COL.down = pal[1]; COL.acc = acc; COL.acc2 = second(acc); }
    document.body && document.body.classList.toggle("dense", ui.density === "compact");
    document.body && document.body.classList.toggle("nomotion", !ui.motion);
    ["insight", "kpis", "safety", "open", "markets"].forEach(k => document.body && document.body.classList.toggle("hide-" + k, !ui.home[k]));
    document.title = ui.brand || "Sentinel";
    if (typeof setFmt === "function") setFmt(CURR[ui.currency] || "$", ui.clock === "12");
    try { localStorage.setItem("sentinel.theme", ui.theme); localStorage.setItem("sentinel.ui", JSON.stringify(ui)); } catch (e) { }
  }
  document.addEventListener("DOMContentLoaded", () => apply());
  apply();
  if (matchMedia) matchMedia("(prefers-color-scheme: light)").addEventListener && matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => ui.theme === "auto" && apply());

  async function save(patch, reload) {
    apply(patch);
    if (me.role === "owner") { try { await api("/api/config", { ui: patch }, true); } catch (e) { toast(e.message, "loss", "Not saved"); } }
    if (reload) setTimeout(() => location.reload(), 150);           // charts are drawn once, so palette / theme changes redraw them with a quick reload
  }

  function tagSections() {
    const m = [["#insight", "insight", 1], ["#kpis", "kpis", 0], ["#meters", "safety", 1], ["#pPos", "open", 1], ["#hMarkets", "markets", 1]];
    m.forEach(([sel, k, card]) => { const el = $(sel); if (!el) return; const t = card ? el.closest(".card") : el; if (t) t.classList.add("sec-" + k); });
  }

  /* ---- health chip: green when fine, amber/red with a plain-English list of what is wrong and how to fix it */
  let hl = { level: "ok", issues: 0, items: [] }, hlT = 0;
  async function pollHealth() {
    if (Date.now() - hlT < 20000) return; hlT = Date.now();
    try { hl = await api("/api/health", undefined, true); } catch (e) { return; }
    let c = $("#healthChip");
    if (!c) { c = document.createElement("span"); c.id = "healthChip"; c.className = "chip btn"; c.setAttribute("data-tip", "System health"); $("#engChip").after(c); c.onclick = openHealth; }
    c.innerHTML = `<span class="led ${hl.level === "ok" ? "on" : hl.level === "warn" ? "warn" : "bad"}"></span><span>${hl.issues ? hl.issues + (hl.issues > 1 ? " issues" : " issue") : "All good"}</span>`;
  }
  function openHealth() {
    const lv = { ok: "on", info: "", warn: "warn", bad: "bad" };
    const order = { bad: 0, warn: 1, info: 2, ok: 3 };
    const items = hl.items.slice().sort((a, b) => order[a.level] - order[b.level]);
    openSheet(`<h2 style="margin:0 0 4px;font-size:18px">System health</h2><p class="note" style="margin-top:0">${hl.issues ? "Here is what needs attention and what to do." : "Everything checks out."}</p>
      <div style="display:grid;gap:8px;max-height:60vh;overflow:auto">${items.map(i => `<div class="card" style="padding:12px 14px;margin:0"><div class="row" style="gap:9px;align-items:flex-start"><span class="led ${lv[i.level]}" style="margin-top:6px"></span><div><b>${esc(i.title)}</b>${i.detail ? `<div class="sub">${esc(i.detail)}</div>` : ""}${i.fix && (i.level === "warn" || i.level === "bad") ? `<div style="margin-top:4px;font-size:12.5px"><b>Fix:</b> ${esc(i.fix)}</div>` : ""}</div></div></div>`).join("")}</div>`, $("#healthChip"));
  }

  function chip() {
    if ($("#userChip") || !me.auth) return;
    const c = document.createElement("span"); c.className = "chip btn"; c.id = "userChip"; c.setAttribute("data-tip", "Your account");
    c.innerHTML = `<span class="av">${esc((me.user || "?")[0].toUpperCase())}</span><span>${esc(me.user)}</span>`;
    $("#acctChip").after(c);
    c.onclick = e => openSheet(`<h2 style="margin:0 0 4px;font-size:18px">${esc(me.user)} <span class="kbadge ${me.role}">${me.role === "owner" ? "Owner" : "View only"}</span></h2>
      <p class="note">Signed in. ${me.role === "owner" ? "You control trading, keys and other accounts." : "You can watch the dashboard but not change anything."}</p>
      <div class="row" style="margin-top:14px"><button class="btn" id="cuSec">${me.role === "owner" ? "Security settings" : "Change password"}</button><button class="btn danger" id="cuOut">Sign out</button></div>`, c);
    c.addEventListener("click", () => setTimeout(() => {
      const o = $("#cuOut"), s = $("#cuSec");
      if (o) o.onclick = guard(async () => { await api("/api/auth/logout", {}, true); location.href = "/login"; });
      if (s) s.onclick = () => { closeSheet(); go("settings"); setTimeout(() => { const t = $("#cardSecurity"); t && t.scrollIntoView({ behavior: "smooth", block: "start" }); }, 350); };
    }, 30));
  }

  function onState(S) {
    me = S.me || me;
    const sig = JSON.stringify(S.cfg.ui || {});
    if (sig !== lastSig && me.role === "owner") { lastSig = sig; const u = S.cfg.ui || {}; apply({ ...u, home: { ...DEF.home, ...(u.home || {}) } }); }
    else if (me.role !== "owner" && !lastSig) { lastSig = "viewer"; const u = S.cfg.ui || {}; apply({ ...u, home: { ...DEF.home, ...(u.home || {}) } }); }
    chip(); tagSections(); pollHealth();
    if (!landed) { landed = true; if (ui.landing && ui.landing !== "portfolio" && typeof go === "function" && PAGES.some(p => p[0] === ui.landing)) go(ui.landing); }
  }

  /* ------------------------------------------------------------ settings cards */
  const row = (title, sub, ctl) => `<div class="kv"><div><b>${title}</b><span class="sub">${sub || ""}</span></div>${ctl}</div>`;
  const sw = (id, on) => `<label class="sw"><input type="checkbox" id="${id}"${on ? " checked" : ""}><span></span></label>`;
  const opts = (list, cur) => list.map(([v, t]) => `<option value="${v}"${String(v) === String(cur) ? " selected" : ""}>${t}</option>`).join("");
  const hours = Array.from({ length: 25 }, (_, i) => [i, String(i).padStart(2, "0") + ":00 UTC"]);

  function appearanceCard() {
    return `<div class="card" style="--i:5"><h3>Appearance <span class="sub">changes apply instantly</span></h3>
     ${row("Theme", "Dark, light, or follow your device", `<div class="seg" id="cuTheme"><div class="thumb"></div><button data-v="dark">Dark</button><button data-v="light">Light</button><button data-v="auto">Auto</button></div>`)}
     <div style="padding:10px 0;border-bottom:1px solid var(--line)"><b style="font-weight:550">Accent colour</b><div class="swatches" id="cuAcc">${ACCENTS.map(c => `<span class="swatch${c === ui.accent ? " on" : ""}" data-c="${c}" style="background:${c}" role="button" aria-label="${c}"></span>`).join("")}<input type="color" id="cuAccPick" value="${ui.accent}" aria-label="Custom colour"></div></div>
     ${row("Price colours", "Up and down colours on charts and numbers", `<select id="cuPal">${opts(Object.entries(PALETTES).map(([k, v]) => [k, v[2]]), ui.palette)}</select>`)}
     ${row("Layout density", "Compact fits more on screen", `<div class="seg" id="cuDen"><div class="thumb"></div><button data-v="comfortable">Comfortable</button><button data-v="compact">Compact</button></div>`)}
     ${row("Text size", "<span id=\"cuFontV\">" + ui.font + "%</span>", `<input type="range" id="cuFont" min="90" max="115" step="5" value="${ui.font}">`)}
     ${row("Corner roundness", "<span id=\"cuRadV\">" + ui.radius + " px</span>", `<input type="range" id="cuRad" min="6" max="24" step="2" value="${ui.radius}">`)}
     ${row("Animations", "Turn off for a calmer, faster feel", sw("cuMotion", ui.motion))}
     ${row("Currency symbol", "Display only; the account is simulated", `<select id="cuCur">${opts(Object.keys(CURR).map(k => [k, k + " (" + CURR[k].trim() + ")"]), ui.currency)}</select>`)}
     ${row("Clock", "", `<select id="cuClock">${opts([["24", "24-hour"], ["12", "12-hour"]], ui.clock)}</select>`)}
     ${row("Open on", "The page you land on after signing in", `<select id="cuLand">${opts(PAGES.filter(p => p[0] !== "lab").map(p => [p[0], p[2]]), ui.landing)}</select>`)}
     ${row("App name", "Shown in the browser tab and sign-in page", `<input id="cuBrand" maxlength="24" value="${esc(ui.brand)}" style="max-width:170px">`)}
     <div style="padding-top:10px"><b style="font-weight:550">Home page sections</b><div class="row" style="flex-wrap:wrap;gap:14px;margin-top:8px">${[["insight", "Insight"], ["kpis", "Key numbers"], ["safety", "Safety meters"], ["open", "Open trades"], ["markets", "My markets"]].map(([k, t]) => `<label class="row" style="gap:7px;font-size:12.5px;cursor:pointer"><input type="checkbox" data-home="${k}"${ui.home[k] ? " checked" : ""}> ${t}</label>`).join("")}</div></div></div>`;
  }

  function rulesCard() {
    const r = S.cfg.rules, h = r.hours;
    const dn = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
    return `<div class="card" style="--i:6"><h3>Trading rules <span class="sub">checked before every new trade</span></h3>
     ${row("Only trade at set hours", "UTC clock. Overnight windows work (22 to 06)", sw("ruHrs", h.enabled))}
     <div class="row" id="ruHrsBox" style="gap:10px;padding:8px 0 4px;flex-wrap:wrap"><select id="ruFrom">${opts(hours.slice(0, 24), h.start)}</select><span class="sub">to</span><select id="ruTo">${opts(hours, h.end)}</select>
       <div class="days" id="ruDays">${dn.map((d, i) => `<label><input type="checkbox" value="${i}"${(h.days || []).includes(i) ? " checked" : ""}>${d}</label>`).join("")}</div></div>
     ${row("Allowed direction", "Longs only is simplest for stocks", `<select id="ruDir">${opts([["both", "Long and short"], ["long", "Long only"], ["short", "Short only"]], r.direction)}</select>`)}
     ${row("Max new trades per day", "0 means no limit", `<input id="ruMax" type="number" min="0" step="1" value="${r.max_trades_day}" style="max-width:90px">`)}
     ${row("Cool-down per market", "Minutes to wait after closing a market before re-entering", `<input id="ruCool" type="number" min="0" step="5" value="${r.cooldown_min}" style="max-width:90px">`)}
     ${row("Pause after stop-outs", "If this many stops hit within an hour, pause new trades (0 = off)", `<input id="ruStops" type="number" min="0" step="1" value="${r.pause_after_stops}" style="max-width:90px">`)}
     ${row("Pause length", "Minutes", `<input id="ruPause" type="number" min="5" step="5" value="${r.pause_minutes}" style="max-width:90px">`)}
     ${row("Max money at risk, day trades", "Total of all open stop distances, % of account", `<input id="ruRisk" type="number" min="0" step="0.5" value="${r.max_open_risk_pct}" style="max-width:90px">`)}
     <div class="row" style="margin-top:14px"><button class="btn pri" id="ruSave">Save rules</button><span class="sub" id="ruOut"></span></div></div>`;
  }

  function notifyCard() {
    const n = S.cfg.notify;
    return `<div class="card" style="--i:7"><h3>Alerts <span class="sub">phone or desktop notifications</span></h3>
     <p class="note" style="margin-top:0">Get a message when a trade opens or closes or something needs attention. Works with Discord, Slack, Telegram or any webhook.</p>
     <div class="form"><div><label class="f">Service</label><select id="nfKind">${opts([["discord", "Discord"], ["slack", "Slack"], ["telegram", "Telegram"], ["generic", "Other (JSON webhook)"]], n.kind)}</select></div>
      <div id="nfUrlBox"><label class="f">Webhook URL ${n.has_url ? '<span class="up">saved</span>' : ""}</label><input id="nfUrl" type="password" placeholder="${n.has_url ? "leave blank to keep the saved one" : "https://..."}" autocomplete="off"></div>
      <div id="nfTgBox" style="display:none"><label class="f">Bot token ${n.has_telegram_token ? '<span class="up">saved</span>' : ""}</label><input id="nfTok" type="password" placeholder="${n.has_telegram_token ? "leave blank to keep" : "123456:ABC..."}" autocomplete="off"></div>
      <div id="nfChatBox" style="display:none"><label class="f">Chat ID</label><input id="nfChat" value="${esc(n.telegram_chat)}"></div></div>
     <div class="row" style="gap:18px;margin-top:12px;flex-wrap:wrap">${[["nfOpen", "Trade opened", n.on_open], ["nfClose", "Trade closed", n.on_close], ["nfAlert", "Alerts and errors", n.on_alert]].map(([id, t, on]) => `<label class="row" style="gap:7px;font-size:12.5px;cursor:pointer"><input type="checkbox" id="${id}"${on ? " checked" : ""}> ${t}</label>`).join("")}</div>
     <div class="row" style="margin-top:14px"><button class="btn pri" id="nfSave">Save</button><button class="btn" id="nfTest">Send a test</button><span class="sub" id="nfOut"></span></div></div>`;
  }

  async function securityCard() {
    if (me.role !== "owner" && !me.auth) return "";
    if (!me.auth) {
      return `<div class="card" id="cardSecurity" style="--i:8"><h3>Sign-in <span class="sub">protect the dashboard</span></h3>
       <p class="note" style="margin-top:0">Right now anyone using this computer can open Sentinel. Turn on sign-in to require a username and password. Required if you put it on the internet.</p>
       <div class="form"><div><label class="f">Username</label><input id="seUser" autocomplete="username" placeholder="owner"></div><div><label class="f">Password (10+ characters)</label><input id="sePw" type="password" autocomplete="new-password"></div><div><label class="f">Repeat password</label><input id="sePw2" type="password" autocomplete="new-password"></div></div>
       <div class="row" style="margin-top:14px"><button class="btn pri" id="seOn">Turn on sign-in</button><span class="sub" id="seOut"></span></div></div>`;
    }
    let list = "";
    if (me.role === "owner") {
      try { const u = await api("/api/auth/users", undefined, true); list = `<table class="ulist" id="seUsers">${u.users.map(x => `<tr><td><b>${esc(x.name)}</b> <span class="kbadge ${x.role}">${x.role === "owner" ? "Owner" : "View only"}</span><span class="sub">${x.last_login ? "last sign-in " + new Date(x.last_login * 1000).toLocaleString() : "never signed in"}</span></td><td style="text-align:right">${x.name === me.user ? '<span class="sub">you</span>' : `<button class="btn sm" data-reset="${esc(x.name)}">New password</button> <button class="btn sm danger" data-del="${esc(x.name)}">Remove</button>`}</td></tr>`).join("")}</table>`; } catch (e) { }
    }
    return `<div class="card" id="cardSecurity" style="--i:8"><h3>Sign-in and accounts</h3>
      <div class="kv"><div><b>Signed in as ${esc(me.user)}</b><span class="sub">${me.role === "owner" ? "Owner: full control" : "View only"}</span></div><div class="row"><button class="btn sm" id="seOthers">Sign out other devices</button><button class="btn sm danger" id="seOut2">Sign out</button></div></div>
      <div class="form" style="margin-top:12px"><div><label class="f">Current password</label><input id="seOld" type="password" autocomplete="current-password"></div><div><label class="f">New password</label><input id="seNew" type="password" autocomplete="new-password"></div><div style="align-self:end"><button class="btn" id="sePwBtn">Change password</button></div></div>
      ${me.role === "owner" ? `<h3 style="margin-top:22px;font-size:13px">People who can sign in</h3>${list}
       <div class="form" style="margin-top:10px"><div><label class="f">Username</label><input id="nuName" autocomplete="off"></div><div><label class="f">Password</label><input id="nuPw" type="password" autocomplete="new-password"></div><div><label class="f">Access</label><select id="nuRole"><option value="viewer">View only</option><option value="owner">Owner</option></select></div><div style="align-self:end"><button class="btn" id="nuAdd">Add person</button></div></div>` : ""}
      <span class="sub" id="seMsg"></span></div>`;
  }

  async function settings() {
    const host = $("#p-settings .grid.g2"); if (!host || built) return; built = true;
    const sec = await securityCard();
    host.insertAdjacentHTML("beforeend", (me.role === "owner" ? appearanceCard() : appearanceCard()) + (me.role === "owner" ? rulesCard() + notifyCard() : "") + sec);
    icons && icons(host);
    wireAppearance(); if (me.role === "owner") { wireRules(); wireNotify(); } wireSecurity();
  }

  function wireAppearance() {
    seg($("#cuTheme"), v => save({ theme: v }, true)).set(ui.theme);
    seg($("#cuDen"), v => save({ density: v })).set(ui.density);
    $$("#cuAcc .swatch").forEach(s => s.onclick = () => { $$("#cuAcc .swatch").forEach(x => x.classList.toggle("on", x === s)); $("#cuAccPick").value = s.dataset.c; save({ accent: s.dataset.c }); });
    $("#cuAccPick").oninput = e => { $$("#cuAcc .swatch").forEach(x => x.classList.remove("on")); apply({ accent: e.target.value }); };
    $("#cuAccPick").onchange = e => save({ accent: e.target.value });
    $("#cuPal").onchange = e => save({ palette: e.target.value }, true);
    $("#cuFont").oninput = e => { $("#cuFontV").textContent = e.target.value + "%"; apply({ font: +e.target.value }); }; $("#cuFont").onchange = e => save({ font: +e.target.value });
    $("#cuRad").oninput = e => { $("#cuRadV").textContent = e.target.value + " px"; apply({ radius: +e.target.value }); }; $("#cuRad").onchange = e => save({ radius: +e.target.value });
    $("#cuMotion").onchange = e => save({ motion: e.target.checked });
    $("#cuCur").onchange = e => save({ currency: e.target.value }); $("#cuClock").onchange = e => save({ clock: e.target.value });
    $("#cuLand").onchange = e => save({ landing: e.target.value });
    $("#cuBrand").onchange = e => save({ brand: (e.target.value || "Sentinel").trim().slice(0, 24) });
    $$("[data-home]").forEach(c => c.onchange = () => save({ home: { ...ui.home, [c.dataset.home]: c.checked } }));
  }

  function wireRules() {
    const sync = () => { $("#ruHrsBox").style.display = $("#ruHrs").checked ? "flex" : "none"; }; $("#ruHrs").onchange = sync; sync();
    $("#ruSave").onclick = guard(async () => {
      const n = id => +$("#" + id).value;
      await api("/api/config", { rules: { hours: { enabled: $("#ruHrs").checked, start: n("ruFrom"), end: n("ruTo"), days: $$("#ruDays input:checked").map(x => +x.value) }, direction: $("#ruDir").value,
        max_trades_day: n("ruMax"), cooldown_min: n("ruCool"), pause_after_stops: n("ruStops"), pause_minutes: n("ruPause"), max_open_risk_pct: n("ruRisk") } });
      $("#ruOut").innerHTML = '<span class="up">Saved. Applies to the next trade.</span>'; refresh();
    });
  }

  function wireNotify() {
    const sync = () => { const t = $("#nfKind").value === "telegram"; $("#nfUrlBox").style.display = t ? "none" : ""; $("#nfTgBox").style.display = $("#nfChatBox").style.display = t ? "" : "none"; }; $("#nfKind").onchange = sync; sync();
    const body = () => ({ kind: $("#nfKind").value, url: $("#nfUrl").value, telegram_token: $("#nfTok").value, telegram_chat: $("#nfChat").value, on_open: $("#nfOpen").checked, on_close: $("#nfClose").checked, on_alert: $("#nfAlert").checked });
    $("#nfSave").onclick = guard(async () => { await api("/api/config", { notify: body() }); $("#nfUrl").value = $("#nfTok").value = ""; $("#nfOut").innerHTML = '<span class="up">Saved</span>'; refresh(); });
    $("#nfTest").onclick = guard(async () => { $("#nfOut").innerHTML = '<span class="spin"></span>sending'; const b = body(); await api("/api/config", { notify: b }, true); const r = await api("/api/notify/test", {}, true); $("#nfOut").innerHTML = r.ok ? '<span class="up">Sent. Check your phone.</span>' : `<span class="down">${esc(r.error)}</span>`; });
  }

  function wireSecurity() {
    const on = $("#seOn");
    if (on) on.onclick = guard(async () => {
      if ($("#sePw").value !== $("#sePw2").value) { $("#seOut").innerHTML = '<span class="down">The two passwords differ</span>'; return; }
      const r = await api("/api/auth/setup", { username: $("#seUser").value, password: $("#sePw").value }); toast("Sign-in is on. You are signed in as the owner.", "win", "Protected"); setTimeout(() => location.reload(), 600);
    });
    const o2 = $("#seOut2"); if (o2) o2.onclick = guard(async () => { await api("/api/auth/logout", {}, true); location.href = "/login"; });
    const oth = $("#seOthers"); if (oth) oth.onclick = guard(async () => { await api("/api/auth/sessions", {}); toast("Every other device was signed out.", "info", "Done"); });
    const pw = $("#sePwBtn"); if (pw) pw.onclick = guard(async () => { await api("/api/auth/password", { old: $("#seOld").value, new: $("#seNew").value }); $("#seOld").value = $("#seNew").value = ""; $("#seMsg").innerHTML = '<span class="up">Password changed. Other devices were signed out.</span>'; });
    const add = $("#nuAdd"); if (add) add.onclick = guard(async () => { await api("/api/auth/users", { action: "add", username: $("#nuName").value, password: $("#nuPw").value, role: $("#nuRole").value }); built = false; $("#cardSecurity").remove(); settings(); });
    $$("[data-del]").forEach(b => b.onclick = guard(async () => { if (!await confirmSheet("Remove " + b.dataset.del + "?", "They will be signed out and can no longer open the dashboard.", "Remove", true, b)) return; await api("/api/auth/users", { action: "remove", username: b.dataset.del }); built = false; $("#cardSecurity").remove(); settings(); }));
    $$("[data-reset]").forEach(b => b.onclick = guard(async () => { const p = prompt("New password for " + b.dataset.reset + " (10+ characters):"); if (!p) return; await api("/api/auth/users", { action: "reset", username: b.dataset.reset, password: p }); toast("Password changed and their devices signed out.", "info", "Done"); }));
  }

  return { onState, settings, apply, get ui() { return ui; } };
})();
