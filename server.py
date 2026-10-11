"""Dashboard + API server (standard library only). Run: py server.py   ->  http://localhost:8765
Everything except POST /hook is restricted to local requests, so exposing the port through a tunnel for TradingView
webhooks does not expose the dashboard."""
import gzip, json, mimetypes, os, sys, threading, time, traceback, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tradebot import data, backtest as B, ai, analyst, llm, catalog, chat, indicators as I
from tradebot.auth import Auth, AuthError
from tradebot.engine import Engine
from tradebot.store import Store, SECRETS, DATA as DATA_DIR
from tradebot.strategies import STRATEGIES

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
store = Store()
store.save_cfg()
store.migrate_watchlist()
engine = Engine(store)
auth = Auth(store.db, store.lock, DATA_DIR)
jobs = {}

# ---- hosting. Local use needs none of this: no login, loopback only. To publish it (for example on a subdomain behind Caddy/nginx):
#   SENTINEL_HOSTS=sentineltrade.example.com   which Host names to answer (and it turns login on)
#   SENTINEL_AUTH=1                            force login even without a public host
#   SENTINEL_TRUST_PROXY=1                     believe X-Forwarded-For / -Proto (automatic when the proxy is on this machine)
#   HOST / PORT                                where to listen (default 127.0.0.1:8765; use 0.0.0.0 inside Docker)
PUBLIC_HOSTS = {h.strip().lower() for h in os.environ.get("SENTINEL_HOSTS", "").split(",") if h.strip()}
FORCE_AUTH = os.environ.get("SENTINEL_AUTH", "").lower() in ("1", "true", "yes") or bool(PUBLIC_HOSTS)
BIND_HOST = os.environ.get("HOST", "127.0.0.1")
TRUST_PROXY = os.environ.get("SENTINEL_TRUST_PROXY", "").lower() in ("1", "true", "yes")
PUBLIC_PATHS = ("/healthz", "/login", "/login.html", "/style.css", "/api/auth/status", "/api/auth/login", "/api/auth/setup")
COOKIE = "sentinel_session"


