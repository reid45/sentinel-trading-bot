"""Run: py tests.py   (uses a temp data dir; hits the network only for the live-data smoke test)"""
import json, os, sys, tempfile, threading, time, unittest, urllib.request, urllib.error

TMP = tempfile.mkdtemp()
os.environ["TRADEBOT_DATA"] = TMP
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tradebot import data, indicators as I, backtest as B, ai
from tradebot.store import Store, DEFAULTS
from tradebot.engine import Engine
from tradebot.brokers import BinanceFutures

BTC_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "futures_BTCUSDT_4h.csv")


def mk_store(**over):
    s = Store(path=os.path.join(tempfile.mkdtemp(), "t.db"), cfg_path=os.path.join(tempfile.mkdtemp(), "c.json"))
    s.save_cfg(over)
    return s


def synth(n=1500, seed=3, drift=0.0004, vol=0.012):
    import random
    r = random.Random(seed)
    p, out, t = 100.0, [], 1_600_000_000_000
    for i in range(n):
        o = p
        p = p * (1 + drift + r.gauss(0, vol))
        out.append((t + i * 14400000, o, max(o, p) * 1.003, min(o, p) * 0.997, p, 1.0))
    return out


class Indicators(unittest.TestCase):
    def test_ema_rsi_atr(self):
        self.assertAlmostEqual(I.ema([1, 2, 3, 4, 5], 3)[-1], 4.0625, 3)
        up = I.rsi([float(i) for i in range(1, 40)], 14)[-1]
        self.assertEqual(up, 100.0)
        c = [10 + (i % 3) for i in range(60)]
        a = I.atr([x + 1 for x in c], [x - 1 for x in c], c, 14)[-1]
        self.assertTrue(2.0 <= a <= 4.1)
        self.assertEqual(I.highest([1, 5, 2, 3], 2)[3], 5)


class Backtest(unittest.TestCase):
    def test_trend_makes_money_noise_does_not(self):
        p = dict(n=40, trend=0, stop=4.0, trail=1, tp=0, reverse=1)
        up = B.run(synth(2000, drift=0.002, vol=0.008), "donchian", p, "futures", "4h")
        self.assertGreater(up["ret"], 0)
        self.assertGreater(up["trades"], 3)

    def test_costs_hurt(self):
        c = synth(1500, seed=9)
        p = dict(n=20, trend=0, stop=3.0, trail=1, tp=0, reverse=1)
        free = dict(B.MARKET_COSTS["futures"])
        a = B.run(c, "donchian", p, "futures", "4h")
        B.MARKET_COSTS["futures"].update(fee=0, slip=0, funding_8h=0)
        b = B.run(c, "donchian", p, "futures", "4h")
        B.MARKET_COSTS["futures"].update(free)
        self.assertGreaterEqual(b["end"], a["end"])

    def test_stop_loss_bounds_loss(self):
        # one entry then straight crash: loss must be about the risk budget (1%), not more (gap excepted)
        c = [(i * 14400000, 100, 100.5, 99.5, 100, 1) for i in range(60)]
        c += [(60 * 14400000, 100, 103, 100, 103, 1)]                       # breakout bar
        c += [(61 * 14400000, 103, 103.2, 102.9, 103, 1)]                    # entry bar
        c += [((62 + i) * 14400000, 100, 100.2, 99, 99.5, 1) for i in range(5)]  # crash through stop
        m = B.run(c, "donchian", dict(n=20, trend=0, stop=2.0, trail=0, tp=0, reverse=0), "futures", "4h")
        self.assertEqual(m["trades"], 1)
        self.assertGreater(m["trade_list"][0][5], -10000 * 0.03)


