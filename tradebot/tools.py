"""Evidence tools for the AI (day trader, analyst, chat): facts and measurements it can ask for, so it reasons from data instead of guessing.

  levels  liquidity map: prior day / week highs and lows, swing highs / lows, equal-highs / equal-lows clusters, distance to each in % and ATR
  mtf     trend / momentum / volatility on 15m, 1h, 4h and 1d in one call
  flow    live order-book depth imbalance + spread, and taker buy/sell pressure from recent trades
  edge    how a trigger (breakout, RSI extreme, volume surge) has actually performed on THIS coin recently, net of fees and slippage

Everything is computed from public market data; nothing here can place an order."""
import json, time
from . import data, indicators as I, smc

COST = 0.0016                     # round-trip fees + slippage on a perp, as in the backtester


def _atr(h, l, c, n=14):
    return I.atr(h, l, c, n)[-1] or (h[-1] - l[-1]) or 1e-9


def _pivots(h, l, k=3, age=400):
    hi, lo = [], []
    n = len(h)
    for j in range(max(k, n - age), n - k):
        if h[j] > max(h[j - k:j]) and h[j] >= max(h[j + 1:j + k + 1]):
            hi.append((j, h[j]))
        if l[j] < min(l[j - k:j]) and l[j] <= min(l[j + 1:j + k + 1]):
            lo.append((j, l[j]))
    return hi, lo


def levels(hist):
    """Liquidity map from candles (15m or 1h). Levels already traded through are dropped; clusters of equal highs/lows are flagged."""
    h, l, c = [x[2] for x in hist], [x[3] for x in hist], [x[4] for x in hist]
    px, a = c[-1], _atr(h, l, c)
    day = 86400000
    t_now = hist[-1][0]
    d0 = t_now - t_now % day
    prev = [x for x in hist if d0 - day <= x[0] < d0]
    today = [x for x in hist if x[0] >= d0]
    w0 = d0 - ((time.gmtime(d0 / 1000).tm_wday) * day)                      # Monday 00:00 UTC
    pw = [x for x in hist if w0 - 7 * day <= x[0] < w0]
    out = {"price": px}
    for name, rows in (("prev_day", prev), ("today", today), ("prev_week", pw)):
        if rows:
            out[name + "_high"], out[name + "_low"] = max(x[2] for x in rows), min(x[3] for x in rows)
    hi, lo = _pivots(h, l)
    hi = [v for j, v in hi if v > max(h[j + 1:] or [0]) * 0.99999 or v >= max(h[j + 1:] or [0])]       # still unswept
    lo = [v for j, v in lo if v <= min(l[j + 1:] or [1e18])]
    near = lambda arr, up: sorted((v for v in arr if (v > px) == up), key=lambda v: abs(v - px))[:3]
    out["swing_highs_above"], out["swing_lows_below"] = near(hi, True), near(lo, False)

    def clusters(arr):
        arr = sorted(arr)
        return [round(sum(arr[i:i + 2]) / 2, 8) for i in range(len(arr) - 1) if abs(arr[i + 1] - arr[i]) <= 0.15 * a]
    out["equal_highs"], out["equal_lows"] = [v for v in clusters(hi) if v > px][:2], [v for v in clusters(lo) if v < px][:2]
    flat = {}
    for k_, v in out.items():
        if isinstance(v, list):
            flat[k_] = [(round(x, 6), "%+.2f%% / %+.1f ATR" % ((x / px - 1) * 100, (x - px) / a)) for x in v]
        elif k_ != "price":
            flat[k_] = "%.6g (%+.2f%%, %+.1f ATR)" % (v, (v / px - 1) * 100, (v - px) / a)
    flat["price"], flat["atr_pct"] = px, round(a / px * 100, 3)
    return flat


