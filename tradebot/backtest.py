"""Bar-by-bar backtester. Signal at close of bar i -> fill at open of bar i+1 (+slippage/fees).
Stops are checked intrabar and win ties with take-profit (conservative)."""
import math
from .strategies import STRATEGIES, grid_combos

TF_HOURS = {"1m": 1 / 60, "5m": 5 / 60, "15m": .25, "1h": 1, "4h": 4, "1d": 24}
MARKET_COSTS = {  # per side, fraction of notional
    "crypto": dict(fee=0.0010, slip=0.0003, funding_8h=0.0, max_lev=1.0),
    "futures": dict(fee=0.0005, slip=0.0003, funding_8h=0.0001, max_lev=3.0),
    "forex": dict(fee=0.0000, slip=0.00008, funding_8h=0.0, max_lev=10.0),
    "stock": dict(fee=0.0000, slip=0.0003, funding_8h=0.0, max_lev=2.0),      # commission-free brokers; stocks/ETFs/indices/commodities via Yahoo, long-only
}


def run(candles, strat, params, market="futures", tf="4h", equity0=10000.0, risk=0.01, lev=None, allow_short=None, gate=None, sig=None):
    """A strategy may return (side, dist) or (side, dist, stop_px): with stop_px the stop sits at that exact price (structure stops).
    params: rr = target as a multiple of the risk, be = move the stop to breakeven after that many R, maxbars = time stop.
    `sig` lets a caller pass precomputed strategy output (research sweeps reuse it)."""
    out = sig or STRATEGIES[strat]["fn"](candles, params)
    side, dist = out[0], out[1]
    sx = out[2] if len(out) > 2 else None
    mc = MARKET_COSTS[market]
    lev = lev or mc["max_lev"]
    if allow_short is None:
        allow_short = market not in ("crypto", "stock")
    fee = mc["fee"] + mc["slip"]
    fund = mc["funding_8h"] * TF_HOURS[tf] / 8.0
    n = len(candles)
    eq = equity0
    curve = [equity0] * n
    trades = []
    pos = None
    pending = None
    tp_m, trail, rev, stop_m = params.get("tp", 0), params.get("trail", 0), params.get("reverse", 0), params.get("stop", 1)
    rr, be, maxbars = params.get("rr", 0), params.get("be", 0), params.get("maxbars", 0)
    for j in range(n):
        t, o, h, l, c, _ = candles[j]
        if pending is not None:
            sd, d, ok, spx = pending
            mult = 1.0 if ok is True else float(ok)
            pending = None
            if pos and pos["side"] != sd:
                px = o * (1 - fee * pos["side"])
                eq += pos["side"] * (px - pos["entry"]) * pos["qty"] - pos["fund"]
                trades.append((pos["t"], t, pos["side"], pos["entry"], px, eq - pos["eq0"]))
                pos = None
            if pos is None and d and ok and (sd == 1 or allow_short):
                entry = o * (1 + fee * sd)
                if spx is not None:
                    d = (entry - spx) * sd           # structure stop: exact price; skip if the next open already blew through it
                if d and d > 0:
                    qty = min(eq * risk * mult / (d + 2 * fee * entry), eq * lev / entry)       # risk budget covers fees both ways (same sizing as the live engine)
                else:
                    qty = 0
                if qty > 0:
                    tp = entry + sd * d * rr if rr else (entry + sd * d / stop_m * tp_m if tp_m else None)
                    pos = dict(side=sd, entry=entry, qty=qty, stop=entry - sd * d, tp=tp, d=d, t=t, eq0=eq, fund=0.0, n=0)
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
            else:
                pos["n"] += 1
                if be and ((h - pos["entry"]) if sd == 1 else (pos["entry"] - l)) >= be * pos["d"]:
                    pos["stop"] = max(pos["stop"], pos["entry"]) if sd == 1 else min(pos["stop"], pos["entry"])
                if trail:
                    pos["stop"] = max(pos["stop"], h - pos["d"]) if sd == 1 else min(pos["stop"], l + pos["d"])
                if maxbars and pos["n"] >= maxbars:
                    px = c * (1 - fee * sd)
                    eq += sd * (px - pos["entry"]) * pos["qty"] - pos["fund"]
                    trades.append((pos["t"], t, sd, pos["entry"], px, eq - pos["eq0"]))
                    pos = None
        curve[j] = eq + (pos["side"] * (c - pos["entry"]) * pos["qty"] - pos["fund"] if pos else 0)
        if curve[j] <= equity0 * 0.05:
            for k in range(j, n):
                curve[k] = curve[j]
            break
        if side[j] and (dist[j] or (sx and sx[j])) and (pos is None or (rev and pos["side"] != side[j])):
            pending = (side[j], dist[j], gate is None or gate(j, side[j]), sx[j] if sx else None)
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