class EngineReplay(unittest.TestCase):
    """The live engine, fed bars one at a time, must reproduce what the backtester does on the same bars."""

    def test_matches_backtest(self):
        if not os.path.exists(BTC_CACHE):
            data.fetch("futures", "BTCUSDT", "4h", years=5)
        allc = data.load_csv(BTC_CACHE)[-2600:]
        start = 800
        inst = dict(DEFAULTS["instruments"][0])
        s = mk_store(instruments=[inst], risk={"max_positions": 10, "daily_loss_limit_pct": 99, "max_drawdown_halt_pct": 99})
        k = {"i": start}
        eng = Engine(s, fetcher=lambda m, sym, tf, n: allc[:k["i"] + 1][-500:], pricer=lambda m, sym: allc[k["i"]][4])
        st = {"last_ts": None, "price": None, "signal": 0, "error": None, "price_t": 0}
        eng.status[inst["id"]] = st
        eng._process(inst, st)  # baseline
        for k["i"] in range(start + 1, len(allc)):
            eng._process(inst, st)
        eng_trades = s.trades(10000)
        ref = B.run(allc[start - 250:], inst["strategy"], inst["params"], "futures", "4h", risk=s.cfg["risk"]["per_trade_pct"] / 100)
        n_e, n_b = len(eng_trades) + len(s.positions()), ref["trades"]
        print("  engine trades %d vs backtest %d, engine pnl %+.1f vs backtest %+.1f" % (n_e, n_b, eng.equity() - 10000, ref["end"] - 10000))
        self.assertLessEqual(abs(n_e - n_b), max(3, n_b * 0.15))
        self.assertAlmostEqual((eng.equity() - 10000) / 10000, ref["ret"], delta=max(0.04, abs(ref["ret"]) * 0.5))


class Risk(unittest.TestCase):
    def setUp(self):
        self.s = mk_store(instruments=[dict(DEFAULTS["instruments"][i]) for i in range(3)])
        self.e = Engine(self.s, pricer=lambda m, sym: 100.0)
        self.inst = self.s.cfg["instruments"][0]

    def test_sizing_and_limits(self):
        ok, msg = self.e.open(self.inst, 1, 100.0, 2.0)
        self.assertTrue(ok, msg)
        p = self.s.positions()[self.inst["id"]]
        self.assertLessEqual(p["qty"] * p["d"], 10000 * 0.005 * 1.001)           # risk <= 0.5% of equity
        self.assertFalse(self.e.open(self.inst, 1, 100.0, 2.0)[0])               # no stacking
        self.s.cfg["risk"]["max_positions"] = 1
        self.assertFalse(self.e.open(self.s.cfg["instruments"][1], 1, 100.0, 2.0)[0])

    def test_leverage_cap(self):
        self.e.open(self.inst, 1, 100.0, 0.01)                                    # tiny stop would imply huge size
        p = self.s.positions()[self.inst["id"]]
        self.assertLessEqual(p["qty"] * 100.0, 10000 * 3.0 * 1.001)

    def test_kill_switch_flattens_and_halts(self):
        self.e.open(self.inst, 1, 100.0, 2.0)
        self.e.kill()
        self.assertEqual(self.s.positions(), {})
        self.assertTrue(self.e.halted())
        self.assertFalse(self.e.open(self.inst, 1, 100.0, 2.0)[0])
        self.e.resume()
        self.assertTrue(self.e.open(self.inst, 1, 100.0, 2.0)[0])

    def test_daily_loss_and_drawdown_guards(self):
        self.e.cycle = lambda: None
        self.e._risk_guards()
        self.s.put("cash", 9600.0)                                                # -4% on the day (limit 3%)
        self.e._risk_guards()
        self.assertTrue(self.s.get("daily_block"))
        self.assertFalse(self.e.open(self.inst, 1, 100.0, 2.0)[0])
        self.s.put("cash", 7900.0)                                                # -21% from peak (halt 20%)
        self.e._risk_guards()
        self.assertTrue(self.e.halted())

    def test_pnl_accounting(self):
        self.e.open(self.inst, 1, 100.0, 2.0)
        self.e.status[self.inst["id"]] = {"price": 104.0}
        self.e.close_by_id(self.inst["id"])
        t = self.s.trades()[0]
        self.assertGreater(t["pnl"], 0)
        self.assertAlmostEqual(self.e.equity() - 10000, t["pnl"], places=2)


