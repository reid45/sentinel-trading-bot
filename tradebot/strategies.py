"""Strategies. Each fn(candles, params) returns (side[i], dist[i]): side +1/-1/0 decided at close of bar i,
dist = stop distance in price. Execution params (in params): stop (ATR mult), tp (ATR mult, 0=none), trail (0/1), reverse (0/1)."""
from . import indicators as I


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
}


def grid_combos(grid):
    out = [{}]
    for k in grid:
        out = [dict(d, **{k: v}) for d in out for v in grid[k]]
    return out
