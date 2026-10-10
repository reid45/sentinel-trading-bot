"""High-frequency scalper (paper simulation). Many small, short trades on 1-minute candles, entered with resting LIMIT orders (maker fees),
exited at a limit target (maker) or a stop (taker + slippage). The fill model is the conservative one from research/scalp_study.py:
a limit order fills only when price trades THROUGH it, same-bar ties go against us, stops pay taker fees plus slippage.

Honest status: in 75 days of BTC / ETH / SOL / XRP 1-minute data no setting was profitable (see docs/STRATEGY_STUDY.md). The break-even
fee calculator and the auto-pause exist so you can see for yourself, with your own fee tier, whether it earns anything. Paper only."""
import time
from . import data, indicators as I

DEFAULTS = dict(enabled=False, markets=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"], maker_bps=2.0, taker_bps=5.0, slip_bps=1.0,
                k=2.0, rsi=20, off=0.3, wait=3, tgt=0.6, stop=2.0, hold=10,
                risk_pct=0.1, max_trades_day=300, daily_loss_pct=1.0, auto_pause=True, auto_pause_trades=40)


def signal(hist, c):
    """Mean-reversion fade at the close of the last bar: price beyond the Bollinger band AND RSI(3) extreme -> resting limit order slightly further out.
    -> (side, limit_price, atr) or None."""
    n = len(hist)
    if n < 40:
        return None
    h, l, cl = [x[2] for x in hist], [x[3] for x in hist], [x[4] for x in hist]
    a = I.atr(h, l, cl, 14)[-1]
    ma = sum(cl[-20:]) / 20
    sd = (sum((x - ma) ** 2 for x in cl[-20:]) / 20) ** 0.5
    rs = I.rsi(cl, 3)[-1]
    if not a or not sd or rs is None:
        return None
    if cl[-1] < ma - c["k"] * sd and rs < c["rsi"]:
        return 1, cl[-1] - c["off"] * a, a
    if cl[-1] > ma + c["k"] * sd and rs > 100 - c["rsi"]:
        return -1, cl[-1] + c["off"] * a, a
    return None