class Accounting(unittest.TestCase):
    """Every number on the dashboard must reconcile: start + sum(closed P&L) == equity once flat."""

    def test_books_reconcile(self):
        s = mk_store(instruments=[dict(DEFAULTS["instruments"][i]) for i in range(3)])
        e = Engine(s, pricer=lambda m, sym: 100.0)
        e._lots = {i["symbol"]: (0.001, 0.001) for i in s.cfg["instruments"]}
        prices = [(104.0, 1), (97.5, -1), (101.0, 1)]
        for inst, (exit_px, side) in zip(s.cfg["instruments"], prices):
            self.assertTrue(e.open(inst, side, 100.0, 3.0)[0])
        eq_open = e.equity()
        self.assertLess(eq_open, 10000)                                        # fees already felt while open
        for inst, (exit_px, side) in zip(s.cfg["instruments"], prices):
            e.status[inst["id"]] = {"price": exit_px}
            e.close_by_id(inst["id"])
        ts = s.trades()
        self.assertEqual(len(ts), 3)
        self.assertAlmostEqual(e.equity(), 10000 + sum(t["pnl"] for t in ts), places=6)
        snap = e.snapshot()
        self.assertEqual(snap["trades"], 3)
        self.assertAlmostEqual(snap["win_rate"], sum(1 for t in ts if t["pnl"] > 0) / 3 * 100)
        wins, losses = sum(t["pnl"] for t in ts if t["pnl"] > 0), -sum(t["pnl"] for t in ts if t["pnl"] <= 0)
        self.assertAlmostEqual(snap["profit_factor"], wins / losses, places=6) if losses > 0 else None
        self.assertAlmostEqual(snap["equity"], snap["cash"], places=9)          # flat: equity == cash

    def test_open_position_equity_equals_close_now(self):
        s = mk_store(instruments=[dict(DEFAULTS["instruments"][0])])
        e = Engine(s, pricer=lambda m, sym: 100.0)
        e._lots = {"BTCUSDT": (0.001, 0.001)}
        inst = s.cfg["instruments"][0]
        e.open(inst, 1, 100.0, 3.0)
        pos = s.positions()[inst["id"]]
        pos["mark"] = 106.0
        s.set_position(inst["id"], pos)
        before = e.equity()
        e.status[inst["id"]] = {"price": 106.0}
        e.close_by_id(inst["id"])
        self.assertAlmostEqual(e.equity(), before, places=6)                    # shown equity == realized equity

    def test_custom_account_and_small_account_rules(self):
        s = mk_store(instruments=[dict(DEFAULTS["instruments"][0])])
        e = Engine(s, pricer=lambda m, sym: 100.0)
        e._lots = {"BTCUSDT": (0.001, 0.001)}
        e.set_account(500)
        self.assertEqual(e.equity(), 500)
        s.event("info", "marker")
        e.set_account(500)                                                      # same balance, untouched account -> no wipe
        self.assertTrue(any(v["msg"] == "marker" for v in s.events()))
        self.assertEqual(s.cfg["start_equity"], 500)
        for bad in (5, -1, 1e9):
            with self.assertRaises(ValueError):
                e.set_account(bad)
        inst = s.cfg["instruments"][0]
        ok, msg = e.open(inst, 1, 80000.0, 3000.0)                              # needs 0.00083 BTC < 0.001 minimum
        self.assertFalse(ok)
        self.assertIn("too small", msg)
        self.assertTrue(e.open(inst, 1, 100.0, 3.0)[0])                         # a cheaper setup fits
        qty = list(s.positions().values())[0]["qty"]
        self.assertAlmostEqual(qty / 0.001, round(qty / 0.001), places=6)       # obeys the exchange step
        s.cfg["mode"] = "live"
        with self.assertRaises(ValueError):
            e.set_account(1000)                                                 # balance belongs to the exchange there