def mtf(fetch, market, symbol):
    rows = []
    for tf, n in (("15m", 260), ("1h", 260), ("4h", 260), ("1d", 260)):
        try:
            hist = fetch(market, symbol, tf, n)
        except Exception as e:
            rows.append("%s: unavailable" % tf)
            continue
        h, l, c = [x[2] for x in hist], [x[3] for x in hist], [x[4] for x in hist]
        e20, e50, e200 = I.ema(c, 20)[-1], I.ema(c, 50)[-1], I.ema(c, 200)[-1]
        adx, rsi = I.adx(h, l, c, 14)[-1], I.rsi(c, 14)[-1]
        a = _atr(h, l, c)
        trend = "up" if e20 and e50 and e20 > e50 and c[-1] > e50 else "down" if e20 and e50 and e20 < e50 and c[-1] < e50 else "mixed"
        rows.append("%s: %s | ADX %s | RSI %s | ATR %.2f%% | vs EMA200 %s | 20-bar range pos %.0f%%" % (
            tf, trend, "%.0f" % adx if adx else "n/a", "%.0f" % rsi if rsi else "n/a", a / c[-1] * 100, "%+.1f%%" % ((c[-1] / e200 - 1) * 100) if e200 else "n/a",
            (c[-1] - min(l[-20:])) / ((max(h[-20:]) - min(l[-20:])) or 1) * 100))
    return "\n".join(rows)


def flow(symbol):
    """Order book (top 50 levels each side) and taker pressure. Live-only: there is no history for this, so it is never used in backtests."""
    from urllib.parse import urlencode
    out = []
    try:
        d = data._get("https://fapi.binance.com/fapi/v1/depth?" + urlencode(dict(symbol=symbol, limit=50)), timeout=6)
        bids, asks = [(float(p), float(q)) for p, q in d["bids"]], [(float(p), float(q)) for p, q in d["asks"]]
        mid = (bids[0][0] + asks[0][0]) / 2
        for pct in (0.1, 0.5):
            b = sum(p * q for p, q in bids if p >= mid * (1 - pct / 100))
            a = sum(p * q for p, q in asks if p <= mid * (1 + pct / 100))
            out.append("book within %.1f%%: bids $%.0f vs asks $%.0f (imbalance %+.0f%%)" % (pct, b, a, (b - a) / (b + a) * 100 if b + a else 0))
        out.append("spread %.2f bps" % ((asks[0][0] - bids[0][0]) / mid * 1e4))
    except Exception as e:
        out.append("book unavailable (%s)" % str(e)[:40])
    try:
        k = data._get("https://fapi.binance.com/fapi/v1/klines?" + urlencode(dict(symbol=symbol, interval="5m", limit=12)), timeout=6)
        tb, tv = sum(float(r[9]) for r in k), sum(float(r[5]) for r in k)
        last = sum(float(r[9]) for r in k[-3:]) / (sum(float(r[5]) for r in k[-3:]) or 1)
        out.append("taker buy share: last hour %.0f%%, last 15m %.0f%% (50%% = balanced)" % (tb / (tv or 1) * 100, last * 100))
    except Exception as e:
        out.append("trade flow unavailable (%s)" % str(e)[:40])
    return "\n".join(out)


# ------------------------------------------------------------------ historical edge of a trigger on this coin
SMC = (("sweep", smc.sweep, dict(k=3, conf=3, mind=0.0015, maxd=4.0)), ("fvg", smc.fvg, dict(mind=0.0015, maxd=4.0, age=20)), ("ob", smc.ob, dict(mind=0.0015, maxd=4.0, age=20)))


def smc_hits(hist):
    """Smart-money patterns firing on the LAST closed bar -> list of (kind, side, text)."""
    out = []
    for name, fn, p in SMC:
        sd = fn(hist[-400:], p)[0]
        if sd[-1]:
            out.append((name, sd[-1], smc.describe(name, sd[-1])))
    return out


def _triggers(hist):
    """Per-bar trigger flags, computed once (causal). -> list of (follow_side or 0, kind)."""
    h, l, c, v = [x[2] for x in hist], [x[3] for x in hist], [x[4] for x in hist], [x[5] for x in hist]
    n = len(hist)
    a, rs = I.atr(h, l, c, 14), I.rsi(c, 14)
    out = [None] * n
    vs = 0.0
    for i in range(40, n):
        if i >= 21:
            vs = sum(v[i - 20:i]) / 20
        ks = []
        if c[i] > max(h[i - 20:i]):
            ks.append(("breakout", 1))
        elif c[i] < min(l[i - 20:i]):
            ks.append(("breakout", -1))
        if rs[i] is not None and rs[i] >= 75:
            ks.append(("rsi_extreme", 1))
        elif rs[i] is not None and rs[i] <= 25:
            ks.append(("rsi_extreme", -1))
        if vs and a[i] and v[i] >= 2.2 * vs and abs(c[i] - hist[i][1]) >= 0.8 * a[i]:
            ks.append(("volume_surge", 1 if c[i] > hist[i][1] else -1))
        out[i] = ks
    for name, fn, p in SMC:                                   # ICT / smart-money patterns as extra triggers (direction = the trade the pattern argues for)
        sd = fn(hist, p)[0]
        for i in range(40, n):
            if sd[i]:
                out[i] = (out[i] or []) + [(name, sd[i])]
    return out, a


