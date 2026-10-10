import sys, os, glob
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tradebot import backtest as B, data
from portfolio_study import CFG, ORDER, load4h
syms = [os.path.basename(f)[len("futures_"):-len("_1h.csv")] for f in glob.glob(os.path.join(data.DATA_DIR, "futures_*_1h.csv"))]
syms = [s for s in ORDER if s in syms] + sorted(s for s in syms if s not in ORDER)
cs = {s: load4h(s) for s in syms}
t0 = max(c[0][0] for s, c in cs.items() if s in ORDER); t1 = min(c[-1][0] for c in cs.values())
split = t0 + int((t1 - t0) * 0.6); mid = t0 + int((t1 - t0) * 0.3)
members = [dict(strat=CFG[n][0], p=CFG[n][1]) for n in ("donch20", "donch40", "mtf1d", "ema20_150")]
P = dict(members=members, trail=1, reverse=1)
uni = [(s, cs[s], "ensemble", P) for s in syms]
for mp, rk in ((6, 0.005), (10, 0.004), (15, 0.003), (20, 0.0025), (12, 0.005)):
    row = []
    for label, a, b in (("earlier", mid, split), ("OUT-OF-SAMPLE", split, t1)):
        m = B.portfolio_run(uni, "4h", risk=rk, max_pos=mp, max_lev=3.0, start=a, end=b)
        row.append("%s ret %+6.1f%% mdd %4.1f%% sharpe %+.2f PF %.2f %3.1f/wk" % (label, m["ret"] * 100, m["mdd"] * 100, m["sharpe"], m["pf"], m["trades"] / (m["years"] * 52.18)))
    print("single-position ensemble maxpos %2d risk %.2f%% | %s" % (mp, rk * 100, " | ".join(row)), flush=True)