def cli_token():
    """A random secret kept in data/cli_token.txt. Only requests that arrive from this machine itself (no proxy) can use it."""
    path = os.path.join(DATA_DIR, "cli_token.txt")
    try:
        with open(path) as f:
            t = f.read().strip()
        if t:
            return t
    except OSError:
        pass
    import secrets as _s
    t = _s.token_urlsafe(32)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w") as f:
        f.write(t + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return t


def auth_on():
    return FORCE_AUTH or bool(store.get("auth_required"))

_candle_cache = {}


# ---------------------------------------------------------------- helpers
def downsample(ts, ys, n=320):
    step = max(1, len(ys) // n)
    pts = [[ts[i], round(ys[i], 2)] for i in range(0, len(ys), step)]
    if pts[-1][0] != ts[-1]:
        pts.append([ts[-1], round(ys[-1], 2)])
    return pts


def clean(m):
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items() if k not in ("curve", "trade_list")}


def verdict(oos):
    sh, pf, n = oos["sharpe"], oos["pf"], oos["trades"]
    if n < 20:
        return ("unproven", "Only %d out-of-sample trades - too few to conclude anything." % n)
    if sh < 0.2 or pf < 1.1:
        return ("noedge", "No reliable edge out-of-sample (Sharpe %.2f, PF %.2f). Do not trade this live." % (sh, pf))
    if sh < 0.7:
        return ("weak", "Weak edge (Sharpe %.2f). Expect modest returns relative to drawdown; paper trade first." % sh)
    return ("promising", "Promising out-of-sample (Sharpe %.2f, PF %.2f) - still paper trade before risking money." % (sh, pf))


def run_any(c, strat, params, market, tf, risk):
    """Backtest one market. A trendmix runs each member as its own sleeve (own position and trailing stop) on one account, as the live engine does."""
    if strat != "trendmix":
        return B.run(c, strat, params, market, tf, risk=risk)
    runs = [B.run(c, m["strat"], m["p"], market, tf, risk=risk) for m in params["members"]]
    ts, eq = B.portfolio([(c, m) for m in runs])
    trades = [t for m in runs for t in m["trade_list"]]
    out = B.metrics(eq, trades, tf, 10000.0)
    out["trades"] = len(trades)
    return out


def get_hist(market, symbol, tf, years):
    return data.fetch(market, symbol, tf, years=years, max_age_s=6 * 3600)


def run_job(kind, req):
    j = jobs[req["job"]]
    try:
        risk = float(req.get("risk", store.cfg["risk"]["per_trade_pct"])) / 100
        years = min(int(req.get("years", 5)), 7)
        if kind == "portfolio":
            runs, rows = [], []
            insts = [i for i in store.cfg["instruments"] if i["enabled"]]
            for k, inst in enumerate(insts):
                j["progress"] = "%s (%d/%d)" % (inst["symbol"], k + 1, len(insts))
                c = get_hist(inst["market"], inst["symbol"], inst["tf"], years)
                m = run_any(c, inst["strategy"], inst["params"], inst["market"], inst["tf"], risk)
                runs.append((c, m))
                rows.append(dict(symbol=inst["symbol"], ret=round(m["ret"] * 100, 1), mdd=round(m["mdd"] * 100, 1), sharpe=round(m["sharpe"], 2), trades=m["trades"]))
            ts, eq = B.portfolio(runs)
            half = len(eq) // 2
            full = B.metrics(eq, [], "4h", 10000.0)
            a, b = B.metrics(eq[:half], [], "4h", 10000.0), B.metrics([x / eq[half] * 10000 for x in eq[half:]], [], "4h", 10000.0)
            full["trades"] = sum(m["trades"] for _, m in runs)
            full["pf"] = 0
            j["result"] = dict(kind=kind, metrics=clean(full), first_half=clean(a), second_half=clean(b), per_symbol=rows,
                               curve=downsample(ts, eq), verdict=verdict(dict(b, trades=full["trades"], pf=1.2)))
        else:
            market, sym, tf, strat = req["market"], req["symbol"], req.get("tf", "4h"), req["strategy"]
            j["progress"] = "downloading %s history" % sym
            c = get_hist(market, sym, tf, years)
            bh = [x[4] / c[0][4] * 10000 for x in c]
            if kind == "optimize":
                j["progress"] = "searching parameters (walk-forward)"
                if strat == "trendmix":                       # nothing to search: the mix is already fixed from the research
                    k = int(len(c) * 0.6)
                    r = dict(params=req.get("params") or catalog.defaults_for(market)[1], full=run_any(c, strat, req.get("params") or catalog.defaults_for(market)[1], market, tf, risk),
                             oos=run_any(c[k - 300:], strat, req.get("params") or catalog.defaults_for(market)[1], market, tf, risk))
                    r["insample"] = run_any(c[:k], strat, r["params"], market, tf, risk)
                else:
                    r = B.optimize(c, strat, market, tf, risk=risk)
                m, oos = r["full"], r["oos"]
                j["result"] = dict(kind=kind, params=r["params"], insample=clean(r["insample"]), oos=clean(oos), metrics=clean(m),
                                   curve=downsample([x[0] for x in c], m["curve"]), buyhold=downsample([x[0] for x in c], bh),
                                   trades=[list(t) for t in m["trade_list"][-80:]], verdict=verdict(oos), split_ts=c[int(len(c) * 0.6)][0])
            else:
                m = run_any(c, strat, req["params"], market, tf, risk)
                k = int(len(c) * 0.6)
                oos = run_any(c[k - 300:], strat, req["params"], market, tf, risk)
                j["result"] = dict(kind=kind, params=req["params"], metrics=clean(m), oos=clean(oos), curve=downsample([x[0] for x in c], m["curve"]),
                                   buyhold=downsample([x[0] for x in c], bh), trades=[list(t) for t in m["trade_list"][-80:]], verdict=verdict(oos))
        j["state"] = "done"
    except Exception as e:
        j["state"] = "error"
        j["error"] = str(e)[:300]
        traceback.print_exc()


def candles_view(inst_id, n=300, tf=None):
    inst = next((i for i in store.cfg["instruments"] if i["id"] == inst_id), None)
    if not inst:
        return {"error": "unknown instrument"}
    tf = tf if tf in data.TF_MS else inst["tf"]
    key = (inst_id, tf, int(time.time() // 10))
    if key not in _candle_cache:
        _candle_cache.clear()
        _candle_cache[key] = data.fetch_recent(inst["market"], inst["symbol"], tf, n + 250, forming=True)
    allb = _candle_cache[key]
    tfms = data.TF_MS[tf]
    forming = allb[-1][0] + tfms > int(time.time() * 1000)
    hist = allb[:-1] if forming else allb                      # indicators only ever use CLOSED bars
    cl, h, l = [x[4] for x in hist], [x[2] for x in hist], [x[3] for x in hist]
    p = inst["params"]
    overlays = {}
    if tf != inst["tf"]:
        pass                                                   # strategy levels only make sense on the timeframe the bot trades
    elif inst["strategy"] == "trendmix":                       # show the first member's channel + the 200 EMA
        m0 = p["members"][0]["p"]
        overlays["upper"], overlays["lower"], overlays["trend"] = I.highest(h, m0["n"]), I.lowest(l, m0["n"]), I.ema(cl, 200)
    elif inst["strategy"] == "donchian":
        overlays["upper"], overlays["lower"] = I.highest(h, p["n"]), I.lowest(l, p["n"])
        if p.get("trend"):
            overlays["trend"] = I.ema(cl, p["trend"])
    elif inst["strategy"] == "emacross":
        overlays["fast"], overlays["slow"] = I.ema(cl, p["fast"]), I.ema(cl, p["slow"])
    else:
        overlays["trend"] = I.ema(cl, p["trend"])
    off = len(hist) - n
    trades = [t for t in store.trades(500) if t["inst"].partition("#")[0] in (inst_id, "day:" + inst_id)]
    return dict(inst=inst, candles=allb[-(n + (1 if forming else 0)):], forming=forming, overlays={k: v[max(0, off):] for k, v in overlays.items()},
                trades=[dict(t_open=t["t_open"], t_close=t["t_close"], side=t["side"], entry=t["entry"], exit=t["exit"], pnl=t["pnl"]) for t in trades],
                position=store.positions().get(inst_id) or store.positions().get("day:" + inst_id) or next((v for k, v in store.positions().items() if k.startswith(inst_id + "#")), None))


MAX_MARKETS = 80
SPEED_TFS = ("5m", "15m", "1h", "4h")


def speed_action(b):
    """Switch every swing-bot market to a new timeframe (ids, lookbacks and state follow). Flattens open swing trades first."""
    tf = b.get("tf")
    if tf not in SPEED_TFS:
        return 400, {"error": "timeframe must be one of %s" % ", ".join(SPEED_TFS)}
    ins = store.cfg["instruments"]
    swing_open = [pid for pid in store.positions() if not pid.startswith("day:")]
    if swing_open and not b.get("flatten"):
        return 409, {"error": "close open swing trades first (or confirm flatten)"}
    for pid in swing_open:
        engine.close_by_id(pid, "timeframe change")
    mins = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}
    f = min(6, 240 // mins[tf])                    # longer lookbacks on faster candles so levels still mean something
    for i in ins:
        old = data.TF_MS[i["tf"]]
        engine.status.pop(i["id"], None)
        base = 240 // max(1, int(old / 60000)) if old < data.TF_MS["4h"] else 1
        for p in ([m["p"] for m in i["params"]["members"]] if i["strategy"] == "trendmix" else [i["params"]]):
            for k in ("n", "fast", "slow", "trend"):
                if p.get(k):
                    p[k] = max(2, int(round(p[k] / max(1, min(6, base)) * f)))
        i["tf"] = tf
        i["id"] = "%s:%s:%s" % (i["market"], i["symbol"], tf)
    store.save_cfg()
    return 200, {"ok": True}


def watchlist_action(b):
    ins, act = store.cfg["instruments"], b.get("action")
    tf = ins[0]["tf"] if ins else "4h"
    if act == "add":
        added = 0
        for it in b.get("items", []):
            if len(ins) >= MAX_MARKETS:
                return 400, {"error": "Watchlist is full (%d markets). Remove some first." % MAX_MARKETS}
            if it.get("market") not in ("futures", "forex", "stock") or not it.get("symbol"):
                continue
            if any(i["market"] == it["market"] and i["symbol"] == it["symbol"] for i in ins):
                continue
            ins.append(catalog.make_instrument(it, tf, bool(b.get("trade", True)), bool(it.get("fav"))))
            added += 1
        store.save_cfg()
        return 200, {"ok": True, "added": added}
    inst = next((i for i in ins if i["id"] == b.get("id")), None)
    if not inst:
        return 404, {"error": "unknown market"}
    if act == "fav":
        inst["fav"] = bool(b.get("value"))
    elif act == "trade":
        inst["enabled"] = bool(b.get("value"))
    elif act == "remove":
        engine.close_by_id(inst["id"], "removed")
        ins.remove(inst)
        engine.status.pop(inst["id"], None)
    else:
        return 400, {"error": "bad action"}
    store.save_cfg()
    return 200, {"ok": True}


PRESETS = {
    # each preset pairs a risk per trade with a drawdown halt that fits its historical worst dip (see docs/STRATEGY_STUDY.md, "the risk dial")
    "cautious": dict(label="Cautious 0.15%", risk=dict(per_trade_pct=0.15, max_positions=15, max_leverage_per_pos=3.0, max_total_leverage=3.0, max_drawdown_halt_pct=15.0)),
    "trendmix": dict(label="Trend mix 0.3% (tested default)", risk=dict(per_trade_pct=0.3, max_positions=15, max_leverage_per_pos=3.0, max_total_leverage=3.0, max_drawdown_halt_pct=20.0)),
    "balanced": dict(label="Balanced 0.5% (more profit, deeper dips)", risk=dict(per_trade_pct=0.5, max_positions=15, max_leverage_per_pos=3.0, max_total_leverage=3.0, max_drawdown_halt_pct=30.0)),
    "bold": dict(label="Bold 1% (heavy dips)", risk=dict(per_trade_pct=1.0, max_positions=15, max_leverage_per_pos=3.0, max_total_leverage=3.0, max_drawdown_halt_pct=50.0)),
}


def preset_action(b):
    """One tap: every crypto market runs the validated 4h trend mix, the 40-coin universe is added, and the risk limits match the tested setup."""
    p = PRESETS.get(b.get("name"))
    if not p:
        return 400, {"error": "unknown preset"}
    swing_open = [pid for pid in store.positions() if not pid.startswith("day:")]
    if swing_open and not b.get("flatten"):
        return 409, {"error": "%d swing trade(s) are open; close them first or confirm flatten" % len(swing_open)}
    for pid in swing_open:
        engine.close_by_id(pid, "preset change")
    ins = store.cfg["instruments"]
    have = {(i["market"], i["symbol"]) for i in ins}
    for sym in catalog.UNIVERSE:
        if ("futures", sym) not in have and len(ins) < MAX_MARKETS:
            row = catalog.lookup("futures", sym) or dict(market="futures", symbol=sym, name=sym, cat="crypto")
            ins.append(catalog.make_instrument(row, "4h", True, False))
    for i in ins:
        if i["market"] == "futures":
            i["strategy"], i["params"] = catalog.defaults_for("futures")
            i["tf"] = "4h"
            i["id"] = "futures:%s:4h" % i["symbol"]
    ins.sort(key=lambda i: (i["market"] != "futures", not i.get("fav"), catalog.UNIVERSE.index(i["symbol"]) if i["symbol"] in catalog.UNIVERSE else 999))
    engine.status.clear()
    engine.sub_status.clear()
    store.save_cfg({"risk": p["risk"]})
    store.save_cfg()
    store.event("info", "Preset applied: %s (%d crypto markets)" % (p["label"], sum(1 for i in ins if i["market"] == "futures")))
    return 200, {"ok": True}


def style_action(b):
    """Switch a bot style on or off; the scalper's 'restart' clears its auto-pause and starts a fresh scorecard."""
    sid, act = b.get("style"), b.get("action")
    if sid not in ("trend", "smart", "scalp") or act not in ("on", "off", "restart"):
        return 400, {"error": "bad request"}
    if sid in ("smart", "scalp") and act in ("on", "restart") and store.cfg["mode"] == "live":
        return 400, {"error": "this style runs in practice mode only; switch the account to Paper mode first"}
    if sid == "trend":
        engine.start() if act == "on" else engine.stop()
    else:
        if sid == "scalp" and act == "restart":
            store.put("scalp_since", int(time.time() * 1000))
            engine.scalper.pend.clear()
        store.save_cfg({sid: {"enabled": act != "off"}})
        if act != "off" and not engine.running and not engine.halted():
            engine.start()
    return 200, {"ok": True}


def _ai_cfg(b):
    """Saved AI settings overlaid with whatever the form currently holds (so 'Test' works before 'Save'). A blank key means 'use the saved one'."""
    cfg = dict(store.cfg["ai"])
    for k in ("provider", "base_url", "model", "api_key"):
        if b.get(k):
            cfg[k] = b[k]
    if b.get("provider") and b["provider"] != store.cfg["ai"].get("provider") and not b.get("base_url"):
        cfg["base_url"] = llm.PROVIDERS.get(b["provider"], {}).get("base_url", cfg.get("base_url", ""))
    if b.get("provider") and b["provider"] != store.cfg["ai"].get("provider") and not b.get("api_key"):
        cfg["api_key"] = ""
    return cfg


# ---------------------------------------------------------------- HTTP
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def secrets_equal(a, b):
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 60                      # drop idle or trickling connections instead of holding a thread forever

    def log_message(self, *a):
        pass

    # ---- request identity -------------------------------------------------
    def _host(self):
        h = (self.headers.get("Host") or "").lower()
        return "[::1]" if h.startswith("[") else h.rsplit(":", 1)[0]

    def _peer_trusted(self):
        return TRUST_PROXY or self.client_address[0] in ("127.0.0.1", "::1")

    def _ip(self):
        cf = self.headers.get("CF-Connecting-IP")                  # Cloudflare (or its tunnel) always sets this to the real visitor
        if cf and self._peer_trusted():
            return cf.strip()[:64]
        xff = self.headers.get("X-Forwarded-For")
        if xff and self._peer_trusted():
            return xff.split(",")[-1].strip()[:64]            # the address our own proxy appended (the last one cannot be forged by the client)
        return self.client_address[0]

    def _https(self):
        return self._peer_trusted() and (self.headers.get("X-Forwarded-Proto") or "").split(",")[-1].strip() == "https"

    def _local(self):
        """A genuinely local request: loopback peer, local Host name, and no proxy in between."""
        return self._host() in LOCAL_HOSTS and not self.headers.get("X-Forwarded-For") and self.client_address[0] in ("127.0.0.1", "::1")

    def _cli_ok(self):
        h = self.headers.get("Authorization") or ""
        return h.startswith("Bearer ") and secrets_equal(h[7:].strip(), cli_token())

    def _token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return ""

    ZIPPABLE = ("application/json", "text/javascript", "text/css", "text/html", "text/csv", "image/svg+xml")

    def _send(self, code, obj, ctype="application/json", cookie=None, location=None):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        gz = len(b) > 1200 and ctype.split(";")[0] in self.ZIPPABLE and "gzip" in (self.headers.get("Accept-Encoding") or "")
        if gz:                                                      # the dashboard state is polled every few seconds: 58 KB becomes about 8 KB
            b = gzip.compress(b, 5)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", self.__dict__.pop("_cc", "no-store"))   # per-request: HTTP/1.1 keep-alive reuses this handler
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if self._https():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        if cookie is not None:
            token, life = cookie
            self.send_header("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s" % (COOKIE, token, life if token else 0, "; Secure" if self._https() else ""))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(b)

    def _guard(self, method, path):
        """-> (user, role) when the request may proceed, else None after sending the refusal."""
        host = self._host()
        if host not in LOCAL_HOSTS and host not in PUBLIC_HOSTS:
            self._send(421, {"error": "unknown host"})
            return None
        if method == "POST":
            origin = self.headers.get("Origin")
            if origin and origin.split("://", 1)[-1].lower() != (self.headers.get("Host") or "").lower():
                self._send(403, {"error": "cross-site request refused"})
                return None
        if not auth_on():
            if not self._local():
                self._send(403, {"error": "local only"})
                return None
            return ("", "owner")
        if path in PUBLIC_PATHS or path.startswith("/vendor/"):
            return ("", "anon")
        if self._local() and self._cli_ok():                      # the command-line tool on this machine, using data/cli_token.txt
            return ("cli", "owner")
        sess = auth.session(self._token())
        if sess:
            if method == "POST" and self.headers.get("X-Sentinel") != "1":
                self._send(403, {"error": "missing X-Sentinel header"})
                return None
            return sess
        if path.startswith("/api/") or method == "POST":
            self._send(401, {"error": "login"})
        else:
            self._send(302, b"", "text/plain", location="/login")
        return None

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        ident = self._guard("GET", u.path)
        if ident is None:
            return
        self._versioned = "v" in q
        try:
            if u.path == "/healthz":
                return self._send(200, {"ok": True})
            if u.path == "/api/affordability":
                return self._send(200, engine.affordability())
            if u.path == "/api/styles":
                from tradebot import styles
                return self._send(200, styles.overview(store, engine))
            if u.path == "/api/health":
                from tradebot import health
                items = health.checks(store, engine, dict(auth=auth_on(), public=bool(PUBLIC_HOSTS), https=self._https(), exposed=BIND_HOST not in ("127.0.0.1", "localhost", "::1")))
                return self._send(200, dict(items=items, **health.summary(items)))
            if u.path == "/api/auth/status":
                return self._send(200, self.auth_status(ident))
            if u.path == "/api/auth/users":
                if ident[1] != "owner" or not auth_on():
                    return self._send(403, {"error": "owner only"})
                return self._send(200, dict(users=auth.users(), sessions=auth.session_count(ident[0])))
            if u.path == "/api/state":
                cfg = store.public_cfg()
                if ident[1] != "owner":
                    cfg["webhook_secret"] = ""
                return self._send(200, dict(me=dict(user=ident[0], role=ident[1], auth=auth_on()), engine=engine.snapshot(), cfg=cfg, events=store.events(40),
                                            strategies={k: v["label"] for k, v in STRATEGIES.items()},
                                            providers=llm.public_list(), groups=llm.GROUPS, defaults={k: v["grid"] for k, v in STRATEGIES.items()}))
            if u.path == "/api/spark":
                return self._send(200, {k: {"s": v.get("spark", []), "c": v.get("chg24", 0)} for k, v in engine.status.items()})
            if u.path == "/api/equity":
                return self._send(200, store.equity_curve())
            if u.path == "/api/trades":
                return self._send(200, store.trades(int(q.get("n", 500))))
            if u.path == "/api/candles":
                return self._send(200, candles_view(q["inst"], tf=q.get("tf")))
            if u.path == "/api/thoughts":
                return self._send(200, store.thoughts(int(q.get("n", 80)), int(q.get("after", 0))))
            if u.path == "/api/catalog":
                rows = catalog.search(q.get("q", ""), q.get("cat", "all"), int(q.get("n", 60)))
                have = {(i["market"], i["symbol"]): i for i in store.cfg["instruments"]}
                return self._send(200, dict(rows=[dict(r, added=(r["market"], r["symbol"]) in have, fav=bool(have.get((r["market"], r["symbol"]), {}).get("fav"))) for r in rows], cats=catalog.CATS))
            if u.path == "/api/job":
                j = jobs.get(q.get("id"), {"state": "error", "error": "unknown job"})
                return self._send(200, j)
            if u.path == "/api/ai/log":
                rows, tot = store.ai_log()
                vp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "ai_validation.json")
                val = None
                if os.path.exists(vp):
                    with open(vp, encoding="utf-8") as f:
                        val = json.load(f)
                b = store.cfg["ai"].get("budget", {})
                return self._send(200, dict(rows=rows, totals=tot, validation=val, score=store.scorecard(), spend=analyst.spend(store), budget=b, peak=analyst.is_peak()))
            if u.path == "/api/trades.csv":
                rows = store.trades(100000)
                cols = ["t_open", "t_close", "symbol", "side", "qty", "entry", "exit", "pnl", "reason", "source", "mode"]
                out = ",".join(cols) + "\n" + "\n".join(",".join(str(r[c]) for c in cols) for r in rows)
                return self._send(200, out.encode(), "text/csv")
            return self._static(u.path)
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})

    def _static(self, path):
        path = "/index.html" if path in ("", "/") else "/login.html" if path == "/login" else path
        full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
        if not (full == WEB or full.startswith(WEB + os.sep)) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        if "/vendor/" in path:
            self._cc = "public, max-age=604800, immutable"        # pinned third-party files never change: browser caches them
        elif getattr(self, "_versioned", False) and path.endswith((".js", ".css")):
            self._cc = "private, max-age=31536000, immutable"     # app files requested as name?v=<mtime>: the URL changes whenever the file does
        with open(full, "rb") as f:
            body = f.read()
        if full.endswith("index.html"):                              # stamp local assets with their mtime so an update can never serve stale JS/CSS
            import re

            def stamp(m):
                fp = os.path.join(WEB, m.group(2))
                return '%s="%s?v=%d"' % (m.group(1), m.group(2), int(os.path.getmtime(fp))) if os.path.isfile(fp) else m.group(0)
            body = re.sub(r'(src|href)="((?!vendor/|http|data:)[^"?]+\.(?:js|css))"', stamp, body.decode("utf-8")).encode("utf-8")
        self._send(200, body, mimetypes.guess_type(full)[0] or "application/octet-stream")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > 1_000_000:
            self.close_connection = True
            return self._send(413, {"error": "request too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if u.path == "/hook":
            code, out = engine.handle_signal(body)
            return self._send(code, out)
        ident = self._guard("POST", u.path)
        if ident is None:
            return
        try:
            if u.path.startswith("/api/auth/"):
                return self.auth_api(u.path, body, ident)
            if ident[1] == "viewer":
                return self._send(403, {"error": "view-only account"})
            self._send(*self.post_api(u.path, body))
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})

    def auth_status(self, ident):
        ui = store.cfg.get("ui", {})
        return dict(required=auth_on(), authed=bool(ident[0]) or not auth_on(), user=ident[0], role=ident[1], setup=auth_on() and not auth.has_users(),
                    need_code=bool(FORCE_AUTH or not self._local()), public=bool(PUBLIC_HOSTS), https=self._https(),
                    brand=ui.get("brand") or "Sentinel", accent=ui.get("accent") or "")

    def auth_api(self, path, b, ident):
        ip, ua = self._ip(), (self.headers.get("User-Agent") or "")
        try:
            if path == "/api/auth/login":
                if not auth_on():
                    return self._send(400, {"error": "login is not turned on"})
                who, (tok, life) = auth.login(b.get("username", ""), b.get("password", ""), ip, ua, bool(b.get("remember")))
                store.event("info", "%s signed in from %s" % (who, ip))
                return self._send(200, {"ok": True, "user": who}, cookie=(tok, life))
            if path == "/api/auth/setup":                       # create the first owner (and thereby turn login on)
                if auth.has_users():
                    return self._send(409, {"error": "an owner already exists"})
                if FORCE_AUTH or not self._local():
                    if not secrets_equal(str(b.get("code", "")).strip(), auth.setup_code()):
                        return self._send(403, {"error": "wrong setup code - it is printed in the server console and saved in data/setup_code.txt"})
                auth.create_user(b.get("username", ""), b.get("password", ""), "owner")
                store.put("auth_required", True)
                auth.clear_setup_code()
                who, (tok, life) = auth.login(b["username"], b["password"], ip, ua, True)
                store.event("info", "Login turned on; owner account '%s' created" % who)
                return self._send(200, {"ok": True, "user": who}, cookie=(tok, life))
            if path == "/api/auth/logout":
                auth.logout(self._token())
                return self._send(200, {"ok": True}, cookie=("", 0))
            if not auth_on() or not ident[0]:
                return self._send(400, {"error": "login is not turned on"})
            if path == "/api/auth/password":
                if not auth.verify(ident[0], b.get("old", "")):
                    return self._send(403, {"error": "current password is wrong"})
                auth.set_password(ident[0], b.get("new", ""))
                tok, life = auth._new_session(ident[0], ip, ua, True)          # this device stays signed in, every other one is signed out
                return self._send(200, {"ok": True}, cookie=(tok, life))
            if ident[1] != "owner":
                return self._send(403, {"error": "owner only"})
            if path == "/api/auth/sessions":
                auth.revoke_all(ident[0], keep_token=self._token())
                return self._send(200, {"ok": True})
            if path == "/api/auth/users":
                act, name = b.get("action"), b.get("username", "")
                if act == "add":
                    auth.create_user(name, b.get("password", ""), b.get("role", "viewer"))
                elif act == "remove":
                    if name == ident[0]:
                        return self._send(400, {"error": "you can't remove yourself"})
                    auth.delete_user(name)
                elif act == "reset":
                    auth.set_password(name, b.get("password", ""))
                else:
                    return self._send(400, {"error": "bad action"})
                return self._send(200, {"ok": True, "users": auth.users()})
            if path == "/api/auth/disable":                  # turn login off again (from the machine running Sentinel only)
                if FORCE_AUTH:
                    return self._send(400, {"error": "login is forced on by the server settings (SENTINEL_AUTH / SENTINEL_HOSTS)"})
                if not self._local():
                    return self._send(403, {"error": "turn login off from the computer running Sentinel"})
                if not auth.verify(ident[0], b.get("password", "")):
                    return self._send(403, {"error": "password is wrong"})
                store.put("auth_required", False)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "unknown endpoint"})
        except AuthError as e:
            return self._send(429 if "Too many" in str(e) else 400, {"error": str(e)})

    def post_api(self, path, b):
        if path == "/api/engine":
            a = b.get("action")
            if a == "start":
                if engine.halted():
                    return 409, {"error": "trading is halted - press Resume first"}
                engine.start()
            elif a == "stop":
                engine.stop()
            elif a == "kill":
                engine.kill()
            elif a == "resume":
                engine.resume()
            elif a == "flatten":
                engine.flatten_all("manual flatten")
            elif a == "resize":
                return 200, {"ok": True, "resized": engine.resize_to_risk()}
            elif a == "close":
                engine.close_by_id(b["inst"], "manual")
            else:
                return 400, {"error": "bad action"}
            return 200, {"ok": True}
        if path == "/api/config":
            new = json.loads(json.dumps(b))
            for sec, k in SECRETS:
                if sec in new and not new[sec].get(k):
                    new[sec].pop(k, None)
            if new.get("mode") == "live" and store.cfg["mode"] != "live":
                if b.get("confirm_live") != "I UNDERSTAND":
                    return 400, {"error": "type I UNDERSTAND to enable live trading"}
            new.pop("confirm_live", None)
            if "mode" in new or "broker" in new:
                engine._broker = None
            for inst in new.get("instruments", []):
                inst["id"] = "%s:%s:%s" % (inst["market"], inst["symbol"], inst["tf"])
            old_risk = store.cfg["risk"]["per_trade_pct"]
            store.save_cfg(new)
            if "risk" in new and new["risk"].get("per_trade_pct", old_risk) < old_risk:
                engine.resize_to_risk()                                       # lowering the risk also trims trades that are now too big
            return 200, {"ok": True}
        if path == "/api/account":
            print("[account] amount=%s from %s ua=%s referer=%s" % (b.get("amount"), self.client_address, (self.headers.get("User-Agent") or "")[:40], self.headers.get("Referer")), flush=True)
            try:
                engine.set_account(b["amount"])
            except (ValueError, KeyError) as e:
                return 400, {"error": str(e)}
            return 200, {"ok": True}
        if path == "/api/chat":
            return 200, chat.ask(store, engine, b.get("messages", []))
        if path == "/api/speed":
            return speed_action(b)
        if path == "/api/preset":
            return preset_action(b)
        if path == "/api/style":
            return style_action(b)
        if path == "/api/watchlist":
            return watchlist_action(b)
        if path == "/api/reset":
            if b.get("confirm") != "RESET":
                return 400, {"error": "type RESET"}
            engine.stop()
            store.reset_paper()
            engine.status.clear()
            return 200, {"ok": True}
        if path == "/api/backtest":
            jid = "j%d" % int(time.time() * 1000)
            jobs[jid] = {"state": "running", "progress": "starting"}
            for old in list(jobs)[:-20]:
                jobs.pop(old)
            threading.Thread(target=run_job, args=(b["kind"], dict(b, job=jid)), daemon=True).start()
            return 200, {"job": jid}
        if path == "/api/notify/test":
            from tradebot import notify
            cfg = dict(store.cfg["notify"], **{k: v for k, v in (b.get("notify") or {}).items() if v})
            ok, err = notify.send(cfg, "Sentinel test", "If you can read this, alerts are working.", wait=True)
            return 200, {"ok": ok, "error": err}
        if path == "/api/broker/test":
            try:
                br = engine.broker()
                if not br:
                    return 200, {"ok": False, "error": "no API keys saved for the selected exchange"}
                return 200, {"ok": True, "equity": br.equity(), "exchange": store.cfg["broker"]["exchange"]}
            except Exception as e:
                return 200, {"ok": False, "error": str(e)[:200]}
        if path == "/api/ai/models":
            cfg = _ai_cfg(b)
            return 200, llm.list_models(cfg)
        if path == "/api/ai/test":
            cfg = _ai_cfg(b)
            t = time.time()
            try:
                text, ti, to = ai.ask(cfg, 'Reply exactly {"take":true,"why":"ok"}')
                return 200, {"ok": True, "reply": text.strip()[:200], "ms": int((time.time() - t) * 1000), "tokens": ti + to}
            except Exception as e:
                return 200, {"ok": False, "error": str(e)[:240]}
        if path == "/api/ai/brief":
            snap = engine.snapshot()
            stats = {k: (round(snap[k], 2) if isinstance(snap[k], float) else snap[k]) for k in
                     ("equity", "total_return_pct", "day_pnl_pct", "drawdown_pct", "trades", "win_rate", "profit_factor") if snap.get(k) is not None}
            stats["open"] = [(p["symbol"], p["side"], round(p["upnl"], 2)) for p in snap["positions"]]
            stats["recent"] = [(t["symbol"], t["side"], round(t["pnl"], 2), t["reason"]) for t in store.trades(10)]
            try:
                return 200, {"ok": True, "text": ai.brief(store, stats)}
            except Exception as e:
                return 200, {"ok": False, "error": str(e)[:200]}
        return 404, {"error": "unknown endpoint"}