class Webhook(unittest.TestCase):
    def test_hook(self):
        s = mk_store(instruments=[dict(DEFAULTS["instruments"][0])])
        e = Engine(s, pricer=lambda m, sym: 100.0)
        sec = s.cfg["webhook_secret"]
        self.assertTrue(sec)
        self.assertEqual(e.handle_signal({"secret": "no", "action": "buy", "symbol": "BTCUSDT"})[0], 403)
        self.assertEqual(e.handle_signal({"secret": sec, "action": "hodl", "symbol": "BTCUSDT"})[0], 400)
        self.assertEqual(e.handle_signal({"secret": sec, "action": "buy", "symbol": "NOPE", "stop": 1})[0], 404)
        self.assertEqual(e.handle_signal({"secret": sec, "action": "buy", "symbol": "BTCUSDT.P", "price": 100, "stop": 105})[0], 400)
        c, o = e.handle_signal({"secret": sec, "action": "buy", "symbol": "BTCUSDT.P", "price": 100, "stop": 95})
        self.assertTrue(c == 200 and o["ok"], o)
        self.assertEqual(e.handle_signal({"secret": sec, "action": "close", "symbol": "BTCUSDT"})[1]["closed"], True)


class MockLLM:
    """Fake OpenAI-compatible endpoint. NO test ever touches a real AI API."""

    def __init__(self, script=None):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        outer = self
        self.reqs, self.script = [], list(script or [])
        self.default = {"choices": [{"message": {"role": "assistant", "content": '{"p":72,"flag":"none","why":"clean trend"}'}}],
                        "usage": {"prompt_cache_hit_tokens": 300, "prompt_cache_miss_tokens": 120, "completion_tokens": 20}}

        class Hd(BaseHTTPRequestHandler):
            def log_message(s, *a): pass

            def do_POST(s):
                outer.reqs.append(json.loads(s.rfile.read(int(s.headers["Content-Length"]))))
                r = outer.script.pop(0) if outer.script else outer.default
                if r == "500":
                    s.send_response(500); s.send_header("Content-Length", "0"); s.end_headers(); return
                b = json.dumps(r).encode()
                s.send_response(200); s.send_header("Content-Length", str(len(b))); s.end_headers(); s.wfile.write(b)
        self.httpd = HTTPServer(("127.0.0.1", 0), Hd)
        self.url = "http://127.0.0.1:%d/deepseek" % self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


