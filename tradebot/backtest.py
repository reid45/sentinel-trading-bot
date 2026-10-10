"""Bar-by-bar backtester. Signal at close of bar i -> fill at open of bar i+1 (+slippage/fees).
Stops are checked intrabar and win ties with take-profit (conservative)."""
import math
from .strategies import STRATEGIES, grid_combos

TF_HOURS = {"1m": 1 / 60, "5m": 5 / 60, "15m": .25, "1h": 1, "4h": 4, "1d": 24}
MARKET_COSTS = {  # per side, fraction of notional
    "crypto": dict(fee=0.0010, slip=0.0003, funding_8h=0.0, max_lev=1.0),
    "futures": dict(fee=0.0005, slip=0.0003, funding_8h=0.0001, max_lev=3.0),
    "forex": dict(fee=0.0000, slip=0.00008, funding_8h=0.0, max_lev=10.0),
}


def run(candles, strat, params, market="futures", tf="4h", equity0=10000.0, risk=0.01, lev=None, allow_short=None, gate=None):
    side, dist = STRATEGIES[strat]["fn"](candles, params)
    mc = MARKET_COSTS[market]
    lev = lev or mc["max_lev"]
    if allow_short is None:
        allow_short = market != "crypto"
    fee = mc["fee"] + mc["slip"]
    fund = mc["funding_8h"] * TF_HOURS[tf] / 8.0
    n = len(candles)
    eq = equity0
    curve = [equity0] * n
    trades = []
    pos = None
    pending = None
    tp_m, trail, rev, stop_m = params.get("tp", 0), params.get("trail", 0), params.get("reverse", 0), params["stop"]
    for j in range(n):
        t, o, h, l, c, _ = candles[j]
        if pending is not None:
            sd, d, ok = pending
            mult = 1.0 if ok is True else float(ok)
            pending = None
            if pos and pos["side"] != sd:
                px = o * (1 - fee * pos["side"])
                eq += pos["side"] * (px - pos["entry"]) * pos["qty"] - pos["fund"]
                trades.append((pos["t"], t, pos["side"], pos["entry"], px, eq - pos["eq0"]))
                pos = None
            if pos is None and d and ok and (sd == 1 or allow_short):
                entry = o * (1 + fee * sd)
                qty = min(eq * risk * mult / d, eq * lev / entry)
                if qty > 0:
                    tp = entry + sd * d / stop_m * tp_m if tp_m else None
                    pos = dict(side=sd, entry=entry, qty=qty, stop=entry - sd * d, tp=tp, d=d, t=t, eq0=eq, fund=0.0)
        if pos:
            sd = pos["side"]
            pos["fund"] += fund * pos["qty"] * c
            hit = None
            if sd == 1:
                if l <= pos["stop"]:
                    hit = min(pos["stop"], o)
                elif pos["tp"] and h >= pos["tp"]:
                    hit = pos["tp"]
            else:
                if h >= pos["stop"]:
                    hit = max(pos["stop"], o)
                elif pos["tp"] and l <= pos["tp"]:
                    hit = pos["tp"]
            if hit is not None:
                px = hit * (1 - fee * sd)
                eq += sd * (px - pos["entry"]) * pos["qty"] - pos["fund"]
                trades.append((pos["t"], t, sd, pos["entry"], px, eq - pos["eq0"]))
                pos = None
            elif trail:
                pos["stop"] = max(pos["stop"], h - pos["d"]) if sd == 1 else min(pos["stop"], l + pos["d"])
        curve[j] = eq + (pos["side"] * (c - pos["entry"]) * pos["qty"] - pos["fund"] if pos else 0)
        if curve[j] <= equity0 * 0.05:
            for k in range(j, n):
                curve[k] = curve[j]
            break
        if side[j] and dist[j] and (pos is None or (rev and pos["side"] != side[j])):
            pending = (side[j], dist[j], gate is None or gate(j, side[j]))
    if pos:
        sd = pos["side"]
        px = candles[-1][4]
        eq += sd * (px - pos["entry"]) * pos["qty"] - pos["fund"]
        trades.append((pos["t"], candles[-1][0], sd, pos["entry"], px, eq - pos["eq0"]))
        curve[-1] = eq
    return metrics(curve, trades, tf, equity0)


def metrics(curve, trades, tf, eq0):
    n = len(curve)
    years = max(n * TF_HOURS[tf] / 24 / 365.25, 1e-9)
    end = curve[-1]
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    rets = [curve[i] / curve[i - 1] - 1 for i in range(1, n) if curve[i - 1] > 0]
    mu = sum(rets) / len(rets) if rets else 0
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / len(rets)) if rets else 0
    bars_yr = 365.25 * 24 / TF_HOURS[tf]
    pnls = [t[5] for t in trades]
    w = [x for x in pnls if x > 0]
    ls = [x for x in pnls if x <= 0]
    return dict(ret=end / eq0 - 1, cagr=(end / eq0) ** (1 / years) - 1 if end > 0 else -1, mdd=mdd,
                sharpe=(mu / sd * math.sqrt(bars_yr)) if sd else 0, trades=len(trades),
                win=len(w) / len(pnls) if pnls else 0,
                pf=(sum(w) / -sum(ls)) if ls and sum(ls) < 0 else (99 if w else 0),
                years=years, end=end, curve=curve, trade_list=trades)


def score(m):
    """Robust objective: return per unit drawdown, requires enough trades."""
    if m["trades"] < 15 or m["mdd"] <= 0:
        return -9
    return m["cagr"] / max(m["mdd"], 0.05) * min(1.0, m["trades"] / 40)


def optimize(candles, strat, market, tf, split=0.6, **kw):
    """Grid search on first `split` of data; the winner is then judged on untouched out-of-sample data."""
    k = int(len(candles) * split)
    ins, oos = candles[:k], candles[k - 300:]
    best = None
    for p in grid_combos(STRATEGIES[strat]["grid"]):
        m = run(ins, strat, p, market, tf, **kw)
        s = score(m)
        if best is None or s > best[0]:
            best = (s, p, m)
    s, p, m_in = best
    return dict(strategy=strat, params=p, insample=m_in, oos=run(oos, strat, p, market, tf, **kw),
                full=run(candles, strat, p, market, tf, **kw), score=s)


def portfolio(runs, equity0=10000.0):
    """runs: list of (candles, metrics) each simulated on equity0. Combined account = equity0 + sum of each sleeve's P&L,
    aligned on timestamps (forward-filled). Each sleeve risks its own % of equity, so risk adds up across symbols."""
    ts = sorted({row[0] for c, _ in runs for row in c})
    idx = {t: i for i, t in enumerate(ts)}
    total = [0.0] * len(ts)
    for c, m in runs:
        cur = m["curve"]
        pnl = [None] * len(ts)
        for k, row in enumerate(c):
            pnl[idx[row[0]]] = cur[k] - equity0
        last = 0.0
        for i in range(len(ts)):
            if pnl[i] is None:
                pnl[i] = last
            last = pnl[i]
            total[i] += last
    return ts, [equity0 + x for x in total]
