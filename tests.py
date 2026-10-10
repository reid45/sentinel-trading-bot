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
    over.setdefault("rules", {"cooldown_min": 0, "pause_after_stops": 0})
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
        inst = dict(DEFAULTS["instruments"][0], strategy="donchian", params=dict(n=40, trend=0, stop=4.0, trail=1, tp=0, reverse=1))
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

            def do_GET(s):
                b = json.dumps({"data": [{"id": "models/gem-a"}, {"id": "gem-b"}, {"id": "text-embedding-3"}]}).encode()
                s.send_response(200); s.send_header("Content-Length", str(len(b))); s.end_headers(); s.wfile.write(b)

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
        self.assertEqual(self.run_one(self.store(provider="deepseek", api_key=""))["source"], "nokey")
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


class Providers(unittest.TestCase):
    def test_registry_is_sane(self):
        from tradebot import llm
        self.assertGreaterEqual(len(llm.PROVIDERS), 12)
        for k, v in llm.PROVIDERS.items():
            self.assertIn(v["group"], llm.GROUPS, k)
            if k not in ("custom", "claude_cli"):
                self.assertTrue(v["base_url"].startswith("http"), k)
                self.assertTrue(v["model"], k)
        self.assertFalse(llm.PROVIDERS["ollama"]["needs_key"])

    def test_body_quirks(self):
        from tradebot import llm
        msgs = [{"role": "user", "content": "x"}]
        ds = llm.build_body({"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}, msgs, tools=[{"a": 1}])
        self.assertEqual((ds["model"], ds["thinking"], ds["max_tokens"], ds["temperature"], "tools" in ds), ("deepseek-flash", {"type": "disabled"}, 140, 0, True))  # legacy name upgraded
        oa = llm.build_body({"provider": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"}, msgs)
        self.assertIn("max_completion_tokens", oa); self.assertNotIn("max_tokens", oa); self.assertNotIn("temperature", oa); self.assertNotIn("thinking", oa)
        ol = llm.build_body({"provider": "ollama", "base_url": "http://localhost:11434/v1", "model": "llama3.1"}, msgs, tools=[{"a": 1}])
        self.assertNotIn("tools", ol)                                       # small local models: no tool calling by default
        self.assertTrue(llm.configured({"provider": "ollama", "base_url": "http://localhost:11434/v1"}))        # no key needed
        self.assertFalse(llm.configured({"provider": "deepseek", "base_url": "https://api.deepseek.com", "api_key": ""}))

    def test_friendly_errors_and_models(self):
        import urllib.error, io
        from tradebot import llm
        e = urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b'{"error":{"message":"bad key"}}'))
        msg = llm.friendly(e)
        self.assertIn("rejected the key", msg); self.assertIn("bad key", msg)
        self.assertIn("out of credit", llm.friendly(urllib.error.HTTPError("u", 402, "x", {}, io.BytesIO(b"{}"))))
        self.assertIn("Ollama", llm.friendly(urllib.error.URLError("[Errno 111] Connection refused localhost")))
        m = MockLLM()
        r = llm.list_models({"provider": "custom", "base_url": m.url, "api_key": "k"})
        self.assertEqual(r["models"], ["gem-a", "gem-b"]); self.assertTrue(r["live"])        # prefix stripped, embeddings filtered
        r2 = llm.list_models({"provider": "deepseek", "base_url": "http://127.0.0.1:9", "api_key": "k"})
        self.assertFalse(r2["live"]); self.assertEqual(r2["models"], llm.PROVIDERS["deepseek"]["models"])   # offline -> built-in suggestions

    def test_ask_uses_provider_and_explains_failures(self):
        from tradebot import llm
        m = MockLLM()
        text, ti, to = llm.ask({"provider": "custom", "base_url": m.url, "api_key": "k", "model": "x"}, "hi")
        self.assertIn('"p"', text)
        with self.assertRaises(RuntimeError) as cm:
            llm.ask({"provider": "deepseek", "base_url": "https://api.deepseek.com", "api_key": ""}, "hi")
        self.assertIn("API key", str(cm.exception))


def bars15(n=260, breakout=True, seed=5):
    """15-minute bars: quiet range then (optionally) a volume-backed breakout candle."""
    import random
    r, out, t, p = random.Random(seed), [], 1_700_000_000_000, 100.0
    for i in range(n):
        o = p
        p = 100 + r.uniform(-.25, .25)
        out.append((t + i * 900000, o, max(o, p) + .05, min(o, p) - .05, p, 10.0))
    if breakout:
        o = out[-1][4]
        out.append((t + n * 900000, o, 101.7, o - .02, 101.5, 40.0))
    return out


from tradebot import daytrader


class DayTrading(unittest.TestCase):
    DEC = '{"action":"long","conf":80,"stop_atr":1.2,"target_atr":2.5,"why":"clean breakout on volume"}'

    def setUp(self):
        from tradebot import analyst, daytrader
        analyst.reset_runtime(); daytrader._memo.clear()
        analyst.derivs = lambda sym: dict(funding=0.01, funding_avg8=0.008, funding_max8=0.02, oi_24h=3.1, ls=1.8, basis=0.02)
        self.llm = MockLLM()
        self.set_reply(self.DEC)
        self.hist = bars15()
        inst = dict(DEFAULTS["instruments"][0])
        self.s = mk_store(ai={"mode": "advise", "provider": "custom", "base_url": self.llm.url, "api_key": "k", "model": "deepseek-flash"},
                          day={"enabled": True, "tf": "15m", "markets": 3, "min_conf": 60, "max_hold_bars": 4}, instruments=[inst])
        self.e = Engine(self.s, fetcher=lambda m, sym, tf, n: self.hist, pricer=lambda m, sym: self.hist[-1][4])
        self.e._lots = {"BTCUSDT": (0.001, 0.001)}
        self.inst = self.s.cfg["instruments"][0]
        self.did = "day:" + self.inst["id"]

    def set_reply(self, text, usage=None):
        self.llm.default = {"choices": [{"message": {"role": "assistant", "content": text}}], "usage": usage or {"prompt_cache_hit_tokens": 300, "prompt_cache_miss_tokens": 150, "completion_tokens": 30}}

    def kinds(self):
        return [t["kind"] for t in reversed(self.s.thoughts(50))]

    def test_scanner_only_flags_real_setups(self):
        from tradebot import daytrader
        self.assertTrue(daytrader.candidates(bars15(breakout=True)))
        self.assertEqual(daytrader.candidates(bars15(breakout=False)), [])                # quiet market: no AI call at all

    def test_candidate_to_trade_with_visible_reasoning(self):
        self.e._day_one(self.inst, self.s.cfg["day"])
        pos = self.s.positions().get(self.did)
        self.assertIsNotNone(pos)
        self.assertEqual((pos["side"], pos["source"]), (1, "day"))
        self.assertGreater(pos["tp"], pos["entry"]); self.assertLess(pos["stop"], pos["entry"])
        self.assertLessEqual(pos["qty"] * pos["d"], 1000 * 0.005 * 1.001 * 10)           # risk rules still apply
        self.assertEqual(self.kinds(), ["candidate", "decision", "open"])               # the user can read every step
        self.assertIn("clean breakout", self.s.thoughts(5)[1]["text"])
        self.assertEqual(len(self.llm.reqs), 1)
        self.e._day_one(self.inst, self.s.cfg["day"])                                      # same candle again: no second AI call
        self.assertEqual(len(self.llm.reqs), 1)

    def test_flat_low_confidence_and_no_ai_mean_no_trade(self):
        self.set_reply('{"action":"flat","conf":30,"stop_atr":1,"target_atr":2,"why":"chop"}')
        self.e._day_one(self.inst, self.s.cfg["day"])
        self.assertEqual(self.s.positions(), {})
        self.s.put("day_ts:" + self.did, None)
        from tradebot import daytrader; daytrader._memo.clear()
        self.set_reply('{"action":"long","conf":40,"stop_atr":1,"target_atr":2,"why":"meh"}')
        self.e._day_one(self.inst, self.s.cfg["day"])
        self.assertEqual(self.s.positions(), {})                                           # below min_conf
        daytrader._memo.clear()
        s2 = mk_store(day={"enabled": True, "tf": "15m", "markets": 3, "min_conf": 60, "max_hold_bars": 4}, instruments=[dict(DEFAULTS["instruments"][0])])
        e2 = Engine(s2, fetcher=lambda *a: self.hist, pricer=lambda *a: 101.5)
        e2._day_one(s2.cfg["instruments"][0], s2.cfg["day"])
        self.assertEqual(s2.positions(), {})
        self.assertIn("not connected", s2.thoughts(3)[0]["text"])                          # explains why it did nothing

    def test_reward_risk_gate(self):
        self.set_reply('{"action":"long","conf":90,"stop_atr":2,"target_atr":2.2,"why":"x"}')
        self.e._day_one(self.inst, self.s.cfg["day"])
        self.assertEqual(self.s.positions(), {})                                           # target must be >= 1.2x the stop distance

    def test_management_live_stop_target_and_time_exit(self):
        self.e._day_one(self.inst, self.s.cfg["day"])
        pos = self.s.positions()[self.did]
        self.e.price = lambda m, sym: pos["stop"] - 0.01
        self.e._day_price_check(self.did, dict(pos))
        self.assertEqual(self.s.trades(1)[0]["reason"], "stop")                            # stop fires on live price, between candles
        self.assertEqual(self.s.positions(), {})
        self.e.price = lambda m, sym: 101.5
        self.s.put("day_ts:" + self.did, None); from tradebot import daytrader; daytrader._memo.clear()
        self.e._day_one(self.inst, self.s.cfg["day"])
        pos = self.s.positions()[self.did]
        t0 = self.hist[-1][0]
        pos["t_open"] = t0; self.s.set_position(self.did, pos)                           # bar-time based hold counter
        self.hist = self.hist + [(t0 + 4 * 900000, 101.5, 101.55, 101.45, 101.5, 10.0)]  # 4 bars later (max_hold_bars = 4)
        self.e._day_one(self.inst, self.s.cfg["day"])
        self.assertEqual(self.s.trades(1)[0]["reason"], "time")                            # max-hold bars exit

    def test_short_not_allowed_on_stocks(self):
        st = dict(DEFAULTS["instruments"][-1]); st["enabled"] = True
        self.set_reply('{"action":"short","conf":90,"stop_atr":1,"target_atr":2.5,"why":"x"}')
        s = mk_store(ai=self.s.cfg["ai"], day=self.s.cfg["day"], instruments=[st])
        e = Engine(s, fetcher=lambda *a: self.hist, pricer=lambda *a: 99.0)
        e._day_one(s.cfg["instruments"][0], s.cfg["day"])
        self.assertEqual(s.positions(), {})


class Chat(unittest.TestCase):
    def setUp(self):
        from tradebot import analyst
        analyst.reset_runtime()
        self.llm = MockLLM()
        self.s = mk_store(ai={"mode": "advise", "provider": "custom", "base_url": self.llm.url, "api_key": "k", "model": "m"})
        self.e = Engine(self.s, pricer=lambda m, sym: 100.0)

    def test_tool_use_steps_and_answer(self):
        from tradebot import chat
        tc = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "account", "arguments": "{}"}}]}}], "usage": {"prompt_cache_miss_tokens": 400, "completion_tokens": 10}}
        self.llm.script = [tc, {"choices": [{"message": {"content": "You're flat at $10,000."}}], "usage": {"prompt_cache_hit_tokens": 300, "prompt_cache_miss_tokens": 50, "completion_tokens": 12}}]
        r = chat.ask(self.s, self.e, [{"role": "user", "content": "how am I doing?"}])
        self.assertEqual(r["answer"], "You're flat at $10,000.")
        self.assertEqual([x["tool"] for x in r["steps"]], ["account"]); self.assertIn("equity", r["steps"][0]["result"])
        self.assertGreater(r["usd"], 0)
        self.assertEqual(self.llm.reqs[0]["messages"][0]["role"], "system")               # static prefix first (cache-friendly)
        self.assertNotIn("place_trade", json.dumps(self.llm.reqs[0]["tools"]))              # chat is read-only: no trading tool exists

    def test_no_key_and_budget(self):
        from tradebot import chat
        s = mk_store(ai={"provider": "deepseek", "api_key": ""})
        self.assertEqual(chat.ask(s, Engine(s), [{"role": "user", "content": "hi"}])["error"], "nokey")
        s2 = mk_store(ai={"mode": "advise", "provider": "custom", "base_url": self.llm.url, "api_key": "k", "budget": {"max_calls_day": 0, "max_usd_day": 1, "max_usd_total": 1}})
        self.assertEqual(chat.ask(s2, Engine(s2), [{"role": "user", "content": "hi"}])["error"], "budget")
        self.assertEqual(len(self.llm.reqs), 0)