def edge(hist, kind, stop_atr=1.5, tgt_atr=2.4, hold=12):
    """Simulate taking every past occurrence of `kind` in the direction of the move (follow) and against it (fade): entry next open, ATR stop / target, time stop,
    costs included, stop wins ties. -> {side: dict(n, win, expR)}. R = profit / initial risk."""
    trig, atr = _triggers(hist)
    res = {"follow": [], "fade": []}
    n = len(hist)
    for i in range(40, n - hold - 2):
        for k, sd0 in (trig[i] or []):
            if k != kind or not atr[i]:
                continue
            for name, sd in (("follow", sd0), ("fade", -sd0)):
                entry = hist[i + 1][1]
                d = stop_atr * atr[i]
                stop, tgt = entry - sd * d, entry + sd * tgt_atr * atr[i]
                r = None
                for j in range(i + 1, i + 1 + hold):
                    _, o, hh, ll, cc, _v = hist[j]
                    if (sd == 1 and ll <= stop) or (sd == -1 and hh >= stop):
                        r = -1.0
                        break
                    if (sd == 1 and hh >= tgt) or (sd == -1 and ll <= tgt):
                        r = tgt_atr / stop_atr
                        break
                if r is None:
                    r = (hist[i + hold][4] - entry) * sd / d
                res[name].append(r - COST * entry / d)
    out = {}
    for name, rs in res.items():
        out[name] = dict(n=len(rs), win=round(sum(1 for x in rs if x > 0) / len(rs) * 100) if rs else 0, expR=round(sum(rs) / len(rs), 2) if rs else 0.0)
    return out


def edge_line(fetch, market, symbol, kinds, tf="15m"):
    """One compact line for the dossier: what each trigger has paid on this coin over the last ~30 days (3000 bars at 15m)."""
    hist = fetch(market, symbol, tf, 3000)
    bits = []
    for k in kinds:
        e = edge(hist, k)
        bits.append("%s follow n=%d win %d%% exp %+.2fR | fade n=%d win %d%% exp %+.2fR" % (k, e["follow"]["n"], e["follow"]["win"], e["follow"]["expR"], e["fade"]["n"], e["fade"]["win"], e["fade"]["expR"]))
    return "history (net of fees, 1.5xATR stop / 2.4xATR target): " + "; ".join(bits)


# ------------------------------------------------------------------ tool schema + dispatcher (merged into analyst.TOOLS)
SYM = {"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]}
TOOLS = [
    {"type": "function", "function": {"name": "levels", "description": "Liquidity map for a market: prior day/week highs and lows, unswept swing highs/lows, equal-highs/lows clusters, with distances in % and ATR.", "parameters": SYM}},
    {"type": "function", "function": {"name": "mtf", "description": "Trend, ADX, RSI, ATR and range position on 15m, 1h, 4h and 1d in one call.", "parameters": SYM}},
    {"type": "function", "function": {"name": "flow", "description": "Live order-book imbalance, spread and taker buy/sell pressure for a crypto perp.", "parameters": SYM}},
    {"type": "function", "function": {"name": "edge", "description": "How often and how much a trigger paid on this coin over the last ~30 days, net of fees: follow vs fade.",
                                      "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}, "trigger": {"type": "string", "enum": ["breakout", "rsi_extreme", "volume_surge", "sweep", "fvg", "ob"]}}, "required": ["symbol", "trigger"]}}},
]


def run(name, args, inst, fetch):
    sym = inst["symbol"]
    if name == "levels":
        return json.dumps(levels(fetch(inst["market"], sym, "15m", 1200)))
    if name == "mtf":
        return mtf(fetch, inst["market"], sym)
    if name == "flow":
        return flow(sym) if inst["market"] == "futures" else "n/a (not a crypto perp)"
    if name == "edge":
        return edge_line(fetch, inst["market"], sym, [args.get("trigger", "breakout")])
    return None
