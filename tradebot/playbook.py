"""Smart-money day trader: patient, confirmation-first. It never anticipates. It waits until the large players have visibly acted and only then follows.

Order of work for every market:
  1. LIQUIDITY first. Is this market deep enough that our order does not move it, and is the spread tight? (dollar volume, spread, book depth)
  2. BIAS from the higher timeframe (4h trend). No clear trend, no trade.
  3. SWEEP: price pokes through a pool of stop orders (a swing low in an uptrend / swing high in a downtrend) and closes back inside. That is the
     big players collecting liquidity.
  4. DISPLACEMENT: a strong, high-volume candle in the trend direction that breaks the structure. That is them acting.
  5. RETEST: price comes back into the lower half of that move and is rejected. Only now do we enter, after the move is proven, with a stop just beyond the sweep.
  6. FOOTPRINT (crypto): open interest rising with price, top-trader positioning, large-print flow, order-book imbalance should not argue against us.
  7. TARGET the next pool of liquidity and take the trade only if reward is at least `rr_min` times the risk.
Stocks use the opening-range version: a high-volume break of the first 15 minutes, on the right side of VWAP, with the index agreeing; flat before the close.

Honest status: none of these ideas showed a profit in our out-of-sample tests (docs/STRATEGY_STUDY.md). They run on paper with a scorecard so you can judge them yourself."""
import time
from . import data, indicators as I, tools

DEFAULTS = dict(enabled=False, crypto=True, stocks=False, tf="15m", markets=8, min_score=5, max_trades_day=3, rr_min=2.0, risk_pct=0.5, use_ai=True,
                min_dollar_vol_m=100.0, max_spread_bps=4.0, max_hold_bars=16, stock_symbols=["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA"], stock_min_dollar_vol_m=500.0)

_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    v = fn()
    _cache[key] = (time.time(), v)
    return v


# ------------------------------------------------------------------ 1. liquidity
def crypto_volumes():
    """24h quote volume for every USDT perp in one request (cached 10 minutes)."""
    def get():
        d = data._get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=10)
        return {x["symbol"]: float(x["quoteVolume"]) for x in d}
    return _cached("vol24", 600, get)


def book(symbol):
    """spread (bps), depth within 0.1% each side (USD), imbalance (-1..1)."""
    def get():
        d = data._get("https://fapi.binance.com/fapi/v1/depth?symbol=%s&limit=50" % symbol, timeout=6)
        bids, asks = [(float(p), float(q)) for p, q in d["bids"]], [(float(p), float(q)) for p, q in d["asks"]]
        mid = (bids[0][0] + asks[0][0]) / 2
        b = sum(p * q for p, q in bids if p >= mid * 0.999)
        a = sum(p * q for p, q in asks if p <= mid * 1.001)
        return dict(spread_bps=(asks[0][0] - bids[0][0]) / mid * 1e4, depth_usd=min(a, b), imb=(b - a) / (b + a) if a + b else 0.0)
    return _cached("book:" + symbol, 20, get)


def liquidity_crypto(symbol, cfg):
    try:
        vol = crypto_volumes().get(symbol, 0.0)
        bk = book(symbol)
    except Exception as e:
        return dict(ok=False, why="liquidity data unavailable (%s)" % str(e)[:40], vol=0, spread_bps=None, depth_usd=0, imb=0)
    why = []
    if vol < cfg["min_dollar_vol_m"] * 1e6:
        why.append("24h volume $%.0fM is under your $%.0fM minimum" % (vol / 1e6, cfg["min_dollar_vol_m"]))
    if bk["spread_bps"] > cfg["max_spread_bps"]:
        why.append("spread %.1f bps is wider than %.1f" % (bk["spread_bps"], cfg["max_spread_bps"]))
    return dict(ok=not why, why="; ".join(why), vol=vol, spread_bps=bk["spread_bps"], depth_usd=bk["depth_usd"], imb=bk["imb"])


def liquidity_stock(symbol, fetch, cfg):
    try:
        d = fetch("stock", symbol, "1d", 40)[-21:-1]
        dv = sum(x[4] * x[5] for x in d) / len(d)
    except Exception as e:
        return dict(ok=False, why="no daily volume data (%s)" % str(e)[:40], vol=0)
    return dict(ok=dv >= cfg["stock_min_dollar_vol_m"] * 1e6, vol=dv, why="" if dv >= cfg["stock_min_dollar_vol_m"] * 1e6 else "average daily dollar volume $%.0fM is under $%.0fM" % (dv / 1e6, cfg["stock_min_dollar_vol_m"]))


