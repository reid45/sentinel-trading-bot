"""Autonomous trading engine: polls bars, runs strategies, sizes by risk, enforces limits, journals everything.
Paper mode simulates fills (same costs as the backtester). Live mode (futures only) routes through brokers.BinanceFutures."""
import math, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor
from . import data, analyst, daytrader, rules
from .scalper import Scalper
from .smart import Smart
from .backtest import MARKET_COSTS, TF_HOURS
from .strategies import STRATEGIES
from . import indicators as I


def now_ms():
    return int(time.time() * 1000)


class Engine:
    def __init__(self, store, fetcher=None, pricer=None):
        self.s = store
        self.fetch = fetcher or data.fetch_recent
        self.price = pricer or data.last_price
        self.status = {}          # inst id -> dict for the dashboard
        self.sub_status = {}      # sleeve id -> status (a trendmix coin runs several sleeves under one dashboard entry)
        self._cc = None           # per-cycle cache (set only inside cycle()) so the sleeves of one coin share one price call and one candle download
        self.running = False
        self.thread = None
        self.lock = threading.RLock()
        self.last_cycle = 0
        self.last_snapshot = 0
        self._broker = None
        self.day_stats = dict(scans=0, candidates=0, trades=0, last=0)
        self.scalper = Scalper(self)
        self.smart = Smart(self)

    # ------------------------------------------------------------------ lifecycle
    def migrate_accounting(self):
        """One-time fix for versions that charged the entry fee twice (once in the fill price, once as a cash deduction). Refunds the extra charge on
        open positions and on past paper trades, marks everything as corrected, and says so in the activity log."""
        if self.s.get("acct_v2"):
            return 0.0
        refund = 0.0
        with self.lock:
            for pid, p in self.s.positions().items():
                if p.get("acct") != 2:
                    refund += p.get("fee_in", 0.0)
                    p["acct"] = 2
                    self.s.set_position(pid, p)
            for t in self.s.trades(100000):
                if t.get("mode") != "live":
                    mkt = (t["inst"].split(":")[1] if t["inst"].startswith(("day:", "scalp:", "smart:")) else t["inst"].split(":")[0])
                    mc = MARKET_COSTS.get(mkt, MARKET_COSTS["futures"])
                    refund += t["qty"] * t["entry"] * (mc["fee"] + mc["slip"])
                    with self.s.lock:
                        self.s.db.execute("UPDATE trades SET pnl = pnl + ? WHERE id = ?", (t["qty"] * t["entry"] * (mc["fee"] + mc["slip"]), t["id"]))
                        self.s.db.commit()
            if refund > 0:
                self.s.put("cash", self.cash() + refund)
            self.s.put("acct_v2", True)
        if refund > 0:
            self.s.event("info", "Accounting corrected: entry fees had been charged twice. Refunded $%.2f across open and past trades." % refund)
        return refund

    def start(self):
        with self.lock:
            if self.running:
                return
            self.migrate_accounting()
            self.running = True
            self.s.put("engine_on", True)
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
            threading.Thread(target=self._scalp_loop, daemon=True).start()
            self.s.event("info", "Engine started (%s mode)" % self.s.cfg["mode"])

    def _scalp_loop(self):
        """The scalper needs a quicker heartbeat than the 20-second main loop (it works on 1-minute candles)."""
        errs = 0
        while self.running:
            try:
                self.scalper.tick()
                errs = 0
            except Exception:
                errs += 1
                if errs in (1, 20):
                    self.s.event("error", "scalper loop: " + traceback.format_exc().splitlines()[-1][:100])
            for _ in range(4):
                if not self.running:
                    return
                time.sleep(1)

    def stop(self):
        self.running = False
        self.s.put("engine_on", False)
        self.s.event("info", "Engine stopped")

    def _loop(self):
        while self.running:
            try:
                self.cycle()
            except Exception:
                self.s.event("error", "cycle crashed: " + traceback.format_exc().splitlines()[-1])
            fast = min([data.TF_MS[i["tf"]] for i in self.s.cfg["instruments"]] or [14400000]) / 1000
            for _ in range(int(max(3, min(self.s.cfg["poll_seconds"], fast / 10)))):      # faster timeframes are polled faster
                if not self.running:
                    return
                time.sleep(1)

    # ------------------------------------------------------------------ accounting
    def cash(self):
        return self.s.get("cash", self.s.cfg["start_equity"])

    @staticmethod
    def upnl(p):
        """Unrealized P&L after funding and the cost of closing right now (fee + slippage)."""
        market = p.get("market") or p["inst"].split(":")[0]
        mc = MARKET_COSTS.get(market, MARKET_COSTS["futures"])
        mark = p.get("mark", p["entry"])
        return p["side"] * (mark - p["entry"]) * p["qty"] - p.get("fund", 0) - mark * p["qty"] * p.get("cost_out_stop", mc["fee"] + mc["slip"])

    def equity(self):
        return self.cash() + sum(self.upnl(p) for p in self.s.positions().values())

    def halted(self):
        return bool(self.s.get("halted", False))

    def broker(self):
        if self._broker is None:
            from . import brokers
            self._broker = brokers.make_broker(self.s.cfg["broker"])
        return self._broker

    def live(self):
        return self.s.cfg["mode"] == "live"

    # ------------------------------------------------------------------ main cycle
    def _sleeves(self, inst):
        """A trendmix coin expands into one pseudo-instrument per member strategy ('<id>#<name>'), each with its own position and trailing stop.
        Watch-only coins run just the first member, so they still get prices and signals without any risk of an entry."""
        if inst.get("strategy") != "trendmix":
            return [inst]
        ms = inst["params"]["members"]
        ms = ms if inst.get("enabled", True) else ms[:1]
        return [dict(inst, id="%s#%s" % (inst["id"], m["name"]), parent=inst["id"], strategy=m["strat"], params=m["p"]) for m in ms]

    def _cached(self, key, fn):
        cc = self._cc
        if cc is None:
            return fn()
        if key not in cc:
            cc[key] = fn()
        return cc[key]

    def cycle(self):
        cfg = self.s.cfg
        t = now_ms()
        self._cc = {}
        def one(inst):
            subs = self._sleeves(inst)
            sts = [self.sub_status.setdefault(s["id"], {"last_ts": None, "price": None, "signal": 0, "error": None, "price_t": 0}) for s in subs]
            for s, st in zip(subs, sts):
                try:
                    self._process(s, st)
                except Exception as e:
                    st["error"] = str(e)[:120]
            head = self.status.setdefault(inst["id"], {})
            head.update({k: v for k, v in sts[0].items()})
            head["signal"] = next((x["signal"] for x in sts if x.get("signal")), 0)
            head["error"] = next((x["error"] for x in sts if x.get("error")), None)
        todo = list(cfg["instruments"])          # watch-only markets are still polled for prices; entries are gated in _on_bar
        for i in todo:
            self.status.setdefault(i["id"], {"last_ts": None, "price": None, "signal": 0, "error": None, "price_t": 0})
        for gone in [k for k in self.status if k not in {i["id"] for i in todo}]:      # removed markets disappear from the dashboard
            self.status.pop(gone, None)
        try:
            with ThreadPoolExecutor(8) as ex:      # network-bound: poll all markets at once
                list(ex.map(one, todo))
        finally:
            self._cc = None
        if cfg["day"].get("enabled"):
            self._day_cycle()
        if cfg["smart"].get("enabled"):
            try:
                self.smart.tick()
            except Exception as ex:
                self.s.event("error", "smart-money trader: " + str(ex)[:100])
        self._risk_guards()
        self.last_cycle = t
        if t - self.last_snapshot >= 60000:
            self.s.add_equity(t, self.equity(), self.cash())
            self.last_snapshot = t

    def _process(self, inst, st):
        tfms = data.TF_MS[inst["tf"]]
        pos = self.s.positions().get(inst["id"])
        t = now_ms()
        # keep marks fresh: open positions every cycle, others every 2 min
        if pos or t - st["price_t"] > 120000:
            try:
                st["price"], st["price_t"] = self._cached(("p", inst["market"], inst["symbol"]), lambda: self.price(inst["market"], inst["symbol"])), t
                if pos:
                    pos["mark"] = st["price"]
                    self.s.set_position(inst["id"], pos)
                    px_, sd_ = st["price"], pos["side"]
                    if (sd_ == 1 and px_ <= pos["stop"]) or (sd_ == -1 and px_ >= pos["stop"]):      # stops trigger on live price, not only at candle close
                        self._close(inst, pos, px_, "stop", exchange_side=True)
                        pos = None
                    elif pos.get("tp") and ((sd_ == 1 and px_ >= pos["tp"]) or (sd_ == -1 and px_ <= pos["tp"])):
                        self._close(inst, pos, px_, "target", exchange_side=True)
                        pos = None
            except Exception as e:
                st["error"] = "price: " + str(e)[:80]
        due = st["last_ts"] is None or t >= st["last_ts"] + 2 * tfms + 4000
        st["next_bar"] = (st["last_ts"] + 2 * tfms) if st["last_ts"] else None
        if not due:
            return
        hist = self._cached(("c", inst["market"], inst["symbol"], inst["tf"]), lambda: self.fetch(inst["market"], inst["symbol"], inst["tf"], 500))
        if len(hist) < 250:
            st["error"] = "only %d bars" % len(hist)
            return
        if inst["market"] == "futures" and sum(1 for b in hist[-12:] if b[5] == 0) >= 6:
            st["error"] = "not trading: the exchange reports no volume for this contract"        # frozen / delisted contracts must never produce signals
            return
        st["error"] = None
        cl = [b[4] for b in hist]
        st["spark"] = [round(x, 8) for x in cl[-48:]]
        st["chg24"] = (cl[-1] / cl[-7] - 1) * 100 if len(cl) > 7 else 0.0
        n = int(inst["params"].get("n") or 20)               # how far the market is from its next breakout trigger (the scanner on the Trader page)
        st["to_up"] = (max(b[2] for b in hist[-n - 1:-1]) / cl[-1] - 1) * 100
        st["to_dn"] = (cl[-1] / min(b[3] for b in hist[-n - 1:-1]) - 1) * 100
        e200 = I.ema(cl, 200)[-1]
        st["trend"] = 0 if e200 is None else (1 if cl[-1] > e200 else -1)
        if st["last_ts"] is None:
            st["last_ts"] = self.s.get("last_ts:" + inst["id"])
        newest = hist[-1][0]
        if st["last_ts"] is None:   # first run: act only if the latest bar JUST closed, otherwise baseline
            fresh = t - (newest + tfms) < 15 * 60000
            st["last_ts"] = hist[-2][0] if fresh else newest
        for i, bar in enumerate(hist):
            if bar[0] > st["last_ts"]:
                self._on_bar(inst, hist[:i + 1], st)
                st["last_ts"] = bar[0]
                self.s.put("last_ts:" + inst["id"], bar[0])
        st["next_bar"] = st["last_ts"] + 2 * tfms

    # ------------------------------------------------------------------ per-bar logic
    def _on_bar(self, inst, hist, st):
        bar = hist[-1]
        t, o, h, l, c, _ = bar
        pos = self.s.positions().get(inst["id"])
        mc = MARKET_COSTS[inst["market"]]
        if pos:
            pos["mark"] = c
            pos["fund"] = pos.get("fund", 0) + mc["funding_8h"] * TF_HOURS[inst["tf"]] / 8 * pos["qty"] * c
            hit = None
            if pos["side"] == 1:
                if l <= pos["stop"]:
                    hit = ("stop", min(pos["stop"], o))
                elif pos.get("tp") and h >= pos["tp"]:
                    hit = ("target", pos["tp"])
            else:
                if h >= pos["stop"]:
                    hit = ("stop", max(pos["stop"], o))
                elif pos.get("tp") and l <= pos["tp"]:
                    hit = ("target", pos["tp"])
            if hit:
                self._close(inst, pos, hit[1], hit[0], exchange_side=True)
                pos = None
            elif inst["params"].get("trail") and pos.get("source") != "tradingview":
                new = max(pos["stop"], h - pos["d"]) if pos["side"] == 1 else min(pos["stop"], l + pos["d"])
                if new != pos["stop"]:
                    pos["stop"] = new
                    self._sync_stop(inst, pos)
                self.s.set_position(inst["id"], pos)
            else:
                self.s.set_position(inst["id"], pos)
        side_arr, dist_arr = STRATEGIES[inst["strategy"]]["fn"](hist, inst["params"])
        sig, d = side_arr[-1], dist_arr[-1]
        st["signal"] = sig
        if not sig or not d:
            return
        pos = self.s.positions().get(inst["id"])
        if pos and pos["side"] != sig and inst["params"].get("reverse"):
            self._close(inst, pos, c, "reverse")
            pos = None
        if pos is None:
            if not inst.get("enabled", True):
                return                          # watch-only: never open a trade
            if sig == -1 and inst["market"] in ("crypto", "stock"):
                return
            ok, msg = self.open(inst, sig, c, d, source="engine", hist=hist)
            self.s.event("trade" if ok else "skip", "%s %s: %s" % (inst["symbol"], "LONG" if sig == 1 else "SHORT", msg))
            self.s.think(inst, "decision" if ok else "skip", "%s signal on %s %s: %s" % ("Long" if sig == 1 else "Short", inst["symbol"], inst["tf"], msg))

    # ------------------------------------------------------------------ orders
    def open(self, inst, *a, **k):
        with self.lock:
            return self._open(inst, *a, **k)

    def _open(self, inst, side, ref_price, d, source="engine", tp=None, hist=None, stop=None, costs=None, risk_pct=None):
        """Risk-checked entry. Returns (ok, message). `costs` = dict(in, out_target, out_stop) as fractions of notional for styles with their own
        fee model (the scalper's limit orders); every cost is charged once, inside the fill price."""
        cfg = self.s.cfg
        r = cfg["risk"]
        if self.halted():
            return False, "trading halted (reset to resume)"
        poss = self.s.positions()
        if inst["id"] in poss:
            return False, "already in position"
        if source != "scalp" and len([p for p in poss.values() if p.get("source") != "scalp"]) >= r["max_positions"]:       # scalps have their own small book
            return False, "max positions reached"
        if self.s.get("daily_block"):
            return False, "daily loss limit hit"
        eq = self.equity()
        mc = MARKET_COSTS[inst["market"]]
        fee = mc["fee"] + mc["slip"]
        fee_in = costs["in"] if costs else fee
        fee_rt = (costs["in"] + costs["out_stop"]) if costs else 2 * fee
        px = ref_price * (1 + fee_in * side)
        if stop is not None:
            d = abs(px - stop)
        if d <= 0:
            return False, "bad stop distance"
        qty = min(eq * (risk_pct if risk_pct is not None else r["per_trade_pct"]) / 100 / (d + fee_rt * px), eq * min(r["max_leverage_per_pos"], mc["max_lev"]) / px)      # risk budget covers fees + slippage both ways, so a stop-out costs what you set
        gross = sum(p["qty"] * p.get("mark", p["entry"]) for p in poss.values())
        room = eq * r["max_total_leverage"] - gross
        qty = min(qty, room / px)
        if qty <= 0:
            return False, "no room under the leverage / exposure limits"
        lot = self._lot(inst)
        if lot:
            step, mn = lot
            q = math.floor(qty / step + 1e-9) * step
            if q < mn:
                need = mn * d / (r["per_trade_pct"] / 100)
                return False, "too small for this account: exchange minimum order is %g and this setup needs ~$%s of equity at %g%% risk" % (
                    mn, format(int(need), ","), r["per_trade_pct"])
            qty = round(q, 8)
        if qty * px < 5:
            return False, "below the $5 minimum order value"
        ok, why = rules.entry_allowed(self.s, inst, side, qty * (d + fee_rt * px), source, eq, poss)
        if not ok:
            return False, why
        ai_note, ai_p = "", None
        if source == "engine" and hist and cfg["ai"]["mode"] in ("veto", "advise"):
            r = analyst.assess(self.s, inst, side, hist, self._btc(inst), poss, eq, self.fetch)
            if not r["take"]:
                return False, "AI veto: " + r["note"]
            ai_note, ai_p = (" | " + r["note"]) if r["note"] else "", r["p"]
        stop_px = px - side * d
        if self.live():
            if inst["market"] != "futures" or not self.broker():
                return False, "exchange mode needs API keys in Settings and a futures instrument (forex: paper mode only)"
            b = self.broker()
            qty = b.round_qty(inst["symbol"], qty)
            if qty <= 0:
                return False, "below exchange min size"
            try:
                b.set_leverage(inst["symbol"], max(1, int(min(r["max_leverage_per_pos"], mc["max_lev"]))))
                px = b.open(inst["symbol"], side, qty, stop_px) or px
            except Exception as e:
                return False, "broker error: " + str(e)[:100]
        pos = dict(inst=inst["id"], symbol=inst["symbol"], market=inst["market"], side=side, qty=qty, entry=px, stop=stop_px, tp=tp, d=d,
                   t_open=now_ms(), fee_in=ref_price * qty * fee_in, fund=0.0, source=source, mark=ref_price, mode=cfg["mode"], ai_p=ai_p)
        pos["acct"] = 2
        if costs:
            pos.update(cost_out_target=costs["out_target"], cost_out_stop=costs["out_stop"])
        self.s.set_position(inst["id"], pos)                       # entry cost already sits in the fill price: no second deduction from cash
        if source != "scalp":
            self.s.think(inst, "open", "Opened %s %s at %.6g, stop %.6g%s, risking $%.2f" % ("LONG" if side == 1 else "SHORT", inst["symbol"], px, stop_px, (", target %.6g" % tp) if tp else "", qty * d))
        return True, "opened %.5g @ %.6g stop %.6g (risk $%.2f)%s" % (qty, px, stop_px, qty * (d + fee_rt * px), ai_note)

    def _close(self, inst, *a, **k):
        with self.lock:
            return self._close_locked(inst, *a, **k)

    def _close_locked(self, inst, pos, ref_price, reason, exchange_side=False):
        mc = MARKET_COSTS[inst["market"]]
        fee = mc["fee"] + mc["slip"]
        if reason == "target" and pos.get("cost_out_target") is not None:        # a resting limit order that got hit pays the maker fee
            fee = pos["cost_out_target"]
        elif pos.get("cost_out_stop") is not None:
            fee = pos["cost_out_stop"]
        px = ref_price * (1 - fee * pos["side"])
        if self.live() and not exchange_side and self.broker():
            try:
                px = self.broker().close(inst["symbol"], pos["side"], pos["qty"]) or px
            except Exception as e:
                self.s.event("error", "close failed %s: %s" % (inst["symbol"], str(e)[:100]))
        gross = pos["side"] * (px - pos["entry"]) * pos["qty"]
        fees = pos.get("fee_in", 0) + abs(ref_price - px) * pos["qty"]          # reporting only: both sides' costs are already inside entry and exit prices
        pnl = gross - pos.get("fund", 0)
        self.s.put("cash", self.cash() + gross - pos.get("fund", 0))
        self.s.add_trade(inst=inst["id"], symbol=inst["symbol"], side=pos["side"], qty=pos["qty"], entry=pos["entry"], exit=px,
                         pnl=pnl, fees=fees + pos.get("fund", 0), t_open=pos["t_open"], t_close=now_ms(), reason=reason,
                         source=pos.get("source"), mode=pos.get("mode"), ai_p=pos.get("ai_p"))
        self.s.set_position(inst["id"], None)
        if pos.get("source") != "scalp":                                         # a scalper makes hundreds of trades a day: they live in the journal, not the activity feed
            self.s.event("close", "%s closed (%s) %+.2f" % (inst["symbol"], reason, pnl))
            self.s.think(inst, "close", "Closed %s (%s): %s" % (inst["symbol"], reason, ("+" if pnl >= 0 else "-") + "$%.2f" % abs(pnl)))

    def _sync_stop(self, inst, pos):
        if self.live() and self.broker() and inst["market"] == "futures":
            try:
                self.broker().set_stop(inst["symbol"], pos["side"], pos["stop"])
            except Exception as e:
                self.s.event("error", "stop update failed %s: %s" % (inst["symbol"], str(e)[:100]))

    def close_by_id(self, inst_id, reason="manual"):
        if inst_id not in self.s.positions():                      # a coin id closes all of that coin's trendmix sleeves
            kids = [k for k in self.s.positions() if k.startswith(inst_id + "#")]
            return bool(kids) and all([self.close_by_id(k, reason) for k in kids])
        pos = self.s.positions().get(inst_id)
        inst = self._inst_for(inst_id)
        if not pos or not inst:
            return False
        px = (self.status.get(inst_id) or {}).get("price") or pos.get("mark") or pos["entry"]
        self._close(inst, pos, px, reason)
        return True

    def _inst_for(self, inst_id):
        """Config instrument, a trendmix sleeve ('<id>#<name>'), or the pseudo-instrument a day-trading position lives under ('day:' + base id)."""
        for style in ("scalp", "smart"):                              # '<style>:<watchlist id>'
            if inst_id.startswith(style + ":"):
                b = next((i for i in self.s.cfg["instruments"] if i["id"] == inst_id[len(style) + 1:]), None)
                return self.style_inst(style, b) if b else None
        base = inst_id[4:] if inst_id.startswith("day:") else inst_id
        parent = base.partition("#")[0]
        b = next((i for i in self.s.cfg["instruments"] if i["id"] == parent), None)
        if b and inst_id.startswith("day:"):
            return dict(b, id=inst_id, tf=self.s.cfg["day"]["tf"], strategy="aiday", params={"trail": 1}, enabled=True)
        if b and "#" in base:
            return next((s for s in self._sleeves(dict(b, enabled=True)) if s["id"] == base), None)
        return b

    def style_inst(self, style, base):
        """Pseudo-instrument a scalper / smart-money position lives under: '<style>:' + the market's watchlist id."""
        tf = {"scalp": "1m", "smart": self.s.cfg["smart"]["tf"]}[style]
        return dict(base, id="%s:%s" % (style, base["id"]), tf=tf, strategy=style, params={"trail": 0}, enabled=True)

    # ------------------------------------------------------------------ AI day trading
    def _day_cycle(self):
        d = self.s.cfg["day"]
        picks = sorted([i for i in self.s.cfg["instruments"] if i.get("enabled", True)], key=lambda i: not i.get("fav"))[:int(d.get("markets", 6))]
        for pid, pos in list(self.s.positions().items()):             # live price checks for open day trades every cycle
            if pid.startswith("day:"):
                self._day_price_check(pid, pos)
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(lambda i: self._safe(self._day_one, i, d), picks))

    def _safe(self, fn, *a):
        try:
            fn(*a)
        except Exception as e:
            self.s.event("error", "day trading: " + str(e)[:100])

    def _day_price_check(self, pid, pos):
        inst = self._inst_for(pid)
        if not inst:
            return
        try:
            px_ = self.price(inst["market"], inst["symbol"])
        except Exception:
            return
        pos["mark"] = px_
        self.s.set_position(pid, pos)
        sd = pos["side"]
        if (sd == 1 and px_ <= pos["stop"]) or (sd == -1 and px_ >= pos["stop"]):
            self._close(inst, pos, px_, "stop", exchange_side=True)
        elif pos.get("tp") and ((sd == 1 and px_ >= pos["tp"]) or (sd == -1 and px_ <= pos["tp"])):
            self._close(inst, pos, px_, "target", exchange_side=True)

    def _day_one(self, base, d):
        tf, tfms, now = d["tf"], data.TF_MS[d["tf"]], now_ms()
        did = "day:" + base["id"]
        last = self.s.get("day_ts:" + did)
        if last is not None and now < last + 2 * tfms + 4000:
            return
        hist = self.fetch(base["market"], base["symbol"], tf, 300)
        if len(hist) < 120 or (last is not None and hist[-1][0] <= last):
            return
        self.s.put("day_ts:" + did, hist[-1][0])
        inst = self._inst_for(did)
        self.day_stats["scans"] += 1
        self.day_stats["last"] = now
        pos = self.s.positions().get(did)
        c = hist[-1]
        if pos:
            pos["mark"] = c[4]
            held = (c[0] - pos["t_open"]) / tfms
            new = max(pos["stop"], c[2] - pos["d"]) if pos["side"] == 1 else min(pos["stop"], c[3] + pos["d"])
            if new != pos["stop"]:
                pos["stop"] = new
                self.s.think(inst, "manage", "Trailing %s stop to %.6g" % (inst["symbol"], new))
                self._sync_stop(inst, pos)
            if held >= d.get("max_hold_bars", 24):
                self._close(inst, pos, c[4], "time")
            else:
                self.s.set_position(did, pos)
            return
        reasons = daytrader.candidates(hist)
        if not reasons:
            return
        self.day_stats["candidates"] += 1
        self.s.think(inst, "candidate", "%s: %s" % (inst["symbol"], "; ".join(reasons)))
        if self.halted() or self.s.get("daily_block"):
            return self.s.think(inst, "skip", "Not trading: safety limit active")
        poss = self.s.positions()
        ok, why = rules.entry_allowed(self.s, inst, 0, None, "day", self.equity(), poss)
        if not ok:
            return self.s.think(inst, "skip", "Not trading %s: %s" % (inst["symbol"], why))
        dec = daytrader.decide(self.s, inst, hist, self._btc_tf(inst, tf), poss, self.equity(), self.fetch, reasons, tf)
        self.s.think(inst, "decision", "%s %s%% - %s%s" % (dec["action"].upper(), int(dec["conf"]), dec["why"] or "no reason given", " ($%.4f, %d tokens)" % (dec["usd"], dec["tokens"]) if dec["source"] == "api" else ""))
        if dec["action"] == "flat":
            return
        if dec["conf"] < d.get("min_conf", 50):
            return self.s.think(inst, "skip", "Passed on %s: confidence %d%% is under your %d%% minimum" % (inst["symbol"], int(dec["conf"]), int(d.get("min_conf", 50))))
        side = 1 if dec["action"] == "long" else -1
        if side == -1 and inst["market"] in ("crypto", "stock"):
            return self.s.think(inst, "skip", "Shorting isn't available for %s" % inst["symbol"])
        atr = daytrader.stats(hist)["atr"]
        price = c[4]
        ok, msg = self.open(inst, side, price, dec["stop_atr"] * atr, source="day", tp=price + side * dec["target_atr"] * atr)
        if ok:
            self.day_stats["trades"] += 1
            self.s.event("trade", "DAY %s %s: %s" % (inst["symbol"], "LONG" if side == 1 else "SHORT", msg))
        else:
            self.s.think(inst, "skip", "Risk check: " + msg)

    def _btc_tf(self, inst, tf):
        if inst["symbol"].startswith("BTC") or inst["market"] != "futures":
            return None
        try:
            return self.fetch("futures", "BTCUSDT", tf, 120)
        except Exception:
            return None

    def flatten_all(self, reason="kill switch"):
        n = 0
        for iid in list(self.s.positions()):
            n += bool(self.close_by_id(iid, reason))
        return n

    def kill(self):
        """Emergency: flatten everything, halt entries, stop the engine."""
        n = self.flatten_all("kill switch")
        self.s.put("halted", True)
        self.stop()
        self.s.event("error", "KILL SWITCH: flattened %d positions, trading halted" % n)
        return n

    def resume(self):
        self.s.put("halted", False)
        self.s.put("daily_block", False)
        self.s.put("peak", self.equity())
        self.s.event("info", "Trading resumed")

    # ------------------------------------------------------------------ risk guards
    def _risk_guards(self):
        r = self.s.cfg["risk"]
        eq = self.equity()
        day = time.strftime("%Y-%m-%d", time.gmtime())
        d = self.s.get("day") or {}
        if d.get("d") != day:
            d = {"d": day, "eq": eq}
            self.s.put("day", d)
            self.s.put("daily_block", False)
        if not self.s.get("daily_block") and (d["eq"] - eq) / d["eq"] * 100 >= r["daily_loss_limit_pct"]:
            self.s.put("daily_block", True)
            self.s.event("error", "Daily loss limit hit: new entries blocked until 00:00 UTC")
        peak = max(self.s.get("peak", eq), eq)
        self.s.put("peak", peak)
        if not self.halted() and (peak - eq) / peak * 100 >= r["max_drawdown_halt_pct"]:
            self.s.event("error", "Max drawdown halt hit (%.1f%%)" % ((peak - eq) / peak * 100))
            self.kill()

    def _lot(self, inst):
        """(qty step, min qty) from Bybit's public instrument rules so paper fills obey real lot sizes. None if unknown."""
        if inst["market"] != "futures":
            return None
        c = self.__dict__.setdefault("_lots", {})
        if inst["symbol"] not in c:
            try:
                from .bybit import Bybit
                f = Bybit("", "", "real")._step(inst["symbol"])
                c[inst["symbol"]] = (f[0], f[2])
            except Exception:
                c[inst["symbol"]] = None
        return c[inst["symbol"]]

    def affordability(self):
        """Which crypto coins can this balance actually trade? Exchanges set a minimum order size per coin; at 0.3% risk a typical 4-hour stop (about 8% away)
        needs this much equity to afford one minimum lot. Cached for an hour. -> dict(equity, risk_pct, ok=[symbols], blocked=[(symbol, equity needed)])"""
        c = getattr(self, "_afford", None)
        eq = self.equity()
        if c and time.time() - c[0] < 3600 and abs(c[1]["equity"] - eq) < eq * 0.02 and c[1]["risk_pct"] == self.s.cfg["risk"]["per_trade_pct"]:
            return c[1]
        risk = self.s.cfg["risk"]["per_trade_pct"] / 100
        ok, blocked = [], []
        for i in self.s.cfg["instruments"]:
            if i["market"] != "futures" or not i.get("enabled", True):
                continue
            px = (self.status.get(i["id"]) or {}).get("price")
            lot = self._lot(i)
            if not px or not lot:
                continue
            need = max(lot[1] * px, 5.0) * (0.08 + 0.0016) / risk                # equity at which one minimum order risks exactly the budget
            (ok if need <= eq else blocked).append((i["symbol"], round(need, -1) if need > 100 else round(need)))
        out = dict(equity=eq, risk_pct=risk * 100, ok=[s for s, _ in ok], blocked=sorted(blocked, key=lambda x: x[1]))
        self._afford = (time.time(), out)
        return out

    def set_account(self, amount):
        """Fresh paper account with a chosen starting balance (wipes paper history). Not allowed in exchange mode."""
        if self.live():
            raise ValueError("balance comes from the exchange in Exchange mode")
        amount = float(amount)
        if not 10 <= amount <= 10_000_000:
            raise ValueError("balance must be between $10 and $10,000,000")
        with self.lock:
            if abs(amount - self.s.cfg["start_equity"]) < 1e-9 and not self.s.positions() and not self.s.trades(1):
                return                                  # same balance on an untouched account: nothing to reset
            was = self.running
            self.stop()
            self.s.reset_paper()
            self.s.save_cfg({"start_equity": amount})
            self.s.put("cash", amount)
            self.status.clear()
            self.s.event("info", "Paper account reset to $%s" % format(amount, ",.2f"))
        if was:
            self.start()

    def _btc(self, inst):  # noqa: swing-mode BTC regime
        """BTC 4h candles for the market-regime features (cached 5 min). None for BTC itself or on failure."""
        if inst["symbol"].startswith("BTC") or inst["market"] != "futures":
            return None
        c = getattr(self, "_btc_cache", None)
        if not c or time.time() - c[0] > 300:
            try:
                c = (time.time(), self.fetch("futures", "BTCUSDT", "4h", 400))
            except Exception:
                return None
            self._btc_cache = c
        return c[1]

    # ------------------------------------------------------------------ TradingView webhook
    def handle_signal(self, d):
        """Payload: {secret, action: buy|sell|close, symbol, price?, stop?, tp?}. Returns (http_code, dict)."""
        import hmac
        if not hmac.compare_digest(str(d.get("secret", "")), self.s.cfg["webhook_secret"]):
            return 403, {"error": "bad secret"}
        sym = str(d.get("symbol", "")).upper().replace(".P", "").replace("BINANCE:", "")
        act = d.get("action")
        if act not in ("buy", "sell", "close"):
            return 400, {"error": "bad action"}
        inst = next((i for i in self.s.cfg["instruments"] if i["symbol"].replace("=X", "").upper() == sym.replace("=X", "")), None)
        if not inst:
            return 404, {"error": "symbol %s not configured" % sym}
        if act == "close":
            return 200, {"closed": self.close_by_id(inst["id"], "tradingview")}
        try:
            price = float(d.get("price") or (self.status.get(inst["id"]) or {}).get("price") or self.price(inst["market"], inst["symbol"]))
            stop = float(d["stop"])
            tp = float(d["tp"]) if d.get("tp") else None
        except (KeyError, ValueError, TypeError):
            return 400, {"error": "need stop (and a price)"}
        side = 1 if act == "buy" else -1
        if (side == 1 and not stop < price) or (side == -1 and not stop > price):
            return 400, {"error": "stop on wrong side of price"}
        ok, msg = self.open(inst, side, price, abs(price - stop), source="tradingview", tp=tp, stop=None)
        self.s.event("trade" if ok else "skip", "TV %s %s: %s" % (act, inst["symbol"], msg))
        return 200, {"ok": ok, "msg": msg}

    # ------------------------------------------------------------------ dashboard state
    def snapshot(self):
        eq, cash = self.equity(), self.cash()
        poss = self.s.positions()
        trades = self.s.trades(1000)
        wins = [t for t in trades if t["pnl"] > 0]
        d = self.s.get("day") or {"eq": eq}
        peak = self.s.get("peak", eq)
        start = self.s.cfg["start_equity"]
        return dict(
            running=self.running, halted=self.halted(), daily_block=bool(self.s.get("daily_block")), mode=self.s.cfg["mode"],
            equity=eq, cash=cash, start_equity=start, total_return_pct=(eq / start - 1) * 100,
            day_pnl=eq - d["eq"], day_pnl_pct=(eq / d["eq"] - 1) * 100 if d["eq"] else 0, drawdown_pct=(peak - eq) / peak * 100 if peak else 0,
            open_risk=sum(abs(p["mark"] - p["stop"]) * p["qty"] if p.get("mark") else p["d"] * p["qty"] for p in poss.values()),
            trades=len(trades), win_rate=len(wins) / len(trades) * 100 if trades else 0,
            profit_factor=(sum(t["pnl"] for t in wins) / -sum(t["pnl"] for t in trades if t["pnl"] <= 0)) if any(t["pnl"] <= 0 for t in trades) and sum(t["pnl"] for t in trades if t["pnl"] <= 0) < 0 else None,
            positions=[dict(p, id=k, upnl=self.upnl(p), risk_now=abs(p.get("mark", p["entry"]) - p["stop"]) * p["qty"]) for k, p in poss.items()],
            status={k: {a: b for a, b in v.items() if a != "spark"} for k, v in self.status.items()}, last_cycle=self.last_cycle, now=now_ms(),
            day=dict(self.day_stats, enabled=bool(self.s.cfg["day"]["enabled"]), tf=self.s.cfg["day"]["tf"]))