class Analyst(unittest.TestCase):
    def setUp(self):
        from tradebot import analyst
        self.A = analyst
        analyst.reset_runtime()
        analyst.derivs = lambda sym: dict(funding=0.01, funding_avg8=0.008, funding_max8=0.02, oi_24h=3.1, ls=1.8, basis=0.02)
        self.llm = MockLLM()
        self.inst = dict(DEFAULTS["instruments"][0])

    def store(self, **ai):
        return mk_store(ai=dict({"mode": "advise", "provider": "custom", "base_url": self.llm.url, "api_key": "k", "model": "deepseek-flash"}, **ai))

    def run_one(self, s, n=400, side=1):
        return self.A.assess(s, self.inst, side, synth(n), None, {}, 1000.0, lambda *a: synth(60))

    def test_one_cheap_call_cache_friendly_prefix(self):
        s = self.store()
        r1, r2 = self.run_one(s, 400), self.run_one(s, 401)
        self.assertEqual((r1["p"], r1["take"], r1["flag"]), (72, True, "none"))
        self.assertEqual(len(self.llm.reqs), 2)
        q1, q2 = self.llm.reqs
        self.assertEqual(q1["messages"][0], q2["messages"][0])                        # static system prompt: byte-identical -> cache hit
        self.assertEqual(q1["tools"], q2["tools"])                                    # static tools: byte-identical
        self.assertEqual(q1["thinking"], {"type": "disabled"})                        # no hidden reasoning tokens
        self.assertLessEqual(q1["max_tokens"], 140)
        self.assertEqual(q1["messages"][-1]["role"], "user")                          # variable dossier LAST
        self.assertIn("funding", q1["messages"][-1]["content"])                       # real extra data is in the prompt up front
        self.assertLess(len(q1["messages"][-1]["content"]), 700)                      # compact dossier
        exp = self.A.cost_usd("deepseek-flash", 300, 120, 20) * 2
        self.assertAlmostEqual(self.A.spend(s)["usd_day"], exp, places=9)
        self.run_one(s, 400)                                                          # same market+bar+side: memo, no second bill
        self.assertEqual(len(self.llm.reqs), 2)

    def test_tool_round_trip(self):
        tc = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "journal", "arguments": '{"symbol":"BTCUSDT"}'}}]}}], "usage": {"prompt_cache_miss_tokens": 200, "completion_tokens": 15}}
        self.llm.script = [tc]
        r = self.run_one(self.store())
        self.assertEqual(len(self.llm.reqs), 2)
        tool_msg = self.llm.reqs[1]["messages"][-1]
        self.assertEqual((tool_msg["role"], tool_msg["tool_call_id"]), ("tool", "c1"))
        self.assertIn("no closed trades", tool_msg["content"])
        self.assertEqual(r["p"], 72)

    def test_tool_rounds_are_capped(self):
        tc = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c", "type": "function", "function": {"name": "journal", "arguments": '{"symbol":"BTCUSDT"}'}}]}}], "usage": {}}
        self.llm.script = [tc, tc, tc, tc, tc]
        r = self.run_one(self.store(max_tool_rounds=2))
        self.assertLessEqual(len(self.llm.reqs), 3)                                   # 2 tool rounds + 1 forced final answer
        self.assertNotIn("tools", self.llm.reqs[-1])
        self.assertEqual(r["source"], "error")                                        # never an infinite loop; fails open
        self.assertTrue(r["take"])

    def test_budget_hard_stop(self):
        s = self.store(budget={"max_calls_day": 1, "max_usd_day": 1.0, "max_usd_total": 1.0})
        self.assertEqual(self.run_one(s, 400)["source"], "api")
        r = self.run_one(s, 401)
        self.assertEqual(r["source"], "budget")
        self.assertTrue(r["take"])
        self.assertEqual(len(self.llm.reqs), 1)                                       # the second signal cost nothing
        s2 = self.store(budget={"max_calls_day": 99, "max_usd_day": 0.0000001, "max_usd_total": 1.0})
        self.run_one(s2, 402)
        self.assertEqual(self.run_one(s2, 403)["source"], "budget")                   # dollar cap works too

    def test_circuit_breaker_and_fail_open(self):
        self.llm.script = ["500", "500", "500"]
        s = self.store()
        for n in (400, 401, 402):
            r = self.run_one(s, n)
            self.assertTrue(r["take"])
        self.assertEqual(len(self.llm.reqs), 3)
        self.assertEqual(self.run_one(s, 403)["source"], "breaker")                  # stops hammering a failing API
        self.assertEqual(len(self.llm.reqs), 3)

    def test_veto_vs_advise_and_garbage(self):
        self.llm.default = dict(self.llm.default, choices=[{"message": {"content": '{"p":30,"flag":"chop","why":"low adx"}'}}])
        self.assertTrue(self.run_one(self.store(mode="advise"), 400)["take"])        # advise never blocks
        self.assertFalse(self.run_one(self.store(mode="veto", min_p=50), 401)["take"])
        self.llm.default = dict(self.llm.default, choices=[{"message": {"content": "sure thing!"}}])
        r = self.run_one(self.store(mode="veto", min_p=50), 402)
        self.assertTrue(r["take"])                                                     # unreadable reply is NOT a decision
        self.assertEqual(r["source"], "error")

    def test_no_key_and_off_cost_nothing(self):
        self.assertEqual(self.run_one(self.store(api_key=""))["source"], "nokey")
        self.assertEqual(self.run_one(self.store(mode="off"))["note"], "AI off")
        self.assertEqual(len(self.llm.reqs), 0)

    def test_scorecard_links_ratings_to_outcomes(self):
        s = self.store()
        e = Engine(s, pricer=lambda m, sym: 100.0)
        e._lots = {"BTCUSDT": (0.001, 0.001)}
        for p_, exit_px in ((72, 106.0), (30, 97.0)):
            inst = s.cfg["instruments"][0]
            ok, msg = e.open(inst, 1, 100.0, 3.0, hist=synth(400), source="engine")
            self.assertTrue(ok, msg)
            pos = s.positions()[inst["id"]]; pos["ai_p"] = p_; s.set_position(inst["id"], pos)
            e.status[inst["id"]] = {"price": exit_px}
            e.close_by_id(inst["id"])
            self.A.reset_runtime()
        sc = s.scorecard()
        self.assertEqual(sc["n"], 2)
        hi = next(r for r in sc["rows"] if r["label"].startswith("Rated high"))
        lo = next(r for r in sc["rows"] if r["label"].startswith("Rated low"))
        self.assertGreater(hi["avg"], 0)
        self.assertLess(lo["avg"], 0)