# ------------------------------------------------------------------ 6. footprint of the large players (crypto, live)
def footprint(symbol):
    """OI change with price, top-trader positioning, large-print flow. Each is a weak, noisy clue (see the study), shown as such."""
    def get():
        out = {}
        try:
            oi = data._get("https://fapi.binance.com/futures/data/openInterestHist?symbol=%s&period=5m&limit=13" % symbol, timeout=6)
            out["oi_chg"] = float(oi[-1]["sumOpenInterestValue"]) / float(oi[0]["sumOpenInterestValue"]) - 1
        except Exception:
            out["oi_chg"] = None
        try:
            tp = data._get("https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol=%s&period=5m&limit=13" % symbol, timeout=6)
            out["top_ratio"], out["top_chg"] = float(tp[-1]["longShortRatio"]), float(tp[-1]["longShortRatio"]) / float(tp[0]["longShortRatio"]) - 1
        except Exception:
            out["top_ratio"] = out["top_chg"] = None
        try:
            tr = data._get("https://fapi.binance.com/fapi/v1/aggTrades?symbol=%s&limit=1000" % symbol, timeout=6)
            sizes = sorted(float(x["p"]) * float(x["q"]) for x in tr)
            big = sizes[int(len(sizes) * 0.95)]
            buy = sum(float(x["p"]) * float(x["q"]) for x in tr if not x["m"] and float(x["p"]) * float(x["q"]) >= big)
            sell = sum(float(x["p"]) * float(x["q"]) for x in tr if x["m"] and float(x["p"]) * float(x["q"]) >= big)
            out["big_flow"] = (buy - sell) / (buy + sell) if buy + sell else 0.0
        except Exception:
            out["big_flow"] = None
        return out
    return _cached("fp:" + symbol, 60, get)


def footprint_votes(fp, bk, side, px_chg):
    """Which large-player clues agree with `side`? -> list of plain sentences."""
    v = []
    if fp.get("oi_chg") is not None and fp["oi_chg"] > 0.002 and (px_chg > 0) == (side == 1):
        v.append("open interest +%.1f%% in the last hour while price moved our way (new positions, not just covering)" % (fp["oi_chg"] * 100))
    if fp.get("big_flow") is not None and fp["big_flow"] * side > 0.15:
        v.append("large prints are net %s (%+.0f%% of big volume)" % ("buying" if side == 1 else "selling", fp["big_flow"] * 100))
    if fp.get("top_chg") is not None and fp["top_chg"] * side > 0.01:
        v.append("top traders have been adding %s positions" % ("long" if side == 1 else "short"))
    if bk and bk["imb"] * side > 0.1:
        v.append("order book leans our way (%+.0f%% imbalance)" % (bk["imb"] * 100 * side))
    return v


# ------------------------------------------------------------------ 2-5, 7: the confirmation sequence
def crypto_setup(h15, h4, cfg, now_check=True):
    """Pure function of candles: -> dict(side, stop, target, reasons, score, entry_ref) or None, plus a status string explaining where it stands.
    Used live and in the study."""
    if len(h15) < 120 or len(h4) < 60:
        return None, "not enough history"
    c4 = [x[4] for x in h4]
    e20, e50 = I.ema(c4, 20)[-1], I.ema(c4, 50)[-1]
    if not e20 or not e50:
        return None, "no higher-timeframe data"
    bias = 1 if e20 > e50 and c4[-1] > e50 else -1 if e20 < e50 and c4[-1] < e50 else 0
    if not bias:
        return None, "waiting: the 4-hour trend is not clear"
    o, h, l, c, v = ([x[k] for x in h15] for k in (1, 2, 3, 4, 5))
    n = len(h15)
    a = I.atr(h, l, c, 14)[-1]
    if not a:
        return None, "no volatility data"
    vavg = sum(v[-21:-1]) / 20 or 1
    hi_piv, lo_piv = tools._pivots(h, l, 3, 300)
    pool = lo_piv if bias == 1 else hi_piv                        # liquidity the big players want to collect: stops below swing lows (longs) / above swing highs (shorts)
    sweep = None
    for j in range(n - 14, n - 2):
        for pj, lvl in pool:
            if pj >= j - 2 or pj < n - 120:
                continue
            if (bias == 1 and l[j] < lvl < c[j] and l[j] == min(l[pj + 1:j + 1])) or (bias == -1 and h[j] > lvl > c[j] and h[j] == max(h[pj + 1:j + 1])):
                if not any((bias == 1 and l[k] < l[j]) or (bias == -1 and h[k] > h[j]) for k in range(j + 1, n)):    # the sweep extreme must still hold
                    sweep = (j, lvl, l[j] if bias == 1 else h[j])
    if not sweep:
        return None, "waiting: no liquidity sweep against the %s trend in the last 3.5 hours" % ("up" if bias == 1 else "down")
    j, lvl, ext = sweep
    reasons = ["4-hour trend is %s" % ("up" if bias == 1 else "down"), "price swept the %s at %.6g and closed back inside (stops collected)" % ("lows" if bias == 1 else "highs", lvl)]
    disp = None
    for m in range(j + 1, n - 1):
        body = (c[m] - o[m]) * bias
        broke = c[m] > max(h[j:m]) if bias == 1 else c[m] < min(l[j:m])
        if body >= 1.0 * a and broke and v[m] >= 1.3 * vavg:
            disp = m
            break
    if disp is None:
        return None, "waiting: swept, but no strong high-volume candle has confirmed the move yet"
    reasons.append("strong displacement candle (%.1fx average volume) broke the structure: the large players have acted" % (v[disp] / vavg))
    leg_lo, leg_hi = (ext, max(h[disp:])) if bias == 1 else (min(l[disp:]), ext)
    mid = (leg_lo + leg_hi) / 2
    retest = (bias == 1 and l[-1] <= mid and c[-1] > mid) or (bias == -1 and h[-1] >= mid and c[-1] < mid)
    if not retest or disp >= n - 1:
        return None, "waiting: move confirmed, now waiting for a pullback into the lower half and a rejection (no chasing)"
    reasons.append("pullback into the move's %s half was rejected: entry after confirmation, not before" % ("lower" if bias == 1 else "upper"))
    entry = c[-1]
    stop = ext - 0.2 * a * bias
    risk = abs(entry - stop)
    if risk <= 0 or risk / entry > 0.03:
        return None, "skipped: stop would be %.1f%% away, too wide" % (risk / entry * 100)
    # target: the first unswept liquidity pool at least `rr_min` away (pools inside the move itself are structure to break through, not targets), capped at 4R
    pools = sorted((lv for pj, lv in (hi_piv if bias == 1 else lo_piv) if (lv - entry) * bias >= cfg["rr_min"] * risk), key=lambda x: abs(x - entry))
    target = pools[0] if pools else entry + bias * cfg["rr_min"] * risk
    if abs(target - entry) > 4 * risk:
        target = entry + bias * 4 * risk
    reasons.append("target %.1fR away (%s)" % (abs(target - entry) / risk, "next liquidity pool" if pools and abs(target - entry) < 4 * risk - 1e-9 else "minimum reward-to-risk"))
    return dict(side=bias, stop=stop, target=target, reasons=reasons, score=4, entry_ref=entry, sweep_bar=j), "setup confirmed"


