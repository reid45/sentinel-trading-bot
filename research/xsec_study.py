"""Fast, market-neutral idea: rank the 38 coins by how much they moved over the last L hours, then go long the 6 strongest and short the 6 weakest ("momentum")
or the reverse ("reversal"), re-ranking every H hours. Dollar-neutral, so the market direction mostly cancels. Hourly candles, about 3 years. Costs are charged on every
change in position (round trip = 2 x cost per side). First 60% = in-sample, last 40% = out-of-sample.   Run: py research/xsec_study.py"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tradebot import data, catalog

N = 6
Ls, Hs, COSTS = (6, 24, 72, 168), (4, 12, 24, 72), (0.0008, 0.0004)


def load():
    cl = {}
    for s in catalog.UNIVERSE:
        p = os.path.join(data.DATA_DIR, "futures_%s_1h.csv" % s)
        if os.path.exists(p):
            cl[s] = {r[0]: r[4] for r in data.load_csv(p)}
    ts = sorted(set(t for d in cl.values() for t in d))
    return cl, ts


def run(cl, ts, L, H, mode, cost, lo, hi):
    """-> list of net returns per rebalance (fraction of gross capital)."""
    out, prev = [], {}
    step = 3600000
    for ti in range(max(lo, L), hi - H, H):
        t = ts[ti]
        rets = {}
        for s, d in cl.items():
            a, b, c = d.get(ts[ti - L]), d.get(t), d.get(ts[ti + H])
            if a and b and c:
                rets[s] = (b / a - 1, c / b - 1)
        if len(rets) < 2 * N + 4:
            continue
        rk = sorted(rets, key=lambda s: rets[s][0])
        lose, win = rk[:N], rk[-N:]
        longs, shorts = (win, lose) if mode == "mom" else (lose, win)
        w = {s: 0.5 / N for s in longs}
        w.update({s: -0.5 / N for s in shorts})
        turn = sum(abs(w.get(s, 0) - prev.get(s, 0)) for s in set(w) | set(prev))
        gross = sum(w[s] * rets[s][1] for s in w)
        out.append(gross - cost * turn)
        prev = w
    return out


def stat(r, H):
    n = len(r)
    if n < 5:
        return 0, 0, 0, 0
    m = sum(r) / n
    sd = (sum((x - m) ** 2 for x in r) / (n - 1)) ** 0.5
    per_year = 8766 / H
    return n, m * per_year * 100, (m / sd * math.sqrt(per_year) if sd else 0), sum(1 for x in r if x > 0) / n * 100


if __name__ == "__main__":
    cl, ts = load()
    cut = int(len(ts) * 0.6)
    print("coins %d, hours %d" % (len(cl), len(ts)))
    rows = []
    for mode in ("mom", "rev"):
        for L in Ls:
            for H in Hs:
                for cost in COSTS:
                    i = stat(run(cl, ts, L, H, mode, cost, 0, cut), H)
                    o = stat(run(cl, ts, L, H, mode, cost, cut, len(ts)), H)
                    rows.append((mode, L, H, cost, i, o))
    for cost in COSTS:
        sub = [r for r in rows if r[3] == cost]
        print("\ncost %.2f%% per side: %d settings | positive in-sample %d | positive out-of-sample %d | positive in BOTH %d" % (cost * 100, len(sub), sum(1 for r in sub if r[4][1] > 0), sum(1 for r in sub if r[5][1] > 0), sum(1 for r in sub if r[4][1] > 0 and r[5][1] > 0)))
        for r in sorted(sub, key=lambda r: -r[5][1])[:6]:
            print("  %s L=%3d H=%2d | IS %+6.1f%%/yr sharpe %+.2f | OOS %+6.1f%%/yr sharpe %+.2f win %2.0f%% (n=%d)" % (r[0], r[1], r[2], r[4][1], r[4][2], r[5][1], r[5][2], r[5][3], r[5][0]))
