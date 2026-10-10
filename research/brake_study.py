"""Does cutting position size during drawdowns help the trend mix? Run: py research/brake_study.py"""
import sys, os, glob
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
wins = (("early 0-30%", cut(0), cut(0.3)), ("30-60%", cut(0.3), cut(0.6)), ("OOS 60-100%", cut(0.6), t1), ("last 50 days", t1 - 300 * 14400000, t1))
for name, brake in (("no brake", None), ("halve at 5% dd", [(0.05, 0.5)]), ("halve 4%, quarter 8%", [(0.04, 0.5), (0.08, 0.25)]), ("halve 6%, quarter 10%", [(0.06, 0.5), (0.10, 0.25)]), ("half at 8%", [(0.08, 0.5)])):
    row = []
    for w, a, b in wins:
        m = B.portfolio_run(uni, "4h", risk=0.003, max_pos=15, max_lev=3.0, start=a, end=b, brake=brake)
        row.append("%s %+6.1f%% dd %4.1f%%" % (w, m["ret"] * 100, m["mdd"] * 100))
    print("%-24s | %s" % (name, " | ".join(row)), flush=True)