def stock_setup(bars5, prior_vols, spy5, cfg):
    """Opening-range breakout after confirmation. bars5: today's 5-minute bars so far (tuples with minute-of-day first: (m,o,h,l,c,v)).
    -> (setup or None, status). Long only (the built-in stock model cannot short)."""
    if len(bars5) < 5:
        return None, "waiting: the opening range (first 15 minutes) is still forming"
    orb = bars5[:3]
    hi, lo, vol = max(x[2] for x in orb), min(x[3] for x in orb), sum(x[5] for x in orb)
    if hi <= lo or (hi - lo) / orb[-1][4] < 0.002:
        return None, "skipped: opening range too tight to trade"
    if len(prior_vols) >= 8 and sum(prior_vols[-10:]) > 0:
        rv = vol / (sum(prior_vols[-10:]) / len(prior_vols[-10:]))
    else:
        return None, "waiting: not enough history to measure relative volume"
    if rv < 1.5:
        return None, "skipped: opening volume only %.1fx normal (needs 1.5x: the big players are not here)" % rv
    last = bars5[-1]
    if not (last[4] > hi and len(bars5) > 3):
        return None, "waiting: no close above the opening range high %.2f yet (volume %.1fx normal)" % (hi, rv)
    num = sum((b[2] + b[3] + b[4]) / 3 * b[5] for b in bars5)
    den = sum(b[5] for b in bars5) or 1
    if last[4] <= num / den:
        return None, "skipped: broke out but below VWAP"
    if spy5 and spy5[-1][4] < spy5[0][1]:
        return None, "skipped: the index is trading down on the day"
    entry, stop = last[4], lo
    risk = entry - stop
    if risk <= 0 or risk / entry > 0.03:
        return None, "skipped: stop too wide"
    return dict(side=1, stop=stop, target=entry + cfg["rr_min"] * risk, entry_ref=entry, score=4,
                reasons=["opening volume %.1fx normal" % rv, "closed above the 15-minute range high %.2f after the break was confirmed" % hi, "above VWAP and the index is up", "stop under the opening range, target %.1fR" % cfg["rr_min"]]), "setup confirmed"


def market_open_et(now=None):
    """(is_open, minutes_since_open) for the US regular session (9:30-16:00 ET, Mon-Fri; holidays are not modelled: no bars means no trades)."""
    now = now or time.time()
    t = time.gmtime(now)
    y = t.tm_year
    import calendar

    def nth(month, n):
        first = calendar.monthrange(y, month)[0]
        return 1 + (6 - first) % 7 + 7 * (n - 1)
    start, end = calendar.timegm((y, 3, nth(3, 2), 7, 0, 0)), calendar.timegm((y, 11, nth(11, 1), 6, 0, 0))
    off = 4 if start <= now < end else 5
    lt = time.gmtime(now - off * 3600)
    m = lt.tm_hour * 60 + lt.tm_min
    return (lt.tm_wday < 5 and 570 <= m < 955), m - 570