class Misc(unittest.TestCase):
    def test_binance_signature_matches_docs(self):
        b = BinanceFutures("k", "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j")
        q = b.sign({"symbol": "LTCBTC", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": 1, "price": 0.1,
                    "recvWindow": 5000, "timestamp": 1499827319559})
        self.assertTrue(q.endswith("c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"))

    def test_context_and_gate(self):
        from tradebot import context
        f = context.features(synth(400), 1)
        self.assertIn("ext", context.render("BTCUSDT", 1, f))
        c = synth(2000, drift=0.002, vol=0.008)                      # the backtester's entry gate really blocks entries
        pp = dict(n=40, trend=0, stop=4.0, trail=1, tp=0, reverse=1)
        self.assertEqual(B.run(c, "donchian", pp, "futures", "4h", gate=lambda j, sd: False)["trades"], 0)
        self.assertGreater(B.run(c, "donchian", pp, "futures", "4h", gate=lambda j, sd: True)["trades"], 3)

    def test_live_data_smoke(self):
        c = data.fetch_recent("futures", "BTCUSDT", "4h", 300)
        self.assertGreater(len(c), 250)
        self.assertTrue(all(c[i][0] < c[i + 1][0] for i in range(len(c) - 1)))


class ServerApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ensure_server()

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request("http://127.0.0.1:18765" + path, data=json.dumps(body).encode() if body is not None else None, headers=headers or {})
        try:
            r = urllib.request.urlopen(req, timeout=60)
            return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_endpoints(self):
        c, b = self.call("/api/state")
        self.assertEqual(c, 200)
        st = json.loads(b)
        self.assertEqual(self.call("/api/config", {"ai": {"api_key": "SUPERSECRET123"}, "broker": {"binance_secret": "BSECRET456"}})[0], 200)
        c, b = self.call("/api/state")
        self.assertNotIn(b"SUPERSECRET123", b)                                                   # secrets never leave the server
        self.assertNotIn(b"BSECRET456", b)
        self.assertTrue(json.loads(b)["cfg"]["ai"]["has_api_key"])
        self.assertEqual(self.call("/")[0], 200)
        self.assertEqual(self.call("/api/state", headers={"X-Forwarded-For": "1.2.3.4"})[0], 403)  # tunnel traffic can't reach the dashboard
        self.assertEqual(self.call("/hook", {"secret": "bad"})[0], 403)                            # but the webhook is reachable
        self.assertEqual(self.call("/api/config", {"mode": "live"})[0], 400)                      # live needs explicit confirmation
        self.assertEqual(self.call("/../server.py")[0], 404)                                       # no path traversal
        self.assertEqual(self.call("/api/engine", {"action": "nonsense"})[0], 400)

    def test_static_assets_and_new_endpoints(self):
        import urllib.request as ur
        r = ur.urlopen("http://127.0.0.1:18765/vendor/lwc.js")
        self.assertEqual(r.status, 200)
        self.assertIn("immutable", r.headers["Cache-Control"])                       # pinned libs are browser-cached: fewer requests
        self.assertEqual(ur.urlopen("http://127.0.0.1:18765/app.js").headers["Cache-Control"], "no-store")
        self.assertEqual(ur.urlopen("http://127.0.0.1:18765/vendor/fonts/inter.woff2").headers["Content-Type"], "font/woff2")
        self.assertEqual(self.call("/api/spark")[0], 200)
        c, b = self.call("/api/ai/log")
        d = json.loads(b)
        self.assertEqual(c, 200)
        for k in ("spend", "budget", "score", "validation"):
            self.assertIn(k, d)
        self.assertLessEqual(d["spend"]["usd"], d["budget"]["max_usd_total"])
        st = json.loads(self.call("/api/state")[1])
        self.assertTrue(all("spark" not in v for v in st["engine"]["status"].values()))   # hot payload stays small

    def test_backtest_job(self):
        c, b = self.call("/api/backtest", {"kind": "single", "market": "futures", "symbol": "BTCUSDT", "tf": "4h", "strategy": "donchian",
                                          "params": dict(n=40, trend=0, stop=4.0, trail=1, tp=0, reverse=1), "years": 3})
        jid = json.loads(b)["job"]
        for _ in range(120):
            j = json.loads(self.call("/api/job?id=" + jid)[1])
            if j["state"] != "running":
                break
            time.sleep(1)
        self.assertEqual(j["state"], "done", j)
        self.assertIn("verdict", j["result"])
        self.assertGreater(len(j["result"]["curve"]), 50)

    def test_candles(self):
        c, b = self.call("/api/candles?inst=futures:BTCUSDT:4h")
        d = json.loads(b)
        self.assertEqual(c, 200)
        self.assertGreater(len(d["candles"]), 200)