class Scalper:
    def __init__(self, engine):
        self.e = engine
        self.pend = {}            # symbol -> dict(side, limit, atr, placed_bar)
        self.seen = {}            # symbol -> open time of the last bar processed
        self.day = {"d": None, "n": 0}
        self.last_note = 0.0

    # ------------------------------------------------------------------ helpers
    @property
    def s(self):
        return self.e.s

    def cfg(self):
        return self.s.cfg["scalp"]

    @staticmethod
    def costs(c):
        mk, tk, sl = c["maker_bps"] / 1e4, c["taker_bps"] / 1e4, c["slip_bps"] / 1e4
        return {"in": mk, "out_target": mk, "out_stop": tk + sl}

    def _base(self, sym):
        return next((i for i in self.s.cfg["instruments"] if i["market"] == "futures" and i["symbol"] == sym), None)

    def _today(self):
        d = time.strftime("%Y-%m-%d", time.gmtime())
        if self.day["d"] != d:
            start = int(time.time() // 86400 * 86400 * 1000)
            self.day = {"d": d, "n": sum(1 for t in self.s.trades(2000) if t.get("source") == "scalp" and t["t_open"] >= start) + sum(1 for p in self.s.positions().values() if p.get("source") == "scalp"),
                        "pnl": sum(t["pnl"] for t in self.s.trades(2000) if t.get("source") == "scalp" and t["t_close"] >= start), "blocked": False}
        return self.day

    # ------------------------------------------------------------------ main tick (every few seconds)
    def tick(self):
        c = self.cfg()
        if not c["enabled"] or self.e.halted() or self.e.live():
            return
        if self.s.get("daily_block"):
            return
        for sym in c["markets"]:
            try:
                self._one(sym, c)
            except Exception as ex:
                self.s.event("error", "scalper %s: %s" % (sym, str(ex)[:90]))
        self._note()

    def _one(self, sym, c):
        base = self._base(sym)
        if not base:
            return
        inst = self.e.style_inst("scalp", base)
        key = inst["id"]
        pos = self.s.positions().get(key)
        if pos:                                                       # protective stop on the live price, between candles
            try:
                px = self.e._cached(("p", "futures", sym), lambda: self.e.price("futures", sym))
            except Exception:
                px = None
            if px:
                pos["mark"] = px
                self.s.set_position(key, pos)
                if (pos["side"] == 1 and px <= pos["stop"]) or (pos["side"] == -1 and px >= pos["stop"]):
                    self.e._close(inst, pos, px, "stop", exchange_side=True)
                    self._after_close()
                    return
        last = self.seen.get(sym)
        now = int(time.time() * 1000)
        if last is not None and now < last + 2 * 60000 + 1500:         # a new closed 1m candle is not due yet
            return
        hist = self.e.fetch("futures", sym, "1m", 120)
        if len(hist) < 60:
            return
        if last is None:
            self.seen[sym] = hist[-1][0]
            return
        for k, bar in enumerate(hist):
            if bar[0] > last:
                self._bar(sym, inst, hist[:k + 1], c)
                self.seen[sym] = bar[0]

    def _bar(self, sym, inst, hist, c):
        t, o, h, l, cl, _ = hist[-1]
        key = inst["id"]
        costs = self.costs(c)
        pend = self.pend.get(sym)
        if pend:                                                       # 1) a resting limit order: did price trade through it?
            if t > pend["t"] + c["wait"] * 60000:
                self.pend.pop(sym, None)
            elif t > pend["t"] and ((pend["side"] == 1 and l < pend["limit"]) or (pend["side"] == -1 and h > pend["limit"])):
                self.pend.pop(sym, None)
                d = self._today()
                if d["n"] < c["max_trades_day"] and not d.get("blocked"):
                    sd, lim, a = pend["side"], pend["limit"], pend["atr"]
                    ok, msg = self.e.open(inst, sd, lim, 0, source="scalp", stop=lim - sd * c["stop"] * a, tp=lim + sd * c["tgt"] * a, costs=costs, risk_pct=c["risk_pct"])
                    if ok:
                        d["n"] += 1
                        p = self.s.positions().get(key)
                        if p:
                            p["bar_open"] = t
                            self.s.set_position(key, p)
        pos = self.s.positions().get(key)
        if pos:                                                        # 2) manage the open scalp on this closed candle (stop first on ties)
            self._manage(inst, pos, hist[-1], c)
            pos = self.s.positions().get(key)
        if not pos and sym not in self.pend:                           # 3) look for a new setup
            d = self._today()
            if d["n"] >= c["max_trades_day"] or d.get("blocked"):
                return
            sg = signal(hist, c)
            if sg:
                self.pend[sym] = dict(side=sg[0], limit=sg[1], atr=sg[2], t=t)

    def _manage(self, inst, pos, bar, c):
        t, o, h, l, cl, _ = bar
        sd = pos["side"]
        stop, tgt = pos["stop"], pos["tp"]
        if (sd == 1 and l <= stop) or (sd == -1 and h >= stop):
            self.e._close(inst, pos, min(stop, o) if sd == 1 else max(stop, o), "stop", exchange_side=True)
        elif (sd == 1 and h > tgt) or (sd == -1 and l < tgt):
            self.e._close(inst, pos, tgt, "target", exchange_side=True)
        elif (t - pos.get("bar_open", t)) / 60000 >= c["hold"]:
            self.e._close(inst, pos, cl, "time", exchange_side=True)
        else:
            pos["mark"] = cl
            self.s.set_position(inst["id"], pos)
            return
        self._after_close()

    # ------------------------------------------------------------------ guards, summaries
    def _after_close(self):
        c = self.cfg()
        d = self._today()
        last = [t for t in self.s.trades(400) if t.get("source") == "scalp"]
        since = self.s.get("scalp_since") or 0
        last = [t for t in last if t["t_close"] >= since]
        d["pnl"] = sum(t["pnl"] for t in last if t["t_close"] >= int(time.time() // 86400 * 86400 * 1000))
        eq = self.e.equity()
        if eq > 0 and -d["pnl"] / eq * 100 >= c["daily_loss_pct"] and not d.get("blocked"):
            d["blocked"] = True
            self.s.event("error", "Scalper hit its daily loss limit (%.1f%%): no new scalps until tomorrow" % c["daily_loss_pct"])
        n = int(c["auto_pause_trades"])
        if c["auto_pause"] and n and len(last) >= n:
            win = last[:n]
            avg = sum(t["pnl"] / (t["qty"] * t["entry"]) for t in win) / n * 1e4
            if avg < 0:
                self.s.save_cfg({"scalp": {"enabled": False}})
                self.s.event("error", "Scalper paused itself: its last %d trades averaged %+.1f bps after fees (it needs to be above 0). "
                                      "Lower your fees or change settings, then press Restart on the Strategies page." % (n, avg))

    def _note(self):
        if time.time() - self.last_note < 1800:
            return
        self.last_note = time.time()
        st = stats(self.s)
        if st["n"]:
            self.s.think({"id": "scalp", "symbol": ""}, "manage", "Scalper: %d trades so far, %.0f%% winners, %+.1f bps per trade after fees (fees %.1f bps)." % (st["n"], st["win"], st["net_bps"], st["fee_bps"]))


def stats(store):
    """Scorecard from the journal. Everything per trade in basis points of the money traded."""
    since = store.get("scalp_since") or 0
    tr = [t for t in store.trades(100000) if t.get("source") == "scalp" and t["t_close"] >= since]
    if not tr:
        return dict(n=0, win=0.0, net_bps=0.0, fee_bps=0.0, gross_bps=0.0, pnl=0.0, per_day=0.0, today=0, last=[])
    notional = [t["qty"] * t["entry"] for t in tr]
    net = [t["pnl"] / nv * 1e4 for t, nv in zip(tr, notional)]
    fee = [t["fees"] / nv * 1e4 for t, nv in zip(tr, notional)]
    span = max(1.0, (max(t["t_close"] for t in tr) - min(t["t_open"] for t in tr)) / 86400000)
    start = int(time.time() // 86400 * 86400 * 1000)
    return dict(n=len(tr), win=sum(1 for x in net if x > 0) / len(tr) * 100, net_bps=sum(net) / len(tr), fee_bps=sum(fee) / len(tr), gross_bps=(sum(net) + sum(fee)) / len(tr),
                pnl=sum(t["pnl"] for t in tr), per_day=len(tr) / span, today=sum(1 for t in tr if t["t_close"] >= start), last=[round(x, 1) for x in net[:20]])


def breakeven(st, c):
    """What would have to change for the scalper to break even, in plain numbers (None when there are too few trades to say)."""
    if st["n"] < 20:
        return None
    gap = -st["net_bps"]
    return dict(gap_bps=gap, fee_bps=st["fee_bps"], gross_bps=st["gross_bps"],
                msg=("Average trade: %+.1f bps before fees, %.1f bps of fees, %+.1f bps net. " % (st["gross_bps"], st["fee_bps"], st["net_bps"]) +
                     ("Fees would have to fall by %.1f bps per trade (about %.0f%% lower) to break even." % (gap, gap / max(st["fee_bps"], 1e-9) * 100) if gap > 0 else "Net positive at your fee settings.")))
