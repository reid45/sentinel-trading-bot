"""Runs the smart-money day-trading playbook (tradebot/playbook.py) inside the engine: scans on each new closed candle, checks liquidity first, waits for the
full confirmation sequence, optionally asks the AI for a final yes/no with the setup's measured track record, then opens a paper trade with a structure stop
and a liquidity-pool target. Every wait and every skip is recorded with its reason so the Strategies page can say what it is waiting for."""
import time
from . import data, daytrader, playbook

DEFAULTS = playbook.DEFAULTS


class Smart:
    def __init__(self, engine):
        self.e = engine
        self.seen = {}                 # (kind, symbol) -> open time of the last bar scanned
        self.status = {}               # symbol -> (text, timestamp ms): what it is waiting for, shown on the Strategies page
        self.vol_days = {}             # stock symbol -> {date: first-15-minute volume}

    @property
    def s(self):
        return self.e.s

    def cfg(self):
        return self.s.cfg["smart"]

    def _say(self, sym, text):
        self.status[sym] = (text, int(time.time() * 1000))

    def _base(self, market, sym):
        return next((i for i in self.s.cfg["instruments"] if i["market"] == market and i["symbol"] == sym), None)

    def _opened_today(self):
        start = int(time.time() // 86400 * 86400 * 1000)
        return sum(1 for t in self.s.trades(500) if t.get("source") == "smart" and t["t_open"] >= start) + sum(1 for p in self.s.positions().values() if p.get("source") == "smart")

    # ------------------------------------------------------------------ main (called every engine cycle)
    def tick(self):
        c = self.cfg()
        if not c["enabled"] or self.e.live():
            return
        self._manage(c)
        if self.e.halted() or self.s.get("daily_block"):
            return
        if c["crypto"]:
            self._crypto(c)
        if c["stocks"]:
            self._stocks(c)

    # ------------------------------------------------------------------ open positions
    def _manage(self, c):
        for key, pos in list(self.s.positions().items()):
            if pos.get("source") != "smart":
                continue
            inst = self.e._inst_for(key)
            if not inst:
                continue
            try:
                px = self.e.price(inst["market"], inst["symbol"])
            except Exception:
                continue
            pos["mark"] = px
            self.s.set_position(key, pos)
            sd, now = pos["side"], int(time.time() * 1000)
            if (sd == 1 and px <= pos["stop"]) or (sd == -1 and px >= pos["stop"]):
                self.e._close(inst, pos, px, "stop", exchange_side=True)
            elif pos.get("tp") and ((sd == 1 and px >= pos["tp"]) or (sd == -1 and px <= pos["tp"])):
                self.e._close(inst, pos, px, "target", exchange_side=True)
            elif inst["market"] == "stock" and (not playbook.market_open_et(now / 1000)[0] or playbook.market_open_et(now / 1000)[1] >= 385):
                self.e._close(inst, pos, px, "time", exchange_side=True)           # flat before the close: no overnight risk
            elif inst["market"] != "stock" and now - pos["t_open"] >= c["max_hold_bars"] * data.TF_MS[c["tf"]]:
                self.e._close(inst, pos, px, "time", exchange_side=True)

    # ------------------------------------------------------------------ crypto
    def _crypto(self, c):
        try:
            vols = playbook.crypto_volumes()
        except Exception as ex:
            self._say("crypto", "liquidity data unavailable: %s" % str(ex)[:60])
            return
        futs = [i for i in self.s.cfg["instruments"] if i["market"] == "futures" and i.get("enabled", True)]
        futs.sort(key=lambda i: -vols.get(i["symbol"], 0))
        tfms = data.TF_MS[c["tf"]]
        for base in futs[:int(c["markets"])]:
            sym = base["symbol"]
            try:
                self._crypto_one(base, sym, c, tfms, vols)
            except Exception as ex:
                self._say(sym, "error: %s" % str(ex)[:70])

    def _crypto_one(self, base, sym, c, tfms, vols):
        inst = self.e.style_inst("smart", base)
        if inst["id"] in self.s.positions():
            self._say(sym, "in a trade")
            return
        last, now = self.seen.get(("c", sym)), int(time.time() * 1000)
        if last is not None and now < last + 2 * tfms + 4000:
            return
        h15 = self.e.fetch("futures", sym, c["tf"], 300)
        if len(h15) < 150 or (last is not None and h15[-1][0] <= last):
            return
        self.seen[("c", sym)] = h15[-1][0]
        liq = playbook.liquidity_crypto(sym, c)
        if not liq["ok"]:
            self._say(sym, "skipped: " + liq["why"])
            return
        if self._opened_today() >= c["max_trades_day"]:
            self._say(sym, "waiting: today's limit of %d trades is used" % c["max_trades_day"])
            return
        h4 = self.e.fetch("futures", sym, "4h", 120)
        setup, msg = playbook.crypto_setup(h15, h4, c)
        if not setup:
            self._say(sym, msg)
            return
        side = setup["side"]
        try:
            fp, bk = playbook.footprint(sym), playbook.book(sym)
        except Exception:
            fp, bk = {}, None
        votes = playbook.footprint_votes(fp, bk, side, h15[-1][4] / h15[-5][4] - 1)
        score = setup["score"] + min(2, len(votes)) + 1                            # +1 for passing the liquidity check
        reasons = setup["reasons"] + votes
        if score < c["min_score"]:
            self._say(sym, "setup is forming but only %d of the %d confirmations are in (footprint: %s)" % (score, c["min_score"], "; ".join(votes) or "no big-player clue agrees"))
            self.s.think(inst, "skip", "%s: smart-money setup (%s) passed on, confirmation score %d/%d" % (sym, "long" if side == 1 else "short", score, c["min_score"]))
            return
        self.s.think(inst, "candidate", "%s: %s" % (sym, "; ".join(reasons)))
        if c["use_ai"]:
            dec = daytrader.decide(self.s, inst, h15, None, self.s.positions(), self.e.equity(), self.e.fetch, ["smart-money playbook " + ("long" if side == 1 else "short") + " setup: " + "; ".join(reasons)], c["tf"])
            if dec["source"] in ("api", "memo"):
                self.s.think(inst, "decision", "AI sign-off: %s %s%% - %s ($%.4f)" % (dec["action"].upper(), int(dec["conf"]), dec["why"] or "no reason given", dec["usd"]))
                if dec["action"] != ("long" if side == 1 else "short") or dec["conf"] < self.s.cfg["day"].get("min_conf", 50):
                    self._say(sym, "setup confirmed by rules, passed by the AI: " + (dec["why"] or dec["action"]))
                    return
        ok, why = self.e.open(inst, side, setup["entry_ref"], 0, source="smart", stop=setup["stop"], tp=setup["target"], risk_pct=c["risk_pct"])
        if ok:
            self._say(sym, "entered " + ("long" if side == 1 else "short"))
            self.s.event("trade", "SMART %s %s: %s" % (sym, "LONG" if side == 1 else "SHORT", why))
        else:
            self._say(sym, "setup confirmed but blocked: " + why)
            self.s.think(inst, "skip", "Not entering %s: %s" % (sym, why))

    # ------------------------------------------------------------------ stocks (US regular session)
    def _stocks(self, c):
        is_open, mins = playbook.market_open_et()
        if not is_open:
            self._say("stocks", "US market is closed")
            return
        if mins < 20:
            self._say("stocks", "waiting for the first 15 minutes to finish")
            return
        spy = None
        for sym in c["stock_symbols"]:
            try:
                spy = spy or self._today_bars("SPY")
                self._stock_one(sym, c, spy)
            except Exception as ex:
                self._say(sym, "error: %s" % str(ex)[:70])

    def _today_bars(self, sym):
        rows = self.e.fetch("stock", sym, "5m", 600)
        out = {}
        for t, o, h, l, cl, v in rows:
            lt = time.gmtime(t / 1000 - self._off(t) * 3600)
            m = lt.tm_hour * 60 + lt.tm_min
            if 570 <= m < 960:
                out.setdefault((lt.tm_year, lt.tm_mon, lt.tm_mday), []).append((m, o, h, l, cl, v))
        return out

    @staticmethod
    def _off(t_ms):
        import calendar
        g = time.gmtime(t_ms / 1000)

        def nth(month, n):
            first = calendar.monthrange(g.tm_year, month)[0]
            return 1 + (6 - first) % 7 + 7 * (n - 1)
        return 4 if calendar.timegm((g.tm_year, 3, nth(3, 2), 7, 0, 0)) <= t_ms / 1000 < calendar.timegm((g.tm_year, 11, nth(11, 1), 6, 0, 0)) else 5

    def _stock_one(self, sym, c, spy):
        base = self._base("stock", sym)
        if not base:
            self._say(sym, "add %s to your markets (+ button) to let this style trade it" % sym)
            return
        inst = self.e.style_inst("smart", base)
        if inst["id"] in self.s.positions() or any(t.get("source") == "smart" and t["symbol"] == sym and t["t_open"] >= int(time.time() // 86400 * 86400 * 1000) for t in self.s.trades(100)):
            self._say(sym, "done for today")
            return
        days = self._today_bars(sym)
        if not days:
            return
        today = max(days)
        bars = days[today]
        stamp = (today, bars[-1][0])
        if self.seen.get(("s", sym)) == stamp:
            return
        self.seen[("s", sym)] = stamp
        liq = playbook.liquidity_stock(sym, self.e.fetch, c)
        if not liq["ok"]:
            self._say(sym, "skipped: " + liq["why"])
            return
        prior = [sum(b[5] for b in v[:3]) for d, v in sorted(days.items()) if d < today and len(v) >= 70]
        spy_today = spy.get(max(spy)) if spy else None
        setup, msg = playbook.stock_setup(bars, prior, spy_today, c)
        if not setup:
            self._say(sym, msg)
            return
        self.s.think(inst, "candidate", "%s: %s" % (sym, "; ".join(setup["reasons"])))
        ok, why = self.e.open(inst, 1, setup["entry_ref"], 0, source="smart", stop=setup["stop"], tp=setup["target"], risk_pct=c["risk_pct"])
        self._say(sym, "entered long" if ok else "setup confirmed but blocked: " + why)
        if ok:
            self.s.event("trade", "SMART %s LONG: %s" % (sym, why))
        else:
            self.s.think(inst, "skip", "Not entering %s: %s" % (sym, why))
