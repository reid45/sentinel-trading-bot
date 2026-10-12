"""Market-neutral momentum basket (paper). Once a day it ranks the coins by how far they moved over the last `lookback_h` hours, goes LONG the strongest `n`
and SHORT the weakest `n` with equal money on each leg, and holds until the next ranking. Long and short roughly cancel the market's direction, so what is left is
"strong coins keep outrunning weak ones". It does not predict the market and it does not trade fast: one ranking a day, about 12 small positions.

Evidence (research/xsec_study.py, research/xsec_robust.py; 38 coins, 3 years of hourly candles, costs charged on every change): lookbacks of 1 to 3 weeks with a daily
re-rank were positive; reversal and short lookbacks lost. A 14-day lookback with 6 coins a side made about +33% a year on the capital used (Sharpe 1.2, worst dip 26%) at
retail costs. Honest limits: the last six months were about flat, funding payments are not modelled, the coin list is today's liquid coins (survivorship), and it is the same
momentum family as the trend bot so bad weeks tend to arrive together. Paper only."""
import time
from concurrent.futures import ThreadPoolExecutor
from .backtest import MARKET_COSTS

DEFAULTS = dict(enabled=False, lookback_h=336, n=6, gross=1.0, rebalance_h=24, stop_pct=15.0)


class Basket:
    def __init__(self, engine):
        self.e = engine
        self.status = dict(msg="", t=0, ranked=[], next=0)

    @property
    def s(self):
        return self.e.s

    def cfg(self):
        return self.s.cfg["basket"]

    def universe(self):
        out = {}
        for i in self.s.cfg["instruments"]:
            if i["market"] == "futures" and "#" not in i["id"]:
                out.setdefault(i["symbol"], i)
        return out

    def legs(self):
        return {p["symbol"]: (pid, p) for pid, p in self.s.positions().items() if p.get("source") == "basket"}

    # ------------------------------------------------------------------ tick (called from the engine cycle)
    def tick(self):
        c = self.cfg()
        if not c["enabled"] or self.e.halted() or self.e.live():
            return
        self._marks(c)
        now = int(time.time() * 1000)
        last = self.s.get("basket_last") or 0
        self.status["next"] = last + int(c["rebalance_h"] * 3600000)
        if now >= self.status["next"]:
            self.rebalance(c, now)

    def _marks(self, c):
        """Refresh each leg's price (so the account shows its profit or loss) and enforce the wide protective stop."""
        for sym, (pid, pos) in list(self.legs().items()):
            inst = self.e._inst_for(pid)
            try:
                px = self.e.price("futures", sym)
            except Exception:
                px = None
            if not px or not inst:
                continue
            pos["mark"] = px
            self.s.set_position(pid, pos)
            if (pos["side"] == 1 and px <= pos["stop"]) or (pos["side"] == -1 and px >= pos["stop"]):
                self.e._close(inst, pos, px, "stop", exchange_side=True)

    # ------------------------------------------------------------------ ranking and rebalancing
    def rank(self, c):
        """-> list of (symbol, return over the lookback), weakest first. Uses every coin whose history is long enough."""
        L = int(c["lookback_h"])

        def one(sym):
            try:
                h = self.e.fetch("futures", sym, "1h", L + 6)
                return (sym, h[-1][4] / h[-1 - L][4] - 1) if len(h) > L else None
            except Exception:
                return None
        with ThreadPoolExecutor(6) as ex:
            rows = [r for r in ex.map(one, list(self.universe())) if r]
        return sorted(rows, key=lambda r: r[1])

    def rebalance(self, c, now):
        rules = self.s.cfg["rules"]
        if rules.get("direction", "both") != "both":
            self.status.update(msg="Paused: your trading rules allow %s trades only, and this style needs both longs and shorts." % rules["direction"], t=now)
            return
        if self.s.get("daily_block"):
            return
        n = int(c["n"])
        rows = self.rank(c)
        if len(rows) < 2 * n + 2:
            self.status.update(msg="Waiting for price history (%d coins ready, needs %d)." % (len(rows), 2 * n + 2), t=now)
            return
        longs = [r[0] for r in rows[-n:]]
        shorts = [r[0] for r in rows[:n]]
        target = {s: 1 for s in longs}
        target.update({s: -1 for s in shorts})
        uni = self.universe()
        cur = self.legs()
        closed = opened = 0
        for sym, (pid, pos) in cur.items():                            # leave what still belongs, close the rest
            if target.get(sym) != pos["side"]:
                inst = self.e._inst_for(pid)
                if inst:
                    self.e._close(inst, pos, pos.get("mark", pos["entry"]), "rebalance", exchange_side=True)
                    closed += 1
        keep = {s for s, (pid, p) in self.legs().items()}
        mc = MARKET_COSTS["futures"]
        fee_rt = 2 * (mc["fee"] + mc["slip"])
        skipped = []
        for sym, sd in target.items():
            if sym in keep:
                continue
            base = uni.get(sym)
            try:
                px = self.e.price("futures", sym)
            except Exception:
                px = None
            if not base or not px:
                skipped.append(sym)
                continue
            eq = self.e.equity()
            notional = eq * float(c["gross"]) / (2 * n)
            sp = float(c["stop_pct"]) / 100
            risk_pct = notional * (sp + fee_rt) / eq * 100            # sizes the leg to `notional` given the wide stop, costs included
            ok, msg = self.e.open(self.e.style_inst("basket", base), sd, px, px * sp, source="basket", risk_pct=risk_pct)
            if ok:
                opened += 1
            else:
                skipped.append(sym)
        self.s.put("basket_last", now)
        self.status.update(t=now, next=now + int(c["rebalance_h"] * 3600000),
                           ranked=[dict(sym=r[0], ret=round(r[1] * 100, 1)) for r in rows[:n][::-1] + rows[-n:][::-1]],
                           msg="Ranked %d coins over %dh: long %s, short %s. %d closed, %d opened%s." % (
                               len(rows), c["lookback_h"], ", ".join(s.replace("USDT", "") for s in longs), ", ".join(s.replace("USDT", "") for s in shorts), closed, opened,
                               (" (skipped %s: too small or no room)" % ", ".join(s.replace("USDT", "") for s in skipped)) if skipped else ""))
        self.s.think({"id": "basket", "symbol": ""}, "manage", "Momentum basket: " + self.status["msg"])
