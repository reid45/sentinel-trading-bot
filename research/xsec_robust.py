"""Robustness of the one family that survived xsec_study: dollar-neutral momentum (long the strongest coins, short the weakest over the last L hours).
Checks: other lookbacks and basket sizes, four separate time windows (not just two halves), maximum drawdown, and the cost level.   Run: py research/xsec_robust.py"""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xsec_study as X

cl, ts = X.load()
n = len(ts)
wins = [("yr1", 0, n // 3), ("yr2", n // 3, 2 * n // 3), ("yr3", 2 * n // 3, n), ("last 6mo", n - 4380, n)]


def dd(r):
    eq, pk, m = 1.0, 1.0, 0.0
    for x in r:
        eq *= 1 + x
        pk = max(pk, eq)
        m = max(m, 1 - eq / pk)
    return m * 100


for N in (4, 6, 8):
    X.N = N
    for cost in (0.0008, 0.0005):
        print("\nbasket %d per side, cost %.2f%% per side" % (N, cost * 100))
        for L in (72, 120, 168, 240, 336, 504):
            for H in (24, 48):
                cells, allr = [], []
                for name, a, b in wins:
                    r = X.run(cl, ts, L, H, "mom", cost, a, b)
                    cells.append("%s %+5.0f%%" % (name, X.stat(r, H)[1]))
                r = X.run(cl, ts, L, H, "mom", cost, 0, n)
                s = X.stat(r, H)
                print("  L=%3d H=%2d | %s | all %+5.1f%%/yr sharpe %+.2f maxDD %2.0f%%" % (L, H, " | ".join(cells), s[1], s[2], dd(r)), flush=True)
