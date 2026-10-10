"""Dashboard + API server (standard library only). Run: py server.py   ->  http://localhost:8765
Everything except POST /hook is restricted to local requests, so exposing the port through a tunnel for TradingView
webhooks does not expose the dashboard."""
import json, mimetypes, os, sys, threading, time, traceback, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tradebot import data, backtest as B, ai, analyst, indicators as I
from tradebot.engine import Engine
from tradebot.store import Store
from tradebot.strategies import STRATEGIES

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
store = Store()
store.save_cfg()
engine = Engine(store)
jobs = {}
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
                m = B.run(c, inst["strategy"], inst["params"], inst["market"], inst["tf"], risk=risk)
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
                r = B.optimize(c, strat, market, tf, risk=risk)
                m, oos = r["full"], r["oos"]
                j["result"] = dict(kind=kind, params=r["params"], insample=clean(r["insample"]), oos=clean(oos), metrics=clean(m),
                                   curve=downsample([x[0] for x in c], m["curve"]), buyhold=downsample([x[0] for x in c], bh),
                                   trades=[list(t) for t in m["trade_list"][-80:]], verdict=verdict(oos), split_ts=c[int(len(c) * 0.6)][0])
            else:
                m = B.run(c, strat, req["params"], market, tf, risk=risk)
                k = int(len(c) * 0.6)
                oos = B.run(c[k - 300:], strat, req["params"], market, tf, risk=risk)
                j["result"] = dict(kind=kind, params=req["params"], metrics=clean(m), oos=clean(oos), curve=downsample([x[0] for x in c], m["curve"]),
                                   buyhold=downsample([x[0] for x in c], bh), trades=[list(t) for t in m["trade_list"][-80:]], verdict=verdict(oos))
        j["state"] = "done"
    except Exception as e:
        j["state"] = "error"
        j["error"] = str(e)[:300]
        traceback.print_exc()


def candles_view(inst_id, n=300):
    inst = next((i for i in store.cfg["instruments"] if i["id"] == inst_id), None)
    if not inst:
        return {"error": "unknown instrument"}
    key = (inst_id, int(time.time() // 10))
    if key not in _candle_cache:
        _candle_cache.clear()
        _candle_cache[key] = data.fetch_recent(inst["market"], inst["symbol"], inst["tf"], n + 250, forming=True)
    allb = _candle_cache[key]
    tfms = data.TF_MS[inst["tf"]]
    forming = allb[-1][0] + tfms > int(time.time() * 1000)
    hist = allb[:-1] if forming else allb                      # indicators only ever use CLOSED bars
    cl, h, l = [x[4] for x in hist], [x[2] for x in hist], [x[3] for x in hist]
    p = inst["params"]
    overlays = {}
    if inst["strategy"] == "donchian":
        overlays["upper"], overlays["lower"] = I.highest(h, p["n"]), I.lowest(l, p["n"])
        if p.get("trend"):
            overlays["trend"] = I.ema(cl, p["trend"])
    elif inst["strategy"] == "emacross":
        overlays["fast"], overlays["slow"] = I.ema(cl, p["fast"]), I.ema(cl, p["slow"])
    else:
        overlays["trend"] = I.ema(cl, p["trend"])
    off = len(hist) - n
    trades = [t for t in store.trades(500) if t["inst"] == inst_id]
    return dict(inst=inst, candles=allb[-(n + (1 if forming else 0)):], forming=forming, overlays={k: v[max(0, off):] for k, v in overlays.items()},
                trades=[dict(t_open=t["t_open"], t_close=t["t_close"], side=t["side"], entry=t["entry"], exit=t["exit"], pnl=t["pnl"]) for t in trades],
                position=store.positions().get(inst_id))


# ---------------------------------------------------------------- HTTP
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _local(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0] if not (self.headers.get("Host") or "").startswith("[") else "[::1]"
        return host in LOCAL_HOSTS and not self.headers.get("X-Forwarded-For") and self.client_address[0] in ("127.0.0.1", "::1")

    def _send(self, code, obj, ctype="application/json"):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", getattr(self, "_cc", "no-store"))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if not self._local():
            return self._send(403, {"error": "local only"})
        try:
            if u.path == "/api/state":
                return self._send(200, dict(engine=engine.snapshot(), cfg=store.public_cfg(), events=store.events(40),
                                            strategies={k: v["label"] for k, v in STRATEGIES.items()},
                                            presets={k: list(v) for k, v in ai.PRESETS.items()}, defaults={k: v["grid"] for k, v in STRATEGIES.items()}))
            if u.path == "/api/spark":
                return self._send(200, {k: {"s": v.get("spark", []), "c": v.get("chg24", 0)} for k, v in engine.status.items()})
            if u.path == "/api/equity":
                return self._send(200, store.equity_curve())
            if u.path == "/api/trades":
                return self._send(200, store.trades(int(q.get("n", 500))))
            if u.path == "/api/candles":
                return self._send(200, candles_view(q["inst"]))
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
        path = "/index.html" if path in ("", "/") else path
        full = os.path.normpath(os.path.join(WEB, path.lstrip("/")))
        if not full.startswith(WEB) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        if "/vendor/" in path:
            self._cc = "public, max-age=604800, immutable"        # pinned third-party files never change: browser caches them
        with open(full, "rb") as f:
            self._send(200, f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        if u.path == "/hook":
            code, out = engine.handle_signal(body)
            return self._send(code, out)
        if not self._local():
            return self._send(403, {"error": "local only"})
        try:
            self._send(*self.post_api(u.path, body))
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})

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
            elif a == "close":
                engine.close_by_id(b["inst"], "manual")
            else:
                return 400, {"error": "bad action"}
            return 200, {"ok": True}
        if path == "/api/config":
            new = json.loads(json.dumps(b))
            for sec, k in (("ai", "api_key"), ("broker", "binance_key"), ("broker", "binance_secret"), ("broker", "bybit_key"), ("broker", "bybit_secret")):
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
            store.save_cfg(new)
            return 200, {"ok": True}
        if path == "/api/account":
            print("[account] amount=%s from %s ua=%s referer=%s" % (b.get("amount"), self.client_address, (self.headers.get("User-Agent") or "")[:40], self.headers.get("Referer")), flush=True)
            try:
                engine.set_account(b["amount"])
            except (ValueError, KeyError) as e:
                return 400, {"error": str(e)}
            return 200, {"ok": True}
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
        if path == "/api/broker/test":
            try:
                br = engine.broker()
                if not br:
                    return 200, {"ok": False, "error": "no API keys saved for the selected exchange"}
                return 200, {"ok": True, "equity": br.equity(), "exchange": store.cfg["broker"]["exchange"]}
            except Exception as e:
                return 200, {"ok": False, "error": str(e)[:200]}
        if path == "/api/ai/test":
            cfg = dict(store.cfg["ai"], **{k: v for k, v in (b or {}).items() if v})
            t = time.time()
            try:
                text, ti, to = ai.ask(cfg, 'Reply exactly {"take":true,"why":"ok"}')
                return 200, {"ok": True, "reply": text.strip()[:200], "ms": int((time.time() - t) * 1000), "tokens": ti + to}
            except Exception as e:
                return 200, {"ok": False, "error": str(e)[:200]}
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
    on = store.get("engine_on")
    if (on is None or on) and not engine.halted():   # autonomous: resumes by itself after a restart (paper by default)
        engine.start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print("Dashboard: http://localhost:%d   (webhook: POST /hook)" % port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