class MockBybit:
    """Minimal Bybit V5 stand-in that verifies request signatures and keeps a one-position book."""

    def __init__(self, key="K", secret="S"):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        outer = self
        self.key, self.secret, self.calls, self.pos, self.bad_sigs = key, secret, [], None, 0
        import hmac, hashlib

        class Hd(BaseHTTPRequestHandler):
            def log_message(s, *a): pass

            def _go(s, method):
                n = int(s.headers.get("Content-Length") or 0)
                body = s.rfile.read(n).decode() if n else ""
                path, _, qs = s.path.partition("?")
                if path.startswith("/v5/") and not path.startswith("/v5/market/"):
                    payload = qs if method == "GET" else body
                    exp = hmac.new(outer.secret.encode(), (s.headers["X-BAPI-TIMESTAMP"] + outer.key + s.headers["X-BAPI-RECV-WINDOW"] + payload).encode(), hashlib.sha256).hexdigest()
                    if s.headers.get("X-BAPI-API-KEY") != outer.key or s.headers.get("X-BAPI-SIGN") != exp:
                        outer.bad_sigs += 1
                        return s._out({"retCode": 10004, "retMsg": "error sign"})
                d = json.loads(body) if body else {}
                outer.calls.append((method, path, d))
                res = {}
                if path == "/v5/market/instruments-info":
                    res = {"list": [{"lotSizeFilter": {"qtyStep": "0.001", "minOrderQty": "0.001"}, "priceFilter": {"tickSize": "0.1"}}]}
                elif path == "/v5/account/wallet-balance":
                    res = {"list": [{"totalEquity": "10000.5"}]}
                elif path == "/v5/order/create":
                    if d.get("reduceOnly"):
                        outer.pos = None
                    else:
                        outer.pos = {"size": d["qty"], "avgPrice": "100.5", "side": d["side"], "stopLoss": d["stopLoss"]}
                elif path == "/v5/position/list":
                    res = {"list": [outer.pos or {"size": "0", "avgPrice": "0"}]}
                elif path == "/v5/execution/list":
                    res = {"list": [{"execPrice": "101.0"}]}
                elif path == "/v5/position/trading-stop":
                    outer.pos["stopLoss"] = d["stopLoss"]
                s._out({"retCode": 0, "retMsg": "OK", "result": res})

            def _out(s, o):
                b = json.dumps(o).encode()
                s.send_response(200); s.send_header("Content-Length", str(len(b))); s.end_headers(); s.wfile.write(b)

            def do_GET(s): s._go("GET")
            def do_POST(s): s._go("POST")

        self.httpd = HTTPServer(("127.0.0.1", 0), Hd)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


