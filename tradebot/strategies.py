"""Strategies. Each fn(candles, params) returns (side[i], dist[i]): side +1/-1/0 decided at close of bar i,
dist = stop distance in price. Execution params (in params): stop (ATR mult), tp (ATR mult, 0=none), trail (0/1), reverse (0/1)."""
from . import indicators as I, smc


def _cols(c):
    return [x[1] for x in c], [x[2] for x in c], [x[3] for x in c], [x[4] for x in c]


def donchian(c, p):
    o, h, l, cl = _cols(c)
    a = I.atr(h, l, cl, p.get("atr_n", 14))
    hi, lo = I.highest(h, p["n"]), I.lowest(l, p["n"])
    tr = I.ema(cl, p["trend"]) if p.get("trend") else None
    side = [0] * len(c)
    for i in range(len(c)):
        if hi[i] is None or a[i] is None:
            continue
        up = tr is None or tr[i] is None or cl[i] > tr[i]
        dn = tr is None or tr[i] is None or cl[i] < tr[i]
        if cl[i] > hi[i] and up:
            side[i] = 1
        elif cl[i] < lo[i] and dn:
            side[i] = -1
    return side, [x * p["stop"] if x else None for x in a]


def pullback(c, p):
    o, h, l, cl = _cols(c)
    a = I.atr(h, l, cl, p.get("atr_n", 14))
    r = I.rsi(cl, p["rsi_n"])
    tr = I.ema(cl, p["trend"])
    side = [0] * len(c)
    for i in range(len(c)):
        if r[i] is None or tr[i] is None or a[i] is None:
            continue
        if cl[i] > tr[i] and r[i] < p["lo"]:
            side[i] = 1
        elif cl[i] < tr[i] and r[i] > 100 - p["lo"]:
            side[i] = -1
    return side, [x * p["stop"] if x else None for x in a]


def emacross(c, p):
    o, h, l, cl = _cols(c)
    a = I.atr(h, l, cl, p.get("atr_n", 14))
    f, s, ad = I.ema(cl, p["fast"]), I.ema(cl, p["slow"]), I.adx(h, l, cl, 14)
    side = [0] * len(c)
    for i in range(1, len(c)):
        if None in (f[i], s[i], f[i - 1], s[i - 1], a[i], ad[i]):
            continue
        if ad[i] < p["adx"]:
            continue
        if f[i - 1] <= s[i - 1] and f[i] > s[i]:
            side[i] = 1
        elif f[i - 1] >= s[i - 1] and f[i] < s[i]:
            side[i] = -1
    return side, [x * p["stop"] if x else None for x in a]


