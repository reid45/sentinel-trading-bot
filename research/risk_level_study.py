"""How much risk per trade is worth taking? Same 38-coin trend mix, shared account, live-engine limits (leverage cap 3x, 15 open trades), four windows.
Higher risk scales profit AND drawdown; the leverage cap also starts skipping trades.   Run: py research/risk_level_study.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tradebot import backtest as B, catalog, data
from portfolio_study import ORDER, load4h

syms = [s for s in catalog.UNIVERSE if os.path.exists(os.path.join(data.DATA_DIR, "futures_%s_1h.csv" % s))]
cs = {s: load4h(s) for s in syms}
t0 = max(c[0][0] for s, c in cs.items() if s in ORDER); t1 = min(c[-1][0] for c in cs.values())
cut = lambda f: t0 + int((t1 - t0) * f)
members = catalog.defaults_for("futures")[1]["members"]
uni = [(s, cs[s], m["strat"], m["p"]) for m in members for s in syms]
wins = (("0-30%", cut(0), cut(0.3)), ("30-60%", cut(0.3), cut(0.6)), ("60-100%", cut(0.6), t1), ("last 50d", t1 - 300 * 14400000, t1), ("ALL", cut(0), t1))
print("risk/trade  max_open | " + " | ".join(w[0].ljust(18) for w in wins), flush=True)
for risk in (0.003, 0.005, 0.0075, 0.01, 0.015, 0.02):
    for mp in (15,):
        row = []
        for w, a, b in wins:
            m = B.portfolio_run(uni, "4h", risk=risk, max_pos=mp, max_lev=3.0, start=a, end=b)
            row.append(("%+6.1f%% dd %4.1f%%" % (m["ret"] * 100, m["mdd"] * 100)).ljust(18))
        print("%-10s  %-8d | %s" % ("%.2f%%" % (risk * 100), mp, " | ".join(row)), flush=True)