def main():
    port = int(os.environ.get("PORT", "8765"))
    host = BIND_HOST
    ThreadingHTTPServer.allow_reuse_address = os.name != "nt"          # on Windows SO_REUSEADDR lets two copies share a port, so one would serve a stale page
    try:
        srv = ThreadingHTTPServer((host, port), H)
    except OSError:
        print("Sentinel is already running on port %d (or the port is busy). Close the other window first, then try again." % port)
        raise SystemExit(1)
    cli_token()                                          # make sure data/cli_token.txt exists for `sentinel.py` on this machine
    on = store.get("engine_on")
    if (on is None or on) and not engine.halted() and not os.environ.get("SENTINEL_NO_ENGINE"):   # autonomous: resumes by itself after a restart (paper by default); only after the port is ours so a second copy can never run a second engine
        engine.start()
    print("Dashboard: http://localhost:%d   (webhook: POST /hook)" % port, flush=True)
    if auth_on() and not auth.has_users():
        print("First-time setup: open the site, choose 'Create owner account' and enter this one-time code: %s" % auth.setup_code(), flush=True)
    if host not in ("127.0.0.1", "localhost", "::1") and not auth_on():
        print("WARNING: listening on %s without login. Set SENTINEL_AUTH=1 (or SENTINEL_HOSTS=your.domain) before exposing this." % host, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