# ---------------------------------------------------------------- multi-timeframe helpers + more families
HTF_MS = {"15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}


def htf_map(c, htf, fn):
    """Evaluate fn(higher-timeframe candles) -> list, then give every base bar the value of the *previous completed* higher-TF bar (never the forming one)."""
    ms = HTF_MS[htf]
    buckets, order = {}, []
    for x in c:
        b = x[0] - x[0] % ms
        cur = buckets.get(b)
        if cur is None:
            buckets[b] = [b, x[1], x[2], x[3], x[4], x[5]]
            order.append(b)
        else:
            cur[2] = max(cur[2], x[2]); cur[3] = min(cur[3], x[3]); cur[4] = x[4]; cur[5] += x[5]
    hc = [tuple(buckets[b]) for b in order]
    vals = fn(hc)
    pos = {b: i for i, b in enumerate(order)}
    out = []
    for x in c:
        i = pos[x[0] - x[0] % ms] - 1
        out.append(vals[i] if i >= 0 else None)
    return out


def _htf_trend(hc, fast, slow, adx_min):
    h, l, cl = [x[2] for x in hc], [x[3] for x in hc], [x[4] for x in hc]
    f, s_, ad = I.ema(cl, fast), I.ema(cl, slow), I.adx(h, l, cl, 14)
    return [0 if None in (f[i], s_[i], ad[i]) or ad[i] < adx_min else (1 if f[i] > s_[i] else -1) for i in range(len(hc))]


def mtf_break(c, p):
    """Breakout on this timeframe, only in the direction of the higher timeframe's trend (EMA fast/slow + ADX filter)."""
    o, h, l, cl = _cols(c)
    a = I.atr(h, l, cl, p.get("atr_n", 14))
    hi, lo = I.highest(h, p["n"]), I.lowest(l, p["n"])
    tr = htf_map(c, p["htf"], lambda hc: _htf_trend(hc, p.get("fast", 20), p.get("slow", 50), p.get("adx", 18)))
    side = [0] * len(c)
    for i in range(len(c)):
        if hi[i] is None or a[i] is None or not tr[i]:
            continue
        if cl[i] > hi[i] and tr[i] == 1:
            side[i] = 1
        elif cl[i] < lo[i] and tr[i] == -1:
            side[i] = -1
    return side, [x * p["stop"] if x else None for x in a]


def squeeze(c, p):
    """Volatility compression (Bollinger bandwidth in its lowest `q` percent) then a close outside the Donchian channel, with the higher-timeframe trend."""
    o, h, l, cl = _cols(c)
    a = I.atr(h, l, cl, 14)
    n = p.get("bb", 20)
    ma = I.sma(cl, n)
    bw = [None] * len(c)
    for i in range(n, len(c)):
        seg = cl[i - n + 1:i + 1]
        m = ma[i]
        sd = (sum((x - m) ** 2 for x in seg) / n) ** 0.5
        bw[i] = 4 * sd / m if m else None
    hi, lo = I.highest(h, p.get("n", 20)), I.lowest(l, p.get("n", 20))
    tr = htf_map(c, p["htf"], lambda hc: _htf_trend(hc, 20, 50, 0)) if p.get("htf") else None
    side = [0] * len(c)
    look = p.get("look", 200)
    for i in range(look, len(c)):
        if bw[i - 1] is None or hi[i] is None or a[i] is None:
            continue
        win = sorted(x for x in bw[i - look:i] if x is not None)
        if not win or bw[i - 1] > win[int(len(win) * p.get("q", 0.2))]:
            continue                                            # was not compressed on the previous bar
        up, dn = cl[i] > hi[i], cl[i] < lo[i]
        if tr is not None and tr[i]:
            up, dn = up and tr[i] == 1, dn and tr[i] == -1
        side[i] = 1 if up else -1 if dn else 0
    return side, [x * p["stop"] if x else None for x in a]


def bbrev(c, p):
    """Range mean reversion: fade closes beyond the Bollinger band while ADX says there is no trend and RSI agrees. Target = back to the middle (via rr)."""
    o, h, l, cl = _cols(c)
    a = I.atr(h, l, cl, 14)
    n, k = p.get("bb", 20), p.get("k", 2.5)
    ma, ad, rs = I.sma(cl, n), I.adx(h, l, cl, 14), I.rsi(cl, p.get("rsi_n", 3))
    side = [0] * len(c)
    for i in range(n, len(c)):
        if ma[i] is None or ad[i] is None or a[i] is None or rs[i] is None or ad[i] > p.get("adx", 20):
            continue
        seg = cl[i - n + 1:i + 1]
        sd = (sum((x - ma[i]) ** 2 for x in seg) / n) ** 0.5
        if cl[i] < ma[i] - k * sd and rs[i] < p.get("lo", 15):
            side[i] = 1
        elif cl[i] > ma[i] + k * sd and rs[i] > 100 - p.get("lo", 15):
            side[i] = -1
    return side, [x * p["stop"] if x else None for x in a]


def ensemble(c, p):
    """Single-position preview of a trend mix, used only for chart overlays. The live engine and the Lab run every member as its OWN sleeve
    (own position, own trailing stop), which tested clearly better than one blended position (research/ensemble_union.py).
    params: members = [{"name", "strat", "p"}, ...]."""
    outs = [STRATEGIES[m["strat"]]["fn"](c, m["p"]) for m in p["members"]]
    n = len(c)
    side, dist = [0] * n, [None] * n
    for i in range(n):
        votes = [(o[0][i], o[1][i]) for o in outs if o[0][i] and o[1][i]]
        if not votes:
            continue
        sd = votes[0][0]
        ds = [d for s_, d in votes if s_ == sd]
        side[i], dist[i] = sd, sum(ds) / len(ds)
    return side, dist


STRATEGIES = {
    "donchian": {"fn": donchian, "label": "Donchian breakout + trailing stop",
                 "grid": {"n": [20, 40, 60, 100], "trend": [0, 100, 200], "stop": [2.5, 3.5, 5.0],
                          "trail": [1], "tp": [0], "reverse": [0, 1]}},
    "pullback": {"fn": pullback, "label": "Trend pullback (RSI dip inside EMA trend)",
                 "grid": {"rsi_n": [2, 3], "lo": [10, 20], "trend": [100, 200], "stop": [2.0, 3.0],
                          "tp": [2.0, 4.0], "trail": [0], "reverse": [0]}},
    "emacross": {"fn": emacross, "label": "EMA cross + ADX filter + trailing stop",
                 "grid": {"fast": [10, 20, 30], "slow": [50, 100, 150], "adx": [15, 25], "stop": [3.0, 4.5],
                          "trail": [1], "tp": [0], "reverse": [1]}},
    "mtf_break": {"fn": mtf_break, "label": "Breakout with higher-timeframe trend filter",
                  "grid": {"n": [20, 40], "htf": ["4h"], "fast": [20], "slow": [50], "adx": [18], "stop": [2.0, 3.0], "trail": [1], "tp": [0], "reverse": [0]}},
    "squeeze": {"fn": squeeze, "label": "Volatility squeeze breakout",
                "grid": {"bb": [20], "q": [0.2], "n": [20], "htf": ["4h"], "stop": [2.0, 3.0], "trail": [1], "tp": [0], "reverse": [0]}},
    "bbrev": {"fn": bbrev, "label": "Range mean reversion (Bollinger + RSI, no-trend filter)",
              "grid": {"k": [2.0, 2.5], "adx": [20], "lo": [15], "stop": [2.0], "rr": [1.0, 1.5], "maxbars": [12]}},
    "trendmix": {"fn": ensemble, "label": "Trend mix: 4 trend systems per coin (recommended)", "grid": {}},
    "sweep": {"fn": smc.sweep, "label": "Liquidity sweep reversal (stop-hunt, structure stop)",
              "grid": {"k": [3], "wick": [0.35], "conf": [0, 3], "mind": [0.003], "rr": [1.5, 2.5], "be": [0], "maxbars": [24]}},
    "fvg": {"fn": smc.fvg, "label": "Fair value gap retest (ICT)",
            "grid": {"gmin": [0.3], "disp": [1.0], "mind": [0.003], "rr": [1.5, 2.5], "be": [0], "maxbars": [24]}},
    "ob": {"fn": smc.ob, "label": "Order block retest (SMC)",
           "grid": {"disp": [1.0], "mind": [0.003], "rr": [1.5, 2.5], "be": [0], "maxbars": [24]}},
}


def grid_combos(grid):
    out = [{}]
    for k in grid:
        out = [dict(d, **{k: v}) for d in out for v in grid[k]]
    return out
