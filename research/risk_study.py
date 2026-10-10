"""Do portfolio-level risk rules improve the trend mix? Tests (same four windows as brake_study): a cap on simultaneous trades per coin, a cap on how many trades
may point the same way, and a Bitcoin-regime gate (altcoin longs only while BTC is above its 4h EMA, shorts only while below).
A rule is only worth adopting if it helps in MOST windows, not one.   Run: py research/risk_study.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tradebot import backtest as B, catalog, data, indicators as I
from portfolio_study import ORDER, load4h

syms = [s for s in catalog.UNIVERSE if os.path.exists(os.path.join(data.DATA_DIR, "futures_%s_1h.csv" % s))]
cs = {s: load4h(s) for s in syms}
t0 = max(c[0][0] for s, c in cs.items() if s in ORDER); t1 = min(c[-1][0] for c in cs.values())
cut = lambda f: t0 + int((t1 - t0) * f)
members = catalog.defaults_for("futures")[1]["members"]
uni = [(s, cs[s], m["strat"], m["p"]) for m in members for s in syms]
wins = (("early 0-30%", cut(0), cut(0.3)), ("30-60%", cut(0.3), cut(0.6)), ("OOS 60-100%", cut(0.6), t1), ("last 50d", t1 - 300 * 14400000, t1))

btc = cs["BTCUSDT"]
bt = [x[0] for x in btc]
trend = {}
for n in (50, 100, 200):
    e = I.ema([x[4] for x in btc], n)
    trend[n] = {btc[i][0]: (1 if btc[i][4] > e[i] else -1) if e[i] else 0 for i in range(len(btc))}


def regime(n):
    def gate(sym, side, t):
        if sym == "BTCUSDT":
            return True
        prev = t - 4 * 3600000                                    # the last COMPLETED BTC bar
        r = trend[n].get(prev, 0)
        return r == 0 or r == side
    return gate


def run(name, **kw):
    row = []
    for w, a, b in wins:
        m = B.portfolio_run(uni, "4h", risk=0.003, max_pos=15, max_lev=3.0, start=a, end=b, **kw)
        row.append("%s %+6.1f%% dd %4.1f%%" % (w, m["ret"] * 100, m["mdd"] * 100))
    print("%-30s | %s" % (name, " | ".join(row)), flush=True)


run("baseline")
run("max 2 trades per coin", max_per_coin=2)
run("max 1 trade per coin", max_per_coin=1)
run("max 9 same-direction", dir_cap=9)
run("max 7 same-direction", dir_cap=7)
run("BTC above/below EMA50", gate=regime(50))
run("BTC above/below EMA100", gate=regime(100))
run("BTC above/below EMA200", gate=regime(200))
run("2 per coin + EMA100 gate", max_per_coin=2, gate=regime(100))