def portfolio_run(universe, tf="4h", equity0=10000.0, risk=0.005, max_pos=6, max_lev=3.0, lev_per=3.0, market="futures", start=None, end=None, rev_ok=True, brake=None, max_per_coin=0, dir_cap=0, gate=None):
    """Event-driven multi-market backtest with ONE shared account, the way the live engine behaves:
    universe = list of (symbol, candles, strat_name, params) in priority order (earlier = first pick when slots are scarce).
    Signals at a bar's close fill at the next open of that market; each entry risks `risk` of current equity (fees included), is skipped when
    `max_pos` positions are open or total exposure would pass `max_lev` x equity. Stops are intrabar and win ties with targets (conservative)."""
    mc = MARKET_COSTS[market]
    fee = mc["fee"] + mc["slip"]
    fund = mc["funding_8h"] * TF_HOURS[tf] / 8.0
    lev_per = min(lev_per, mc["max_lev"])
    books = []
    for sym, cs, strat, p in universe:
        out = STRATEGIES[strat]["fn"](cs, p)
        books.append(dict(sym=sym, c=cs, side=out[0], dist=out[1], sx=out[2] if len(out) > 2 else None, p=p, idx={r[0]: i for i, r in enumerate(cs)}, pos=None, pend=None, last=None))
    ts = sorted({r[0] for b in books for r in b["c"] if (start is None or r[0] >= start) and (end is None or r[0] <= end)})
    cash, trades, curve, held_ts = equity0, [], [], []
    peak = equity0                                 # brake = [(drawdown, risk multiplier), ...] widest first: smaller bets while the account is hurting
    for t in ts:
        # 1) exits and stop management on bars that exist at t
        for b in books:
            j = b["idx"].get(t)
            if j is None:
                continue
            _, o, h, l, c, _v = b["c"][j]
            b["last"] = c
            pos, p = b["pos"], b["p"]
            # fill the pending entry / reversal at this bar's open
            if b["pend"] is not None:
                sd, d, spx = b["pend"]
                b["pend"] = None
                if pos and pos["side"] != sd:
                    px = o * (1 - fee * pos["side"])
                    cash += pos["side"] * (px - pos["entry"]) * pos["qty"] - pos["fund"]
                    trades.append((pos["t"], t, pos["side"], pos["entry"], px, pos["side"] * (px - pos["entry"]) * pos["qty"] - pos["fund"], b["sym"]))
                    b["pos"] = pos = None
                if pos is None and (sd == 1 or market != "stock"):
                    live = [x for x in books if x["pos"]]
                    eq = cash + sum(x["pos"]["side"] * ((x["last"] or x["pos"]["entry"]) - x["pos"]["entry"]) * x["pos"]["qty"] - x["pos"]["fund"] for x in live)
                    entry = o * (1 + fee * sd)
                    d = (entry - spx) * sd if spx is not None else d
                    if max_per_coin and sum(1 for x in live if x["sym"] == b["sym"]) >= max_per_coin:
                        d = 0                                      # too much on one coin already
                    elif dir_cap and sum(1 for x in live if x["pos"]["side"] == sd) >= dir_cap:
                        d = 0                                      # too many trades betting the same way
                    elif gate is not None and not gate(b["sym"], sd, t):
                        d = 0                                      # outside the allowed market regime
                    if d and d > 0 and len(live) < max_pos:
                        peak = max(peak, eq)
                        mult = 1.0
                        for dd_, m_ in (brake or ()):
                            if (peak - eq) / peak >= dd_:
                                mult = m_
                        qty = min(eq * risk * mult / (d + 2 * fee * entry), eq * lev_per / entry)
                        gross = sum(x["pos"]["qty"] * (x["last"] or x["pos"]["entry"]) for x in live)
                        qty = min(qty, max(0.0, eq * max_lev - gross) / entry)
                        if qty > 0 and qty * entry >= 5:
                            cash -= 0.0
                            tp = entry + sd * d * p["rr"] if p.get("rr") else None
                            b["pos"] = pos = dict(side=sd, entry=entry, qty=qty, stop=entry - sd * d, tp=tp, d=d, t=t, fund=0.0, n=0)
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
                pos["n"] += 1
                if hit is None and p.get("maxbars") and pos["n"] >= p["maxbars"]:
                    hit = c
                if hit is not None:
                    px = hit * (1 - fee * sd)
                    pnl = sd * (px - pos["entry"]) * pos["qty"] - pos["fund"]
                    cash += pnl
                    trades.append((pos["t"], t, sd, pos["entry"], px, pnl, b["sym"]))
                    b["pos"] = None
                elif p.get("trail"):
                    pos["stop"] = max(pos["stop"], h - pos["d"]) if sd == 1 else min(pos["stop"], l + pos["d"])
            # 2) signal at this bar's close -> pending for the next bar
            if b["side"][j] and (b["dist"][j] or (b["sx"] and b["sx"][j])) and (b["pos"] is None or (rev_ok and p.get("reverse") and b["pos"]["side"] != b["side"][j])):
                b["pend"] = (b["side"][j], b["dist"][j], b["sx"][j] if b["sx"] else None)
        eq = cash + sum(x["pos"]["side"] * ((x["last"] or x["pos"]["entry"]) - x["pos"]["entry"]) * x["pos"]["qty"] - x["pos"]["fund"] for x in books if x["pos"])
        curve.append(eq)
        held_ts.append(t)
    if not curve:
        return None
    for b in books:                                              # close what is still open at the final price
        if b["pos"]:
            pos = b["pos"]
            px = (b["last"] or pos["entry"]) * (1 - fee * pos["side"])
            pnl = pos["side"] * (px - pos["entry"]) * pos["qty"] - pos["fund"]
            cash += pnl
            trades.append((pos["t"], ts[-1], pos["side"], pos["entry"], px, pnl, b["sym"]))
    m = metrics(curve, trades, tf, equity0)
    m["ts"] = held_ts
    return m
