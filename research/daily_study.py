"""Is slower better? Textbook (untuned) daily-candle trend systems vs the 4h trend mix, same coins, same shared account, same four windows.   Run: py research/daily_study.py"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tradebot import backtest as B, catalog, data
from portfolio_study import ORDER, load4h

syms = [s for s in catalog.UNIVERSE if os.path.exists(os.path.join(data.DATA_DIR, "futures_%s_1h.csv" % s))]
c4 = {s: load4h(s) for s in syms}
c1 = {s: data._resample(data.load_csv(os.path.join(data.DATA_DIR, "futures_%s_1h.csv" % s)), 86400000) for s in syms}
t0 = max(c[0][0] for s, c in c4.items() if s in ORDER); t1 = min(c[-1][0] for c in c4.values())
cut = lambda f: t0 + int((t1 - t0) * f)
wins = (("0-30%", cut(0), cut(0.3)), ("30-60%", cut(0.3), cut(0.6)), ("60-100%", cut(0.6), t1), ("last 50d", t1 - 300 * 14400000, t1), ("ALL", cut(0), t1))
mix4 = catalog.defaults_for("futures")[1]["members"]
daily = [dict(strat="donchian", p=dict(n=20, trend=100, stop=3.0, trail=1, tp=0, reverse=1)), dict(strat="donchian", p=dict(n=55, trend=0, stop=3.5, trail=1, tp=0, reverse=1)),
         dict(strat="emacross", p=dict(fast=20, slow=100, adx=15, stop=3.0, trail=1, tp=0, reverse=1))]


def run(name, tf, cands, members, risk, mp=15):
    uni = [(s, cands[s], m["strat"], m["p"]) for m in members for s in syms]
    row = []
    for w, a, b in wins:
        m = B.portfolio_run(uni, tf, risk=risk, max_pos=mp, max_lev=3.0, start=a, end=b)
        row.append(("%+6.1f%% dd %4.1f%%" % (m["ret"] * 100, m["mdd"] * 100) + (" %3.0f/wk" % (m["trades"] / (m["years"] * 52.18)) if w == "ALL" else "")).ljust(26 if w == "ALL" else 18))
    print("%-26s | %s" % (name, " | ".join(row)), flush=True)


print("%-26s | %s" % ("system (0.5% risk)", " | ".join(w[0].ljust(18) for w in wins)), flush=True)
run("4h trend mix (default)", "4h", c4, mix4, 0.005)
run("1d trend mix (3 systems)", "1d", c1, daily, 0.005)
run("1d trend mix, 0.75% risk", "1d", c1, daily, 0.0075)