class BybitAdapter(unittest.TestCase):
    def test_signed_flow_and_engine_exchange_mode(self):
        from tradebot.bybit import Bybit
        m = MockBybit()
        b = Bybit("K", "S", base=m.url)
        self.assertAlmostEqual(b.equity(), 10000.5)
        self.assertEqual(b.round_qty("BTCUSDT", 0.0123456), 0.012)
        b.set_leverage("BTCUSDT", 3)
        fill = b.open("BTCUSDT", 1, 0.012, 95.0)
        self.assertEqual(fill, 100.5)
        order = [c for c in m.calls if c[1] == "/v5/order/create"][0][2]
        self.assertEqual((order["side"], order["qty"], order["stopLoss"], order["category"]), ("Buy", "0.012", "95.0", "linear"))
        b.set_stop("BTCUSDT", 1, 97.26)
        self.assertEqual(m.pos["stopLoss"], "97.3")
        self.assertEqual(b.close("BTCUSDT", 1, 0.012), 101.0)
        self.assertIsNone(m.pos)
        self.assertEqual(m.bad_sigs, 0)
        with self.assertRaises(RuntimeError):
            Bybit("K", "WRONG", base=m.url).equity()                     # bad signature is rejected, not swallowed
        # engine in exchange mode drives the same adapter end to end
        s = mk_store(mode="live", instruments=[dict(DEFAULTS["instruments"][0])], broker={"exchange": "bybit", "bybit_key": "K", "bybit_secret": "S"})
        e = Engine(s, pricer=lambda mk, sym: 100.0)
        e._broker = Bybit("K", "S", base=m.url)
        inst = s.cfg["instruments"][0]
        ok, msg = e.open(inst, 1, 100.0, 5.0)
        self.assertTrue(ok, msg)
        p = s.positions()[inst["id"]]
        self.assertEqual(p["entry"], 100.5)                               # uses the exchange's real fill price
        e.status[inst["id"]] = {"price": 104.0}
        e.close_by_id(inst["id"])
        self.assertIsNone(m.pos)                                          # flatten really reached the exchange
        self.assertEqual(s.positions(), {})

    def test_make_broker(self):
        from tradebot import brokers
        self.assertIsNone(brokers.make_broker({"exchange": "bybit", "bybit_key": "", "bybit_secret": ""}))
        self.assertEqual(brokers.make_broker({"exchange": "bybit", "bybit_key": "a", "bybit_secret": "b", "bybit_env": "demo"}).base, "https://api-demo.bybit.com")

    def test_real_instrument_rounding(self):
        from tradebot.bybit import Bybit
        b = Bybit("k", "s", "testnet")
        self.assertGreaterEqual(b.round_qty("BTCUSDT", 0.0123), 0.001)
        self.assertEqual(b.round_qty("BTCUSDT", 0.0001), 0.0)             # below exchange minimum -> refuse, don't send


_srv = {}


def ensure_server():
    if not _srv:
        import server
        _srv["m"] = server
        _srv["h"] = server.ThreadingHTTPServer(("127.0.0.1", 18765), server.H)
        threading.Thread(target=_srv["h"].serve_forever, daemon=True).start()
    return _srv["m"]


class Cli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_server()

    def test_cli_commands(self):
        import subprocess
        env = dict(os.environ, SENTINEL_URL="http://127.0.0.1:18765")
        root = os.path.dirname(os.path.abspath(__file__))

        def run(*a):
            return subprocess.run([sys.executable, os.path.join(root, "sentinel.py")] + list(a), capture_output=True, text=True, env=env, timeout=120)
        r = run("status"); self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("equity", r.stdout)
        self.assertIn("no open positions", run("positions").stdout)
        self.assertEqual(run("stop").returncode, 0)
        self.assertEqual(run("config", "risk.per_trade_pct", "0.7").returncode, 0)
        self.assertEqual(run("config", "risk.per_trade_pct").stdout.strip(), "0.7")
        bad = subprocess.run([sys.executable, os.path.join(root, "sentinel.py"), "status"], capture_output=True, text=True,
                             env=dict(os.environ, SENTINEL_URL="http://127.0.0.1:9"), timeout=30)
        self.assertIn("server not running", bad.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