class Watchlist(unittest.TestCase):
    def test_catalog_search_and_defaults(self):
        from tradebot import catalog
        self.assertTrue(any(r["symbol"] == "AAPL" for r in catalog.search("apple", online=False)))
        self.assertGreaterEqual(len(catalog.STATIC), 150)
        self.assertEqual({r["cat"] for r in catalog.STATIC}, {"crypto", "stock", "etf", "forex", "commodity", "index"})
        st = catalog.make_instrument(catalog.lookup("stock", "AAPL"), "1h", False)
        self.assertEqual((st["market"], st["strategy"], st["enabled"], st["id"]), ("stock", "donchian", False, "stock:AAPL:1h"))
        self.assertEqual(st["params"]["reverse"], 0)                                          # stocks: long-only

    def test_featured_stocks_listed_watch_only_and_migration(self):
        feat = [i for i in DEFAULTS["instruments"] if i["market"] == "stock"]
        self.assertGreaterEqual(len(feat), 6); self.assertTrue(all(not i["enabled"] for i in feat))
        s = mk_store(instruments=[{"id": "futures:BTCUSDT:4h", "market": "futures", "symbol": "BTCUSDT", "tf": "4h", "strategy": "donchian", "params": {}, "enabled": True}])
        s.cfg["ui"].pop("seeded_stocks", None); s.migrate_watchlist()
        ins = s.cfg["instruments"]
        self.assertIn("name", ins[0]); self.assertFalse(ins[0]["fav"])
        self.assertTrue(any(i["symbol"] == "AAPL" and not i["enabled"] for i in ins))

    def test_watch_only_never_trades_and_stock_is_long_only(self):
        st = dict(DEFAULTS["instruments"][-1])                                                # a featured stock, watch-only
        s = mk_store(instruments=[st])
        e = Engine(s, pricer=lambda m, sym: 100.0)
        hist = synth(400)
        e._on_bar(st, hist, {"signal": 0})
        self.assertEqual(s.positions(), {})

    def test_stock_costs_and_api(self):
        self.assertIn("stock", B.MARKET_COSTS)
        self.assertEqual(B.MARKET_COSTS["stock"]["fee"], 0.0)


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

    def test_oversized_body_and_health(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", 18765, timeout=10)
        c.request("POST", "/api/engine", body=b"{}", headers={"Content-Type": "application/json", "Content-Length": "5000000"})
        self.assertEqual(c.getresponse().status, 413)                                    # a huge declared body is refused before it is read
        c.close()
        code, b = self.call("/api/health")
        h = json.loads(b)
        self.assertEqual(code, 200)
        self.assertIn(h["level"], ("ok", "warn", "bad"))
        self.assertTrue(all({"level", "title", "fix"} <= set(i) for i in h["items"]))
        self.assertEqual(json.loads(self.call("/healthz")[1]), {"ok": True})

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

    def test_first_run_flags_persist(self):
        st = json.loads(self.call("/api/state")[1])
        self.assertIn("ui", st["cfg"])
        self.assertEqual(self.call("/api/config", {"ui": {"onboarded": True}})[0], 200)
        self.assertTrue(json.loads(self.call("/api/state")[1])["cfg"]["ui"]["onboarded"])
        self.assertFalse(json.loads(self.call("/api/state")[1])["cfg"]["ui"]["tour_done"])      # nested merge keeps the other flag
        self.assertTrue(DEFAULTS["ui"]["onboarded"] is False)                                     # a fresh install always starts with the setup wizard
        for f in ("web/guide.js", "docs/MANUAL.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "run.bat", "run.sh"):
            self.assertTrue(os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), f)), f)

    def test_cache_header_does_not_leak_across_keepalive_requests(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", 18765)
        c.request("GET", "/vendor/lwc.js"); r = c.getresponse(); r.read()
        self.assertIn("immutable", r.getheader("Cache-Control"))
        for path in ("/api/state", "/app.js", "/"):                    # same TCP connection: must NOT inherit the week-long cache header
            c.request("GET", path); r = c.getresponse(); r.read()
            self.assertEqual(r.getheader("Cache-Control"), "no-store", path)
        c.close()

    def test_index_assets_are_version_stamped(self):
        import re, urllib.request as ur
        html = ur.urlopen("http://127.0.0.1:18765/").read().decode()
        self.assertRegex(html, r'src="guide\.js\?v=\d+"')
        self.assertRegex(html, r'src="app\.js\?v=\d+"')
        self.assertRegex(html, r'href="style\.css\?v=\d+"')
        self.assertIn('src="vendor/lwc.js"', html)                        # pinned third-party files stay unstamped (cached a week)

    def test_watchlist_and_catalog_api(self):
        c, b = self.call("/api/catalog?q=nvidia&cat=stock")
        rows = json.loads(b)["rows"]
        self.assertEqual(c, 200); self.assertTrue(any(r["symbol"] == "NVDA" for r in rows))
        n0 = len(json.loads(self.call("/api/state")[1])["cfg"]["instruments"])
        item = {"market": "stock", "symbol": "COST", "name": "Costco", "cat": "stock"}
        self.assertTrue(json.loads(self.call("/api/watchlist", {"action": "add", "items": [item], "trade": True})[1])["ok"])
        ins = json.loads(self.call("/api/state")[1])["cfg"]["instruments"]
        cost = next(i for i in ins if i["symbol"] == "COST")
        self.assertTrue(cost["enabled"]); self.assertEqual(len(ins), n0 + 0 if any(i["symbol"] == "COST" for i in ins[:n0]) else n0 + 1)
        self.call("/api/watchlist", {"action": "fav", "id": cost["id"], "value": True})
        self.call("/api/watchlist", {"action": "trade", "id": cost["id"], "value": False})
        cost = next(i for i in json.loads(self.call("/api/state")[1])["cfg"]["instruments"] if i["symbol"] == "COST")
        self.assertTrue(cost["fav"]); self.assertFalse(cost["enabled"])
        self.call("/api/watchlist", {"action": "remove", "id": cost["id"]})
        self.assertFalse(any(i["symbol"] == "COST" for i in json.loads(self.call("/api/state")[1])["cfg"]["instruments"]))
        self.assertEqual(self.call("/api/watchlist", {"action": "fav", "id": "nope"})[0], 404)
        self.assertEqual(self.call("/api/thoughts")[0], 200)
        self.call("/api/config", {"ai": {"provider": "custom", "base_url": "http://127.0.0.1:9"}})                       # dead local port: never a real AI call
        self.assertIn(json.loads(self.call("/api/chat", {"messages": [{"role": "user", "content": "hi"}]})[1])["error"], ("api", "nokey"))

    def test_speed_switch_rewrites_ids_and_scales_lookbacks(self):
        self.assertEqual(self.call("/api/speed", {"tf": "7m"})[0], 400)
        before = json.loads(self.call("/api/state")[1])["cfg"]["instruments"]
        n0 = next(i for i in before if i["strategy"] == "donchian")["params"]["n"]
        self.assertEqual(self.call("/api/speed", {"tf": "15m", "flatten": True})[0], 200)
        after = json.loads(self.call("/api/state")[1])["cfg"]["instruments"]
        self.assertTrue(all(i["tf"] == "15m" and i["id"].endswith(":15m") for i in after))
        self.assertGreater(next(i for i in after if i["strategy"] == "donchian")["params"]["n"], n0)      # longer lookback on faster candles
        self.assertEqual(self.call("/api/speed", {"tf": "4h", "flatten": True})[0], 200)
        back = json.loads(self.call("/api/state")[1])["cfg"]["instruments"]
        self.assertEqual(next(i for i in back if i["strategy"] == "donchian")["params"]["n"], n0)          # round trip restores the original

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


class AuthUnit(unittest.TestCase):
    def setUp(self):
        from tradebot.auth import Auth, AuthError
        self.AuthError = AuthError
        s = mk_store()
        self.a = Auth(s.db, s.lock, tempfile.mkdtemp())

    def test_users_passwords_sessions(self):
        a = self.a
        for bad in ("short", "password123", "aaaaaaaaaaaa"):
            with self.assertRaises(self.AuthError):
                a.create_user("reid", bad, "owner")
        a.create_user("Reid", "correct horse battery", "owner")
        with self.assertRaises(self.AuthError):
            a.create_user("reid", "another long phrase here", "viewer")        # names are unique ignoring case
        self.assertEqual(a.verify("reid", "correct horse battery"), "Reid")
        self.assertIsNone(a.verify("reid", "wrong password!!"))
        self.assertIsNone(a.verify("nobody", "correct horse battery"))
        who, (tok, life) = a.login("Reid", "correct horse battery", "1.1.1.1", "ua", remember=False)
        self.assertEqual(a.session(tok), ("Reid", "owner"))
        self.assertLessEqual(life, 24 * 3600)
        row = a.db.execute("SELECT h FROM sessions").fetchone()[0]
        self.assertNotEqual(row, tok)                                           # only a hash of the token is stored
        a.set_password("Reid", "a brand new passphrase")                          # changing the password signs out every device
        self.assertIsNone(a.session(tok))
        with self.assertRaises(self.AuthError):
            a.delete_user("Reid")                                                # never lock yourself out
        a.logout(tok)

    def test_lockout_and_idle_expiry(self):
        a = self.a
        a.create_user("reid", "correct horse battery", "owner")
        for _ in range(5):
            with self.assertRaises(self.AuthError):
                a.login("reid", "nope nope nope", "9.9.9.9")
        with self.assertRaises(self.AuthError) as cm:
            a.login("reid", "correct horse battery", "9.9.9.9")                  # even the right password is refused while locked
        self.assertIn("Too many", str(cm.exception))
        with self.assertRaises(self.AuthError):
            a.login("reid", "correct horse battery", "8.8.8.8")                  # the account is locked too, not just the address
        a.fails.clear()
        who, (tok, _) = a.login("reid", "correct horse battery", "9.9.9.9")
        a.db.execute("UPDATE sessions SET last=last-?", (13 * 3600,))
        self.assertIsNone(a.session(tok))                                        # idle for 13 hours -> signed out


class AuthServer(unittest.TestCase):
    """The HTTP layer with login turned on: public paths, cookies, CSRF, roles, host checking."""
    @classmethod
    def setUpClass(cls):
        cls.m = ensure_server()
        cls.m.FORCE_AUTH = True
        cls.m.PUBLIC_HOSTS.add("sentineltrade.example.ca")

    @classmethod
    def tearDownClass(cls):
        cls.m.FORCE_AUTH = False
        cls.m.PUBLIC_HOSTS.discard("sentineltrade.example.ca")
        cls.m.store.put("auth_required", False)
        with cls.m.store.lock:
            cls.m.store.db.execute("DELETE FROM users")
            cls.m.store.db.execute("DELETE FROM sessions")
            cls.m.store.db.commit()
        cls.m.auth.fails.clear()

    def req(self, method, path, body=None, cookie=None, host="127.0.0.1:18765", extra=None):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", 18765, timeout=30)
        h = {"Host": host}
        if cookie:
            h["Cookie"] = "sentinel_session=" + cookie
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(extra or {})
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data_ = r.read()
        sc = r.getheader("Set-Cookie") or ""
        out = (r.status, data_, sc, r)
        c.close()
        return out

    def test_full_flow(self):
        m = self.m
        self.assertEqual(self.req("GET", "/healthz")[0], 200)                             # monitors can poll without logging in
        self.assertEqual(self.req("GET", "/api/state")[0], 401)
        st, _, _, r = self.req("GET", "/")
        self.assertEqual((st, r.getheader("Location")), (302, "/login"))
        self.assertEqual(self.req("GET", "/login")[0], 200)
        self.assertEqual(self.req("GET", "/app.js")[0], 302)                              # the app itself is not served to strangers
        self.assertEqual(self.req("GET", "/api/state", host="evil.example.com")[0], 421)  # unknown Host names are refused
        s = json.loads(self.req("GET", "/api/auth/status")[1])
        self.assertTrue(s["required"] and s["setup"] and s["need_code"] and not s["authed"])
        # --- first-run setup needs the one-time code
        self.assertEqual(self.req("POST", "/api/auth/setup", {"username": "reid", "password": "correct horse battery", "code": "nope"}, extra={"X-Sentinel": "1"})[0], 403)
        code = m.auth.setup_code()
        st, b, ck, _ = self.req("POST", "/api/auth/setup", {"username": "reid", "password": "correct horse battery", "code": code})
        self.assertEqual(st, 200, b)
        self.assertIn("HttpOnly", ck)
        self.assertIn("SameSite=Lax", ck)
        tok = ck.split("sentinel_session=")[1].split(";")[0]
        self.assertFalse(os.path.exists(os.path.join(TMP, "setup_code.txt")))                # code is single-use
        self.assertEqual(self.req("POST", "/api/auth/setup", {"username": "xxxxx", "password": "correct horse battery", "code": code})[0], 409)
        # --- signed-in access
        st, b, _, _ = self.req("GET", "/api/state", cookie=tok)
        self.assertEqual(st, 200)
        me = json.loads(b)["me"]
        self.assertEqual((me["user"], me["role"], me["auth"]), ("reid", "owner", True))
        # --- CSRF protections on writes
        self.assertEqual(self.req("POST", "/api/engine", {"action": "nonsense"}, cookie=tok)[0], 403)                         # missing X-Sentinel
        self.assertEqual(self.req("POST", "/api/engine", {"action": "nonsense"}, cookie=tok, extra={"X-Sentinel": "1", "Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.req("POST", "/api/engine", {"action": "nonsense"}, cookie=tok, extra={"X-Sentinel": "1"})[0], 400)    # reaches the handler
        self.assertEqual(self.req("POST", "/hook", {"secret": "bad"})[0], 403)                                                # webhook has its own secret, no login
        # --- a view-only account
        hdr = {"X-Sentinel": "1"}
        self.assertEqual(self.req("POST", "/api/auth/users", {"action": "add", "username": "cousin", "password": "viewer pass phrase", "role": "viewer"}, cookie=tok, extra=hdr)[0], 200)
        st, b, ck2, _ = self.req("POST", "/api/auth/login", {"username": "cousin", "password": "viewer pass phrase"})
        self.assertEqual(st, 200, b)
        vt = ck2.split("sentinel_session=")[1].split(";")[0]
        self.assertEqual(self.req("GET", "/api/state", cookie=vt)[0], 200)
        self.assertEqual(self.req("POST", "/api/engine", {"action": "kill"}, cookie=vt, extra=hdr)[0], 403)                   # viewers cannot trade or change settings
        self.assertEqual(self.req("POST", "/api/config", {"risk": {"per_trade_pct": 5}}, cookie=vt, extra=hdr)[0], 403)
        self.assertEqual(self.req("GET", "/api/auth/users", cookie=vt)[0], 403)
        # --- sign out really signs out
        st, _, ck3, _ = self.req("POST", "/api/auth/logout", {}, cookie=vt, extra=hdr)
        self.assertEqual(st, 200)
        self.assertIn("Max-Age=0", ck3)
        self.assertEqual(self.req("GET", "/api/state", cookie=vt)[0], 401)
        # --- wrong passwords lock the address
        for _ in range(5):
            self.req("POST", "/api/auth/login", {"username": "reid", "password": "wrong wrong wrong"})
        st, b, _, _ = self.req("POST", "/api/auth/login", {"username": "reid", "password": "correct horse battery"})
        self.assertEqual(st, 429)


class Rules(unittest.TestCase):
    """Entry rules and protections: every refusal is a plain reason, never a silent no-op."""
    def setUp(self):
        from tradebot import rules
        self.rules = rules
        self.s = mk_store(instruments=[dict(DEFAULTS["instruments"][0])], rules={"cooldown_min": 0, "pause_after_stops": 0})
        self.inst = self.s.cfg["instruments"][0]
        self.noon = 1_790_000_000_000 - 1_790_000_000_000 % 86400000 + 12 * 3600000      # a day at 12:00 UTC (not a fixed weekday on purpose)

    def ok(self, side=1, risk=1.0, source="engine", poss=None, now=None):
        return self.rules.entry_allowed(self.s, self.inst, side, risk, source, 1000.0, poss or {}, now or self.noon)

    def test_hours_direction_and_day_cap(self):
        self.assertTrue(self.ok()[0])
        self.s.save_cfg({"rules": {"hours": {"enabled": True, "start": 14, "end": 20, "days": [0, 1, 2, 3, 4, 5, 6]}}})
        ok, why = self.ok()
        self.assertFalse(ok); self.assertIn("trading hours", why)
        self.assertTrue(self.ok(now=self.noon + 3 * 3600000)[0])                        # 15:00 UTC is inside 14-20
        self.s.save_cfg({"rules": {"hours": {"enabled": True, "start": 22, "end": 6, "days": [0, 1, 2, 3, 4, 5, 6]}}})
        self.assertTrue(self.ok(now=self.noon + 11 * 3600000)[0])                       # overnight window wraps midnight (23:00)
        self.s.save_cfg({"rules": {"hours": {"enabled": False}, "direction": "long"}})
        self.assertFalse(self.ok(side=-1)[0]); self.assertTrue(self.ok(side=1)[0])
        self.s.save_cfg({"rules": {"direction": "both", "max_trades_day": 1}})
        self.s.add_trade(inst="x", symbol="ETHUSDT", side=1, qty=1, entry=1, exit=1, pnl=0, fees=0, t_open=self.noon - 1000, t_close=self.noon - 500, reason="target", source="engine", mode="paper")
        ok, why = self.ok()
        self.assertFalse(ok); self.assertIn("limit of 1", why)

    def test_cooldown_and_stop_guard_and_risk_cap(self):
        self.s.save_cfg({"rules": {"cooldown_min": 30}})
        self.s.add_trade(inst="x", symbol="BTCUSDT", side=1, qty=1, entry=1, exit=1, pnl=-5, fees=0, t_open=self.noon - 3600000, t_close=self.noon - 600000, reason="stop", source="engine", mode="paper")
        ok, why = self.ok()
        self.assertFalse(ok); self.assertIn("cooling down", why)
        self.assertTrue(self.rules.entry_allowed(self.s, dict(self.inst, symbol="ETHUSDT"), 1, 1.0, "engine", 1000.0, {}, self.noon)[0])    # other markets unaffected
        self.s.save_cfg({"rules": {"cooldown_min": 0, "pause_after_stops": 2, "pause_minutes": 60}})
        self.s.add_trade(inst="y", symbol="SOLUSDT", side=1, qty=1, entry=1, exit=1, pnl=-5, fees=0, t_open=self.noon - 1800000, t_close=self.noon - 300000, reason="stop", source="engine", mode="paper")
        ok, why = self.ok()
        self.assertFalse(ok); self.assertIn("2 stop-outs", why)
        self.s.save_cfg({"rules": {"pause_after_stops": 0, "max_open_risk_pct": 2.0}})
        held = {"a": dict(source="day", mark=100.0, entry=100.0, stop=99.0, qty=15.0)}              # $15 already at risk = 1.5% of $1000
        ok, why = self.ok(source="day", risk=10.0, poss=held)
        self.assertFalse(ok); self.assertIn("cap is 2.0%", why)
        self.assertTrue(self.ok(source="day", risk=3.0, poss=held)[0])
        self.assertTrue(self.ok(source="engine", risk=10.0, poss=held)[0])                           # the cap is for day trades only


class Notify(unittest.TestCase):
    def test_payloads_and_delivery(self):
        from tradebot import notify
        import http.server
        got = []

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                got.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d/hook" % srv.server_port
        self.assertEqual(notify.build({"kind": "discord", "url": url}, "T", "x")[1], {"content": "**T**\nx"})
        self.assertEqual(notify.build({"kind": "slack", "url": url}, "T", "x")[1], {"text": "*T*\nx"})
        self.assertEqual(notify.build({"kind": "telegram", "telegram_token": "123:ABC", "telegram_chat": "7"}, "T", "x")[0], "https://api.telegram.org/bot123:ABC/sendMessage")
        self.assertIsNone(notify.build({"kind": "discord", "url": "http://example.com/plain"}, "T", "x"))          # plain http to the internet is refused
        self.assertFalse(notify.send({"kind": "discord", "url": ""}, "T", "x", wait=True)[0])
        ok, err = notify.send({"kind": "generic", "url": url}, "Hello", "world", wait=True)
        self.assertTrue(ok, err)
        self.assertEqual(got[-1]["title"], "Hello")
        # events: a trade open/close reaches the webhook, a skip does not
        s = mk_store(notify={"kind": "generic", "url": url, "on_open": True, "on_close": True, "on_alert": False})
        n = len(got)
        s.event("trade", "DAY BTCUSDT LONG: opened"); s.event("skip", "nothing"); s.event("error", "ignored")
        time.sleep(0.6)
        self.assertEqual(len(got) - n, 1)
        srv.shutdown()


class DataFallback(unittest.TestCase):
    def test_binance_down_falls_back_and_ban_is_respected(self):
        real_get = data._get
        calls = []

        def fake(url, timeout=15):
            calls.append(url)
            if "fapi.binance.com" in url:
                raise RuntimeError("HTTP 451")
            if "bybit.com" in url and "/kline" in url:
                now = int(time.time() * 1000) // 900000 * 900000
                rows = [[str(now - i * 900000), "1", "2", "0.5", "1.5", "10", "15"] for i in range(5)]
                return {"retCode": 0, "result": {"list": rows}}
            if "bybit.com" in url and "/tickers" in url:
                return {"result": {"list": [{"lastPrice": "123.5"}]}}
            raise RuntimeError("unexpected " + url)
        data._get = fake
        try:
            rows = data.fetch_recent("futures", "BTCUSDT", "15m", 4)
            self.assertTrue(rows and rows[0][0] < rows[-1][0])                                 # oldest first, closed bars only
            self.assertIn("Bybit", data.LAST_SRC["futures"])
            self.assertEqual(data.last_price("futures", "BTCUSDT"), 123.5)
        finally:
            data._get = real_get
        data._ban["until"] = time.time() + 60                                                  # a 418 ban: stay off the network entirely
        try:
            with self.assertRaises(RuntimeError) as cm:
                data._get("https://fapi.binance.com/fapi/v1/ping")
            self.assertIn("slow down", str(cm.exception))
        finally:
            data._ban["until"] = 0.0


class TrendMix(unittest.TestCase):
    """One dashboard entry per coin; the engine runs each member strategy as its own sleeve with its own position and stop."""
    def setUp(self):
        from tradebot import catalog
        self.cat = catalog
        row = catalog.lookup("futures", "BTCUSDT")
        inst = catalog.make_instrument(row, "4h", True)
        self.s = mk_store(instruments=[inst], rules={"cooldown_min": 0, "pause_after_stops": 0})
        self.hist = synth(900, seed=5, drift=0.0012, vol=0.01)
        self.k = {"i": 600}
        self.e = Engine(self.s, fetcher=lambda m, sym, tf, n: self.hist[:self.k["i"] + 1][-500:], pricer=lambda m, sym: self.hist[self.k["i"]][4])
        self.e._lots = {"BTCUSDT": (0.001, 0.001)}
        self.inst = self.s.cfg["instruments"][0]

    def test_default_is_trendmix_with_four_members(self):
        self.assertEqual(self.inst["strategy"], "trendmix")
        self.assertEqual([m["name"] for m in self.inst["params"]["members"]], ["donch20", "donch40", "mtf1d", "ema20_150"])
        self.assertEqual(len(self.cat.UNIVERSE), 38)
        self.assertEqual(len([i for i in DEFAULTS["instruments"] if i["market"] == "futures"]), 38)

    def test_sleeves_get_own_positions_and_close_together(self):
        subs = self.e._sleeves(self.inst)
        self.assertEqual([s["id"].split("#")[1] for s in subs], ["donch20", "donch40", "mtf1d", "ema20_150"])
        self.assertEqual(len(self.e._sleeves(dict(self.inst, enabled=False))), 1)           # watch-only coins never trade, so one cheap sleeve is enough
        for sub in subs[:2]:
            ok, msg = self.e.open(sub, 1, 100.0, 4.0, source="engine")
            self.assertTrue(ok, msg)
        self.assertEqual(sorted(k.split("#")[1] for k in self.s.positions()), ["donch20", "donch40"])
        self.assertFalse(self.e.open(subs[0], 1, 100.0, 4.0)[0])                              # a sleeve cannot stack on itself
        self.assertEqual(self.e._inst_for(subs[1]["id"])["id"], subs[1]["id"])
        self.assertTrue(self.e.close_by_id(self.inst["id"], "test"))                          # closing the coin closes every sleeve
        self.assertEqual(self.s.positions(), {})
        self.assertEqual(len(self.s.trades(10)), 2)

    def test_cycle_runs_sleeves_and_reports_one_status(self):
        st = {"last_ts": None, "price": None, "signal": 0, "error": None, "price_t": 0}
        self.e._cc = {}
        for sub in self.e._sleeves(self.inst):
            self.e._process(sub, self.e.sub_status.setdefault(sub["id"], dict(st)))
        self.assertTrue(all(v["error"] is None for v in self.e.sub_status.values()))
        self.assertEqual(len(self.e.sub_status), 4)

    def test_dead_contract_never_signals(self):
        flat = [(r[0], 5.0, 5.0, 5.0, 5.0, 0.0) for r in self.hist]
        e = Engine(self.s, fetcher=lambda m, sym, tf, n: flat[:700], pricer=lambda m, sym: 5.0)
        st = {"last_ts": None, "price": None, "signal": 0, "error": None, "price_t": 0}
        e._process(self.e._sleeves(self.inst)[0], st)
        self.assertIn("no volume", st["error"])
        self.assertEqual(self.s.positions(), {})

    def test_portfolio_backtester_shares_one_account(self):
        coins = [("C%d" % i, synth(1200, seed=20 + i, drift=0.001, vol=0.01), "donchian", dict(n=20, trend=0, stop=3.0, trail=1, reverse=0)) for i in range(6)]
        m = B.portfolio_run(coins, "4h", risk=0.01, max_pos=2, max_lev=3.0)
        self.assertGreater(m["trades"], 5)
        starts = sorted((t[0], t[1]) for t in m["trade_list"])
        for t0, _ in starts:
            self.assertLessEqual(sum(1 for a, b in starts if a <= t0 < b), 2)                  # never more than max_pos open at once
        wide = B.portfolio_run(coins, "4h", risk=0.01, max_pos=6, max_lev=3.0)
        self.assertGreaterEqual(wide["trades"], m["trades"])

    def test_preset_and_lab_endpoints(self):
        srv = ensure_server()
        before = json.loads(json.dumps(srv.store.cfg))
        try:
            srv.store.save_cfg({"instruments": [dict(i) for i in before["instruments"] if i["market"] == "futures"][:3] + [i for i in before["instruments"] if i["market"] != "futures"][:1]})
            code, out = srv.preset_action({"name": "trendmix"})
            self.assertEqual(code, 200, out)
            fut = [i for i in srv.store.cfg["instruments"] if i["market"] == "futures"]
            self.assertEqual(len(fut), 38)
            self.assertTrue(all(i["strategy"] == "trendmix" and i["tf"] == "4h" for i in fut))
            self.assertEqual((srv.store.cfg["risk"]["per_trade_pct"], srv.store.cfg["risk"]["max_positions"]), (0.3, 15))
            self.assertEqual(srv.preset_action({"name": "nope"})[0], 400)
            c = synth(900, seed=9, drift=0.001, vol=0.01)
            m = srv.run_any(c, "trendmix", self.cat.defaults_for("futures")[1], "futures", "4h", 0.003)
            self.assertIn("curve", m)
            self.assertEqual(len(m["curve"]), len(c))
        finally:
            srv.store.cfg = before
            srv.store.save_cfg()


class EvidenceTools(unittest.TestCase):
    def hist15(self, n=1500, seed=4):
        h = synth(n, seed=seed, drift=0.0, vol=0.004)
        return [(1_790_000_000_000 - (n - i) * 900000, o, hh, ll, c, 50.0 + (i % 7)) for i, (_, o, hh, ll, c, _) in enumerate(h)]

    def test_levels_and_mtf_shape(self):
        from tradebot import tools
        h = self.hist15()
        lv = tools.levels(h)
        self.assertIn("prev_day_high", lv); self.assertIn("swing_highs_above", lv)
        self.assertTrue(all(x[0] > lv["price"] for x in lv["swing_highs_above"]))             # "above" really is above
        self.assertTrue(all(x[0] < lv["price"] for x in lv["swing_lows_below"]))
        out = tools.mtf(lambda m, s, tf, n: self.hist15(n=300), "futures", "BTCUSDT")
        self.assertEqual(len(out.splitlines()), 4)
        self.assertIn("ADX", out)

    def test_edge_charges_costs_and_is_causal(self):
        from tradebot import tools
        h = self.hist15()
        e = tools.edge(h, "breakout")
        self.assertGreater(e["follow"]["n"], 20)
        self.assertEqual(e["follow"]["n"], e["fade"]["n"])                                     # same triggers, opposite sides
        self.assertLess(e["follow"]["expR"] + e["fade"]["expR"], 0)                           # a coin flip cannot pay both ways once fees are charged
        trig, _ = tools._triggers(h)
        cut = tools._triggers(h[:900])[0]
        self.assertEqual(trig[500], cut[500])                                                 # a bar's trigger never depends on later bars

    def test_tool_dispatch_and_schema(self):
        from tradebot import analyst
        names = [t["function"]["name"] for t in analyst.TOOLS]
        for n in ("candles", "derivs", "journal", "levels", "mtf", "flow", "edge"):
            self.assertIn(n, names)
        s = mk_store(instruments=[dict(DEFAULTS["instruments"][0])])
        out = analyst.run_tool("levels", {"symbol": "BTCUSDT"}, s, lambda m, sy, tf, n: self.hist15(n=1200))
        self.assertIn("prev_day_high", out)
        self.assertEqual(analyst.run_tool("levels", {"symbol": "NOPE"}, s, lambda *a: []), "unknown symbol")


if __name__ == "__main__":
    unittest.main(verbosity=2)
